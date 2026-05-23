# Polymarket Trader Analyzer

A premium trader-analysis tool integrated into the Weather Trader dashboard.
Plug in a Polymarket wallet or username → get full profile stats, by-day
breakdown, strategy classification, weather-specific dissection (entry timing
vs GFS runs, price-bucket P&L, city specialization), open positions, and
AI commentary framed against our current strategy.

## Architecture

```
React UI (Netlify)
    │
    │ Authorization: Bearer <ANALYZER_AUTH_TOKEN>
    ▼
Caddy (TLS, reverse proxy)  ← runs on bot server
    │
    ▼
FastAPI worker on 127.0.0.1:8001
    │
    ├─→ data-api.polymarket.com  (trades, positions)
    ├─→ gamma-api.polymarket.com (market resolutions)
    ├─→ lb-api.polymarket.com    (username → address)
    ├─→ api.anthropic.com        (commentary)
    └─→ Supabase                 (cache + watchlist)
```

The vendored polymarket-toolkit (MIT, runesleo) does the audit-grade PnL
via cashflow reconstruction. We layer our own weather analytics on top.

## Deployment runbook

Do these once in order. Each step is verifiable before moving on.

### 1. Supabase migration

In the Supabase SQL editor for the existing project, run:

```sql
-- scripts/migrate_analyzer.sql
```

Verify three tables now exist: `analyzer_runs`, `analyzer_commentary`,
`analyzer_watchlist`.

### 2. Bot server — install analyzer

SSH to the bot server (where `/root/polymarket/` lives). Then:

```bash
cd /root/polymarket
# Copy this entire `analyzer/` directory here. Easiest: rsync from your laptop:
#   rsync -av --exclude .venv ./analyzer/ root@BOT_HOST:/root/polymarket/analyzer/

cd /root/polymarket/analyzer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in:
#   ANALYZER_AUTH_TOKEN      = openssl rand -hex 32  (save this — you need it in the UI)
#   ANTHROPIC_API_KEY        = your Anthropic key
#   SUPABASE_URL / SUPABASE_KEY = same values your bot uses
```

Sanity-check it can boot:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
# In another shell:
curl http://127.0.0.1:8001/health
# Should return: {"status":"ok","version":"0.1.0"}
# Ctrl-C to stop.
```

### 3. Bot server — install systemd unit

```bash
cp /root/polymarket/analyzer/systemd/analyzer.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable analyzer
systemctl start analyzer
systemctl status analyzer       # should be "active (running)"
journalctl -u analyzer -f       # tail logs
```

### 4. Bot server — Caddy TLS proxy

Install Caddy if not present:

```bash
# Ubuntu/Debian
apt install caddy
```

Point a subdomain to your bot server IP via DNS, then:

```bash
# Edit /etc/caddy/Caddyfile — copy in the block from analyzer/caddy/Caddyfile.example
# Replace analyzer.example.com with your actual subdomain
caddy fmt --overwrite /etc/caddy/Caddyfile
systemctl reload caddy
```

Verify TLS works:

```bash
curl https://analyzer.your-subdomain.com/health
# Should return: {"status":"ok","version":"0.1.0"}
```

### 5. Optional — restrict CORS

By default the worker allows requests from `weatherornotbot.netlify.app` and
localhost. If you have a custom domain, set in `.env`:

```
ANALYZER_CORS_ORIGINS=https://weatherornotbot.netlify.app,https://your-custom-domain.com
```

Then `systemctl restart analyzer`.

### 6. Frontend — add Netlify env var

In Netlify dashboard → Site → Settings → Environment variables:

- `VITE_ANALYZER_URL` = `https://analyzer.your-subdomain.com`

Trigger a redeploy. The new "Trader Analyzer" tab is now live in the dashboard.

### 7. Test end-to-end

1. Open the dashboard, click "Trader Analyzer"
2. Paste the `ANALYZER_AUTH_TOKEN` value into the unlock prompt
3. Try wallet: `0x81035115a389a085e36255e5cb9b9ab8ee3723a1` (fridius2 — known
   ~30K trades, fetch takes ~2 minutes the first time, cached for 1h after)
4. Verify all panels populate: profile stats, strategy badge, by-day, weather
   dissection (price-bucket P&L is the key one), open positions
5. Click "Analyze (Sonnet)" — should return ~400 words of markdown commentary
   referencing your strategy context

## Editing strategy context

The AI commentary reads three live sources at each request:

1. `scripts/config.py` (bot params: MIN_EDGE, KELLY_FRACTION, etc.)
2. Supabase `system_config` and `settings` tables (live bankroll, mode)
3. `analyzer/strategy_context.md` (free-form notes you can edit)

When your thinking evolves — e.g. you learn something new about a city's
delta accuracy, or want Claude to consider a new angle when analyzing
traders — edit `strategy_context.md`. No restart needed; the file is read
on every commentary call.

## Cost notes

- **Toolkit fetches**: free (public APIs).
- **Anthropic commentary**: ~$0.02 (Sonnet) or ~$0.10 (Opus) per analysis.
  System prompt is cached for 5 minutes → back-to-back analyses are cheaper.
  Supabase persists every commentary so you can re-read old ones for free.
- **Supabase storage**: trivial; one JSONB row per analyzed wallet.

## Operational notes

- The worker is `nice -n 10` so it can't starve the trading bot under load.
- `CACHE_TTL_SECONDS=3600` means repeated lookups within 1h return cached
  results. Force fresh with the refresh button in the UI.
- Long fetches (whales w/ 30K+ trades) can take 2+ minutes. Caddy timeouts
  are set to 10 minutes so this works.
- If the worker hangs, `systemctl restart analyzer` is safe — no shared
  state with the trading bot.

## Phase 3 — Watchlist (not built yet)

`analyzer_watchlist` table is created; once we want this, the next pieces are:
- Daily cron polling watched wallets for new activity
- Diff against previous snapshot, alert on significant moves
- Add `Add to watchlist` button in the UI
