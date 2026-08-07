import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from alert_only_strategy import build_signal, telegram_text
from alert_signal_tracker import SignalTracker

class AlertOnlyPipeline:
    def __init__(self, tracker=None, state_file='storage/alert_cooldown.json', cooldown_minutes=25):
        self.tracker=tracker or SignalTracker(); self.state_file=Path(state_file); self.state_file.parent.mkdir(parents=True, exist_ok=True); self.cooldown=timedelta(minutes=cooldown_minutes); self.state=self._load()
    def _load(self):
        try: return json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError): return {}
    def _save(self): self.state_file.write_text(json.dumps(self.state, indent=2))
    def _allowed(self, key, side):
        previous=self.state.get(key)
        if not previous: return True
        if previous.get('side') != side: return True
        then=datetime.fromisoformat(previous['sent_at'])
        return datetime.now(timezone.utc)-then >= self.cooldown
    def process(self, script, contract, market, current_price, candles_15m, candles_30m, candles_1h, send, context='NEUTRAL'):
        signal=build_signal(candles_15m, candles_30m, candles_1h, context)
        if signal is None: return None
        key=f'{market}:{contract}'
        if not self._allowed(key, signal.side): return None
        message=telegram_text(script, contract, market, float(current_price), signal)
        send(message)
        signal_id=self.tracker.record(market, script, contract, signal.side, signal.entry, signal.stop_loss, signal.target1, signal.target2, signal.target3, signal.score)
        self.state[key]={'side':signal.side,'sent_at':datetime.now(timezone.utc).isoformat(),'signal_id':signal_id}; self._save()
        return {'signal_id':signal_id,'signal':signal,'message':message}
