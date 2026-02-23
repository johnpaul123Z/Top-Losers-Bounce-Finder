import os
import secrets
from base64 import b64decode
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import json

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from alpaca_reporting import export_trades_json
from main import build_rankings, export_json
from trade_top_pick import get_trading_client

app = FastAPI(title="AI Bounce Finder API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def basic_auth_guard(request: Request, call_next):
    # Optional dashboard auth for all routes except health checks.
    username = os.getenv("DASHBOARD_USERNAME", "").strip()
    password = os.getenv("DASHBOARD_PASSWORD", "").strip()
    if not username or not password:
        return await call_next(request)
    if request.url.path == "/health":
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Basic "):
        return JSONResponse(
            status_code=401,
            content={"detail": "Authentication required"},
            headers={"WWW-Authenticate": "Basic"},
        )
    try:
        decoded = b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        provided_user, provided_pass = decoded.split(":", 1)
    except Exception:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid auth header"},
            headers={"WWW-Authenticate": "Basic"},
        )
    if not (secrets.compare_digest(provided_user, username) and secrets.compare_digest(provided_pass, password)):
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": "Basic"},
        )
    return await call_next(request)


def require_dashboard_token(x_dashboard_token: Optional[str] = Header(default=None)) -> None:
    token = os.getenv("DASHBOARD_TOKEN", "").strip()
    if not token:
        return
    if x_dashboard_token != token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DATA_DIR = Path("data")
RESULTS_CACHE_PATH = DATA_DIR / "results.cache.json"


def empty_results_payload() -> dict:
    return {
        "generatedAt": now_iso(),
        "project": {
            "name": "AI Bounce Finder",
            "description": "Ranks top daily losers by short-term bounce probability after large down days.",
        },
        "stats": {"totalCandidates": 0, "avgBounceProb": 0.0, "highConfidenceCount": 0},
        "candidates": [],
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "ts": now_iso()}


@app.get("/api/results")
def get_results(_: None = Depends(require_dashboard_token)) -> dict:
    # Serve cached results so page views do not trigger model reruns.
    if RESULTS_CACHE_PATH.exists():
        return json.loads(RESULTS_CACHE_PATH.read_text(encoding="utf-8"))
    return empty_results_payload()


@app.post("/api/refresh-results")
def refresh_results(_: None = Depends(require_dashboard_token)) -> dict:
    """
    Rebuild probability rankings and overwrite cache.
    Intended for cron/manual refresh, not for every page view.
    """
    ranked = build_rankings(top_n=50)
    if ranked.empty:
        payload = empty_results_payload()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    export_json(ranked, str(RESULTS_CACHE_PATH))
    return json.loads(RESULTS_CACHE_PATH.read_text(encoding="utf-8"))


@app.get("/api/trades")
def get_trades(_: None = Depends(require_dashboard_token)) -> dict:
    client = get_trading_client()
    # Use temp path to avoid exposing data as public static files.
    return export_trades_json(client, output_path="data/trades.cache.json")


class TradeRequest(BaseModel):
    shares: int = 100
    min_prob: float = 0.0


@app.post("/api/trade-now")
def trade_now(req: TradeRequest, _: None = Depends(require_dashboard_token)) -> dict:
    ranked = build_rankings(top_n=50)
    if ranked.empty:
        raise HTTPException(status_code=400, detail="No ranked candidates available.")

    top = ranked.iloc[0]
    symbol = str(top["Ticker"])
    bounce_prob = float(top["BounceProb"])
    if bounce_prob < req.min_prob:
        raise HTTPException(
            status_code=400,
            detail=f"Top pick {symbol} probability {bounce_prob:.1f}% below threshold {req.min_prob:.1f}%",
        )

    client = get_trading_client()
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    order = client.submit_order(
        MarketOrderRequest(
            symbol=symbol,
            qty=req.shares,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
    )
    return {
        "ok": True,
        "symbol": symbol,
        "shares": req.shares,
        "bounceProb": round(bounce_prob, 2),
        "orderId": str(order.id),
        "status": str(order.status),
    }


DIST_DIR = Path(__file__).parent / "ui" / "dist"


@app.get("/")
def spa_index():
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"message": "UI not built yet. Run: cd ui && npm run build"}


@app.get("/{full_path:path}")
def spa_fallback(full_path: str):
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="Not found")
    candidate = DIST_DIR / full_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    index = DIST_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="UI not built.")
