import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)
from dotenv import load_dotenv

from alpaca_reporting import export_trades_json
from main import HOLD_DAYS, build_rankings, export_json

ET = ZoneInfo("America/New_York")
MIN_PRICE = 5.0
MIN_AVG_VOLUME = 500_000
TRADE_LOG_PATH = Path("data/trade_log.json")
FILL_WAIT_SECONDS = 90
OPEN_BUY_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "partially_filled",
    "filled",
    "held",
}


def get_trading_client() -> TradingClient:
    load_dotenv()
    key = os.getenv("ALPACA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_API_SECRET", "").strip()
    base_url = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").strip()

    if not key or not secret:
        raise RuntimeError(
            "Missing Alpaca credentials. Set ALPACA_API_KEY and ALPACA_API_SECRET in .env"
        )

    paper = "paper-api.alpaca.markets" in base_url
    return TradingClient(api_key=key, secret_key=secret, paper=paper)


def is_market_open(client: TradingClient) -> bool:
    clock = client.get_clock()
    return bool(getattr(clock, "is_open", False))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _norm_enum(value) -> str:
    text = str(value or "").strip().lower()
    if "." in text:
        text = text.split(".")[-1]
    return text


def already_bought_today(client: TradingClient) -> bool:
    """
    One opening buy per US market day, regardless of symbol.
    """
    req = GetOrdersRequest(status="all", limit=500)
    orders = client.get_orders(filter=req)
    today = datetime.now(ET).date()
    for order in orders:
        side = _norm_enum(getattr(order, "side", ""))
        status = _norm_enum(getattr(order, "status", ""))
        if side != "buy" or status not in OPEN_BUY_STATUSES:
            continue
        submitted = getattr(order, "submitted_at", None)
        if submitted and _as_utc(submitted).astimezone(ET).date() == today:
            return True
    return False


def market_days_since(start, end) -> int:
    """Weekdays after start, up to and including end."""
    if end <= start:
        return 0
    days = 0
    cursor = start
    while cursor < end:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            days += 1
    return days


def load_trade_log(path: Path = TRADE_LOG_PATH) -> list:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    trades = payload.get("trades", [])
    return trades if isinstance(trades, list) else []


def save_trade_log(trades: list, path: Path = TRADE_LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trades": trades}, indent=2), encoding="utf-8")


def _position_qty(client: TradingClient, symbol: str) -> float:
    for position in client.get_all_positions():
        if str(getattr(position, "symbol", "")) == symbol:
            return float(getattr(position, "qty", 0) or 0)
    return 0.0


def cancel_open_orders(client: TradingClient, symbol: str) -> None:
    req = GetOrdersRequest(status="open", symbols=[symbol], limit=50)
    for order in client.get_orders(filter=req):
        client.cancel_order_by_id(order.id)


def wait_for_order(client: TradingClient, order_id: str, timeout: int = FILL_WAIT_SECONDS):
    deadline = time.time() + timeout
    order = client.get_order_by_id(order_id)
    while time.time() < deadline:
        status = _norm_enum(getattr(order, "status", ""))
        if status in {"filled", "canceled", "expired", "rejected", "suspended"}:
            return order
        time.sleep(2)
        order = client.get_order_by_id(order_id)
    return order


def record_exit(trades: list, symbol: str, exit_price: float, closed_at: str) -> None:
    for trade in reversed(trades):
        if trade.get("symbol") != symbol or trade.get("status") == "closed":
            continue
        entry = float(trade.get("fillPrice") or 0)
        shares = float(trade.get("shares") or 0)
        trade["exitPrice"] = round(exit_price, 4)
        trade["closedAt"] = closed_at
        trade["status"] = "closed"
        if entry > 0 and shares > 0:
            trade["realizedPnl"] = round((exit_price - entry) * shares, 2)
        return


def refresh_trade_log(client: TradingClient, path: Path = TRADE_LOG_PATH) -> list:
    """Fill in exit price and realized P/L once a logged trade is flat."""
    trades = load_trade_log(path)
    if not trades:
        return trades
    open_symbols = {
        str(getattr(position, "symbol", ""))
        for position in client.get_all_positions()
        if float(getattr(position, "qty", 0) or 0) > 0
    }
    req = GetOrdersRequest(status="closed", limit=200)
    closed_orders = client.get_orders(filter=req)
    changed = False
    for trade in trades:
        if trade.get("status") == "closed":
            continue
        symbol = str(trade.get("symbol") or "")
        if symbol in open_symbols:
            continue
        entry_at = trade.get("entryTime")
        exit_order = None
        for order in closed_orders:
            if str(getattr(order, "symbol", "")) != symbol:
                continue
            if _norm_enum(getattr(order, "side", "")) != "sell":
                continue
            if _norm_enum(getattr(order, "status", "")) != "filled":
                continue
            filled_at = getattr(order, "filled_at", None)
            if entry_at and filled_at and filled_at.isoformat() < entry_at:
                continue
            exit_order = order
            break
        if exit_order is None:
            continue
        filled_at = getattr(exit_order, "filled_at", None)
        record_exit(
            trades,
            symbol,
            float(getattr(exit_order, "filled_avg_price", 0) or 0),
            filled_at.isoformat() if filled_at else datetime.now(timezone.utc).isoformat(),
        )
        changed = True
    if changed:
        save_trade_log(trades, path)
    return trades


def close_expired_positions(client: TradingClient, dry_run: bool, path: Path = TRADE_LOG_PATH) -> None:
    """Market-sell a logged position that is still open after the 3-day hold."""
    trades = load_trade_log(path)
    today = datetime.now(ET).date()
    changed = False
    for trade in trades:
        if trade.get("status") == "closed":
            continue
        symbol = str(trade.get("symbol") or "")
        entry_raw = trade.get("entryTime")
        if not symbol or not entry_raw:
            continue
        entry_day = datetime.fromisoformat(entry_raw).astimezone(ET).date()
        if market_days_since(entry_day, today) < HOLD_DAYS:
            continue
        qty = _position_qty(client, symbol)
        if qty <= 0:
            continue
        print(f"{symbol} is still open after {HOLD_DAYS} market days. Closing it.")
        if dry_run:
            print(f"[DRY RUN] Would market-sell {qty} {symbol}.")
            continue
        cancel_open_orders(client, symbol)
        order = client.submit_order(
            MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
        )
        filled = wait_for_order(client, str(order.id))
        status = _norm_enum(getattr(filled, "status", ""))
        print(f"Close order {filled.id} for {symbol} status={status}")
        if status == "filled":
            filled_at = getattr(filled, "filled_at", None)
            record_exit(
                trades,
                symbol,
                float(getattr(filled, "filled_avg_price", 0) or 0),
                filled_at.isoformat() if filled_at else datetime.now(timezone.utc).isoformat(),
            )
            changed = True
    if changed:
        save_trade_log(trades, path)


def append_trade(entry: dict, path: Path = TRADE_LOG_PATH) -> None:
    trades = load_trade_log(path)
    trades.append(entry)
    save_trade_log(trades, path)


def place_exit_orders(client: TradingClient, symbol: str, qty: float, fill_price: float, tp_pct: float, sl_pct: float):
    take_profit_price = round(fill_price * (1 + (tp_pct / 100.0)), 2)
    stop_loss_price = round(fill_price * (1 - (sl_pct / 100.0)), 2)
    if stop_loss_price <= 0 or take_profit_price <= stop_loss_price:
        raise RuntimeError("Invalid exit prices from the fill. Check take-profit and stop-loss percents.")
    print(
        f"Exits from fill ${fill_price:.2f} | TP=${take_profit_price:.2f} (+{tp_pct:.2f}%) "
        f"SL=${stop_loss_price:.2f} (-{sl_pct:.2f}%) held up to {HOLD_DAYS} market days"
    )
    return client.submit_order(
        LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=take_profit_price,
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=take_profit_price),
            stop_loss=StopLossRequest(stop_price=stop_loss_price),
        )
    )


def shares_for_notional(price: float, notional: float) -> int:
    """
    Whole shares closest to the dollar budget. Bracket orders need a share count.
    """
    if price <= 0:
        raise RuntimeError(f"Invalid reference price: {price}")
    qty = int(round(notional / price))
    if qty < 1:
        raise RuntimeError(
            f"Reference price ${price:.2f} is above the ${notional:.0f} order size."
        )
    return qty


def get_reference_price(symbol: str) -> float:
    """
    Get latest close as reference for TP/SL bracket levels.
    """
    data = yf.download(symbol, period="5d", interval="1d", auto_adjust=True, progress=False)
    if data.empty:
        raise RuntimeError(f"Could not fetch reference price for {symbol}.")
    if "Close" not in data.columns:
        raise RuntimeError(f"Reference price missing Close column for {symbol}.")
    close_col = data["Close"]
    if hasattr(close_col, "iloc") and getattr(close_col, "ndim", 1) > 1:
        close_col = close_col.iloc[:, 0]
    price = float(close_col.iloc[-1])
    if price <= 0:
        raise RuntimeError(f"Invalid reference price for {symbol}: {price}")
    return price


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Buy about $5,000 of the top bounce-ranked loser once per market day."
    )
    parser.add_argument(
        "--notional",
        type=float,
        default=5000.0,
        help="Dollar amount to buy (default: 5000). Set <=0 to use --shares instead.",
    )
    parser.add_argument(
        "--shares",
        type=int,
        default=0,
        help="Share count. Used only when --notional is <= 0.",
    )
    parser.add_argument(
        "--min-prob",
        type=float,
        default=60.0,
        help="Minimum bounce probability required (0-100). Default: 60.",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default="data/results.cache.json",
        help="Where to save ranking JSON for the React dashboard.",
    )
    parser.add_argument(
        "--trades-json-out",
        type=str,
        default="data/trades.cache.json",
        help="Where to save Alpaca trade/PnL JSON for the React dashboard.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do everything except place the order.",
    )
    parser.add_argument(
        "--require-market-open",
        action="store_true",
        help="Only place trade when Alpaca market clock is open.",
    )
    parser.add_argument(
        "--take-profit-pct",
        type=float,
        default=3.0,
        help="Take profit percent above the fill price (default: 3). Set <=0 to skip exits.",
    )
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=2.0,
        help="Stop loss percent below the fill price (default: 2). Set <=0 to skip exits.",
    )
    args = parser.parse_args()

    client = get_trading_client()
    account = client.get_account()
    print(f"Account status={account.status} buying_power={account.buying_power}")

    if args.require_market_open and not is_market_open(client):
        print("Market is closed. Skipping order due to --require-market-open.")
        return

    refresh_trade_log(client)
    close_expired_positions(client, dry_run=args.dry_run)

    if already_bought_today(client):
        print("Already placed the opening buy today. No trade placed.")
        return

    ranked = build_rankings(top_n=50)
    if ranked.empty:
        print("No ranked candidates available. No trade placed.")
        return

    export_json(ranked, args.json_out)

    trades_payload = export_trades_json(client, args.trades_json_out)
    print(
        "Trade report updated: "
        f"realized=${trades_payload['summary']['realizedPnl']:.2f} "
        f"unrealized=${trades_payload['summary']['unrealizedPnl']:.2f}"
    )

    selected_row = None
    for _, row in ranked.iterrows():
        symbol = str(row["Ticker"])
        bounce_prob = float(row["BounceProb"])
        price = float(row["Price"]) if row.get("Price") == row.get("Price") else 0.0
        avg_volume = float(row["AvgVolume"]) if row.get("AvgVolume") == row.get("AvgVolume") else 0.0
        if bounce_prob < args.min_prob:
            break
        if price < MIN_PRICE:
            print(f"Skip {symbol}: price ${price:.2f} is below ${MIN_PRICE:.0f}.")
            continue
        if avg_volume < MIN_AVG_VOLUME:
            print(f"Skip {symbol}: average volume {avg_volume:,.0f} is below {MIN_AVG_VOLUME:,.0f}.")
            continue
        selected_row = row
        break

    if selected_row is None:
        print("No tradable candidate cleared the filters. No trade placed.")
        return

    symbol = str(selected_row["Ticker"])
    bounce_prob = float(selected_row["BounceProb"])
    ref_price = float(selected_row["Price"])
    print(
        f"Top eligible pick: {symbol} | BounceProb={bounce_prob:.1f}% | "
        f"Price=${ref_price:.2f} | AvgVolume={float(selected_row['AvgVolume']):,.0f}"
    )

    tp_pct = float(args.take_profit_pct)
    sl_pct = float(args.stop_loss_pct)
    use_exits = tp_pct > 0 and sl_pct > 0
    notional = float(args.notional)

    if notional > 0:
        qty = shares_for_notional(ref_price, notional)
        print(
            f"Order size: {qty} shares of {symbol} at ${ref_price:.2f} "
            f"(~${qty * ref_price:.2f}, target ${notional:.0f})"
        )
    else:
        qty = int(args.shares)
        if qty < 1:
            raise RuntimeError("Set --notional above 0 or --shares to at least 1.")

    if args.dry_run:
        print(
            f"[DRY RUN] Would buy {qty} {symbol}"
            + (f", then a {HOLD_DAYS}-day {tp_pct:.1f}% / {sl_pct:.1f}% exit from the fill" if use_exits else "")
        )
        return

    order = client.submit_order(
        MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
    )
    print(f"Entry submitted: id={order.id} symbol={symbol} qty={qty} status={order.status}")
    filled = wait_for_order(client, str(order.id))
    status = _norm_enum(getattr(filled, "status", ""))
    fill_price = float(getattr(filled, "filled_avg_price", 0) or 0)
    filled_qty = float(getattr(filled, "filled_qty", 0) or 0)
    print(f"Entry result: status={status} filled_qty={filled_qty} fill=${fill_price:.2f}")
    if status != "filled" or fill_price <= 0 or filled_qty <= 0:
        print("Entry did not fill. No exit orders placed.")
        return

    exit_order_id = None
    if use_exits:
        try:
            exit_order = place_exit_orders(client, symbol, filled_qty, fill_price, tp_pct, sl_pct)
            exit_order_id = str(exit_order.id)
            print(f"Exit order submitted: id={exit_order.id} status={exit_order.status}")
        except Exception as exc:
            print(f"Entry filled, but the exit order failed: {exc}")

    filled_at = getattr(filled, "filled_at", None)
    append_trade(
        {
            "symbol": symbol,
            "bounceProb": bounce_prob,
            "fillPrice": round(fill_price, 4),
            "shares": filled_qty,
            "notional": round(fill_price * filled_qty, 2),
            "entryTime": filled_at.isoformat() if filled_at else datetime.now(timezone.utc).isoformat(),
            "entryOrderId": str(filled.id),
            "exitOrderId": exit_order_id,
            "exitPrice": None,
            "realizedPnl": None,
            "closedAt": None,
            "status": "open",
        }
    )

    # Refresh report to include newest order quickly once it fills.
    export_trades_json(client, args.trades_json_out)


if __name__ == "__main__":
    main()
