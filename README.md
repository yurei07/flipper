# flipper

A personal deal scanner. Every 15 minutes it walks a set of searches on
Kleinanzeigen (Flensburg area) and Vinted (Germany, shipped), runs new listings
through an LLM (Gemini 2.5 Flash with vision, local Ollama as fallback), prices
them against working analogues, and assigns a verdict of TAKE / MAYBE / SKIP.
Everything lands in SQLite and is served over a REST API to a web app
(PWA and Android APK). TAKE listings trigger notifications: Telegram, native
push (FCM), or Web Push.

The project was built together with AI: code and docs were produced in a
dialogue with an LLM, then finished by hand and verified on a real server.
It is a personal tool, not a product, and scraping these marketplaces goes
against their terms — see "Limitations".

## How it works

One cycle (APScheduler, every 15 minutes):

1. `scraper/search.py` — rotates searches across both marketplaces, dedupes,
   writes new listings into `listings`.
2. `scraper/detail.py` — pulls descriptions from detail pages, marks removed
   listings.
3. `pipeline.py` — takes unanalysed listings:
   - `analyzer/` — the LLM determines device type, fault, and repair cost.
     Accessories (cases, cables) are filtered out deterministically before
     the LLM — immediate SKIP. If the provider has vision enabled, listing
     photos are included in the request.
   - `pricing/market.py` — market price as the median of working analogues on
     the same marketplace; if there are too few, the model's estimate is used
     (flagged as `market_estimated`).
   - `recompute_verdict()` — margin and verdict. Liquidity rule: hot niches
     (GPU/CPU/laptops/consoles/Apple) get a lower TAKE threshold, illiquid
     items a higher one.
4. `notify/` — sends notifications through the configured channels (gate:
   verdict in list, margin above threshold, listing alive and not hidden).

## Running

### Docker compose (production)

```bash
cd flipper
docker compose up -d --build
docker compose logs -f
```

`network_mode: host`: the scraper goes out from the home IP, Ollama is
reachable at `localhost:11434`. The backend listens on port 8000 directly.

### Local venv (development)

```bash
cd backend
python -m venv .venv
./.venv/bin/pip install -e '.[test]'
cp ../.env.example ../.env
./.venv/bin/uvicorn flipper.app.main:app --host 0.0.0.0 --port 8000
```

Requires Python 3.12+, an LLM provider key (Gemini free tier is enough),
optionally Ollama with `qwen3:8b` as fallback, Node for frontend builds.

## Config

Main settings live in `backend/config.yaml`:

- `scraper` — cycle interval, Kleinanzeigen searches (query, max_price_eur,
  radius_km, location, category_ids), per-cycle and per-hour request budgets,
  work/rest mode (2.5 h / 30 min), stop words.
- `vinted` — separate search list and budget; search spans all of Germany,
  so noise is cut by price, stop words, and categories.
- `analyzer` — provider (`gemini` by default; also `groq`, `openai`,
  `openrouter`, `cerebras`, `together`, `local`), model, `vision` (photos),
  `max_tokens`, `fallback_local` (on cloud rate limits the listing just waits
  for the next cycle — no fallback to the local model).
- `deal` — TAKE/MAYBE margin thresholds for repair and resale, `market_factor`,
  liquidity rule.
- `api`, `db`, `notify` — API key, DB path, notification channels.

Secrets go in `.env` (`FLIPPER_API_KEY`, `GEMINI_API_KEY`,
`FLIPPER_TELEGRAM_BOT_TOKEN`, etc.) and never reach git. The DB path in
`config.yaml` is absolute and machine-specific: in Docker it is
`sqlite:////app/flipper.db`; adjust it for your machine.

Searches, stop words, and the Vinted toggle are editable without a restart —
from the app settings page or via the API (`/api/v1/searches`,
`/api/v1/settings`).

## CLI

Run from the `backend` directory:

```bash
./.venv/bin/flipper status            # counters, verdict breakdown
./.venv/bin/flipper recent -n 20      # latest listings
./.venv/bin/flipper cycles -n 10      # cycle history
./.venv/bin/flipper run --once        # manual pass (respects lock and cooldown)
./.venv/bin/flipper analyze <ad_id>   # analyse a specific listing
./.venv/bin/flipper reanalyze -n 10   # re-analyse old SKIPs
./.venv/bin/flipper uncooldown        # reset cooldown
./.venv/bin/flipper test-notify       # test Telegram
./.venv/bin/flipper test-heartbeat    # force a heartbeat summary
```

## REST API

Auth: `Authorization: Bearer <FLIPPER_API_KEY>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | service alive (no key needed) |
| GET | `/api/v1/listings` | feed (filters: verdict, min_margin, saved, created_after_hours) |
| GET | `/api/v1/listings/{ad_id}` | listing + analysis + flip |
| POST | `/api/v1/listings/{ad_id}/saved` / `ignored` / `dismissed` | user actions |
| POST | `/api/v1/analyze/{ad_id}` | re-analyse a listing |
| GET/POST | `/api/v1/flips`, `/api/v1/flips/summary` | portfolio: deals and summary |
| PUT/DELETE | `/api/v1/flips/{flip_id}` | edit/delete a deal |
| GET/POST | `/api/v1/searches`, PUT/DELETE `/api/v1/searches/{id}` | searches in DB |
| GET/PUT | `/api/v1/settings` | settings (stop words, vinted) |
| POST | `/api/v1/notifications/register`, `/test` | FCM token, test push |
| GET/POST/DELETE | `/api/v1/push/*` | Web Push (VAPID) |
| GET | `/api/v1/stats`, `/api/v1/export.csv` | stats, export |

The frontend is served by the same server at `/`.

## Notifications

- Telegram — long-polling bot. Commands: `/recent`, `/deal <id>`, `/status`,
  `/stats`, `/pulse`, `/help`. Sends a summary every
  `notify.heartbeat_interval_min` minutes. If a notified listing's price drops
  by `notify.price_drop_pct`%, a follow-up alert is sent.
- Web Push (PWA) — HTTPS only (cloudflared tunnel or `tailscale serve`).
- FCM (Android APK) — requires a Firebase service account: the key from the
  Firebase Console goes to `flipper-firebase-adminsdk.json` (gitignored),
  the path to `.env` as `FLIPPER_FCM_SERVICE_ACCOUNT`.

## Android app

`frontend/` — a React (Vite) PWA and the source of the Capacitor 8 APK. Build:

```bash
cd frontend
export JAVA_HOME=$HOME/.local/share/mise/installs/java/21   # Capacitor 8 needs JDK 21
export ANDROID_HOME=$HOME/Android
npm run apk     # build -> cap sync -> assembleDebug -> dist/flipper.apk
```

The server serves the APK at `/flipper.apk`. Push in the app is native (FCM):
on startup it sends its token to `POST /api/v1/notifications/register`.

## Anti-ban

- Per-cycle request budget and a sliding hourly limit (shared by CLI and service).
- Niche rotation: a subset of searches per cycle, shuffled order.
- Work/rest mode 2.5 h / 30 min — the account cools down.
- One-hour cooldown on block (`flipper uncooldown` to reset manually).
- Stable install-id, single-instance lock, pauses between requests.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `status` shows 0 but DB has data | not run from `backend/`, DB path is absolute |
| logs say "включаю cooldown" (cooldown on) | anti-ban triggered; wait or run `uncooldown` |
| `LLM 429 (лимит), ждём Ns` (rate limit) | provider free tier; listings wait for the next cycle, consider lowering `analyze_per_cycle` |
| "рыночный поиск упал" (market search failed) | temporary API failure, verdict falls back to the model estimate |
| empty `recent` | cycle hasn't run yet; wait or `run --once` |

Tests:

```bash
cd backend && ./.venv/bin/pytest -q
```

## Structure

```
flipper/
├── backend/
│   ├── flipper/
│   │   ├── app/
│   │   │   ├── analyzer/     # LLM: providers, prompt, schema parsing
│   │   │   ├── api/routes.py # REST API + static frontend
│   │   │   ├── notify/       # telegram, fcm, web push, heartbeat, price-drop
│   │   │   ├── pricing/      # market price from analogues
│   │   │   ├── scraper/      # Kleinanzeigen and Vinted
│   │   │   ├── pipeline.py   # margin, verdict, liquidity
│   │   │   ├── scheduler.py  # schedule, lock, cooldown
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

## Limitations

- Scraping Kleinanzeigen and Vinted violates their terms of use. The project
  is personal; the owner is responsible for how it is used. The budgets and
  anti-ban measures above are tuned to stay reasonable.
- Code and text were written with the help of AI: structure and comments are
  occasionally redundant, but everything is tested and runs on a real server.
- The project is not meant for distribution: personal niches, city, and
  settings are baked in.
