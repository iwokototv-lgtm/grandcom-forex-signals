# Trade Execution Model — Gold Signals V4

> **Critical reading for anyone deploying this system in production.**
> This document clarifies who generates signals, who executes orders, where
> prices come from, and what the latency risks are.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Signal Server (this repo)                   │
│                                                                 │
│  gold_server_v4.py                                              │
│    ├── Generates BUY/SELL signals (4H strategy + GPT filter)    │
│    ├── Writes signal documents to MongoDB                       │
│    └── Runs trade_manager every 2 min                           │
│                                                                 │
│  trade_manager.py                                               │
│    ├── Reads open trades from MongoDB (in-memory cache)         │
│    ├── Evaluates BE / TS / partial-profit logic                 │
│    └── Writes updated SL/TP values back to MongoDB              │
│                  │                                              │
│                  │  ← NO broker API calls are made here         │
└──────────────────┼──────────────────────────────────────────────┘
                   │
                   ▼  MongoDB (gold_signals_v4 collection)
                   │
                   │  Broker / Copy-Trade Follower polls or
                   │  receives webhooks from MongoDB
                   ▼
┌─────────────────────────────────────────────────────────────────┐
│              Broker / Copy-Trade Platform (external)            │
│                                                                 │
│  Reads signal documents from MongoDB                            │
│  Places / modifies hard SL/TP orders on the trading account     │
│  Executes partial closes when TP levels are reached             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Trade Execution Model

### 2.1 Signal Server is a SIGNAL GENERATOR, not an Order Executor

`trade_manager.py` and `gold_server_v4.py` **do not call any broker API**.
They do not open positions, modify orders, or close trades on a live account.

What the signal server **does**:
- Generates BUY/SELL signals with entry, SL, and TP levels.
- Writes those signals as documents into the `gold_signals_v4` MongoDB collection.
- Every 2 minutes, evaluates BE/TS/partial-profit logic against current prices.
- Writes updated `current_sl`, `be_activated`, `tp1_hit`, `status`, etc. back to MongoDB.

What the signal server **does NOT do**:
- Call a broker REST API or WebSocket to place or modify orders.
- Directly close positions on any trading account.
- Guarantee that a stop-loss is enforced at the broker level.

### 2.2 The Broker / Follower is Responsible for Order Execution

The actual hard SL/TP orders live at the **broker or copy-trade platform**.
That external system is responsible for:

1. **Reading** signal documents from MongoDB (polling or webhook).
2. **Placing** the initial order with the SL/TP levels from the signal.
3. **Modifying** the SL/TP on the live order whenever `current_sl` changes in MongoDB.
4. **Closing** partial lots when `tp1_hit`, `tp2_hit`, etc. are set to `true`.

Until the broker/follower reads the updated MongoDB document and modifies the
live order, the old SL/TP remains active at the broker. **MongoDB is the
source of truth for signal intent; the broker is the source of truth for
order state.**

### 2.3 Integration Points

| MongoDB Field     | Meaning                                      | Broker Action Required          |
|-------------------|----------------------------------------------|---------------------------------|
| `status: ACTIVE`  | New signal — open a position                 | Place order with SL/TP          |
| `current_sl`      | Updated stop-loss level                      | Modify SL on live order         |
| `be_activated`    | Breakeven triggered — SL moved to entry      | Modify SL to `be_sl` value      |
| `tp1_hit: true`   | TP1 reached — close 50% of position          | Close partial lots              |
| `tp2_hit: true`   | TP2 reached — close 30% of remaining         | Close partial lots              |
| `status: LOSS`    | SL detected as hit by signal server          | Confirm close on broker side    |
| `status: PARTIAL` | Trade partially closed, TS now active        | Trailing stop now in effect     |

---

## 3. Price Source

### 3.1 Current Implementation (⚠️ Stale Price Risk)

The `run_trade_management_loop()` in `gold_server_v4.py` currently fetches
prices by calling:

```python
df = await fetch_ohlcv(pair, interval="4h", outputsize=5)
ind = compute_indicators(df, PAIRS[pair]["decimals"])
current_prices[pair] = ind["price"]   # ← last 4H candle CLOSE price
```

`ind["price"]` is `df.iloc[-1]["close"]` — the **close price of the most
recent completed 4H candle** from TwelveData. Between candle closes this
value is **up to 4 hours stale**.

This means:
- BE activation may be delayed by up to 4 hours after the trigger price is
  actually reached in the market.
- SL hits may not be detected until the next 4H candle closes.
- TP1 partial-profit may not be recorded until the candle closes above TP1.

### 3.2 Recommended: Real-Time Quote Feed

For accurate trade management, `current_prices` should be populated from a
**real-time quote feed**, not from 4H candle closes. Options include:

- **Broker WebSocket** — subscribe to live bid/ask ticks for XAUUSD/XAUEUR.
- **TwelveData WebSocket** (`wss://ws.twelvedata.com/v1/quotes/price`) —
  real-time price stream, available on paid plans.
- **Dedicated quote endpoint** — a lightweight REST endpoint that returns the
  current mid-price, polled every 30–60 seconds.

The `current_prices` dict passed to `trade_manager.run_management_cycle()`
accepts any `{pair: float}` mapping. Replacing the TwelveData 4H fetch with
a real-time source requires only changing how that dict is populated in
`run_trade_management_loop()`.

---

## 4. Latency and Spike Risk

### 4.1 Two Sources of Latency

| Source                          | Worst-case lag         |
|---------------------------------|------------------------|
| Management loop interval        | 2 minutes              |
| 4H candle price staleness       | Up to 4 hours          |
| **Combined worst case**         | **~4 hours**           |

With a real-time quote feed the combined worst case drops to **2 minutes**
(the loop interval alone).

### 4.2 Spike Risk

A fast price spike can:
1. **Blow through the stop-loss** before the 2-minute loop detects it.
2. **Reach TP1** and retrace before the loop records the partial close.
3. **Trigger BE** and retrace before the loop moves the SL.

**The signal server cannot prevent this.** It is a polling-based system.

### 4.3 Mitigations

| Mitigation                                  | Who implements it          |
|---------------------------------------------|----------------------------|
| Hard SL/TP orders at the broker             | Broker / copy-trade platform |
| Reduce loop interval from 2 min → 30 sec   | `gold_server_v4.py` scheduler config |
| Replace 4H price with real-time quote feed  | `run_trade_management_loop()` |
| Broker-side trailing stop (native feature)  | Broker / copy-trade platform |

**The most important mitigation is that the broker enforces hard SL/TP orders
natively.** If the broker holds a hard stop at the SL price, a spike will be
caught at the broker level regardless of what the signal server does. The
signal server's SL detection is a secondary safety net and a record-keeping
mechanism — it should not be the primary line of defence against adverse
price moves.

---

## 5. Recommended Production Configuration

```
1. Broker places hard SL/TP orders immediately when a signal is received.
   Do NOT rely solely on the signal server's polling loop for stop enforcement.

2. Replace the 4H TwelveData price fetch in run_trade_management_loop()
   with a real-time quote feed (broker WebSocket or TwelveData WebSocket).

3. If a real-time feed is not available, reduce the management loop interval
   from 2 minutes to 30 seconds:
     scheduler.add_job(run_trade_management_loop, "interval", seconds=30, ...)

4. The broker/follower should poll MongoDB for signal updates at least as
   frequently as the management loop runs (every 30–120 seconds).

5. Treat MongoDB signal documents as advisory intent. The broker's own
   order state is the authoritative record of what is actually open.
```

---

## 6. Summary

| Question                                      | Answer                                                  |
|-----------------------------------------------|---------------------------------------------------------|
| Does trade_manager call a broker API?         | **No.** It only reads/writes MongoDB.                   |
| Who executes the actual orders?               | The broker or copy-trade follower (external system).    |
| Where do hard SL/TP orders live?              | At the broker, not in MongoDB.                          |
| What price does the management loop use?      | Last 4H candle close from TwelveData (**stale**).       |
| Can a spike jump the stop between checks?     | **Yes.** The loop runs every 2 minutes.                 |
| What is the primary spike protection?         | Hard SL/TP orders enforced by the broker natively.      |
| How to improve latency?                       | Real-time quote feed + reduce loop to 30 seconds.       |
