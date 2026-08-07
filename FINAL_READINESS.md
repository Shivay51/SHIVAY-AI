# SHIVAY Final Readiness

1. Run `RUN_SHIVAY_PREFLIGHT.bat`. It must report `PREFLIGHT PASSED`.
2. Start only with the existing `START_SHIVAY.bat` after the preflight passes.
3. Keep `.env` private. Do not commit or send credentials.
4. The policy remains alerts-only: no broker order placement is enabled.
5. Configure TradingView only after the local webhook health check reports healthy.
