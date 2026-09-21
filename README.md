# flipper — личный сканер выгодных объявлений на Kleinanzeigen

Ищет объявления в **Фленсбурге и в радиусе 5 км**, прогоняет их через LLM
(по умолчанию бесплатный облачный API Groq, `qwen/qwen3.8-27b`; офлайн-фолбэк —
локальная Ollama `qwen3:8b`), считает реальную рыночную цену, ставит вердикт
**TAKE / MAYBE / SKIP** и складывает всё в базу + REST API. Опционально —
уведомления в Telegram или push (FCM).

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
12. [Уведомления: FCM и Telegram](#12-уведомления-fcm-и-telegram)
13. [systemd-сервисы и автозапуск](#13-systemd-сервисы-и-автозапуск)
14. [Cloudflare-туннель (доступ с телефона)](#14-cloudflare-туннель)
15. [Структура проекта](#15-структура-проекта)
16. [Диагностика проблем](#16-диагностика-проблем)
17. [Дальнейшие шаги](#17-дальнейшие-шаги)

---

## 1. Что делает проект

Раз в 15 минут (с джиттером), в режиме **2.5 ч работы / 30 мин отдыха**, сервис:

1. Пробегает по списку поисков (Flensburg ±5 км, конкретные категории Kleinanzeigen).
2. Новые объявления сохраняет в SQLite и добирает детали.
3. Каждое объявление прогоняет через LLM (облачный Groq `qwen/qwen3.8-27b`,
   либо локальная `qwen3:8b`, если ключа нет или API недоступен):
   определяет тип устройства, неисправность, стоимость ремонта.
4. Проверяет **реальную** рыночную цену по рабочим аналогам на Kleinanzeigen.
5. Считает маржу и ставит вердикт **TAKE / MAYBE / SKIP**.
6. Показывает всё в CLI и REST API; по `TAKE` (если настроено) шлёт push.

Никаких облаков: LLM крутится локально на этом же ПК.

---

## 2. Как это работает (пайплайн)

```
APScheduler (каждые 15 мин, 2.5 ч/30 мин)
        │
        ▼
scan_one()          scraper/search.py   — ротация ниш → дедуп → запись в listings
        │
        ▼
enrich_new()        scraper/detail.py   — добирает описание детальной страницы
        │
        ▼
analyze_pending()   pipeline.py         — берёт N объявлений без анализа:
        │                                  ├─ analyze_listing()  → LLM (Groq/Ollama)
        │                                  ├─ market_price_eur() → медиана цен аналогов
        │                                  ├─ recompute_verdict() → TAKE/MAYBE/SKIP
        │                                  └─ запись в analyses
        ▼
maybe_notify()      notify/fcm.py       — push по TAKE (если FCM настроен)
notify_telegram()   notify/telegram.py  — сообщение в Telegram (если настроен)
```

Ключевые файлы:

| Файл | Ответственность |
|---|---|
| `app/scraper/client.py` | единая точка доступа к API Kleinanzeigen, бюджет запросов, детект блока |
| `app/scraper/search.py` | проход по поискам, дедуп, клиентские фильтры (стоп-слова) |
| `app/scraper/detail.py` | детальные страницы, пометка удалённых (`gone`) |
| `app/analyzer/ollama.py` | клиент Ollama с ретраями (локальный провайдер) |
| `app/analyzer/openai_compat.py` | клиент облачного OpenAI-совместимого API (Groq и др.), бэкофф на 429 |
| `app/analyzer/factory.py` | выбор провайдера + фолбэк на локальную модель |
| `app/analyzer/analyze.py` | промпт + парсинг ответа LLM в строгую схему |
| `app/pricing/market.py` | реальная рыночная цена (медиана рабочих аналогов) |
| `app/pipeline.py` | пересчёт маржи/вердикта, запись анализа |
| `app/scheduler.py` | расписание, cooldown, single-instance lock |
| `app/api/routes.py` | REST API |
| `cli.py` | команды терминала |

---

## 3. Требования

- Linux (проверено на Omarchy/Arch), Python **3.12+**
- Ключ бесплатного облачного LLM **Groq** (`GROQ_API_KEY`, без карты:
  https://console.groq.com → API Keys) — основной вариант
- **Ollama** с моделью `qwen3:8b` (`ollama pull qwen3:8b`) — офлайн-фолбэк,
  если ключа нет или облако вернуло ошибку/лимит
- Docker + docker compose (опционально — есть и systemd-вариант)
- `cloudflared` (опционально — доступ с телефона)

Железо: облачный LLM не грузит машину вообще. Локальный фолбэк `qwen3:8b`
(~6 ГБ) жрёт заметно RAM/GPU: на CPU один анализ ~1.5–3 мин, с GPU быстрее.
Это влияет на `analyze_per_cycle` (см. ниже).

> Бесплатный тариф Groq: 1000 запросов/день и **8000 токенов/мин** на модель —
> при всплеске анализ получает `429`, клиент сам ждёт и повторяет (fallback на
> локальную модель только если ретраи исчерпаны).

---

## 4. Установка и запуск

### 4.1. Локально (venv)

```bash
cd ~/Work/flipper/backend
python -m venv .venv
./.venv/bin/pip install -e '.[test]'
cp ../.env.example ../.env            # заполни FLIPPER_API_KEY и GROQ_API_KEY
ollama pull qwen3:8b                  # только для офлайн-фолбэка
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
./.venv/bin/flipper test-notify         # проверить Telegram-уведомления (§12.1)
./.venv/bin/flipper test-heartbeat      # принудительный «пульс»-сводку (сейчас же)
./.venv/bin/flipper reanalyze -n 10     # пере-разбор старых SKIP новой моделью (§12.3)
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

### scraper

| Ключ | Смысл |
|---|---|
| `poll_interval_min` | период циклов в минутах (15) |
| `pages_per_search` | страниц на один поиск (для Flensburg+5км хватает `1`) |
| `detail_delay_sec` | случайная пауза перед каждым запросом |
| `max_requests_per_cycle` | жёсткий бюджет запросов за цикл |
| `max_requests_per_hour` | жёсткий часовой потолок запросов (общий для CLI и сервиса) |
| `searches_per_cycle` | сколько ниш обходить за цикл (ротация; 8 из 16) |
| `active_minutes` / `rest_minutes` | режим работы: 2.5 ч активны, 30 мин полный покой |
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
| `provider` | `groq` (облако) / `local` (Ollama) / `openrouter` / `cerebras` / `together` |
| `model` | модель (`qwen/qwen3.8-27b` для Groq, `qwen3:8b` для local) |
| `base_url` | OpenAI-совместимый endpoint (для Groq заполнен) |
| `api_key` | ключ (лучше не тут, а в `.env`: `GROQ_API_KEY`) |
| `api_key_env` | имя env-переменной с ключом |
| `fallback_local` | при ошибке/лимите API считать локальной моделью |
| `local_model` | какая Ollama-модель для фолбэка |
| `temperature` | 0.1 — меньше фантазии |
| `timeout_sec` | таймаут одного запроса к LLM |
| `retries` | повторы при таймауте/429 |
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
  telegram_bot_token: ""    # токен @BotFather (или FLIPPER_TELEGRAM_BOT_TOKEN)
  telegram_chat_id: ""      # твой chat_id (или FLIPPER_TELEGRAM_CHAT_ID)
  heartbeat_interval_min: 180  # «пульс»-сводка каждые N минут (0 = выключено)
  price_drop_pct: 15           # падение цены на уведомлённом лоте -> повторный алерт
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

3. Убедись, что хватает бюджета: `searches_per_cycle + enrich_per_cycle + analyze_per_cycle ≤ max_requests_per_cycle`, и что часовой лимит не выедается на новых циклах.

4. `systemctl --user restart flipper`

> Категории Kleinanzeigen задают «полку» поиска, но аксессуары часто лежат в той же
> категории, что и техника (чехлы в «Handy & Telefon»). Поэтому дополнительно работает
> `exclude_title_keywords` — стоп-слова для аксессуаров.

---

## 10. Анти-бан

Чтобы не получить капчу/бан от Kleinanzeigen:

- **Бюджет запросов** на цикл (`max_requests_per_cycle`). При превышении — `ScraperBlocked`.
- **Часовой потолок** (`max_requests_per_hour`, файл `~/.config/flipper/rate_limit.json`
  под `flock`): общий для CLI и сервиса, скользящее окно 1 час. Превышение → cooldown.
- **Ротация ниш и случайный порядок**: за цикл берём `searches_per_cycle` ниш из общего
  списка со сдвигом по времени, порядок каждый раз перемешивается — нет монотонного
  паттерна.
- **Режим работы/отдыха**: `active_minutes` (2.5 ч) активны, `rest_minutes` (30 мин) —
  цикл полностью пропускается (запись `rest`), аккаунт «остывает».
- **Cooldown 1 час** при блоке (`app/cooldown.py`, таблица `settings`). Снять вручную:
  `flipper uncooldown`.
- **Счётчик сбоев**: серия неудачных запросов (`FAILURES_BEFORE_COOLDOWN`) или три
  пустых ответа подряд → `ScraperBlocked` → cooldown.
- **Стабильный install-id** (`~/.config/flipper/install_id`) — не выглядим как новое
  устройство на каждом цикле.
- **Single-instance lock** (`/tmp/flipper_cycle.lock`) — CLI и таймер не запускают
  два цикла одновременно.
- **Человекоподобные паузы** перед каждым запросом; `distance_km` и разумный
  `pages_per_search`.

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

## 12. Уведомления: FCM, Telegram и Web Push

Каналы используют общий гейт `app/notify/gate.py` (`should_notify`): вердикт в
`notify_only_verdicts`, маржа выше профильного порога, объявление живо и не отмёчено
пользователем (`dismissed`/`ignored`). Не настроенный канал тихо ничего не шлёт.

### 12.0. Web Push (PWA — приложение в браузере/на телефоне)

Веб-приложение (`frontend/`) раздаётся тем же сервером (см. §18). Уведомления Web Push
(VAPID) приходят на телефон/браузер, где открыто приложение и разрешены уведомления:

- Верхний переключатель в приложении — включить push; рядом «и MAYBE» — получать и
  MAYBE-лоты (по умолчанию только TAKE).
- Сервер: `app/notify/push.py` — ключи VAPID сохраняются в `~/.flipper/vapid.json`,
  подписки в таблице `push_subscriptions`. TAKE уходит всем подпискам, MAYBE — только
  подписчикам с `send_maybe`.
- Снижение цены у уведомлённого лота тоже дублируется в push.
- **Требование:** Web Push работает только по HTTPS. Быстрый cloudflared-туннель
  даёт HTTPS (открой приложение по его адресу). Именованный туннель со стабильным
  адресом — когда заведёшь Cloudflare-аккаунт (см. §14): quick-URL меняется при каждом
  рестарте cloudflared, и телефон периодически теряет связь.

### 12.1. Telegram (рекомендуется — мобильное приложение не нужно)

1. В Telegram напиши [@BotFather](https://t.me/BotFather) → `/newbot` → получи **токен**.
2. Узнай свой **chat_id**: напиши что-нибудь боту, затем открой
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates` и возьми `chat.id`.
   (Или напиши [@userinfobot](https://t.me/userinfobot) — он сразу покажет id.)
3. Пропиши в `~/Work/flipper/.env`:

   ```env
   FLIPPER_TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   FLIPPER_TELEGRAM_CHAT_ID=123456789
   ```

4. Проверь канал:

   ```bash
   cd ~/Work/flipper/backend && ./.venv/bin/flipper test-notify
   ```

   Должно прийти «✅ flipper: тестовое уведомление — связь есть».
5. `systemctl --user restart flipper`. Дальше по каждому новому `TAKE` будет приходить
   карточка (HTML): вердикт/маржа, модель, цена-рынок, город и **кнопка «Открыть объявление»**.

Свой формат карточки — `format_deal()` и `format_deal_reply_markup()` в `app/notify/telegram.py`.

#### Команды бота (в чате с ним)

Сервис крутит long-polling (`app/notify/bot.py`) и отвечает на команды:

| Команда | Что делает |
|---|---|
| `/recent [n]` | последние лоты: вердикт, маржа, цена, город и `id` (до 20) |
| `/deal <id>` | полный вердикт по id: модель, маржа, рынок/вложения, плюсы/минусы, действие |
| `/status` | сводка по базе: активных, анализов, вердикты |
| `/stats` | статистика за 24 ч: новых, проанализировано, TAKE |
| `/pulse` | принудительный «пульс»-сводку прямо сейчас |
| `/help` | список команд |

Незнакомые команды игнорируются.

### 12.2. «Пульс» бота (heartbeat, периодический статус)

Чтобы бот не молчал часами, раз в `notify.heartbeat_interval_min` (по умолчанию 180)
шлёт в чат краткую сводку: новых объявлений, проанализировано, TAKE за период,
активных в базе. Работает через `app/notify/heartbeat.py` (флаг `heartbeat_at`
в таблице `settings` — чтобы не спамить). Проверка вручную:

```bash
cd ~/Work/flipper/backend && ./.venv/bin/flipper test-heartbeat
```

### 12.3. Пере-разбор старых SKIP

Старые SKIP могли быть оценены слабой моделью. `flipper reanalyze -n 10` берёт SKIP
старше суток с наибольшей маржой (ближе всех к TAKE), удаляет их анализ — ближайшие
циклы разберут их заново уже новой моделью; если лот «перевернётся» в TAKE, придёт
уведомление.

### 12.4. Слежение за ценой (price-drop)

Лот уведомляли (TAKE/MAYBE), а продавец снизил цену — это «второй шанс».
Каждый цикл (`app/notify/pricedrop.py`) сверяет текущую цену живых лотов с ценой
на момент уведомления (колонка `last_notified_price_cents`). Упала на
`notify.price_drop_pct`% (по умолчанию 15) — уходит новая карточка
«🔻 Цена упала: 50€ → 40€ (−20%)» с пересчитанной маржой. LLM не дёргаем
(маржа считается локально из сохранённого анализа) — облачный бюджет не тратится.

Цена в БД обновляется при каждом повторном показе лота в поиске (`scan_one`),
поэтому алерты работают даже на давно живых объявлениях.

### 12.5. FCM (сложнее, нужен приложение-клиент)

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

> **Проще:** используй Telegram (§12.1) — без мобильного приложения и Firebase.

---

## 13. systemd-сервисы и автозапуск

Сервисы (user-level):

- `flipper.service` — uvicorn на `:8000` + APScheduler
- `cloudflared.service` — туннель к `:8000`
- `ollama.service` — LLM (системный, нужен только для офлайн-фолбэка)

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
│   │   │   ├── analyzer/     # LLM: base, ollama, openai_compat, factory, prompt, schema
│   │   │   ├── api/routes.py # REST API + раздача web-приложения
│   │   │   ├── notify/       # telegram.py, heartbeat.py, fcm.py, gate.py, push.py
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
├── frontend/                 # React PWA (Vite) — npm run build → backend раздаёт dist
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
| Пусто в `recent` | сервис ещё не прошёл цикл — подожди до 15 мин или `flipper run --once` |
| В логе `LLM 429 (лимит), ждём Ns` | бесплатный тариф Groq (8000 токенов/мин). Клиент сам ждёт; если долго — уменьши `analyze_per_cycle` |
| `фолбэк на локальную модель` | API-ключ не задан/ошибка/лимит — считает Ollama. Проверь `GROQ_API_KEY` |
| `окно отдыха — цикл пропущен` | штатный режим 2.5 ч/30 мин, ничего делать не надо |
| `FCM не настроен — пуши отключены` | нет service-account (см. §12). Не мешает работе |
| Много аксессуаров в ленте | добавь слова в `exclude_title_keywords` |

Тесты:

```bash
cd ~/Work/flipper/backend
./.venv/bin/pytest -q
```

---

## 17. Дальнейшие шаги

- [x] **Telegram-бот** как простой канал уведомлений (вместо/вместе с FCM) — см. §12.1.
- [x] **Облачный LLM** (Groq, free) вместо локальной Ollama — быстрее и умнее; офлайн-фолбэк остался.
- [x] **Режим работы/отдыха** 2.5 ч / 30 мин + часовой лимит запросов + ротация ниш.
- [x] **Радиус 15 км и 30 ниш** (catch-all «defekt» по категориям + бренды) — больше лотов.
- [x] **«Пульс» бота** (§12.2) + пере-разбор старых SKIP (§12.3) + карточки с кнопкой (§12.1).
- [x] **Слежение за ценой** (§12.4): цена упала на уведомлённом лоте → повторный алерт.
- [x] **cloudflared Restart=always** + таймер рестарта раз в 3 ч (маскирует edge-ошибки быстрого туннеля).
- [ ] Тюнинг ниш по факту улова (смотреть, что реально даёт TAKE).
- [ ] Постоянный cloudflare-туннель (именованный) или локальный доступ.
- [ ] Приложение/веб-лента для удобного просмотра карточек.
- [ ] Больше категорий (Спорт, Инструменты, Wearables).
- [ ] Экспорт статистики: сколько TAKE/MAYBE, средняя маржа, конверсия.
