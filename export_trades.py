from trade_top_pick import get_trading_client
from alpaca_reporting import export_trades_json


def main() -> None:
    client = get_trading_client()
    payload = export_trades_json(client, "data/trades.cache.json")
    summary = payload["summary"]
    print(
        "Saved data/trades.cache.json | "
        f"realized=${summary['realizedPnl']:.2f} "
        f"unrealized=${summary['unrealizedPnl']:.2f} "
        f"total=${summary['totalPnl']:.2f}"
    )


if __name__ == "__main__":
    main()
