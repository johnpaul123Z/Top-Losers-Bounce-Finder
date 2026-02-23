import argparse
import json
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

warnings.filterwarnings("ignore", category=FutureWarning)

# -----------------------------
# SETTINGS
# -----------------------------
TOP_N = 50
DROP_THRESHOLD = -0.05          # >= 5% down day
BOUNCE_TARGET = 0.03            # "small bounce": +3% max high in hold window
HOLD_DAYS = 3
LOOKBACK_PERIOD = "2y"
FEATURES = ["RSI", "ATR_PCT", "VolumeRatio", "Dist_MA20", "LowerWick", "Return"]


def fetch_top_losers(top_n: int = TOP_N, min_drop: float = DROP_THRESHOLD) -> pd.DataFrame:
    """
    Pull current day losers from Yahoo screener via yfinance.
    """
    payload = yf.screen("day_losers", count=top_n)
    quotes = payload.get("quotes", [])
    if not quotes:
        raise RuntimeError("No screener results returned for day_losers.")

    rows = []
    for q in quotes:
        symbol = q.get("symbol")
        pct = q.get("regularMarketChangePercent")
        if not symbol or pct is None:
            continue
        rows.append({"Symbol": symbol, "PctChange": float(pct) / 100.0})

    losers = pd.DataFrame(rows)
    if losers.empty:
        raise RuntimeError("Screener returned no parseable loser rows.")

    losers = losers.dropna(subset=["PctChange"])
    losers = losers[losers["PctChange"] <= min_drop].head(top_n).reset_index(drop=True)
    return losers


def _future_window_max(high: pd.Series, days: int) -> pd.Series:
    """
    Max of next N days' highs (excluding current bar).
    """
    shifted = [high.shift(-i) for i in range(1, days + 1)]
    return pd.concat(shifted, axis=1).max(axis=1)


def build_features_for_ticker(ticker: str) -> pd.DataFrame:
    """
    Create model features + labels for one ticker.
    """
    df = yf.download(ticker, period=LOOKBACK_PERIOD, auto_adjust=True, progress=False)
    if df.empty or len(df) < 60:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    def col1d(name: str) -> pd.Series:
        col = df[name]
        if isinstance(col, pd.DataFrame):
            return col.iloc[:, 0]
        return col

    close = col1d("Close")
    high = col1d("High")
    low = col1d("Low")
    volume = col1d("Volume")

    df["Return"] = close.pct_change()
    df["RSI"] = RSIIndicator(close, window=14).rsi()
    atr = AverageTrueRange(high, low, close, window=14)
    df["ATR_PCT"] = atr.average_true_range() / close
    df["VolumeRatio"] = volume / volume.rolling(20).mean()
    df["MA20"] = close.rolling(20).mean()
    df["Dist_MA20"] = (close - df["MA20"]) / df["MA20"]

    candle_range = (high - low).replace(0, pd.NA)
    df["LowerWick"] = (close - low) / candle_range

    df["FutureHigh"] = _future_window_max(high, HOLD_DAYS)
    df["FutureReturn"] = (df["FutureHigh"] - close) / close
    df["Bounce"] = (df["FutureReturn"] >= BOUNCE_TARGET).astype(int)
    df["BigDrop"] = df["Return"] <= DROP_THRESHOLD
    df["Ticker"] = ticker
    keep_cols = ["Return", "RSI", "ATR_PCT", "VolumeRatio", "Dist_MA20", "LowerWick", "Bounce", "BigDrop", "Ticker"]
    return df[keep_cols].dropna()


def train_model(history: pd.DataFrame) -> RandomForestClassifier:
    if history.empty:
        raise RuntimeError("No training data available.")

    dataset = history[history["BigDrop"]].copy()
    if dataset.empty:
        raise RuntimeError("No drop events found for training.")
    if dataset["Bounce"].nunique() < 2:
        raise RuntimeError("Training labels contain only one class; need more diverse data.")

    X = dataset[FEATURES]
    y = dataset["Bounce"]

    model = RandomForestClassifier(
        n_estimators=400,
        max_depth=7,
        min_samples_leaf=15,
        random_state=42,
        class_weight="balanced_subsample",
    )
    model.fit(X, y)
    return model


def rank_today_candidates(history: pd.DataFrame, model: RandomForestClassifier) -> pd.DataFrame:
    latest = history.sort_index().groupby("Ticker").tail(1).copy()
    today = latest[latest["Return"] <= DROP_THRESHOLD].copy()
    if today.empty:
        return today

    today["BounceProb"] = model.predict_proba(today[FEATURES])[:, 1]
    return today.sort_values("BounceProb", ascending=False)


def build_rankings(top_n: int = TOP_N) -> pd.DataFrame:
    print("Pulling current losers...")
    losers = fetch_top_losers(top_n=top_n)
    if losers.empty:
        return pd.DataFrame()

    tickers = losers["Symbol"].tolist()
    print(f"Found {len(tickers)} losers. Downloading history and building features...")

    all_frames = []
    for ticker in tickers:
        frame = build_features_for_ticker(ticker)
        if not frame.empty:
            all_frames.append(frame)

    if not all_frames:
        return pd.DataFrame()

    history = pd.concat(all_frames).dropna()
    model = train_model(history)
    ranked = rank_today_candidates(history, model)
    if ranked.empty:
        return pd.DataFrame()

    merged = ranked.merge(losers, left_on="Ticker", right_on="Symbol", how="left")
    cols = ["Ticker", "PctChange", "Return", "VolumeRatio", "RSI", "BounceProb"]
    out = merged[cols].copy()
    out["PctChange"] = (out["PctChange"] * 100).round(2)
    out["Return"] = (out["Return"] * 100).round(2)
    out["BounceProb"] = (out["BounceProb"] * 100).round(1)
    out = out.sort_values("BounceProb", ascending=False).reset_index(drop=True)
    return out


def export_json(data: pd.DataFrame, output_path: str) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "project": {
            "name": "AI Bounce Finder",
            "description": "Ranks top daily losers by short-term bounce probability after large down days.",
            "model": "RandomForestClassifier",
            "signals": FEATURES,
            "thresholds": {
                "topN": TOP_N,
                "dropThresholdPct": DROP_THRESHOLD * 100,
                "bounceTargetPct": BOUNCE_TARGET * 100,
                "holdDays": HOLD_DAYS,
            },
        },
        "stats": {
            "totalCandidates": int(len(data)),
            "avgBounceProb": round(float(data["BounceProb"].mean()), 2) if not data.empty else 0.0,
            "highConfidenceCount": int((data["BounceProb"] >= 60).sum()) if not data.empty else 0,
        },
        "candidates": data.to_dict(orient="records"),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved JSON output to: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Find top losers likely to bounce.")
    parser.add_argument("--top-n", type=int, default=TOP_N, help="Number of top losers to pull.")
    parser.add_argument(
        "--json-out",
        type=str,
        default="",
        help="Optional path to write results JSON (for UI).",
    )
    args = parser.parse_args()

    ranked = build_rankings(top_n=args.top_n)

    if ranked.empty:
        print("No qualifying losers today after feature checks.")
        return

    out = ranked.head(10).copy()
    print("\nTop bounce candidates (AI model probability):")
    print(out.to_string(index=False))

    if args.json_out:
        export_json(ranked, args.json_out)


if __name__ == "__main__":
    main()