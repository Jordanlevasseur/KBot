import unittest
from screen_thresholds import simulate,fee,quotes,stats,choose,MODES


def market(bids,asks=None,result='yes'):
    if asks is None:asks=[bid+100 for bid in bids]
    return {'ticker':'KXBTC15M-TEST','result':result,'bars':[{'remaining':240-i*60,'yes':{'ask':a,'bid':p,'low':p,'high':p},'no':{'ask':10000-p,'bid':10000-a,'low':10000-a,'high':10000-a}} for i,(p,a) in enumerate(zip(bids,asks))]}


class ScreenTests(unittest.TestCase):
    def test_fee_and_winning_settlement(self):
        self.assertEqual(fee(9500),333)
        r=simulate(market([9400]*4,[9500]*4),95,99,71,'touch_limit')
        self.assertEqual(r[:4],(4667,1,1,0))
    def test_no_side_is_distinct_and_symmetric(self):
        yes=market([9400]*4,[9500]*4);no=market([500]*4,[600]*4,result='no')
        self.assertEqual(simulate(yes,95,99,71,'touch_limit'),simulate(no,95,99,71,'touch_limit'))
    def test_gap_below_limit_does_not_invent_fill(self):
        m=market([9400,5000,4000,0],[9500,5100,4100,100],result='no')
        for mode in MODES:
            r=simulate(m,95,99,71,mode);self.assertEqual(r[0],-95333);self.assertEqual(r[-1],1);self.assertEqual(r[5],0)
    def test_stop_remains_latched_after_rebound(self):
        m=market([9400,5000,9000,9000],[9500,5100,9100,9100])
        r=simulate(m,95,99,71,'next_open_recovery');self.assertEqual(r[5],1);self.assertEqual(r[1],1);self.assertLess(r[0],0)
    def test_touch_assumption_and_recovery_are_distinct(self):
        m=market([9400,9400,9400,9400],[9500]*4);m['bars'][0]['yes']['low']=6000
        a=simulate(m,95,99,71,'touch_limit');b=simulate(m,95,99,71,'next_open_recovery')
        self.assertNotEqual(a,b);self.assertGreaterEqual(a[1],b[1])
    def test_final_30_seconds_never_enter(self):
        m=market([4000,4000,4000,4000],[4100]*4);m['bars'].append({'remaining':30,'yes':{'ask':9500,'bid':9400,'low':9400,'high':9400},'no':{'ask':600,'bid':500,'low':500,'high':500}})
        self.assertEqual(simulate(m,95,99,71,'touch_limit')[1],0)
    def test_selection_accepts_no_holdout_data(self):
        def s(net,drawdown=1):return {m:{'net_dollars':net,'max_drawdown_dollars':drawdown,'buy_cycles':30} for m in MODES}
        base={'train':s(1),'validation':s(1)};good={'train':s(2),'validation':s(2)};overfit={'train':s(100),'validation':s(-1)}
        selected,_=choose({(95,99,71):base,(96,99,70):good,(99,99,50):overfit,(91,96,0):{'train':s(1000),'validation':s(1000)}},base)
        self.assertEqual(selected,(96,99,70))
    def test_validation_checks_all_training_qualified_buy_bands(self):
        def s(net):return {m:{'net_dollars':net,'max_drawdown_dollars':1,'buy_cycles':30} for m in MODES}
        base={'train':s(1),'validation':s(1)}
        candidates={(95,99,71):base,(91,96,50):{'train':s(2),'validation':s(3)}}
        for stop in range(51,76):candidates[(98,99,stop)]={'train':s(100),'validation':s(0)}
        selected,_=choose(candidates,base)
        self.assertEqual(selected,(91,96,50))

    def test_no_quote_extremes_use_complementary_ask_and_bid(self):
        m={'ticker':'KXBTC15M-T','close':900,'result':'no'};candles=[]
        for end in (720,780,840,900):
            candles.append({'end_period_ts':end,'yes_bid':{'open_dollars':'.04','low_dollars':'.01','high_dollars':'.20'},'yes_ask':{'open_dollars':'.05','low_dollars':'.02','high_dollars':'.30'}})
        v=quotes(m,{'candlesticks':candles})['bars'][0]['no']
        self.assertEqual(v,{'ask':9600,'bid':9500,'low':7000,'high':9800})
        candles[0]['yes_ask']['open_dollars']=None
        with self.assertRaises(ValueError):quotes(m,{'candlesticks':candles})

    def test_drawdown_uses_chronological_market_sequence(self):
        v=stats([(10000,1,1,0,0,0,0),(-20000,1,0,1,0,0,0),(5000,1,1,0,0,0,0)])
        self.assertEqual(v['net_dollars'],-.5);self.assertEqual(v['max_drawdown_dollars'],2)

if __name__=='__main__':unittest.main()
