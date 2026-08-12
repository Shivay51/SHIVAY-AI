from __future__ import annotations
import asyncio,json,os,sys,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import AsyncMock,patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tradingview_cache import TradingViewCache,get_tradingview_cache
from tradingview_payload import parse_payload,PayloadError
from tradingview_security import TradingViewSecurity,SecurityError

SECRET="synthetic-test-secret-1234567890";SYMBOL="TEST:NIFTYCURRENT";MAPPING=str(ROOT/"tests"/"tv_mapping.json")
def raw(tf=15,event="one",age=0,side="BUY",price=101.0,confirmed=True):
    now=datetime.now(timezone.utc)-timedelta(seconds=age);bull=side=="BUY"
    return {"secret":SECRET,"event_id":event,"event_type":"preliminary_buy" if bull else "preliminary_sell","script_version":"test","symbol":SYMBOL,"ticker_id":SYMBOL,"exchange":"NSE","instrument":"NIFTY FUT","category":"NSE_FO","contract_text":"SYNTHETIC TEST","timeframe":tf,"open":price-.25 if bull else price+.25,"high":max(102,price+1),"low":min(99,price-1),"close":price,"volume":1200,"previous_close":100,"day_open":100,"day_high":110,"day_low":90,"previous_day_high":108,"previous_day_low":92,"ema20":100 if bull else 102,"ema50":99 if bull else 103,"ema200":98 if bull else 104,"vwap":100 if bull else 102,"rsi":60 if bull else 40,"adx":30,"plus_di":30 if bull else 15,"minus_di":15 if bull else 30,"macd":1 if bull else -1,"macd_signal":.5 if bull else -.5,"macd_histogram":.5 if bull else -.5,"atr":2,"relative_volume":1.3,"chandelier_long_stop":97,"chandelier_short_stop":105,"chandelier_direction":"BULLISH" if bull else "BEARISH","supertrend_direction":"BULLISH" if bull else "BEARISH","trend_state":"BULLISH" if bull else "BEARISH","momentum_state":"BULLISH" if bull else "BEARISH","structure_state":"BULLISH" if bull else "BEARISH","swing_high":105,"swing_low":95,"breakout_level":105,"breakdown_level":95,"signal_candle_high":101,"signal_candle_low":99,"higher_high":bull,"higher_low":bull,"lower_high":not bull,"lower_low":not bull,"bullish_breakout":bull,"bearish_breakdown":not bull,"bullish_retest":False,"bearish_retest":False,"bullish_pullback":False,"bearish_pullback":False,"chandelier_direction_changed":False,"preliminary_buy":bull,"preliminary_sell":not bull,"volume_confirmed":True,"setup_valid":True,"bar_timestamp":now.isoformat(),"generated_at":now.isoformat(),"bar_confirmed":confirmed}

class PayloadSecurityTests(unittest.TestCase):
    def setUp(self):self.env=patch.dict(os.environ,{"TRADINGVIEW_WEBHOOK_SECRET":SECRET,"TRADINGVIEW_MAX_DELAY_SECONDS":"180"});self.env.start()
    def tearDown(self):self.env.stop()
    def test_valid_payload(self):self.assertEqual(parse_payload(raw()).timeframe,15)
    def test_malformed_payload(self):
        value=raw();del value["close"]
        with self.assertRaises(PayloadError):parse_payload(value)
    def test_impossible_ohlc(self):
        value=raw();value["high"]=98
        with self.assertRaises(PayloadError):parse_payload(value)
    def test_authentication(self):TradingViewSecurity().authenticate(SECRET)
    def test_bad_authentication(self):
        with self.assertRaises(SecurityError):TradingViewSecurity().authenticate("wrong")
    def test_replay_and_duplicate(self):
        security=TradingViewSecurity();payload=parse_payload(raw());security.validate(payload,{SYMBOL},{15})
        with self.assertRaises(SecurityError):security.validate(payload,{SYMBOL},{15})
    def test_symbol_allowlist(self):
        with self.assertRaises(SecurityError):TradingViewSecurity().validate(parse_payload(raw()),{"OTHER"},{15})
    def test_timeframe_allowlist(self):
        with self.assertRaises(SecurityError):TradingViewSecurity().validate(parse_payload(raw()),{SYMBOL},{5})
    def test_stale_payload(self):
        with self.assertRaises(SecurityError):TradingViewSecurity().validate(parse_payload(raw(age=400)),{SYMBOL},{15})
    def test_future_payload(self):
        with self.assertRaises(SecurityError):TradingViewSecurity().validate(parse_payload(raw(age=-90)),{SYMBOL},{15})
    def test_unconfirmed_bar_rejected(self):
        with self.assertRaises(PayloadError):parse_payload(raw(confirmed=False))

class CacheTests(unittest.TestCase):
    def test_alignment(self):
        cache=TradingViewCache()
        for tf in (5,15,30,60):cache.put(parse_payload(raw(tf,event=str(tf))))
        self.assertEqual(set(cache.aligned(SYMBOL)),{5,15,30,60})
    def test_missing_timeframe_waits(self):
        cache=TradingViewCache();cache.put(parse_payload(raw(15)))
        self.assertIsNone(cache.aligned(SYMBOL))
    def test_same_bar_is_replaced(self):
        cache=TradingViewCache();value=raw();first=parse_payload(value);cache.put(first);value["event_id"]="two";value["close"]=101.5;cache.put(parse_payload(value));self.assertEqual(len(cache.bars(SYMBOL,15)),1)
    def test_contracts_never_mix(self):
        cache=TradingViewCache();first=raw(event="contract-a");second=raw(event="contract-b");second["contract_text"]="OTHER CONTRACT";cache.put(parse_payload(first));cache.put(parse_payload(second));self.assertIsNone(cache.latest(SYMBOL,15));self.assertIsNone(cache.aligned(SYMBOL))

class ReceiverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ,{"TRADINGVIEW_WEBHOOK_ENABLED":"true","TRADINGVIEW_WEBHOOK_SECRET":SECRET,"TRADINGVIEW_SYMBOL_MAP_FILE":MAPPING,"TRADINGVIEW_ALLOWED_SYMBOLS":SYMBOL,"TRADINGVIEW_ALLOWED_TIMEFRAMES":"5,15,30,60","TRADINGVIEW_WEBHOOK_HOST":"127.0.0.1","TRADINGVIEW_WEBHOOK_PORT":"18765","TRADINGVIEW_REPLAY_STORE":""});self.env.start()
        import tradingview_webhook as hook;await hook.stop_tradingview_webhook();self.hook=hook;self.assertTrue(await hook.start_tradingview_webhook(object()))
    async def asyncTearDown(self):await self.hook.stop_tradingview_webhook();self.env.stop()
    async def send(self,value,content_type="application/json"):
        reader,writer=await asyncio.open_connection("127.0.0.1",18765);body=json.dumps(value).encode();writer.write(f"POST /tradingview-webhook HTTP/1.1\r\nHost: local\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\n\r\n".encode()+body);await writer.drain();reply=await reader.read();writer.close();await writer.wait_closed();return int(reply.split(b" ",2)[1])
    async def test_invalid_json(self):
        reader,writer=await asyncio.open_connection("127.0.0.1",18765);body=b"{";writer.write(f"POST /tradingview-webhook HTTP/1.1\r\nHost: local\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()+body);await writer.drain();reply=await reader.read();writer.close();await writer.wait_closed();self.assertIn(b"400 Bad Request",reply)
    async def test_health(self):
        reader,writer=await asyncio.open_connection("127.0.0.1",18765);writer.write(b"GET /health HTTP/1.1\r\nHost: local\r\n\r\n");await writer.drain();reply=await reader.read();writer.close();await writer.wait_closed();self.assertIn(b"200 OK",reply);self.assertIn(b'"service":"shivay-ai-webhook"',reply);self.assertNotIn(SECRET.encode(),reply)
    async def test_accept_and_duplicate_reject(self):
        value=raw(event="http-unique");self.assertEqual(await self.send(value),202);self.assertEqual(await self.send(value),401)
    async def test_all_supported_timeframes_return_202(self):
        for tf in (5,15,30,60):
            value=raw(tf,event=f"accepted-{tf}");value.update(event_type="timeframe_update",preliminary_buy=False,preliminary_sell=False,setup_valid=False)
            self.assertEqual(await self.send(value),202)
    async def test_json_only(self):self.assertEqual(await self.send(raw(event="plain"),"text/plain"),415)
    async def test_wrong_secret(self):
        value=raw(event="bad-secret");value["secret"]="bad";self.assertEqual(await self.send(value),401)
    async def test_unknown_symbol(self):
        value=raw(event="unknown");value["symbol"]=value["ticker_id"]="TEST:OTHER";self.assertEqual(await self.send(value),401)
    async def test_instrument_mismatch(self):
        value=raw(event="mismatch");value["instrument"]="BANKNIFTY FUT";self.assertEqual(await self.send(value),401)
    async def test_category_rejection(self):
        value=raw(event="bad-category");value["category"]="MCX";self.assertEqual(await self.send(value),401)
    async def test_duplicate_candle_with_new_event_id(self):
        value=raw(event="candle-original");self.assertEqual(await self.send(value),202);value["event_id"]="candle-copy";self.assertEqual(await self.send(value),401)
    async def test_standard_alert_payload_returns_202(self):
        now=datetime.now(timezone.utc)
        value={"secret":SECRET,"source":"TRADINGVIEW_STANDARD_ALERT","event_id":"standard-http-unique","symbol":SYMBOL,"exchange":"NSE","category":"NSE_FO","contract":"SYNTHETIC TEST","timeframe":15,"open":100,"high":102,"low":99,"close":101,"volume":1200,"bar_time":now.isoformat()}
        self.assertEqual(await self.send(value),202)

class DecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env=patch.dict(os.environ,{"TRADINGVIEW_WEBHOOK_ENABLED":"true","TRADINGVIEW_WEBHOOK_SECRET":SECRET,"TRADINGVIEW_SYMBOL_MAP_FILE":MAPPING});self.env.start();cache=get_tradingview_cache();cache._bars.clear();cache._latest.clear()
    async def asyncTearDown(self):self.env.stop()
    async def decision(self,side="BUY",strong=True,missed=False):
        cache=get_tradingview_cache()
        for tf in (5,15,30,60):
            value=raw(tf,event=f"decision-{side}-{tf}",side=side,price=(104 if missed and tf==5 and side=="BUY" else 96 if missed and tf==5 else 102 if side=="BUY" else 98));value["adx"]=30 if strong else 22;value["relative_volume"]=1.3 if strong else .9;cache.put(parse_payload(value))
            bank=raw(tf,event=f"decision-bank-{side}-{tf}",side=side,price=102 if side=="BUY" else 98);bank.update(symbol="TEST:BANKNIFTYCURRENT",ticker_id="TEST:BANKNIFTYCURRENT",instrument="BANKNIFTY FUT");cache.put(parse_payload(bank))
        import tradingview_bridge as bridge
        with patch("signal_memory.signal_exists",return_value=False),patch("signal_memory.add_signal"),patch("trade_monitor.add_trade"),patch("telegram_service.send_buy_signal",new=AsyncMock(return_value=True)),patch("telegram_service.send_sell_signal",new=AsyncMock(return_value=True)):
            return await bridge.process_accepted_payload(object(),parse_payload(raw(5,event="trigger",side=side,price=cache.latest(SYMBOL,5)["close"])))
    async def test_strong_buy(self):self.assertEqual((await self.decision("BUY",True))["decision"],"WAIT")
    async def test_buy(self):self.assertEqual((await self.decision("BUY",False))["decision"],"WAIT")
    async def test_strong_sell(self):self.assertEqual((await self.decision("SELL",True))["decision"],"WAIT")
    async def test_sell(self):self.assertEqual((await self.decision("SELL",False))["decision"],"WAIT")
    async def test_entry_missed_wait(self):self.assertEqual((await self.decision("BUY",True,True))["decision"],"WAIT")
    async def test_safe_test_event_never_trades(self):
        import tradingview_bridge as bridge
        value=raw(event="safe-test");value.update(event_type="test",preliminary_buy=False,preliminary_sell=False,setup_valid=False)
        result=await bridge.process_accepted_payload(object(),parse_payload(value));self.assertEqual((result["decision"],result["reason"]),("WAIT","safe_test_event"))

class ContractRiskLifecycleTests(unittest.TestCase):
    def setUp(self):self.env=patch.dict(os.environ,{"TRADINGVIEW_WEBHOOK_ENABLED":"true","TRADINGVIEW_WEBHOOK_SECRET":SECRET,"TRADINGVIEW_SYMBOL_MAP_FILE":MAPPING});self.env.start()
    def tearDown(self):self.env.stop()
    def test_exact_banknifty_contract(self):
        from tradingview_bridge import TradingViewBridgeProvider
        _,item=TradingViewBridgeProvider()._mapping("BANKNIFTY FUT");self.assertEqual(item["instrument_type"],"FUTIDX")
    def test_exact_gold_is_mcx(self):
        from tradingview_bridge import TradingViewBridgeProvider
        _,item=TradingViewBridgeProvider()._mapping("MCX GOLD");self.assertEqual((item["exchange"],item["instrument_type"]),("MCX","FUTCOM"));self.assertNotEqual(item["trading_symbol"],"GC=F")
    def test_exact_silver_is_mcx(self):
        from tradingview_bridge import TradingViewBridgeProvider
        _,item=TradingViewBridgeProvider()._mapping("MCX SILVER");self.assertEqual(item["segment"],"MCX_COMM");self.assertNotEqual(item["trading_symbol"],"SI=F")
    def test_exact_stock_future(self):
        from tradingview_bridge import TradingViewBridgeProvider
        _,item=TradingViewBridgeProvider()._mapping("TEST STOCK FUT");self.assertEqual((item["exchange"],item["instrument_type"]),("NSE","FUTSTK"))
    def test_gift_and_comex_are_context_only(self):
        from tradingview_bridge import TradingViewBridgeProvider
        provider=TradingViewBridgeProvider()
        for symbol in ("GIFT NIFTY","COMEX GOLD","COMEX SILVER"):
            with self.assertRaises(Exception):provider._mapping(symbol)
    def test_nonzero_buy_levels(self):
        from tradeplan import create_trade_plan
        value=create_trade_plan(100,2,"BUY",{"support":97,"resistance":105,"ema20":99,"vwap":99.5,"chandelier_15m":{"long_stop":97.5}});self.assertTrue(all(float(value[x])>0 for x in ("entry","sl","target1","target2","target3")));self.assertTrue(value["sl"]<value["entry"]<value["target1"]<value["target2"]<value["target3"])
    def test_nonzero_sell_levels(self):
        from tradeplan import create_trade_plan
        value=create_trade_plan(100,2,"SELL",{"support":95,"resistance":103,"ema20":101,"vwap":101.5,"chandelier_15m":{"short_stop":103}});self.assertTrue(value["sl"]>value["entry"]>value["target1"]>value["target2"]>value["target3"]>0)
    def test_chandelier_stops_never_loosen(self):
        from chandelier_exit import update_chandelier_trailing_stop
        self.assertEqual(update_chandelier_trailing_stop("BUY",95,94),95);self.assertEqual(update_chandelier_trailing_stop("SELL",105,106),105)
    def test_targets_and_stop(self):
        from trade_monitor import check_target,check_stoploss
        buy={"side":"BUY","sl":95,"target1":105,"target2":110,"target3":115,"t1_hit":False,"t2_hit":False,"t3_hit":False};self.assertTrue(check_stoploss(buy,95));self.assertEqual(check_target(buy,105),"TARGET1")
        sell={"side":"SELL","sl":105,"target1":95,"target2":90,"target3":85,"t1_hit":False,"t2_hit":False,"t3_hit":False};self.assertTrue(check_stoploss(sell,105));self.assertEqual(check_target(sell,95),"TARGET1")
    def test_signal_expiry(self):
        from trade_monitor import add_trade,expire_trade,remove_trade
        trade={"symbol":"EXPIRY TEST FUT","decision":"BUY","entry":100,"sl":98,"target1":103,"target2":105,"target3":108,"requires_entry_confirmation":True};add_trade(trade);result=expire_trade(trade["symbol"]);self.assertTrue(result["cancelled"]);remove_trade(trade["symbol"])
    def test_prediction_requires_history(self):
        from tradingview_bridge import market_outlook
        self.assertEqual(market_outlook("BANKNIFTY FUT")["status"],"UNAVAILABLE")
    def _prediction_ready(self,tv_symbol,instrument):
        cache=get_tradingview_cache();cache._bars.clear();cache._latest.clear();now=datetime.now(timezone.utc)
        for index in range(20):
            value=raw(60,event=f"history-{instrument}-{index}",price=101+index*.1);value.update(symbol=tv_symbol,ticker_id=tv_symbol,instrument=instrument,bar_timestamp=(now-timedelta(minutes=20-index)).isoformat(),generated_at=(now-timedelta(minutes=20-index)).isoformat());cache.put(parse_payload(value))
        value=raw(30,event=f"confirm-{instrument}");value.update(symbol=tv_symbol,ticker_id=tv_symbol,instrument=instrument);cache.put(parse_payload(value))
        from tradingview_bridge import market_outlook
        return market_outlook(instrument)
    def test_nifty_prediction(self):self.assertEqual(self._prediction_ready(SYMBOL,"NIFTY FUT")["status"],"READY")
    def test_banknifty_prediction(self):self.assertEqual(self._prediction_ready("TEST:BANKNIFTYCURRENT","BANKNIFTY FUT")["status"],"READY")
    def test_telegram_signal_is_compact_and_private(self):
        from telegram_service import _signal_text
        text=_signal_text({"market_category":"MCX","symbol":"MCX GOLD","trading_symbol":"GOLDV2026","price":100,"entry":100,"entry_zone":[99.5,100.5],"sl":98,"target1":103,"target2":105,"target3":108,"score":84,"risk_level":"MEDIUM","trend":"STRONG BULLISH","signal_candle_high":101,"signal_candle_low":97.5,"confirmation_candle":{"timestamp":datetime.now(timezone.utc)},"volume":1200,"open_interest":5000,"risk_reward":2.5,"provider":"tradingview_alert_bridge","reasons":["Chandelier BUY","Next candle confirmed","Structure supports"]},"BUY")
        for expected in ("🔱 SHIVAY AI PRO","MARKET: MCX","MCX GOLD","CONTRACT: GOLDV2026","DIRECTION: 🟢 BUY","CURRENT PRICE","ENTRY ZONE","STOP LOSS","TARGET 1","TARGET 2","TARGET 3","84/100","MODERATE","CHANDELIER SIGNAL","SIGNAL CANDLE HIGH","SIGNAL CANDLE LOW","NEXT CANDLE + APPROX. 1 MIN CONFIRMED ✅","BULLISH","VOLUME","OI","RISK : REWARD","EXPECTED HOLD","TOP REASONS"):self.assertIn(expected,text)
        self.assertNotIn("SOURCE:",text.upper());self.assertNotIn("PROVIDER:",text.upper())
        self.assertNotIn("tradingview",text.lower());self.assertNotIn("₹0.00",text);self.assertFalse(text.startswith("??"))
    def test_simulated_sell_formatter_has_all_safe_fields(self):
        from telegram_service import _signal_text
        text=_signal_text({"market_category":"NSE F&O","symbol":"NIFTY","trading_symbol":"NIFTY26AUGFUT","price":24700,"entry":24695,"entry_zone":[24690,24700],"sl":24750,"target1":24620,"target2":24550,"target3":24480,"score":81,"risk_level":"LOW","trend":"BEARISH","signal_candle_high":24750,"signal_candle_low":24680,"confirmation_candle":{"timestamp":datetime.now(timezone.utc)},"volume":150000,"open_interest":480000,"risk_reward":2.6,"provider":"hidden","source":"hidden","reasons":["Chandelier SELL","Next candle confirmed","Risk checks passed"]},"SELL")
        for expected in ("🔱 SHIVAY AI PRO","SCRIPT: NIFTY","CONTRACT: NIFTY26AUGFUT","DIRECTION: 🔴 SELL","CURRENT PRICE:","ENTRY ZONE:","STOP LOSS:","TARGET 1:","TARGET 2:","TARGET 3:","81/100","SAFE","CHANDELIER SIGNAL:\nSELL","NEXT CANDLE + APPROX. 1 MIN CONFIRMED ✅"):
            self.assertIn(expected,text)
        for forbidden in ("SOURCE:","PROVIDER:","HIDDEN","??","₹0.00"):
            self.assertNotIn(forbidden,text.upper())

    def test_provider_priority(self):
        from provider_manager import ProviderManager
        self.assertEqual(ProviderManager.DEFAULT_PRIORITY,("angelone_primary","tradingview_alert_bridge"))
        self.assertEqual(ProviderManager.DEFAULT_PRIORITY[-1],"tradingview_alert_bridge")
        self.assertIn("yahoo_emergency",ProviderManager.ARCHIVED_PROVIDERS)
    def test_no_order_surface(self):
        from tradingview_bridge import TradingViewBridgeProvider
        provider=TradingViewBridgeProvider();self.assertFalse(any(hasattr(provider,x) for x in ("place_order","modify_order","cancel_order")))

class UnicodeTransmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_shivay_unicode_survives_transmission_preparation(self):
        import telegram_service
        captured=[]
        class Bot:
            async def send_message(self,chat_id,text):captured.append(text)
        class App:bot=Bot()
        with patch("telegram_service.recipients",return_value=[{"id":1}]),patch("telegram_service._admin_ids",return_value={1}):
            sent=await telegram_service._send(App(),("UNICODE",id(self)),"🔱 SHIVAY AI PRO\n\nWAIT")
        self.assertTrue(sent);self.assertEqual(captured,["🔱 SHIVAY AI PRO\n\nWAIT"]);self.assertFalse(captured[0].startswith("??"))

if __name__=="__main__":unittest.main()
