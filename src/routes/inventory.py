from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.inventory import (
    ReserveFailedItem,
    ReserveFailureResponse,
    ReserveItemResult,
    ReserveOpenAPIRequest,
    ReserveOpenAPIResponse,
    ReserveRequest,
    ReserveSuccessResponse,
    UnreserveOpenAPIResponse,
    UnreserveRequest,
    UnreserveResponse,
)
from src.services.inventory_service import InventoryService, ReserveResult

router = APIRouter(prefix="/api/v1", tags=["inventory"])


async def _verify_service_key(x_service_key: str | None) -> None:
    if x_service_key != settings.b2b_to_b2c_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is missing"},
        )


def _to_success_response(result: ReserveResult) -> ReserveSuccessResponse:
    return ReserveSuccessResponse(
        reserved=True,
        items=[
            ReserveItemResult(
                sku_id=item.sku_id,
                reserved_quantity=item.reserved_quantity,
                remaining_stock=item.remaining_stock,
            )
            for item in result.items
        ],
    )


def _to_failure_response(result: ReserveResult) -> ReserveFailureResponse:
    return ReserveFailureResponse(
        reserved=False,
        failed_items=[
            ReserveFailedItem(
                sku_id=item.sku_id,
                requested=item.requested,
                available=item.available,
                reason=item.reason,
            )
            for item in result.failed_items
        ],
    )


def _conflict_content(result: ReserveResult) -> dict:
    return {
        "code": "OUT_OF_STOCK",
        "message": "One or more SKUs cannot be reserved",
        "details": {
            "failed_items": [
                {
                    "sku_id": str(item.sku_id),
                    "requested": item.requested,
                    "available": item.available,
                    "reason": item.reason,
                }
                for item in result.failed_items
            ]
        },
    }


@router.post(
    "/reserve",
    responses={
        200: {"model": ReserveSuccessResponse, "description": "Reserved (or idempotent replay)"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "SKU not found"},
        409: {"model": ReserveFailureResponse, "description": "Insufficient stock"},
    },
)
async def reserve(
    payload: ReserveRequest,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> ReserveSuccessResponse:
    await _verify_service_key(x_service_key)
    service = InventoryService(session)
    try:
        result = await service.reserve(payload.idempotency_key, payload.items)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc

    if not result.reserved:
        await session.rollback()
        failure = _to_failure_response(result)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=failure.model_dump(mode="json"),
        )

    await session.commit()
    return _to_success_response(result)


@router.post(
    "/unreserve",
    responses={
        200: {"model": UnreserveResponse, "description": "Unreserved"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "SKU not found"},
        409: {"model": Error, "description": "Insufficient reserved quantity"},
    },
)
async def unreserve(
    payload: UnreserveRequest,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> UnreserveResponse:
    await _verify_service_key(x_service_key)
    service = InventoryService(session)
    try:
        await service.unreserve(payload.order_id, payload.items)
    except ValueError as exc:
        await session.rollback()
        code = "NOT_FOUND" if str(exc) == "SKU not found" else "CONFLICT"
        http_status = (
            status.HTTP_404_NOT_FOUND
            if code == "NOT_FOUND"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": code, "message": str(exc)},
        ) from exc

    await session.commit()
    return UnreserveResponse(ok=True)


@router.post(
    "/inventory/reserve",
    responses={
        200: {"model": ReserveOpenAPIResponse, "description": "Reserved"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "SKU not found"},
        409: {"model": Error, "description": "Insufficient stock"},
    },
)
async def reserve_openapi(
    payload: ReserveOpenAPIRequest,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> ReserveOpenAPIResponse:
    await _verify_service_key(x_service_key)
    service = InventoryService(session)
    try:
        result = await service.reserve(
            payload.idempotency_key, payload.items, order_id=payload.order_id
        )
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc

    if not result.reserved:
        await session.rollback()
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=_conflict_content(result))

    await session.commit()
    return ReserveOpenAPIResponse(
        order_id=result.order_id or payload.order_id,
        reserved_at=result.reserved_at,
    )


@router.post(
    "/inventory/unreserve",
    responses={
        200: {"model": UnreserveOpenAPIResponse, "description": "Unreserved"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "SKU not found"},
        409: {"model": Error, "description": "Insufficient reserved quantity"},
    },
)
async def unreserve_openapi(
    payload: UnreserveRequest,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> UnreserveOpenAPIResponse:
    await _verify_service_key(x_service_key)
    service = InventoryService(session)
    try:
        processed_at = await service.unreserve(payload.order_id, payload.items)
    except ValueError as exc:
        await session.rollback()
        code = "NOT_FOUND" if str(exc) == "SKU not found" else "CONFLICT"
        http_status = (
            status.HTTP_404_NOT_FOUND
            if code == "NOT_FOUND"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=http_status,
            detail={"code": code, "message": str(exc)},
        ) from exc

    await session.commit()
    return UnreserveOpenAPIResponse(order_id=payload.order_id, processed_at=processed_at)
