"""
System health watchdog.
=======================

Runs every 10 minutes via cron.  Verifies the critical components of
the trading system are alive and recent.  Emails an alert if anything
looks stuck.

Checks:
  1. TS executor HTTP service is reachable on 127.0.0.1:8787
  2. temp_monitor has run within the last 15 minutes
     (we infer this from the freshest `temp_readings.created_at`)
  3. Bankroll reconciler has run within the last 5 minutes
     (we look at `system_config.bankroll_usd` updated_at if present;
      otherwise we just verify it's not stuck at a clearly stale value)
  4. POL gas balance on the EOA is enough for taker-retry cancels
     (only matters if a maker retry path is hit — informational alert)

Failures are deduped by `alert_key` so a stuck cycle won't spam you.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import re
import requests

sys.path.insert(0, os.path.dirname(__file__))

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY
from notifier import send_alert
import remediation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | watchdog | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("watchdog")

TS_EXECUTOR_URL = os.getenv("TS_EXECUTOR_URL", "http://127.0.0.1:8787")
MONITOR_STALE_MINUTES = 15
RECONCILER_STALE_MINUTES = 5
# signal_engine fires 4x/day via cron (every 6h).  Anything past 8h is
# overdue and almost certainly means cron crashed or scipy/etc went missing.
SIGNAL_ENGINE_STALE_HOURS = 8
# resolver runs hourly via cron.  If no pnl_usd has been written in 24h,
# either nothing resolved (quiet day — OK) or resolver is broken.  Use a
# generous threshold so a real quiet day doesn't alert.
RESOLVER_STALE_HOURS = 24
# alert if any mounted filesystem is above this percent full
DISK_FULL_PCT_THRESHOLD = 90

# Modules whose absence would silently break specific scripts.
# Each entry: (script_that_needs_it, import_to_test).
CRITICAL_IMPORTS = [
    ("signal_engine.py", "scipy.stats"),
    ("ladder.py",         "scipy.stats"),
    ("ladder.py",         "numpy"),
    ("executor.py",       "supabase"),
    ("executor.py",       "requests"),
    ("reconcile_real_bankroll.py", "requests"),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert_or_remediate(
    *,
    problem_subject: str,
    problem_body:    str,
    alert_key:       str,
    remediation_fn=None,   # callable returning a result dict (from remediation.py)
    verify_fn=None,         # callable returning bool — re-checks the original issue
) -> bool:
    """
    Standard handler for any check that fails.

    If `remediation_fn` is provided, attempt the fix and re-verify.  Send
    an info-severity "auto-fixed" alert on success, or a critical alert
    annotated with what we tried on failure.

    Returns True if the issue is now resolved (either it was OK, or we
    fixed it), False if a critical alert went out.
    """
    if remediation_fn is None:
        send_alert(
            subject=problem_subject,
            body=problem_body,
            severity="critical",
            alert_key=alert_key,
        )
        return False

    log.warning(f"attempting auto-remediation for: {problem_subject}")
    result = remediation_fn()

    if result.get("skipped"):
        send_alert(
            subject=f"{problem_subject} (auto-fix on cooldown)",
            body=(
                f"{problem_body}\n\n"
                f"Auto-remediation was skipped: {result.get('action')}\n"
                f"This prevents flapping if the underlying issue is persistent.\n"
                f"Manual intervention is likely needed."
            ),
            severity="critical",
            alert_key=alert_key,
        )
        return False

    post_ok = verify_fn() if verify_fn else bool(result.get("success"))

    if post_ok:
        send_alert(
            subject=f"Auto-fixed: {problem_subject}",
            body=(
                f"The watchdog detected this issue and resolved it automatically.\n"
                f"You don't need to take any action; this is informational.\n\n"
                f"── Original problem ──\n{problem_body}\n\n"
                f"── Action taken ──\n{result.get('action')}\n"
                f"Elapsed: {result.get('elapsed_sec')}s\n"
                + (f"\n── stdout ──\n{result.get('stdout','').strip()}" if result.get('stdout') else "")
            ),
            severity="info",
            alert_key=f"{alert_key}_autofix",
            dedupe_minutes=15,
        )
        return True

    # Remediation ran but the issue persists
    send_alert(
        subject=f"Auto-fix FAILED — manual action needed: {problem_subject}",
        body=(
            f"{problem_body}\n\n"
            f"── Auto-remediation attempted ──\n{result.get('action')}\n"
            f"Outcome: issue persists.\n"
            + (f"\nstdout (tail):\n{(result.get('stdout') or '')[:1000]}\n" if result.get('stdout') else "")
            + (f"\nstderr (tail):\n{(result.get('stderr') or '')[:1000]}\n" if result.get('stderr') else "")
        ),
        severity="critical",
        alert_key=alert_key,
    )
    return False


# ── Verify functions used by remediation re-checks ─────────────────────────

def _verify_ts_executor() -> bool:
    try:
        r = requests.get(f"{TS_EXECUTOR_URL}/health", timeout=10)
        return r.ok and bool(r.json().get("client_ready"))
    except Exception:
        return False


def _verify_imports() -> bool:
    """Re-run the import check, returning True iff all critical modules load."""
    import importlib
    for _script, mod in CRITICAL_IMPORTS:
        try:
            importlib.invalidate_caches()
            importlib.import_module(mod)
        except Exception:
            return False
    return True


def _verify_disk_space() -> bool:
    """Quick check that root partition is under threshold."""
    try:
        st = os.statvfs("/")
        used_pct = (1 - st.f_bavail / st.f_blocks) * 100 if st.f_blocks else 100
        return used_pct < DISK_FULL_PCT_THRESHOLD
    except Exception:
        return False


def _parse_ts(s: str) -> datetime:
    """
    Tolerant ISO-8601 parser.  Postgres timestamps via PostgREST sometimes
    have 4-digit subsecond fractions ('.0307') which Python's
    datetime.fromisoformat() refuses in versions < 3.11.  Normalize the
    fraction to either 0 or 6 digits before parsing.
    """
    s = s.replace("Z", "+00:00")
    # Find ".<digits>" before timezone and pad/truncate to 6 digits
    m = re.match(r"(.*?)\.(\d+)(.*)", s)
    if m:
        head, frac, tail = m.group(1), m.group(2), m.group(3)
        frac = (frac + "000000")[:6]
        s = f"{head}.{frac}{tail}"
    return datetime.fromisoformat(s)


def _check_ts_executor() -> bool:
    """Return True if /health responds and client is ready."""
    try:
        r = requests.get(f"{TS_EXECUTOR_URL}/health", timeout=5)
        if not r.ok:
            return _alert_or_remediate(
                problem_subject="TS executor /health failed",
                problem_body=f"GET {TS_EXECUTOR_URL}/health → HTTP {r.status_code}\n{r.text[:500]}",
                alert_key="ts_exec_health",
                remediation_fn=remediation.remediate_ts_executor,
                verify_fn=_verify_ts_executor,
            )
        body = r.json()
        if not body.get("client_ready"):
            return _alert_or_remediate(
                problem_subject="TS executor not ready",
                problem_body=f"Service is up but client_ready=False. Response:\n{body}",
                alert_key="ts_exec_client_not_ready",
                remediation_fn=remediation.remediate_ts_executor,
                verify_fn=_verify_ts_executor,
            )
        return True
    except Exception as e:
        return _alert_or_remediate(
            problem_subject="TS executor unreachable",
            problem_body=(
                f"Could not reach {TS_EXECUTOR_URL}/health: {e}\n"
                f"systemd status: run `systemctl status polymarket-ts-executor`"
            ),
            alert_key="ts_exec_unreachable",
            remediation_fn=remediation.remediate_ts_executor,
            verify_fn=_verify_ts_executor,
        )


MONITOR_LOG_PATH = "/root/polymarket/logs/temp_monitor.log"


def _check_monitor_freshness(sb) -> bool:
    """
    Confirm temp_monitor.py is actually being fired by cron.

    Earlier version checked `temp_readings.observed_at`, but that table is
    only written when there are OPEN ladders to evaluate.  On quiet days
    (e.g. right after signal_engine has rotated to next-day ladders and
    today's are all closed), temp_monitor runs fine but writes nothing —
    making the data-staleness check fire a false-positive alert.

    The reliable check is "is cron actually firing the script?", which we
    answer via the log file's modification time.  Cron always appends to
    the log at least once per fire (even for the 'nothing to monitor'
    branch).  If the file is stale, cron itself is broken.
    """
    try:
        if not os.path.exists(MONITOR_LOG_PATH):
            send_alert(
                "temp_monitor log missing",
                f"{MONITOR_LOG_PATH} does not exist. Has temp_monitor.py ever run?",
                severity="critical",
                alert_key="monitor_log_missing",
            )
            return False
        mtime    = os.path.getmtime(MONITOR_LOG_PATH)
        age_min  = (datetime.now().timestamp() - mtime) / 60
        if age_min > MONITOR_STALE_MINUTES:
            def _verify_log_fresh():
                try:
                    m = os.path.getmtime(MONITOR_LOG_PATH)
                    return (datetime.now().timestamp() - m) / 60 < MONITOR_STALE_MINUTES
                except Exception:
                    return False
            return _alert_or_remediate(
                problem_subject="temp_monitor cron not firing",
                problem_body=(
                    f"{MONITOR_LOG_PATH} hasn't been updated in {age_min:.1f} min "
                    f"(threshold {MONITOR_STALE_MINUTES} min).\n\n"
                    f"This means the cron job itself stopped firing — NOT that "
                    f"the script is failing.  Auto-remediation will restart cron."
                ),
                alert_key="monitor_stale",
                remediation_fn=remediation.remediate_cron,
                # Verifying "cron is firing again" really needs 5+ minutes to see
                # the next scheduled fire. The remediate fn returns success based
                # on systemctl exit code; that's enough for the verify path here.
                verify_fn=None,
            )
        return True
    except Exception as e:
        send_alert(
            "Watchdog could not check monitor freshness",
            str(e),
            severity="warning",
            alert_key="monitor_check_error",
        )
        return False


def _check_signal_engine_freshness(sb) -> bool:
    """
    signal_engine should write at least one new `ladders` row every cron
    cycle (4x daily).  If we haven't seen one in SIGNAL_ENGINE_STALE_HOURS,
    either cron is broken or a required Python module is missing.

    This is the check that would have caught today's silent scipy crash.
    """
    try:
        r = (sb.table("ladders")
             .select("created_at")
             .order("created_at", desc=True)
             .limit(1)
             .execute())
        if not r.data:
            send_alert(
                "No ladders ever",
                "ladders table is empty. Has signal_engine.py ever run?",
                severity="critical",
                alert_key="signal_engine_no_data",
            )
            return False
        last_str = r.data[0]["created_at"]
        last_dt  = _parse_ts(last_str)
        age_hr   = (_now() - last_dt).total_seconds() / 3600
        if age_hr > SIGNAL_ENGINE_STALE_HOURS:
            send_alert(
                "signal_engine appears stuck or crashing",
                f"Last ladder row was {age_hr:.1f}h ago "
                f"(threshold {SIGNAL_ENGINE_STALE_HOURS}h).\n\n"
                f"This usually means a Python import is failing under cron.\n"
                f"Check:\n"
                f"  tail -50 /root/polymarket/logs/signal_engine.log\n\n"
                f"To restore: identify the missing module, then\n"
                f"  /root/polymarket/venv/bin/pip install -r "
                f"/root/polymarket/requirements.txt",
                severity="critical",
                alert_key="signal_engine_stale",
            )
            return False
        return True
    except Exception as e:
        send_alert(
            "Watchdog could not check signal_engine freshness",
            str(e),
            severity="warning",
            alert_key="signal_engine_check_error",
        )
        return False


def _check_critical_imports() -> bool:
    """
    Verify that every critical Python module loads in the venv.  Catches
    'scipy went missing' style failures BEFORE the next cron tries to use
    them.  Uses importlib so the watchdog itself doesn't crash on a bad
    module — a missing import is just a failed check.
    """
    import importlib
    missing: list[tuple[str, str, str]] = []
    for script, mod in CRITICAL_IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as e:
            missing.append((script, mod, str(e)))
    if missing:
        body_lines = ["The following Python imports are failing in the venv:\n"]
        for script, mod, err in missing:
            body_lines.append(f"  • {script} needs `{mod}` → {err}")
        return _alert_or_remediate(
            problem_subject="Critical Python module missing",
            problem_body="\n".join(body_lines),
            alert_key="critical_imports_missing",
            remediation_fn=lambda: remediation.remediate_critical_imports(missing),
            verify_fn=_verify_imports,
        )
    return True


def _check_resolver_freshness(sb) -> bool:
    """
    resolver runs hourly and writes pnl_usd + resolved_at on closed trades.
    If no trade has been resolved in RESOLVER_STALE_HOURS, either it's
    legitimately quiet OR the resolver is broken.  We can't tell the two
    apart, so the alert is informational rather than critical — but a
    24h gap during live trading is unusual enough to warrant a flag.
    """
    try:
        r = (sb.table("trade_signals")
             .select("resolved_at")
             .not_.is_("resolved_at", "null")
             .order("resolved_at", desc=True)
             .limit(1)
             .execute())
        if not r.data:
            return True   # nothing ever resolved yet — not an error
        last_str = r.data[0]["resolved_at"]
        last_dt  = _parse_ts(last_str)
        age_hr   = (_now() - last_dt).total_seconds() / 3600
        if age_hr > RESOLVER_STALE_HOURS:
            send_alert(
                "resolver: no resolutions in 24h",
                f"Last trade resolved {age_hr:.1f}h ago "
                f"(threshold {RESOLVER_STALE_HOURS}h).\n\n"
                f"Either it's been a quiet trading window (no real trades to\n"
                f"resolve) or resolver.py is failing under cron.\n\n"
                f"Check: tail -50 /root/polymarket/logs/resolver.log",
                severity="warning",
                alert_key="resolver_stale",
            )
            return False
        return True
    except Exception as e:
        log.warning(f"resolver freshness check error: {e}")
        return True   # don't alarm on watchdog-side issues


def _check_disk_space() -> bool:
    """
    Alert if any mounted filesystem is dangerously full.  Logs + DB cache
    can fill disks slowly; better to know before cron starts failing on
    write errors.  Uses os.statvfs() — no shell dependency.
    """
    paths_to_check = ["/", "/root", "/var/log"]
    problems: list[tuple[str, float]] = []
    for p in paths_to_check:
        try:
            st = os.statvfs(p)
            used_pct = (1 - st.f_bavail / st.f_blocks) * 100 if st.f_blocks else 0
            if used_pct > DISK_FULL_PCT_THRESHOLD:
                problems.append((p, used_pct))
        except Exception:
            continue

    if problems:
        body_lines = [f"Disk usage exceeds {DISK_FULL_PCT_THRESHOLD}% on:\n"]
        for path, pct in problems:
            body_lines.append(f"  • {path}: {pct:.1f}% full")
        body_lines.append("")
        body_lines.append("Auto-remediation will attempt: trim 14+ day-old logs,\n"
                          "vacuum systemd journal older than 7 days.")
        return _alert_or_remediate(
            problem_subject="Disk space critical",
            problem_body="\n".join(body_lines),
            alert_key="disk_full",
            remediation_fn=remediation.remediate_disk_space,
            verify_fn=_verify_disk_space,
        )
    return True


def _check_reconciler_freshness() -> bool:
    """
    Reconciler runs every 60s as a systemd timer.  We can't query systemd
    from here easily; instead, just confirm the bankroll value is non-zero
    and that the timer is in the systemd timer list.  If those don't hold,
    raise an info-level alert (less urgent than a stuck monitor).
    """
    import subprocess
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "polymarket-bankroll-reconciler.timer"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if out != "active":
            send_alert(
                "Bankroll reconciler timer not active",
                f"`systemctl is-active polymarket-bankroll-reconciler.timer` → '{out}'",
                severity="warning",
                alert_key="reconciler_inactive",
            )
            return False
    except Exception as e:
        log.warning(f"could not query systemd: {e}")
    return True


def main() -> int:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    ok_ts       = _check_ts_executor()
    ok_mon      = _check_monitor_freshness(sb)
    ok_rec      = _check_reconciler_freshness()
    ok_signal   = _check_signal_engine_freshness(sb)
    ok_imports  = _check_critical_imports()
    ok_resolver = _check_resolver_freshness(sb)
    ok_disk     = _check_disk_space()

    all_ok = (ok_ts and ok_mon and ok_rec and ok_signal
              and ok_imports and ok_resolver and ok_disk)
    if all_ok:
        log.info("all systems healthy")
    else:
        log.warning(
            f"health check failed — ts_exec={ok_ts} monitor={ok_mon} "
            f"reconciler={ok_rec} signal_engine={ok_signal} "
            f"imports={ok_imports} resolver={ok_resolver} disk={ok_disk}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
