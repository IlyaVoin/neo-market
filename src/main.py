from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.database import close_db, init_db
from src.routes import (
    cart_router,
    catalog_router,
    inventory_router,
    moderation_router,
    orders_router,
    products_router,
    skus_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()


app = FastAPI(
    title="NeoMarket B2B API",
    description="Seller Cabinet - Product Management",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(products_router)
app.include_router(skus_router)
app.include_router(inventory_router)
app.include_router(moderation_router)
app.include_router(cart_router)
app.include_router(catalog_router)
app.include_router(orders_router)


def _map_validation_error(errors: list[dict]) -> str:
    if not errors:
        return "Invalid request"

    first_error = errors[0]
    field = ".".join(str(loc) for loc in first_error["loc"])
    msg = first_error["msg"]
    error_type = first_error.get("type", "")

    # Map common validation errors to canon messages
    if "title" in field:
        if error_type == "string_too_long" or "at most" in msg:
            return "title must be 1-255 characters"
        if error_type == "missing" or "Field required" in msg:
            return "title is required"
        if error_type == "string_too_short" or "at least" in msg:
            return "title must be 1-255 characters"
    elif "images" in field:
        if error_type == "missing" or "Field required" in msg:
            return "At least one image is required"
        if "min_length" in error_type or "at least" in msg:
            return "At least one image is required"
    elif "category_id" in field:
        # Per canon: missing category_id or invalid UUID both return "Category not found" or "category_id must be a valid UUID"
        # The test expects "Category not found" for missing field
        if error_type == "missing" or "Field required" in msg:
            return "Category not found"
        if "uuid" in error_type or "uuid" in msg.lower():
            return "category_id must be a valid UUID"
        return "Category not found"
    elif "description" in field:
        if error_type == "missing" or "Field required" in msg:
            return "description is required"
        if error_type == "string_too_short" or "at least" in msg:
            return "description must be at least 1 character"

    return f"{field}: {msg}"


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    message = _map_validation_error(exc.errors())
    return JSONResponse(
        status_code=400,
        content={"code": "INVALID_REQUEST", "message": message},
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
    message = _map_validation_error(exc.errors())
    return JSONResponse(
        status_code=400,
        content={"code": "INVALID_REQUEST", "message": message},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": "ERROR", "message": str(exc.detail)},
    )


@app.get("/health")
async def health_check():
    return {"status": "ok"}
