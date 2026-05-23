"""FastAPI entry point for the Polymarket trader analyzer worker.

Bind to 127.0.0.1:8001. Caddy proxies external HTTPS traffic in.
All endpoints require Authorization: Bearer <ANALYZER_AUTH_TOKEN>.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .cache import (
    get_annotations, get_recent_run, list_recent_wallets, list_watchlist,
    purge_trader, save_commentary, save_run, set_annotations,
    watchlist_add, watchlist_remove, watchlist_set_label,
    watchlist_update_annotations,
)
from . import jobs as jobs_mod
from .claude_commentary import commentary as run_commentary
from .config import AUTH_TOKEN, HOST, PORT
from .profile import build_profile, resolve_username
from .weather_dissect import dissect

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | analyzer | %(message)s",
)
log = logging.getLogger("analyzer")

app = FastAPI(title="Polymarket Trader Analyzer", version="0.1.0")

# CORS — allow the deployed frontend and localhost dev. Override with
# ANALYZER_CORS_ORIGINS env var (comma-separated) when adding domains.
import os as _os
_origins_env = _os.environ.get(
    "ANALYZER_CORS_ORIGINS",
    "https://weatherornotbot.netlify.app,http://localhost:5173,http://127.0.0.1:5173",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins_env.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


def require_auth(authorization: str | None = Header(None)) -> None:
    if not AUTH_TOKEN:
        raise HTTPException(500, "server misconfigured: ANALYZER_AUTH_TOKEN not set")
    if not authorization or authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(401, "unauthorized")


class AnalyzeRequest(BaseModel):
    wallet: str | None = None
    username: str | None = None
    force_refresh: bool = False
    # When True, also run the toolkit's audit-grade compute_address_pnl,
    # which re-fetches every activity type and adds ~60-120s on whale wallets.
    # Default off — the standard stats from TRADE activity are already rich.
    deep: bool = False


class CommentaryRequest(BaseModel):
    run_id: int | None = None
    wallet: str | None = None
    mode: str = "standard"  # "standard" | "deep"


class WatchlistAddRequest(BaseModel):
    wallet: str
    label: str = ""


class WatchlistLabelRequest(BaseModel):
    """PATCH /watchlist/:wallet body — updates the watchlist label only.
    Headline + notes have their own /annotations endpoint."""
    label: str | None = None


class AnnotationRequest(BaseModel):
    """PATCH /annotations/:wallet body — both fields optional, send what
    you want to change.  Available for ANY analyzed wallet (no follow
    required)."""
    headline: str | None = None
    notes:    str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.2.0"}


@app.get("/history")
def history(limit: int = 50, _=Depends(require_auth)) -> dict[str, Any]:
    """Recently analyzed wallets (one row per wallet, latest run only)."""
    return {"runs": list_recent_wallets(limit=min(max(limit, 1), 200))}


@app.get("/watchlist")
def watchlist_get(_=Depends(require_auth)) -> dict[str, Any]:
    return {"entries": list_watchlist()}


@app.post("/watchlist")
def watchlist_post(req: WatchlistAddRequest, _=Depends(require_auth)) -> dict[str, Any]:
    wallet = req.wallet.lower().strip()
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(400, f"invalid wallet: {wallet!r}")
    ok = watchlist_add(wallet, req.label)
    if not ok:
        raise HTTPException(500, "watchlist add failed")
    return {"wallet": wallet, "followed": True}


@app.delete("/watchlist/{wallet}")
def watchlist_delete(wallet: str, _=Depends(require_auth)) -> dict[str, Any]:
    ok = watchlist_remove(wallet)
    if not ok:
        raise HTTPException(500, "watchlist remove failed")
    return {"wallet": wallet.lower(), "followed": False}


@app.patch("/watchlist/{wallet}")
def watchlist_patch(wallet: str, req: WatchlistLabelRequest,
                    _=Depends(require_auth)) -> dict[str, Any]:
    """Patch the watchlist label.  (Headline + notes use /annotations.)"""
    if req.label is None:
        raise HTTPException(400, "no fields to update")
    ok = watchlist_update_annotations(wallet, label=req.label)
    if not ok:
        raise HTTPException(500, "watchlist update failed")
    return {"wallet": wallet.lower(), "label": req.label}


# ── Annotations (headline + notes) ───────────────────────────────────────
# Available for any analyzed wallet — no follow required.

@app.get("/annotations/{wallet}")
def annotations_get(wallet: str, _=Depends(require_auth)) -> dict[str, Any]:
    ann = get_annotations(wallet)
    return {"wallet": wallet.lower(), **ann}


@app.patch("/annotations/{wallet}")
def annotations_patch(wallet: str, req: AnnotationRequest,
                      _=Depends(require_auth)) -> dict[str, Any]:
    if req.headline is None and req.notes is None:
        raise HTTPException(400, "no fields to update")
    ok = set_annotations(wallet, headline=req.headline, notes=req.notes)
    if not ok:
        raise HTTPException(500, "annotation update failed")
    return {
        "wallet":   wallet.lower(),
        "headline": req.headline,
        "notes":    req.notes,
    }


@app.delete("/trader/{wallet}")
def trader_purge(wallet: str, _=Depends(require_auth)) -> dict[str, Any]:
    """Permanently delete all runs, commentary, and watchlist data for a wallet."""
    counts = purge_trader(wallet)
    return {"wallet": wallet.lower(), **counts}


def _resolve_target(req: AnalyzeRequest) -> str:
    """Translate the request into a normalised 0x wallet address, or raise."""
    if not req.wallet and not req.username:
        raise HTTPException(400, "must provide wallet or username")
    wallet = (req.wallet or "").lower().strip()
    if not wallet and req.username:
        with httpx.Client(http2=False, timeout=30.0) as client:
            resolved = resolve_username(client, req.username)
        if not resolved:
            raise HTTPException(404, f"could not resolve username '{req.username}'")
        wallet = resolved.lower()
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(400, f"invalid wallet address: {wallet!r}")
    return wallet


def _run_analyze(wallet: str, force_refresh: bool, deep: bool, reporter) -> dict[str, Any]:
    """The actual analyze workload — runs inside the background thread."""
    # Cache hit short-circuit
    if not force_refresh:
        cached = get_recent_run(wallet)
        if cached:
            reporter.update(pct=100, stage="cache hit", detail="returning cached run")
            return {
                "run_id": cached["id"],
                "from_cache": True,
                "fetched_at": cached["fetched_at"],
                **cached["stats_json"],
            }

    log.info(f"fetching profile for {wallet} (deep={deep})")
    t0 = time.time()
    # deep=False skips the toolkit's audit-grade compute_address_pnl which
    # re-fetches every activity type and adds 60-120s. The Deep Dive button
    # re-runs with deep=True on demand. For triage we already have rich stats.
    profile, activity = build_profile(wallet, deep=deep, progress=reporter)

    # Weather-specific layer — reuses the activity we already fetched
    reporter.update(pct=60, stage="dissecting weather positions",
                    detail="aggregating by city, bucket, GFS phase…")
    try:
        profile["weather_dissection"] = dissect(activity, fetch_markets=True, progress=reporter)
    except Exception as e:
        log.warning(f"weather dissection failed: {e}")
        profile["weather_dissection"] = {"error": str(e)}

    reporter.update(pct=97, stage="saving", detail="writing to Supabase…")
    fetch_ms = int((time.time() - t0) * 1000)
    run_id = save_run(wallet, profile, fetch_ms, profile["meta"]["trade_count"])
    log.info(f"profile complete for {wallet} in {fetch_ms}ms (run_id={run_id})")
    return {"run_id": run_id, "from_cache": False, **profile}


@app.post("/analyze")
def analyze(req: AnalyzeRequest, _=Depends(require_auth)) -> dict[str, Any]:
    """Start an analyze job. Returns immediately with a job_id; poll /jobs/{id}.

    Cached runs (TTL window) short-circuit and return inline without spawning
    a job — this keeps the snappy case snappy.
    """
    wallet = _resolve_target(req)

    # Synchronous fast path: cache hit returns inline so the user doesn't
    # need to poll for a 50ms cache lookup.
    if not req.force_refresh:
        cached = get_recent_run(wallet)
        if cached:
            log.info(f"cache hit for {wallet} (run_id={cached['id']})")
            return {
                "run_id": cached["id"],
                "from_cache": True,
                "fetched_at": cached["fetched_at"],
                "job_id": None,
                **cached["stats_json"],
            }

    # Cache miss → kick off a background job and return the id
    job = jobs_mod.new_job(wallet)
    jobs_mod.run_in_thread(job, lambda r: _run_analyze(wallet, req.force_refresh, req.deep, r))
    log.info(f"started job {job.id} for {wallet} (force_refresh={req.force_refresh}, deep={req.deep})")
    return {"job_id": job.id, "wallet": wallet, "status": "running", "from_cache": False}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, _=Depends(require_auth)) -> dict[str, Any]:
    """Poll a running analyze job. Returns status + progress, and the full
    result blob once status='done'."""
    job = jobs_mod.get_job(job_id)
    if not job:
        raise HTTPException(404, f"job {job_id} not found (may have expired after 1h)")
    return jobs_mod.to_dict(job)


@app.post("/commentary")
def commentary_endpoint(req: CommentaryRequest, _=Depends(require_auth)) -> dict[str, Any]:
    # Load the profile to comment on
    if req.run_id:
        from .cache import _client
        sb = _client()
        if not sb:
            raise HTTPException(500, "Supabase not configured; cannot fetch run by id")
        r = sb.table("analyzer_runs").select("*").eq("id", req.run_id).limit(1).execute()
        rows = r.data or []
        if not rows:
            raise HTTPException(404, f"run_id {req.run_id} not found")
        profile = rows[0]["stats_json"]
        run_id = req.run_id
    elif req.wallet:
        cached = get_recent_run(req.wallet)
        if cached:
            profile = cached["stats_json"]
            run_id = cached["id"]
        else:
            raise HTTPException(404, "no recent run for this wallet; call /analyze first")
    else:
        raise HTTPException(400, "must provide run_id or wallet")

    if req.mode not in ("standard", "deep"):
        raise HTTPException(400, "mode must be 'standard' or 'deep'")

    log.info(f"generating commentary (mode={req.mode}, run_id={run_id})")
    result = run_commentary(profile, mode=req.mode)
    save_commentary(run_id, result, req.mode)
    log.info(f"commentary done (model={result.get('model_used')}, cost=${result.get('cost_usd')})")
    return {"run_id": run_id, **result}


def run() -> None:
    """Entry point for `python -m analyzer.app.main`."""
    import uvicorn
    uvicorn.run("analyzer.app.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    run()
