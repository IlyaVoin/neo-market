import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.schemas.catalog import (
    CatalogProductListResponse,
    CategoryFiltersResponse,
    FacetsResponse,
)
from src.schemas.errors import Error
from src.schemas.product_card import ProductCardResponse
from src.services.cart_service import CartIdentity
from src.services.catalog_b2b_client import B2BUnavailableError, CatalogParams
from src.services.catalog_service import CatalogService, InvalidSortError
from src.services.product_service import ProductService

router = APIRouter(prefix="/api/v1/catalog", tags=["catalog"])
optional_security = HTTPBearer(auto_error=False)


async def _verify_service_key(x_service_key: str | None) -> None:
    if x_service_key != settings.b2b_to_b2c_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is missing"},
        )


async def _optional_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(optional_security),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
) -> CartIdentity | None:
    if credentials is not None:
        try:
            payload = jwt.decode(
                credentials.credentials,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            sub = payload.get("sub")
            if sub is not None:
                return CartIdentity(user_id=uuid.UUID(sub))
        except (JWTError, ValueError):
            pass
    if x_session_id:
        return CartIdentity(session_id=x_session_id)
    return None


def _parse_attributes(request: Request) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for key in request.query_params:
        for prefix in ("filter[", "filters["):
            if key.startswith(prefix) and key.endswith("]"):
                attributes[key[len(prefix) : -1]] = request.query_params[key]
    return attributes


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={"code": "BAD_GATEWAY", "message": "Каталог временно недоступен"},
    )


@router.get(
    "/products",
    response_model=CatalogProductListResponse,
    responses={
        200: {"model": CatalogProductListResponse, "description": "Страница каталога"},
        400: {"model": Error, "description": "Invalid sort parameter"},
        401: {"model": Error, "description": "Unauthorized"},
        502: {"model": Error, "description": "B2B unavailable"},
    },
)
async def catalog_products(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    identity: CartIdentity | None = Depends(_optional_identity),
    category_id: uuid.UUID | None = Query(default=None),
    price_min: int | None = Query(default=None, ge=0),
    price_max: int | None = Query(default=None, ge=0),
    q: str | None = Query(default=None),
    sort: str = Query(default="popularity"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> CatalogProductListResponse:
    await _verify_service_key(x_service_key)
    service = CatalogService(session)
    params = CatalogParams(
        category_id=category_id,
        price_min=price_min,
        price_max=price_max,
        attributes=_parse_attributes(request),
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    try:
        return await service.search(params, identity)
    except InvalidSortError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc
    except B2BUnavailableError as exc:
        raise _unavailable() from exc


@router.get(
    "/products/{product_id}",
    response_model=ProductCardResponse,
    responses={
        200: {"model": ProductCardResponse, "description": "Карточка товара B2C"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "Not found"},
    },
)
async def product_card(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> ProductCardResponse:
    await _verify_service_key(x_service_key)
    service = ProductService(session)
    try:
        return await service.get_product_card(product_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc


@router.get(
    "/facets",
    response_model=FacetsResponse,
    responses={
        200: {"model": FacetsResponse, "description": "Фасеты с подсчётами"},
        401: {"model": Error, "description": "Unauthorized"},
        502: {"model": Error, "description": "B2B unavailable"},
    },
)
async def catalog_facets(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    category_id: uuid.UUID | None = Query(default=None),
) -> FacetsResponse:
    await _verify_service_key(x_service_key)
    service = CatalogService(session)
    try:
        return await service.facets(category_id, _parse_attributes(request))
    except B2BUnavailableError as exc:
        raise _unavailable() from exc


@router.get(
    "/categories/{category_id}/filters",
    response_model=CategoryFiltersResponse,
    responses={
        200: {"model": CategoryFiltersResponse, "description": "Доступные фильтры"},
        401: {"model": Error, "description": "Unauthorized"},
        502: {"model": Error, "description": "B2B unavailable"},
    },
)
async def catalog_category_filters(
    category_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> CategoryFiltersResponse:
    await _verify_service_key(x_service_key)
    service = CatalogService(session)
    try:
        return await service.category_filters(category_id)
    except B2BUnavailableError as exc:
        raise _unavailable() from exc
