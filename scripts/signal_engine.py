"""Main signal engine: fetch forecasts → build ladders → store signals."""
import sys
import uuid
import argparse
import logging
from datetime import datetime, timezone
from supabase import create_client
import schedule
import time

from config import CITIES, SUPABASE_URL, SUPABASE_KEY, CITY_UNITS
from fetch_forecasts import run_for_city
from fetch_markets import fetch_markets_for_city
from ladder import build_ladder, ladder_summary
from resolver import resolve_signals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def get_latest_forecast(city: str, forecast_date: str) -> dict | None:
    res = (
        sb.table("ensemble_forecasts")
        .select("*")
        .eq("city", city)
        .eq("forecast_date", forecast_date)
        .order("model_run", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _get_phase1_cap() -> float:
    """
    Phase 1 is now observation-only. All positions are set to $0.01 so this
    cap is never meaningfully triggered. Returns a large sentinel value.
    """
    return 9999.0


def run_signal_pipeline(
    paper: bool = True,
    dry_run: bool = False,
    ladder_config: dict | None = None,
) -> list[dict]:
    run_time = datetime.now(timezone.utc)
    log.info(f"=== WEATHER TRADER — LADDER ENGINE === {run_time.strftime('%Y-%m-%d %H:%M')} UTC")
    log.info(f"Mode: {'PAPER' if paper else 'LIVE'} | dry_run={dry_run}")

    # Resolve yesterday's closed markets before computing today's signals
    if not dry_run:
        log.info("--- Resolving closed markets ---")
        resolve_signals(log=log)
        log.info("--- Resolution complete ---")

    all_rungs: list[dict] = []
    # Deferred DB writes: collect (ladder_row, [signal_rows]) pairs.
    # Scaling is applied in-memory BEFORE any DB insert so stored amounts are correct.
    pending_ladders: list[tuple[dict, list[dict]]] = []

    for city in CITIES:
        log.info(f"Processing {city}...")

        run_for_city(city)

        markets = fetch_markets_for_city(city)
        if not markets:
            log.info(f"  {city}: no markets found")
            continue

        for mkt in markets:
            forecast_date = mkt["date"] if len(mkt["date"]) == 10 else run_time.strftime("%Y-%m-%d")
            forecast = get_latest_forecast(city, forecast_date)
            if not forecast:
                log.info(f"  {city} {forecast_date}: no forecast data")
                continue

            mean_c = forecast["mean_high"]
            std_c  = forecast["std_high"]
            buckets = mkt.get("buckets", [])
            if not buckets:
                log.info(f"  {city} {forecast_date}: no parsed buckets")
                continue

            # Combine GFS + ECMWF members (already delta-adjusted in fetch_forecasts)
            gfs_members   = forecast.get("raw_members")   or []
            ecmwf_members = forecast.get("ecmwf_members") or []
            members_c     = gfs_members + ecmwf_members if gfs_members else None
            spread_c      = forecast.get("consensus_spread_c")

            rungs = build_ladder(
                mean_c, std_c, buckets, city,
                config=ladder_config,
                members_c=members_c,
                consensus_spread_c=spread_c,
            )
            if not rungs:
                skip_reason = (
                    f"spread={spread_c:.1f}°C > max" if spread_c and spread_c > 3.0
                    else "no eligible rungs"
                )
                log.info(f"  {city} {forecast_date}: {skip_reason}")
                continue

            n_members = len(members_c) if members_c else 0
            spread_str = f"  spread={spread_c:.1f}C" if spread_c is not None else ""
            summary = ladder_summary(rungs, mean_c, std_c, city)
            log.info(
                f"  {city} {forecast_date}: {summary['num_rungs']} rungs "
                f"({summary['num_core']} YES-core, {summary['num_no']} NO, {summary['num_wings']} wings) "
                f"total=${summary['total_usd']:.2f}  forecast={summary['mean']} +/-{summary['std']}"
                f"  members={n_members}{spread_str}"
            )

            # Skip if a ladder already exists for this city/date (avoid duplicates across runs)
            existing = (
                sb.table("ladders")
                .select("id")
                .eq("city", city)
                .eq("forecast_date", forecast_date)
                .eq("status", "open")
                .limit(1)
                .execute()
            )
            if existing.data and not dry_run:
                log.info(f"  {city} {forecast_date}: ladder already exists, skipping")
                continue

            # Build ladder record (not yet written to DB)
            ladder_id = str(uuid.uuid4())
            unit = CITY_UNITS.get(city, "C")
            import json as _json
            ladder_row = {
                "id":             ladder_id,
                "city":           city,
                "forecast_date":  forecast_date,
                "event_slug":     mkt.get("event_slug", ""),
                "mean_high":      round(mean_c, 2),
                "std_high":       round(std_c, 2),
                "unit":           unit,
                "num_rungs":      summary["num_rungs"],
                "num_core":       summary["num_core"],
                "num_wings":      summary["num_wings"],
                "total_cost_usd": summary["total_usd"],
                "is_paper":       paper,
                "status":         "open",
                # Store buckets so temp_monitor can look them up after market closes
                "buckets_json":   _json.dumps([
                    {k: b[k] for k in ("label","low","high","unit") if k in b}
                    for b in buckets
                ]),
            }

            # Build signal rows (not yet written to DB)
            ladder_signals: list[dict] = []
            for rung in rungs:
                side  = rung.get("side", "YES")
                color = {"core": "🟢", "wing": "🔵", "no": "🔴"}.get(rung["rung_type"], "⚪")
                yes_ref = rung["yes_price"] if side == "YES" else rung.get("yes_price_ref", rung["yes_price"])
                log.info(
                    f"    {color} [{rung['rung_type']:4}] {side} {rung['label']:<12} "
                    f"model={rung['model_prob']*100:5.1f}%  "
                    f"yes={yes_ref*100:5.1f}c  "
                    f"pay={rung['market_price']*100:5.1f}c  "
                    f"ev={rung['ev']:+.3f}  "
                    f"dist={rung['distance_sigma']:.1f}s  "
                    f"${rung['size_usd']:.2f}"
                )

                signal_row = {
                    "city":                  city,
                    "market_id":             rung.get("market_id", ""),
                    "condition_id":          rung.get("condition_id", ""),
                    "outcome":               rung["label"],
                    "side":                  side,
                    "market_price":          rung["market_price"],   # NO price for NO rungs
                    "model_probability":     rung["model_prob"],
                    "corrected_probability": rung["model_prob"],
                    "edge":                  rung["ev"],
                    "delta_mean":            0.0,
                    "delta_std":             round(std_c, 2),
                    "confidence":            rung["model_prob"],  # corrected model probability (not EV)
                    # Phase 1 is observation-only: $0.01 symbolic size, no capital deployed.
                    # This preserves ladder structure for Phase 2 and Brier score tracking
                    # while committing zero meaningful capital.
                    "recommended_position":  0.01,
                    "forecast_date":         forecast_date,
                    "market_question":       rung.get("question", ""),
                    "event_slug":            mkt.get("event_slug", ""),
                    "mean_high":             round(mean_c, 2),
                    "std_high":              round(std_c, 2),
                    "signal_time":           run_time.isoformat(),
                    "traded":                False,
                    # Ladder-specific columns
                    "ladder_id":             ladder_id,
                    "rung_type":             rung["rung_type"],
                    "distance_sigma":        rung["distance_sigma"],
                    "signal_phase":          "phase1",
                }
                ladder_signals.append(signal_row)
                all_rungs.append(signal_row)

            pending_ladders.append((ladder_row, ladder_signals))

    # ── Apply total run cap BEFORE writing to DB ──────────────────────────────
    total_usd = sum(r["recommended_position"] for r in all_rungs)
    TOTAL_RUN_CAP_USD = _get_phase1_cap()
    if total_usd > TOTAL_RUN_CAP_USD:
        scale = TOTAL_RUN_CAP_USD / total_usd
        for r in all_rungs:
            r["recommended_position"] = round(r["recommended_position"] * scale, 2)
        log.info(f"Run cap applied: ${total_usd:.2f} → ${TOTAL_RUN_CAP_USD:.2f} (×{scale:.2f})")

    # ── Now write scaled values to DB ─────────────────────────────────────────
    if not dry_run:
        try:
            from executor import place_order as _place_order
            _executor_available = True
        except Exception:
            _executor_available = False

        for ladder_row, ladder_signals in pending_ladders:
            # Update ladder total_cost_usd to reflect post-scaling amounts
            ladder_row["total_cost_usd"] = round(
                sum(s["recommended_position"] for s in ladder_signals), 2
            )
            sb.table("ladders").insert(ladder_row).execute()
            for signal_row in ladder_signals:
                res = sb.table("trade_signals").insert(signal_row).execute()
                signal_id = res.data[0]["id"] if res.data else None

                # Phase 1 is observation-only — no order placed, no capital deployed.
                # Phase 2 trades are placed by phase2_engine.py when a bracket locks.
                if signal_id:
                    sb.table("trade_signals").update({
                        "traded":       True,
                        "order_status": "observation",
                    }).eq("id", signal_id).execute()

    total_deployed = sum(r["recommended_position"] for r in all_rungs)
    log.info(f"\n=== {len(all_rungs)} rungs | ${total_deployed:.2f} deployed across {len(CITIES)} cities ===")

    # Write 4x-daily health snapshot (non-fatal if it fails)
    if not dry_run:
        try:
            from reporter import write_report
            write_report()
        except Exception as _rep_err:
            log.warning(f"[reporter] snapshot skipped: {_rep_err}")

    return all_rungs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live",     action="store_true",  help="Execute live (default: paper)")
    parser.add_argument("--dry-run",  action="store_true",  help="Skip all DB writes")
    parser.add_argument("--schedule", action="store_true",  help="Run on 6-hour GFS schedule")
    # Ladder tuning knobs exposed as CLI args
    parser.add_argument("--core-sigma",    type=float, default=None)
    parser.add_argument("--wing-sigma",    type=float, default=None)
    parser.add_argument("--max-market",    type=float, default=None, dest="max_market_usd")
    args = parser.parse_args()

    ladder_config: dict = {}
    if args.core_sigma    is not None: ladder_config["core_sigma"]    = args.core_sigma
    if args.wing_sigma    is not None: ladder_config["wing_sigma"]    = args.wing_sigma
    if args.max_market_usd is not None: ladder_config["max_market_usd"] = args.max_market_usd

    run_kwargs = dict(
        paper=not args.live,
        dry_run=args.dry_run,
        ladder_config=ladder_config or None,
    )

    if args.schedule:
        for t in ["03:30", "09:30", "15:30", "21:30"]:
            schedule.every().day.at(t).do(run_signal_pipeline, **run_kwargs)
        log.info("Scheduler running. Next GFS windows: 03:30, 09:30, 15:30, 21:30 UTC...")
        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        run_signal_pipeline(**run_kwargs)


if __name__ == "__main__":
    main()
