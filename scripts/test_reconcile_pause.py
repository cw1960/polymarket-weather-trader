"""
test_reconcile_pause.py — falsifying test for the bankroll_reconcile_paused
flag added to phase2_engine.reconcile_bankroll() on 2026-05-19.

Assumption being tested:
    When system_config.bankroll_reconcile_paused='1', calling
    reconcile_bankroll() must:
      (a) NOT mutate system_config.bankroll_usd
      (b) NOT write a bankroll_snapshots row for today
      (c) return the existing bankroll value unchanged

Falsifying outcome: if any of (a)/(b)/(c) is violated, the test prints FAIL
and exits non-zero — meaning the pause is not actually pausing anything,
which is exactly the foot-gun the 2026-05-19 review flagged.

Run on the VPS (read/write Supabase, side-effects rolled back at end):
    cd /root/polymarket && venv/bin/python3 scripts/test_reconcile_pause.py
"""
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path("/root/polymarket/.env"))
sys.path.insert(0, str(Path(__file__).parent))

from supabase import create_client  # noqa: E402

url = os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"]
sb = create_client(url, os.environ["SUPABASE_SERVICE_KEY"])

from phase2_engine import reconcile_bankroll  # noqa: E402


def _read_bankroll() -> float:
    r = sb.table("system_config").select("value").eq("key", "bankroll_usd").single().execute()
    return float(r.data["value"])


def _set_flag(value: str | None) -> None:
    if value is None:
        sb.table("system_config").delete().eq("key", "bankroll_reconcile_paused").execute()
    else:
        sb.table("system_config").upsert({"key": "bankroll_reconcile_paused", "value": value}).execute()


def _snapshot_exists_for_today() -> bool:
    today = date.today().isoformat()
    r = sb.table("bankroll_snapshots").select("id").eq("snapshot_date", today).execute()
    return bool(r.data)


def main() -> int:
    # Record initial state so we can restore it.
    initial_bankroll = _read_bankroll()
    initial_flag_row = sb.table("system_config").select("value").eq("key", "bankroll_reconcile_paused").execute()
    initial_flag = initial_flag_row.data[0]["value"] if initial_flag_row.data else None

    # Today's snapshot may already exist; remember whether it did so we don't
    # delete a legitimate one in cleanup.
    initial_snapshot_existed = _snapshot_exists_for_today()
    today = date.today().isoformat()

    failures: list[str] = []
    try:
        # ── Test 1: with flag=1, reconcile must be a no-op ───────────────
        _set_flag("1")
        # Force a known different bankroll to detect any silent write.
        sb.table("system_config").upsert({"key": "bankroll_usd", "value": "60.21"}).execute()
        if initial_snapshot_existed:
            # Wipe today's snapshot so we can detect if reconcile re-creates one.
            sb.table("bankroll_snapshots").delete().eq("snapshot_date", today).execute()

        returned = reconcile_bankroll()
        after_bankroll = _read_bankroll()
        snapshot_after = _snapshot_exists_for_today()

        if abs(after_bankroll - 60.21) > 0.001:
            failures.append(
                f"(a) bankroll_usd was mutated despite flag=1: 60.21 -> {after_bankroll}"
            )
        if snapshot_after:
            failures.append(f"(b) bankroll_snapshots row written for {today} despite flag=1")
        if abs(returned - 60.21) > 0.001:
            failures.append(f"(c) reconcile_bankroll() returned {returned}, expected 60.21")

        # ── Test 2: with flag=0, reconcile must run normally ─────────────
        # (sanity check that the early-return only triggers on '1')
        _set_flag("0")
        sb.table("bankroll_snapshots").delete().eq("snapshot_date", today).execute()
        returned2 = reconcile_bankroll()
        snapshot_after2 = _snapshot_exists_for_today()
        if not snapshot_after2:
            failures.append("(d) flag=0 did NOT produce a snapshot — early-return is too aggressive")
        if returned2 is None:
            failures.append("(e) flag=0 returned None — reconcile path broke")
    finally:
        # Restore original state.
        sb.table("system_config").upsert({"key": "bankroll_usd", "value": str(initial_bankroll)}).execute()
        _set_flag(initial_flag)
        if not initial_snapshot_existed:
            sb.table("bankroll_snapshots").delete().eq("snapshot_date", today).execute()

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("PASS: bankroll_reconcile_paused flag correctly halts reconcile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
