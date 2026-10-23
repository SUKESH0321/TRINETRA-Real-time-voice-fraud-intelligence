"""Runs every scenario instantly and prints the outcome next to the expectation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import load_rules
from app.scenarios import load_scenarios, run_sync
from app.session import CallSession


def main() -> int:
    bad = 0
    for sid, sc in load_scenarios().items():
        s = CallSession(load_rules())
        run_sync(s, sc)
        snap = s.snapshot()
        exp = sc.data["expected"]
        ok = snap["max_level"] == exp["max_level"] and len(snap["pings"]) == exp["pings"]
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {sid:28} max={snap['max_level']:9} (exp {exp['max_level']:9}) "
              f"pings={len(snap['pings'])} (exp {exp['pings']}) findings={len(snap['findings'])} alerts={len(snap['alerts'])}")
        for a in snap["alerts"]:
            print(f"      t={a['t']:>5} {a['level']:9} {a['trigger']:10} sound={a['sound']}  {a['reason'][:70]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
