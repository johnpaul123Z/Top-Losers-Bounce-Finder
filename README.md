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
.venv/bin/python trade_top_pick.py --shares 100 --min-prob 55 --require-market-open
```

## Render Deployment

This repo includes `render.yaml` with:

- `ai-bounce-finder` (single Python service that also serves built UI)
- `ai-bounce-finder-daily-trade` (cron job at 13:30 UTC weekdays = 8:30 AM ET standard time)

On Render, configure secrets:

- `ALPACA_API_KEY`
- `ALPACA_API_SECRET`
- `DASHBOARD_TOKEN`
- `DASHBOARD_USERNAME` (optional)
- `DASHBOARD_PASSWORD` (optional)
- `ALLOWED_ORIGINS`

Cron is already defined in `render.yaml` and runs:

```bash
python trade_top_pick.py --shares 100 --require-market-open
```
