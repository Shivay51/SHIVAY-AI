# SHIVAY AI V10 Security Checklist

- [x] `.env`, keys, secrets, credentials, logs, runtime cache, locks, and backups are ignored by Git.
- [x] Startup enforces `ENABLE_LIVE_ORDER_PLACEMENT=false`, `SIGNALS_ONLY=true`, and `PAPER_MONITORING=true`.
- [x] No Telegram order-placement command is registered.
- [x] Webhook requests require a non-empty local secret and constant-time authentication.
- [x] Replay, duplicate-event, stale-time, future-time, size, symbol, category, contract, timeframe, and OHLC validation are enabled.
- [x] Exact symbol/contract allowlisting is required before a stream can be enabled.
- [x] Admin actions use configured IDs and authorization checks.
- [x] Rate limiting, temporary blocking, sanitization, path validation, and audit events are implemented.
- [x] Logs mask tokens, passwords, API keys, webhook secrets, and access tokens.
- [x] User messages do not expose providers, webhook diagnostics, raw indicators, or stack traces.
- [x] Public debug mode is disabled.
- [ ] Confirm public DNS, TLS, reverse proxy, and firewall configuration on the hosting account.
- [ ] Rotate any credential immediately if a future secret scan reports exposure.

Never commit `.env`, storage data, user/chat databases, logs, security audit data, or backup archives.
