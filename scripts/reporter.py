"""
reporter.py — Generate a 4x-daily health snapshot after each signal_engine run.

Called automatically at the end of run_signal_pipeline() in signal_engine.py.
Can also be run standalone:
    python scripts/reporter.py

What it computes
----------------
- Execution health: signals generated, orders placed, phase breakdown
- Delta calibration: avg delta_c, which cities are still at 0 (uncalibrated)
- 7-day rolling performance: win rate, ROI, phase 1 vs phase 2 split
- 30-day go-live criteria: Brier score, worst-city Brier, total predictions, win rate
- Per-city metrics: delta_c, 7-day win rate, ROI, signal count, health status
- "Flag for review": city has been RED in 16+ of the last 20 run reports (~4 days)
- Projected go-live ETA: simple linear projection toward June 1st criteria
"""

import logging
from datetime import datetime, timezone, timedelta, date
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, CITIES

log = logging.getLogger(__name__)
sb = create_client(SUPABASE_URL, SUPABASE_KEY)

GO_LIVE_TARGET = date(2026, 6, 1)

# Flag a city for review if it has been RED in this many of the last 20 run snapshots
# 20 runs ≈ 5 days × 4 runs/day; 16/20 = consistently red for 4+ days
FLAG_REVIEW_THRESHOLD = 16


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_slot(run_time: datetime) -> str:
    """Return the nearest scheduled run slot label for a given UTC datetime.
    Must match the SLOTS constant in ReportsTab.tsx exactly:
      02:05 — after resolver       (9:05 PM Monterrey CST)
      06:05 — after signal_engine  (12:05 AM Monterrey CST)
      15:30 — midday snapshot      (9:30 AM Monterrey CST)
      21:30 — evening snapshot     (3:30 PM Monterrey CST)
    """
    slots = [(2, 5), (6, 5), (15, 30), (21, 30)]
    h, m = run_time.hour, run_time.minute
    best = min(slots, key=lambda s: abs((s[0] * 60 + s[1]) - (h * 60 + m)))
    return f"{best[0]:02d}:{best[1]:02d}"


def _is_win(r: dict) -> bool:
    """True if the signal was a winning bet.

    Normalises actual_outcome before comparing — supabase-py may return Postgres
    booleans as Python booleans (True/False), integers (1/0), or lowercase strings
    ('true'/'false') depending on the driver version.
    """
    raw = r.get("actual_outcome")
    if raw is None:
        return False
    if isinstance(raw, str):
        outcome = raw.lower() == "true"
    else:
        outcome = bool(raw)

    side = r.get("side")
    return (side == "YES" and outcome) or (side == "NO" and not outcome)


def _project_go_live(
    total_preds: int,
    brier: float | None,
    worst_brier: float | None,
    win_rate: float | None,
) -> date | None:
    """
    Linear projection of when all 4 go-live criteria will be met.

    Criteria:
      1. total_predictions_30d >= 200
      2. brier_score_30d < 0.15
      3. worst_city_brier <= 0.22
      4. win_rate_30d > 65%

    Returns None if there is no data to project from.
    """
    today = date.today()

    if total_preds == 0 or brier is None:
        return None

    criteria_met = sum([
        total_preds >= 200,
        brier < 0.15,
        (worst_brier or 1.0) <= 0.22,
        (win_rate or 0) > 65,
    ])

    if criteria_met == 4:
        return today

    days_estimates = []

    # Criterion 1: prediction count.  Conservative: ~30 new resolved predictions/day
    if total_preds < 200:
        days_estimates.append(max(1, (200 - total_preds) // 30))

    # Criterion 2: Brier score < 0.15.  Delta correction improves ~0.007/week.
    if brier is not None and brier >= 0.15:
        improvement_per_day = 0.001          # ~0.007/week
        days_estimates.append(max(1, int((brier - 0.14) / improvement_per_day)))

    # Criterion 3: worst city Brier <= 0.22  (similar rate)
    if worst_brier is not None and worst_brier > 0.22:
        improvement_per_day = 0.001
        days_estimates.append(max(1, int((worst_brier - 0.21) / improvement_per_day)))

    # Criterion 4: win rate > 65%.  Hard to project without trend data; give 14 days.
    if win_rate is None or win_rate <= 65:
        days_estimates.append(14)

    if not days_estimates:
        return today

    days_needed = max(days_estimates)
    projected = today + timedelta(days=days_needed)

    # Cap at end of 2026; never return a date before today
    return max(today, min(projected, date(2026, 12, 31)))


# ── Core report builder ───────────────────────────────────────────────────────

def generate_report() -> dict:
    """Query DB and compute a complete run health snapshot."""
    now      = datetime.now(timezone.utc)
    today    = now.date()
    today_str        = today.isoformat()
    seven_days_ago   = (today - timedelta(days=7)).isoformat()
    thirty_days_ago  = (today - timedelta(days=30)).isoformat()

    # ── 1. Signals generated today (rolling 24-hour window) ───────────────────
    # signal_engine runs once daily; a 24h window ensures afternoon/evening
    # snapshots still show today's actual signal activity rather than 0.
    twenty_four_hours_ago = (now - timedelta(hours=24)).isoformat()
    run_sigs_res = sb.table("trade_signals").select(
        "city, signal_phase, order_status, traded"
    ).gte("signal_time", twenty_four_hours_ago).execute()
    run_sigs = run_sigs_res.data or []

    signals_generated = len(run_sigs)
    orders_placed     = sum(1 for s in run_sigs if s.get("traded"))
    orders_filled     = sum(1 for s in run_sigs if s.get("order_status") == "filled")
    orders_queued     = sum(1 for s in run_sigs if s.get("order_status") in ("queued", "pending", "paper"))
    orders_failed     = sum(1 for s in run_sigs if s.get("order_status") == "failed")
    phase1_signals    = sum(1 for s in run_sigs if s.get("signal_phase") == "phase1")
    phase2_signals    = sum(1 for s in run_sigs if s.get("signal_phase") in ("phase2", "phase2_sweep"))

    cities_with_sigs  = {s["city"] for s in run_sigs}
    cities_no_signals = [c for c in CITIES if c not in cities_with_sigs]

    # ── 2. Phase 2 fires today ────────────────────────────────────────────────
    p2_today_res = sb.table("trade_signals").select(
        "city, confidence, outcome"
    ).gte("signal_time", today_str).eq("signal_phase", "phase2").execute()
    phase2_fires = [
        {
            "city":       r["city"],
            "confidence": round(r.get("confidence") or 0, 3),
            "bracket":    r.get("outcome") or "",
        }
        for r in (p2_today_res.data or [])
    ]

    # ── 3. Delta calibration ──────────────────────────────────────────────────
    delta_res = sb.table("resolution_stations").select(
        "city, delta_c, delta_samples"
    ).execute()
    delta_map: dict[str, tuple[float, int]] = {
        r["city"]: (float(r.get("delta_c") or 0.0), int(r.get("delta_samples") or 0))
        for r in (delta_res.data or [])
    }

    cities_uncalibrated = [c for c in CITIES if delta_map.get(c, (0.0, 0))[0] == 0.0]
    avg_delta = (
        sum(abs(delta_map[c][0]) for c in CITIES if c in delta_map)
        / max(len(delta_map), 1)
    )

    # ── 4. 7-day resolved performance ─────────────────────────────────────────
    resolved_7d_res = sb.table("trade_signals").select(
        "city, side, actual_outcome, pnl_usd, signal_phase, recommended_position"
    ).gte("forecast_date", seven_days_ago).not_.is_("pnl_usd", "null").execute()
    resolved_7d = resolved_7d_res.data or []

    def _wr(rows: list[dict]) -> float | None:
        if not rows:
            return None
        return sum(1 for r in rows if _is_win(r)) / len(rows) * 100

    def _roi(rows: list[dict]) -> float | None:
        cost = sum(r.get("recommended_position") or 0 for r in rows)
        pnl  = sum(r.get("pnl_usd")             or 0 for r in rows)
        return (pnl / cost * 100) if cost > 0 else None

    win_rate_7d  = _wr(resolved_7d)
    roi_7d       = _roi(resolved_7d)
    p1_7d        = [r for r in resolved_7d if r.get("signal_phase") == "phase1"]
    p2_7d        = [r for r in resolved_7d if r.get("signal_phase") in ("phase2", "phase2_sweep")]
    win_rate_p1  = _wr(p1_7d)
    win_rate_p2  = _wr(p2_7d)

    # ── 5. 30-day go-live criteria ────────────────────────────────────────────
    # Explicit .limit(50_000) on the next two queries — without it,
    # Supabase silently caps the reply at 1000 rows, so total_preds
    # plateaus and the worst-city Brier is computed over an arbitrary
    # 1000-row slice instead of the full 30-day sample.
    scored_30d_res = sb.table("trade_signals").select(
        "city, brier_score"
    ).gte("signal_time", thirty_days_ago).not_.is_("brier_score", "null").limit(50_000).execute()
    scored_30d  = scored_30d_res.data or []
    total_preds = len(scored_30d)

    brier_30d = (
        sum(r["brier_score"] for r in scored_30d) / total_preds
        if total_preds > 0 else None
    )

    by_city_brier: dict[str, list[float]] = {}
    for r in scored_30d:
        by_city_brier.setdefault(r["city"], []).append(r["brier_score"])

    worst_city_name  = None
    worst_city_brier = None
    if by_city_brier:
        city_b = {c: sum(v) / len(v) for c, v in by_city_brier.items()}
        worst_city_name  = max(city_b, key=city_b.__getitem__)
        worst_city_brier = city_b[worst_city_name]

    resolved_30d_res = sb.table("trade_signals").select(
        "side, actual_outcome, pnl_usd, recommended_position"
    ).gte("forecast_date", thirty_days_ago).not_.is_("pnl_usd", "null").limit(50_000).execute()
    resolved_30d = resolved_30d_res.data or []
    win_rate_30d = _wr(resolved_30d)

    criteria_met = sum([
        total_preds >= 200,
        brier_30d is not None   and brier_30d < 0.15,
        worst_city_brier is not None and worst_city_brier <= 0.22,
        win_rate_30d is not None and win_rate_30d > 65,
    ])

    projected_gl = _project_go_live(total_preds, brier_30d, worst_city_brier, win_rate_30d)

    # ── 6. Flag cities for review using past run_reports ──────────────────────
    flag_map: dict[str, bool] = {}
    try:
        recent_res = sb.table("run_reports").select("city_metrics").order(
            "run_time", desc=True
        ).limit(20).execute()
        city_red_counts: dict[str, int] = {}
        for row in (recent_res.data or []):
            for cm in (row.get("city_metrics") or []):
                if cm.get("status") == "red":
                    c = cm["city"]
                    city_red_counts[c] = city_red_counts.get(c, 0) + 1
        for c, cnt in city_red_counts.items():
            if cnt >= FLAG_REVIEW_THRESHOLD:
                flag_map[c] = True
    except Exception as e:
        log.warning(f"[reporter] Could not compute flag_review counts: {e}")

    # ── 7. Per-city health metrics ────────────────────────────────────────────
    by_city_resolved: dict[str, list[dict]] = {}
    for r in resolved_7d:
        by_city_resolved.setdefault(r["city"], []).append(r)

    city_metrics: list[dict] = []
    for city in CITIES:
        city_rows   = by_city_resolved.get(city, [])
        c_wr        = _wr(city_rows)
        c_roi       = _roi(city_rows)
        c_pnl       = sum(r.get("pnl_usd") or 0 for r in city_rows)
        c_delta, c_samples = delta_map.get(city, (0.0, 0))

        # City health status
        if c_wr is None:
            status = "gray"
        elif c_wr >= 55 and (c_roi is None or c_roi >= 0):
            status = "green"
        elif c_wr >= 40 or (c_roi is not None and c_roi >= -20):
            status = "yellow"
        else:
            status = "red"

        city_metrics.append({
            "city":           city,
            "delta_c":        round(c_delta, 3),
            "delta_samples":  c_samples,
            "win_rate_7d":    round(c_wr, 1)  if c_wr  is not None else None,
            "roi_7d":         round(c_roi, 1) if c_roi is not None else None,
            "signals_7d":     len(city_rows),
            "pnl_7d":         round(c_pnl, 2),
            "status":         status,
            "flag_review":    flag_map.get(city, False),
        })

    # ── 8. Overall health score ───────────────────────────────────────────────
    red_cities    = sum(1 for cm in city_metrics if cm["status"] == "red")
    executor_rate = (orders_placed / signals_generated * 100) if signals_generated > 0 else 100.0

    if red_cities >= 10 or executor_rate < 20 or orders_failed > 5:
        health_score = "red"
    elif red_cities >= 5 or executor_rate < 50 or (win_rate_7d is not None and win_rate_7d < 35):
        health_score = "yellow"
    else:
        health_score = "green"

    # ── 9. Summary line ───────────────────────────────────────────────────────
    wr_str    = f"{win_rate_7d:.0f}%" if win_rate_7d is not None else "no data"
    roi_str   = f" · {roi_7d:+.0f}% ROI"  if roi_7d  is not None else ""
    delta_str = f"Δ avg {avg_delta:+.2f}°"
    summary   = (
        f"{signals_generated} signals · {orders_placed} orders placed · "
        f"{delta_str} · 7d win: {wr_str}{roi_str} · {criteria_met}/4 go-live"
    )

    return {
        "run_time":             now.isoformat(),
        "run_slot":             _run_slot(now),
        "health_score":         health_score,
        "summary":              summary,
        "signals_generated":    signals_generated,
        "orders_placed":        orders_placed,
        "orders_filled":        orders_filled,
        "orders_queued":        orders_queued,
        "orders_failed":        orders_failed,
        "cities_no_signals":    cities_no_signals,
        "phase1_signals":       phase1_signals,
        "phase2_signals":       phase2_signals,
        "phase2_fires":         phase2_fires,
        "avg_delta_c":          round(avg_delta, 3),
        "cities_uncalibrated":  cities_uncalibrated,
        "win_rate_7d":          round(win_rate_7d,  1) if win_rate_7d  is not None else None,
        "roi_7d":               round(roi_7d,       1) if roi_7d       is not None else None,
        "win_rate_phase1_7d":   round(win_rate_p1,  1) if win_rate_p1  is not None else None,
        "win_rate_phase2_7d":   round(win_rate_p2,  1) if win_rate_p2  is not None else None,
        "resolved_count_7d":    len(resolved_7d),
        "total_predictions_30d": total_preds,
        "brier_score_30d":      round(brier_30d,       4) if brier_30d       is not None else None,
        "worst_city_brier":     round(worst_city_brier, 4) if worst_city_brier is not None else None,
        "worst_city_name":      worst_city_name,
        "win_rate_30d":         round(win_rate_30d,    1) if win_rate_30d    is not None else None,
        "criteria_met":         criteria_met,
        "projected_go_live":    projected_gl.isoformat() if projected_gl else None,
        "city_metrics":         city_metrics,
    }


def write_report() -> None:
    """Generate and persist a run health snapshot to run_reports."""
    try:
        report = generate_report()
        sb.table("run_reports").insert(report).execute()
        log.info(
            f"[reporter] {report['health_score'].upper()} — {report['summary']}"
        )
    except Exception as e:
        # Never crash signal_engine — reporter failure is non-fatal
        log.warning(f"[reporter] Failed to write snapshot: {e}")


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s UTC | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    write_report()
    print("Done.")
