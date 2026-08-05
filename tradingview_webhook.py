"""Supervised minimal HTTP receiver for POST /tradingview-webhook."""
from __future__ import annotations
import asyncio,json,logging,os,secrets,tempfile
from pathlib import Path
from typing import Any
from tradingview_bridge import load_symbol_map,process_accepted_payload
from tradingview_cache import get_tradingview_cache
from tradingview_payload import parse_payload,PayloadError
from tradingview_security import TradingViewSecurity,SecurityError

LOGGER=logging.getLogger("shivay.tradingview.webhook");_SERVER=None;_LOCK=asyncio.Lock();_APPLICATION=None;_SECURITY=None
ROOT=Path(__file__).resolve().parent
def _enabled()->bool:return os.getenv("TRADINGVIEW_WEBHOOK_ENABLED","false").strip().lower() in {"1","true","yes","on"}
def _web_enabled()->bool:return os.getenv("WEB_SERVER_ENABLED","true").strip().lower() in {"1","true","yes","on"}
def _path()->str:
    value=os.getenv("TRADINGVIEW_WEBHOOK_PATH","/tradingview-webhook").strip();return value if value.startswith("/") and ".." not in value else "/tradingview-webhook"
def _security()->TradingViewSecurity:
    replay=os.getenv("TRADINGVIEW_REPLAY_STORE","").strip();return TradingViewSecurity(replay or None)
def status()->dict[str,Any]:return {"enabled":_enabled(),"web_server_enabled":_web_enabled(),"running":_SERVER is not None,"path":_path(),"public_base_url":os.getenv("TRADINGVIEW_PUBLIC_BASE_URL","https://app.vanraj.co.in"),"cache":get_tradingview_cache().status(),"symbols":sorted(load_symbol_map())}
def _ensure_secret()->None:
    """Generate a dedicated local webhook secret without logging its value."""
    current=os.getenv("TRADINGVIEW_WEBHOOK_SECRET","").strip()
    if 24<=len(current)<=128:return
    value=secrets.token_urlsafe(48);path=ROOT/".env"
    lines=path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    replaced=False;updated=[]
    for line in lines:
        if line.lstrip().startswith("TRADINGVIEW_WEBHOOK_SECRET="):
            updated.append(f"TRADINGVIEW_WEBHOOK_SECRET={value}");replaced=True
        else:updated.append(line)
    if not replaced:updated.extend(["",f"TRADINGVIEW_WEBHOOK_SECRET={value}"])
    handle,temp_name=tempfile.mkstemp(prefix=".env.",suffix=".tmp",dir=str(ROOT))
    try:
        with os.fdopen(handle,"w",encoding="utf-8") as stream:
            stream.write("\n".join(updated).rstrip()+"\n");stream.flush();os.fsync(stream.fileno())
        os.replace(temp_name,path);os.environ["TRADINGVIEW_WEBHOOK_SECRET"]=value
    finally:
        if os.path.exists(temp_name):os.unlink(temp_name)
async def _reply(writer,status:int,message:str)->None:
    payload={"status":"ok","service":"shivay-ai-webhook"} if status==200 else {"status":message}
    body=json.dumps(payload,separators=(",",":")).encode();reason={200:"OK",202:"Accepted",400:"Bad Request",401:"Unauthorized",403:"Forbidden",404:"Not Found",405:"Method Not Allowed",413:"Payload Too Large",415:"Unsupported Media Type",429:"Too Many Requests"}.get(status,"Error")
    writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()+body);await writer.drain();writer.close();await writer.wait_closed()
async def _handle(reader:asyncio.StreamReader,writer:asyncio.StreamWriter)->None:
    try:
        head=await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"),3);lines=head.decode("latin1").split("\r\n");parts=lines[0].split()
        if len(parts)!=3:return await _reply(writer,400,"rejected")
        method,path,_=parts
        if method=="GET" and path=="/health":return await _reply(writer,200,"ok")
        if path!=_path():return await _reply(writer,404,"rejected")
        if method!="POST":return await _reply(writer,405,"rejected")
        if not _enabled():return await _reply(writer,403,"rejected")
        headers={k.strip().lower():v.strip() for line in lines[1:] if ":" in line for k,v in [line.split(":",1)]}
        require_https=os.getenv("TRADINGVIEW_REQUIRE_HTTPS","false").strip().lower() in {"1","true","yes","on"}
        if require_https and headers.get("x-forwarded-proto","").lower()!="https":return await _reply(writer,403,"rejected")
        if not headers.get("content-type","").lower().startswith("application/json"):return await _reply(writer,415,"rejected")
        try:length=int(headers.get("content-length","-1"))
        except ValueError:length=-1
        maximum=max(1024,min(int(os.getenv("TRADINGVIEW_MAX_REQUEST_BYTES","32768") or 32768),131072))
        if length<2 or length>maximum:return await _reply(writer,413 if length>maximum else 400,"rejected")
        body=await asyncio.wait_for(reader.readexactly(length),3);data=json.loads(body.decode("utf-8"));security=_SECURITY or _security();security.authenticate(data.get("secret"));payload=parse_payload(data)
        mappings=load_symbol_map();allowed=set(mappings);configured={x.strip() for x in os.getenv("TRADINGVIEW_ALLOWED_SYMBOLS","").split(",") if x.strip()};allowed&=configured if configured else allowed
        mapping=mappings.get(payload.symbol) or {}
        expected_category=str(mapping.get("category") or ("GLOBAL_CONTEXT" if mapping.get("context_only") else "GIFT" if "GIFT" in str(mapping.get("symbol","")).upper() else "MCX" if str(mapping.get("exchange","")).upper()=="MCX" else "NSE_FO")).upper()
        allowed_categories={x.strip().upper() for x in os.getenv("TRADINGVIEW_ALLOWED_CATEGORIES","NSE_FO,MCX,CONTEXT,GIFT,GLOBAL_CONTEXT").split(",") if x.strip()}
        equivalent = {"GIFT", "CONTEXT"}
        category_matches = payload.category == expected_category or {payload.category, expected_category}.issubset(equivalent)
        if payload.category not in allowed_categories or not category_matches:raise SecurityError("category_not_allowed")
        if str(mapping.get("exchange","")).upper()!=payload.exchange.upper():raise SecurityError("instrument_mapping_mismatch")
        if payload.source=="TRADINGVIEW_PINE_ALERT" and str(mapping.get("symbol","")).upper()!=payload.instrument.upper():raise SecurityError("instrument_mapping_mismatch")
        if mapping.get("contract_text") and str(mapping["contract_text"]).strip()!=payload.contract_text:raise SecurityError("contract_mismatch")
        timeframes={int(x) for x in os.getenv("TRADINGVIEW_ALLOWED_TIMEFRAMES","5,15,30,60").split(",") if x.strip().isdigit()}
        configured_timeframes={int(x) for x in mapping.get("allowed_timeframes",[]) if str(x).isdigit()}
        if configured_timeframes:timeframes&=configured_timeframes
        peer=writer.get_extra_info("peername");security.validate(payload,allowed,timeframes,str(peer[0]) if peer else "unknown")
        if get_tradingview_cache().put(payload)=="out_of_order":raise PayloadError("out_of_order_candle")
        asyncio.create_task(process_accepted_payload(_APPLICATION,payload),name="tradingview-analysis");await _reply(writer,202,"accepted")
    except (PayloadError,ValueError,json.JSONDecodeError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):await _reply(writer,400,"rejected")
    except SecurityError as error:await _reply(writer,429 if str(error)=="rate_limited" else 401,"rejected")
    except Exception:
        LOGGER.exception("TradingView webhook request failed safely")
        try:await _reply(writer,400,"rejected")
        except Exception:writer.close()
async def start_tradingview_webhook(application:Any)->bool:
    global _SERVER,_APPLICATION,_SECURITY
    async with _LOCK:
        if _SERVER:return True
        if _enabled():_ensure_secret()
        security=_security()
        if not _web_enabled():return False
        if _enabled() and (not security.configured or not load_symbol_map()):LOGGER.warning("TradingView webhook enabled but security/symbol mapping is incomplete")
        host=os.getenv("TRADINGVIEW_WEBHOOK_HOST",os.getenv("WEB_SERVER_HOST","127.0.0.1")).strip();port=max(1,min(int(os.getenv("TRADINGVIEW_WEBHOOK_PORT",os.getenv("WEB_SERVER_PORT","8765")) or 8765),65535))
        if host not in {"127.0.0.1","::1","localhost"}:raise RuntimeError("Webhook server must bind to loopback behind the HTTPS reverse proxy")
        _APPLICATION=application;_SECURITY=security;_SERVER=await asyncio.start_server(_handle,host,port,limit=65536);LOGGER.info("TradingView webhook receiver started on configured local endpoint");return True
async def stop_tradingview_webhook()->None:
    global _SERVER,_APPLICATION,_SECURITY
    async with _LOCK:
        if _SERVER:_SERVER.close();await _SERVER.wait_closed()
        _SERVER=_APPLICATION=_SECURITY=None

async def startup_self_check()->dict[str,Any]:
    """Run secret-safe local lifecycle checks; never sends Telegram messages."""
    state=status();host=os.getenv("TRADINGVIEW_WEBHOOK_HOST",os.getenv("WEB_SERVER_HOST","127.0.0.1")).strip();port=max(1,min(int(os.getenv("TRADINGVIEW_WEBHOOK_PORT",os.getenv("WEB_SERVER_PORT","8765")) or 8765),65535));health=False
    if state["running"]:
        try:
            reader,writer=await asyncio.wait_for(asyncio.open_connection(host,port),3)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n");await writer.drain();reply=await asyncio.wait_for(reader.read(2048),3);writer.close();await writer.wait_closed();health=b"200 OK" in reply and b"shivay-ai-webhook" in reply
        except Exception:health=False
    security=_security();timeframes={int(x) for x in os.getenv("TRADINGVIEW_ALLOWED_TIMEFRAMES","5,15,30,60").split(",") if x.strip().isdigit()}
    try:
        import config
        safety=not bool(config.ENABLE_LIVE_ORDER_PLACEMENT) and bool(config.SIGNALS_ONLY) and bool(config.PAPER_MONITORING)
    except Exception:safety=False
    return {"server_running":state["running"],"local_health":health,"webhook_enabled":state["enabled"],"token_configured":security.configured,"enabled_symbols":len(state["symbols"]),"timeframes_valid":timeframes=={5,15,30,60},"signals_only":safety}
