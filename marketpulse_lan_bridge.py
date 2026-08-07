"""LAN-only Market Pulse notification-to-Telegram bridge; never places orders."""
from __future__ import annotations
import asyncio, hashlib, hmac, json, os, time
from typing import Any
from dotenv import load_dotenv
from telegram import Bot
load_dotenv(override=False)
RECENT: dict[str, float] = {}
def env(name: str, default: str = "") -> str: return os.getenv(name, default).strip()
def clean(value: Any, limit: int) -> str: return " ".join(str(value or "").split())[:limit]
async def respond(writer: asyncio.StreamWriter, status: int, value: str) -> None:
    body=json.dumps({"status":value}).encode(); reason={200:"OK",202:"Accepted",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",413:"Payload Too Large",415:"Unsupported Media Type",429:"Too Many Requests"}.get(status,"Error")
    writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()+body); await writer.drain(); writer.close(); await writer.wait_closed()
async def receive(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, bot: Bot, destination: int, secret: str) -> None:
    try:
        head=await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),4); lines=head.decode("latin1").split("\r\n"); request=lines[0].split()
        if len(request)!=3:return await respond(writer,400,"rejected")
        method,path,_=request
        if method=="GET" and path=="/health":return await respond(writer,200,"ok")
        if method!="POST" or path!="/marketpulse-alert":return await respond(writer,404,"rejected")
        headers={key.strip().lower():value.strip() for line in lines[1:] if ":" in line for key,value in [line.split(":",1)]}
        if not headers.get("content-type","").lower().startswith("application/json"):return await respond(writer,415,"rejected")
        length=int(headers.get("content-length","-1"))
        if length<2 or length>8192:return await respond(writer,413 if length>8192 else 400,"rejected")
        data=json.loads((await asyncio.wait_for(reader.readexactly(length),4)).decode())
        if not isinstance(data,dict) or not hmac.compare_digest(str(data.get("secret","")),secret):return await respond(writer,401,"rejected")
        if clean(data.get("package"),80)!="in.marketpulse":return await respond(writer,403,"rejected")
        title,text=clean(data.get("title"),240),clean(data.get("text"),1500)
        if not title and not text:return await respond(writer,400,"rejected")
        now=time.monotonic(); key=hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()
        RECENT.update({item:stamp for item,stamp in RECENT.items() if now-stamp<120})
        if key in RECENT:return await respond(writer,202,"duplicate")
        if len(RECENT)>=100:return await respond(writer,429,"rate_limited")
        RECENT[key]=now
        await bot.send_message(chat_id=destination,text=f"📲 SHIVAY — MARKET PULSE ALERT\n\n{title or 'Market Pulse'}\n{text or 'Notification received'}\n\nExternal app alert only — verify in Market Pulse before action."[:4000])
        await respond(writer,202,"accepted")
    except Exception:
        try: await respond(writer,400,"rejected")
        except Exception: writer.close()
async def main() -> None:
    if env("MARKETPULSE_BRIDGE_ENABLED").lower() not in {"1","true","yes","on"}:raise RuntimeError("Set MARKETPULSE_BRIDGE_ENABLED=true in .env")
    secret=env("MARKETPULSE_BRIDGE_SECRET"); token=env("BOT_TOKEN") or env("TELEGRAM_BOT_TOKEN"); target=env("CHAT_ID") or env("ADMIN_ID")
    if len(secret)<32 or not token or not target.lstrip("-").isdigit():raise RuntimeError("Set MARKETPULSE_BRIDGE_SECRET (32+ chars), BOT_TOKEN and ADMIN_ID/CHAT_ID in .env")
    port=int(env("MARKETPULSE_BRIDGE_PORT","8766")); server=await asyncio.start_server(lambda r,w:receive(r,w,Bot(token),int(target),secret),env("MARKETPULSE_BRIDGE_HOST","0.0.0.0"),port)
    print(f"Market Pulse bridge listening on port {port}")
    async with server: await server.serve_forever()
if __name__=="__main__":asyncio.run(main())
