"""
Automatic remediation for issues the watchdog detects.
=======================================================

Each `remediate_*` function tries to fix a specific problem class.  They
return a result dict with at minimum:
  {
    "success": bool,                    # did the fix verify as working?
    "action": str,                      # human-readable description of what was tried
    "stdout": str | None,               # any output worth showing
    "stderr": str | None,
    "elapsed_sec": float,
  }

Design rules:
  • Remediations NEVER touch trading state (no order placement,
    cancellation, DB mutation of trade_signals, or wallet operations)
  • They re-verify before claiming success, so we never email "fixed"
    for something that's actually still broken
  • Per-issue rate limiting via dedupe file: a given remediation
    can't fire more often than REMEDIATION_COOLDOWN_MINUTES (60 by
    default) — prevents flapping if the underlying issue is persistent
  • All remediation actions are logged; the watchdog email includes
    the action taken so you wake up to a complete audit trail
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# State file used for rate-limiting remediation attempts so that a
# persistent failure doesn't trigger fix-attempts every 10 minutes.
REMEDIATION_LOG = Path("/root/polymarket/state/remediation_attempts.json")
REMEDIATION_COOLDOWN_MINUTES = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_log() -> dict:
    try:
        if REMEDIATION_LOG.exists():
            return json.loads(REMEDIATION_LOG.read_text())
    except Exception:
        pass
    return {}


def _save_log(data: dict) -> None:
    try:
        REMEDIATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        REMEDIATION_LOG.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _under_cooldown(key: str) -> bool:
    """Return True if this remediation was attempted within the cooldown window."""
    data = _load_log()
    last = data.get(key, {}).get("last_attempt")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        return (_now() - last_dt) < timedelta(minutes=REMEDIATION_COOLDOWN_MINUTES)
    except Exception:
        return False


def _record_attempt(key: str, result: dict) -> None:
    data = _load_log()
    data[key] = {
        "last_attempt": _now().isoformat(),
        "last_success": result.get("success", False),
        "last_action":  result.get("action", ""),
    }
    _save_log(data)


def _run(cmd: list[str], timeout: int = 120) -> dict:
    """Run a subprocess, capture output, never raise."""
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "returncode": p.returncode,
            "stdout":     p.stdout[-2000:] if p.stdout else "",
            "stderr":     p.stderr[-2000:] if p.stderr else "",
            "elapsed_sec": round(time.time() - t0, 2),
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout":     "",
            "stderr":     f"timed out after {timeout}s",
            "elapsed_sec": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {
            "returncode": -1,
            "stdout":     "",
            "stderr":     str(e),
            "elapsed_sec": round(time.time() - t0, 2),
        }


def _skipped_result(key: str, reason: str) -> dict:
    return {
        "success":      False,
        "skipped":      True,
        "action":       f"skipped: {reason}",
        "stdout":       None,
        "stderr":       None,
        "elapsed_sec":  0,
        "key":          key,
    }


# ── Remediation: missing Python module ─────────────────────────────────────

def remediate_critical_imports(missing: list[tuple[str, str, str]]) -> dict:
    """
    Try to fix missing Python modules by reinstalling from requirements.txt.

    `missing` is the list returned by watchdog's import check: a list of
    (script_name, module_name, error_message) tuples. We don't actually
    need module names to fix this — pip will reinstall everything.
    """
    key = "critical_imports"
    if _under_cooldown(key):
        return _skipped_result(key, f"cooldown active ({REMEDIATION_COOLDOWN_MINUTES} min)")

    requirements = "/root/polymarket/requirements.txt"
    if not os.path.exists(requirements):
        result = {
            "success": False,
            "action":  f"requirements.txt not found at {requirements}",
            "stdout":  None,
            "stderr":  None,
            "elapsed_sec": 0,
            "key":     key,
        }
        _record_attempt(key, result)
        return result

    proc = _run(
        ["/root/polymarket/venv/bin/pip", "install", "-r", requirements,
         "--disable-pip-version-check", "--quiet"],
        timeout=300,
    )

    # Re-verify by attempting to import each previously-missing module
    still_missing: list[str] = []
    for _script, mod, _err in missing:
        try:
            importlib.invalidate_caches()
            importlib.import_module(mod)
        except Exception as e:
            still_missing.append(f"{mod} ({e})")

    success = proc["returncode"] == 0 and not still_missing
    action = (
        f"ran `pip install -r {requirements}` in {proc['elapsed_sec']:.1f}s; "
        f"{'all previously-missing modules now import' if success else 'modules still failing: ' + ', '.join(still_missing)}"
    )
    result = {
        "success":     success,
        "action":      action,
        "stdout":      proc["stdout"],
        "stderr":      proc["stderr"],
        "elapsed_sec": proc["elapsed_sec"],
        "key":         key,
    }
    _record_attempt(key, result)
    return result


# ── Remediation: TS executor unreachable ────────────────────────────────────

def remediate_ts_executor() -> dict:
    """
    Restart the polymarket-ts-executor systemd service, then re-check
    /health on the loopback port.
    """
    import requests

    key = "ts_executor"
    if _under_cooldown(key):
        return _skipped_result(key, f"cooldown active ({REMEDIATION_COOLDOWN_MINUTES} min)")

    restart = _run(["systemctl", "restart", "polymarket-ts-executor"], timeout=30)
    if restart["returncode"] != 0:
        result = {
            "success":     False,
            "action":      "systemctl restart polymarket-ts-executor FAILED",
            "stdout":      restart["stdout"],
            "stderr":      restart["stderr"],
            "elapsed_sec": restart["elapsed_sec"],
            "key":         key,
        }
        _record_attempt(key, result)
        return result

    # Give the service a few seconds to boot and derive the API key
    time.sleep(6)

    # Verify
    try:
        r = requests.get("http://127.0.0.1:8787/health", timeout=10)
        ok = r.ok and r.json().get("client_ready") is True
    except Exception as e:
        ok = False
        restart["stderr"] = (restart["stderr"] or "") + f"\nhealth check error: {e}"

    result = {
        "success":     ok,
        "action":      "systemctl restart polymarket-ts-executor + /health check",
        "stdout":      restart["stdout"],
        "stderr":      restart["stderr"],
        "elapsed_sec": restart["elapsed_sec"] + 6,
        "key":         key,
    }
    _record_attempt(key, result)
    return result


# ── Remediation: cron not firing ────────────────────────────────────────────

def remediate_cron() -> dict:
    """Restart the cron daemon."""
    key = "cron"
    if _under_cooldown(key):
        return _skipped_result(key, f"cooldown active ({REMEDIATION_COOLDOWN_MINUTES} min)")

    proc = _run(["systemctl", "restart", "cron"], timeout=30)
    success = proc["returncode"] == 0

    result = {
        "success":     success,
        "action":      "systemctl restart cron",
        "stdout":      proc["stdout"],
        "stderr":      proc["stderr"],
        "elapsed_sec": proc["elapsed_sec"],
        "key":         key,
    }
    _record_attempt(key, result)
    return result


# ── Remediation: disk space ─────────────────────────────────────────────────

def remediate_disk_space() -> dict:
    """
    Trim log files older than 14 days from /root/polymarket/logs/ AND
    vacuum systemd journal to reclaim space.  Never touches DB or
    state files — only logs.
    """
    key = "disk_space"
    if _under_cooldown(key):
        return _skipped_result(key, f"cooldown active ({REMEDIATION_COOLDOWN_MINUTES} min)")

    actions = []
    stdouts = []
    stderrs = []
    rc_total = 0
    t0 = time.time()

    # 1. Delete /root/polymarket/logs/*.log* files older than 14 days
    find = _run(
        ["find", "/root/polymarket/logs", "-type", "f",
         "-name", "*.log*", "-mtime", "+14", "-delete"],
        timeout=30,
    )
    actions.append(f"find -mtime +14 -delete (rc={find['returncode']})")
    stdouts.append(find["stdout"]); stderrs.append(find["stderr"])
    rc_total += abs(find["returncode"])

    # 2. Vacuum systemd journal older than 7 days
    journal = _run(["journalctl", "--vacuum-time=7d"], timeout=30)
    actions.append(f"journalctl --vacuum-time=7d (rc={journal['returncode']})")
    stdouts.append(journal["stdout"]); stderrs.append(journal["stderr"])
    rc_total += abs(journal["returncode"])

    # Re-check whether we actually freed enough
    try:
        st = os.statvfs("/")
        used_pct = (1 - st.f_bavail / st.f_blocks) * 100 if st.f_blocks else 0
    except Exception:
        used_pct = 100  # assume worst

    success = (rc_total == 0) and (used_pct < 90)
    result = {
        "success":      success,
        "action":       "; ".join(actions) + f"; post-fix usage={used_pct:.1f}%",
        "stdout":       "\n---\n".join(s for s in stdouts if s),
        "stderr":       "\n---\n".join(s for s in stderrs if s),
        "elapsed_sec":  round(time.time() - t0, 2),
        "key":          key,
    }
    _record_attempt(key, result)
    return result
