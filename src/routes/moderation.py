import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database import get_session
from src.schemas.errors import Error
from src.schemas.moderation import (
    ApproveRequest,
    DeclineRequest,
    IncomingProductEvent,
    ModerationEventOpenAPIRequest,
    ModerationEventRequest,
)
from src.services.moderation_approval_service import (
    ApproveConflictError,
    BlockingReasonNotFoundError,
    CardNotFoundError,
    DuplicateProductEventError,
    ModerationApprovalService,
    ModerationCardNotFoundError,
    NotAssignedError,
)
from src.services.moderation_service import ApplyResult, ModerationDecision, ModerationService

router = APIRouter(prefix="/api/v1", tags=["moderation"])
moderator_security = HTTPBearer(auto_error=False)


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


def _get_moderator_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(moderator_security),
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
        moderator_id = uuid.UUID(sub) if sub is not None else None
    except (JWTError, ValueError):
        moderator_id = None
    if moderator_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authentication required"},
        )
    return moderator_id


@router.post(
    "/products/{product_id}/approve",
    responses={
        200: {"description": "Товар одобрен (MODERATED)"},
        401: {"model": Error, "description": "Unauthorized"},
        403: {"model": Error, "description": "Not assigned to you"},
        404: {"model": Error, "description": "Product not found in moderation queue"},
        409: {"model": Error, "description": "Invalid status / no SKUs"},
    },
)
async def approve_product(
    product_id: uuid.UUID,
    payload: ApproveRequest | None = None,
    session: AsyncSession = Depends(get_session),
    moderator_id: uuid.UUID = Depends(_get_moderator_id),
) -> dict:
    service = ModerationApprovalService(session)
    comment = payload.moderator_comment if payload is not None else None
    try:
        card = await service.approve_product(product_id, moderator_id, comment)
        await session.commit()
    except ModerationCardNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc
    except NotAssignedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(exc)},
        ) from exc
    except ApproveConflictError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": str(exc)},
        ) from exc

    return {"product_id": str(card.product_id), "status": card.status.value}


@router.post(
    "/products/{product_id}/decline",
    responses={
        200: {"description": "BLOCKED или HARD_BLOCKED"},
        400: {"model": Error, "description": "Blocking reason not found"},
        401: {"model": Error, "description": "Unauthorized"},
        403: {"model": Error, "description": "Not assigned to you"},
        404: {"model": Error, "description": "Product not found in moderation queue"},
        409: {"model": Error, "description": "Invalid status / permanently blocked"},
    },
)
async def decline_product(
    product_id: uuid.UUID,
    payload: DeclineRequest,
    session: AsyncSession = Depends(get_session),
    moderator_id: uuid.UUID = Depends(_get_moderator_id),
) -> dict:
    service = ModerationApprovalService(session)
    try:
        card = await service.decline_product(product_id, moderator_id, payload)
        await session.commit()
    except ModerationCardNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from exc
    except NotAssignedError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": str(exc)},
        ) from exc
    except BlockingReasonNotFoundError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc
    except ApproveConflictError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CONFLICT", "message": str(exc)},
        ) from exc

    return {"product_id": str(card.product_id), "status": card.status.value}


async def _verify_b2b_key(x_service_key: str | None) -> None:
    if x_service_key != settings.b2b_to_mod_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Authorization header is missing"},
        )


@router.post(
    "/events/product",
    responses={
        200: {"description": "Событие принято"},
        400: {"model": Error, "description": "Invalid event"},
        401: {"model": Error, "description": "Unauthorized"},
    },
)
async def receive_product_event(
    payload: IncomingProductEvent,
    session: AsyncSession = Depends(get_session),
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
) -> dict:
    await _verify_b2b_key(x_service_key)
    service = ModerationApprovalService(session)
    try:
        result = await service.handle_b2b_product_event(payload)
        await session.commit()
    except (DuplicateProductEventError, CardNotFoundError) as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_REQUEST", "message": str(exc)},
        ) from exc
    return {"result": result}
