# flipper

Личный сканер выгодных объявлений. Раз в 15 минут обходит поиски на Kleinanzeigen
(Фленсбург и окрестности) и Vinted (Германия, с доставкой), новые лоты прогоняет
через LLM (Gemini 2.5 Flash с фото, фолбэк — локальная Ollama), считает рыночную
цену по рабочим аналогам и ставит вердикт TAKE / MAYBE / SKIP. Всё складывается
в SQLite и отдаётся через REST API в веб-приложение (PWA и Android-APK).
По TAKE приходят уведомления: Telegram, нативный push (FCM) или Web Push.

Проект написан вместе с ИИ: код и документация создавались в диалоге с LLM,
потом доводились руками и проверялись на реальном сервере. Это личный инструмент,
не продукт, и скрейпинг площадок идёт против их правил — подробнее в «Ограничениях».

## Как это работает

Цикл (APScheduler, каждые 15 минут):

1. `scraper/search.py` — ротация поисков по обеим площадкам, дедуп, запись
   новых лотов в `listings`.
2. `scraper/detail.py` — дозагрузка описаний с детальных страниц, пометка удалённых.
3. `pipeline.py` — лоты без анализа идут в разбор:
   - `analyzer/` — LLM определяет тип устройства, дефект, стоимость ремонта.
     Аксессуары (чехлы, кабели) отсекаются детерминированным фильтром до LLM —
     сразу SKIP. Если у провайдера включён vision, в запрос уходят фото лота.
   - `pricing/market.py` — рыночная цена: медиана цен рабочих аналогов на той же
     площадке. Аналогов мало — берётся оценка модели (помечается `market_estimated`).
   - `recompute_verdict()` — маржа и вердикт. Правило ликвидности: у ходовых ниш
     (GPU/CPU/ноутбуки/консоли/Apple) порог TAKE ниже, у неликвида — выше.
4. `notify/` — рассылка по настроенным каналам (гейт: вердикт в списке, маржа
   выше порога, лот жив и не скрыт пользователем).

## Запуск

### Docker compose (боевой вариант)

```bash
cd flipper
docker compose up -d --build
docker compose logs -f
```

`network_mode: host`: скрейпер ходит с домашнего IP, Ollama доступна на
`localhost:11434`. Бэкенд слушает 8000 напрямую на хосте.

### Локально (venv, для разработки)

```bash
cd backend
python -m venv .venv
./.venv/bin/pip install -e '.[test]'
cp ../.env.example ../.env
./.venv/bin/uvicorn flipper.app.main:app --host 0.0.0.0 --port 8000
```

Нужно: Python 3.12+, ключ LLM-провайдера (бесплатного тарифа Gemini хватает),
опционально Ollama с `qwen3:8b` как фолбэк, Node для сборки фронта.

## Конфиг

Основное — в `backend/config.yaml`:

- `scraper` — интервал циклов, поиски Kleinanzeigen (query, max_price_eur,
  radius_km, location, category_ids), бюджеты запросов за цикл и за час,
  режим работы/отдыха (2.5 ч / 30 мин), стоп-слова.
- `vinted` — отдельный список поисков и бюджет; поиск по всей Германии,
  поэтому шум отсекается ценой, стоп-словами и категориями.
- `analyzer` — провайдер (по умолчанию `gemini`; также `groq`, `openai`,
  `openrouter`, `cerebras`, `together`, `local`), модель, `vision` (фото),
  `max_tokens`, `fallback_local` (при лимите облака лот просто ждёт следующего
  цикла, на локальную модель не падаем).
- `deal` — пороги маржи TAKE/MAYBE для ремонта и перепродажи, `market_factor`,
  правило ликвидности.
- `api`, `db`, `notify` — ключ API, путь к БД, каналы уведомлений.

Секреты — в `.env` (`FLIPPER_API_KEY`, `GEMINI_API_KEY`,
`FLIPPER_TELEGRAM_BOT_TOKEN` и т.д.), в git они не попадают. Путь к БД
в `config.yaml` абсолютный и машинозависимый: в Docker это
`sqlite:////app/flipper.db`, на своей машине правь под себя.

Поиски, стоп-слова и переключатель Vinted правятся без перезапуска — со страницы
настроек в приложении или через API (`/api/v1/searches`, `/api/v1/settings`).

## CLI

Запускать из папки `backend`:

```bash
./.venv/bin/flipper status            # счётчики, разбивка вердиктов
./.venv/bin/flipper recent -n 20      # последние лоты
./.venv/bin/flipper cycles -n 10      # история циклов
./.venv/bin/flipper run --once        # ручной проход (уважает lock и cooldown)
./.venv/bin/flipper analyze <ad_id>   # разобрать конкретный лот
./.venv/bin/flipper reanalyze -n 10   # пере-разбор старых SKIP
./.venv/bin/flipper uncooldown        # сбросить cooldown
./.venv/bin/flipper test-notify       # проверить Telegram
./.venv/bin/flipper test-heartbeat    # принудительная пульс-сводка
```

## REST API

Авторизация: `Authorization: Bearer <FLIPPER_API_KEY>`.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/health` | жив ли сервис (без ключа) |
| GET | `/api/v1/listings` | лента (фильтры verdict, min_margin, saved, created_after_hours) |
| GET | `/api/v1/listings/{ad_id}` | лот + анализ + сделка |
| POST | `/api/v1/listings/{ad_id}/saved` / `ignored` / `dismissed` | отметки |
| POST | `/api/v1/analyze/{ad_id}` | переанализ лота |
| GET/POST | `/api/v1/flips`, `/api/v1/flips/summary` | портфель: сделки и сводка |
| PUT/DELETE | `/api/v1/flips/{flip_id}` | правка/удаление сделки |
| GET/POST | `/api/v1/searches`, PUT/DELETE `/api/v1/searches/{id}` | поиски в БД |
| GET/PUT | `/api/v1/settings` | настройки (стоп-слова, vinted) |
| POST | `/api/v1/notifications/register`, `/test` | FCM-токен, тестовый push |
| GET/POST/DELETE | `/api/v1/push/*` | Web Push (VAPID) |
| GET | `/api/v1/stats`, `/api/v1/export.csv` | статистика, выгрузка |

Фронт раздаётся тем же сервером по `/`.

## Уведомления

- Telegram — бот на long-polling. Команды: `/recent`, `/deal <id>`, `/status`,
  `/stats`, `/pulse`, `/help`. Раз в `notify.heartbeat_interval_min` минут шлёт
  сводку. Если у уведомлённого лота цена упала на `notify.price_drop_pct`%,
  приходит повторный алерт.
- Web Push (PWA) — работает только по HTTPS (cloudflared-туннель или
  `tailscale serve`).
- FCM (Android-APK) — нужен service-account Firebase: ключ из Firebase Console
  кладётся в `flipper-firebase-adminsdk.json` (в .gitignore), путь — в `.env`
  как `FLIPPER_FCM_SERVICE_ACCOUNT`.

## Android-приложение

`frontend/` — React (Vite) PWA, он же исходник APK на Capacitor 8. Сборка:

```bash
cd frontend
export JAVA_HOME=$HOME/.local/share/mise/installs/java/21   # Capacitor 8 требует JDK 21
export ANDROID_HOME=$HOME/Android
npm run apk     # build -> cap sync -> assembleDebug -> dist/flipper.apk
```

Готовый APK сервер раздаёт по `/flipper.apk`. Пуш в приложении нативный (FCM):
при старте оно шлёт токен на `POST /api/v1/notifications/register`.

## Анти-бан

- Бюджет запросов за цикл и скользящий лимит за час (общий для CLI и сервиса).
- Ротация ниш: за цикл берётся часть поисков, порядок перемешивается.
- Режим работы/отдыха 2.5 ч / 30 мин — аккаунт «остывает».
- Cooldown на час при блоке (`flipper uncooldown` — снять вручную).
- Стабильный install-id, single-instance lock, паузы между запросами.

## Диагностика

| Симптом | Причина / решение |
|---|---|
| `status` показывает 0, а в БД данные есть | запуск не из `backend/`, путь к БД абсолютный |
| в логе «включаю cooldown» | сработал анти-бан, переждать или `uncooldown` |
| `LLM 429 (лимит), ждём Ns` | бесплатный тариф провайдера; лоты перейдут на следующий цикл, можно снизить `analyze_per_cycle` |
| `рыночный поиск упал` | временный сбой API, вердикт считается по оценке модели |
| пусто в `recent` | цикл ещё не прошёл: подождать или `run --once` |

Тесты:

```bash
cd backend && ./.venv/bin/pytest -q
```

## Структура

```
flipper/
├── backend/
│   ├── flipper/
│   │   ├── app/
│   │   │   ├── analyzer/     # LLM: провайдеры, промпт, парсинг в схему
│   │   │   ├── api/routes.py # REST API + раздача фронта
│   │   │   ├── notify/       # telegram, fcm, web push, heartbeat, price-drop
│   │   │   ├── pricing/      # рыночная цена по аналогам
│   │   │   ├── scraper/      # Kleinanzeigen и Vinted
│   │   │   ├── pipeline.py   # маржа, вердикт, ликвидность
│   │   │   ├── scheduler.py  # расписание, lock, cooldown
│   │   │   └── models.py, db.py
│   │   ├── cli.py
│   │   └── config.py
│   ├── tests/
│   ├── config.yaml
│   └── Dockerfile
├── frontend/                 # React PWA + Capacitor Android
├── docker-compose.yml
└── .env.example
```

## Ограничения

- Скрейпинг Kleinanzeigen и Vinted против их правил использования. Проект
  личный, ответственность за использование — на владельце; лимиты и анти-бан
  выше настроены, чтобы не мешать площадкам сверх разумного.
- Код и тексты писались с помощью ИИ: структура и комментарии местами
  избыточны, но всё это протестировано и работает на реальном сервере.
- Для распространения проект не предназначен: зашиты личные ниши, город
  и настройки.
