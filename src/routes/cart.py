import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.schemas.cart import (
    CartItemAddRequest,
    CartItemUpdateRequest,
    CartItemView,
    CartResponse,
    CartValidationResponse,
)
from src.schemas.errors import Error
from src.services.cart_service import CartIdentity, CartService

router = APIRouter(prefix="/api/v1/cart", tags=["cart"])
security = HTTPBearer(auto_error=False)


async def get_cart_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    session: AsyncSession = Depends(get_session),
) -> CartIdentity:
    user_id: uuid.UUID | None = None
    if credentials is not None:
        try:
            payload = jwt.decode(
                credentials.credentials,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            sub = payload.get("sub")
            if sub is not None:
                user_id = uuid.UUID(sub)
        except (JWTError, ValueError):
            user_id = None

    if user_id is not None:
        # Залогиненный пользователь: X-Session-Id игнорируется, кроме merge гостевой корзины.
        if x_session_id:
            service = CartService(session)
            await service.merge_guest_cart(user_id, x_session_id)
            await session.commit()
        return CartIdentity(user_id=user_id, session_id=None)

    if x_session_id:
        return CartIdentity(user_id=None, session_id=x_session_id)

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
    )


@router.get(
    "",
    response_model=CartResponse,
    responses={
        200: {"model": CartResponse, "description": "Содержимое корзины"},
        401: {"model": Error, "description": "Unauthorized"},
    },
)
async def get_cart(
    identity: CartIdentity = Depends(get_cart_identity),
    session: AsyncSession = Depends(get_session),
) -> CartResponse:
    service = CartService(session)
    return await service.get_cart(identity)


@router.post(
    "/items",
    response_model=CartItemView,
    responses={
        200: {"model": CartItemView, "description": "Позиция обновлена (quantity увеличено)"},
        201: {"model": CartItemView, "description": "Позиция создана"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "SKU not found"},
        409: {"model": Error, "description": "SKU unavailable / insufficient stock"},
    },
)
async def add_cart_item(
    payload: CartItemAddRequest,
    identity: CartIdentity = Depends(get_cart_identity),
    session: AsyncSession = Depends(get_session),
) -> CartItemView:
    service = CartService(session)
    try:
        view, created = await service.add_item(identity, payload.sku_id, payload.quantity)
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise _map_add_error(str(exc)) from exc

    if created:
        return JSONResponse(status_code=status.HTTP_201_CREATED, content=view.model_dump(mode="json"))
    return view


@router.put(
    "/items/{item_id}",
    response_model=CartItemView,
    responses={
        200: {"model": CartItemView, "description": "Обновлено"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "Cart item not found"},
        409: {"model": Error, "description": "Insufficient stock"},
    },
)
async def update_cart_item(
    item_id: uuid.UUID,
    payload: CartItemUpdateRequest,
    identity: CartIdentity = Depends(get_cart_identity),
    session: AsyncSession = Depends(get_session),
) -> CartItemView:
    service = CartService(session)
    try:
        view = await service.update_item(identity, item_id, payload.quantity)
        await session.commit()
        return view
    except ValueError as exc:
        await session.rollback()
        msg = str(exc)
        if msg == "Cart item not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Cart item not found"},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": msg},
        ) from exc


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Удалено"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "Cart item not found"},
    },
)
async def delete_cart_item(
    item_id: uuid.UUID,
    identity: CartIdentity = Depends(get_cart_identity),
    session: AsyncSession = Depends(get_session),
) -> Response:
    service = CartService(session)
    try:
        await service.delete_item(identity, item_id)
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "Cart item not found"},
        ) from exc


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Корзина очищена"},
        401: {"model": Error, "description": "Unauthorized"},
    },
)
async def clear_cart(
    identity: CartIdentity = Depends(get_cart_identity),
    session: AsyncSession = Depends(get_session),
) -> Response:
    service = CartService(session)
    await service.clear_cart(identity)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/validate",
    response_model=CartValidationResponse,
    responses={
        200: {"model": CartValidationResponse, "description": "Результат валидации"},
        401: {"model": Error, "description": "Unauthorized"},
    },
)
async def validate_cart(
    identity: CartIdentity = Depends(get_cart_identity),
    session: AsyncSession = Depends(get_session),
) -> CartValidationResponse:
    service = CartService(session)
    return await service.validate(identity)


def _map_add_error(msg: str) -> HTTPException:
    if msg == "SKU not found":
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "SKU not found"},
        )
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "CONFLICT", "message": msg},
    )
