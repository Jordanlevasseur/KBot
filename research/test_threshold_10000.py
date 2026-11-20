import unittest
from threshold_10000 import analyze,summary

def bars(vals):
 return [{'end_period_ts':(i+1)*60,'price':{'high_dollars':str(h),'low_dollars':str(l),'close_dollars':str(c)}} for i,(h,l,c) in enumerate(vals)]
class StudyTests(unittest.TestCase):
 def test_same_minute_low_before_touch_not_assumed(self):
  r=analyze(bars([(.99,.5,.99),(.99,.99,.99),(1,1,1),(1,1,1)]),'yes',9800,'yes')
  self.assertFalse(r['definite_dip']);self.assertTrue(r['possible_dip']);self.assertEqual(r['confirmed_low_cents'],99)
 def test_close_after_touch_proves_dip(self):
  r=analyze(bars([(.98,.5,.90),(.99,.95,.99),(1,1,1),(1,1,1)]),'yes',9800,'yes')
  self.assertTrue(r['definite_dip']);self.assertTrue(r['confirmed_trade_recovery']);self.assertTrue(r['won'])
 def test_later_dip_and_winning_settlement_without_print_recovery(self):
  r=analyze(bars([(.98,.97,.98),(.95,.63,.70),(.8,.7,.8),(.9,.8,.9)]),'yes',9800,'yes')
  self.assertTrue(r['won']);self.assertTrue(r['definite_dip']);self.assertFalse(r['confirmed_trade_recovery']);self.assertEqual(r['confirmed_low_cents'],63)
 def test_flip_and_final_minute_uncertainty(self):
  r=analyze(bars([(.9,.8,.9),(.9,.8,.9),(.9,.8,.9),(.98,.01,.01)]),'yes',9800,'no')
  self.assertFalse(r['won']);self.assertTrue(r['definite_dip']);self.assertTrue(r['first_touch_final_minute'])
 def test_no_symmetry(self):
  yes=bars([(.98,.5,.9),(.99,.95,.99),(1,1,1),(1,1,1)])
  no=bars([(.5,.02,.1),(.05,.01,.01),(0,0,0),(0,0,0)])
  a=analyze(yes,'yes',9800,'yes');b=analyze(no,'no',9800,'no')
  for k in a:
   if k!='side':self.assertEqual(a[k],b[k])
 def test_below_threshold_excluded(self):self.assertIsNone(analyze(bars([(.95,.5,.9)]*4),'yes',9600,'yes'))
if __name__=='__main__':unittest.main()
