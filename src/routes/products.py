import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_seller_id, get_optional_seller_id
from src.config import settings
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.product import (
    ProductCreate,
    ProductDetailResponse,
    ProductPublicPaginatedResponse,
    ProductResponse,
    ProductUpdate,
)
from src.services.product_service import ProductService

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def _parse_ids_param(ids: str | None) -> list[uuid.UUID] | None:
    if not ids:
        return None
    parsed: list[uuid.UUID] = []
    for raw in ids.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            parsed.append(uuid.UUID(raw))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": "ids must be a valid comma-separated UUID list"},
            )
    return parsed or None


@router.get(
    "",
    response_model=ProductPublicPaginatedResponse,
    responses={
        200: {"model": ProductPublicPaginatedResponse, "description": "B2C catalog list"},
        400: {"model": Error, "description": "Invalid request"},
        401: {"model": Error, "description": "Unauthorized"},
    },
)
async def list_products(
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    ids: str | None = Query(default=None, description="Comma-separated UUID list for batch query"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ProductPublicPaginatedResponse:
    if x_service_key != settings.b2b_to_b2c_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is missing"},
        )

    service = ProductService(session)
    try:
        result = await service.list_products(
            service_key=x_service_key,
            ids=_parse_ids_param(ids),
            limit=limit,
            offset=offset,
        )
        await session.commit()
        return result
    except ValueError:
        await session.rollback()
        raise


@router.post(
    "",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": Error, "description": "Validation error"},
        401: {"model": Error, "description": "Unauthorized"},
    },
)
async def create_product(
    product_data: ProductCreate,
    session: AsyncSession = Depends(get_session),
    seller_id: str = Depends(get_current_seller_id),
) -> ProductResponse:
    try:
        seller_uuid = uuid.UUID(seller_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid seller ID in token"},
        )

    service = ProductService(session)
    try:
        product = await service.create_product(seller_uuid, product_data)
        await session.commit()
        return product
    except ValueError as e:
        await session.rollback()
        if str(e) == "Category not found":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": "Category not found"},
            )
        raise


@router.get(
    "/{product_id}",
    response_model=ProductDetailResponse,
    responses={
        200: {"model": ProductDetailResponse, "description": "Product detail"},
        400: {"model": Error, "description": "Invalid request"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "Product not found"},
    },
)
async def get_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    seller_id: str | None = Depends(get_optional_seller_id),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> ProductDetailResponse:
    service_key: str | None = None
    if x_service_key is not None and x_service_key in (
        settings.b2b_to_mod_key,
        settings.b2b_to_b2c_key,
    ):
        service_key = x_service_key

    if service_key is None and seller_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is missing"},
        )

    service = ProductService(session)
    try:
        product = await service.get_product(
            product_id,
            seller_id=uuid.UUID(seller_id) if seller_id is not None else None,
            service_key=service_key,
        )
        await session.commit()
        return product
    except ValueError as e:
        await session.rollback()
        if str(e) == "Product not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Product not found"},
            )
        raise


@router.patch(
    "/{product_id}",
    response_model=ProductDetailResponse,
    responses={
        200: {"model": ProductDetailResponse, "description": "Updated"},
        400: {"model": Error, "description": "Invalid request"},
        401: {"model": Error, "description": "Unauthorized"},
        403: {"model": Error, "description": "HARD_BLOCKED product is not editable"},
        404: {"model": Error, "description": "Product not found"},
    },
)
async def update_product(
    product_id: uuid.UUID,
    product_data: ProductUpdate,
    session: AsyncSession = Depends(get_session),
    seller_id: str = Depends(get_current_seller_id),
) -> ProductDetailResponse:
    try:
        seller_uuid = uuid.UUID(seller_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid seller ID in token"},
        ) from e

    service = ProductService(session)
    try:
        product = await service.update_product(product_id, seller_uuid, product_data)
        await session.commit()
        return product
    except ValueError as e:
        await session.rollback()
        if str(e) == "Product not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Product not found"},
            )
        if str(e) == "Cannot edit hard-blocked product":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "Cannot edit hard-blocked product"},
            )
        if str(e) == "Category not found":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": "Category not found"},
            )
        raise


@router.delete(
    "/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Мягкое удаление (deleted=true)"},
        400: {"model": Error, "description": "Product already deleted"},
        401: {"model": Error, "description": "Unauthorized"},
        403: {"model": Error, "description": "HARD_BLOCKED product cannot be deleted"},
        404: {"model": Error, "description": "Product not found"},
    },
)
async def delete_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    seller_id: str = Depends(get_current_seller_id),
) -> Response:
    try:
        seller_uuid = uuid.UUID(seller_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid seller ID in token"},
        ) from e

    service = ProductService(session)
    try:
        await service.delete_product(product_id, seller_uuid)
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ValueError as e:
        await session.rollback()
        if str(e) == "Product not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Product not found"},
            )
        if str(e) == "Cannot delete hard-blocked product":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "Cannot delete hard-blocked product"},
            )
        if str(e) == "Product already deleted":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_REQUEST", "message": "Product already deleted"},
            )
        raise
