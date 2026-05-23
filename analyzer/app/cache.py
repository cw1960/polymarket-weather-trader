"""Supabase-backed cache for analyzer runs and commentary."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .config import CACHE_TTL_SECONDS, SUPABASE_KEY, SUPABASE_URL

_sb = None


def _client():
    global _sb
    if _sb is None and SUPABASE_URL and SUPABASE_KEY:
        from supabase import create_client  # type: ignore
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _sb


def get_recent_run(wallet: str) -> dict | None:
    sb = _client()
    if not sb or CACHE_TTL_SECONDS <= 0:
        return None
    try:
        r = (sb.table("analyzer_runs")
             .select("*")
             .eq("wallet", wallet.lower())
             .order("fetched_at", desc=True)
             .limit(1)
             .execute())
        rows = r.data or []
        if not rows:
            return None
        row = rows[0]
        fetched = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
        age = (datetime.now(tz=timezone.utc) - fetched).total_seconds()
        if age > CACHE_TTL_SECONDS:
            return None
        return row
    except Exception:
        return None


def save_run(wallet: str, stats_json: dict, fetch_ms: int, trade_count: int) -> int | None:
    sb = _client()
    if not sb:
        return None
    try:
        r = sb.table("analyzer_runs").insert({
            "wallet": wallet.lower(),
            "username": (stats_json.get("identity") or {}).get("username") or "",
            "stats_json": stats_json,
            "fetch_ms": fetch_ms,
            "trade_count": trade_count,
        }).execute()
        rows = r.data or []
        return rows[0]["id"] if rows else None
    except Exception:
        return None


def list_recent_wallets(limit: int = 50) -> list[dict]:
    """Latest run per unique wallet, newest first.  Each row is enriched
    with the user's personal headline + notes (empty if unset)."""
    sb = _client()
    if not sb:
        return []
    try:
        r = (sb.table("analyzer_runs")
             .select("wallet, username, fetched_at, stats_json")
             .order("fetched_at", desc=True)
             .limit(limit * 4)
             .execute())
    except Exception:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for row in (r.data or []):
        w = (row.get("wallet") or "").lower()
        if not w or w in seen:
            continue
        seen.add(w)
        stats = row.get("stats_json") or {}
        out.append(_summarize_run(w, row.get("username"), row.get("fetched_at"), stats))
        if len(out) >= limit:
            break
    # Attach headline + notes (single batch fetch)
    notes_map = get_annotations_bulk([row["wallet"] for row in out])
    for row in out:
        ann = notes_map.get(row["wallet"], {"headline": "", "notes": ""})
        row["headline"] = ann["headline"]
        row["notes"]    = ann["notes"]
    return out


def _summarize_run(wallet: str, username: str | None, fetched_at: str | None,
                   stats_json: dict) -> dict:
    identity = stats_json.get("identity") or {}
    stats = stats_json.get("stats") or {}
    strategy = stats_json.get("strategy") or {}
    meta = stats_json.get("meta") or {}
    return {
        "wallet": wallet,
        "username": username or identity.get("username") or "",
        "pseudonym": identity.get("pseudonym") or "",
        "fetched_at": fetched_at,
        "total_trades": stats.get("total_trades", 0),
        "unique_markets": stats.get("unique_markets", 0),
        "weather_share": stats.get("weather_share", 0),
        "net_cashflow_usd": stats.get("net_cashflow_usd", 0),
        "open_positions": stats.get("open_positions", 0),
        "strategy_label": strategy.get("label", ""),
        "trade_count": meta.get("trade_count", 0),
    }


def list_watchlist() -> list[dict]:
    """Active watchlist entries joined with their latest run summary."""
    sb = _client()
    if not sb:
        return []
    try:
        rows = (sb.table("analyzer_watchlist")
                .select("*")
                .eq("active", True)
                .order("added_at", desc=True)
                .execute().data or [])
    except Exception:
        return []
    out: list[dict] = []
    for w in rows:
        wallet = (w.get("wallet") or "").lower()
        # Pull latest run summary
        try:
            runs = (sb.table("analyzer_runs")
                    .select("username, fetched_at, stats_json")
                    .eq("wallet", wallet)
                    .order("fetched_at", desc=True).limit(1).execute().data or [])
        except Exception:
            runs = []
        if runs:
            summary = _summarize_run(wallet, runs[0].get("username"),
                                     runs[0].get("fetched_at"),
                                     runs[0].get("stats_json") or {})
        else:
            summary = {"wallet": wallet, "username": "", "pseudonym": "",
                       "fetched_at": None, "total_trades": 0, "weather_share": 0,
                       "strategy_label": "", "open_positions": 0,
                       "net_cashflow_usd": 0, "unique_markets": 0, "trade_count": 0}
        summary["label"]       = w.get("label") or ""
        summary["added_at"]    = w.get("added_at")
        summary["last_polled"] = w.get("last_polled")
        out.append(summary)
    # Attach personal annotations (separate table)
    notes_map = get_annotations_bulk([row["wallet"] for row in out])
    for row in out:
        ann = notes_map.get(row["wallet"], {"headline": "", "notes": ""})
        row["headline"] = ann["headline"]
        row["notes"]    = ann["notes"]
    return out


def watchlist_add(wallet: str, label: str = "") -> bool:
    sb = _client()
    if not sb:
        return False
    try:
        sb.table("analyzer_watchlist").upsert({
            "wallet": wallet.lower(),
            "label": label,
            "active": True,
        }).execute()
        return True
    except Exception:
        return False


def watchlist_remove(wallet: str) -> bool:
    sb = _client()
    if not sb:
        return False
    try:
        sb.table("analyzer_watchlist").update(
            {"active": False}
        ).eq("wallet", wallet.lower()).execute()
        return True
    except Exception:
        return False


def watchlist_set_label(wallet: str, label: str) -> bool:
    sb = _client()
    if not sb:
        return False
    try:
        sb.table("analyzer_watchlist").update(
            {"label": label}
        ).eq("wallet", wallet.lower()).execute()
        return True
    except Exception:
        return False


def watchlist_update_annotations(
    wallet: str,
    *,
    label: str | None = None,
) -> bool:
    """
    Patch the label on a watchlist row.  Headline and notes were moved
    out to `analyzer_annotations` (see get/set_annotations) so they can
    exist on any analyzed wallet without requiring it be followed.
    """
    sb = _client()
    if not sb:
        return False
    if label is None:
        return True
    try:
        sb.table("analyzer_watchlist").update({"label": label}).eq(
            "wallet", wallet.lower()
        ).execute()
        return True
    except Exception:
        return False


# ── Personal annotations (headline + notes) ──────────────────────────────
# These live on their own table, keyed by wallet.  Available for any
# analyzed wallet — no follow required.

def get_annotations(wallet: str) -> dict[str, str]:
    """Return {headline, notes} for a wallet; empty strings if unset."""
    sb = _client()
    if not sb:
        return {"headline": "", "notes": ""}
    try:
        r = (sb.table("analyzer_annotations")
             .select("headline, notes")
             .eq("wallet", wallet.lower())
             .limit(1)
             .execute())
        if r.data:
            row = r.data[0]
            return {
                "headline": row.get("headline") or "",
                "notes":    row.get("notes") or "",
            }
    except Exception:
        pass
    return {"headline": "", "notes": ""}


def get_annotations_bulk(wallets: list[str]) -> dict[str, dict[str, str]]:
    """Batch fetch annotations for a list of wallets — used by /watchlist
    and /history to attach headline/notes to each list row in one query."""
    sb = _client()
    if not sb or not wallets:
        return {}
    try:
        lower = [w.lower() for w in wallets]
        r = (sb.table("analyzer_annotations")
             .select("wallet, headline, notes")
             .in_("wallet", lower)
             .execute())
        out: dict[str, dict[str, str]] = {}
        for row in (r.data or []):
            w = (row.get("wallet") or "").lower()
            out[w] = {
                "headline": row.get("headline") or "",
                "notes":    row.get("notes") or "",
            }
        return out
    except Exception:
        return {}


def set_annotations(
    wallet: str,
    *,
    headline: str | None = None,
    notes:    str | None = None,
) -> bool:
    """Upsert one or both annotation fields for a wallet."""
    sb = _client()
    if not sb:
        return False
    payload: dict[str, Any] = {"wallet": wallet.lower()}
    if headline is not None: payload["headline"] = headline
    if notes    is not None: payload["notes"]    = notes
    if len(payload) == 1:    # only wallet, nothing to write
        return True
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        sb.table("analyzer_annotations").upsert(payload).execute()
        return True
    except Exception:
        return False


def save_commentary(run_id: int, result: dict[str, Any], mode: str) -> None:
    sb = _client()
    if not sb:
        return
    try:
        sb.table("analyzer_commentary").insert({
            "run_id": run_id,
            "model": result.get("model_used", ""),
            "mode": mode,
            "markdown": result.get("markdown", ""),
            "cost_usd": result.get("cost_usd", 0.0),
        }).execute()
    except Exception:
        pass


def purge_trader(wallet: str) -> dict[str, int]:
    """Delete every record for a wallet: runs (cascade commentary) + watchlist.

    Returns counts deleted for transparency.
    """
    sb = _client()
    if not sb:
        return {"runs": 0, "watchlist": 0}
    wallet = wallet.lower()
    counts = {"runs": 0, "watchlist": 0}
    try:
        # analyzer_commentary has ON DELETE CASCADE on analyzer_runs.id
        r = sb.table("analyzer_runs").delete().eq("wallet", wallet).execute()
        counts["runs"] = len(r.data or [])
    except Exception:
        pass
    try:
        r = sb.table("analyzer_watchlist").delete().eq("wallet", wallet).execute()
        counts["watchlist"] = len(r.data or [])
    except Exception:
        pass
    return counts
