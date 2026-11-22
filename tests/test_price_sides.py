"""Both outcomes remain explicit in quotes and NO stop decisions."""
import tempfile
import unittest
from strategy_fixture import configure_policy

def setUpModule():
    configure_policy(92,71)

from decimal import Decimal as D
from pathlib import Path
from kalshi_bot.desktop_worker import quote_state,simulation_state
from kalshi_bot.execution import Journal
from kalshi_bot.core import Engine,Quote
from kalshi_bot.api import book_prices
from test_trader import Venue
from kalshi_bot.trader import cycle


class PriceSideTests(unittest.TestCase):
    def prices(self):
        return book_prices({'orderbook_fp':{'yes_dollars':[['.0180','10']], 'no_dollars':[['.9800','10']]}})

    def test_yes_bid_complements_no_buy_ask_not_no_sell_bid(self):
        state=quote_state(self.prices(),{'ticker':'KXBTC15M-T','side':'no'},'KXBTC15M-T')
        self.assertEqual(D(state['yes_bid_cents']),D('1.8'))
        self.assertEqual(D(state['no_ask_cents']),D('98.2'))
        self.assertEqual(D(state['no_bid_cents']),D('98'))
        self.assertEqual(D(state['bid_cents']),D('98'))
        self.assertEqual(state['bid_side'],'no')

    def test_flat_or_different_market_never_defaults_position_bid_to_yes(self):
        for position in (None,{'ticker':'KXBTC15M-OTHER','side':'no'}):
            state=quote_state(self.prices(),position,'KXBTC15M-T')
            self.assertIsNone(state['bid_cents'])
            self.assertIsNone(state['bid_side'])
            self.assertEqual(D(state['yes_bid_cents']),D('1.8'))
            self.assertEqual(D(state['no_bid_cents']),D('98'))

    def test_absent_book_hides_prices_instead_of_reusing_opposite_side(self):
        state=quote_state(None,{'ticker':'KXBTC15M-T','side':'no'},'KXBTC15M-T')
        self.assertIsNone(state['bid_cents'])
        self.assertIsNone(state['no_bid_cents'])

    def test_simulation_missing_bid_does_not_break_price_display(self):
        with tempfile.TemporaryDirectory() as directory:
            engine=Engine(Path(directory)/'db')
            try:
                state=simulation_state(engine,Quote('KXBTC15M-T',900,780,None,98,2,None))
                self.assertIsNone(state['yes_bid_cents'])
                self.assertIsNone(state['no_ask_cents'])
            finally: engine.close()

    def test_no_position_stop_uses_no_bid_even_if_yes_bid_is_low(self):
        class NoVenue(Venue):
            def pages(self,path,params=None):
                if path=='/portfolio/positions':
                    return [{'ticker':'KXBTC15M-T','position_fp':str(-self.quantity)}] if self.quantity else []
                return super().pages(path,params)
            def get(self,path,params=None):
                if path.endswith('/orderbook'):
                    return {'orderbook_fp':{'yes_dollars':[['.018','10']], 'no_dollars':[[str(self.no_bid),'10']]}}
                return super().get(path,params)
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'db','offline'); venue=NoVenue(); venue.no_bid=D('.98')
            try:
                journal.reconcile([],[],[]); journal.running=True
                journal.submit(venue,'KXBTC15M-T','buy','no',D('.99'),3,venue.close,venue.close-100)
                order=venue.orders[0]
                order.update(status='executed',fill_count_fp='3',remaining_count_fp='0')
                venue.quantity=D(3)
                venue.fills=[{'fill_id':'no-test','order_id':order['order_id'],'count_fp':'3',
                              'yes_price_dollars':'.01','no_price_dollars':'.99','fee_cost':'.01'}]
                result=cycle(journal,venue,venue,D('.03'),now=venue.close-90)
                self.assertEqual(D(result['bid']),D('.98'))
                self.assertEqual(len(venue.orders),1)
                self.assertEqual(journal.db.execute('SELECT stop FROM markets').fetchone()[0],0)
                venue.no_bid=D('.71')
                result=cycle(journal,venue,venue,D('.03'),now=venue.close-89)
                self.assertEqual(len(venue.orders),2)
                sell=journal.db.execute("SELECT side,price FROM orders WHERE action='sell'").fetchone()
                self.assertEqual(tuple(sell),('no','0.01'))
            finally: journal.close()


if __name__=='__main__': unittest.main()
