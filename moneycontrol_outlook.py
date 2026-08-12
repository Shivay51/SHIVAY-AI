"""Strict Moneycontrol snapshots for market context only.

This module is deliberately separate from tradable-contract providers.  Its
values may influence an outlook, but must never be used for entries or orders.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import requests
import websocket
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

IST = ZoneInfo("Asia/Kolkata")
LOGGER = logging.getLogger("shivay.moneycontrol.outlook")
INDIA_URL = "https://www.moneycontrol.com/stocksmarketsindia/"
GIFT_URL = "https://www.moneycontrol.com/live-index/gift-nifty?symbol=in%3Bgsx"
INDEX_URLS = {
    "DOW": "https://www.moneycontrol.com/live-index/dowjones",
    "S&P 500": "https://www.moneycontrol.com/live-index/sp500?symbol=SPX%3AIND",
    "NASDAQ": "https://www.moneycontrol.com/live-index/nasdaq",
}
CORE_NAMES = ("GIFT NIFTY", "DOW", "S&P 500", "NASDAQ")
_CACHE_LOCK = threading.RLock()
_CACHE: dict[str, Any] | None = None
_CACHE_AT = 0.0


def _chrome_path() -> str | None:
    configured = os.getenv("CHROME_EXECUTABLE", "").strip()
    candidates = [configured,
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]
    return next((value for value in candidates if value and os.path.isfile(value)), None)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _rendered_next_data(urls: Mapping[str, str], timeout: float = 25.0) -> dict[str, Mapping[str, Any]]:
    """Read public rendered DOM through an installed browser, without login or bypass."""
    executable = _chrome_path()
    if not executable:
        return {}
    port = _free_local_port()
    profile = tempfile.mkdtemp(prefix="shivay_mc_")
    startup = None
    creation_flags = 0
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        creation_flags = subprocess.CREATE_NO_WINDOW
    command = [executable, f"--remote-debugging-port={port}", "--remote-allow-origins=*",
               "--disable-background-mode", "--disable-component-update", "--no-first-run",
               "--no-default-browser-check", f"--user-data-dir={profile}", *urls.values()]
    process: subprocess.Popen[bytes] | None = None
    connections: list[Any] = []
    result: dict[str, Mapping[str, Any]] = {}
    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   startupinfo=startup, creationflags=creation_flags)
        deadline = time.monotonic() + timeout
        tabs: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            try:
                response = requests.get(f"http://127.0.0.1:{port}/json", timeout=1)
                response.raise_for_status()
                tabs = [dict(item) for item in response.json() if isinstance(item, Mapping)]
                if all(any(target.split("?", 1)[0] in str(tab.get("url", "")) for tab in tabs)
                       for target in urls.values()):
                    break
            except (requests.RequestException, TypeError, ValueError):
                pass
            time.sleep(0.25)
        for name, target in urls.items():
            tab = next((item for item in tabs if target.split("?", 1)[0] in str(item.get("url", ""))), None)
            if not tab or not tab.get("webSocketDebuggerUrl"):
                continue
            connection = websocket.create_connection(str(tab["webSocketDebuggerUrl"]), timeout=5)
            connections.append(connection)
            request_id = 1
            value = ""
            while time.monotonic() < deadline and not value:
                connection.send(json.dumps({"id": request_id, "method": "Runtime.evaluate",
                                            "params": {"expression": "document.querySelector('#__NEXT_DATA__')?.textContent || ''",
                                                       "returnByValue": True}}))
                while True:
                    message = json.loads(connection.recv())
                    if message.get("id") == request_id:
                        value = str((((message.get("result") or {}).get("result") or {}).get("value") or ""))
                        break
                request_id += 1
                if not value:
                    time.sleep(0.25)
            if value:
                result[name] = parse_next_data(f'<script id="__NEXT_DATA__">{value}</script>')
        if connections:
            try:
                connections[0].send(json.dumps({"id": 99999, "method": "Browser.close"}))
            except Exception:
                pass
    except (OSError, subprocess.SubprocessError, requests.RequestException, websocket.WebSocketException,
            TypeError, ValueError, json.JSONDecodeError):
        LOGGER.warning("Public rendered market page unavailable", exc_info=True)
    finally:
        for connection in connections:
            try:
                connection.close()
            except Exception:
                pass
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
        for _ in range(4):
            try:
                shutil.rmtree(profile)
                break
            except OSError:
                time.sleep(0.25)
    return result


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(value: Any) -> datetime | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def parse_next_data(html: str) -> Mapping[str, Any]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.I | re.S)
    if not match:
        raise ValueError("Next data is unavailable")
    payload = json.loads(unescape(match.group(1)))
    stock = (((payload.get("props") or {}).get("pageProps") or {}).get("consumptionData") or {}).get("stockData")
    if not isinstance(stock, Mapping):
        raise ValueError("Stock snapshot is unavailable")
    return stock


def parse_stock_snapshot(name: str, stock: Mapping[str, Any], fetched_at: datetime | None = None) -> dict[str, Any]:
    fetched = fetched_at or datetime.now(timezone.utc)
    price = _number(stock.get("current_price", stock.get("lastprice")))
    previous = _number(stock.get("prev_close", stock.get("prevclose")))
    displayed_change = _number(stock.get("net_change", stock.get("change")))
    displayed_percent = _number(stock.get("percent_change", stock.get("percentchange")))
    opened = _number(stock.get("open"))
    high = _number(stock.get("high"))
    low = _number(stock.get("low"))
    stamp = _timestamp(stock.get("lastupd_epoch", stock.get("epoch_time")))
    state = str(stock.get("market_state") or "UNKNOWN").upper()
    errors: list[str] = []
    if price is None or price <= 0 or previous is None or previous <= 0:
        errors.append("invalid price or previous close")
    if displayed_change is None or displayed_percent is None:
        errors.append("missing displayed change")
    if price is not None and previous and displayed_change is not None:
        calculated = price - previous
        if abs(calculated - displayed_change) > max(0.05, price * 0.00001):
            errors.append("change arithmetic mismatch")
        calculated_percent = calculated / previous * 100.0
        if displayed_percent is None or abs(calculated_percent - displayed_percent) > 0.025:
            errors.append("percentage arithmetic mismatch")
        if abs(displayed_change) > 0.05 and displayed_percent and displayed_change * displayed_percent < 0:
            errors.append("change sign mismatch")
    if stamp is None:
        errors.append("missing market timestamp")
    age = max(0.0, (fetched - stamp).total_seconds()) if stamp else None
    max_age = 20 * 60 if state == "OPEN" else 36 * 60 * 60
    fresh = age is not None and age <= max_age
    if not fresh:
        errors.append("stale market timestamp")
    return {
        "name": name, "available": not errors, "validated": not errors,
        "ltp": price, "previous_close": previous, "change": displayed_change,
        "change_percent": displayed_percent, "open": opened, "high": high, "low": low,
        "timestamp": stamp, "retrieved_at": fetched, "age_seconds": age,
        "freshness": "LIVE" if fresh and state == "OPEN" else "CLOSED" if fresh else "STALE",
        "direction_usable": not errors, "market_state": state, "validation_errors": errors,
    }


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.current: list[str] | None = None
        self.cell: list[str] | None = None
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "tr":
            self.current = []
        elif tag.lower() in {"td", "th"} and self.current is not None:
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self.current is not None and self.cell is not None:
            value = re.sub(r"\s+", " ", unescape("".join(self.cell))).strip()
            self.current.append(value)
            self.cell = None
        elif tag.lower() == "tr" and self.current is not None:
            if self.current:
                self.rows.append(self.current)
            self.current = None


def parse_market_table(html: str, fetched_at: datetime | None = None) -> dict[str, dict[str, Any]]:
    fetched = fetched_at or datetime.now(timezone.utc)
    parser = _TableParser()
    parser.feed(html)
    aliases = {"GIFT NIFTY": "GIFT NIFTY", "DOW JONES": "DOW", "S&P 500": "S&P 500",
               "NASDAQ": "NASDAQ", "NIKKEI": "NIKKEI", "HANG SENG": "HANG SENG", "KOSPI": "KOSPI"}
    result: dict[str, dict[str, Any]] = {}
    for row in parser.rows:
        if len(row) < 4:
            continue
        label = re.sub(r"\s*\([^)]*\)\s*$", "", row[0]).upper().strip()
        name = next((target for source, target in aliases.items() if label == source), None)
        if not name:
            continue
        price, change, percent = _number(row[1]), _number(row[2]), _number(row[3])
        previous = price - change if price is not None and change is not None else None
        stock = {"current_price": price, "prev_close": previous, "net_change": change,
                 "percent_change": percent, "lastupd_epoch": fetched.timestamp(), "market_state": "OPEN"}
        item = parse_stock_snapshot(name, stock, fetched)
        item["timestamp_kind"] = "page_fetch_time"
        result[name] = item
    return result


class MoneycontrolOutlookClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        retry = Retry(total=2, connect=2, read=2, backoff_factor=0.35,
                      status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET"}))
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SHIVAY-AI/1.0)", "Accept": "text/html"})

    def _get(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def get_snapshot(self, force_refresh: bool = True) -> dict[str, Any]:
        global _CACHE, _CACHE_AT
        with _CACHE_LOCK:
            if not force_refresh and _CACHE is not None and time.monotonic() - _CACHE_AT <= 30:
                return dict(_CACHE)
        fetched = datetime.now(timezone.utc)
        values: dict[str, dict[str, Any]] = {}
        failures: dict[str, str] = {}
        try:
            values["GIFT NIFTY"] = parse_stock_snapshot("GIFT NIFTY", parse_next_data(self._get(GIFT_URL)), fetched)
        except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError) as error:
            failures["GIFT NIFTY"] = type(error).__name__
        try:
            table = parse_market_table(self._get(INDIA_URL), fetched)
            values.update({key: value for key, value in table.items() if key not in values})
        except (requests.RequestException, ValueError, TypeError) as error:
            failures["market table"] = type(error).__name__
        for name, url in INDEX_URLS.items():
            try:
                values[name] = parse_stock_snapshot(name, parse_next_data(self._get(url)), fetched)
            except (requests.RequestException, ValueError, TypeError, json.JSONDecodeError) as error:
                failures[name] = type(error).__name__
        missing = {name: (GIFT_URL if name == "GIFT NIFTY" else INDEX_URLS[name])
                   for name in CORE_NAMES if not values.get(name, {}).get("validated")}
        if missing:
            rendered = _rendered_next_data(missing, timeout=max(20.0, self.timeout + 10.0))
            for name, stock in rendered.items():
                item = parse_stock_snapshot(name, stock, fetched)
                if item.get("validated"):
                    values[name] = item
                    failures.pop(name, None)
        for name in CORE_NAMES:
            values.setdefault(name, {"name": name, "available": False, "validated": False,
                                     "freshness": "UNAVAILABLE", "direction_usable": False,
                                     "retrieved_at": fetched, "validation_errors": [failures.get(name, "unavailable")]})
        valid = all(values[name].get("validated") for name in CORE_NAMES)
        result = {"fetched_at": fetched, "inputs": values, "valid": valid, "failures": failures,
                  "validation_errors": [f"{name}: {', '.join(values[name].get('validation_errors') or [])}"
                                        for name in CORE_NAMES if not values[name].get("validated")]}
        with _CACHE_LOCK:
            _CACHE, _CACHE_AT = result, time.monotonic()
        return dict(result)

    def close(self) -> None:
        self.session.close()


__all__ = ["CORE_NAMES", "MoneycontrolOutlookClient", "parse_market_table", "parse_next_data", "parse_stock_snapshot"]
