"""
test_sizing.py — falsifying tests for sizing.py.

Tested assumptions (CLAUDE.md Rule 2):
  (a) When TODAY is inside the week-1 sizing_schedule row, get_current_sizing
      returns the week-1 row (not defaults).
  (b) When TODAY is OUTSIDE any sizing_schedule row, returns safe defaults
      (size = $0.01) — i.e. an expired schedule never produces an oversized trade.
  (c) size_for_yes_lock and size_for_no_sweep at week 1 (kelly=0) ignore edge
      and return the schedule's flat $3 / $5.
  (d) deployment_cap_pct caps the per-trade size against bankroll regardless
      of the schedule's nominal size.

This is a logic test — it stubs Supabase via monkeypatch so it can run on a
laptop with no DB access.
"""
import sys
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import sizing  # noqa: E402


class _FakeSB:
    def __init__(self, week1_in_range: bool):
        self.week1_in_range = week1_in_range

    def table(self, _name):
        return self

    def select(self, *_a, **_kw):
        return self

    def lte(self, *_a, **_kw):
        return self

    def gte(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def execute(self):
        class R:
            pass
        r = R()
        if self.week1_in_range:
            r.data = [{
                "week_label": "week_1",
                "phase2_yes_size_usd": 3.00,
                "phase2_no_sweep_size_usd": 5.00,
                "phase2_no_sweep_max_per_city": 3,
                "deployment_cap_pct": 30.0,
                "kelly_fraction": 0.0,
            }]
        else:
            r.data = []
        return r


def run_case(label: str, sb_in_range: bool, today: date, asserts) -> bool:
    with mock.patch.object(sizing, "_sb", _FakeSB(sb_in_range)):
        cfg = sizing.get_current_sizing(today=today)
        try:
            asserts(cfg)
        except AssertionError as e:
            print(f"  FAIL: {label} — {e}")
            return False
    print(f"  PASS: {label}")
    return True


def run_size_case(label: str, sb_in_range: bool, fn, args, expected) -> bool:
    with mock.patch.object(sizing, "_sb", _FakeSB(sb_in_range)):
        got = fn(*args)
        if abs(got - expected) > 0.005:
            print(f"  FAIL: {label} — expected ${expected:.2f}, got ${got:.2f}")
            return False
    print(f"  PASS: {label} — ${got:.2f}")
    return True


def main():
    today = date(2026, 5, 22)  # week 1

    results = []

    # (a) Inside the week-1 row, returns it.
    results.append(run_case(
        "(a) inside week-1 range returns week-1 row",
        sb_in_range=True,
        today=today,
        asserts=lambda c: (
            assert_eq(c.week_label, "week_1") or True,
            assert_eq(c.phase2_yes_size_usd, 3.00),
            assert_eq(c.phase2_no_sweep_size_usd, 5.00),
            assert_eq(c.phase2_no_sweep_max_per_city, 3),
            assert_eq(c.deployment_cap_pct, 30.0),
            assert_false(c.is_default, "should NOT be flagged is_default"),
        ),
    ))

    # (b) Outside any row, returns safe defaults.
    results.append(run_case(
        "(b) outside any row returns safe defaults ($0.01 sizes)",
        sb_in_range=False,
        today=today,
        asserts=lambda c: (
            assert_true(c.is_default, "should be flagged is_default"),
            assert_eq(c.phase2_yes_size_usd, 0.01),
            assert_eq(c.phase2_no_sweep_size_usd, 0.01),
        ),
    ))

    # (c) size_for_yes_lock at week 1 (kelly=0) returns $3 regardless of edge.
    # Bankroll huge so deployment cap doesn't bind.
    results.append(run_size_case(
        "(c) size_for_yes_lock returns flat $3 at week 1",
        sb_in_range=True,
        fn=sizing.size_for_yes_lock,
        args=(0.85, 0.50, 100_000),   # large edge, large bankroll
        expected=3.00,
    ))
    results.append(run_size_case(
        "(c) size_for_no_sweep returns flat $5 at week 1",
        sb_in_range=True,
        fn=sizing.size_for_no_sweep,
        args=(0.85, 0.50, 100_000),
        expected=5.00,
    ))

    # (d) deployment_cap caps below nominal when bankroll is tiny.
    # Week 1 cap = 30% of bankroll. Bankroll = $5 → cap = $1.50 < nominal $3.
    results.append(run_size_case(
        "(d) deployment cap binds when bankroll=$5 (cap $1.50 < nominal $3)",
        sb_in_range=True,
        fn=sizing.size_for_yes_lock,
        args=(0.85, 0.50, 5.00),
        expected=1.50,
    ))

    # (e) Absolute cap binds when schedule nominal exceeds it.
    # Force a high nominal (kelly path), confirm absolute cap (default $10) wins.
    # We patch the schedule to have kelly=1.0 with $5 base → at 28pp edge,
    # bump = 1 + 1.0 * (0.28 - 0.08)/0.10 = 3x => $15 nominal. Absolute cap = $10.
    class _HighKellySB(_FakeSB):
        def execute(self):
            class R: pass
            r = R()
            if self.week1_in_range:
                r.data = [{
                    "week_label": "kelly_test",
                    "phase2_yes_size_usd": 5.00,
                    "phase2_no_sweep_size_usd": 5.00,
                    "phase2_no_sweep_max_per_city": 3,
                    "deployment_cap_pct": 100.0,  # effectively disabled
                    "kelly_fraction": 1.0,
                }]
            else:
                r.data = []
            return r

    def run_abs_cap():
        # Use mock that returns the high-kelly row and a high _config_float for
        # absolute cap to confirm the absolute cap is being consulted.
        with mock.patch.object(sizing, "_sb", _HighKellySB(True)), \
             mock.patch.object(sizing, "_absolute_cap_usd", return_value=10.0), \
             mock.patch.object(sizing, "_daily_deploy_cap_usd", return_value=10_000.0), \
             mock.patch.object(sizing, "deployed_today_usd", return_value=0.0):
            got = sizing.size_for_no_sweep(0.85, 0.57, 1_000_000)
        ok = abs(got - 10.0) < 0.005
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: (e) absolute $10 cap binds against $15 nominal — got ${got:.2f}")
        return ok
    results.append(run_abs_cap())

    # (f) Daily-deploy cap returns $0 when budget exhausted.
    def run_daily_cap():
        with mock.patch.object(sizing, "_sb", _FakeSB(True)), \
             mock.patch.object(sizing, "_absolute_cap_usd", return_value=10.0), \
             mock.patch.object(sizing, "_daily_deploy_cap_usd", return_value=50.0), \
             mock.patch.object(sizing, "deployed_today_usd", return_value=50.0):  # fully used
            got = sizing.size_for_no_sweep(0.85, 0.50, 100_000)
        ok = abs(got - 0.0) < 0.005
        status = "PASS" if ok else "FAIL"
        print(f"  {status}: (f) daily-deploy cap exhausted → size=$0 — got ${got:.2f}")
        return ok
    results.append(run_daily_cap())

    if all(results):
        print("ALL PASS")
        return 0
    print("FAILURES present")
    return 1


def assert_eq(a, b):
    assert a == b, f"expected {b!r}, got {a!r}"


def assert_true(x, msg=""):
    assert bool(x), msg or "expected truthy"


def assert_false(x, msg=""):
    assert not bool(x), msg or "expected falsy"


if __name__ == "__main__":
    sys.exit(main())
