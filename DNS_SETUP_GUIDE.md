# DNS setup for SHIVAY AI

Current evidence: SHIVAY AI is running on a Windows 10 laptop. No Nginx, Caddy, Apache, IIS, listener on ports 80/443, or public-server configuration was found. Public DNS currently has no record for `app.vanraj.co.in`.

Do not point DNS at a guessed address. First decide where the always-on public HTTPS service will run:

- Recommended: deploy SHIVAY AI and Caddy together on an always-on server with a fixed public IPv4 address.
- Alternative: connect the public server to this laptop using an explicitly approved secure private network or tunnel. Do not expose local port 8765 to the internet.

## Add the record in GoDaddy

1. Sign in to GoDaddy.
2. Open **My Products**, select `vanraj.co.in`, then open **DNS**.
3. Choose **Add New Record**.
4. If the final server has a fixed public IPv4 address, enter:

   - Type: `A`
   - Name: `app`
   - Value: the final server's real fixed public IPv4 address
   - TTL: `600 seconds` or `Automatic`

5. If the hosting provider gives a stable hostname instead of an IP, use:

   - Type: `CNAME`
   - Name: `app`
   - Value: the exact hostname supplied by that provider
   - TTL: `600 seconds` or `Automatic`

6. Do not create both A and CNAME records for `app`.
7. Wait for DNS propagation and verify that `app.vanraj.co.in` resolves to the intended server.
8. Only after DNS resolves, start Caddy so it can obtain a trusted TLS certificate.

The final public checks must be:

```text
https://app.vanraj.co.in/health
https://app.vanraj.co.in/tradingview-webhook
```

The health endpoint must return `{"status":"ok","service":"shivay-ai-webhook"}`. A valid synthetic webhook POST should return HTTP 202. Never place the webhook token in DNS, the URL, screenshots, or support messages.
