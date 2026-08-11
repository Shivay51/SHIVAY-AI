from pathlib import Path

path = Path(__file__).with_name("morning_prediction.py")
text = path.read_text(encoding="utf-8")
old = '''    nifty = _previous_session("NIFTY FUT", now.date())
    banknifty = _previous_session("BANKNIFTY FUT", now.date())'''
new = '''    nifty = _previous_session("NIFTY", now.date())
    banknifty = _previous_session("BANKNIFTY", now.date())'''
if old not in text:
    raise SystemExit("Expected lines were not found; no changes made.")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("DONE: updated NIFTY and BANKNIFTY previous-session symbols.")
