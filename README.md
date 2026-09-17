# flipper — личный сканер выгодных объявлений на Kleinanzeigen

Ищет объявления в **Фленсбурге и в радиусе 5 км**, прогоняет их через локальную
LLM (Ollama), считает реальную рыночную цену, ставит вердикт **TAKE / MAYBE / SKIP**
и складывает всё в базу + REST API. Опционально — push на телефон (FCM).

> ⚠️ Проект **личный**. Не для распространения: скрейпинг Kleinanzeigen и перепродажа
> — на свой страх и риск. Соблюдай их правила и лимиты (см. раздел «Анти-бан»).

---

## Содержание

1. [Что делает проект](#1-что-делает-проект)
2. [Как это работает (пайплайн)](#2-как-это-работает-пайплайн)
3. [Требования](#3-требования)
4. [Установка и запуск](#4-установка-и-запуск)
5. [Команды CLI](#5-команды-cli)
6. [REST API](#6-rest-api)
7. [Мониторинг через терминал](#7-мониторинг-через-терминал)
8. [Конфиг `config.yaml`](#8-конфиг-configyaml)
9. [Как добавить новую категорию/нишу](#9-как-добавить-новую-категориюнишу)
10. [Анти-бан: cooldown, бюджет, install-id, lock](#10-анти-бан)
11. [Как считаются вердикты](#11-как-считаются-вердикты)
12. [Push-уведомления (FCM)](#12-push-уведомления-fcm)
13. [systemd-сервисы и автозапуск](#13-systemd-сервисы-и-автозапуск)
14. [Cloudflare-туннель (доступ с телефона)](#14-cloudflare-туннель)
15. [Структура проекта](#15-структура-проекта)
16. [Диагностика проблем](#16-диагностика-проблем)
17. [Дальнейшие шаги](#17-дальнейшие-шаги)

---

## 1. Что делает проект

Раз в ~20 минут (с джиттером) сервис:

1. Пробегает по списку поисков (Flensburg ±5 км, конкретные категории Kleinanzeigen).
2. Новые объявления сохраняет в SQLite и добирает детали.
3. Каждое объявление прогоняет через локальную LLM `qwen3:8b` (Ollama):
   определяет тип устройства, неисправность, стоимость ремонта.
4. Проверяет **реальную** рыночную цену по рабочим аналогам на Kleinanzeigen.
5. Считает маржу и ставит вердикт **TAKE / MAYBE / SKIP**.
6. Показывает всё в CLI и REST API; по `TAKE` (если настроено) шлёт push.

Никаких облаков: LLM крутится локально на этом же ПК.

---

## 2. Как это работает (пайплайн)

```
APScheduler (каждые 20 мин)
        │
        ▼
scan_one()          scraper/search.py   — поиски → дедуп → запись в listings
        │
        ▼
enrich_new()        scraper/detail.py   — добирает описание детальной страницы
        │
        ▼
analyze_pending()   pipeline.py         — берёт N объявлений без анализа:
        │                                  ├─ analyze_listing()  → Ollama (LLM)
        │                                  ├─ market_price_eur() → медиана цен аналогов
        │                                  ├─ recompute_verdict() → TAKE/MAYBE/SKIP
        │                                  └─ запись в analyses
        ▼
maybe_notify()      notify/fcm.py       — push по TAKE (если FCM настроен)
```

Ключевые файлы:

| Файл | Ответственность |
|---|---|
| `app/scraper/client.py` | единая точка доступа к API Kleinanzeigen, бюджет запросов, детект блока |
| `app/scraper/search.py` | проход по поискам, дедуп, клиентские фильтры (стоп-слова) |
| `app/scraper/detail.py` | детальные страницы, пометка удалённых (`gone`) |
| `app/analyzer/ollama.py` | клиент Ollama с ретраями |
| `app/analyzer/analyze.py` | промпт + парсинг ответа LLM в строгую схему |
| `app/pricing/market.py` | реальная рыночная цена (медиана рабочих аналогов) |
| `app/pipeline.py` | пересчёт маржи/вердикта, запись анализа |
| `app/scheduler.py` | расписание, cooldown, single-instance lock |
| `app/api/routes.py` | REST API |
| `cli.py` | команды терминала |

---

## 3. Требования

- Linux (проверено на Omarchy/Arch), Python **3.12+**
- **Ollama** с моделью `qwen3:8b` (`ollama pull qwen3:8b`)
- Docker + docker compose (опционально — есть и systemd-вариант)
- `cloudflared` (опционально — доступ с телефона)

Железо: LLM `qwen3:8b` (~6 ГБ) жрёт заметно RAM/GPU. На CPU один анализ ~1.5–3 мин,
с GPU — быстрее. Это влияет на `analyze_per_cycle` (см. ниже).

---

## 4. Установка и запуск

### 4.1. Локально (venv)

```bash
cd ~/Work/flipper/backend
python -m venv .venv
./.venv/bin/pip install -e '.[test]'
cp ../.env.example ../.env            # и заполни FLIPPER_API_KEY
ollama pull qwen3:8b
```

Запуск сервера:

```bash
cd ~/Work/flipper/backend
./.venv/bin/uvicorn flipper.app.main:app --host 0.0.0.0 --port 8000
```

### 4.2. Docker compose

```bash
cd ~/Work/flipper
docker compose up -d --build
docker compose logs -f
```

> В compose используется `network_mode: host`, чтобы скрейпер выходил с домашнего IP
> и видел Ollama на `localhost:11434`.

### 4.3. systemd (рекомендуется, уже настроено)

Сервисы живут в `~/.config/systemd/user/`:

```bash
systemctl --user start flipper          # запустить
systemctl --user restart flipper        # перезапустить (после правок конфига)
systemctl --user status flipper         # состояние
```

---

## 5. Команды CLI

> ⚠️ **Запускать из папки `backend`** (путь к БД относительный → абсолютный, но команды
> рассчитаны на этот каталог). Иначе создастся пустая БД в другом месте.

```bash
cd ~/Work/flipper/backend

./.venv/bin/flipper status              # сколько объявлений/анализов, разбивка вердиктов
./.venv/bin/flipper recent -n 20        # ★ последние найденные объявления
./.venv/bin/flipper cycles -n 10        # история циклов: новых/анализов/запросов
./.venv/bin/flipper run --once          # один ручной проход (уважает lock и cooldown)
./.venv/bin/flipper analyze <ad_id>     # вручную разобрать конкретное объявление
./.venv/bin/flipper uncooldown          # сбросить часовой cooldown
```

Пример вывода `recent`:

```
20:28 Flensburg   100€  -     -                3502989898  https://www.kleinanzeigen.de/...
20:28 Fruerlund    35€  TAKE  +160€ (533%)     3445547608  https://www.kleinanzeigen.de/...
        │            │      │     │                 │         └ ссылка
        │            │      │     │                 └ ad_id
        │            │      │     └ маржа EUR (%)
        │            │      └ вердикт (TAKE/MAYBE/SKIP, «-» = ещё не разобран)
        │            └ цена объявления
        └ город
```

---

## 6. REST API

Base: `http://localhost:8000`. Авторизация — Bearer-токен из `.env` (`FLIPPER_API_KEY`).

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/health` | жив ли сервис (без авторизации) |
| GET | `/api/v1/listings?verdict=TAKE&min_margin=50&limit=50&offset=0` | лента |
| GET | `/api/v1/listings/{ad_id}` | деталка + анализ |
| POST | `/api/v1/listings/{ad_id}/saved` | отметить «сохранено» |
| POST | `/api/v1/listings/{ad_id}/ignored` | отметить «игнор» |
| POST | `/api/v1/listings/{ad_id}/dismissed` | скрыть |
| POST | `/api/v1/analyze/{ad_id}` | пересчитать анализ |
| GET/POST/DELETE | `/api/v1/searches` | управление поисками в БД |
| POST | `/api/v1/notifications/register` | регистрация FCM-токена |
| GET | `/api/v1/notifications/tokens` | список устройств (отладка) |

Примеры:

```bash
curl localhost:8000/api/v1/health

curl -H "Authorization: Bearer flipper_dev_key_2026" \
  "localhost:8000/api/v1/listings?verdict=TAKE&limit=20"
```

---

## 7. Мониторинг через терминал

```bash
# Сервис
systemctl --user status flipper
journalctl --user -u flipper -f              # живой лог (-f = follow)
journalctl --user -u flipper --since "1 hour"
systemctl --user restart flipper

# Что нашлось
cd ~/Work/flipper/backend
./.venv/bin/flipper recent -n 30
./.venv/bin/flipper status
./.venv/bin/flipper cycles -n 10

# Напрямую из БД (все объявления с городом/ценой)
sqlite3 ~/Work/flipper/backend/flipper.db \
  "SELECT location_city, title, price_cents/100, url FROM listings
   ORDER BY last_seen_at DESC LIMIT 20;"

# Только TAKE
sqlite3 ~/Work/flipper/backend/flipper.db \
  "SELECT l.title, l.price_cents/100, a.margin_eur, l.url
   FROM analyses a JOIN listings l ON l.ad_id=a.listing_id
   WHERE a.verdict='TAKE' ORDER BY a.created_at DESC;"

# URL туннеля для телефона
journalctl --user -u cloudflared | grep trycloudflare | tail -1
```

Полезный алиас (добавь в `~/.bashrc`):

```bash
alias flip='cd ~/Work/flipper/backend && ./.venv/bin/flipper'
# потом: flip recent -n 20
```

---

## 8. Конфиг `config.yaml`

Файл: `backend/config.yaml`.

### scrapyer

| Ключ | Смысл |
|---|---|
| `poll_interval_min` | период циклов в минутах (20 = спокойно для API) |
| `pages_per_search` | страниц на один поиск (для Flensburg+5км хватает `1`) |
| `detail_delay_sec` | случайная пауза между детальными запросами |
| `max_requests_per_cycle` | жёсткий бюджет запросов за цикл |
| `analyze_per_cycle` | сколько объявлений разбирать LLM за цикл |
| `enrich_per_cycle` | сколько деталей добирать за цикл |
| `searches[]` | список поисков (см. ниже) |
| `exclude_keywords` | стоп-слова: искать в заголовке **и** описании |
| `exclude_title_keywords` | стоп-слова: только в заголовке (аксессуары) |

Каждый поиск:

```yaml
- query: "iPhone defekt"       # текст запроса
  max_price_eur: 80            # потолок цены
  radius_km: 5                 # радиус вокруг location
  location: "Flensburg"        # город (резолвится автоматически)
  sort_price: PRICE_ASCENDING  # необязательно: PRICE_ASCENDING/PRICE_DESCENDING (иначе «по дате»)
  category_ids: [173]          # необязательно: ID категории Kleinanzeigen
```

**ID категорий** (из каталога библиотеки):

| ID | Категория |
|---|---|
| 173 | Handy & Telefon |
| 278 | Notebooks |
| 285 | Tablets & Reader |
| 225 | PC-Zubehör & Software |
| 279 | Konsolen |
| 176 | Haushaltsgeräte |
| 245 | Foto |
| 172 | Audio & Hifi |
| 228 | PCs |
| 175 | TV & Video |

### analyzer

| Ключ | Смысл |
|---|---|
| `provider` | `local` (Ollama) |
| `model` | модель, по умолчанию `qwen3:8b` |
| `temperature` | 0.1 — меньше фантазии |
| `timeout_sec` | таймаут одного запроса к Ollama |
| `retries` | повторы при таймауте |
| `min_confidence` | минимальная уверенность модели |
| `ollama_base_url` | `http://localhost:11434` |

### deal

| Ключ | Смысл |
|---|---|
| `min_margin_eur` / `min_margin_pct` | порог TAKE для **ремонта** (40€ / 35%) |
| `min_margin_eur_resale` / `min_margin_pct_resale` | порог TAKE для **перепродажи** (35€ / 35%) |
| `logistics_eur` | издержки доставки при перепродаже |
| `notify_only_verdicts` | по каким вердиктам пушить (`["TAKE"]`) |
| `market_factor` | медиана × 0.88 (поправка на торг/VB) |
| `price_cache_days` | сколько дней держать цены-сэмплы в кэше |
| `min_price_samples` | меньше сэмплов → верить оценке модели (флаг estimate) |

### api / db / notify

```yaml
api:
  api_key: ""     # реально берётся из .env (FLIPPER_API_KEY)
db:
  url: "sqlite:////home/prizrak/Work/flipper/backend/flipper.db"   # абсолютный путь!
notify:
  fcm_service_account: ""   # путь к service-account.json (или FLIPPER_FCM_SERVICE_ACCOUNT)
```

---

## 9. Как добавить новую категорию/нишу

1. Найди ID категории (каталог библиотеки):

   ```bash
   cd ~/Work/flipper/backend
   ./.venv/bin/python -c "from kleinanzeigen_api import categories as c; \
     print([(x.id,x.name) for x in c.find_categories('Kamera', limit=5)])"
   ```

2. Добавь блок в `config.yaml` → `scraper.searches`:

   ```yaml
   - query: "Objektiv"
     max_price_eur: 80
     radius_km: 5
     location: "Flensburg"
     category_ids: [245]
   ```

3. Убедись, что хватает бюджета: `поисков + enrich_per_cycle + analyze_per_cycle ≤ max_requests_per_cycle`.

4. `systemctl --user restart flipper`

> Категории Kleinanzeigen задают «полку» поиска, но аксессуары часто лежат в той же
> категории, что и техника (чехлы в «Handy & Telefon»). Поэтому дополнительно работает
> `exclude_title_keywords` — стоп-слова для аксессуаров.

---

## 10. Анти-бан

Чтобы не получить капчу/бан от Kleinanzeigen:

- **Бюджет запросов** на цикл (`max_requests_per_cycle`). При превышении — `ScraperBlocked`.
- **Cooldown 1 час** при блоке (`app/cooldown.py`, таблица `settings`). Снять вручную:
  `flipper uncooldown`.
- **Стабильный install-id** (`~/.config/flipper/install_id`) — не выглядим как новое
  устройство на каждом цикле.
- **Single-instance lock** (`/tmp/flipper_cycle.lock`) — CLI и таймер не запускают
  два цикла одновременно.
- Случайные паузы между запросами; `distance_km` и разумный `pages_per_search`.

Если в логах `включаю cooldown` — просто пережди час; сервис продолжит сам.

---

## 11. Как считаются вердикты

```
маржа = рыночная_цена − цена_объявления − вложения
  вложения = стоимость запчастей (ремонт)  |  logistics_eur (перепродажа)
```

- Рыночная цена = **медиана цен рабочих аналогов** × `market_factor`.
  Если аналогов < `min_price_samples`, берётся оценка модели (в логе `(estimate)`).
- **TAKE** — маржа ≥ порога (repair: 40€/35%, resale: 35€/35%).
- **MAYBE** — маржа ≥ 15€ (ремонт) / 25€ (перепродажа).
- **SKIP** — иначе, либо сработал «жёсткий флаг» (iCloud-лок, water damage, битая плата).
- `flip_type`: `repair` (чиним) или `resale` (перепродаём как есть).

---

## 12. Push-уведомления (FCM)

Код готов (`app/notify/fcm.py`), но **включается только если задан service-account**.
Push приходит на зарегистрированное устройство (нужно приложение-клиент с FCM).

Порядок настройки:

1. Создай проект в [Firebase Console](https://console.firebase.google.com/).
2. Добавь Android-приложение, скачай `google-services.json` для приложения.
3. Project settings → Service accounts → Generate new private key →
   сохрани как `~/Work/flipper/backend/service-account.json`.
4. Пропиши в `~/Work/flipper/.env`:

   ```env
   FLIPPER_FCM_SERVICE_ACCOUNT=/home/prizrak/Work/flipper/backend/service-account.json
   ```

5. `systemctl --user restart flipper`. В логе: `FCM инициализирован из ...`.
6. Приложение при старте шлёт свой FCM-токен:

   ```bash
   curl -X POST localhost:8000/api/v1/notifications/register \
     -H "Authorization: Bearer flipper_dev_key_2026" \
     -H "Content-Type: application/json" \
     -d '{"fcm_token":"<TOKEN>","platform":"android"}'
   ```

Файл `service-account*.json` уже в `.gitignore` — в репозиторий не попадёт.

> **Альтернатива, проще:** Telegram-бот (просто HTTP-запрос к Bot API, без мобильного
> приложения). Хороший кандидат на следующий шаг.

---

## 13. systemd-сервисы и автозапуск

Сервисы (user-level):

- `flipper.service` — uvicorn на `:8000` + APScheduler
- `cloudflared.service` — туннель к `:8000`
- `ollama.service` — LLM (системный)

Автозапуск:

```bash
systemctl --user enable flipper cloudflared
loginctl enable-linger prizrak     # старт user-сервисов при загрузке, без логина
```

Проверка:

```bash
loginctl show-user prizrak | grep Linger
```

---

## 14. Cloudflare-туннель

`cloudflared` поднимает **quick tunnel** (временный URL) к `localhost:8000`, чтобы
открывать API с телефона.

```bash
journalctl --user -u cloudflared | grep trycloudflare | tail -1
# https://<random>.trycloudflare.com
```

⚠️ URL **меняется при каждом рестарте** `cloudflared`. Для постоянного адреса нужен
именованный туннель с доменом (на будущее).

---

## 15. Структура проекта

```
flipper/
├── backend/
│   ├── flipper/
│   │   ├── app/
│   │   │   ├── analyzer/     # LLM: base, ollama, analyze, prompt, schema
│   │   │   ├── api/routes.py # REST API
│   │   │   ├── notify/fcm.py # push
│   │   │   ├── pricing/      # market.py, parts.py
│   │   │   ├── scraper/      # client.py, search.py, detail.py
│   │   │   ├── main.py       # uvicorn + lifespan
│   │   │   ├── scheduler.py  # расписание, lock, cooldown
│   │   │   ├── pipeline.py   # пайплайн анализа
│   │   │   ├── cooldown.py
│   │   │   ├── db.py
│   │   │   └── models.py
│   │   ├── cli.py
│   │   └── config.py
│   ├── tests/                # pytest
│   ├── config.yaml           # ← основные настройки
│   ├── pyproject.toml
│   └── Dockerfile
├── docker-compose.yml
├── .env / .env.example
└── README.md
```

---

## 16. Диагностика проблем

| Симптом | Причина / решение |
|---|---|
| `flipper status` показывает 0, а в БД есть | запускаешь не из `backend/`. Путь к БД в `config.yaml` — абсолютный, команды — из `backend/` |
| В логе `включаю cooldown` | сработал анти-бан. Переждать час или `flipper uncooldown` |
| `ollama попытка N/3 упала: timed out` | модель не успела. Увеличь `analyzer.timeout_sec`, оставь ретраи. Первый запрос после простоя всегда медленный (загрузка модели) |
| `рыночный поиск ... упал: GET failed` | временный сбой API; вердикт считается по оценке модели (флаг estimate). Не критично |
| Поиск `упал: GET failed` | невалидный `sort_price` или сеть. Валидные: `PRICE_ASCENDING`, `PRICE_DESCENDING`, `DATE_DESCENDING`, `DISTANCE_ASCENDING` |
| Пусто в `recent` | сервис ещё не прошёл цикл — подожди до 20 мин или `flipper run --once` |
| `FCM не настроен — пуши отключены` | нет service-account (см. §12). Не мешает работе |
| Много аксессуаров в ленте | добавь слова в `exclude_title_keywords` |

Тесты:

```bash
cd ~/Work/flipper/backend
./.venv/bin/pytest -q
```

---

## 17. Дальнейшие шаги

- [ ] **Telegram-бот** как простой канал уведомлений (вместо/вместе с FCM).
- [ ] Тюнинг ниш по факту улова (смотреть, что реально даёт TAKE).
- [ ] Постоянный cloudflare-туннель (именованный) или локальный доступ.
- [ ] Приложение/веб-лента для удобного просмотра карточек.
- [ ] Больше категорий (Спорт, Инструменты, Wearables).
- [ ] Экспорт статистики: сколько TAKE/MAYBE, средняя маржа, конверсия.
