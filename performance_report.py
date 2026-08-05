"""Durable completed-trade analytics and reporting for SHIVAY AI."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import math
import threading
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from security import sanitize_text, secure_json_load, secure_json_write, validate_file_path
from audience_router import recipients


LOGGER = logging.getLogger("shivay.performance")
IST = ZoneInfo("Asia/Kolkata")
PROJECT_ROOT = Path(__file__).resolve().parent
STORAGE_FILE = PROJECT_ROOT / "performance_data.json"
STORAGE_BACKUP = PROJECT_ROOT / "performance_data.json.bak"
LEGACY_CSV = PROJECT_ROOT / "trade_history.csv"
EXPORT_DIRECTORY = PROJECT_ROOT / "exports"
SCHEMA_VERSION = 1
_STORAGE_LOCK = threading.RLock()


class PerformanceStorageError(RuntimeError):
    """Raised when performance history cannot be loaded without risking data loss."""


def _now() -> datetime:
    return datetime.now(IST)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(str(value).replace("%", "").strip())
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _datetime(value: Any, default: datetime | None = None) -> datetime | None:
    if value in (None, ""):
        return default
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value).strip()
        parsed = None  # type: ignore[assignment]
        for parser in (
            lambda item: datetime.fromisoformat(item.replace("Z", "+00:00")),
            lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M"),
            lambda item: datetime.strptime(item, "%d-%m-%Y %H:%M"),
        ):
            try:
                parsed = parser(text)
                break
            except ValueError:
                continue
        if parsed is None:
            return default
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _side(value: Any) -> str | None:
    text = str(value or "").upper()
    buy = "BUY" in text or text in {"LONG", "BULLISH"}
    sell = "SELL" in text or text in {"SHORT", "BEARISH"}
    if buy == sell:
        return None
    return "BUY" if buy else "SELL"


def _direction(value: Any) -> str | None:
    text = str(value or "").upper()
    if any(item in text for item in ("BUY", "UP", "BULL")):
        return "UP"
    if any(item in text for item in ("SELL", "DOWN", "BEAR")):
        return "DOWN"
    if any(item in text for item in ("FLAT", "SIDEWAYS", "NEUTRAL")):
        return "FLAT"
    return None


def _empty_store() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trades": [],
        "statistics": {},
        "updated_at": None,
    }


def _valid_store(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("trades"), list)
        and all(isinstance(trade, dict) for trade in value["trades"])
    )


def _load_store() -> dict[str, Any]:
    with _STORAGE_LOCK:
        if not STORAGE_FILE.exists():
            return _empty_store()
        marker = object()
        data = secure_json_load(STORAGE_FILE, marker)
        if _valid_store(data):
            return data
        backup = secure_json_load(STORAGE_BACKUP, marker)
        if _valid_store(backup):
            LOGGER.warning("Recovered performance history from backup")
            secure_json_write(STORAGE_FILE, backup, backup=False)
            return backup
        raise PerformanceStorageError("Performance history is malformed and no valid backup exists")


def _save_store(store: dict[str, Any]) -> None:
    with _STORAGE_LOCK:
        if not secure_json_write(STORAGE_FILE, store, backup=True):
            raise PerformanceStorageError("Performance history could not be saved")


def _trade_id(trade: Mapping[str, Any]) -> str:
    supplied = sanitize_text(trade.get("trade_id", trade.get("id", "")), 128)
    if supplied:
        return supplied
    identity = "|".join(
        (
            str(trade.get("symbol", "")).upper(),
            str(trade.get("side", "")).upper(),
            f"{_number(trade.get('entry')):.8f}",
            f"{_number(trade.get('exit')):.8f}",
            str(trade.get("opened_at", "")),
            str(trade.get("closed_at", "")),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _normalize_trade(source: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol = sanitize_text(source.get("symbol", source.get("Symbol", "")), 40).upper()
    side = _side(source.get("side", source.get("decision", source.get("type"))))
    entry = _number(source.get("entry", source.get("Entry")))
    exit_price = _number(
        source.get("exit", source.get("exit_price", source.get("Exit", source.get("price"))))
    )
    if not symbol or side is None or entry <= 0 or exit_price <= 0:
        return None
    opened = _datetime(
        source.get("opened_at", source.get("entry_time", source.get("open_time")))
    )
    closed = _datetime(
        source.get("closed_at", source.get("exit_time", source.get("Date"))),
        _now(),
    )
    if opened is not None and closed is not None and opened > closed:
        opened = None
    quantity = max(_number(source.get("quantity", source.get("qty", 1)), 1.0), 0.0)
    calculated_pnl = (
        (exit_price - entry) * quantity
        if side == "BUY"
        else (entry - exit_price) * quantity
    )
    pnl = _number(source.get("pnl", source.get("profit", source.get("net_profit"))), calculated_pnl)
    calculated_percent = ((exit_price - entry) / entry) * 100.0
    if side == "SELL":
        calculated_percent *= -1.0
    pnl_percent = _number(
        source.get("pnl_percent", source.get("PnL %", source.get("return_percent"))),
        calculated_percent,
    )
    result_text = str(source.get("result", source.get("Result", ""))).upper()
    if result_text not in {"WIN", "LOSS", "BREAKEVEN"}:
        result_text = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
    stop_loss = _number(source.get("sl", source.get("stop_loss")))
    target = _number(source.get("target", source.get("target2", source.get("target1"))))
    risk = abs(_number(source.get("risk_amount", source.get("risk"))))
    if risk <= 0 and stop_loss > 0:
        risk = abs(entry - stop_loss) * max(quantity, 1.0)
    reward = abs(_number(source.get("reward_amount", source.get("reward"))))
    if reward <= 0 and target > 0:
        reward = abs(target - entry) * max(quantity, 1.0)
    if reward <= 0:
        reward = max(pnl, 0.0)
    risk_reward = _number(source.get("risk_reward", source.get("risk_reward_ratio")))
    if risk_reward <= 0 and risk > 0:
        risk_reward = reward / risk
    holding_seconds = _number(source.get("holding_seconds"))
    if holding_seconds <= 0 and opened is not None and closed is not None:
        holding_seconds = max((closed - opened).total_seconds(), 0.0)
    trade = {
        "symbol": symbol,
        "side": side,
        "entry": round(entry, 4),
        "exit": round(exit_price, 4),
        "quantity": round(quantity, 4),
        "opened_at": opened.isoformat() if opened else None,
        "closed_at": closed.isoformat() if closed else _now().isoformat(),
        "result": result_text,
        "pnl": round(pnl, 4),
        "pnl_percent": round(pnl_percent, 4),
        "risk": round(risk, 4),
        "reward": round(reward, 4),
        "risk_reward": round(risk_reward, 4),
        "holding_seconds": round(holding_seconds, 2),
        "strategy": sanitize_text(source.get("strategy", source.get("setup", "UNKNOWN")), 80) or "UNKNOWN",
        "market_condition": sanitize_text(
            source.get("market_condition", source.get("market", source.get("regime", "UNKNOWN"))), 80
        ) or "UNKNOWN",
        "gift_nifty_prediction": sanitize_text(
            source.get("gift_nifty_prediction", source.get("gift_prediction", "")), 40
        ) or None,
        "actual_market_direction": sanitize_text(
            source.get("actual_market_direction", source.get("market_direction", "")), 40
        ) or None,
        "reason": sanitize_text(source.get("reason", source.get("Reason", "")), 160),
        "failure_cause": sanitize_text(source.get("failure_cause", ""), 120) or None,
        "confidence": round(_number(source.get("confidence")), 2),
        "trend": sanitize_text(source.get("trend", source.get("regime", "UNKNOWN")), 80) or "UNKNOWN",
        "recorded_at": _now().isoformat(),
    }
    trade["trade_id"] = _trade_id({**trade, "trade_id": source.get("trade_id", source.get("id"))})
    return trade


def _legacy_trades() -> list[dict[str, Any]]:
    if not LEGACY_CSV.exists():
        return []
    trades = []
    try:
        with LEGACY_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                entry = _number(row.get("Entry"))
                exit_price = _number(row.get("Exit"))
                pnl_percent = _number(row.get("PnL %"))
                inferred_side = "BUY" if exit_price >= entry else "SELL"
                normalized = _normalize_trade(
                    {
                        **row,
                        "side": row.get("Side") or inferred_side,
                        "pnl": pnl_percent,
                        "pnl_percent": pnl_percent,
                        "closed_at": row.get("Date"),
                        "strategy": "LEGACY",
                    }
                )
                if normalized is not None:
                    normalized["source"] = "legacy_csv"
                    trades.append(normalized)
    except (OSError, csv.Error):
        LOGGER.exception("Legacy trade history could not be read")
    return trades


def _all_trades() -> list[dict[str, Any]]:
    store = _load_store()
    combined: dict[str, dict[str, Any]] = {}
    for source in [*store["trades"], *_legacy_trades()]:
        normalized = _normalize_trade(source)
        if normalized is not None:
            combined[normalized["trade_id"]] = normalized
    return sorted(combined.values(), key=lambda item: item.get("closed_at") or "")


def record_trade(
    trade: Mapping[str, Any] | str | None = None,
    entry: float | None = None,
    exit_price: float | None = None,
    result: str | None = None,
    pnl: float | None = None,
    reason: str = "",
    **kwargs: Any,
) -> bool:
    """Atomically record one completed trade; duplicate calls are idempotent.

    Accepts either a trade mapping or legacy-style symbol/entry/exit arguments.
    """
    if isinstance(trade, Mapping):
        source = dict(trade)
        source.update(kwargs)
    else:
        source = dict(kwargs)
        if trade is not None:
            source["symbol"] = trade
        source.update(
            entry=entry,
            exit_price=exit_price,
            result=result,
            pnl=pnl,
            reason=reason,
        )
    normalized = _normalize_trade(source)
    if normalized is None:
        LOGGER.warning("Rejected incomplete completed-trade record")
        return False
    try:
        with _STORAGE_LOCK:
            store = _load_store()
            identities = {str(item.get("trade_id")) for item in store["trades"]}
            if normalized["trade_id"] in identities:
                return False
            store["trades"].append(normalized)
            store["statistics"] = _calculate_metrics(store["trades"])
            store["updated_at"] = _now().isoformat()
            _save_store(store)
        return True
    except PerformanceStorageError:
        LOGGER.exception("Completed trade was not recorded because storage is unsafe")
        return False


def calculate_win_rate(trades: Sequence[Mapping[str, Any]] | None = None) -> float:
    records = list(trades) if trades is not None else _all_trades()
    wins = sum(1 for item in records if str(item.get("result", "")).upper() == "WIN")
    losses = sum(1 for item in records if str(item.get("result", "")).upper() == "LOSS")
    return round((wins / (wins + losses)) * 100.0, 2) if wins + losses else 0.0


def calculate_accuracy(trades: Sequence[Mapping[str, Any]] | None = None) -> float:
    """Return directional trade accuracy; breakeven trades are excluded."""
    return calculate_win_rate(trades)


def calculate_drawdown(trades: Sequence[Mapping[str, Any]] | None = None) -> float:
    records = list(trades) if trades is not None else _all_trades()
    equity = 0.0
    peak = 0.0
    maximum = 0.0
    for trade in records:
        equity += _number(trade.get("pnl"))
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return round(maximum, 2)


def _streaks(trades: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    maximum_wins = maximum_losses = current_wins = current_losses = 0
    for trade in trades:
        result = str(trade.get("result", "")).upper()
        if result == "WIN":
            current_wins += 1
            current_losses = 0
            maximum_wins = max(maximum_wins, current_wins)
        elif result == "LOSS":
            current_losses += 1
            current_wins = 0
            maximum_losses = max(maximum_losses, current_losses)
        else:
            current_wins = current_losses = 0
    return maximum_wins, maximum_losses


def _group_performance(trades: Sequence[Mapping[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(trade.get(field) or "UNKNOWN")].append(trade)
    result = {}
    for name, records in groups.items():
        result[name] = {
            "trades": len(records),
            "wins": sum(1 for item in records if item.get("result") == "WIN"),
            "losses": sum(1 for item in records if item.get("result") == "LOSS"),
            "win_rate": calculate_win_rate(records),
            "net_profit": round(sum(_number(item.get("pnl")) for item in records), 2),
            "net_profit_percent": round(sum(_number(item.get("pnl_percent")) for item in records), 2),
        }
    return dict(sorted(result.items()))


def _gift_accuracy(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tested = correct = 0
    for trade in trades:
        predicted = _direction(trade.get("gift_nifty_prediction"))
        actual = _direction(trade.get("actual_market_direction"))
        if predicted is None or actual is None:
            continue
        tested += 1
        correct += int(predicted == actual)
    return {
        "predictions": tested,
        "correct": correct,
        "accuracy": round((correct / tested) * 100.0, 2) if tested else 0.0,
    }


def _calculate_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda item: str(item.get("closed_at", "")))
    wins = [item for item in ordered if str(item.get("result", "")).upper() == "WIN"]
    losses = [item for item in ordered if str(item.get("result", "")).upper() == "LOSS"]
    profit_values = [_number(item.get("pnl")) for item in wins]
    loss_values = [_number(item.get("pnl")) for item in losses]
    holding = [_number(item.get("holding_seconds")) for item in ordered if _number(item.get("holding_seconds")) > 0]
    risk = sum(_number(item.get("risk")) for item in ordered)
    reward = sum(_number(item.get("reward")) for item in ordered)
    consecutive_wins, consecutive_losses = _streaks(ordered)
    total_profit = sum(value for value in profit_values if value > 0)
    total_loss = abs(sum(value for value in loss_values if value < 0))
    expectancy = (total_profit - total_loss) / len(ordered) if ordered else 0.0
    profit_factor = total_profit / total_loss if total_loss > 0 else (float(total_profit > 0) if ordered else 0.0)
    def asset(item: Mapping[str, Any]) -> str:
        symbol = str(item.get("symbol", "")).upper()
        if "GOLD" in symbol: return "GOLD"
        if "SILVER" in symbol: return "SILVER"
        if "BANKNIFTY" in symbol: return "BANKNIFTY"
        if "NIFTY" in symbol: return "NIFTY"
        return "OTHER"
    enriched = [{**item, "asset_class": asset(item)} for item in ordered]
    count = len(ordered)
    hit_rate = lambda key: round(sum(item.get(key) not in (None, "", False) for item in ordered) / count * 100, 2) if count else 0.0
    return {
        "total_trades": len(ordered),
        "buy_trades": sum(1 for item in ordered if item.get("side") == "BUY"),
        "sell_trades": sum(1 for item in ordered if item.get("side") == "SELL"),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "breakeven_trades": len(ordered) - len(wins) - len(losses),
        "win_rate": calculate_win_rate(ordered),
        "accuracy": calculate_accuracy(ordered),
        "total_profit": round(total_profit, 2),
        "total_loss": round(total_loss, 2),
        "net_profit": round(total_profit - total_loss, 2),
        "net_profit_percent": round(sum(_number(item.get("pnl_percent")) for item in ordered), 2),
        "average_profit": round(total_profit / len(wins), 2) if wins else 0.0,
        "average_loss": round(total_loss / len(losses), 2) if losses else 0.0,
        "average_win": round(total_profit / len(wins), 2) if wins else 0.0,
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "average_holding_seconds": round(sum(holding) / len(holding), 2) if holding else 0.0,
        "average_holding_time": _format_duration(sum(holding) / len(holding) if holding else 0.0),
        "risk_reward_ratio": round(reward / risk, 2) if risk > 0 else 0.0,
        "maximum_drawdown": calculate_drawdown(ordered),
        "consecutive_wins": consecutive_wins,
        "consecutive_losses": consecutive_losses,
        "symbol_wise_performance": _group_performance(ordered, "symbol"),
        "strategy_wise_performance": _group_performance(ordered, "strategy"),
        "market_condition_performance": _group_performance(ordered, "market_condition"),
        "gift_nifty_prediction_accuracy": _gift_accuracy(ordered),
        "asset_wise_performance": _group_performance(enriched, "asset_class"),
        "t1_hit_rate": hit_rate("time_to_t1"),
        "t2_hit_rate": hit_rate("time_to_t2"),
        "t3_hit_rate": hit_rate("time_to_t3"),
        "stop_loss_rate": hit_rate("time_to_sl"),
        "average_mfe": round(sum(_number(item.get("mfe")) for item in ordered) / count, 2) if count else 0.0,
        "average_mae": round(sum(_number(item.get("mae")) for item in ordered) / count, 2) if count else 0.0,
        "time_slot_performance": _group_performance(ordered, "time_slot"),
    }


def _format_duration(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _period_report(start: datetime, end: datetime, label: str) -> dict[str, Any]:
    trades = []
    for trade in _all_trades():
        closed = _datetime(trade.get("closed_at"))
        if closed is not None and start <= closed < end:
            trades.append(trade)
    report = _calculate_metrics(trades)
    report.update(
        {
            "period": label,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "generated_at": _now().isoformat(),
        }
    )
    return report


def generate_daily_report(report_date: date | datetime | str | None = None) -> dict[str, Any]:
    target = _datetime(report_date, _now()) or _now()
    start = datetime.combine(target.date(), time.min, tzinfo=IST)
    return _period_report(start, start + timedelta(days=1), "daily")


def generate_weekly_report(report_date: date | datetime | str | None = None) -> dict[str, Any]:
    target = _datetime(report_date, _now()) or _now()
    start_date = target.date() - timedelta(days=target.weekday())
    start = datetime.combine(start_date, time.min, tzinfo=IST)
    return _period_report(start, start + timedelta(days=7), "weekly")


def generate_monthly_report(report_date: date | datetime | str | None = None) -> dict[str, Any]:
    target = _datetime(report_date, _now()) or _now()
    start = datetime(target.year, target.month, 1, tzinfo=IST)
    end = datetime(target.year + (target.month == 12), 1 if target.month == 12 else target.month + 1, 1, tzinfo=IST)
    return _period_report(start, end, "monthly")


def generate_performance_dashboard() -> dict[str, Any]:
    trades = _all_trades()
    dashboard = _calculate_metrics(trades)
    dashboard.update(
        {
            "period": "all_time",
            "generated_at": _now().isoformat(),
            "daily": generate_daily_report(),
            "weekly": generate_weekly_report(),
            "monthly": generate_monthly_report(),
        }
    )
    return dashboard


def get_completed_trades(limit: int | None = None) -> list[dict[str, Any]]:
    """Return defensive copies of normalized completed trades, newest first."""
    trades = [dict(item) for item in reversed(_all_trades())]
    if limit is None:
        return trades
    try:
        maximum = max(0, min(int(limit), 10_000))
    except (TypeError, ValueError, OverflowError):
        maximum = 0
    return trades[:maximum]


def _report_for_period(period: str) -> dict[str, Any]:
    value = str(period or "daily").strip().lower()
    functions = {
        "daily": generate_daily_report,
        "weekly": generate_weekly_report,
        "monthly": generate_monthly_report,
        "all": generate_performance_dashboard,
        "all_time": generate_performance_dashboard,
        "dashboard": generate_performance_dashboard,
    }
    function = functions.get(value)
    if function is None:
        raise ValueError("Period must be daily, weekly, monthly, or all")
    return function()


def export_report(
    period: str = "daily",
    file_format: str = "csv",
    path: str | Path | None = None,
) -> str | None:
    """Export a summary as CSV or JSON within approved project storage."""
    try:
        report = _report_for_period(period)
    except (ValueError, PerformanceStorageError):
        return None
    output_format = str(file_format).lower().lstrip(".")
    if output_format not in {"csv", "json"}:
        return None
    EXPORT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if path is None:
        path = EXPORT_DIRECTORY / f"performance_{period}_{_now():%Y%m%d_%H%M%S}.{output_format}"
    safe_path = validate_file_path(path, for_write=True)
    if safe_path is None:
        return None
    try:
        if output_format == "json":
            if not secure_json_write(safe_path, report, backup=False):
                return None
        else:
            temporary = safe_path.with_suffix(safe_path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("Metric", "Value"))
                for key, value in report.items():
                    if isinstance(value, (dict, list)):
                        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    writer.writerow((key, value))
                handle.flush()
            temporary.replace(safe_path)
        return str(safe_path)
    except OSError:
        LOGGER.exception("Performance export failed")
        return None


def format_telegram_report(report: Mapping[str, Any]) -> str:
    return (
        f"📊 SHIVAY AI PERFORMANCE — {str(report.get('period', 'REPORT')).upper()}\n\n"
        f"Trades: {report.get('total_trades', 0)} | BUY: {report.get('buy_trades', 0)} | SELL: {report.get('sell_trades', 0)}\n"
        f"Wins: {report.get('winning_trades', 0)} | Losses: {report.get('losing_trades', 0)}\n"
        f"Win Rate: {report.get('win_rate', 0)}% | Accuracy: {report.get('accuracy', 0)}%\n\n"
        f"Total Profit: {report.get('total_profit', 0)}\n"
        f"Total Loss: {report.get('total_loss', 0)}\n"
        f"Net Profit: {report.get('net_profit', 0)}\n"
        f"Net Return: {report.get('net_profit_percent', 0)}%\n\n"
        f"Avg Profit: {report.get('average_profit', 0)} | Avg Loss: {report.get('average_loss', 0)}\n"
        f"Avg Holding: {report.get('average_holding_time', '00:00:00')}\n"
        f"Risk:Reward: 1:{report.get('risk_reward_ratio', 0)}\n"
        f"Max Drawdown: {report.get('maximum_drawdown', 0)}\n"
        f"Best Win Streak: {report.get('consecutive_wins', 0)} | Max Loss Streak: {report.get('consecutive_losses', 0)}\n\n"
        f"Generated: {_now():%d-%b-%Y %I:%M %p IST}"
    )


async def send_performance_report(
    application: Any,
    period: str = "daily",
    chat_id: int | None = None,
    report: Mapping[str, Any] | None = None,
) -> bool:
    """Generate and deliver a Telegram-safe performance report."""
    if application is None or not hasattr(application, "bot"):
        return False
    try:
        data = dict(report) if report is not None else await asyncio.to_thread(_report_for_period, period)
        text = format_telegram_report(data)
        if chat_id is not None:
            recipients = [int(chat_id)] if int(chat_id) > 0 else []
        else:
            users = await asyncio.to_thread(recipients, "ADMIN")
            recipients = [int(user["id"]) for user in users if user.get("active", False)]
        delivered = False
        for recipient in dict.fromkeys(recipients):
            try:
                await application.bot.send_message(chat_id=recipient, text=text)
                delivered = True
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.warning("Performance report delivery failed")
        return delivered
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("Performance report generation failed")
        return False


def get_report() -> dict[str, Any]:
    """Compatibility summary matching the existing performance.py vocabulary."""
    dashboard = generate_performance_dashboard()
    return {
        "total": dashboard["total_trades"],
        "wins": dashboard["winning_trades"],
        "loss": dashboard["losing_trades"],
        "open": 0,
        "win_rate": dashboard["win_rate"],
        "total_pnl": dashboard["net_profit_percent"],
        "average_pnl": round(
            dashboard["net_profit_percent"] / dashboard["total_trades"], 2
        ) if dashboard["total_trades"] else 0.0,
        "best_trade": max((_number(item.get("pnl_percent")) for item in _all_trades()), default=0.0),
        "worst_trade": min((_number(item.get("pnl_percent")) for item in _all_trades()), default=0.0),
    }
