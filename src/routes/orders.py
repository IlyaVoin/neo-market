import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.order import OrderCreateRequest, OrderItemResponse, OrderResponse
from src.services.order_service import (
    B2BUnavailableError,
    BodyMismatchError,
    EmptyCartError,
    InvalidQuantityError,
    OrderService,
    ReserveFailedError,
)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])
security = HTTPBearer(auto_error=False)


def _get_buyer_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> uuid.UUID:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        sub = payload.get("sub")
        buyer_id = uuid.UUID(sub) if sub is not None else None
    except (JWTError, ValueError):
        buyer_id = None
    if buyer_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )
    return buyer_id


def _to_response(order) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        buyer_id=order.buyer_id,
        status=order.status.value,
        items=[
            OrderItemResponse(
                id=item.id,
                sku_id=item.sku_id,
                product_id=item.product_id,
                product_title=item.product_title,
                sku_name=item.sku_name,
                name=item.product_title,
                image_url=item.image_url,
                quantity=item.quantity,
                unit_price=item.unit_price,
                line_total=item.line_total,
            )
            for item in order.items
        ],
        subtotal=order.total_amount,
        total=order.total_amount,
        total_amount=order.total_amount,
        address=order.delivery_address,
        delivery_address=order.delivery_address,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"model": OrderResponse, "description": "Заказ создан (или уже существует)"},
        400: {"model": Error, "description": "Invalid request"},
        401: {"model": Error, "description": "Unauthorized"},
        409: {"model": Error, "description": "Reserve failed / idempotency conflict"},
        422: {"model": Error, "description": "Invalid quantity"},
        503: {"model": Error, "description": "B2B unavailable"},
    },
)
async def create_order(
    payload: OrderCreateRequest,
    session: AsyncSession = Depends(get_session),
    buyer_id: uuid.UUID = Depends(_get_buyer_id),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> OrderResponse:
    key = idempotency_key_header or (str(payload.idempotency_key) if payload.idempotency_key else None)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "Idempotency-Key is required"},
        )
    try:
        idempotency_key = uuid.UUID(key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": "Idempotency-Key must be a UUID"},
        ) from exc

    service = OrderService(session)
    try:
        order, _created = await service.create_order(buyer_id, payload, idempotency_key)
        await session.commit()
        await session.refresh(order)
    except EmptyCartError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc
    except InvalidQuantityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_QUANTITY", "message": str(exc)},
        ) from exc
    except BodyMismatchError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc
    except ReserveFailedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "RESERVE_FAILED",
                "message": "Не удалось зарезервировать товары",
                "failed_items": [item.model_dump(mode="json") for item in exc.failed_items],
            },
        ) from exc
    except B2BUnavailableError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "B2B_UNAVAILABLE",
                "message": "Сервис товаров временно недоступен, попробуйте позже",
            },
        ) from exc

    return _to_response(order)
