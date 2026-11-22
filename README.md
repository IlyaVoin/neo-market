# NeoMarket B2B — API кабинета продавца

Реализация backend для B2B-кабинета продавца NeoMarket: товары и SKU, складской резерв,
корзина, каталог, заказы, модерация товаров и интеграция между сервисами (B2B ↔ Moderation ↔ B2C).

- **Приложение:** FastAPI, `title="NeoMarket B2B API"`, `version="0.1.0"`.
- **Аутентификация:** JWT для продавцов и модераторов (`sub` = seller_id/moderator_id); сервисные вызовы — по `X-Service-Key`.
- **События:** outbox-паттерн; интеграционные эндпоинты защищены service-key и идемпотентны.

## Стек

- **FastAPI** (ASGI, Pydantic v2 схемы)
- **SQLAlchemy 2.0** + **asyncpg** (async-сессии, UUID как PostgreSQL `uuid`)
- **Alembic** — миграции БД (001…009)
- **Pytest** + **pytest-asyncio** + **httpx** (AsyncClient, ASGITransport); SQLite in-memory для тестов
- **Docker** + **docker-compose** (приложение + PostgreSQL 15)
- **JWT**: `python-jose` (валидация токенов, `sub` = идентификатор субъекта)

## Структура проекта

```
neo-market/
├── src/
│   ├── main.py                      # FastAPI app, регистрация роутеров, error-handlers
│   ├── config.py                    # Settings (pydantic-settings, env/.env)
│   ├── database.py                  # Engine, sessionmaker, Base (expire_on_commit=False)
│   ├── auth/dependencies.py         # get_current_seller_id, get_optional_seller_id, moderator
│   ├── models/
│   │   ├── enums.py                 # ProductStatus, OrderStatus, ModerationStatus
│   │   ├── seller.py / category.py  # Seller, Category
│   │   ├── product.py               # Product, ProductImage, ProductCharacteristic
│   │   ├── sku.py                   # SKU, SKUCharacteristic
│   │   ├── inventory.py             # ReserveOperation
│   │   ├── moderation.py            # ProcessedEvent (идемпотентность приёма событий)
│   │   ├── product_moderation.py    # ProductModeration (карточка модерации, аудиты)
│   │   ├── product_blocking_reason.py # ProductBlockingReason (справочник причин, hard_block)
│   │   ├── cart.py                  # CartItem
│   │   ├── order.py                 # Order, OrderItem
│   │   └── outbox.py                # OutboxEvent
│   ├── schemas/
│   │   ├── product.py / sku.py / errors.py
│   │   ├── moderation.py            # DeclineRequest, ApproveRequest, события
│   │   ├── inventory.py / cart.py / catalog.py / product_card.py / order.py
│   ├── routes/
│   │   ├── products.py / skus.py / inventory.py
│   │   ├── moderation.py / cart.py / catalog.py / orders.py
│   └── services/
│       ├── product_service.py / sku_service.py
│       ├── inventory_service.py / cart_service.py / catalog_service.py
│       ├── order_service.py / moderation_service.py / moderation_approval_service.py
│       ├── event_publisher.py       # outbox → B2B
│       ├── b2b_client.py / catalog_b2b_client.py  # исходящие интеграционные вызовы
├── migrations/versions/001…009       # Alembic-миграции (вкл. сид blocking_reasons)
├── tests/                            # pytest (см. раздел «Тесты»)
├── Dockerfile / docker-compose.yml / pyproject.toml / requirements.txt
```

## Быстрый старт

### Docker Compose

```bash
docker-compose up --build
```

Приложение:
1. ждёт готовности PostgreSQL (healthcheck);
2. выполняет `alembic upgrade head`;
3. стартует на `http://0.0.0.0:8000`.

### Локальная разработка

```bash
pip install -r requirements.txt

export DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/neomarket"
export JWT_SECRET="your-secret-key"

alembic upgrade head
uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
```

## Тесты

```bash
# Все тесты (95 passed)
pytest -q

# Отдельные потоки
pytest tests/test_products.py tests/test_skus.py -q          # B2B-1, B2B-2
pytest tests/test_product_card.py -q                          # B2C-3 карточка товара
pytest tests/test_catalog.py -q                               # каталог
pytest tests/test_cart.py -q                                  # корзина
pytest tests/test_orders.py tests/test_order_cancel.py -q     # заказы + отмена
pytest tests/test_inventory.py -q                             # резерв/унрезерв
pytest tests/test_moderation_approve.py -q                    # MOD-3 одобрение
pytest tests/test_moderation_hard_block.py -q                 # MOD-5 жёсткая блокировка
pytest tests/test_moderation.py -q                            # MOD-1 приём событий

# Покрытие
pytest tests/ --cov=src --cov-report=term-missing

# Линтер (ruff)
ruff check src tests
```

## Аутентификация и режимы доступа

- **Продавец** — `Authorization: Bearer <JWT>`; `sub` = `seller_id`. Владение проверяется по `product.seller_id == sub`.
- **Модератор** — тот же JWT, но `sub` = `moderator_id` (эндпоинты `approve`/`decline` moderation-роутера).
- **Сервисный режим** — заголовок `X-Service-Key`:
  - `b2b_to_mod_key` — B2B → Moderation (`POST /api/v1/events/product`, приём модерации).
  - `mod_to_b2b_key` / `b2b_to_b2c_key` — Moderation→B2B и B2B→B2C события.
  - `b2b_to_mod_key` / `b2b_to_b2c_key` — доступ к service-режиму `GET /api/v1/products/{id}`.
- `seller_id`/`buyer_id`/`moderator_id` **всегда** берутся из JWT (`claims["sub"]`), никогда из тела запроса (защита от IDOR).

## API Endpoints

Все пути префиксованы (`/api/v1`). Коды ошибок выдаются единообразно: `{"code": "...", "message": "..."}`.

### Товары — `/api/v1/products`

| Метод | Путь | Назначение | Статус |
|-------|------|------------|--------|
| POST | `""` | Создать товар (B2B-1) | 201 |
| GET | `""` | Список товаров (B2C, service-key `b2b_to_b2c_key`), `ids`/`limit`/`offset` | 200 |
| GET | `/{product_id}` | Карточка товара (seller/service-режимы, B2B-5) | 200 |
| PATCH | `/{product_id}` | Обновить товар | 200 |
| DELETE | `/{product_id}` | Мягкое удаление (`deleted=true`) | 204 |

**POST — тело:** `title` (1–255), `description` (1–5000), `category_id` (UUID), `images` (≥1 `{url, ordering}`), `characteristics? [{name,value}]`.
**PATCH — тело:** частичное обновление (`title`, `description`, `category_id`, `images`, `characteristics`).
**Ошибки:** 400 (валидация/категория), 401 (нет/невалидный JWT), 403 (HARD_BLOCKED: «Cannot edit/delete hard-blocked product»), 404 (не найдено/чужой товар).

> `seller_id` всегда из JWT. `GET /{id}`: seller-режим отдаёт `cost_price`/`reserved_quantity`, service-режим возвращает их `null` и не проверяет ownership. Чужой товар в seller-режиме → **404** (защита от перебора ID). `blocked=true` только для `BLOCKED`/`HARD_BLOCKED`.

### SKU — `/api/v1/skus`

| Метод | Путь | Назначение | Статус |
|-------|------|------------|--------|
| POST | `""` | Создать SKU (B2B-2) | 201 |

**Тело:** `product_id` (UUID), `name` (1–255), `price` (>0, копейки), `cost_price` (>0), `image` (URL), `discount?`, `characteristics?`.
**Ошибки:** 400 (валидация), 401, 403 (HARD_BLOCKED: «Cannot add SKU to hard-blocked product»), 404 (товар не найден/чужой).

**Побочные эффекты (канон B2B-2):**
- первый SKU для `CREATED` → товар `ON_MODERATION`, событие `CREATED` в Moderation;
- доп. SKU для `MODERATED`/`BLOCKED` → `ON_MODERATION`, событие `EDITED`;
- события идут через outbox (`X-Service-Key` + `idempotency_key`).

### Резерв склада — `/api/v1`

| Метод | Путь | Назначение |
|-------|------|------------|
| POST | `/reserve` | Зарезервировать SKU (алиас `/inventory/reserve`) |
| POST | `/unreserve` | Снять резерв (алиас `/inventory/unreserve`) |

**Тело:** список позиций `{sku_id, quantity}`; проверка остатка. **Ошибки:** 401, 404 (SKU не найден), 409 (недостаточно остатка/конфликт).

### Корзина — `/api/v1/cart`

| Метод | Путь | Назначение | Статус |
|-------|------|------------|--------|
| GET | `""` | Содержимое корзины | 200 |
| POST | `/items` | Добавить/увеличить позицию (`{sku_id, quantity}`) | 201 / 200 |
| PUT | `/items/{item_id}` | Изменить количество позиции | 200 |
| DELETE | `/items/{item_id}` | Удалить позицию | 204 |
| DELETE | `""` | Очистить корзину | 204 |
| GET | `/validate` | Валидация корзины (остатки/цен) | 200 |

**Ошибки:** 401, 404 (позиция/SKU не найдена), 409 (недоступно/недостаточно остатка).

### Каталог — `/api/v1/catalog`

| Метод | Путь | Назначение | Статус |
|-------|------|------------|--------|
| GET | `/products` | Список товаров каталога (поиск, сортировка) | 200 |
| GET | `/products/{product_id}` | Карточка товара в каталоге | 200 |
| GET | `/facets` | Фасеты фильтрации | 200 |
| GET | `/categories/{category_id}/filters` | Фильтры категории | 200 |

**Допустимые сортировки:** `rating, popularity, price_asc, price_desc, date_desc, discount_desc` (алиас `new` → `date_desc`).
**Ошибки:** 401, 400 (невалидный sort/параметр), 404 (товар/категория не найдены), 502 (B2B недоступен — флаг `catalog_b2b_unavailable`).

### Заказы — `/api/v1/orders`

| Метод | Путь | Назначение | Статус |
|-------|------|------------|--------|
| POST | `""` | Оформить заказ (идемпотентно по `Idempotency-Key`) | 201 |
| POST | `/{order_id}/cancel` | Отменить заказ | 200 (`CANCELLED` / `CANCEL_PENDING`) |

**POST — тело:** позиции `{sku_id, quantity}`, адрес/получатель. **Ошибки:** 400 (валидация), 401, 409 (резерв/идемпотентность), 422 (невалидное количество), 503 (B2B недоступен — флаг `b2b_unavailable`).
**Cancel:** 200 — `CANCELLED` (успех) или `CANCEL_PENDING` (требует подтверждения); 404 (не найден/чужой), 409 (статус не допускает отмену, тело содержит `current_status`).

### Модерация — `/api/v1`

| Метод | Путь | Auth | Назначение |
|-------|------|------|------------|
| POST | `/events/moderation` | `X-Service-Key` (`mod_to_b2b_key`) | Приём события модерации (канон, `status` + `hard_block`) |
| POST | `/moderation/events` | `X-Service-Key` (`mod_to_b2b_key`) | Приём события (OpenAPI-схема `event_type`) → 204 |
| POST | `/products/{product_id}/approve` | JWT модератора | Одобрить товар (MOD-3) |
| POST | `/products/{product_id}/decline` | JWT модератора | Блокировка: soft/hard по `hard_block` причины (MOD-4/MOD-5) |
| POST | `/events/product` | `X-Service-Key` (`b2b_to_mod_key`) | B2B→Moderation: `CREATED`/`EDITED`/`DELETED` (MOD-1) |

**approve — тело:** `{moderator_comment?}`. **Ответ:** `{product_id, status:"MODERATED"}`.
**decline — тело:** `{blocking_reason_id, moderator_comment?, field_reports?}`. **Ответ:** `{product_id, status:"BLOCKED"|"HARD_BLOCKED"}`.

**Порядок предусловий decline:** 404 (нет карточки) → 409 (`HARD_BLOCKED`) → 409 (не `IN_REVIEW`) → 403 (чужой модератор) → 400 (причина не найдена).
**Эффект:** карточка → `HARD_BLOCKED`/`BLOCKED`; событие `BLOCKED` (`hard_block`, `blocking_reason`, `field_reports`) в outbox→B2B; `Product.status` → `HARD_BLOCKED`/`BLOCKED`.

**`POST /events/product`:** `EDITED` на `HARD_BLOCKED` → игнор (200); `DELETED` удаляет карточку (даже `HARD_BLOCKED`); `CREATED` создаёт `PENDING` (дубль → 400). **Ошибки:** 400 (невалидное/дубль), 401.

## Схема БД

Таблицы (миграции 001–009):

- **sellers** — `id (PK)`, `email`, `created_at`
- **categories** — `id (PK)`, `name`, `parent_id (FK)`, `is_active`, `level`, `path`, `created_at`
- **products** — `id (PK)`, `seller_id (FK)`, `category_id (FK)`, `title`, `slug`, `description`, `status`, `deleted`, `blocking_reason_id`, `moderator_comment`, `created_at`, `updated_at`
- **product_images** — `id (PK)`, `product_id (FK)`, `url`, `ordering`
- **product_characteristics** — `id (PK)`, `product_id (FK)`, `name`, `value`
- **skus** — `id (PK)`, `product_id (FK)`, `name`, `price`, `cost_price`, `discount`, `image`, `active_quantity`, `reserved_quantity`, `created_at`, `updated_at`
- **sku_characteristics** — `id (PK)`, `sku_id (FK)`, `name`, `value`
- **reserve_operations** — операции резерва/унрезерва (аудит склада)
- **processed_events** — обработанные события (идемпотентность приёма модерации)
- **product_moderation** — карточка модерации (`status`, `moderator_id`, `date_moderation`, `moderator_comment`, `blocking_reason_id`, `field_reports`)
- **product_blocking_reasons** — справочник причин (`code`, `title`, `hard_block`); сид в 009: 3 hard (`COUNTERFEIT`, `FORBIDDEN_GOODS`, `COPYRIGHT`) + soft
- **cart_items** — позиции корзины
- **orders** / **order_items** — заказы (`status`, `buyer_id`, суммы, позиции)
- **outbox_events** — `id (PK)`, `event_type`, `aggregate_id`, `payload (JSONB)`, `service_key`, `target_url`, `created_at`, `processed_at`, `retry_count`

### Статусы (enums)

- **ProductStatus:** `CREATED`, `ON_MODERATION`, `MODERATED`, `BLOCKED`, `HARD_BLOCKED`
- **OrderStatus:** `CREATED`, `PAID`, `ASSEMBLING`, `DELIVERING`, `DELIVERED`, `CANCELLED`, `CANCEL_PENDING`
- **ModerationStatus:** `PENDING`, `IN_REVIEW`, `MODERATED`, `BLOCKED`, `HARD_BLOCKED`

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/neomarket` | Async-строка подключения к PostgreSQL |
| `JWT_SECRET` | `dev-secret-change-in-production` | Секрет подписи JWT |
| `JWT_ALGORITHM` | `HS256` | Алгоритм JWT |
| `JWT_EXPIRES_MINUTES` | `30` | Время жизни токена |
| `APP_HOST` | `0.0.0.0` | Хост сервера |
| `APP_PORT` | `8000` | Порт сервера |
| `DEBUG` | `false` | Режим отладки (SQL echo) |
| `MODERATION_SERVICE_URL` | `http://moderation:8000` | URL сервиса Moderation |
| `B2B_TO_MOD_KEY` | `dev-b2b-to-mod-key` | Service-key B2B→Moderation |
| `MOD_TO_B2B_KEY` | `dev-mod-to-b2b-key` | Service-key Moderation→B2B |
| `B2B_SERVICE_URL` | `http://b2b:8000` | URL сервиса B2B (для доставки событий Moderation) |
| `B2C_SERVICE_URL` | `http://b2c:8000` | URL сервиса B2C |
| `B2B_TO_B2C_KEY` | `dev-b2b-to-b2c-key` | Service-key B2B→B2C |
| `CATALOG_B2B_UNAVAILABLE` | `false` | Имитация недоступности B2B для каталога (тесты/ops) |
| `B2B_UNAVAILABLE` | `false` | Имитация недоступности B2B для checkout (тесты/ops) |

## Архитектурные решения (ADR)

### Хранение характеристик
Отдельная таблица `product_characteristics` (нормализация 3NF): простая фильтрация через `JOIN` + `WHERE name = ... AND value = ...`, эффективные стандартные индексы по `(product_id, name)`. Альтернативы (JSONB, EAV) отвергнуты — сложнее запросы/сопровождение.

### Два режима доступа к `GET /products/{id}`
Один view с проверкой `X-Service-Key` в начале обработчика: service-режим не проверяет ownership и скрывает `cost_price`/`reserved_quantity` (`null`); иначе — seller-режим по JWT. Альтернатива с двумя view отвергнута — риск рассинхронизации и удвоение кода.

### Outbox-паттерн для событий
События в Moderation/B2C публикуются через таблицу `outbox_events` с `idempotency_key`, что гарантирует доставку и защиту от дублей при ретраях.

### Терминальная блокировка `HARD_BLOCKED`
Терминальный enum-статус + проверки на всех мутирующих endpoint'ах (PATCH/DELETE товара, POST SKU → 403) как единый источник правды. Moderation ставит `HARD_BLOCKED` только из `IN_REVIEW`, игнорирует `EDITED`, удаляет по `DELETED`. Снятие — только суперадмин через Django Admin (вне скоупа MVP). Альтернативы (флаг `is_terminal`, отдельная таблица-архив) отвергнуты — риск рассинхронизации и сложность аудита.

## Definition of Done (DoD)

### B2B-1: Создание товара — `test_products.py`
- [x] `create_product_returns_201`, `seller_id_taken`, `missing_images`, `missing_category` — канонические тесты
- [x] `seller_id` только из JWT `sub` (защита от IDOR)

### B2B-2: Создание SKU — `test_skus.py`
- [x] `first_sku_transitions_product_to_on_moderation`, `first_sku_emits_created_event_to_moderation`, `second_sku_transitions_moderated_product_to_on_moderation_and_emits_edited`, `add_sku_to_hard_blocked_returns_403`, `missing_image_returns_400`
- [x] Товар без SKU не уходит на модерацию (ответственность B2B-2)

### B2B-5: Просмотр карточки товара — `test_products.py`
- [x] `get_moderated`, `get_blocked`, `get_others`, `get_nonexistent`
- [x] Чужой товар → 404 (не 403)
- [x] `cost_price`/`reserved_quantity` только в seller-режиме

### B2C-3: Карточка товара для B2C — `test_product_card.py`
- [x] 5 тестов: отображение, скрытие `cost_price`/`reserved_quantity` в B2C-режиме, 404/403-семантика

### Каталог — `test_catalog.py`
- [x] 6 тестов: поиск, сортировки (вкл. `new`), фасеты, фильтры категории, 502 при недоступности B2B

### Корзина — `test_cart.py`
- [x] 7 тестов: получение, добавление (201/200), изменение, удаление, очистка, валидация, 404/409

### Резерв склада — `test_inventory.py`
- [x] 8 тестов: reserve/unreserve (канон + алиасы), остатки, 404/409

### Заказы — `test_orders.py`, `test_order_cancel.py`
- [x] 10 тестов оформления: создание, идемпотентность, 400/409/422/503
- [x] 6 тестов отмены: `CANCELLED`/`CANCEL_PENDING`, 404/409

### Модерация: приём событий — `test_moderation.py`
- [x] 8 тестов: `POST /events/moderation` и `/moderation/events`, идемпотентность, `hard_block`, 404/401

### Модерация: одобрение (MOD-3) — `test_moderation_approve.py`
- [x] 8 тестов: approve → `MODERATED`, каскад в B2C, 404/403/409

### Модерация: жёсткая блокировка (MOD-5) — `test_moderation_hard_block.py`
- [x] `hard_block_transitions_to_terminal_and_emits_event`
- [x] `hard_block_event_carries_hard_block_true`
- [x] `any_modify_on_hard_blocked_returns_403` (PATCH/DELETE товара + POST SKU)
- [x] `edited_event_on_hard_blocked_is_ignored`
- [x] `deleted_event_removes_hard_blocked`
- [x] soft-block → `BLOCKED`; повторный decline → 409; чужой модератор → 403; неизвестная причина → 400

### Общее
- [x] `pytest` — 95 тестов проходят
- [x] Приложение стартует в `docker-compose up`; create product/sku → 201
- [x] `ruff` по изменённым файлам — только принятый базовый уровень (B008/TRY003)