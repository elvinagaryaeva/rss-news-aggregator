# RSS Aggregator

Учебное web-приложение на FastAPI: агрегирует статьи из внешних и собственного RSS-каналов, применяет персональные keyword- и semantic-фильтры и объясняет каждое совпадение.
## Реализовано

- PostgreSQL, SQLAlchemy 2.x и Alembic migration;
- хранение RSS-источников и статей;
- seed пользователя `demo`, demo-источников и `FeedSubscription`;
- загрузка RSS через отдельный сервисный слой;
- дедупликация статей;
- запись состояния обновления и ошибок каждого канала;
- ручное и периодическое обновление всех активных источников;
- JSON API и минимальный Jinja2-интерфейс;
- Docker Compose, конфигурация через environment variables и базовые тесты.

## Архитектура

```
app/
  api.py                 # JSON API
  web.py                 # HTML endpoints
  models.py              # SQLAlchemy models
  services/rss_service.py# download, parse, normalize, persist
  services/test_rss_service.py # own RSS XML and publication
  services/semantic_service.py # local model, cosine similarity, embedding cache
  services/user_service.py # current user and per-user subscriptions
  services/seed.py       # demo user and configurable feeds
  test_rss.py             # publication UI/API and RSS endpoint
  users.py                # user UI and subscription API
  templates/, static/    # minimal server-rendered UI
  db/, core/             # DB session and settings
alembic/                 # migrations
tests/                   # unit/API tests and RSS fixture
```

`RSSService` скачивает один канал, парсит его с `feedparser`, нормализует поля и сохраняет новые статьи. Ошибка одного источника фиксируется в `rss_feeds.last_error` и не прерывает refresh остальных источников. APScheduler запускает обновление по `RSS_FETCH_INTERVAL_SECONDS`; кнопка на `/feeds` и `POST /api/feeds/refresh` запускают его вручную.

Для каждой статьи строится SHA-256 fingerprint: `guid + URL канала`, иначе canonical URL, иначе набор `feed URL + title + publication time`. В БД на fingerprint есть уникальное ограничение — повторный refresh пропускает уже сохранённые статьи.

## Таблицы

- `users` — пользователи; в startup seed создаётся `demo`;
- `rss_feeds` — источник, URL, статус и metadata загрузок;
- `feed_subscriptions` — связь user/feed с уникальностью `(user_id, feed_id)`;
- `articles` — нормализованные RSS статьи и уникальный `fingerprint`.

## Запуск

1. Создайте локальный конфиг: `Copy-Item .env.example .env` (PowerShell). Можно не создавать: Compose использует значения по умолчанию.
2. Запустите:

   ```bash
   docker compose up -d --build
   ```

3. Откройте <http://localhost:8000>. На `/users` создайте и выберите пользователя. На `/feeds` управляйте его подписками, на `/filters` — keyword- и semantic-фильтрами, на `/test-rss` публикуйте статьи в собственный канал.

Остановить сервисы:

```bash
docker compose down
```

Полностью пересоздать БД (удалит локальные данные PostgreSQL):

```bash
docker compose down -v
docker compose up --build
```

## API

- `GET /api/health` — health check;
- `GET /api/feeds` — RSS-источники;
- `GET /api/articles?limit=50` — агрегированная лента;
- `POST /api/feeds` — создать источник и подписать текущего пользователя либо подписаться на существующий URL; для нового источника выполняется initial fetch. Body: `{"name":"Example","url":"https://example.org/rss.xml"}`;
- `POST /api/feeds/refresh` — обновить все активные источники.
- `GET /api/test-rss/posts` — собственные публикации;
- `POST /api/test-rss/posts` — опубликовать запись и запустить targeted RSS refresh;
- `GET /api/test-rss/posts/{id}` — публикация по ID;
- `GET /rss/test.xml` — собственный RSS 2.0 XML.
- `GET/POST /api/users`, `GET/DELETE /api/users/{id}` — управление пользователями;
- `GET /api/users/{id}/feeds`, `POST/DELETE /api/users/{id}/feeds/{feed_id}` — подписки;
- `POST /api/users/{id}/feeds` — добавить RSS URL пользователю или подписать на уже известный URL;
- `GET/POST/PATCH/DELETE /api/filters` и `GET /api/articles/filtered` — персональные фильтры и выдача. Для API можно указать `user_id`; без него используется текущий пользователь сессии.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy connection URL | Docker PostgreSQL URL |
| `RSS_FETCH_INTERVAL_SECONDS` | automatic refresh period | `300` |
| `RSS_REQUEST_TIMEOUT_SECONDS` | RSS HTTP timeout | `20` |
| `APP_PORT` | host port exposed by Compose | `8000` |
| `ENVIRONMENT` | environment label | `development` |
| `INTERNAL_BASE_URL` | адрес приложения для self-fetch внутри Docker | `http://app:8000` |
| `PUBLIC_BASE_URL` | адрес ссылок в RSS для браузера | `http://localhost:8000` |
| `SEMANTIC_MODEL_NAME` | локальная multilingual sentence-transformer | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| `SESSION_SECRET` | ключ подписи cookie-сессии; задайте собственный для общего стенда | development fallback |
| `DEMO_RSS_FEEDS` | `Name|URL;Name|URL` demo feed list | three public feeds |

Если внешний порт отличается от `8000`, задайте `PUBLIC_BASE_URL` соответственно. При запуске без Docker задайте `INTERNAL_BASE_URL` как адрес локального Uvicorn, например `http://127.0.0.1:8000`.

## Tests

```bash
docker compose exec app python -m pytest -q
```

Набор тестов охватывает анализ RSS, сохранение данных, дедупликацию, недопустимые источники, фильтрацию по ключевым словам/семантическую фильтрацию, объяснимость, публикацию, многопользовательские подписки и конечную точку проверки работоспособности. Он использует SQLite в оперативной памяти исключительно для изоляции тестов; Docker использует PostgreSQL.


## Фильтрация ключевых слов

Добавляется персональная, объяснимая фильтрация ключевых слов для существующего пользователя «демо». «Фильтр» принадлежит «Пользователю»; Ключевые слова хранятся как отдельные записи FilterKeyword, а не как поле базы данных, разделенное запятыми. Каждое ключевое слово нормализуется с помощью Unicode NFKC, `casefold()` и свертывания пробелов. Сопоставление не учитывает регистр для русского и английского языков, поддерживает фразы, состоящие из нескольких слов, и использует границы слов Юникода, поэтому `AI` не соответствует середине несвязанного слова.

`ANY` включает в себя статью, если хотя бы одно ключевое слово встречается в заголовке, сводке или содержании. `ALL` требует каждого ключевого слова. Сохраненный «ArticleFilterMatch» включает в себя метод ключевых слов, оценку («соответствует/общая»), ключевые слова с точным соответствием, поля, в которых каждое из них было найдено, а также «подробности» JSON с полным объяснением. Уникальное ограничение `(article_id, filter_id)` предотвращает дублирование совпадений.

```
RSSFeed → Article → Matching Engine → ArticleFilterMatch
                                  ↑              ↑
                                Filter ───────── User
                                  ↑
                            FilterKeyword
```

Жизненный цикл:

- При создании или изменении одного фильтра этот фильтр сравнивается с существующими статьями только из каналов, на которые подписан его владелец.
- Удаление фильтра каскадируется по его ключевым словам и совпадениям.
- При приеме RSS сбрасываются только недавно сохраненные статьи, а затем сравниваются их только с активными фильтрами ключевых слов, принадлежащими пользователям, подписанным на канал этой статьи. Он не пересчитывает полный корпус статей при каждом обновлении.

### UI and API

- `/filters` — позволяет создавать, редактировать, активировать/деактивировать, удалять и переоценивать фильтры демо-пользователей. Ключевые слова принимают значения, разделенные запятыми, или по одному в строке.
- `/` теперь начинается с личного отфильтрованного канала и краткого понятного для человека объяснения для каждого соответствующего фильтра.
- `GET /api/filters`, `POST /api/filters`, `PATCH /api/filters/{id}`, `DELETE /api/filters/{id}`.
- `GET /api/articles/filtered` и `GET /api/articles/{id}/matches` предоставляют то же объяснение, что и данные. Необязательный параметр запроса user_id поддерживает существующую многопользовательскую модель данных без добавления аутентификации.

Новая миграция: `0002_keyword_filters` создает `filters`, `filter_keywords` и `article_filter_matches`.


## Локальный тестовый RSS канал

`/test-rss` позволяет опубликовать статью и сразу показать полный путь агрегирования. Исходные записи хранятся в отдельной таблице `test_rss_posts` с уникальным стабильным UUID GUID. `GET /rss/test.xml` формирует валидный RSS 2.0 через XML builder, который экранирует пользовательский текст. Опциональный plain-text `content` передаётся через стандартный `content:encoded` extension; `pubDate` выводится в UTC по RFC 2822.

При старте создаётся обычный `RSSFeed` с внутренним URL `http://app:8000/rss/test.xml`, а пользователь `demo` подписывается на него. Публикация сначала сохраняет `TestRSSPost`, затем вызывает targeted `RSSService.refresh_feed()`; `Article` не вставляется напрямую. Для ссылок `channel` и `item` используется `PUBLIC_BASE_URL`.

```
Test RSS UI → TestRSSPost → /rss/test.xml → RSSService → Article
                                                      ↓
                                               FilterService
                                                      ↓
                                            ArticleFilterMatch
                                                      ↓
                                                Filtered Feed
```

Демонстрация: создайте на `/filters` фильтр `OpenAI` с keyword `OpenAI` в режиме `ANY`; на `/test-rss` опубликуйте статью `OpenAI releases new model`; откройте главную страницу и раскройте объяснение совпадения. Затем откройте `/rss/test.xml`, чтобы показать ту же запись как настоящий RSS item, и `/test-rss/posts/{id}` для исходной страницы публикации. Повторный refresh не создаёт дубликат статьи благодаря GUID fingerprint.

## Semantic filtering

На `/filters` можно создать отдельный semantic-фильтр: задать название, тему и порог сходства (по умолчанию `0.20`). Keyword-фильтры продолжают работать по правилам `ANY`/`ALL`; semantic-фильтры не используют список ключевых слов. Тип уже созданного фильтра не меняется — для другого типа создайте новый.

Используется локальная модель [`intfloat/multilingual-e5-small`](https://huggingface.co/intfloat/multilingual-e5-small) с поддержкой русского и английского. При первом использовании semantic-фильтра скачивает её; затем файлы берутся из локального кеша. В Docker кеш находится в volume `hf_cache`, поэтому обычный перезапуск контейнера его сохраняет. Для локального запуска можно задать `HF_HOME` на папку кеша. Первое создание фильтра может занять время и требует доступа к модели. Keyword-фильтры и RSS работают независимо от загрузки модели.

Текст статьи составляется из title, summary и ограниченного по длине content. Для каждой пары `(article_id, model_name)` в `article_embeddings` хранится один вектор; для каждой пары `(filter_id, model_name)` в `filter_embeddings` хранится вектор темы. Изменение темы обновляет его, изменение только порога использует прежний. Модель загружается лениво один раз на процесс. Отдельная векторная БД не требуется.

```
Article → Article embedding ───────┐
                                   ├→ cosine similarity → threshold → ArticleFilterMatch
Filter topic → Filter embedding ───┘
```

При создании или изменении фильтра пересчитываются только статьи из подписок его владельца. Новые RSS-статьи проходят через прежний `FilterService`: он выбирает активные фильтры пользователей, подписанных на источник. Если сходство не ниже порога, в существующей `ArticleFilterMatch` сохраняются `method=semantic`, score и `details` с темой, сходством, порогом и именем модели. UI и `GET /api/articles/filtered` показывают эти данные без выдуманных «совпавших слов».

API: `POST /api/filters` принимает `{"name":"Football transfers","filter_type":"semantic","semantic_query":"Player transfers and recruitment","semantic_threshold":0.2}`. `PATCH /api/filters/{id}` изменяет тему, порог, название или активность; `GET /api/filters` возвращает конфигурацию. `semantic_threshold` должен лежать между `0` и `1`.

Миграция `0004_semantic_filtering` добавляет настройки фильтра и таблицы кеша поверх `0003`. Реальная модель не загружается при запуске приложения или при выполнении unit tests с fake encoder.

## Мультипользовательский режим

`/users` показывает пользователей, позволяет создать нового и выбрать его как текущего в этом браузере. Выбор хранится в подписанной cookie-сессии Starlette. Если выбранный пользователь удалён или cookie отсутствует, приложение использует `demo`. Для общего стенда задайте приватный `SESSION_SECRET` в `.env`; без него используется development fallback с предупреждением в логах.

```
User ── FeedSubscription ──→ RSSFeed ──→ Article
  │                                      └── ArticleEmbedding
  └──── Filter ──→ ArticleFilterMatch ──→ Article
         └── FilterEmbedding
```

`RSSFeed`, `Article`, `ArticleEmbedding` и публикации Test RSS глобальные. `FeedSubscription` и `Filter` принадлежат пользователю; match принадлежит ему через `Filter`. Новый пользователь создаётся без подписок. На `/feeds` видны «Мои подписки» и «Доступные источники». Добавление уже известного URL создаёт только подписку, без второго `RSSFeed`.

Подписка сразу применяет активные фильтры этого пользователя к уже сохранённым статьям выбранного канала. Отписка удаляет только его `ArticleFilterMatch` для этого канала; общий источник, статьи и векторы остаются. Главная страница показывает только статьи из подписок текущего пользователя и только его совпадения. При отсутствии подписок, фильтров или совпадений UI показывает отдельные подсказки. `/test-rss` предупреждает, если текущий пользователь не подписан на Local Test RSS.

Схему БД менять не потребовалось: `users.name` уже уникален, `feed_subscriptions` имеет уникальную пару `(user_id, feed_id)`, а каскады фильтров и совпадений уже существуют. Поэтому миграции `0005` нет; текущая вершина Alembic — `0004_semantic_filtering`.

## demo

1. Откройте `/users`, создайте `Teacher` и выберите его. Новая учётная запись пока не имеет подписок.
2. На `/feeds` подпишитесь на `Local Test RSS`, затем добавьте внешний RSS URL. Если URL уже есть в списке доступных, просто нажмите «Подписаться».
3. На `/filters` создайте keyword-фильтр `OpenAI` и semantic-фильтр с темой `football transfers` и порогом `0.20`.
4. На `/test-rss` опубликуйте подходящую статью. Откройте `/rss/test.xml`, затем главную страницу: статья появится в ленте с объяснением совпадения.
5. На `/feeds` выполните refresh внешних каналов: новые статьи проходят те же персональные фильтры. Повторный refresh не создаёт дубликатов.
6. Создайте второго пользователя и оставьте без подписки на Local Test RSS. Переключайтесь на `/users`: состав источников, фильтры и filtered feed различаются и сохраняются после переключения.
