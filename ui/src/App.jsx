import { useEffect, useMemo, useState } from "react";
import "./App.css";

const pct = (value, digits = 1) => `${Number(value).toFixed(digits)}%`;
const money = (value) => `$${Number(value ?? 0).toFixed(2)}`;
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const DASHBOARD_TOKEN = import.meta.env.VITE_DASHBOARD_TOKEN ?? "";

const buildHeaders = () => {
  const headers = {};
  if (DASHBOARD_TOKEN) {
    headers["x-dashboard-token"] = DASHBOARD_TOKEN;
  }
  return headers;
};

function App() {
  const [data, setData] = useState(null);
  const [trades, setTrades] = useState(null);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      try {
        const [resultsRes, tradesRes] = await Promise.all([
          fetch(`${API_BASE}/api/results`, { cache: "no-store", headers: buildHeaders() }),
          fetch(`${API_BASE}/api/trades`, { cache: "no-store", headers: buildHeaders() }),
        ]);
        if (!resultsRes.ok) {
          throw new Error(`Failed to load /api/results (${resultsRes.status})`);
        }
        if (!tradesRes.ok) {
          throw new Error(`Failed to load /api/trades (${tradesRes.status})`);
        }
        const [resultsPayload, tradesPayload] = await Promise.all([
          resultsRes.json(),
          tradesRes.json(),
        ]);
        setData(resultsPayload);
        setTrades(tradesPayload);
        setStatus("ready");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
        setStatus("error");
      }
    };
    load();
  }, []);

  const topFive = useMemo(() => data?.candidates?.slice(0, 5) ?? [], [data]);
  const orderHistory = useMemo(() => trades?.allOrders ?? [], [trades]);

  return (
    <div className="page">
      <div className="bg-glow bg-glow-1" />
      <div className="bg-glow bg-glow-2" />

      <main className="container">
        <header className="hero card">
          <p className="eyebrow">Trading Dashboard</p>
          <h1>Top Losers Bounce Finder</h1>
          <p className="sub">
            A machine-learning project that scans top daily losers and ranks
            which names are most likely to bounce short-term.
          </p>
          <div className="pill-row">
            <span className="pill">React UI</span>
            <span className="pill">Python + yfinance</span>
            <span className="pill">Random Forest</span>
          </div>
        </header>

        {status === "loading" && <section className="card">Loading data...</section>}

        {status === "error" && (
          <section className="card error">
            Could not load API data: {error}
            <br />
            Check API URL/token env vars and backend health endpoint.
          </section>
        )}

        {status === "ready" && data && (
          <>
            <section className="grid stats">
              <article className="card stat">
                <p>Total Candidates</p>
                <h2>{data?.stats?.totalCandidates ?? 0}</h2>
              </article>
              <article className="card stat">
                <p>Average Bounce Probability</p>
                <h2>{pct(data?.stats?.avgBounceProb ?? 0, 1)}</h2>
              </article>
              <article className="card stat">
                <p>High Confidence (&ge; 60%)</p>
                <h2>{data?.stats?.highConfidenceCount ?? 0}</h2>
              </article>
            </section>

            <section className="grid stats pnl-stats">
              <article className="card stat">
                <p>Realized P/L</p>
                <h2 className={(trades?.summary?.realizedPnl ?? 0) >= 0 ? "up" : "down"}>
                  ${Number(trades?.summary?.realizedPnl ?? 0).toFixed(2)}
                </h2>
              </article>
              <article className="card stat">
                <p>Unrealized P/L</p>
                <h2 className={(trades?.summary?.unrealizedPnl ?? 0) >= 0 ? "up" : "down"}>
                  ${Number(trades?.summary?.unrealizedPnl ?? 0).toFixed(2)}
                </h2>
              </article>
              <article className="card stat">
                <p>Total P/L</p>
                <h2 className={(trades?.summary?.totalPnl ?? 0) >= 0 ? "up" : "down"}>
                  ${Number(trades?.summary?.totalPnl ?? 0).toFixed(2)}
                </h2>
              </article>
            </section>

            <section className="grid split">
              <article className="card">
                <h3>Project Explanation</h3>
                <p>
                  This model learns from historical big-drop days and checks
                  whether the stock achieved a small upside bounce in the next
                  few sessions.
                </p>
                <p>
                  Today, it applies that pattern to the newest top losers and
                  outputs a probability score for each ticker.
                </p>
                <ul>
                  {data?.project?.signals?.map((signal) => (
                    <li key={signal}>{signal}</li>
                  ))}
                </ul>
                <h3>How Bounce Predictions Are Made</h3>
                <p>
                  For each stock, the model compares today&apos;s setup to past
                  setups using RSI, volatility, price distance from trend, wick
                  behavior, volume pressure, and daily return. It then outputs a
                  bounce probability score based on how often similar patterns
                  led to a short-term rebound in historical data.
                </p>
              </article>

              <article className="card">
                <h3>Top 5 Quick View</h3>
                <div className="bars">
                  {topFive.map((row) => (
                    <div className="bar-row" key={row.Ticker}>
                      <div className="bar-head">
                        <strong>{row.Ticker}</strong>
                        <span>{pct(row.BounceProb)}</span>
                      </div>
                      <div className="bar-track">
                        <div
                          className="bar-fill"
                          style={{ width: `${Math.max(0, Math.min(100, row.BounceProb))}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </article>
            </section>

            <section className="card">
              <div className="table-head">
                <h3>Ranked Candidates</h3>
                <p>Generated: {new Date(data.generatedAt).toLocaleString()}</p>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Day Move</th>
                      <th>Return</th>
                      <th>Volume Ratio</th>
                      <th>RSI</th>
                      <th>Bounce Prob</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data?.candidates?.map((row) => (
                      <tr key={row.Ticker}>
                        <td>{row.Ticker}</td>
                        <td className="down">{pct(row.PctChange, 2)}</td>
                        <td className="down">{pct(row.Return, 2)}</td>
                        <td>{Number(row.VolumeRatio).toFixed(2)}x</td>
                        <td>{Number(row.RSI).toFixed(1)}</td>
                        <td>
                          <span className="score">{pct(row.BounceProb, 1)}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="grid split">
              <article className="card">
                <div className="table-head">
                  <h3>Open Positions</h3>
                  <p>{trades?.summary?.openPositions ?? 0} active</p>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Qty</th>
                        <th>Avg Entry</th>
                        <th>Current</th>
                        <th>Market Value</th>
                        <th>Cost Basis</th>
                        <th>Unrealized P/L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(trades?.openPositions ?? []).length === 0 && (
                        <tr>
                          <td colSpan={8}>No open positions.</td>
                        </tr>
                      )}
                      {(trades?.openPositions ?? []).map((p) => (
                        <tr key={p.symbol}>
                          <td>{p.symbol}</td>
                          <td className={p.side === "long" ? "up" : "down"}>{p.side || "-"}</td>
                          <td>{Number(p.qty).toFixed(0)}</td>
                          <td>{money(p.avgEntryPrice)}</td>
                          <td>{money(p.currentPrice)}</td>
                          <td>{money(p.marketValue)}</td>
                          <td>{money(p.costBasis)}</td>
                          <td className={p.unrealizedPnl >= 0 ? "up" : "down"}>
                            {money(p.unrealizedPnl)} ({pct(p.unrealizedPnlPct, 2)})
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>

              <article className="card">
                <div className="table-head">
                  <h3>Recent Filled Trades</h3>
                  <p>{trades?.summary?.filledOrders ?? 0} fills</p>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Symbol</th>
                        <th>Side</th>
                        <th>Qty</th>
                        <th>Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(trades?.recentFills ?? []).length === 0 && (
                        <tr>
                          <td colSpan={5}>No filled orders yet.</td>
                        </tr>
                      )}
                      {(trades?.recentFills ?? []).map((fill, idx) => (
                        <tr key={`${fill.symbol}-${fill.filledAt ?? idx}`}>
                          <td>{fill.filledAt ? new Date(fill.filledAt).toLocaleString() : "-"}</td>
                          <td>{fill.symbol}</td>
                          <td className={fill.side === "buy" ? "up" : "down"}>{fill.side}</td>
                          <td>{Number(fill.qty).toFixed(0)}</td>
                          <td>${Number(fill.price).toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </article>
            </section>

            <section className="card">
              <div className="table-head">
                <h3>All Order History</h3>
                <p>{orderHistory.length} orders</p>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Submitted</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Status</th>
                      <th>Type</th>
                      <th>Qty</th>
                      <th>Filled</th>
                      <th>Avg Fill</th>
                      <th>TIF</th>
                      <th>Order ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orderHistory.length === 0 && (
                      <tr>
                        <td colSpan={10}>No orders yet.</td>
                      </tr>
                    )}
                    {orderHistory.map((o) => (
                      <tr key={o.id}>
                        <td>{o.submittedAt ? new Date(o.submittedAt).toLocaleString() : "-"}</td>
                        <td>{o.symbol || "-"}</td>
                        <td className={o.side === "buy" ? "up" : "down"}>{o.side || "-"}</td>
                        <td>{o.status || "-"}</td>
                        <td>{o.type || "-"}</td>
                        <td>{Number(o.qty ?? 0).toFixed(0)}</td>
                        <td>{Number(o.filledQty ?? 0).toFixed(0)}</td>
                        <td>{o.filledAvgPrice ? money(o.filledAvgPrice) : "-"}</td>
                        <td>{o.timeInForce || "-"}</td>
                        <td className="mono">{o.id}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

export default App;
