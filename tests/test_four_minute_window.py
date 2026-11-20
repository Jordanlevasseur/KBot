import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock,patch
from kalshi_bot.strategy import ENTRY_WINDOW_SECONDS,entry_open
from kalshi_bot.trader import cycle
from kalshi_bot.execution import Journal,order_payload
from kalshi_bot.trade_api import validate_write,CREATE
from kalshi_bot.market_data import MarketMonitor
from test_trader import Venue
from test_missed_no import FreshFeed


class FourMinuteTests(unittest.TestCase):
    def test_shared_boundary(self):
        self.assertEqual(ENTRY_WINDOW_SECONDS,240)
        for remaining in (241,240.001,30,1,.001,0,-1):self.assertFalse(entry_open(900,900-remaining))
        for remaining in (240,210,181,180,150,121,120,31,30.001):self.assertTrue(entry_open(900,900-remaining))

    def test_yes_and_no_actual_cycle_and_gateway_accept_new_minute(self):
        for side in ('yes','no'):
            for remaining in (241,240,210,181,180,150,121,31,30,1,0):
                with self.subTest(side=side,remaining=remaining),tempfile.TemporaryDirectory() as directory:
                    journal=Journal(Path(directory)/'db','offline');journal.running=True;venue=Venue();feed=FreshFeed()
                    original=venue.get
                    venue.get=lambda path,params=None:{'markets':[venue.market()]} if path=='/markets' else original(path,params)
                    if side=='yes':feed.prices={k.replace('yes','TEMP').replace('no','yes').replace('TEMP','no'):v for k,v in feed.prices.items()}
                    try:
                        with patch('kalshi_bot.fees.entry_reserve',return_value=D('.03')):
                            result=cycle(journal,venue,venue,None,now=venue.close-remaining,book_feed=feed)
                        allowed=30<remaining<=240
                        self.assertEqual(len(venue.orders),int(allowed))
                        if allowed:
                            self.assertEqual(result['side'],side)
                            payload=order_payload('KXBTC15M-T','buy',side,D('.98'),1,900,'test')
                            self.assertEqual(validate_write('POST',CREATE,payload,now=900-remaining),CREATE)
                        else:
                            payload=order_payload('KXBTC15M-T','buy',side,D('.98'),1,900,'test')
                            with self.assertRaises(ValueError):validate_write('POST',CREATE,payload,now=900-remaining)
                    finally:journal.close()

    def test_dashboard_window_opens_at_240_seconds(self):
        from test_refresh import market
        feed=Mock();feed.quote.return_value=None
        feed.health.return_value={'status':'reconnecting','last_update':None,'error':None}
        frames=[]
        monitor=MarketMonitor(Mock(),None,'/unused',lambda kind,**v:frames.append(v),feed=feed)
        monitor.market=market('KXBTC15M-T',0,900)
        monitor.publish_once(now=659);self.assertFalse(frames[-1]['entry_window_open'])
        monitor.publish_once(now=660);self.assertTrue(frames[-1]['entry_window_open'])
        monitor.publish_once(now=869.999);self.assertTrue(frames[-1]['entry_window_open'])
        monitor.publish_once(now=870);self.assertFalse(frames[-1]['entry_window_open'])
        monitor.publish_once(now=885);self.assertFalse(frames[-1]['entry_window_open'])


if __name__=='__main__':unittest.main()
