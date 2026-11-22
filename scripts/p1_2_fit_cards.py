"""Fit the P1-2 transport cards from source data and print the held-out checks H1-H5."""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mkt.stitch import cards  # noqa: E402

for cid in (sys.argv[1:] or ["TC-DV1", "TC-RR1", "TC-DV2", "TC-EX1", "TC-TB1"]):
    t = time.time()
    card = cards.fit_all([cid])[cid]
    for h, v in card["heldout"].items():
        if not isinstance(v, dict):
            print(f"{cid}  {h}: {v}"); continue
        print(f"{cid}  {h}: {'PASS' if v.get('pass', True) else 'FAIL'}  {({k: v[k] for k in v if k != 'pass'})}"[:400])
    print(f"{cid}  estimate {str(card['estimate'])[:300]}  status: {card['status']}  ({time.time() - t:.0f}s)")
