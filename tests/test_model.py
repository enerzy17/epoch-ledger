"""The model is four formulas. These tests pin them with numbers worked by hand."""
import math
import unittest

from ledger import model


class ModelTest(unittest.TestCase):
    # 1,000,000,000 NEAR at 2.5% a year is 68,446.27 NEAR a day
    SUPPLY, RATE = 1_000_000_000, 0.025

    def test_issuance_per_day(self):
        self.assertAlmostEqual(model.issuance_near_per_day(self.SUPPLY, self.RATE), 68446.2697, places=3)

    def test_coverage_adds_burn_and_buyback(self):
        # burn 446.2697 NEAR, buyback $100M * 0.04% / $4 = 10,000 NEAR, total 10,446.27 of 68,446.27
        cover = model.coverage(self.SUPPLY, self.RATE, 4.0, 446.2697, 100e6, 0.0004)
        self.assertAlmostEqual(cover, 10446.2697 / 68446.2697, places=9)

    def test_breakeven_volume_gives_full_coverage(self):
        vol = model.breakeven_volume_usd_per_day(self.SUPPLY, self.RATE, 4.0, 446.2697, 0.0004)
        self.assertAlmostEqual(vol, 68000 * 4.0 / 0.0004, delta=1.0)  # $680M a day
        self.assertAlmostEqual(model.coverage(self.SUPPLY, self.RATE, 4.0, 446.2697, vol, 0.0004), 1.0, places=9)

    def test_breakeven_price_gives_full_coverage(self):
        price = model.breakeven_price_usd(self.SUPPLY, self.RATE, 446.2697, 100e6, 0.0004)
        self.assertAlmostEqual(price, 40000 / 68000, places=6)
        self.assertAlmostEqual(model.coverage(self.SUPPLY, self.RATE, price, 446.2697, 100e6, 0.0004), 1.0, places=9)

    def test_higher_price_lowers_coverage(self):
        low = model.coverage(self.SUPPLY, self.RATE, 2.0, 0, 100e6, 0.0004)
        high = model.coverage(self.SUPPLY, self.RATE, 8.0, 0, 100e6, 0.0004)
        self.assertAlmostEqual(low, 4 * high, places=9)

    def test_years_to_reach(self):
        self.assertEqual(model.years_to_reach(10, 5, 0.5), 0.0)
        self.assertAlmostEqual(model.years_to_reach(100, 400, 1.0), 2.0, places=9)
        self.assertEqual(model.years_to_reach(100, 400, 0.0), math.inf)
        self.assertEqual(model.years_to_reach(100, 400, -0.2), math.inf)

    def test_net_dilution(self):
        self.assertAlmostEqual(model.net_dilution_rate(0.025, 0.2), 0.02, places=12)
        self.assertAlmostEqual(model.net_dilution_rate(0.025, 1.0), 0.0, places=12)


if __name__ == "__main__":
    unittest.main()
