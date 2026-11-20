# NeoMarket B2B — API товара и SKU

Реализация endpoints для кабинета продавца NeoMarket B2B:
- `POST /api/v1/products` (B2B-1: Создание товара)
- `POST /api/v1/skus` (B2B-2: Создание SKU)
- `GET /api/v1/products/{id}` (B2B-5: Просмотр карточки товара)

## Стек

- **FastAPI** (ASGI, Pydantic v2 схемы)
- **SQLAlchemy 2.0** + **asyncpg** (async сессии, UUID как PostgreSQL `uuid`)
- **Alembic** — миграции БД
- **Pytest** + **pytest-asyncio** + **httpx** (AsyncClient, ASGITransport)
- **Docker** + **docker-compose** (приложение + PostgreSQL 15)
- **JWT**: `python-jose` для валидации токенов (`sub` = seller_id)

## Структура проекта

```
neo-market/
├── src/
│   ├── main.py                 # FastAPI приложение, регистрация роутеров
│   ├── config.py               # Настройки (env, DATABASE_URL, JWT_SECRET)
│   ├── database.py             # Engine, sessionmaker, Base
│   ├── auth/
│   │   └── dependencies.py     # get_current_seller_id, get_optional_seller_id
│   ├── models/
│   │   ├── __init__.py
│   │   ├── seller.py           # Seller (заготовка для auth)
│   │   ├── category.py         # Category
│   │   ├── product.py          # Product, ProductImage, ProductCharacteristic
│   │   ├── sku.py              # SKU, SKUCharacteristic
│   │   ├── outbox.py           # OutboxEvent
│   │   └── enums.py            # ProductStatus
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── product.py          # ProductCreate, ProductResponse
│   │   ├── sku.py              # SKUCreate, SKUResponse
│   │   └── errors.py           # Error (code, message, details?)
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── products.py         # POST /api/v1/products, GET /api/v1/products/{id}
│   │   └── skus.py             # POST /api/v1/skus
│   └── services/
│       ├── __init__.py
│       ├── product_service.py  # Создание товара + валидация категории
│       ├── sku_service.py      # Создание SKU + переход статуса + события
│       └── event_publisher.py  # Outbox-паттерн для событий
├── migrations/
│   ├── env.py
│   └── versions/
│       ├── 001_create_categories.py
│       ├── 002_create_products.py
│       └── 003_create_skus_and_outbox.py
├── tests/
│   ├── conftest.py             # Фикстуры: in-memory БД, сессия, JWT
│   ├── test_products.py        # 4 канонических теста (DoD)
│   ├── test_skus.py            # 10 канонических тестов SKU (DoD)
│   └── test_validation.py      # 5 дополнительных тестов валидации
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── requirements.txt
├── alembic.ini
└── README.md
```

## Быстрый старт

### Docker Compose

```bash
docker-compose up --build
```

Приложение:
1. Ждёт готовности PostgreSQL (healthcheck)
2. Запускает миграции Alembic (`alembic upgrade head`)
3. Стартует на `http://0.0.0.0:8000`

### Локальная разработка

```bash
# Установка зависимостей
pip install -r requirements.txt

# Переменные окружения
export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/neomarket"
export JWT_SECRET="your-secret-key"

# Миграции
alembic upgrade head

# Запуск сервера
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

## Запуск тестов

```bash
# Все тесты
pytest tests/ -v

# Kanon-тесты: товары (B2B-1 + B2B-5)
pytest tests/test_products.py -k "create_product_returns_201 or seller_id_taken or missing_images or missing_category or get_moderated or get_blocked or get_others or get_nonexistent" -v

# Kanon-тесты: SKU (B2B-2)
pytest tests/test_skus.py -k "first_sku_transitions_product_to_on_moderation or first_sku_emits_created_event_to_moderation or second_sku_transitions_moderated_product_to_on_moderation_and_emits_edited or add_sku_to_hard_blocked_returns_403 or missing_image_returns_400" -v

# С покрытием
pytest tests/ --cov=src --cov-report=term-missing
```

## API Endpoint

### Создание товара

**POST** `/api/v1/products`

**Headers:**
- `Authorization: Bearer <JWT_TOKEN>`
- `Content-Type: application/json`

**Тело запроса:**
```json
{
  "title": "iPhone 15 Pro Max",
  "description": "Последний смартфон Apple с чипом A17 Pro",
  "category_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "images": [
    {"url": "/s3/products/iphone15promax_1.jpg", "ordering": 0}
  ],
  "characteristics": [
    {"name": "Brand", "value": "Apple"},
    {"name": "Color", "value": "Natural Titanium"}
  ]
}
```

**Обязательные поля:** `title` (1-255), `description` (1-5000), `category_id` (UUID), `images` (мин. 1 элемент с `url` и `ordering`).

**Успешный ответ (201):**
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "seller_id": "seller-uuid-from-jwt-sub",
  "category_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "title": "iPhone 15 Pro Max",
  "slug": "iphone-15-pro-max",
  "description": "Последний смартфон Apple с чипом A17 Pro",
  "status": "CREATED",
  "deleted": false,
  "blocking_reason_id": null,
  "moderator_comment": null,
  "images": [
    {"id": "img-uuid", "url": "/s3/products/iphone15promax_1.jpg", "ordering": 0}
  ],
  "characteristics": [
    {"id": "char-uuid", "name": "Brand", "value": "Apple"},
    {"id": "char-uuid", "name": "Color", "value": "Natural Titanium"}
  ],
  "skus": [],
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Ошибки:**

| Ситуация | Код | Тело |
|----------|-----|------|
| Пустой title | 400 | `{"code":"INVALID_REQUEST","message":"title is required"}` |
| Title > 255 символов | 400 | `{"code":"INVALID_REQUEST","message":"title must be 1-255 characters"}` |
| Нет images / пустой массив | 400 | `{"code":"INVALID_REQUEST","message":"At least one image is required"}` |
| Невалидный UUID category_id | 400 | `{"code":"INVALID_REQUEST","message":"category_id must be a valid UUID"}` |
| Категория не найдена | 400 | `{"code":"INVALID_REQUEST","message":"Category not found"}` |
| Невалидный/отсутствующий JWT | 401 | `{"code":"UNAUTHORIZED","message":"..."}` |

> **Важно:** `seller_id` **всегда** берётся из `claims["sub"]` в JWT, никогда из тела запроса (защита от IDOR).

## Пример cURL

```bash
# Генерация тестового токена (замените на реальный JWT от auth-сервиса)
TOKEN=$(python -c "
import jwt, uuid
print(jwt.encode({'sub': str(uuid.uuid4())}, 'dev-secret-change-in-production', algorithm='HS256'))
")

curl -X POST http://localhost:8000/api/v1/products \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Product",
    "description": "Test Description",
    "category_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "images": [{"url": "/s3/test.jpg", "ordering": 0}]
  }'
```

### Создание SKU

**POST** `/api/v1/skus`

**Headers:**
- `Authorization: Bearer <JWT_TOKEN>`
- `Content-Type: application/json`

**Тело запроса:**
```json
{
  "product_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "256GB Black",
  "price": 12999000,
  "cost_price": 9500000,
  "discount": 0,
  "image": "/s3/iphone15-black-256.jpg",
  "characteristics": [
    {"name": "Цвет", "value": "Чёрный"},
    {"name": "Объём памяти", "value": "256 ГБ"}
  ]
}
```

**Обязательные поля:** `product_id` (UUID), `name` (1-255), `price` (>0 копеек), `cost_price` (>0 копеек), `image` (URL).

**Успешный ответ (201):**
```json
{
  "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "product_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "256GB Black",
  "price": 12999000,
  "cost_price": 9500000,
  "discount": 0,
  "image": "/s3/iphone15-black-256.jpg",
  "active_quantity": 0,
  "reserved_quantity": 0,
  "characteristics": [
    {"id": "char-uuid", "name": "Цвет", "value": "Чёрный"},
    {"id": "char-uuid", "name": "Объём памяти", "value": "256 ГБ"}
  ],
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Ошибки:**

| Ситуация | Код | Тело |
|----------|-----|------|
| product_id не существует | 404 | `{"code":"NOT_FOUND","message":"Product not found"}` |
| Товар в статусе HARD_BLOCKED | 403 | `{"code":"FORBIDDEN","message":"Cannot add SKU to hard-blocked product"}` |
| price <= 0 | 400 | `{"code":"INVALID_REQUEST","message":"price must be a positive integer (kopecks)"}` |
| cost_price <= 0 | 400 | `{"code":"INVALID_REQUEST","message":"cost_price must be a positive integer (kopecks)"}` |
| Пустой name | 400 | `{"code":"INVALID_REQUEST","message":"name is required"}` |
| Нет image | 400 | `{"code":"INVALID_REQUEST","message":"image is required"}` |
| Невалидный/отсутствующий JWT | 401 | `{"code":"UNAUTHORIZED","message":"..."}` |

> **Важно:** `seller_id` проверяется через владение товаром (JWT `sub` == `product.seller_id`). Продавец не может добавить SKU к чужому товару.

**Побочные эффекты (по канону B2B-2):**
- **Первый SKU для товара CREATED**: товар переходит в `ON_MODERATION`, отправляется событие `CREATED` в Moderation
- **Дополнительный SKU для MODERATED/BLOCKED**: товар переходит в `ON_MODERATION`, отправляется событие `EDITED` в Moderation
- События идут через outbox-паттерн с `X-Service-Key` и `idempotency_key`

### Пример cURL для SKU

```bash
curl -X POST http://localhost:8000/api/v1/skus \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "name": "256GB Black",
    "price": 12999000,
    "cost_price": 9500000,
    "discount": 0,
    "image": "/s3/iphone15-black-256.jpg",
    "characteristics": []
  }'
```

### Просмотр карточки товара

**GET** `/api/v1/products/{product_id}`

**Headers:**
- `Authorization: Bearer <JWT_TOKEN>` (seller-режим, опционален)
- `X-Service-Key: <KEY>` (service-режим, опционален)

Два режима доступа:
- **Seller (JWT)** — возвращает полный payload включая `cost_price` и `reserved_quantity` у SKU.
- **Service (`X-Service-Key`)** — если ключ совпадает с `settings.b2b_to_mod_key` или `settings.b2b_to_b2c_key`, ownership не проверяется, а `cost_price`/`reserved_quantity` возвращаются как `null`.

**Успешный ответ (200):** `ProductDetailResponse`
```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "seller_id": "seller-uuid",
  "category_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "title": "iPhone 15 Pro Max",
  "slug": "iphone-15-pro-max",
  "description": "Latest Apple smartphone",
  "status": "MODERATED",
  "deleted": false,
  "blocking_reason_id": null,
  "moderator_comment": null,
  "blocked": false,
  "blocking_reason": null,
  "field_reports": [],
  "images": [
    {"id": "img-uuid", "url": "/s3/iphone.jpg", "ordering": 0}
  ],
  "characteristics": [
    {"id": "char-uuid", "name": "Brand", "value": "Apple"}
  ],
  "skus": [
    {
      "id": "sku-uuid",
      "product_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "name": "256GB",
      "price": 120000,
      "cost_price": 95000,
      "discount": 5,
      "image": "/s3/iphone.jpg",
      "active_quantity": 10,
      "reserved_quantity": 2,
      "characteristics": [],
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-15T10:30:00Z"
    }
  ],
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

**Ошибки:**

| Ситуация | Код | Тело |
|----------|-----|------|
| Нет Authorization и нет X-Service-Key | 401 | `{"code":"UNAUTHORIZED","message":"Authorization header is missing"}` |
| Чужой товар (seller-режим) | 404 | `{"code":"NOT_FOUND","message":"Product not found"}` |
| Товар не существует | 404 | `{"code":"NOT_FOUND","message":"Product not found"}` |
| Невалидный UUID | 400 | `{"code":"INVALID_REQUEST","message":"..."}` |

> **Важно:** при чужом товаре в seller-режиме возвращается **404, не 403** (защита от перебора ID). `blocking_reason` собирается из `blocking_reason_id` + `moderator_comment`: `title = moderator_comment или status.value`, `comment = moderator_comment`. `field_reports` — пустой массив для MVP. `blocked = true` только для статусов `BLOCKED` / `HARD_BLOCKED`.

**Пример cURL (seller-режим):**
```bash
curl -X GET http://localhost:8000/api/v1/products/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -H "Authorization: Bearer $TOKEN"
```

**Пример cURL (service-режим):**
```bash
curl -X GET http://localhost:8000/api/v1/products/a1b2c3d4-e5f6-7890-abcd-ef1234567890 \
  -H "X-Service-Key: dev-b2b-to-mod-key"
```

## Схема БД

### Таблицы

- **sellers** — `id (PK)`, `email`, `created_at`
- **categories** — `id (PK)`, `name`, `parent_id (FK)`, `is_active`, `level`, `path`, `created_at`
- **products** — `id (PK)`, `seller_id (FK)`, `category_id (FK)`, `title`, `slug`, `description`, `status`, `deleted`, `blocking_reason_id`, `moderator_comment`, `created_at`, `updated_at`
- **product_images** — `id (PK)`, `product_id (FK)`, `url`, `ordering`
- **product_characteristics** — `id (PK)`, `product_id (FK)`, `name`, `value`
- **skus** — `id (PK)`, `product_id (FK)`, `name`, `price`, `cost_price`, `discount`, `image`, `active_quantity`, `reserved_quantity`, `created_at`, `updated_at`
- **sku_characteristics** — `id (PK)`, `sku_id (FK)`, `name`, `value`
- **outbox_events** — `id (PK)`, `event_type`, `aggregate_id`, `payload (JSONB)`, `service_key`, `target_url`, `created_at`, `processed_at`, `retry_count`

### ADR: Хранение характеристик

**Решение:** Отдельная таблица `product_characteristics` (нормализованная 3NF).

**Рассмотренные альтернативы:**
1. **JSONB в таблице Product** — простота, но фильтрация требует кастомных операторов (`@>`, `jsonb_path_ops`), индексы работают неэффективно.
2. **Отдельная таблица CharacteristicValue** (выбрано) — нормализация 3NF, индексы по `(product_id, name)`, простая фильтрация через JOIN.
3. **EAV-схема** (`entity_id`, `attribute_name`, `value`) — гибкость, но сложные self-join'ы при фильтрации, деградация производительности.

**Критерии выбора:**
- **Сложность запросов при фильтрации** — минимизирована: простой `JOIN` + `WHERE name = ... AND value = ...`, стандартные индексы работают эффективно.
- **Удобство добавления новых характеристик** — вставка новых пар `(name, value)` не требует изменения схемы; у JSONB нет строгой схемы, у EAV — нужно управлять атрибутами отдельно.

### ADR: Два режима доступа к GET /products/{id}

**Решение:** Один view с проверкой `X-Service-Key` в начале обработчика. При отсутствии/неверном ключе используется seller-режим (JWT). Service-режим (Moderation/B2C) — ownership не проверяется, `cost_price`/`reserved_quantity` возвращаются как `null`.

**Рассмотренные альтернативы:**
1. Один view с проверкой `X-Service-Key` (выбрано).
2. Два отдельных view (один с JWT, второй с `X-Service-Key` на другом пути/теге).
3. Отдельный permission-класс/dependencies для переключения режима.

**Критерии выбора:**
- **Читаемость кода** — единый endpoint, логика режима в одном месте, проще поддерживать OpenAPI (один path).
- **Риск утечки чувствительных полей** — явная проверка `X-Service-Key` в начале обработчика; два view удваивают код и риск рассинхронизации схем.

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/neomarket` | Async строка подключения к PostgreSQL |
| `JWT_SECRET` | `dev-secret-change-in-production` | Секрет для подписи JWT |
| `JWT_ALGORITHM` | `HS256` | Алгоритм JWT |
| `JWT_EXPIRES_MINUTES` | `30` | Время жизни токена |
| `APP_HOST` | `0.0.0.0` | Хост сервера |
| `APP_PORT` | `8000` | Порт сервера |
| `DEBUG` | `false` | Режим отладки (SQL echo) |
| `MODERATION_SERVICE_URL` | `http://moderation:8000` | URL сервиса Moderation |
| `B2B_TO_MOD_KEY` | `dev-b2b-to-mod-key` | Service key для B2B→Moderation |
| `B2C_SERVICE_URL` | `http://b2c:8000` | URL сервиса B2C |
| `B2B_TO_B2C_KEY` | `dev-b2b-to-b2c-key` | Service key для B2B→B2C |

## Definition of Done (DoD)

### B2B-1: Создание товара
- [x] `pytest tests/test_products.py -k "create_product_returns_201 or seller_id_taken or missing_images or missing_category"` — канонические тесты товаров проходят
- [x] `seller_id` берётся только из JWT `sub` (защита от IDOR)

### B2B-2: Создание SKU
- [x] `pytest tests/test_skus.py -k "first_sku_transitions_product_to_on_moderation or first_sku_emits_created_event_to_moderation or second_sku_transitions_moderated_product_to_on_moderation_and_emits_edited or add_sku_to_hard_blocked_returns_403 or missing_image_returns_400"` — канонические тесты SKU проходят
- [x] Товар без SKU не уходит на модерацию (ответственность B2B-2)
- [x] ADR: outbox-паттерн для событий в Moderation

### B2B-5: Просмотр карточки товара
- [x] `pytest tests/test_products.py -k "get_moderated or get_blocked or get_others or get_nonexistent"` — зелёный
- [x] Чужой товар → 404 (не 403)
- [x] `cost_price`/`reserved_quantity` только в seller-режиме
- [x] ADR: два режима доступа (один view с `X-Service-Key`)

### Общее
- [x] `pytest tests/ -v` — все 23 теста проходят
- [x] Приложение стартует в `docker-compose up`, `POST /api/v1/products` возвращает 201 со статусом `CREATED`, `POST /api/v1/skus` возвращает 201
- [x] Нет предупреждений pytest
- [x] ADR в описании PR с обоснованием выбора хранения характеристик
- [x] ADR в описании PR с обоснованием выбора outbox-паттерна для событий