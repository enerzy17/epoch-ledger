"""The epoch arithmetic, on a three-epoch chain small enough to check by eye."""
import unittest

from ledger import build, report, samples

Y = 10**24


def epoch(eid, last_h, last_ts, last_supply, first_supply=None, first_burnt=0):
    row = {"epoch_id": eid, "last_height": str(last_h), "last_ts_ns": str(last_ts * 10**9),
           "last_supply": str(last_supply), "protocol_version": "88",
           "first_height": "", "first_ts_ns": "", "first_supply": "", "first_block_burnt": ""}
    if first_supply is not None:
        row.update(first_height=str(last_h - 99), first_ts_ns=str((last_ts - 3600) * 10**9),
                   first_supply=str(first_supply), first_block_burnt=str(first_burnt))
    return row


class LedgerTest(unittest.TestCase):
    def setUp(self):
        # Epoch A ends with 1000 NEAR.
        # Epoch B: the first block shows 1009.5 after minting 10 and burning 0.5 in that block. It ends at 1008.
        # Epoch C: the first block shows 1017.9 (minted 10, burnt 0.1 in the block). It ends at 1017.
        self.epochs = [
            epoch("A", 100, 1_700_000_000, 1000 * Y),
            epoch("B", 200, 1_700_003_600, 1008 * Y, first_supply=10095 * Y // 10, first_burnt=5 * Y // 10),
            epoch("C", 300, 1_700_007_200, 1017 * Y, first_supply=10179 * Y // 10, first_burnt=1 * Y // 10),
        ]

    def test_minted_and_burnt(self):
        ledger, skipped = build.epoch_ledger(self.epochs)
        self.assertEqual(skipped, 0)
        self.assertAlmostEqual(ledger[0]["minted_near"], 10.0)
        self.assertAlmostEqual(ledger[0]["burnt_near"], 2.0)   # 0.5 in the first block and 1.5 after it
        self.assertAlmostEqual(ledger[1]["minted_near"], 10.0)
        self.assertAlmostEqual(ledger[1]["burnt_near"], 1.0)
        self.assertEqual(ledger[0]["blocks"], 100)
        self.assertAlmostEqual(ledger[0]["hours"], 1.0)

    def test_minted_minus_burnt_is_the_supply_change(self):
        ledger, _ = build.epoch_ledger(self.epochs)
        net = sum(e["minted_near"] - e["burnt_near"] for e in ledger)
        self.assertAlmostEqual(net, 17.0)

    def test_epoch_without_first_block_is_counted_not_guessed(self):
        self.epochs[1] = epoch("B", 200, 1_700_003_600, 1008 * Y)
        ledger, skipped = build.epoch_ledger(self.epochs)
        self.assertEqual(skipped, 1)
        self.assertEqual([e["epoch_id"] for e in ledger], ["C"])


class HelpersTest(unittest.TestCase):
    def test_nakamoto(self):
        self.assertEqual(samples.nakamoto([40, 30, 20, 10]), 1)      # 40 > 33.3
        self.assertEqual(samples.nakamoto([25, 25, 25, 25]), 2)      # 50 > 33.3
        self.assertEqual(samples.nakamoto([10] * 10), 4)             # 40 > 33.3

    def test_pick_is_weekly_and_keeps_the_newest(self):
        day = 86400 * 10**9
        eps = [{"last_ts_ns": str(i * day), "epoch_id": str(i)} for i in range(30)]
        got = [e["epoch_id"] for e in samples.pick(eps)]
        self.assertEqual(got, ["0", "7", "14", "21", "28", "29"])

    def test_template_rejects_unknown_fact(self):
        self.assertEqual(report.fill("a {{x}} b", {"x": "1"}), "a 1 b")
        with self.assertRaises(KeyError):
            report.fill("{{missing}}", {})


if __name__ == "__main__":
    unittest.main()


class WalletCheckTest(unittest.TestCase):
    def test_balance_rise_against_buyback_series(self):
        day = 86400
        t0 = 1_772_000_000  # 2026-02-25
        smp = [
            {"ts_ns": str(t0 * 10**9), "height": "1", "buybacks_wallet": str(100 * Y), "fe_wallet": str(5 * Y),
             "onecs_wallet": ""},
            {"ts_ns": str((t0 + 7 * day) * 10**9), "height": "2", "buybacks_wallet": str(160 * Y),
             "fe_wallet": str(5 * Y), "onecs_wallet": str(10 * Y)},
        ]
        d0, d1 = build.day_of(smp[0]["ts_ns"]), build.day_of(smp[1]["ts_ns"])
        table = [
            {"date": d0, "intents_holders_revenue_usd": 999.0, "price_usd": 1.0},   # the start day is not counted
            {"date": "2026-02-27", "intents_holders_revenue_usd": 100.0, "price_usd": 2.0},   # buys 50 NEAR
            {"date": d1, "intents_holders_revenue_usd": 120.0, "price_usd": 4.0},             # buys 30 NEAR
        ]
        out = build.wallet_check(smp, table, (t0 + 30 * day) * 10**9)
        self.assertAlmostEqual(out["balance_rise_near"], 70.0)             # 175 held at the end, 105 at the start
        self.assertAlmostEqual(out["buyback_near_at_daily_price"], 80.0)
        self.assertAlmostEqual(out["buyback_usd"], 220.0)
        self.assertAlmostEqual(out["rise_over_buyback"], 0.875)

    def test_no_samples_no_check(self):
        self.assertIsNone(build.wallet_check([], [], 10**30))
