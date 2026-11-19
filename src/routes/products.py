import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_seller_id
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.product import ProductCreate, ProductResponse
from src.services.product_service import ProductService

router = APIRouter(prefix="/api/v1/products", tags=["products"])


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
