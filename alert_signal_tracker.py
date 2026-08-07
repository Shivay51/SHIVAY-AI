from pathlib import Path
import sqlite3
from datetime import datetime, timezone
class SignalTracker:
 def __init__(self,path='storage/alert_signals.sqlite3'):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
  with self.db() as c:c.execute('CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY,created TEXT,market TEXT,symbol TEXT,side TEXT,entry REAL,sl REAL,t1 REAL,t2 REAL,t3 REAL,score INTEGER,outcome TEXT)')
 def db(self):return sqlite3.connect(self.path)
 def record(self,market,symbol,contract,side,entry,sl,t1,t2,t3,score):
  with self.db() as c:
   q='INSERT INTO alerts(created,market,symbol,side,entry,sl,t1,t2,t3,score) VALUES(?,?,?,?,?,?,?,?,?,?)';return c.execute(q,(datetime.now(timezone.utc).isoformat(),market,f'{symbol}:{contract}',side,entry,sl,t1,t2,t3,score)).lastrowid
 def close(self,id,outcome):
  if outcome not in {'SL','T1','T2','T3','EXPIRED','CANCELLED'}:raise ValueError('invalid outcome')
  with self.db() as c:c.execute('UPDATE alerts SET outcome=? WHERE id=?',(outcome,id))
 def report(self):
  with self.db() as c:
   total,wins=c.execute("SELECT COUNT(*),SUM(outcome IN ('T1','T2','T3')) FROM alerts WHERE outcome IS NOT NULL").fetchone()
  return {'closed':total or 0,'wins':wins or 0,'win_rate':round(100*(wins or 0)/total,2) if total else None}
