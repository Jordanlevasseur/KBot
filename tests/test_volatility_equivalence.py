"""Compare optimized ranges with the original definition, including gaps."""
import random
import unittest
from kalshi_bot.strategy import VolatilityTracker


class VolatilityEquivalenceTests(unittest.TestCase):
    def test_unconfirmed_account_is_reported_before_entry_waiting(self):
        import tempfile
        from test_prepared import Fixture
        with tempfile.TemporaryDirectory() as directory:
            f=Fixture(directory)
            try:
                f.journal.ready=False
                result=f.execute()
                self.assertEqual(result['status'],'Account reconciliation incomplete; new buys blocked')
                self.assertEqual(f.venue.attempts,[])
            finally:f.close()

    def test_ranges_preserve_gaps_duplicate_times_and_unordered_samples(self):
        rng=random.Random(64)
        points=[(rng.randrange(950),rng.randrange(101)/100) for _ in range(900)]
        expected=[(t,(max(v for s,v in points if t-30<=s<=t)-min(v for s,v in points if t-30<=s<=t))*100) for t,_ in points]
        self.assertEqual(VolatilityTracker._ranges(points),expected)

    def test_all_window_values_match_original_history_definition(self):
        rng=random.Random(27);tracker=VolatilityTracker()
        for t in range(930):
            tracker.add(t,{'yes_ask':rng.randrange(101)/100} if t%13 else None)
        for now in (929,940,1000):
            for seconds in (30,60,300,900):
                values=[(t,v) for t,v in tracker.samples if now-seconds<=t<=now]
                if seconds==30:
                    expected=(max(v for _,v in values)-min(v for _,v in values))*100 if values else None
                else:
                    ranges=[]
                    for t,_ in values:
                        history=[v for s,v in tracker.samples if t-30<=s<=t]
                        ranges.append((max(history)-min(history))*100)
                    expected=sum(ranges)/len(ranges) if ranges else None
                self.assertEqual(tracker.window_pct(now,seconds),expected)
