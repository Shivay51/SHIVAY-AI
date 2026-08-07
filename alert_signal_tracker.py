from pathlib import Path
import sqlite3
from datetime import datetime, timezone

class SignalTracker:
    def __init__(self, database='storage/alert_signals.sqlite3'):
        self.path = Path(database); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS alert_signals (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, market TEXT NOT NULL, script TEXT NOT NULL, contract TEXT NOT NULL, side TEXT NOT NULL, entry REAL NOT NULL, stop_loss REAL NOT NULL, target1 REAL NOT NULL, target2 REAL NOT NULL, target3 REAL NOT NULL, score INTEGER NOT NULL, outcome TEXT, closed_at TEXT)''')
    def _db(self): return sqlite3.connect(self.path)
    def record(self, market, script, contract, side, entry, stop_loss, target1, target2, target3, score):
        with self._db() as db:
            cur=db.execute('INSERT INTO alert_signals (created_at,market,script,contract,side,entry,stop_loss,target1,target2,target3,score) VALUES (?,?,?,?,?,?,?,?,?,?,?)',(datetime.now(timezone.utc).isoformat(),market,script,contract,side,entry,stop_loss,target1,target2,target3,score)); return cur.lastrowid
    def close(self, signal_id, outcome):
        if outcome not in {'SL','T1','T2','T3','EXPIRED','CANCELLED'}: raise ValueError('invalid outcome')
        with self._db() as db: db.execute('UPDATE alert_signals SET outcome=?, closed_at=? WHERE id=?',(outcome,datetime.now(timezone.utc).isoformat(),signal_id))
    def report(self, market=None):
        query='SELECT COUNT(*), SUM(CASE WHEN outcome IN (\'T1\',\'T2\',\'T3\') THEN 1 ELSE 0 END), SUM(CASE WHEN outcome=\'SL\' THEN 1 ELSE 0 END) FROM alert_signals WHERE outcome IS NOT NULL'; args=()
        if market: query+=' AND market=?'; args=(market,)
        with self._db() as db: total,wins,stops=db.execute(query,args).fetchone()
        total=total or 0; wins=wins or 0; stops=stops or 0
        return {'closed':total,'wins':wins,'stop_losses':stops,'win_rate':round(wins*100/total,2) if total else None}
