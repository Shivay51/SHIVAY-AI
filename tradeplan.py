# ==========================================
# SHIVAY AI PRO v3
# Trade Plan Generator
# ==========================================

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core.risk_engine import calculate_risk
import config


def create_trade_plan(price, atr_value, decision, score_data=None):

    if "BUY" in decision:

        plan = calculate_risk(
            price,
            atr_value,
            "BUY",
        )

    elif "SELL" in decision:

        plan = calculate_risk(
            price,
            atr_value,
            "SELL",
        )

    else:
        return {
        "entry": None,
        "sl": None,
        "target1": None,
        "target2": None,
        "target3": None,
        }

    score_data = score_data or {}
    side = "BUY" if "BUY" in decision else "SELL"
    support = float(score_data.get("support", 0) or 0)
    resistance = float(score_data.get("resistance", 0) or 0)
    ema20 = float(score_data.get("ema20", 0) or 0)
    vwap = float(score_data.get("vwap", 0) or 0)
    chandelier_15m = score_data.get("chandelier_15m") if isinstance(score_data.get("chandelier_15m"), dict) else {}
    entry = float(plan["entry"])
    atr_value = max(float(atr_value), 0.01)
    if side == "BUY":
        chandelier_stop = float(chandelier_15m.get("long_stop") or 0)
        structures = [level for level in (support, ema20, vwap, chandelier_stop) if 0 < level < entry]
        if structures: plan["sl"] = round(max(max(structures) - atr_value * 0.12, entry - atr_value * 1.65), 2)
        plan["sl"] = round(min(float(plan["sl"]), entry - atr_value * 0.75), 2)
        plan["sl"] = round(max(float(plan["sl"]), entry - atr_value * 1.75), 2)
    elif side == "SELL":
        chandelier_stop = float(chandelier_15m.get("short_stop") or 0)
        structures = [level for level in (resistance, ema20, vwap, chandelier_stop) if level > entry]
        if structures: plan["sl"] = round(min(min(structures) + atr_value * 0.12, entry + atr_value * 1.65), 2)
        plan["sl"] = round(max(float(plan["sl"]), entry + atr_value * 0.75), 2)
        plan["sl"] = round(min(float(plan["sl"]), entry + atr_value * 1.75), 2)
    risk = abs(entry - float(plan["sl"]))
    if risk <= 0:
        return {"entry": None, "sl": None, "target1": None, "target2": None, "target3": None}
    multipliers = (1.5, 2.25, 3.25)
    direction = 1 if side == "BUY" else -1
    plan["target1"], plan["target2"], plan["target3"] = (
        round(entry + direction * risk * multiple, 2) for multiple in multipliers
    )
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    plan.update(
        entry_zone=(round(entry - atr_value * 0.12, 2), round(entry + atr_value * 0.12, 2)),
        risk_reward=round(abs(plan["target2"] - entry) / risk, 2),
        valid_until=(now + timedelta(minutes=max(5, int(getattr(config, "ENTRY_VALIDITY_MINUTES", 18))))).isoformat(),
        invalidation_condition=f"Price closes beyond {plan['sl']:.2f} or setup loses freshness",
    )
    return plan
