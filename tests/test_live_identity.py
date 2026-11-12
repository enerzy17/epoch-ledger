"""Live check of the identity the whole ledger rests on. Needs the network.

    LIVE=1 python -m unittest tests.test_live_identity -v

Inside an epoch, total_supply falls from one block to the next by exactly the
NEAR burnt in the chunks included in the newer block.
"""
import os
import unittest

from ledger import epochs, rpc


@unittest.skipUnless(os.environ.get("LIVE") == "1", "set LIVE=1 to query mainnet")
class LiveIdentityTest(unittest.TestCase):
    def test_supply_falls_by_burnt_fees(self):
        tip = rpc.block()["header"]["height"] - 20
        prev, checked = None, 0
        for height in range(tip, tip + 8):
            try:
                blk = rpc.block(height)
            except rpc.RpcError:
                prev = None
                continue
            supply = int(blk["header"]["total_supply"])
            if prev is not None and blk["header"]["epoch_id"] == prev[1]:
                self.assertEqual(prev[0] - supply, epochs.block_burnt(blk))
                checked += 1
            prev = (supply, blk["header"]["epoch_id"])
        self.assertGreaterEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()
