import uuid
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CartItem
from src.schemas.cart import (
    CartItemView,
    CartResponse,
    CartSummary,
    CartValidationIssue,
    CartValidationResponse,
    CheckoutItem,
    CheckoutPayload,
)
from src.services.b2b_client import B2BClient, SkuSnapshot


@dataclass
class CartIdentity:
    user_id: uuid.UUID | None = None
    session_id: str | None = None


class CartService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.b2b = B2BClient(session)

    # --- identity scoping ---

    def _scope(self, identity: CartIdentity):
        if identity.user_id is not None:
            return [CartItem.user_id == identity.user_id]
        return [CartItem.session_id == identity.session_id]

    async def _find_item(self, identity: CartIdentity, item_id: uuid.UUID) -> CartItem | None:
        stmt = select(CartItem).where(CartItem.id == item_id, *self._scope(identity))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # --- commands ---

    async def add_item(
        self, identity: CartIdentity, sku_id: uuid.UUID, quantity: int
    ) -> tuple[CartItemView, bool]:
        snapshot = (await self.b2b.get_skus_batch([sku_id])).get(sku_id)
        self._validate_sku_available(snapshot, quantity)

        stmt = select(CartItem).where(*self._scope(identity), CartItem.sku_id == sku_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()

        if existing is not None:
            existing.quantity += quantity
            await self.session.flush()
            created = False
            item = existing
        else:
            item = CartItem(
                user_id=identity.user_id,
                session_id=identity.session_id,
                sku_id=sku_id,
                quantity=quantity,
            )
            self.session.add(item)
            await self.session.flush()
            created = True

        view = self._build_view(item, snapshot)
        return view, created

    async def update_item(
        self, identity: CartIdentity, item_id: uuid.UUID, quantity: int
    ) -> CartItemView:
        item = await self._find_item(identity, item_id)
        if item is None:
            raise ValueError("Cart item not found")

        snapshot = (await self.b2b.get_skus_batch([item.sku_id])).get(item.sku_id)
        if snapshot is not None and snapshot.active_quantity < quantity:
            raise ValueError("Insufficient stock")

        item.quantity = quantity
        await self.session.flush()
        return self._build_view(item, snapshot)

    async def delete_item(self, identity: CartIdentity, item_id: uuid.UUID) -> None:
        item = await self._find_item(identity, item_id)
        if item is None:
            raise ValueError("Cart item not found")
        await self.session.delete(item)
        await self.session.flush()

    async def clear_cart(self, identity: CartIdentity) -> None:
        await self.session.execute(delete(CartItem).where(*self._scope(identity)))
        await self.session.flush()

    # --- reads ---

    async def get_cart(self, identity: CartIdentity) -> CartResponse:
        stmt = select(CartItem).where(*self._scope(identity)).order_by(CartItem.created_at)
        items = list((await self.session.execute(stmt)).scalars().all())

        snapshots = await self.b2b.get_skus_batch([item.sku_id for item in items])
        views = [self._build_view(item, snapshots.get(item.sku_id)) for item in items]

        return CartResponse(
            items=views,
            summary=self._build_summary(views),
            checkout_payload=self._build_checkout_payload(views),
        )

    async def validate(self, identity: CartIdentity) -> CartValidationResponse:
        cart = await self.get_cart(identity)
        issues: list[CartValidationIssue] = []
        for view in cart.items:
            if not view.available:
                issues.append(
                    CartValidationIssue(
                        issue_type=view.unavailable_reason or "UNAVAILABLE",
                        severity="critical",
                        message=f"SKU {view.sku_id} недоступен",
                    )
                )
            elif view.available_stock is not None and view.available_stock < view.quantity:
                issues.append(
                    CartValidationIssue(
                        issue_type="INSUFFICIENT_STOCK",
                        severity="warning",
                        message=f"SKU {view.sku_id}: доступно {view.available_stock}, "
                        f"в корзине {view.quantity}",
                    )
                )
        is_valid = not any(i.severity == "critical" for i in issues)
        return CartValidationResponse(
            is_valid=is_valid,
            can_checkout=cart.summary.checkout_ready,
            issues=issues,
        )

    # --- merge (login) ---

    async def merge_guest_cart(self, user_id: uuid.UUID, session_id: str) -> None:
        guest_items = list(
            (
                await self.session.execute(
                    select(CartItem).where(CartItem.session_id == session_id)
                )
            ).scalars().all()
        )
        if not guest_items:
            return

        auth_items = list(
            (
                await self.session.execute(
                    select(CartItem).where(CartItem.user_id == user_id)
                )
            ).scalars().all()
        )
        auth_by_sku = {item.sku_id: item for item in auth_items}

        for guest in guest_items:
            existing = auth_by_sku.get(guest.sku_id)
            if existing is not None:
                existing.quantity = max(existing.quantity, guest.quantity)
                await self.session.delete(guest)
            else:
                guest.user_id = user_id
                guest.session_id = None

        await self.session.flush()
        # Подчищаем возможные остатки (например, если гость уже был частично перенесён).
        await self.session.execute(
            delete(CartItem).where(
                CartItem.session_id == session_id, CartItem.user_id.is_(None)
            )
        )
        await self.session.flush()

    # --- enrichment helpers ---

    @staticmethod
    def _validate_sku_available(snapshot: SkuSnapshot | None, quantity: int) -> None:
        if snapshot is None:
            raise ValueError("SKU not found")
        if snapshot.product_deleted:
            raise ValueError("SKU unavailable")
        if snapshot.product_status != "MODERATED":
            raise ValueError("SKU unavailable")
        if snapshot.active_quantity < quantity:
            raise ValueError("Insufficient stock")

    @staticmethod
    def _build_view(item: CartItem, snapshot: SkuSnapshot | None) -> CartItemView:
        if snapshot is None:
            return CartItemView(
                item_id=item.id,
                sku_id=item.sku_id,
                quantity=item.quantity,
                available=False,
                unavailable_reason="PRODUCT_DELETED",
                available_stock=0,
                line_total=0,
            )

        available = True
        reason: str | None = None
        if snapshot.product_deleted:
            available, reason = False, "PRODUCT_DELETED"
        elif snapshot.product_status in ("BLOCKED", "HARD_BLOCKED"):
            available, reason = False, "PRODUCT_BLOCKED"
        elif snapshot.product_status == "ON_MODERATION":
            available, reason = False, "ON_MODERATION"
        elif snapshot.product_status != "MODERATED":
            available, reason = False, "PRODUCT_BLOCKED"
        elif snapshot.active_quantity <= 0:
            available, reason = False, "OUT_OF_STOCK"

        line_total = snapshot.price * item.quantity if available else 0
        return CartItemView(
            item_id=item.id,
            sku_id=item.sku_id,
            product_id=snapshot.product_id,
            title=snapshot.name,
            price=snapshot.price,
            image=snapshot.image,
            quantity=item.quantity,
            available=available,
            unavailable_reason=reason,
            available_stock=snapshot.active_quantity,
            line_total=line_total,
        )

    @staticmethod
    def _build_summary(views: list[CartItemView]) -> CartSummary:
        available_views = [v for v in views if v.available]
        total_amount = sum(v.line_total for v in views)
        total_items = sum(v.quantity for v in available_views)
        unavailable_count = sum(1 for v in views if not v.available)
        checkout_ready = bool(views) and unavailable_count == 0 and all(
            v.available_stock is not None and v.available_stock >= v.quantity
            for v in available_views
        )
        return CartSummary(
            total_amount=total_amount,
            total_items=total_items,
            unavailable_count=unavailable_count,
            checkout_ready=checkout_ready,
        )

    @staticmethod
    def _build_checkout_payload(views: list[CartItemView]) -> CheckoutPayload:
        return CheckoutPayload(
            items=[
                CheckoutItem(sku_id=v.sku_id, quantity=v.quantity)
                for v in views
                if v.available
            ]
        )
