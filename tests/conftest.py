import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.config import settings
from src.database import Base, get_session
from src.main import app
from src.models import Category, Seller

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)

TestAsyncSessionMaker = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def init_test_db():
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with TestAsyncSessionMaker() as session:
        yield session


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_session():
        yield session

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
def jwt_secret() -> str:
    return settings.jwt_secret


@pytest.fixture
def jwt_algorithm() -> str:
    return settings.jwt_algorithm


def create_token(seller_id: uuid.UUID, secret: str, algorithm: str) -> str:
    return jwt.encode({"sub": str(seller_id)}, secret, algorithm=algorithm)


@pytest.fixture
def valid_token(jwt_secret: str, jwt_algorithm: str) -> str:
    seller_id = uuid.uuid4()
    return create_token(seller_id, jwt_secret, jwt_algorithm)


@pytest.fixture
def seller_id_from_token(valid_token: str, jwt_secret: str, jwt_algorithm: str) -> uuid.UUID:
    payload = jwt.decode(valid_token, jwt_secret, algorithms=[jwt_algorithm])
    return uuid.UUID(payload["sub"])


@pytest.fixture
def category_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest_asyncio.fixture
async def test_category(session: AsyncSession, category_id: uuid.UUID) -> Category:
    category = Category(
        id=category_id,
        name="Electronics",
        path="/electronics",
        level=0,
        is_active=True,
    )
    session.add(category)
    await session.commit()
    return category


@pytest_asyncio.fixture
async def test_seller(session: AsyncSession, seller_id_from_token: uuid.UUID) -> Seller:
    seller = Seller(
        id=seller_id_from_token,
        email="test@example.com",
    )
    session.add(seller)
    await session.commit()
    return seller
