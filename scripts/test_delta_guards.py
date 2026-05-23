"""
test_delta_guards.py — falsifying tests for the two guards added to
resolver._update_city_delta() on 2026-05-19:
  1. Oracle-bug skip list (_ORACLE_BUG_RESOLUTIONS)
  2. ±2°C sanity cap on a single observed_delta (_DELTA_SANITY_CAP_C)

Assumption being tested:
    (a) When (city, forecast_date) is in _ORACLE_BUG_RESOLUTIONS, the
        function must NOT touch resolution_stations.
    (b) When |observed_delta| > 2°C, the function must NOT touch
        resolution_stations.
    (c) A legitimate small observed_delta (|d| <= 2°C) on a NON-bug city
        must STILL update resolution_stations as before — i.e. the guards
        do not over-restrict.

Falsifying outcome: any of (a)/(b)/(c) violated → FAIL + exit non-zero.

This is a pure logic test — it stubs out the database side-effects and the
phase2_lock_temp/bracket_midpoint helpers, then verifies the function's
behavior via Supabase write attempts captured by a mock.

Run locally (no live Supabase needed) or on the VPS:
    venv/bin/python3 scripts/test_delta_guards.py
"""
import logging
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import resolver  # noqa: E402

log = logging.getLogger("test_delta_guards")
logging.basicConfig(level=logging.INFO, format="%(message)s")


class FakeTable:
    """Captures write attempts so we can assert on them."""
    def __init__(self):
        self.update_calls: list[dict] = []
        self.select_payload = [{"delta_c": 0.0, "delta_samples": 0}]

    # supabase-py chainable API surface we need to satisfy:
    def select(self, *_a, **_kw): return self
    def eq(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self
    def execute(self):
        class R:
            def __init__(self, data): self.data = data
        return R(self.select_payload)

    def update(self, payload):
        self.update_calls.append(payload)
        return self  # so .eq().execute() still chains


def run_case(label, city, forecast_date, lock_temp, winner_question, expect_update):
    fake = FakeTable()
    with mock.patch.object(resolver, "sb", mock.MagicMock(table=lambda _n: fake)), \
         mock.patch.object(resolver, "_get_phase2_lock_temp", return_value=lock_temp), \
         mock.patch.object(resolver, "_bracket_midpoint_c",
                           side_effect=lambda q: _midpoint_from_question(q)):
        resolver._update_city_delta(city, forecast_date, winner_question, log)
    got_update = len(fake.update_calls) > 0
    ok = got_update == expect_update
    status = "PASS" if ok else "FAIL"
    print(f"  {status}: {label} (update_called={got_update}, expected={expect_update})")
    return ok


def _midpoint_from_question(q: str) -> float | None:
    # Trivial helper: caller passes the °C midpoint as a string like "23.0".
    try:
        return float(q)
    except Exception:
        return None


def main() -> int:
    print("test_delta_guards:")
    results = [
        # (a) Oracle-bug skip
        run_case(
            "Seoul 2026-05-17 (oracle-bug) — must SKIP",
            city="Seoul", forecast_date="2026-05-17",
            lock_temp=24.0, winner_question="23.0",       # would be -1.0°C, in-cap but skip-listed
            expect_update=False,
        ),
        run_case(
            "Miami 2026-05-17 (oracle-bug) — must SKIP",
            city="Miami", forecast_date="2026-05-17",
            lock_temp=30.0, winner_question="29.0",
            expect_update=False,
        ),
        # (b) Sanity cap
        run_case(
            "São Paulo 2026-05-19 observed_delta=+7°C — must SKIP",
            city="São Paulo", forecast_date="2026-05-19",
            lock_temp=20.0, winner_question="27.0",
            expect_update=False,
        ),
        run_case(
            "Hypothetical observed_delta=-3°C — must SKIP",
            city="Tokyo", forecast_date="2026-05-19",
            lock_temp=30.0, winner_question="27.0",
            expect_update=False,
        ),
        # (c) Legitimate small delta — must STILL update
        run_case(
            "Tokyo 2026-05-19 observed_delta=-0.5°C — must UPDATE",
            city="Tokyo", forecast_date="2026-05-19",
            lock_temp=30.5, winner_question="30.0",
            expect_update=True,
        ),
        run_case(
            "Houston 2026-05-19 observed_delta=+1.5°C — must UPDATE",
            city="Houston", forecast_date="2026-05-19",
            lock_temp=29.0, winner_question="30.5",
            expect_update=True,
        ),
        # Edge: exactly at the cap (2.0°C) — current code skips only when ABOVE 2.0
        run_case(
            "Edge observed_delta=+2.0°C (== cap) — must UPDATE (cap is strict >)",
            city="Dallas", forecast_date="2026-05-19",
            lock_temp=28.0, winner_question="30.0",
            expect_update=True,
        ),
    ]
    if all(results):
        print("ALL PASS")
        return 0
    print("FAILURES present — guards not behaving as documented")
    return 1


if __name__ == "__main__":
    sys.exit(main())
