from confidence_engine import get_risk_score, is_trade_allowed as is_confidence_trade_allowed
from market_prediction import get_market_prediction
from market_sentiment import can_trade_by_sentiment
from market_strength import get_market_strength, is_market_tradeable
from config import MIN_RISK_REWARD


_risk_state = {
    "daily_risk": 0.0,
    "drawdown": 0.0,
    "max_loss": 0.0,
}


def calculate_account_risk(
    account_balance,
    daily_pnl=0.0,
    weekly_pnl=0.0,
    monthly_pnl=0.0,
    consecutive_losses=0,
    consecutive_wins=0,
    peak_balance=None,
):
    global _risk_state

    account_balance = max(float(account_balance), 0.0)
    peak_balance = max(float(peak_balance or account_balance), account_balance)
    daily_loss_limit = account_balance * 0.02
    weekly_loss_limit = account_balance * 0.06
    monthly_loss_limit = account_balance * 0.10
    drawdown = max(0.0, ((peak_balance - account_balance) / peak_balance) * 100)
    daily_risk = abs(min(float(daily_pnl), 0.0)) / account_balance * 100 if account_balance > 0 else 100.0
    weekly_risk = abs(min(float(weekly_pnl), 0.0)) / account_balance * 100 if account_balance > 0 else 100.0
    monthly_risk = abs(min(float(monthly_pnl), 0.0)) / account_balance * 100 if account_balance > 0 else 100.0

    trading_blocked = (
        account_balance <= 0
        or daily_risk >= 2.0
        or weekly_risk >= 6.0
        or monthly_risk >= 10.0
        or drawdown >= 12.0
        or int(consecutive_losses) >= 3
    )

    risk_multiplier = 1.0

    if int(consecutive_losses) >= 2:
        risk_multiplier *= 0.50
    elif int(consecutive_losses) == 1:
        risk_multiplier *= 0.75

    if drawdown >= 8.0:
        risk_multiplier *= 0.50
    elif drawdown >= 5.0:
        risk_multiplier *= 0.75

    if int(consecutive_wins) >= 3:
        risk_multiplier = min(risk_multiplier, 1.0)

    _risk_state = {
        "daily_risk": round(daily_risk, 2),
        "drawdown": round(drawdown, 2),
        "max_loss": round(daily_loss_limit, 2),
    }

    return {
        "daily_loss_limit": round(daily_loss_limit, 2),
        "weekly_loss_limit": round(weekly_loss_limit, 2),
        "monthly_loss_limit": round(monthly_loss_limit, 2),
        "daily_risk": round(daily_risk, 2),
        "weekly_risk": round(weekly_risk, 2),
        "monthly_risk": round(monthly_risk, 2),
        "drawdown": round(drawdown, 2),
        "risk_multiplier": risk_multiplier,
        "trading_blocked": trading_blocked,
    }


def calculate_trade_risk(entry_price, stop_loss, target_price, side="BUY"):
    entry_price = float(entry_price)
    stop_loss = float(stop_loss)
    target_price = float(target_price)

    if entry_price <= 0 or stop_loss <= 0 or target_price <= 0:
        return {
            "risk_per_unit": 0.0,
            "reward_per_unit": 0.0,
            "risk_reward_ratio": 0.0,
            "valid": False,
        }

    if side == "BUY":
        risk_per_unit = entry_price - stop_loss
        reward_per_unit = target_price - entry_price
    else:
        risk_per_unit = stop_loss - entry_price
        reward_per_unit = entry_price - target_price

    risk_per_unit = max(0.0, risk_per_unit)
    reward_per_unit = max(0.0, reward_per_unit)
    risk_reward_ratio = reward_per_unit / risk_per_unit if risk_per_unit > 0 else 0.0

    return {
        "risk_per_unit": round(risk_per_unit, 2),
        "reward_per_unit": round(reward_per_unit, 2),
        "risk_reward_ratio": round(risk_reward_ratio, 2),
        "valid": risk_reward_ratio >= float(MIN_RISK_REWARD),
    }


def calculate_position_size(
    account_balance,
    entry_price,
    stop_loss,
    target_price,
    lot_size=1,
    risk_percent=1.0,
    side="BUY",
    account_risk=None,
):
    account_balance = max(float(account_balance), 0.0)
    lot_size = max(int(lot_size), 1)
    risk_percent = min(max(float(risk_percent), 0.0), 1.0)
    trade_risk = calculate_trade_risk(
        entry_price,
        stop_loss,
        target_price,
        side,
    )

    if account_risk is None:
        account_risk = calculate_account_risk(account_balance)

    prediction = get_market_prediction()
    volatility = str(prediction.get("market_risk", "HIGH"))
    market_strength = get_market_strength()
    confidence_risk = get_risk_score()

    risk_multiplier = float(account_risk.get("risk_multiplier", 0.0))

    if volatility == "HIGH" or confidence_risk > 30:
        risk_multiplier *= 0.50
    elif volatility == "MODERATE":
        risk_multiplier *= 0.75

    if market_strength < 70:
        risk_multiplier *= 0.50

    max_risk_amount = account_balance * (risk_percent / 100.0) * risk_multiplier
    risk_per_unit = float(trade_risk["risk_per_unit"])
    raw_quantity = int(max_risk_amount / risk_per_unit) if risk_per_unit > 0 else 0
    maximum_quantity = (raw_quantity // lot_size) * lot_size
    capital_allocation = maximum_quantity * float(entry_price)

    if (
        account_risk.get("trading_blocked", True)
        or not trade_risk["valid"]
        or maximum_quantity < lot_size
    ):
        maximum_quantity = 0
        capital_allocation = 0.0

    return {
        "position_size": maximum_quantity,
        "maximum_quantity": maximum_quantity,
        "capital_allocation": round(capital_allocation, 2),
        "maximum_loss": round(maximum_quantity * risk_per_unit, 2),
        "risk_reward_ratio": trade_risk["risk_reward_ratio"],
        "recommended_stop_loss": round(float(stop_loss), 2),
    }


def get_daily_risk():
    return _risk_state["daily_risk"]


def get_drawdown():
    return _risk_state["drawdown"]


def get_max_loss():
    return _risk_state["max_loss"]


def is_risk_acceptable(account_risk, trade_risk):
    return (
        not bool(account_risk.get("trading_blocked", True))
        and float(trade_risk.get("risk_reward_ratio", 0.0)) >= float(MIN_RISK_REWARD)
        and get_risk_score() <= 30
        and get_market_strength() >= 70
    )


def can_take_trade(account_risk, trade_risk):
    return (
        is_risk_acceptable(account_risk, trade_risk)
        and is_confidence_trade_allowed()
        and is_market_tradeable()
        and can_trade_by_sentiment()
    )
