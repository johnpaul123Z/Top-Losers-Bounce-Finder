import argparse
import os
from datetime import datetime, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest
from dotenv import load_dotenv

from alpaca_reporting import export_trades_json
from main import build_rankings, export_json


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


def already_bought_today(client: TradingClient, symbol: str) -> bool:
    """
    Prevent duplicate buys for same symbol on same UTC day.
    """
    req = GetOrdersRequest(status="all", limit=500)
    orders = client.get_orders(filter=req)
    today = datetime.now(timezone.utc).date()
    for order in orders:
        side = str(getattr(order, "side", "")).lower()
        status = str(getattr(order, "status", "")).lower()
        if (
            getattr(order, "symbol", None) == symbol
            and "buy" in side
            and status in {"new", "accepted", "partially_filled", "filled"}
        ):
            submitted = getattr(order, "submitted_at", None)
            if submitted and submitted.date() == today:
                return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Buy 100 shares of top bounce-ranked stock on Alpaca paper trading."
    )
    parser.add_argument("--shares", type=int, default=100, help="Shares to buy.")
    parser.add_argument(
        "--min-prob",
        type=float,
        default=0.0,
        help="Minimum bounce probability required (0-100).",
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
    args = parser.parse_args()

    ranked = build_rankings(top_n=50)
    if ranked.empty:
        print("No ranked candidates available. No trade placed.")
        return

    export_json(ranked, args.json_out)
    top = ranked.iloc[0]
    symbol = str(top["Ticker"])
    bounce_prob = float(top["BounceProb"])

    print(f"Top pick: {symbol} | BounceProb={bounce_prob:.1f}% | Return={top['Return']:.2f}%")
    if bounce_prob < args.min_prob:
        print(
            f"Top pick probability {bounce_prob:.1f}% is below threshold {args.min_prob:.1f}%. No trade."
        )
        return

    client = get_trading_client()
    account = client.get_account()
    print(f"Account status={account.status} buying_power={account.buying_power}")

    if args.require_market_open and not is_market_open(client):
        print("Market is closed. Skipping order due to --require-market-open.")
        return

    trades_payload = export_trades_json(client, args.trades_json_out)
    print(
        "Trade report updated: "
        f"realized=${trades_payload['summary']['realizedPnl']:.2f} "
        f"unrealized=${trades_payload['summary']['unrealizedPnl']:.2f}"
    )

    if already_bought_today(client, symbol):
        print(f"Already bought {symbol} today. Skipping duplicate order.")
        return

    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=args.shares,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )

    if args.dry_run:
        print(f"[DRY RUN] Would place order: BUY {args.shares} {symbol}")
        return

    order = client.submit_order(order_data)
    print(
        f"Order submitted: id={order.id} symbol={order.symbol} side={order.side} "
        f"qty={order.qty} status={order.status}"
    )

    # Refresh report to include newest order quickly once it fills.
    export_trades_json(client, args.trades_json_out)


if __name__ == "__main__":
    main()
