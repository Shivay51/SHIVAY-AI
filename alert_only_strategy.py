from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class AlertSignal:
    side: str
    score: int
    entry: float
    stop_loss: float
    target1: float
    target2: float
    target3: float
    risk: str
    hold: str
    reasons: list[str]

def _rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean(); down = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - (100 / (1 + up / down.replace(0, float('nan'))))

def _indicators(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame.copy().dropna(subset=['open', 'high', 'low', 'close'])
    for c in ['open','high','low','close','volume']: d[c] = pd.to_numeric(d[c], errors='coerce')
    d['ema20'] = d.close.ewm(span=20, adjust=False).mean(); d['ema50'] = d.close.ewm(span=50, adjust=False).mean()
    d['rsi'] = _rsi(d.close)
    fast=d.close.ewm(span=12,adjust=False).mean(); slow=d.close.ewm(span=26,adjust=False).mean(); d['macd']=fast-slow; d['macd_signal']=d.macd.ewm(span=9,adjust=False).mean()
    prev=d.close.shift(); tr=pd.concat([d.high-d.low,(d.high-prev).abs(),(d.low-prev).abs()],axis=1).max(axis=1); d['atr']=tr.ewm(alpha=1/14,adjust=False).mean()
    up=d.high.diff(); down=-d.low.diff(); plus=up.where((up>down)&(up>0),0.0); minus=down.where((down>up)&(down>0),0.0); pdi=100*plus.ewm(alpha=1/14,adjust=False).mean()/d.atr; mdi=100*minus.ewm(alpha=1/14,adjust=False).mean()/d.atr; dx=100*(pdi-mdi).abs()/(pdi+mdi).replace(0,float('nan')); d['adx']=dx.ewm(alpha=1/14,adjust=False).mean(); d['pdi']=pdi; d['mdi']=mdi
    tp=(d.high+d.low+d.close)/3; d['vwap']=(tp*d.volume).cumsum()/d.volume.cumsum().replace(0,float('nan')); d['vol_ratio']=d.volume/d.volume.rolling(20).mean()
    return d.dropna()

def build_signal(df15, df30, df60, context='NEUTRAL') -> Optional[AlertSignal]:
    a,b,c=(_indicators(x).iloc[-1] for x in (df15,df30,df60))
    bull=(a.close>a.ema20>a.ema50 and b.close>b.ema20>b.ema50 and c.close>c.ema20>c.ema50)
    bear=(a.close<a.ema20<a.ema50 and b.close<b.ema20<b.ema50 and c.close<c.ema20<c.ema50)
    if not (bull or bear): return None
    side='BUY' if bull else 'SELL'; score=60; reasons=['15m/30m/1h trend aligned']
    if a.adx>=20 and ((bull and a.pdi>a.mdi) or (bear and a.mdi>a.pdi)): score+=10; reasons.append('ADX trend strength')
    if (bull and 52<=a.rsi<=72) or (bear and 28<=a.rsi<=48): score+=10; reasons.append('RSI momentum')
    if (bull and a.macd>a.macd_signal) or (bear and a.macd<a.macd_signal): score+=8; reasons.append('MACD confirmation')
    if a.vol_ratio>=1.15: score+=7; reasons.append('volume confirmation')
    if (bull and a.close>a.vwap) or (bear and a.close<a.vwap): score+=5; reasons.append('VWAP confirmation')
    if context.upper() in (side, 'BULLISH' if bull else 'BEARISH'): score+=5; reasons.append('market context aligned')
    if score<75: return None
    entry=round(float(a.close),2); atr=float(a.atr); sl=entry-1.2*atr if bull else entry+1.2*atr; r=abs(entry-sl); targets=(entry+r,entry+1.8*r,entry+2.6*r) if bull else (entry-r,entry-1.8*r,entry-2.6*r); atr_pct=atr/entry*100; risk='SAFE' if score>=85 and atr_pct<1 else ('MODERATE' if atr_pct<2 else 'RISKY')
    return AlertSignal(side, min(score,100), entry, round(sl,2), *(round(x,2) for x in targets), risk, '25-90 MIN', reasons)

def telegram_text(script, contract, market, current, signal: AlertSignal) -> str:
    icon='🟢' if signal.side=='BUY' else '🔴'
    return f'''🔱 SHIVAY AI PRO\n━━━━━━━━━━━━━━━━\n{icon} {signal.side}\n📌 SCRIPT: {script}\n📄 CONTRACT: {contract}\n🏛 MARKET: {market}\n💰 CURRENT: ₹{current:.2f}\n🎯 ENTRY: ₹{signal.entry:.2f}\n🛑 STOP LOSS: ₹{signal.stop_loss:.2f}\n✅ TARGET 1: ₹{signal.target1:.2f}\n✅ TARGET 2: ₹{signal.target2:.2f}\n✅ TARGET 3: ₹{signal.target3:.2f}\n📊 SHIVAY SCORE: {signal.score}/100\n⚖️ RISK: {signal.risk}\n⏱ HOLD: {signal.hold}\n🧠 {', '.join(signal.reasons)}\n⚠️ Alert only — no order placed'''