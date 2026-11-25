# AI Bounce Finder

Quant workflow that ranks daily market losers by short-term bounce probability and can place paper trades via Alpaca.

## Security First

- Never commit `.env`.
- Rotate Alpaca keys if they were exposed.
- Keep trading/account data behind backend API authentication.

## Local Run

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run backend API (serves both API and built UI):

```bash
.venv/bin/uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

Run frontend dev server (optional during development):

```bash
cd ui
npm install
VITE_API_BASE_URL=http://localhost:8000 VITE_DASHBOARD_TOKEN=your-token npm run dev
```

Set backend env vars in `.env`:

```env
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
DASHBOARD_TOKEN=your-token
Dashboard username/password are optional. If set, all routes except /health require Basic Auth.
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=strong-password
ALLOWED_ORIGINS=http://localhost:5173
```

## Trading Script

Manual run:

```bash
.venv/bin/python trade_top_pick.py --notional 5000 --min-prob 60 --take-profit-pct 3 --stop-loss-pct 2 --require-market-open
```

## Render Deployment

This repo includes `render.yaml` with:

- `ai-bounce-finder` (single Python service that also serves built UI)
- `ai-bounce-finder-daily-trade` (weekdays at 14:31 UTC = 9:31 AM ET standard time)
- `ai-bounce-finder-daily-trade-dst` (weekdays at 13:31 UTC = 9:31 AM ET daylight time)

Render cron is UTC and cannot follow US daylight saving by itself. Both jobs run every weekday. Each invocation buys only when the market is open and no buy has been placed yet that market day, so the early run is a no-op and the later run places the single ~$5,000 order at the open. It skips the day unless a candidate is at least 60% and trades at $5 or more with 500,000 average volume. The exit is a 3% take-profit and 2% stop from the fill, held up to 3 market days.

On Render, configure secrets:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `DASHBOARD_TOKEN`
- `DASHBOARD_USERNAME` (optional)
- `DASHBOARD_PASSWORD` (optional)
- `ALLOWED_ORIGINS`

Cron is already defined in `render.yaml` and runs:

```bash
python trade_top_pick.py --notional 5000 --min-prob 60 --take-profit-pct 3 --stop-loss-pct 2 --require-market-open
```
