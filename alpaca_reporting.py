import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compute_realized_pnl(filled_orders):
    """
    FIFO realized P/L estimate from filled buy/sell orders.
    """
    lots = defaultdict(deque)
    realized = 0.0
    closed_trades = 0
    wins = 0
    losses = 0

    for order in filled_orders:
        symbol = str(getattr(order, "symbol", ""))
        side = str(getattr(order, "side", "")).lower()
        qty = _to_float(getattr(order, "filled_qty", 0))
        price = _to_float(getattr(order, "filled_avg_price", 0))
        if not symbol or qty <= 0 or price <= 0:
            continue

        if "buy" in side:
            lots[symbol].append([qty, price])
            continue

        remaining = qty
        trade_pnl = 0.0
        while remaining > 1e-9 and lots[symbol]:
            lot_qty, lot_price = lots[symbol][0]
            matched = min(remaining, lot_qty)
            trade_pnl += (price - lot_price) * matched
            lot_qty -= matched
            remaining -= matched
            if lot_qty <= 1e-9:
                lots[symbol].popleft()
            else:
                lots[symbol][0][0] = lot_qty

        if qty > remaining:
            closed_trades += 1
            realized += trade_pnl
            if trade_pnl > 0:
                wins += 1
            elif trade_pnl < 0:
                losses += 1

    return {
        "realizedPnl": round(realized, 2),
        "closedTrades": closed_trades,
        "wins": wins,
        "losses": losses,
    }


def export_trades_json(client: TradingClient, output_path: str = "ui/public/trades.json") -> dict:
    req = GetOrdersRequest(status="all", limit=500, nested=False)
    orders = client.get_orders(filter=req)
    orders_sorted = sorted(
        orders,
        key=lambda o: getattr(o, "submitted_at", datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    filled = [o for o in orders if str(getattr(o, "status", "")).lower() == "filled"]
    filled_sorted = sorted(filled, key=lambda o: getattr(o, "filled_at", datetime.min.replace(tzinfo=timezone.utc)))

    buy_count = sum(1 for o in filled_sorted if "buy" in str(getattr(o, "side", "")).lower())
    sell_count = sum(1 for o in filled_sorted if "sell" in str(getattr(o, "side", "")).lower())

    positions = client.get_all_positions()
    open_positions = []
    total_unrealized = 0.0
    total_market_value = 0.0
    for p in positions:
        unrealized = _to_float(getattr(p, "unrealized_pl", 0))
        market_value = _to_float(getattr(p, "market_value", 0))
        total_unrealized += unrealized
        total_market_value += market_value
        open_positions.append(
            {
                "symbol": str(getattr(p, "symbol", "")),
                "qty": _to_float(getattr(p, "qty", 0)),
                "avgEntryPrice": _to_float(getattr(p, "avg_entry_price", 0)),
                "currentPrice": _to_float(getattr(p, "current_price", 0)),
                "marketValue": round(market_value, 2),
                "costBasis": round(_to_float(getattr(p, "cost_basis", 0)), 2),
                "side": str(getattr(p, "side", "")).lower(),
                "unrealizedPnl": round(unrealized, 2),
                "unrealizedPnlPct": round(_to_float(getattr(p, "unrealized_plpc", 0)) * 100, 2),
            }
        )

    realized = _compute_realized_pnl(filled_sorted)

    account = client.get_account()
    equity = _to_float(getattr(account, "equity", 0))
    last_equity = _to_float(getattr(account, "last_equity", 0))
    daily_pnl = round(equity - last_equity, 2)
    account_details = {
        "id": str(getattr(account, "id", "")),
        "status": str(getattr(account, "status", "")),
        "currency": str(getattr(account, "currency", "")),
        "buyingPower": _to_float(getattr(account, "buying_power", 0)),
        "cash": _to_float(getattr(account, "cash", 0)),
        "equity": equity,
        "lastEquity": last_equity,
        "portfolioValue": _to_float(getattr(account, "portfolio_value", 0)),
        "patternDayTrader": bool(getattr(account, "pattern_day_trader", False)),
        "tradingBlocked": bool(getattr(account, "trading_blocked", False)),
        "accountBlocked": bool(getattr(account, "account_blocked", False)),
    }

    recent_fills = []
    for o in reversed(filled_sorted[-25:]):
        recent_fills.append(
            {
                "id": str(getattr(o, "id", "")),
                "symbol": str(getattr(o, "symbol", "")),
                "side": str(getattr(o, "side", "")).lower(),
                "qty": _to_float(getattr(o, "filled_qty", 0)),
                "price": _to_float(getattr(o, "filled_avg_price", 0)),
                "filledAt": getattr(o, "filled_at", None).isoformat() if getattr(o, "filled_at", None) else None,
            }
        )

    all_orders = []
    for o in orders_sorted:
        all_orders.append(
            {
                "id": str(getattr(o, "id", "")),
                "symbol": str(getattr(o, "symbol", "")),
                "side": str(getattr(o, "side", "")).lower(),
                "status": str(getattr(o, "status", "")).lower(),
                "type": str(getattr(o, "type", "")).lower(),
                "timeInForce": str(getattr(o, "time_in_force", "")).lower(),
                "qty": _to_float(getattr(o, "qty", 0)),
                "filledQty": _to_float(getattr(o, "filled_qty", 0)),
                "limitPrice": _to_float(getattr(o, "limit_price", 0)),
                "stopPrice": _to_float(getattr(o, "stop_price", 0)),
                "filledAvgPrice": _to_float(getattr(o, "filled_avg_price", 0)),
                "submittedAt": getattr(o, "submitted_at", None).isoformat() if getattr(o, "submitted_at", None) else None,
                "filledAt": getattr(o, "filled_at", None).isoformat() if getattr(o, "filled_at", None) else None,
            }
        )

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "account": account_details,
        "summary": {
            "filledOrders": len(filled_sorted),
            "allOrders": len(orders),
            "buyOrders": buy_count,
            "sellOrders": sell_count,
            "closedTrades": realized["closedTrades"],
            "wins": realized["wins"],
            "losses": realized["losses"],
            "realizedPnl": realized["realizedPnl"],
            "unrealizedPnl": round(total_unrealized, 2),
            "totalPnl": round(realized["realizedPnl"] + total_unrealized, 2),
            "dailyPnl": daily_pnl,
            "openPositions": len(open_positions),
            "marketValueOpenPositions": round(total_market_value, 2),
        },
        "openPositions": sorted(open_positions, key=lambda x: x["unrealizedPnl"], reverse=True),
        "recentFills": recent_fills,
        "allOrders": all_orders,
    }

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
