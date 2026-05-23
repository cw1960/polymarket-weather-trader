# Vendored: polymarket-toolkit

Upstream: https://github.com/runesleo/polymarket-toolkit
License: MIT (see LICENSE)
Pinned commit: `ee5457145b0033af135dc8174dc24017af65f61a`
Vendored at: 2026-05-15

## What we use directly

- `polymarket-pnl/compute_precise_pnl.py` — cashflow-reconstruction PnL engine
- `polymarket-pnl/lib/pm_http.py` — HTTP client wrapper
- `polymarket-pnl/lib/checkpoint.py` — incremental fetch state

## What we adapted (not vendored as code)

- `profile_SKILL.md` — instruction pattern; our equivalent lives in `analyzer/app/profile.py`
- `brier_SKILL.md` — instruction pattern; our equivalent lives in `analyzer/app/brier.py`

## Update procedure

When updating to a newer upstream commit:

1. `git clone --depth 1 https://github.com/runesleo/polymarket-toolkit /tmp/pm-toolkit`
2. Diff `skills/polymarket-pnl/` against this vendored copy
3. Test against a known wallet (e.g. fridius2 `0x81035115...`) — PnL should still match within ~0.5%
4. Update pinned commit hash above
