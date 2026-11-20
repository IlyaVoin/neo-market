import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_seller_id
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.sku import SKUCreate, SKUResponse
from src.services.sku_service import SKUService

router = APIRouter(prefix="/api/v1/skus", tags=["skus"])


@router.post(
    "",
    response_model=SKUResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": Error, "description": "Validation error"},
        401: {"model": Error, "description": "Unauthorized"},
        403: {"model": Error, "description": "Forbidden - HARD_BLOCKED product"},
        404: {"model": Error, "description": "Product not found"},
    },
)
async def create_sku(
    sku_data: SKUCreate,
    session: AsyncSession = Depends(get_session),
    seller_id: str = Depends(get_current_seller_id),
) -> SKUResponse:
    try:
        seller_uuid = uuid.UUID(seller_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid seller ID in token"},
        )

    service = SKUService(session)
    try:
        sku = await service.create_sku(seller_uuid, sku_data)
        await session.commit()
        return sku
    except ValueError as e:
        await session.rollback()
        error_msg = str(e)
        if error_msg == "Product not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": "Product not found"},
            )
        if error_msg == "Cannot add SKU to hard-blocked product":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "Cannot add SKU to hard-blocked product"},
            )
        raise