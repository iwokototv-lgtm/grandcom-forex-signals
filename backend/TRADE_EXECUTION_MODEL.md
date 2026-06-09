# Trade Execution Model — Gold Signals V4

> **Critical reading for anyone deploying this system in production.**
> This document clarifies who executes orders, where prices come from,
> and what latency risks exist.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     gold_server_v4.py                           │
│                   (Signal Generator)                            │
│                                                                 │
│  ┌──────────────┐   every 4H    ┌──────────────────────────┐   │
│  │ TwelveData   │ ─────────────▶│  Signal Generation       │   │
│  │ (4H candles) │               │  (HybridPortfolioSystem) │   │
│  └──────────────┘               └────────────┬─────────────┘   │
│                                              │ new signal       │
│  ┌──────────────┐   every 2min  ┌────────────▼─────────────┐   │
│  │ TwelveData   │ ─────────────▶│  trade_manager.py        │   │
│  │ (4H candles) │  ⚠ STALE      │  (BE / TS / Partial)     │   │
│  └──────────────┘               └────────────┬─────────────┘   │
│                                              │ writes           │
└──────────────────────────────────────────────┼─────────────────┘
                                               │
                                               ▼
                                    ┌──────────────────┐
                                    │    MongoDB        │
                                    │ gold_signals_v4   │
                                    │  (SL/TP values)   │
                                    └────────┬─────────┘
                                             │ reads (polling or webhook)
                                             ▼
                                    ┌──────────────────┐
                                    │  Broker /         │
                                    │  Copy-Trade       │
                                    │  Follower         │
                                    │  (executes orders)│
                                    └──────────────────┘
```

---

## 1. trade_manager is a Signal Generator, NOT an Order Executor

`trade_manager.py` **does not call any broker API**. It has no broker
credentials, no order-placement logic, and no direct market access.

What it does:
- Reads open trades from MongoDB (`gold_signals_v4` collection).
- Evaluates BE / TS / partial-profit logic against current prices.
- **Writes updated SL/TP values back to MongoDB.**

What it does NOT do:
- Place, modify, or cancel orders at a broker.
- Send instructions to a copy-trade platform.
- Guarantee that any SL/TP change is reflected in the market.

**The broker or copy-trade follower is responsible for reading MongoDB
and applying the updated SL/TP values to live orders.** This integration
is external to this codebase and must be implemented separately.

---

## 2. Price Source — 4H Candle Close from TwelveData (NOT Real-Time)

The management loop in `gold_server_v4.py` (`run_trade_management_loop`)
fetches prices as follows:

```python
df, _ = await fetch_ohlcv(pair, interval="4h", outputsize=5)
ind   = compute_indicators(df, PAIRS[pair]["decimals"])
current_prices[pair] = ind["price"]   # ← last["close"] of the 4H candle
```

`ind["price"]` is the **close price of the most recent 4H candle** returned
by TwelveData. Between candle closes this value is the close of the
*previous completed candle* — it does not update tick-by-tick.

This means:
- During an active 4H candle, `current_prices` can be up to **4 hours stale**.
- SL/TP checks in the management loop are evaluated against this stale price.
- A fast intracandle spike can breach a stop level without the loop detecting it.

### What a real-time price source would look like

To use real-time quotes, `current_prices` must be populated from a live
feed — for example a broker WebSocket stream or a dedicated quote endpoint:

```python
# Example: replace fetch_ohlcv with a real-time quote call
current_prices[pair] = await broker_ws.get_last_quote(pair)
```

Until a real-time feed is wired in, the management loop operates on
**stale 4H candle closes** and the spike risk described below applies.

---

## 3. Latency and Spike Risk

### Sources of latency

| Source | Worst-case lag |
|---|---|
| Management loop interval | 2 minutes |
| 4H candle staleness | Up to 4 hours |
| TwelveData API response time | 1–5 seconds |
| MongoDB write + broker poll round-trip | Broker-dependent |

### Spike scenario

1. Price is at 2340. SL is at 2330. Management loop last ran 90 seconds ago.
2. A news spike drives price to 2325 (below SL) and back to 2342 within 60 seconds.
3. The management loop runs at the 2-minute mark — price is now 2342, above SL.
4. **The SL hit is never detected.** The trade remains open.

Conversely, a spike can trigger a false SL hit if the broker enforces hard
orders but the signal server has not yet updated the SL after BE activation.

### Mitigations

**Recommended (in priority order):**

1. **Broker-side hard SL/TP orders** — The broker must place hard stop-loss
   and take-profit orders at the levels specified in the signal. These execute
   at the broker in real time regardless of what the signal server is doing.
   MongoDB values are then advisory / display-only, not the execution source.

2. **Real-time price feed** — Replace the `fetch_ohlcv` call in
   `run_trade_management_loop` with a real-time quote source (broker
   WebSocket, dedicated quote API). This eliminates 4H candle staleness.

3. **Reduce loop interval** — Lower `SIGNAL_INTERVAL_MINUTES` from 2 minutes
   to 30 seconds. This reduces the polling gap but does not fix candle
   staleness. Set via environment variable:
   ```
   SIGNAL_INTERVAL_MINUTES=1
   ```
   Note: this increases TwelveData API call frequency; check your plan limits.

4. **Webhook-based broker integration** — Instead of the broker polling
   MongoDB, have the signal server push SL/TP updates to the broker via
   webhook immediately after each MongoDB write. Eliminates broker poll lag.

---

## 4. Broker / Follower Integration Points

The following MongoDB fields are written by `trade_manager.py` and must be
consumed by the broker or copy-trade follower:

| Field | Written when | Meaning |
|---|---|---|
| `current_sl` | BE activated or TS updated | New stop-loss price |
| `be_activated` | BE triggered | SL has been moved to entry |
| `be_activated_at` | BE triggered | UTC timestamp of BE activation |
| `tp1_hit` / `tp2_hit` / `tp3_hit` | TP level reached | Partial close flag |
| `tp1_price` / `tp2_price` / `tp3_price` | TP level reached | Execution price |
| `tp1_lots` / `tp2_lots` / `tp3_lots` | TP level reached | Lots to close |
| `ts_last_updated` | TS moved | UTC timestamp of last TS update |
| `status` | Trade closed | `WIN`, `LOSS`, or `CLOSED` |
| `close_price` | Trade closed | Final close price |

The broker integration must:
1. Poll (or subscribe to) the `gold_signals_v4` collection for changes.
2. On `current_sl` change → modify the stop-loss order at the broker.
3. On `tp*_hit = true` → close the specified lot size at the broker.
4. On `status` = terminal → confirm the trade is fully closed.

---

## 5. Summary of Responsibilities

| Component | Responsibility |
|---|---|
| `gold_server_v4.py` | Generate signals; schedule management loop |
| `trade_manager.py` | Evaluate BE/TS/partial logic; write SL/TP to MongoDB |
| MongoDB | Shared state store between signal server and broker |
| **Broker / Follower** | **Execute orders; enforce hard SL/TP in the market** |
| TwelveData | Supply 4H OHLCV candles (not real-time quotes) |
| Real-time quote feed | *(Not yet implemented)* Supply tick-level prices |

---

## 6. Recommended Production Checklist

- [ ] Broker places hard SL/TP orders at signal entry (not relying on polling).
- [ ] Broker integration reads `current_sl` from MongoDB and updates orders.
- [ ] Real-time quote feed replaces `fetch_ohlcv` in `run_trade_management_loop`.
- [ ] Loop interval reviewed (default 2 min; consider 30 s for active markets).
- [ ] `SIGNAL_INTERVAL_MINUTES` env var documented in deployment config.
- [ ] Broker poll frequency is faster than the management loop interval.
- [ ] Spike protection (hard orders at broker) is verified in a staging environment.
