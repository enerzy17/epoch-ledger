"""The committed outputs are exactly what the committed raw data produces."""
import csv
import json
import os
import tempfile
import unittest

from ledger import build, claims

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
DERIVED = os.path.join(ROOT, "data", "derived")


@unittest.skipUnless(os.path.exists(os.path.join(RAW, "MANIFEST.json")), "no raw snapshot in this checkout")
class SnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.metrics, cls.table, cls.ledger, _ = build.build(RAW, cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_metrics_reproduce(self):
        with open(os.path.join(self.tmp.name, "metrics.json")) as a, open(os.path.join(DERIVED, "metrics.json")) as b:
            self.assertEqual(json.load(a), json.load(b))

    def test_every_claim_holds(self):
        failed = [c["id"] for c in claims.claims(self.metrics) if not c["holds"]]
        self.assertEqual(failed, [])

    def test_ledger_reconciles_with_header_supply(self):
        ck = self.metrics["checks"]
        self.assertEqual(ck["epochs_without_first_block"], 0)
        self.assertAlmostEqual(ck["sum_minted_minus_burnt_near"], ck["supply_change_over_ledger_near"], places=3)

    def test_epochs_are_contiguous(self):
        with open(os.path.join(RAW, "epochs.csv"), newline="") as fh:
            rows = list(csv.DictReader(fh))
        for older, newer in zip(rows, rows[1:]):
            gap = int(newer["first_height"]) - int(older["last_height"])
            self.assertTrue(1 <= gap <= 40, f"epoch {newer['epoch_id']} starts {gap} heights after the previous one")
            self.assertEqual(newer["prev_epoch_last_hash"] != "", True)


if __name__ == "__main__":
    unittest.main()
