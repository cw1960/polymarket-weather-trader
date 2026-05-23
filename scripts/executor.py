"""
Execution layer — Polymarket CLOB order placement and fill tracking.
====================================================================
Called by:
  • signal_engine.py  — Phase 1 ladder signals (morning run)
  • phase2_engine.py  — Phase 2 bracket confirmation signals (intraday)
  • temp_monitor.py   — fill-check poll (every 5-min monitor cycle)

Modes
-----
LIVE_TRADING = False (default, config.py)
  Paper mode: logs the intended order, writes order_status='paper' to DB.
  No CLOB calls are made.  Safe to run with real API keys.

LIVE_TRADING = True
  Live mode: places maker (limit) orders via py-clob-client 0.34.6.
  Order size is calculated in tokens (size_usd / price), NOT in USD.
  Uses GTC limit orders (0% maker fee on weather markets).
  After MAKER_TIMEOUT_MINS with no fill, cancels and retries at taker price.

Key facts (confirmed from py-clob-client 0.34.6 docs and live testing)
-----------------------------------------------------------------------
• OrderArgs.size = number of outcome tokens, NOT USD.
  num_tokens = round(size_usd / price, 2)

• clobTokenIds from Gamma API is a JSON-encoded string: '["tok0", "tok1"]'
  ids[0] = YES token, ids[1] = NO token

• To buy YES: BUY YES token
  To buy NO:  BUY NO token  (both sides use Side.BUY for their token)

• post_order(order, OrderType.GTC) — post_only defaults True in 0.34.6

• POLY_PRIVATE_KEY: MetaMask exports without 0x prefix; code adds it.
"""

import json
import os
import logging
import requests
from datetime import datetime, timezone

from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, LIVE_TRADING, MAKER_TIMEOUT_MINS

# Best-effort alerting: never let import or send-failure break the executor.
try:
    from notifier import send_alert  # type: ignore
except Exception:
    def send_alert(*_args, **_kwargs):  # type: ignore
        return False

log = logging.getLogger(__name__)
sb  = create_client(SUPABASE_URL, SUPABASE_KEY)

CLOB_HOST  = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

# Lazy-initialised CLOB client (only created when LIVE_TRADING=True)
_client = None


# ── CLOB client init ──────────────────────────────────────────────────────────

def _get_client():
    """
    Return the HTTP-backed CLOB client (proxies to ts_executor/server.mjs).

    The Polymarket Python SDK `py_clob_client_v2` has a bug in its L1 auth
    handshake that breaks POLY_1271 (deposit wallet) accounts.  We use the
    official TypeScript SDK via a tiny loopback HTTP service; the Python
    shim is in scripts/clob_http.py.

    The legacy in-process Python client init code is preserved below in
    `_legacy_get_client_DISABLED` for reference only — do not call it.
    """
    global _client
    if _client is not None:
        return _client
    try:
        from clob_http import get_client as _http_get_client
        _client = _http_get_client()
        addr = _client.get_address()
        log.info(f"  [Executor] CLOB HTTP client connected (EOA {addr}) ✅")
        return _client
    except Exception as e:
        log.error(f"  [Executor] CLOB HTTP client init failed: {e}")
        return None


def _legacy_get_client_DISABLED():
    """
    DEPRECATED — kept for reference until the Python SDK is fixed upstream.

    Polymarket migrated to CLOB v2 in April 2026; the legacy py-clob-client (0.34.x)
    returns 'order_version_mismatch' on every order. py-clob-client-v2 uses the new
    EIP-712 domain and contract addresses.

    Required .env vars:
      POLY_PRIVATE_KEY     — private key of the EOA (signer)
      POLY_FUNDER_ADDRESS  — address of the proxy (where USDC sits)
    """
    global _client
    if _client is not None:
        return _client
    try:
        from py_clob_client_v2 import ClobClient
        try:
            from py_clob_client_v2.constants import POLYGON
        except Exception:
            POLYGON = 137

        key = os.getenv("POLY_PRIVATE_KEY", "")
        if not key:
            raise ValueError("POLY_PRIVATE_KEY not set in .env")
        if not key.startswith("0x"):
            key = "0x" + key

        funder = os.getenv("POLY_FUNDER_ADDRESS", "")
        # signature_type=1 (POLY_PROXY) for MetaMask-signup users with a proxy wallet.
        # Override via POLY_SIGNATURE_TYPE in .env: 0=EOA, 2=Gnosis Safe, 3=Deposit wallet.
        sig_type = int(os.getenv("POLY_SIGNATURE_TYPE", "1"))

        if funder:
            # First create a temp client to derive API creds
            tmp = ClobClient(host=CLOB_HOST, key=key, chain_id=POLYGON)
            creds = tmp.create_or_derive_api_key()
            # Then create the actual trading client with funder + sig_type
            client = ClobClient(
                host=CLOB_HOST,
                key=key,
                chain_id=POLYGON,
                creds=creds,
                signature_type=sig_type,
                funder=funder,
            )
            log.info(
                f"  [Executor] CLOB v2 client initialised "
                f"(signature_type={sig_type}, funder={funder[:10]}...) ✅"
            )
        else:
            client = ClobClient(host=CLOB_HOST, key=key, chain_id=POLYGON)
            creds  = client.create_or_derive_api_key()
            client.set_api_creds(creds)
            log.info("  [Executor] CLOB v2 client initialised (EOA mode) ✅")

        _client = client
        return client
    except Exception as e:
        log.error(f"  [Executor] CLOB v2 client init failed: {e}")
        return None


# ── Gamma API helpers ─────────────────────────────────────────────────────────

def _get_clob_token_ids(condition_id: str) -> tuple[str | None, str | None]:
    """
    Return (yes_token_id, no_token_id) for a condition_id.

    Uses the **CLOB API** via the TS executor shim because Gamma's
    `?conditionId=...` filter is fuzzy and returns wrong markets when
    the prefix matches another condition.  That bug was responsible
    for "invalid signature" rejections — we were signing an order for
    market A using tokens from market B.
    """
    try:
        from clob_http import get_client
        market = get_client().get_market(condition_id)
        tokens = market.get("tokens", []) if isinstance(market, dict) else []
        yes_id: str | None = None
        no_id:  str | None = None
        for t in tokens:
            tid = str(t.get("token_id", "")) if isinstance(t, dict) else ""
            outcome = str(t.get("outcome", "")).strip().lower() if isinstance(t, dict) else ""
            if outcome == "yes":
                yes_id = tid
            elif outcome == "no":
                no_id  = tid
        # Defensive: confirm the CLOB market really matches the condition we asked for
        if isinstance(market, dict):
            got_cid = market.get("condition_id", "")
            if got_cid and got_cid.lower() != condition_id.lower():
                log.error(
                    f"  [Executor] CLOB returned wrong market for {condition_id[:12]}… "
                    f"(got {got_cid[:12]}…) — refusing to trade"
                )
                return None, None
        return yes_id, no_id
    except Exception as e:
        log.warning(f"  [Executor] CLOB token lookup error for {condition_id[:12]}…: {e}")
    return None, None


def _best_ask(client, token_id: str) -> float | None:
    """Return best ask price from the CLOB order book for a token."""
    try:
        book = client.get_order_book(token_id)
        if book and book.asks:
            return float(book.asks[0].price)
    except Exception as e:
        log.warning(f"  [Executor] order book error: {e}")
    return None


def _snapshot_book(client, token_id: str) -> dict:
    """
    Snapshot best bid/ask/mid at the moment of order placement.
    Used by execution telemetry to measure slippage (fill_price - mid_at_signal).
    Returns {bid, ask, mid} with each value as float or None.
    """
    out = {"bid": None, "ask": None, "mid": None}
    try:
        book = client.get_order_book(token_id)
        if book:
            if book.asks:
                out["ask"] = float(book.asks[0].price)
            if book.bids:
                out["bid"] = float(book.bids[0].price)
            if out["ask"] is not None and out["bid"] is not None:
                out["mid"] = round((out["ask"] + out["bid"]) / 2, 4)
    except Exception as e:
        log.debug(f"  [Executor] book snapshot error: {e}")
    return out


# ── DB helper ─────────────────────────────────────────────────────────────────

def _update_signal_order(
    signal_id:  str,
    order_id:   str | None,
    status:     str,
    fill_price: float | None,
    telemetry:  dict | None = None,
) -> None:
    """
    Write order_id, order_status, fill_price, and optional telemetry fields to trade_signals.

    telemetry may include: intended_price, bid_at_signal, ask_at_signal, mid_at_signal,
    fill_time, fill_latency_ms.
    Unknown columns are dropped gracefully (so this works before the migration is applied).
    """
    update: dict = {"order_status": status}
    if order_id   is not None: update["order_id"]   = order_id
    if fill_price is not None: update["fill_price"] = round(fill_price, 6)
    if telemetry:
        for k, v in telemetry.items():
            if v is not None:
                update[k] = v
    try:
        sb.table("trade_signals").update(update).eq("id", signal_id).execute()
    except Exception as e:
        msg = str(e)
        # Retry without new columns if migration not yet applied
        telemetry_cols = ("intended_price", "bid_at_signal", "ask_at_signal",
                          "mid_at_signal", "fill_time", "fill_latency_ms",
                          "filled_size_usd")
        if any(c in msg for c in telemetry_cols):
            for c in telemetry_cols:
                update.pop(c, None)
            try:
                sb.table("trade_signals").update(update).eq("id", signal_id).execute()
                log.warning(
                    "  [Executor] telemetry columns missing — "
                    "run migrate_exec_telemetry.sql to enable fill-quality tracking"
                )
                return
            except Exception as e2:
                log.warning(f"  [Executor] DB update retry failed for signal {signal_id}: {e2}")
                return
        log.warning(f"  [Executor] DB update failed for signal {signal_id}: {e}")


# ── Main order placement ──────────────────────────────────────────────────────

def place_order(
    condition_id:  str,
    side:          str,    # "YES" or "NO"
    signal_price:  float,  # YES price for YES side; NO price (1-yes) for NO side
    size_usd:      float,
    signal_id:     str | None = None,
    phase:         str = "phase1",
) -> dict:
    """
    Place a maker limit order for a trade signal.

    Paper mode  → logs intent, marks order_status='paper', no CLOB call.
    Live mode   → fetches live order book, places GTC limit order, records order_id.

    Returns a status dict:
      {"status": "paper"|"placed"|"skipped"|"error", ...}
    """
    tag = f"{side} {condition_id[:10]}… ${size_usd:.2f} @ {signal_price*100:.1f}¢ [{phase}]"

    # ── Paper mode ────────────────────────────────────────────────────────────
    if not LIVE_TRADING:
        log.info(f"  [Executor] 📄 PAPER {tag}")
        if signal_id:
            _update_signal_order(signal_id, None, "paper", signal_price)
        return {"status": "paper", "price": signal_price, "size_usd": size_usd}

    # ── Live mode ─────────────────────────────────────────────────────────────
    client = _get_client()
    if client is None:
        if signal_id:
            _update_signal_order(signal_id, None, "failed", None)
        return {"status": "error", "reason": "client_init_failed"}

    # 1. Get token IDs from Gamma API
    yes_token, no_token = _get_clob_token_ids(condition_id)
    if not yes_token:
        log.warning(f"  [Executor] No clobTokenIds for {condition_id[:12]}…")
        if signal_id:
            _update_signal_order(signal_id, None, "failed", None)
        return {"status": "error", "reason": "no_token_id"}

    token_id  = yes_token if side == "YES" else no_token
    buy_price = signal_price   # starting price; may be refined by live order book

    # 2. Snapshot the order book (bid/ask/mid) before placing the order.
    # This is execution telemetry — lets us measure slippage and fill quality
    # against actual fill_price downstream.
    book_snap = _snapshot_book(client, token_id)
    live_ask  = book_snap.get("ask")

    # Slippage tolerance: when the book has a wide spread, snapping to the
    # ask can blow the trade through the +EV envelope.  Use the SMALLER of
    # 2¢ absolute or 10% of the signal price.  At a 28¢ signal that's 2¢;
    # at a 10¢ signal that's 1¢.  Previously this was a flat 5¢, which on
    # the Paris 14°C trade (signal 28.55¢, ask 33.5¢) crossed the 30¢
    # +EV cap and produced a guaranteed-losing fill.
    if live_ask is not None:
        slippage_budget = min(0.02, 0.10 * buy_price)
        if abs(live_ask - buy_price) <= slippage_budget:
            buy_price = live_ask
        else:
            log.info(
                f"  [Executor] {tag}: live ask {live_ask*100:.1f}¢ "
                f"vs signal {signal_price*100:.1f}¢ "
                f"(slippage budget {slippage_budget*100:.1f}¢) — using signal price"
            )

    # 2b. Phase-2 final guards (replaces the old PHASE2_MAX_CALIBRATED_PRICE
    # cap, which was removed 2026-05-20 along with the rest of the static
    # price-cap rule). Two checks here are belt-and-braces against bugs in
    # the caller pipeline:
    #
    #   (i)  Absolute size cap. The sizing module already applies this, but
    #        if any future code path bypasses sizing.size_for_*() and calls
    #        place_order() with a hand-picked size, the executor still
    #        refuses anything above max_trade_usd_absolute.
    #
    #   (ii) Per-signal_id duplicate-execution guard. If a signal row
    #        already has a non-paper, non-failed order_id, refuse to place
    #        a second order on it. Prevents cron-overlap double-fills.
    if signal_id is not None:
        try:
            existing = (sb.table("trade_signals")
                        .select("order_id, order_status")
                        .eq("id", signal_id)
                        .single().execute())
            if existing.data:
                ex_order_id = existing.data.get("order_id")
                ex_status   = existing.data.get("order_status")
                if ex_order_id and ex_status not in (None, "", "paper", "failed", "observation"):
                    log.warning(
                        f"  [Executor] {tag}: signal {signal_id} already has order_id "
                        f"{ex_order_id} (status={ex_status}) — refusing duplicate placement"
                    )
                    return {"status": "skipped", "reason": "already_placed", "order_id": ex_order_id}
        except Exception as _idem_err:
            log.debug(f"  [Executor] idempotency check skipped (read error): {_idem_err}")

    # Absolute size cap from system_config (defense in depth — sizing.py
    # already enforces this, but enforce it here too in case of bypass).
    try:
        _cap_row = (sb.table("system_config").select("value")
                    .eq("key", "max_trade_usd_absolute").maybe_single().execute())
        _abs_cap = float(_cap_row.data["value"]) if _cap_row.data else 10.0
    except Exception:
        _abs_cap = 10.0
    if size_usd > _abs_cap + 0.005:    # +0.005 tolerance for float math
        log.error(
            f"  [Executor] {tag}: size ${size_usd:.2f} > absolute cap ${_abs_cap:.2f} "
            f"— REFUSING (likely a bug upstream of executor)"
        )
        if signal_id:
            _update_signal_order(signal_id, None, "failed", None)
        return {"status": "error", "reason": "exceeds_absolute_cap"}

    # 3. Calculate size in tokens
    if buy_price <= 0:
        if signal_id:
            _update_signal_order(signal_id, None, "failed", None)
        return {"status": "error", "reason": "zero_price"}

    num_tokens = round(size_usd / buy_price, 2)
    if num_tokens < 1.0:
        log.warning(f"  [Executor] {tag}: num_tokens={num_tokens:.2f} < 1 — too small, skipping")
        if signal_id:
            _update_signal_order(signal_id, None, "observation", buy_price)
        return {"status": "skipped", "reason": "too_small"}

    # 4. Place maker limit order (v2 API uses combined create_and_post_order)
    try:
        from clob_http import OrderArgs, OrderType, PartialCreateOrderOptions, BUY

        # Fetch market metadata for tick_size and neg_risk
        try:
            market = client.get_market(condition_id)
            tick_size = str(market.get("minimum_tick_size", "0.01"))
            neg_risk  = bool(market.get("neg_risk", False))
        except Exception as _merr:
            log.warning(f"  [Executor] market lookup failed, using defaults: {_merr}")
            tick_size, neg_risk = "0.01", False

        # Pre-flight: at the price extremes (>0.90 or <0.10), Polymarket
        # enforces a coarser tick than the metadata reports.  Round the price
        # and tick now so the first attempt is on the right grid.  Empirical
        # rule observed live: price∈[0.10, 0.90] → tick=0.001 OK; else 0.01.
        def _force_tick(p: float, fine_ok: bool) -> tuple[float, str]:
            if fine_ok and 0.10 <= p <= 0.90:
                # Fine tick allowed; keep as-is rounded to 3dp
                return round(p, 3), "0.001"
            # Coarse tick — round to 0.01, clamp to (0.01, 0.99) so the order
            # is on a valid resting price (Polymarket rejects 0.00 and 1.00).
            return max(0.01, min(0.99, round(p, 2))), "0.01"

        fine_ok = (tick_size == "0.001")
        buy_price, tick_size = _force_tick(buy_price, fine_ok)
        num_tokens = round(size_usd / buy_price, 2)
        if num_tokens < 1.0:
            log.warning(f"  [Executor] {tag}: post-tick num_tokens={num_tokens:.2f} < 1 — skip")
            if signal_id:
                _update_signal_order(signal_id, None, "observation", buy_price)
            return {"status": "skipped", "reason": "too_small_after_tick_round"}

        order_args = OrderArgs(
            token_id=token_id,
            price=buy_price,
            size=num_tokens,
            side=BUY,
        )
        options  = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

        # ── Place order with retry on transient "invalid signature" errors.
        # Polymarket occasionally returns 'invalid signature' on the first
        # attempt for a market the relayer hasn't recently seen.  A second
        # try ~2s later virtually always succeeds.  Retry up to 3 times then
        # treat as a hard failure.
        import time
        attempts = 3
        last_err: str | None = None
        response: dict = {}
        order_id: str | None = None
        for attempt in range(1, attempts + 1):
            # The HTTP shim raises CLOBHTTPError on non-2xx; we must catch
            # those inside the loop so we still retry on transient server
            # errors like "invalid signature" that surface as HTTP 500.
            try:
                response = client.create_and_post_order(
                    order_args, options=options, order_type=OrderType.GTC,
                )
            except Exception as call_err:
                msg = str(call_err)
                last_err = msg
                # If Polymarket tells us the required tick, swap and retry once.
                # Error format: "invalid tick size (0.001), minimum for the market is 0.01"
                import re
                m_tick = re.search(r"minimum for the market is\s+([0-9.]+)", msg)
                if m_tick:
                    new_tick = m_tick.group(1)
                    new_buy  = round(buy_price, len(new_tick.split('.')[-1]))
                    # Clamp to a valid limit-order range
                    new_buy  = max(float(new_tick), min(1 - float(new_tick), new_buy))
                    new_nt   = round(size_usd / new_buy, 2)
                    if new_nt >= 1.0:
                        log.info(
                            f"  [Executor] re-rounding price {buy_price}->{new_buy} "
                            f"and tick {tick_size}->{new_tick}, retrying"
                        )
                        buy_price = new_buy
                        tick_size = new_tick
                        num_tokens = new_nt
                        order_args = OrderArgs(token_id=token_id, price=buy_price,
                                               size=num_tokens, side=BUY)
                        options   = PartialCreateOrderOptions(
                            tick_size=tick_size, neg_risk=neg_risk
                        )
                        if attempt < attempts:
                            time.sleep(1)
                        continue

                # Truly permanent errors — don't waste retries
                permanent_markers = (
                    "market not found", "not found",
                    "min size", "minimum order", "not allowed",
                )
                if any(p in msg.lower() for p in permanent_markers):
                    log.error(f"  [Executor] permanent error, no retry: {msg}")
                    break

                log.warning(
                    f"  [Executor] order attempt {attempt}/{attempts} raised for {tag}: {msg}"
                )
                if attempt < attempts:
                    time.sleep(2)
                continue

            order_id = response.get("orderID") or response.get("order_id")
            success_flag = response.get("success")
            err_field    = response.get("error") or response.get("errorMsg")
            # Treat as success ONLY if an orderID was returned AND there's
            # no embedded error. Polymarket sometimes returns HTTP 200 with
            # an error body — that is NOT a success.
            if order_id and (success_flag is None or success_flag is True) and not err_field:
                break
            last_err = err_field or str(response)
            order_id = None
            log.warning(
                f"  [Executor] order attempt {attempt}/{attempts} failed for {tag}: {last_err}"
            )
            if attempt < attempts:
                time.sleep(2)

        if not order_id:
            log.error(f"  [Executor] ❌ order placement gave up after {attempts} attempts for {tag}: {last_err}")
            if signal_id:
                _update_signal_order(signal_id, None, "failed", None)
            send_alert(
                subject=f"Order placement failed — {tag}",
                body=(
                    f"Failed to place order after {attempts} attempts.\n"
                    f"Trade:  {tag}\n"
                    f"Signal: {signal_id}\n"
                    f"Last error: {last_err}\n\n"
                    f"order_status set to 'failed'. Loop continues; investigate "
                    f"if many of these happen in a short window."
                ),
                severity="warning",
                alert_key=f"order_failed_{condition_id}_{side}",
            )
            return {"status": "error", "reason": last_err or "unknown"}

        log.info(
            f"  [Executor] ✅ LIVE order placed: {order_id} | "
            f"{num_tokens:.2f} tokens @ {buy_price*100:.1f}¢ | ${size_usd:.2f} | {phase}"
        )
        if signal_id:
            telemetry = {
                "intended_price": round(buy_price, 6),
                "bid_at_signal":  book_snap.get("bid"),
                "ask_at_signal":  book_snap.get("ask"),
                "mid_at_signal":  book_snap.get("mid"),
            }
            _update_signal_order(signal_id, order_id, "pending", None, telemetry=telemetry)

        return {
            "status":     "placed",
            "order_id":   order_id,
            "price":      buy_price,
            "num_tokens": num_tokens,
            "size_usd":   size_usd,
        }

    except Exception as e:
        log.error(f"  [Executor] Order placement error for {tag}: {e}")
        if signal_id:
            _update_signal_order(signal_id, None, "failed", None)
        send_alert(
            subject=f"Order placement error — {tag}",
            body=(
                f"Unhandled error in place_order().\n"
                f"Trade:  {tag}\n"
                f"Signal: {signal_id}\n"
                f"Error:  {e}\n\n"
                f"The order_status was set to 'failed'. The loop continues "
                f"but this trade is lost. Investigate if recurring."
            ),
            severity="warning",
            alert_key=f"order_exception_{condition_id}_{side}",
        )
        return {"status": "error", "reason": str(e)}


# ── Sell path (added 2026-05-21) ──────────────────────────────────────────────
#
# When a Phase 2 YES position goes stale (running_max climbs past the locked
# bracket's upper bound), the YES token can no longer pay $1 — it's headed
# to $0. There's usually still a small bid on the orderbook (1-5¢) we can
# capture by selling. This function does that.
#
# Pre-conditions:
#   • signal row has side='YES', order_status='filled', winning_bracket null
#   • sold_at is null (we haven't already sold)
#
# Post-conditions (on success):
#   • SELL order placed and accepted by CLOB
#   • signal row updated: sold_at, sold_price, sold_size_usd, recovered_usd, pnl_usd
#   • The pnl_usd reflects the loss after recovery: recovered − filled_size_usd
#
# Idempotency: refuses to act if sold_at is already set on the signal row.

def sell_position(signal_id: str, dry_run: bool = False, slippage_pct: float = 0.02) -> dict:
    """Sell the YES token position associated with signal_id at the current best bid
    (minus slippage_pct to ensure a fill). Returns a status dict.

    slippage_pct=0.02 means "sell at best_bid * 0.98" — fills more reliably than
    sitting exactly at best_bid where someone could take the spot ahead of us.
    """
    if not signal_id:
        return {"status": "error", "reason": "missing_signal_id"}

    # Look up signal row
    try:
        sig_res = (sb.table("trade_signals")
                   .select("id, condition_id, side, fill_price, filled_size_usd, "
                           "sold_at, order_status, winning_bracket, city, outcome")
                   .eq("id", signal_id).single().execute())
    except Exception as e:
        log.warning(f"  [Executor.sell] signal {signal_id} lookup failed: {e}")
        return {"status": "error", "reason": "lookup_failed"}
    sig = sig_res.data
    if not sig:
        return {"status": "error", "reason": "signal_not_found"}

    # Pre-condition checks
    if sig.get("side") != "YES":
        return {"status": "skipped", "reason": "not_a_yes_position"}
    if sig.get("order_status") != "filled":
        return {"status": "skipped", "reason": f"order_status={sig.get('order_status')}"}
    if sig.get("sold_at") is not None:
        return {"status": "skipped", "reason": "already_sold"}
    if sig.get("winning_bracket") is not None:
        return {"status": "skipped", "reason": "market_already_resolved"}

    condition_id   = sig.get("condition_id") or ""
    fill_price     = float(sig.get("fill_price") or 0)
    filled_size_usd = float(sig.get("filled_size_usd") or 0)
    if fill_price <= 0 or filled_size_usd <= 0:
        return {"status": "error", "reason": "invalid_fill_data"}
    num_tokens = round(filled_size_usd / fill_price, 2)

    city    = sig.get("city", "?")
    bracket = sig.get("outcome", "?")
    tag = f"SELL YES {city} [{bracket}] {num_tokens} tokens (entry ${fill_price:.3f} → recovery)"

    if not LIVE_TRADING:
        log.info(f"  [Executor.sell] 📄 PAPER {tag}")
        return {"status": "paper", "num_tokens": num_tokens, "fill_price": fill_price}

    if dry_run:
        log.info(f"  [Executor.sell] DRY {tag}")
        return {"status": "dry_run", "num_tokens": num_tokens}

    client = _get_client()
    if client is None:
        log.error(f"  [Executor.sell] {tag}: CLOB client init failed")
        return {"status": "error", "reason": "client_init_failed"}

    yes_token, _no_token = _get_clob_token_ids(condition_id)
    if not yes_token:
        log.warning(f"  [Executor.sell] {tag}: no YES token id")
        return {"status": "error", "reason": "no_token_id"}

    # Snapshot the orderbook to find best bid
    try:
        book = client.get_order_book(yes_token)
    except Exception as e:
        log.warning(f"  [Executor.sell] {tag}: book fetch failed — {e}")
        return {"status": "error", "reason": "book_fetch_failed"}
    if not book.bids:
        log.warning(f"  [Executor.sell] {tag}: no bids on book — nothing to sell into")
        return {"status": "error", "reason": "no_bids"}
    best_bid = float(book.bids[0].price)
    if best_bid <= 0:
        return {"status": "error", "reason": "best_bid_zero"}

    # Sell slightly below best_bid for a more reliable fill. Floor at 1 tick.
    sell_price = round(max(0.01, best_bid * (1.0 - slippage_pct)), 4)
    log.info(f"  [Executor.sell] {tag} | book bid={best_bid:.3f} → submitting SELL @ {sell_price:.3f}")

    try:
        from clob_http import OrderArgs, OrderType, PartialCreateOrderOptions, SELL
        market = client.get_market(condition_id)
        tick_size = str(market.get("minimum_tick_size", "0.01"))
        neg_risk  = bool(market.get("neg_risk", False))
        order_args = OrderArgs(
            token_id=yes_token, price=sell_price, size=num_tokens, side=SELL,
        )
        opts = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
        resp = client.create_and_post_order(order_args, opts, OrderType.GTC)
    except Exception as e:
        log.error(f"  [Executor.sell] {tag}: order submit failed — {e}")
        return {"status": "error", "reason": "submit_failed", "detail": str(e)}

    if not resp.get("success"):
        log.warning(f"  [Executor.sell] {tag}: CLOB rejected — {resp.get('errorMsg')}")
        return {"status": "error", "reason": "clob_rejected", "detail": resp.get("errorMsg")}

    sell_order_id = resp.get("orderID") or resp.get("orderId") or resp.get("id")
    # Mark the signal row immediately (sale recorded; fill confirmation via cron)
    sold_value     = round(num_tokens * sell_price, 4)
    recovered_usd  = sold_value     # net recovered (fees are 0 on weather markets)
    pnl_after_sell = round(recovered_usd - filled_size_usd, 4)
    try:
        sb.table("trade_signals").update({
            "sold_at":       datetime.now(timezone.utc).isoformat(),
            "sold_price":    sell_price,
            "sold_size_usd": sold_value,
            "recovered_usd": recovered_usd,
            "pnl_usd":       pnl_after_sell,
        }).eq("id", signal_id).execute()
    except Exception as e:
        log.warning(f"  [Executor.sell] {tag}: DB update failed — {e}")

    log.info(
        f"  [Executor.sell] ✅ {tag} | sold {num_tokens} @ {sell_price:.3f} → "
        f"recovered ${recovered_usd:.2f} (entry was ${filled_size_usd:.2f}, "
        f"net P&L ${pnl_after_sell:+.2f})  order_id={sell_order_id}"
    )
    return {
        "status":         "placed",
        "order_id":       sell_order_id,
        "sell_price":     sell_price,
        "num_tokens":     num_tokens,
        "recovered_usd":  recovered_usd,
        "pnl_after_sell": pnl_after_sell,
    }


# ── Fill checker ──────────────────────────────────────────────────────────────

def check_and_update_orders() -> int:
    """
    Poll the CLOB for fill status of all 'pending' orders.
    Called once per temp_monitor cycle (every 5 minutes).

    • Filled orders  → update fill_price, set order_status='filled'
    • Timed-out orders (> MAKER_TIMEOUT_MINS) → cancel maker, retry as taker
    • Returns count of orders updated this cycle.
    """
    if not LIVE_TRADING:
        return 0   # nothing to check in paper mode

    try:
        # Explicit .limit(2_000) — defensive against Supabase's silent
        # 1000-row default cap.  Pending orders normally clear within
        # minutes so 2000 is well above any realistic count; if it ever
        # hits the cap, that itself is a signal that something's stuck
        # and worth alerting on (caught by watchdog separately).
        res = (
            sb.table("trade_signals")
            .select("id, order_id, side, condition_id, recommended_position, market_price, created_at, signal_phase")
            .eq("order_status", "pending")
            .limit(2_000)
            .execute()
        )
        pending = res.data or []
    except Exception as e:
        log.warning(f"  [Executor] fill-check DB query failed: {e}")
        return 0

    if not pending:
        return 0

    client = _get_client()
    if client is None:
        return 0

    processed = 0
    now       = datetime.now(timezone.utc)

    for sig in pending:
        order_id  = sig.get("order_id")
        signal_id = sig["id"]
        if not order_id:
            continue

        try:
            order = client.get_order(order_id)
        except Exception as e:
            log.warning(f"  [Executor] get_order({order_id}) failed: {e}")
            continue

        status  = (order.get("status") or "").lower()
        matched = float(order.get("size_matched") or 0)

        # ── Filled / partially filled ─────────────────────────────────────────
        if status in ("matched", "filled") or matched > 0:
            # Only record fill_price when Polymarket gives us a real avg_price.
            # During partial fills, get_order() can return avg_price=None even
            # though size_matched > 0.  Writing the SIGNAL price here would
            # corrupt P&L later.  Better to leave fill_price null and let the
            # next polling cycle pick up the real avg when the order fully
            # matches.  As a last resort (order disappears from the API), use
            # the data-api positions endpoint to recover the true avg.
            avg_raw = order.get("avg_price")
            fill_price: float | None = None
            if avg_raw is not None:
                try:
                    fill_price = float(avg_raw)
                except (TypeError, ValueError):
                    fill_price = None

            # Backstop: if we have ANY match (matched > 0) but no avg_price,
            # fall back to data-api /positions which always knows the true
            # blended avg.  Previously this ran only when status ∈ {matched,
            # filled} — the Houston 2026-05-17 incident showed that partial
            # resting orders can have matched > 0 with status='live' AND
            # avg_price=None, so the backstop was skipped and the row sat
            # forever with fill_price=null and filled_size_usd=null, causing
            # the dashboard to over-report exposure 5× ($15 intent vs $3.06
            # actually filled).  Now we backstop on any partial.
            if fill_price is None and matched > 0:
                try:
                    funder = os.getenv("POLY_FUNDER_ADDRESS", "")
                    if funder:
                        pos = requests.get(
                            "https://data-api.polymarket.com/positions",
                            params={"user": funder}, timeout=10,
                        ).json()
                        cond = sig.get("condition_id", "")
                        for p in pos or []:
                            if str(p.get("conditionId","")).lower() == cond.lower():
                                fill_price = float(p.get("avgPrice"))
                                break
                except Exception as bk_err:
                    log.debug(f"  [Executor] data-api avg lookup failed: {bk_err}")

            shown = f"{fill_price*100:.1f}¢" if fill_price is not None else "pending avg"
            log.info(f"  [Executor] ✅ Filled: {order_id} @ {shown} ({matched:.2f} tokens)")

            telemetry: dict = {"fill_time": now.isoformat()}
            created_str = sig.get("created_at", "")
            if created_str:
                try:
                    created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    telemetry["fill_latency_ms"] = int((now - created_at).total_seconds() * 1000)
                except Exception:
                    pass

            # Capture the ACTUAL filled cost in dollars so the resolver computes
            # P&L on what was really deployed, not the intended order size.
            # Partial fills are the common case for low-price orders that exhaust
            # book depth — see the Cape Town/Amsterdam pattern.  If both
            # fill_price and matched are known, this is the truthful cost basis.
            if fill_price is not None and matched > 0:
                telemetry["filled_size_usd"] = round(fill_price * matched, 4)

            # status flips to filled as soon as ANY match exists, so the
            # ladder/resolver see the trade; fill_price is null until known.
            _update_signal_order(signal_id, order_id, "filled", fill_price, telemetry=telemetry)
            processed += 1
            continue

        # ── Maker timeout → cancel and retry as taker ─────────────────────────
        created_str = sig.get("created_at", "")
        if created_str:
            try:
                created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                age_mins   = (now - created_at).total_seconds() / 60

                if age_mins > MAKER_TIMEOUT_MINS:
                    log.info(
                        f"  [Executor] ⏰ Maker timeout ({age_mins:.0f} min): "
                        f"{order_id} — cancelling and retrying as taker"
                    )
                    try:
                        client.cancel(order_id)
                    except Exception:
                        pass  # ignore cancel errors; old order may have expired

                    _retry_as_taker(client, sig, signal_id)
                    processed += 1
                    continue
            except Exception:
                pass  # malformed timestamp — leave order alone

    if processed:
        log.info(f"  [Executor] Fill check: {processed} order(s) updated this cycle")
    return processed


# ── Taker retry ───────────────────────────────────────────────────────────────

def _retry_as_taker(client, sig: dict, signal_id: str) -> None:
    """
    Re-place a timed-out maker order as a taker order (crosses the spread).
    Price = best ask + 2¢ to guarantee a fill.  Taker fee is 1.25%; only use
    when the maker has sat unfilled for MAKER_TIMEOUT_MINS.
    """
    try:
        from clob_http import OrderArgs, OrderType, PartialCreateOrderOptions, BUY

        condition_id = sig["condition_id"]
        side         = sig.get("side", "YES")
        size_usd     = float(sig["recommended_position"])

        yes_token, no_token = _get_clob_token_ids(condition_id)
        if not yes_token:
            log.warning(f"  [Executor] taker retry: no token for {condition_id[:12]}…")
            _update_signal_order(signal_id, None, "failed", None)
            return

        token_id = yes_token if side == "YES" else no_token

        # Snapshot book before taker retry
        book_snap = _snapshot_book(client, token_id)
        live_ask = book_snap.get("ask")
        if live_ask is None:
            log.warning(f"  [Executor] taker retry: no order book data")
            _update_signal_order(signal_id, None, "failed", None)
            return

        taker_price = min(round(live_ask + 0.02, 4), 0.98)
        num_tokens  = round(size_usd / taker_price, 2)

        if num_tokens < 1.0:
            _update_signal_order(signal_id, None, "observation", taker_price)
            return

        # Apply the same Phase-2 cap final guard used in the maker path:
        # if the taker retry would lift the entry price above the +EV
        # envelope, don't honor the retry.  The original maker order is
        # already cancelled by this point, so a downgrade just means
        # "we skip this trade today" — better than buying above the cap.
        if sig.get("signal_phase") == "phase2":
            try:
                from config import PHASE2_MAX_CALIBRATED_PRICE
                cap = float(PHASE2_MAX_CALIBRATED_PRICE)
            except Exception:
                cap = 0.30
            if taker_price >= cap:
                log.warning(
                    f"  [Executor] taker retry for {sig.get('city','?')} "
                    f"{sig.get('outcome','?')}: taker_price {taker_price*100:.1f}¢ "
                    f">= cap {cap*100:.0f}¢ — abandoning retry"
                )
                _update_signal_order(signal_id, None, "observation", taker_price)
                return

        try:
            market = client.get_market(condition_id)
            tick_size = str(market.get("minimum_tick_size", "0.01"))
            neg_risk  = bool(market.get("neg_risk", False))
        except Exception:
            tick_size, neg_risk = "0.01", False
        order_args = OrderArgs(
            token_id=token_id,
            price=taker_price,
            size=num_tokens,
            side=BUY,
        )
        options  = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)

        # Retry-on-transient-error pattern, same as the maker path.
        import time
        attempts = 3
        last_err: str | None = None
        new_order_id: str | None = None
        permanent_markers = (
            "invalid tick size", "tick size", "minimum_tick_size",
            "not allowed", "market not found", "not found",
            "min size", "minimum order",
        )
        for attempt in range(1, attempts + 1):
            try:
                response = client.create_and_post_order(
                    order_args, options=options, order_type=OrderType.GTC,
                )
            except Exception as call_err:
                msg = str(call_err)
                last_err = msg
                if any(p in msg.lower() for p in permanent_markers):
                    log.error(f"  [Executor] taker-retry permanent error, no retry: {msg}")
                    break
                log.warning(
                    f"  [Executor] taker-retry attempt {attempt}/{attempts} raised: {msg}"
                )
                if attempt < attempts:
                    time.sleep(2)
                continue

            oid = response.get("orderID") or response.get("order_id")
            success_flag = response.get("success")
            err_field    = response.get("error") or response.get("errorMsg")
            if oid and (success_flag is None or success_flag is True) and not err_field:
                new_order_id = oid
                break
            last_err = err_field or str(response)
            log.warning(
                f"  [Executor] taker-retry attempt {attempt}/{attempts} failed: {last_err}"
            )
            if attempt < attempts:
                time.sleep(2)

        if not new_order_id:
            log.error(f"  [Executor] ❌ taker retry gave up after {attempts} attempts: {last_err}")
            _update_signal_order(signal_id, None, "failed", None)
            send_alert(
                subject=f"Taker retry failed — {sig.get('city','?')} {sig.get('outcome','?')}",
                body=(
                    f"Maker order timed out and the taker retry could not place "
                    f"after {attempts} attempts.\n"
                    f"Signal: {signal_id}\n"
                    f"Last error: {last_err}\n"
                ),
                severity="warning",
                alert_key=f"taker_retry_failed_{signal_id}",
            )
            return

        log.info(
            f"  [Executor] 🔄 Taker retry placed: {new_order_id} | "
            f"{num_tokens:.2f} tokens @ {taker_price*100:.1f}¢ | ${size_usd:.2f}"
        )
        telemetry = {
            "intended_price": round(taker_price, 6),
            "bid_at_signal":  book_snap.get("bid"),
            "ask_at_signal":  book_snap.get("ask"),
            "mid_at_signal":  book_snap.get("mid"),
        }
        _update_signal_order(signal_id, new_order_id, "pending", None, telemetry=telemetry)

    except Exception as e:
        log.error(f"  [Executor] taker retry error: {e}")
        _update_signal_order(signal_id, None, "failed", None)


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s UTC | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    print(f"LIVE_TRADING      = {LIVE_TRADING}")
    print(f"MAKER_TIMEOUT_MINS = {MAKER_TIMEOUT_MINS}")
    if LIVE_TRADING:
        c = _get_client()
        print("CLOB client OK" if c else "CLOB client FAILED")
    else:
        print("Paper mode — no CLOB connection needed")

    n = check_and_update_orders()
    print(f"Fill check: {n} order(s) processed")
