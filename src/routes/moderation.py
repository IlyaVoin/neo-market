from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.moderation import ModerationEventOpenAPIRequest, ModerationEventRequest
from src.services.moderation_service import ApplyResult, ModerationDecision, ModerationService

router = APIRouter(prefix="/api/v1", tags=["moderation"])


async def _verify_service_key(x_service_key: str | None) -> None:
    if x_service_key != settings.mod_to_b2b_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is missing"},
        )


def _canon_decision(payload: ModerationEventRequest) -> ModerationDecision:
    blocking_reason = payload.blocking_reason
    return ModerationDecision(
        idempotency_key=payload.idempotency_key,
        product_id=payload.product_id,
        event_type=payload.status,
        hard_block=payload.hard_block,
        blocking_reason_id=blocking_reason.id if blocking_reason else None,
        blocking_reason_title=blocking_reason.title if blocking_reason else None,
        blocking_reason_comment=blocking_reason.comment if blocking_reason else None,
        field_reports=[fr.model_dump() for fr in payload.field_reports],
    )


def _openapi_decision(payload: ModerationEventOpenAPIRequest) -> ModerationDecision:
    return ModerationDecision(
        idempotency_key=payload.idempotency_key,
        product_id=payload.product_id,
        event_type=payload.event_type,
        hard_block=payload.hard_block,
        blocking_reason_id=payload.blocking_reason_id,
        blocking_reason_title=None,
        blocking_reason_comment=payload.moderator_comment,
        field_reports=[fr.model_dump() for fr in (payload.field_reports or [])],
    )


async def _apply(
    session: AsyncSession, decision: ModerationDecision
) -> ApplyResult:
    service = ModerationService(session)
    try:
        result = await service.apply_event(decision)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc
    await session.commit()
    return result


@router.post(
    "/events/moderation",
    responses={
        200: {"description": "Событие принято и обработано (или дубликат)"},
        400: {"model": Error, "description": "Невалидные данные"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "Product not found"},
    },
)
async def receive_moderation_event(
    payload: ModerationEventRequest,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> dict:
    await _verify_service_key(x_service_key)
    result = await _apply(session, _canon_decision(payload))
    return {"status": result.status, "duplicate": result.duplicate}


@router.post(
    "/moderation/events",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Принято и обработано (или дубликат)"},
        400: {"model": Error, "description": "Невалидные данные"},
        401: {"model": Error, "description": "Unauthorized"},
        404: {"model": Error, "description": "Product not found"},
    },
)
async def receive_moderation_event_openapi(
    payload: ModerationEventOpenAPIRequest,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> Response:
    await _verify_service_key(x_service_key)
    await _apply(session, _openapi_decision(payload))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
