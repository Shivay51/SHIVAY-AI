# Windows HTTPS deployment

The current machine has no detected reverse proxy. This folder prepares Caddy because it supports Windows and obtains trusted TLS certificates automatically after public DNS points to this machine or server.

Do not activate this configuration until:

1. `app.vanraj.co.in` resolves to the correct fixed public server IP.
2. TCP ports 80 and 443 reach that server. Port 80 is needed for common certificate validation and redirect handling; the webhook itself uses HTTPS 443.
3. SHIVAY AI runs on the same machine and its local health endpoint works on `127.0.0.1:8765`.
Keep port 8765 bound to `127.0.0.1`. Never forward or expose it publicly.

Install Caddy from its official Windows distribution, validate the configuration, and run it as a Windows service under a restricted service account. Use only one reverse proxy. Caddy will manage the public TLS certificate; do not use a self-signed certificate.

If SHIVAY AI remains on the laptop while Caddy runs on another server, `127.0.0.1:8765` on that server will not reach the laptop. In that case, deploy SHIVAY AI to the server or establish an explicitly approved private connection and change the upstream to that private address.
