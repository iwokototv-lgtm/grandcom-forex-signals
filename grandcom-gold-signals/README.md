# Grandcom Gold Signals

Gold Trading Service for XAUUSD & XAUEUR signals.

Sends automated AI-powered trading signals to the [@grandcomgold](https://t.me/grandcomgold) Telegram channel.

## Features

- Multi-indicator analysis: RSI, MACD, ADX, Stochastic, StochRSI, CCI, Williams%R
- Multi-timeframe analysis (H1 vs H4)
- DXY correlation engine (inverse relationship check)
- News guard (blocks signals ±60 min around high-impact events)
- Weighted confidence scoring: 40% Trend / 30% Momentum / 30% Triggers
- Price action pattern detection (Engulfing, Pin Bar, Doji)
- Safety switch (oversold pullback guard)
- Alignment scoring (Williams%R + StochRSI team vote)

## Pairs

- XAUUSD (Gold / US Dollar)
- XAUEUR (Gold / Euro)

## Deployment (Railway)

- **Root Directory**: `backend/`
- **Start Command**: `uvicorn gold_server:app --host 0.0.0.0 --port $PORT`

## Environment Variables

```
MONGO_URL=
DB_NAME=gold_signals
TELEGRAM_BOT_TOKEN=
TELEGRAM_GOLD_CHANNEL_ID=@grandcomgold
TWELVE_DATA_API_KEY=
OPENAI_API_KEY=
```
