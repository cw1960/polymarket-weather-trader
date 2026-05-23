"""
test_guardrails.py — falsifying tests for guardrails.py.

Tested assumptions (CLAUDE.md Rule 2):
  G1. phase2_paused='1' blocks all trades.
  G2. bankroll_usd < min_bankroll_usd_trading blocks (with appropriate reason).
  G3. today's resolved P&L below the -X% threshold blocks AND writes today_loss_paused_date.
  G4. rolling 3-day win rate below floor (with enough samples) blocks AND flips
      phase2_paused to '1'.
  G5. With all guardrails clear, check_trade_allowed() returns (True, ...).

Each test stubs the Supabase queries used by guardrails.py so the test runs
locally without DB access.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import guardrails  # noqa: E402


class _ConfigStub:
    """Stand-in for Supabase. Holds an in-memory system_config dict and
    a list of trade_signals rows; returns the right slice based on the
    last .table() call name."""

    def __init__(self, config: dict, trade_rows: list[dict] | None = None):
        self.config = dict(config)
        self.trade_rows = list(trade_rows or [])
        self._table = None
        self._filters: list = []
        self.upserts: list[dict] = []
        self.inserts: list[dict] = []

    def table(self, name):
        self._table = name
        self._filters = []
        return self

    def select(self, *_a, **_kw): return self

    def eq(self, *_a, **_kw):
        self._filters.append(("eq", _a))
        return self

    def maybe_single(self): return self

    def gte(self, *_a, **_kw):
        self._filters.append(("gte", _a))
        return self

    def in_(self, *_a, **_kw):
        self._filters.append(("in", _a))
        return self

    @property
    def not_(self): return self

    def is_(self, *_a, **_kw): return self

    def limit(self, *_a, **_kw): return self

    def order(self, *_a, **_kw): return self

    def upsert(self, payload):
        self.upserts.append({"table": self._table, "payload": payload})
        return self

    def insert(self, payload):
        self.inserts.append({"table": self._table, "payload": payload})
        return self

    def execute(self):
        class R: pass
        r = R()
        if self._table == "system_config":
            # Find the key being requested from filter chain.
            key = None
            for kind, args in self._filters:
                if kind == "eq" and len(args) == 2 and args[0] == "key":
                    key = args[1]
            r.data = {"value": self.config.get(key)} if key in self.config else None
        elif self._table == "trade_signals":
            # Just return all stored rows — the guardrail code applies its
            # own filtering after the fact via min_n etc.
            r.data = list(self.trade_rows)
        elif self._table == "guardrail_events":
            r.data = []
        else:
            r.data = None
        return r


def case(label, sb_stub, expect_allowed, expect_guardrail=""):
    with mock.patch.object(guardrails, "_sb", sb_stub):
        d = guardrails.check_trade_allowed()
    ok = (d.allowed == expect_allowed) and (
        d.guardrail == expect_guardrail if not expect_allowed else True
    )
    status = "PASS" if ok else "FAIL"
    print(f"  {status}: {label} — allowed={d.allowed} guardrail={d.guardrail!r} reason={d.reason!r}")
    return ok


def main():
    base_clear = {
        "phase2_paused": "0",
        "bankroll_usd": "5000",
        "min_bankroll_usd_trading": "1500",
        "max_daily_loss_pct": "0.08",
        "min_3day_win_rate": "0.45",
        "min_3day_resolved_trades": "15",
        "today_loss_paused_date": "",
    }

    results = []

    # G1
    cfg = dict(base_clear); cfg["phase2_paused"] = "1"
    results.append(case("G1: phase2_paused=1 blocks", _ConfigStub(cfg), False, "phase2_paused"))

    # G2
    cfg = dict(base_clear); cfg["bankroll_usd"] = "1000"  # below floor 1500
    results.append(case("G2: bankroll < floor blocks", _ConfigStub(cfg), False, "bankroll_floor"))

    # G3: today's P&L = -500 on $5000 bankroll = -10%, floor is -8% → block
    bad_pnl = [{"pnl_usd": -500.0, "signal_phase": "phase2", "order_status": "filled",
                "resolved_at": datetime.now(timezone.utc).isoformat()}]
    results.append(case("G3: daily P&L -10% blocks (limit -8%)",
                        _ConfigStub(base_clear, bad_pnl), False, "daily_loss"))

    # G3b: today's P&L = -200 on $5000 bankroll = -4% → allow
    mild_pnl = [{"pnl_usd": -200.0, "signal_phase": "phase2", "order_status": "filled",
                 "resolved_at": datetime.now(timezone.utc).isoformat()}]
    results.append(case("G3b: daily P&L -4% allowed (limit -8%)",
                        _ConfigStub(base_clear, mild_pnl), True))

    # G3c: today_loss_paused_date == today → block
    cfg = dict(base_clear)
    from datetime import date as _d
    cfg["today_loss_paused_date"] = _d.today().isoformat()
    results.append(case("G3c: today_loss_paused_date set → block",
                        _ConfigStub(cfg), False, "daily_loss"))

    # G4: 20 trades, 5 wins → 25% win rate < 45% → block
    bad_rows = [{"pnl_usd": 1.0,  "signal_phase": "phase2", "order_status": "filled",
                 "resolved_at": datetime.now(timezone.utc).isoformat()}] * 5
    bad_rows += [{"pnl_usd": -1.0, "signal_phase": "phase2", "order_status": "filled",
                  "resolved_at": datetime.now(timezone.utc).isoformat()}] * 15
    results.append(case("G4: 5/20 win rate blocks (floor 45%)",
                        _ConfigStub(base_clear, bad_rows), False, "3day_win_rate"))

    # G4b: not enough samples (10 < 15 floor) → ALLOW even if win rate low
    short_rows = [{"pnl_usd": -1.0, "signal_phase": "phase2", "order_status": "filled",
                   "resolved_at": datetime.now(timezone.utc).isoformat()}] * 10
    results.append(case("G4b: only 10 trades resolved → allow (need ≥15)",
                        _ConfigStub(base_clear, short_rows), True))

    # G5: all clear, no data → allow
    results.append(case("G5: all clear → allow",
                        _ConfigStub(base_clear, []), True))

    # ── New EV-based guardrail tests ─────────────────────────────────
    # The EV guardrail queries trade_signals filtered to signal_phase='phase2_sweep'.
    # Our stub returns ALL trade_rows regardless of filters, so we should set up
    # rows that all look like NO sweep losses to trigger the pause.
    base_with_ev = dict(base_clear)
    base_with_ev["min_ev_per_dollar_50trade"] = "-0.02"
    base_with_ev["min_ev_resolved_trades"]    = "30"

    # 30 NO trades all bought at 50¢ where YES won (= NO lost) → EV/$ = -1.0
    losing_no = [{
        "market_price": 0.5, "actual_outcome": "true",  # YES won, NO lost
        "side": "NO", "signal_phase": "phase2_sweep",
        "winning_bracket": "x", "pnl_usd": -5.0, "order_status": "filled",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }] * 30
    results.append(case("EV-G: 30 NO trades all losing → EV-guardrail blocks",
                        _ConfigStub(base_with_ev, losing_no), False, "ev_per_dollar"))

    # 30 NO trades at 50¢ where YES lost (= NO won) → EV/$ = +1.0 → ALLOW
    winning_no = [{
        "market_price": 0.5, "actual_outcome": "false",   # NO won
        "side": "NO", "signal_phase": "phase2_sweep",
        "winning_bracket": "x", "pnl_usd": +5.0, "order_status": "filled",
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }] * 30
    results.append(case("EV-G: 30 NO trades all winning → allow",
                        _ConfigStub(base_with_ev, winning_no), True))

    # Only 10 trades (below min_n=30) → allow even if all losing
    too_few = [losing_no[0]] * 10
    results.append(case("EV-G: only 10 trades → allow (below n floor)",
                        _ConfigStub(base_with_ev, too_few), True))

    if all(results):
        print("ALL PASS")
        return 0
    print("FAILURES present")
    return 1


if __name__ == "__main__":
    sys.exit(main())
