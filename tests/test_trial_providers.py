from __future__ import annotations
import os,sys,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from gdfl_provider import GDFLProvider

class FakeGDFL:
    configured=True
    def search_instruments(self,search,exchange,instrument_type):
        return [{"InstrumentIdentifier":search+"-CURRENT","Expiry":(datetime.now(timezone.utc)+timedelta(days=12)).isoformat(),"LotSize":25,"TickSize":0.05,"SecurityId":"101"}]
    def get_history(self,exchange,identifier,period,bars):
        now=datetime.now(timezone.utc)
        return [{"Timestamp":(now-timedelta(minutes=(6-i)*period)).isoformat(),"Open":100+i,"High":102+i,"Low":99+i,"Close":101+i,"Volume":1000+i} for i in range(6)]
    def get_quote(self,exchange,identifier):
        return {"LTP":107,"LastTradeTime":datetime.now(timezone.utc).isoformat()}
    def health(self,probe=False):return {"configured":True}
    def close(self):pass

class TrialProviderTests(unittest.TestCase):
    def test_exact_nse_future(self):
        value=GDFLProvider(FakeGDFL()).get_market_data("NIFTY FUT")
        self.assertTrue(value["verified"]);self.assertEqual(value["exchange"],"NSE");self.assertEqual(value["instrument_type"],"FUTIDX")
    def test_exact_mcx_not_comex(self):
        value=GDFLProvider(FakeGDFL()).get_market_data("MCX GOLD")
        self.assertEqual(value["exchange"],"MCX");self.assertEqual(value["instrument_type"],"FUTCOM");self.assertNotIn(value["trading_symbol"],{"GC=F","SI=F"})
    def test_no_order_surface(self):
        provider=GDFLProvider(FakeGDFL())
        self.assertFalse(any(hasattr(provider,name) for name in ("place_order","modify_order","cancel_order")))

if __name__=="__main__":unittest.main()
