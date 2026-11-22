from src.routes.cart import router as cart_router
from src.routes.catalog import router as catalog_router
from src.routes.inventory import router as inventory_router
from src.routes.moderation import router as moderation_router
from src.routes.orders import router as orders_router
from src.routes.products import router as products_router
from src.routes.skus import router as skus_router

__all__ = [
    "products_router",
    "skus_router",
    "inventory_router",
    "moderation_router",
    "cart_router",
    "catalog_router",
    "orders_router",
]
