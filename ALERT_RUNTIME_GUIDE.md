# SHIVAY Alert Runtime

`alert_runtime.py` is an alerts-only safety stage. It reads prepared signals from a JSONL file, rejects signals that fail `alert_quality_policy.json`, suppresses duplicates during the configured cooldown, and writes only accepted alerts to another JSONL file. It never submits an order.

## Signal contract

Each input line must be a JSON object with `symbol`, `market`, `side`, `score`, `risk_reward`, `confirmed_timeframes`, `price_verified`, `candle_closed`, and `volume_confirmed`.

Example:

```json
{"symbol":"NIFTY","market":"NSE_FNO","side":"BUY","score":86,"risk_reward":2.0,"confirmed_timeframes":["15m","30m","1h"],"price_verified":true,"candle_closed":true,"volume_confirmed":true}
```

## Run

```bash
python alert_runtime.py --input signals.jsonl --output accepted_alerts.jsonl
python -m unittest tests/test_shivay_alert_guard.py tests/test_alert_runtime.py
```

Connect the existing notifier only to `accepted_alerts.jsonl`; do not send raw scanner output directly to Telegram or TradingView.
