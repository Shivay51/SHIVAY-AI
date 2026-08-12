from __future__ import annotations
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import AsyncMock,patch

class ReleaseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_recovery_is_silent(self):
        import auto_recovery,config
        application=type("App",(),{})();application.bot=type("Bot",(),{"send_message":AsyncMock()})()
        with patch.object(config,"ENABLE_RECOVERY_ALERTS",False):await auto_recovery._notify_admins(application,"provider","recovered")
        application.bot.send_message.assert_not_awaited()

class ReleaseSynchronousTests(unittest.TestCase):
    def test_trade_journal_round_trip(self):
        import trade_journal
        path=Path(__file__).resolve().parent/".temporary_journal.jsonl"
        try:
            path.unlink(missing_ok=True)
            with patch.object(trade_journal,"JOURNAL",path):
                event=trade_journal.record_signal({"symbol":"TEST FUT","decision":"BUY","entry":100,"sl":98,"target1":103,"target2":105,"target3":108})
                self.assertTrue(event);rows=trade_journal.load_events();self.assertEqual((len(rows),rows[0]["event_type"]),(1,"SIGNAL"))
        finally:path.unlink(missing_ok=True)
    def test_daily_review_does_not_change_strategy(self):
        import daily_review
        today=datetime.now(timezone.utc).date();rows=[{"timestamp":datetime.now(timezone.utc).isoformat(),"event_type":"OUTCOME","symbol":"TEST FUT","side":"BUY","pnl":5,"result":"WIN"}]
        with patch.object(daily_review,"load_events",return_value=rows):value=daily_review.generate_daily_review(today)
        self.assertFalse(value["strategy_changed"]);self.assertTrue(value["statistical_evidence_required"])
    def test_user_routing(self):
        from audience_router import normalize_audience,user_allows
        self.assertEqual(normalize_audience("NSE F&O"),"NSE_FO");self.assertTrue(user_allows({"plan":"MCX"},"MCX"));self.assertFalse(user_allows({"plan":"NSE_FO"},"ADMIN"))
    def test_outlook_format_is_private(self):
        from tradingview_bridge import format_market_outlook
        value={"status":"READY","symbol":"NIFTY FUT","bias":"POSITIVE FOR TOMORROW","open_range":(100,101),"high_range":(103,104),"low_range":(97,98),"bullish_above":102,"bearish_below":99,"support":98,"support2":97,"resistance":103,"resistance2":104,"plan":"BUY ON DIP","risk":"MEDIUM"};text=format_market_outlook(value)
        self.assertIn("POSITIVE FOR TOMORROW",text);self.assertNotIn("provider",text.lower());self.assertNotIn("tradingview",text.lower())
    def test_pine_payload_schema_keys(self):
        from tradingview_payload import NUMBERS,BOOLEANS
        text=(Path(__file__).resolve().parents[1]/"SHIVAY_AI_TV_BRIDGE.pine").read_text(encoding="utf-8")
        required={"event_id","event_type","script_version","symbol","ticker_id","exchange","instrument","category","contract_text","timeframe",*NUMBERS,*BOOLEANS,"bar_timestamp","generated_at","bar_confirmed"}
        self.assertFalse([key for key in required if f'\\"{key}\\"' not in text])

if __name__=="__main__":unittest.main()
