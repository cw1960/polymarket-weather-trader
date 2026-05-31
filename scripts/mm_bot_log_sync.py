"""
mm_bot_log_sync.py — reads mm_bot JSONL logs, pushes new records to Supabase.

Runs as a cron job every 30s. Idempotent: uses fill_ts_ms / settle_ts_ms
as a unique key so re-runs don't duplicate.

Touches the bot's logs in read-only mode — no risk to the running bot.
"""
import json
import os
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import time

from dotenv import load_dotenv
load_dotenv("/root/polymarket/.env")
from supabase import create_client


def fetch_polymarket_value(funder: str) -> float | None:
    """Returns the user's current Polymarket portfolio value (sum of position values)."""
    try:
        url = f"https://data-api.polymarket.com/value?user={funder.lower()}"
        req = urllib.request.Request(url, headers={'User-Agent':'mm_bot_sync'})
        r = json.loads(urllib.request.urlopen(req, timeout=8).read())
        if isinstance(r, list) and r:
            return float(r[0].get("value", 0))
    except Exception:
        pass
    return None


def fetch_open_exposure_from_positions(funder: str) -> float:
    """Sum initialValue of currently-held BTC positions. This is the TRUE
    cost basis of open positions — excludes positions that have been
    redeemed (winning sides that disappeared)."""
    try:
        all_pos = []
        for off in range(0, 2000, 500):
            url = f"https://data-api.polymarket.com/positions?user={funder.lower()}&limit=500&offset={off}&sortBy=CURRENT&sortDirection=desc"
            req = urllib.request.Request(url, headers={'User-Agent':'mm_bot_sync'})
            r = json.loads(urllib.request.urlopen(req, timeout=10).read())
            if not isinstance(r, list) or not r: break
            all_pos.extend(r)
            if len(r) < 500: break
        # Filter to BTC up/down (any duration: 5m, 15m, hourly) and SUM only those
        # still genuinely open (currentValue > 0). Settled-LOSER positions with
        # currentValue=0 have already had their cost realized. Broadened 2026-05-31
        # to cover the btc-updown-15m series the bot now trades.
        open_cost = 0.0
        for p in all_pos:
            slug = p.get("slug") or ""
            title = p.get("title") or ""
            if not (slug.startswith("btc-updown") or slug.startswith("bitcoin-up-or-down-on-")
                    or "Bitcoin Up or Down" in title or "BTC Up or Down" in title):
                continue
            cv = float(p.get("currentValue", 0) or 0)
            if cv > 0.01:    # still has live value → open position
                open_cost += float(p.get("initialValue", 0) or 0)
        return open_cost
    except Exception:
        return 0.0


_MARKET_OUTCOME_CACHE: dict = {}   # slug -> ('Up'/'Down'/None, is_closed_bool)

def get_market_outcome(slug: str) -> tuple[str | None, bool]:
    """Return (outcome, is_closed). Caches closed-market results forever."""
    cached = _MARKET_OUTCOME_CACHE.get(slug)
    if cached and cached[1]:    # closed → cache forever
        return cached
    try:
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        req = urllib.request.Request(url, headers={'User-Agent':'mm_bot_sync'})
        r = json.loads(urllib.request.urlopen(req, timeout=8).read())
        if not r: return (None, False)
        ev = r[0]
        is_closed = bool(ev.get("closed"))
        mk = (ev.get("markets") or [None])[0]
        if not mk: return (None, is_closed)
        op = mk.get("outcomePrices")
        if isinstance(op, str):
            try: op = json.loads(op)
            except: op = None
        outcome = None
        if op and len(op) >= 2:
            try:
                outcome = "Up" if float(op[0]) > 0.5 else "Down"
            except: pass
        result = (outcome, is_closed)
        _MARKET_OUTCOME_CACHE[slug] = result
        return result
    except Exception:
        return (None, False)


def compute_authoritative_pnl(fills: list[dict]) -> dict:
    """
    Compute realized + unrealized P&L from full fill log.
    Groups by market_slug. For each market:
      - If closed: realized_pnl = (winner_size × 1.00 - winner_cost) + (-loser_cost)
                   where "winner" is the side matching outcome.
                   For PAIRED inventory we have BOTH sides; only the winning side pays out.
                   So: realized_pnl = winner_size × 1.0 - (up_cost + down_cost)
      - If open:   unrealized_pnl = 0 (we don't mark-to-market here; let /value handle it)
    """
    by_slug: dict = {}
    for f in fills:
        if f.get("kind") != "FILL": continue
        slug = f.get("slug", "")
        side = f.get("side", "")
        size = float(f.get("size", 0))
        price = float(f.get("price", 0))
        cost = price * size
        if slug not in by_slug:
            by_slug[slug] = {"Up": {"size": 0, "cost": 0}, "Down": {"size": 0, "cost": 0}}
        by_slug[slug][side]["size"] += size
        by_slug[slug][side]["cost"] += cost

    total_fills_cost = sum(d["Up"]["cost"] + d["Down"]["cost"] for d in by_slug.values())
    realized_pnl = 0.0
    open_position_cost = 0.0
    settled_markets = 0
    open_markets = 0
    for slug, d in by_slug.items():
        u_size, u_cost = d["Up"]["size"], d["Up"]["cost"]
        d_size, d_cost = d["Down"]["size"], d["Down"]["cost"]
        total_cost = u_cost + d_cost
        outcome, is_closed = get_market_outcome(slug)
        if is_closed and outcome:
            winner_size = u_size if outcome == "Up" else d_size
            settled_pnl = (winner_size * 1.0) - total_cost
            realized_pnl += settled_pnl
            settled_markets += 1
        else:
            open_position_cost += total_cost
            open_markets += 1
    return {
        "total_fills_cost": total_fills_cost,
        "realized_pnl": realized_pnl,
        "open_position_cost": open_position_cost,
        "settled_markets": settled_markets,
        "open_markets": open_markets,
    }


def fetch_polymarket_pnl_summary(funder: str) -> dict:
    """Compute authoritative P&L from Polymarket position data, restricted
    to btc-updown-5m markets so we don't conflate with old weather bot positions.

    Returns:
      total_cost_basis: $ spent buying btc-updown-5m positions (since launch)
      total_current_value: current $ value of those positions
      position_value_unsettled: subset of current value for positions still trading
      total_pnl: total_current_value - total_cost_basis
    """
    try:
        # Pull positions (paginate via offset if needed)
        all_positions = []
        offset = 0
        while True:
            url = f"https://data-api.polymarket.com/positions?user={funder.lower()}&limit=500&offset={offset}&sortBy=CURRENT&sortDirection=desc"
            req = urllib.request.Request(url, headers={'User-Agent':'mm_bot_sync'})
            r = json.loads(urllib.request.urlopen(req, timeout=12).read())
            if not isinstance(r, list) or not r: break
            all_positions.extend(r)
            if len(r) < 500: break
            offset += 500
    except Exception as e:
        return {"err": str(e)}

    # Filter to btc-updown-5m only (these are the bot's markets)
    btc = []
    for p in all_positions:
        slug = p.get("slug") or ""
        title = p.get("title") or ""
        if slug.startswith("bitcoin-up-or-down-on-") or "Bitcoin Up or Down" in title:
            btc.append(p)
    total_cost = sum(float(p.get("initialValue", 0) or 0) for p in btc)
    total_curr = sum(float(p.get("currentValue", 0) or 0) for p in btc)
    # Determine settled vs unsettled: a position is settled when its market
    # has closed. Heuristic: currentValue is exactly 0 or matches size exactly.
    unsettled_value = 0.0
    settled_count = 0
    unsettled_count = 0
    for p in btc:
        sz = float(p.get("size", 0) or 0)
        cv = float(p.get("currentValue", 0) or 0)
        # If currentValue ≈ 0 or ≈ sz → settled. Otherwise unsettled.
        if abs(cv) < 0.01 or abs(cv - sz) < 0.05:
            settled_count += 1
        else:
            unsettled_count += 1
            unsettled_value += cv
    return {
        "total_cost_basis": total_cost,
        "total_current_value": total_curr,
        "position_value_unsettled": unsettled_value,
        "total_pnl": total_curr - total_cost,
        "settled_positions": settled_count,
        "unsettled_positions": unsettled_count,
        "btc_positions_total": len(btc),
    }


LOG_DIR = Path("/root/polymarket/logs/mm_bot")
EVENTS_FILE = LOG_DIR / "events.jsonl"
FILLS_FILE  = LOG_DIR / "fills.jsonl"

sb = create_client(
    os.environ.get("VITE_SUPABASE_URL") or os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

# Confirmed net deposit (Chris, 2026-05-31): $791, no withdrawals. Lifetime P&L
# is anchored to this — Portfolio - NET_DEPOSIT_USD. Update ONLY if real capital
# is added/withdrawn. Replaces the old derive-from-log-P&L estimate that masked
# ~$259 of real losses on the dashboard.
NET_DEPOSIT_USD = 791.0
_CASH_CACHE = LOG_DIR / "last_cash.txt"


def get_cash_balance():
    """Real USDC cash on the deposit wallet — ground truth, same source as
    reconcile_real_bankroll.py (clob_http get_balance_allowance; balance is a
    string in 1e6 units). Caches last-known-good so a transient API blip never
    reverts the dashboard to a guessed number. Returns None only if both fail."""
    import sys as _sys
    try:
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from clob_http import get_client
        raw = get_client().get_balance_allowance()
        cash = float(raw.get("balance", "0")) / 1e6
        try:
            _CASH_CACHE.write_text(f"{cash:.6f}")
        except Exception:
            pass
        return cash
    except Exception as e:
        print(f"cash fetch failed ({str(e)[:80]}); using last-known-good", flush=True)
        try:
            return float(_CASH_CACHE.read_text().strip())
        except Exception:
            return None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists(): return []
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln: continue
            try: out.append(json.loads(ln))
            except: pass
    return out


def upsert_fills():
    fills = load_jsonl(FILLS_FILE)
    if not fills: return 0
    rows = []
    for f in fills:
        # 'kind' === 'FILL' only
        if f.get("kind") != "FILL": continue
        btc = f.get("btc_at_fill") or {}
        rows.append({
            "fill_ts_ms": f["ts"],
            "fill_time": f["iso"],
            "market_slug": f.get("slug", ""),
            "side": f.get("side", ""),
            "price": float(f.get("price", 0)),
            "size": float(f.get("size", 0)),
            "cost_usd": float(f.get("cost", 0)),
            "cumulative_up": float(f.get("cumulative_up") or 0),
            "cumulative_down": float(f.get("cumulative_down") or 0),
            "btc_binance": btc.get("binance"),
            "btc_chainlink": btc.get("chainlink_onchain"),
        })
    if not rows: return 0
    # Upsert in batches of 500
    n_total = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        try:
            sb.table("mm_bot_fills").upsert(batch, on_conflict="fill_ts_ms").execute()
            n_total += len(batch)
        except Exception as e:
            print(f"fills upsert err on batch {i}: {e}")
    return n_total


def upsert_settlements():
    events = load_jsonl(EVENTS_FILE)
    settle_events = [e for e in events if e.get("kind") == "SETTLEMENT"]
    if not settle_events: return 0
    rows = []
    for e in settle_events:
        rows.append({
            "settle_ts_ms": e["ts"],
            "settlement_time": e["iso"],
            "market_slug": e.get("slug", ""),
            "outcome": e.get("outcome"),
            "up_filled": float(e.get("up_filled") or 0),
            "down_filled": float(e.get("down_filled") or 0),
            "up_cost": float(e.get("up_cost") or 0),
            "down_cost": float(e.get("down_cost") or 0),
            "pnl_usd": float(e.get("pnl") or 0),
            "cumulative_pnl_usd": float(e.get("realized_pnl_cumulative") or 0),
            "notes": e.get("notes", ""),
        })
    n_total = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        try:
            sb.table("mm_bot_settlements").upsert(batch, on_conflict="settle_ts_ms").execute()
            n_total += len(batch)
        except Exception as e:
            print(f"settlements upsert err: {e}")
    return n_total


def push_status():
    """Push current bot status row."""
    # Match either mm_bot.py or mm_bot_v2.py (= any bot variant currently running)
    proc = subprocess.run(["pgrep", "-f", r"scripts/mm_bot.*\.py"], capture_output=True, text=True)
    alive = (proc.returncode == 0 and proc.stdout.strip())

    events = load_jsonl(EVENTS_FILE)
    fills = load_jsonl(FILLS_FILE)
    settlements = [e for e in events if e.get("kind") == "SETTLEMENT"]
    # Only count kill events from the CURRENT bot session (= since last CLIENT_READY)
    client_ready_events = [e for e in events if e.get("kind") == "CLIENT_READY"]
    session_start_ts = client_ready_events[-1].get("ts", 0) if client_ready_events else 0
    kill_events = [e for e in events
                   if e.get("kind") == "KILL_SWITCH_TRIPPED"
                   and e.get("ts", 0) >= session_start_ts]
    client_ready = [e for e in events if e.get("kind") == "CLIENT_READY"]
    bot_started = client_ready[-1]["iso"] if client_ready else None
    realized = settlements[-1].get("realized_pnl_cumulative", 0) if settlements else 0
    placements_today = sum(1 for e in events if e.get("kind") == "POST_OK"
                            and datetime.fromisoformat(e["iso"].replace("Z","+00:00")).date()
                                == datetime.now(timezone.utc).date())

    # Authoritative P&L from our FILL log + per-market outcome lookup.
    # Position value + open exposure come from Polymarket's live data (NOT
    # our fill log) so we don't double-count redeemed positions.
    funder = os.environ["POLY_FUNDER_ADDRESS"]
    pnl_calc = compute_authoritative_pnl(fills)
    live_position_value = fetch_polymarket_value(funder) or 0.0
    true_open_exposure = fetch_open_exposure_from_positions(funder)

    # Ground-truth account math (2026-05-31): Portfolio = real USDC cash + live
    # position value; lifetime P&L = Portfolio - confirmed net deposit. This
    # replaces deriving the balance from a log-estimated P&L, which had been
    # OVERSTATING the account by ~$259 (showing ~$559 vs the real ~$300). The
    # frontend now reads starting_balance_usd + realized_pnl_usd directly.
    cash = get_cash_balance()
    if cash is not None:
        portfolio_total = cash + live_position_value
        lifetime_pnl = portfolio_total - NET_DEPOSIT_USD
        money_note = f"cash=${cash:.2f} port=${portfolio_total:.2f} pnl=${lifetime_pnl:+.2f}"
    else:
        # Both live fetch and cache failed — degrade loudly, don't publish a guess.
        lifetime_pnl = pnl_calc["realized_pnl"]
        money_note = "cash UNAVAILABLE (P&L approximate)"

    row = {
        "id": 1,
        "last_heartbeat": datetime.now(timezone.utc).isoformat(),
        "process_alive": bool(alive),
        "open_orders_count": 0,
        "active_markets_count": pnl_calc["open_markets"],
        "open_exposure_usd": true_open_exposure,
        "starting_balance_usd": NET_DEPOSIT_USD,
        "realized_pnl_usd": lifetime_pnl,
        "total_fills": len([f for f in fills if f.get("kind") == "FILL"]),
        "total_settlements": pnl_calc["settled_markets"],
        "kill_switch_tripped": bool(kill_events),
        "placements_today": placements_today,
        "bot_started_at": bot_started,
        "notes": (f"{money_note} | PIDs: {proc.stdout.strip()}" if alive
                  else f"{money_note} | bot not running"),
        "polymarket_portfolio_value": live_position_value,
    }
    try:
        sb.table("mm_bot_status").upsert(row, on_conflict="id").execute()
    except Exception as e:
        print(f"status upsert err: {e}")


def main_once():
    t0 = time.time()
    nf = upsert_fills()
    ns = upsert_settlements()
    push_status()
    print(f"sync done in {time.time()-t0:.1f}s: fills={nf} settlements={ns}", flush=True)


def main_loop(interval_s: float = 3.0):
    """Daemon mode: run forever, syncing every interval_s seconds."""
    print(f"mm_bot_log_sync daemon starting (interval={interval_s}s)", flush=True)
    while True:
        try:
            main_once()
        except Exception as e:
            print(f"sync error: {e}", flush=True)
        time.sleep(interval_s)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--loop":
        interval = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
        main_loop(interval)
    else:
        main_once()
