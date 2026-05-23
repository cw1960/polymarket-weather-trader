from dotenv import load_dotenv
import os

load_dotenv()

SUPABASE_URL = os.getenv("VITE_SUPABASE_URL", "")
# Scripts use the service role key to bypass RLS; frontend uses the anon key
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("VITE_SUPABASE_ANON_KEY", "")

# All 50 active Polymarket cities
CITIES = [
    # US (°F)
    "NYC", "Chicago", "Miami", "Los Angeles", "Dallas", "Atlanta",
    "Houston", "Austin", "Seattle", "San Francisco", "Denver",
    # Europe (°C)
    "London", "Paris", "Madrid", "Munich", "Milan", "Amsterdam",
    "Warsaw", "Helsinki", "Istanbul", "Ankara",
    # Russia
    "Moscow",
    # Middle East
    "Tel Aviv", "Jeddah",
    # East Asia
    "Hong Kong", "Seoul", "Tokyo", "Busan", "Taipei",
    "Beijing", "Shanghai", "Guangzhou", "Shenzhen",
    "Chengdu", "Chongqing", "Wuhan",
    # Southeast Asia
    "Singapore", "Kuala Lumpur", "Manila", "Jakarta",
    # South Asia
    "Lucknow", "Karachi",
    # Oceania
    "Wellington",
    # Americas (non-US)
    "Toronto", "Mexico City", "São Paulo", "Buenos Aires", "Panama City",
    # Africa
    "Cape Town", "Lagos",
]

# Maps city → NOAA GHCN station ID for the Polymarket resolution station.
# These MUST match exactly what Polymarket uses to resolve each market.
# See seed_stations.sql for confidence levels and full station names.
NOAA_STATIONS = {
    # US
    "NYC":           "USW00014732",  # LaGuardia Airport
    "Chicago":       "USW00094846",  # O'Hare International
    "Miami":         "USW00012839",  # Miami International
    "Los Angeles":   "USW00023174",  # LAX
    "Dallas":        "USW00013960",  # Dallas Love Field
    "Atlanta":       "USW00013874",  # Hartsfield-Jackson
    "Houston":       "USW00012919",  # William P. Hobby
    "Austin":        "USW00013904",  # Austin-Bergstrom
    "Seattle":       "USW00024233",  # Seattle-Tacoma
    "San Francisco": "USW00023234",  # SFO
    "Denver":        "USW00003017",  # Buckley SFB (VERIFY)
    # Europe
    "London":        "UKM00003772",  # Heathrow
    "Paris":         "FRM00007149",  # Orly (Le Bourget has no TMAX)
    "Madrid":        "SPE00120278",  # Madrid/Barajas
    "Munich":        "GMM00010870",  # Schwaigermoos (2km from MUC airport)
    "Milan":         "ITM00016064",  # Cameri (12km from Malpensa; Malpensa not in GHCN)
    "Amsterdam":     "NLE00152485",  # Schiphol
    "Warsaw":        "PLM00012375",  # Okecie
    "Helsinki":      "FIE00142080",  # Helsinki-Vantaa Airport
    "Istanbul":      "TUM00017064",  # Istanbul Bolge Kartal — closest GHCN to LTFM
    "Ankara":        "TUM00017130",  # Ankara/Central (was TRM prefix, correct is TUM)
    # Russia — post-2022 NOAA data sharing gap; delta uses 2019-2021 climatology
    "Moscow":        "RSM00027612",  # Moscow GSN
    # Middle East
    "Tel Aviv":      "ISM00040180",  # Ben Gurion International
    "Jeddah":        "SA000041024",  # Jeddah King Abdul (was SAM prefix, correct is SA0)
    # East Asia
    "Hong Kong":     "MCM00045011",  # Macau Intl (HK Observatory not in GHCN; 55km proxy)
    "Seoul":         "KS000047112",  # Incheon (GHCN has TAVG/TMIN only; delta=0)
    "Tokyo":         "JA000047662",  # Tokyo (GHCN has TAVG/TMIN only; delta=0)
    "Busan":         "KSM00047159",  # Busan (GHCN has TAVG/TMIN only; delta=0)
    "Taipei":        "TWM00046692",  # Songshan (Taiwan not in GHCN; delta=0)
    "Beijing":       "CHM00054511",  # Beijing Capital
    "Shanghai":      "CHM00058362",  # Pudong
    "Guangzhou":     "CHM00059287",  # Baiyun
    "Shenzhen":      "CHM00059501",  # Bao'an
    "Chengdu":       "CHM00056187",  # Wenjiang (Shuangliu airport not in GHCN; 17km proxy)
    "Chongqing":     "CHM00057516",  # Jiangbei
    "Wuhan":         "CHM00057494",  # Tianhe
    # Southeast Asia
    "Singapore":     "IDM00096087",  # Batam/Hang Nadim (Changi has no TMAX; 30km proxy)
    "Kuala Lumpur":  "MYM00048650",  # KLIA (was MAM prefix, correct is MYM)
    "Manila":        "RP000098429",  # Ninoy Aquino (was RPM prefix, correct is RP0)
    "Jakarta":       "IDM00096749",  # Halim Perdanakusuma
    # South Asia
    "Lucknow":       "IN023351400",  # Lucknow/Amausi Airport (WMO 42369)
    "Karachi":       "PKM00041780",  # Jinnah Intl — Masroor not in GHCN
    # Oceania
    "Wellington":    "NZM00093439",  # Wellington International
    # Americas (non-US)
    "Toronto":       "CA006158355",  # Toronto City (Pearson has no TMAX in GHCN)
    "Mexico City":   "MXM00076680",  # Benito Juárez GSN
    "São Paulo":     "BRM00083004",  # São Paulo (no GHCN TMAX; delta=0)
    "Buenos Aires":  "ARM00087576",  # Ministro Pistarini
    "Panama City":   "PMM00078762",  # Tocumen (no GHCN TMAX; delta=0)
    # Africa
    "Cape Town":     "SFM00068816",  # Cape Town International GSN
    "Lagos":         "NIM00065201",  # Murtala Muhammed (was NGM prefix, correct is NIM)
}

# Cities where NOAA GHCN does not provide TMAX data.
# delta correction = 0 for these; Open-Meteo ensemble used directly.
NO_TMAX_CITIES = {
    "Seoul",       # KS stations have TAVG/TMIN only
    "Tokyo",       # JA stations have TAVG/TMIN only
    "Busan",       # KS stations have TAVG/TMIN only
    "Singapore",   # Changi has no TMAX; Batam proxy also unreliable
    "Taipei",      # Taiwan not in NOAA GHCN
    "São Paulo",   # Brazilian stations near Guarulhos have no TMAX
    "Panama City", # No Tocumen station in GHCN
}

# Which unit each city's Polymarket market resolves in.
# CRITICAL: must match the market resolution rules exactly.
CITY_UNITS = {
    "NYC": "F", "Chicago": "F", "Miami": "F", "Los Angeles": "F",
    "Dallas": "F", "Atlanta": "F", "Houston": "F", "Austin": "F",
    "Seattle": "F", "San Francisco": "F", "Denver": "F",
}
# All cities not listed above resolve in °C (default).

# Nearby comparison stations for US cities (used for delta correction).
# For international cities, delta defaults to 0 until comparison stations are added.
COMPARISON_STATIONS = {
    "NYC":           ["USW00014734"],  # JFK (LaGuardia is the resolution station)
    "Chicago":       ["USW00094830", "USW00014819"],  # Midway, Rockford
    "Miami":         ["USW00092811", "USW00012836"],  # Fort Lauderdale, Homestead
    "Los Angeles":   ["USW00023129", "USW00023188"],  # Burbank, Long Beach
    "Dallas":        ["USW00013911", "USW00013958"],  # Dallas/FW, Waco
    "Atlanta":       ["USW00013876", "USW00013880"],  # Athens, Macon
    "Houston":       ["USW00012960", "USW00012918"],  # IAH, Galveston
    "Austin":        ["USW00013958", "USW00013997"],  # Waco, San Antonio
    "Seattle":       ["USW00024234", "USW00024243"],  # Boeing Field, Olympia
    "San Francisco": ["USW00023230", "USW00023272"],  # Oakland, San Jose
    "Denver":        ["USW00023062", "USW00023066"],  # Denver Intl, Stapleton
}

MIN_EDGE = 0.08
KELLY_FRACTION = 0.15
MAX_POSITION_USD = 100
MAX_PCT_BANKROLL = 0.05
PAPER_TRADING = True

# ── Execution layer ───────────────────────────────────────────────────────────
# LIVE_TRADING = False → paper mode (no CLOB calls, order_status='paper').
# LIVE_TRADING = True  → real money via py-clob-client.
LIVE_TRADING = True

# Cancel an unfilled maker order and retry as taker after this many minutes.
# Taker fee is 1.25% — only pay it when the market has moved past our limit.
MAKER_TIMEOUT_MINS = 30

# ── Phase 2: METAR ICAO codes per resolution station ─────────────────────────
# None = no METAR available; falls back to Open-Meteo current weather.
CITY_ICAO: dict[str, str | None] = {
    # US
    "NYC":           "KLGA",
    "Chicago":       "KORD",
    "Miami":         "KMIA",
    "Los Angeles":   "KLAX",
    "Dallas":        "KDAL",
    "Atlanta":       "KATL",
    "Houston":       "KHOU",
    "Austin":        "KAUS",
    "Seattle":       "KSEA",
    "San Francisco": "KSFO",
    "Denver":        "KBKF",   # Buckley SFB — public METAR despite military status
    # Europe (30-min reporters)
    "London":        "EGLC",   # London City Airport — Polymarket resolves against EGLC via WU (was EGLL Heathrow, 16km away)
    "Paris":         "LFPB",   # Le Bourget — Polymarket resolves against LFPB via WU (was LFPO Orly, wrong airport)
    "Madrid":        "LEMD",
    "Munich":        "EDDM",   # 2km proxy for Schwaigermoos
    "Milan":         "LIMC",   # Milan Malpensa — Polymarket resolves against LIMC via WU (was LIMN Cameri, wrong airport)
    "Amsterdam":     "EHAM",
    "Warsaw":        "EPWA",
    "Helsinki":      "EFHK",
    "Istanbul":      "LTFM",   # Istanbul Airport — Polymarket resolves against LTFM via NOAA (was None → Open-Meteo)
    "Ankara":        "LTAC",
    # Russia
    "Moscow":        "UUWW",   # Vnukovo Airport — Polymarket resolves against UUWW via NOAA (was None → Open-Meteo)
    # Middle East
    "Tel Aviv":      "LLBG",
    "Jeddah":        "OEJN",
    # East Asia
    "Hong Kong":     None,     # HKO King's Park — no ICAO; try HKO API then Open-Meteo
    "Seoul":         "RKSI",
    "Tokyo":         "RJTT",   # Haneda; JMA AMeDAS used when available
    "Busan":         "RKPK",
    "Taipei":        "RCSS",
    "Beijing":       "ZBAA",
    "Shanghai":      "ZSPD",
    "Guangzhou":     "ZGGG",
    "Shenzhen":      "ZGSZ",
    "Chengdu":       "ZUUU",   # 17km proxy for Wenjiang
    "Chongqing":     "ZUCK",
    "Wuhan":         "ZHHH",
    # Southeast Asia
    "Singapore":     "WSSS",
    "Kuala Lumpur":  "WMKK",
    "Manila":        "RPLL",
    "Jakarta":       "WIHH",   # Halim Perdanakusuma (dual-use military/civil)
    # South Asia (30-min reporters)
    "Lucknow":       "VILK",
    "Karachi":       "OPKC",
    # Oceania (30-min)
    "Wellington":    "NZWN",
    # Americas (non-US)
    "Toronto":       "CYTZ",
    "Mexico City":   "MMMX",
    "São Paulo":     "SBGR",   # Guarulhos Intl — Polymarket resolves against SBGR via WU (was None → Open-Meteo)
    "Buenos Aires":  "SAEZ",
    "Panama City":   "MPTO",
    # Africa
    "Cape Town":     "FACT",
    "Lagos":         "DNMM",
}

# Cities where METAR is absent; use Open-Meteo current weather as primary.
OPENMETEO_FALLBACK_CITIES = {
    c for c, icao in CITY_ICAO.items() if icao is None
}

# ── Phase 2: local timezones ──────────────────────────────────────────────────
CITY_TIMEZONES: dict[str, str] = {
    "NYC":           "America/New_York",
    "Chicago":       "America/Chicago",
    "Miami":         "America/New_York",
    "Los Angeles":   "America/Los_Angeles",
    "Dallas":        "America/Chicago",
    "Atlanta":       "America/New_York",
    "Houston":       "America/Chicago",
    "Austin":        "America/Chicago",
    "Seattle":       "America/Los_Angeles",
    "San Francisco": "America/Los_Angeles",
    "Denver":        "America/Denver",
    "London":        "Europe/London",
    "Paris":         "Europe/Paris",
    "Madrid":        "Europe/Madrid",
    "Munich":        "Europe/Berlin",
    "Milan":         "Europe/Rome",
    "Amsterdam":     "Europe/Amsterdam",
    "Warsaw":        "Europe/Warsaw",
    "Helsinki":      "Europe/Helsinki",
    "Istanbul":      "Europe/Istanbul",
    "Ankara":        "Europe/Istanbul",
    "Moscow":        "Europe/Moscow",
    "Tel Aviv":      "Asia/Jerusalem",
    "Jeddah":        "Asia/Riyadh",
    "Hong Kong":     "Asia/Hong_Kong",
    "Seoul":         "Asia/Seoul",
    "Tokyo":         "Asia/Tokyo",
    "Busan":         "Asia/Seoul",
    "Taipei":        "Asia/Taipei",
    "Beijing":       "Asia/Shanghai",
    "Shanghai":      "Asia/Shanghai",
    "Guangzhou":     "Asia/Shanghai",
    "Shenzhen":      "Asia/Shanghai",
    "Chengdu":       "Asia/Shanghai",
    "Chongqing":     "Asia/Shanghai",
    "Wuhan":         "Asia/Shanghai",
    "Singapore":     "Asia/Singapore",
    "Kuala Lumpur":  "Asia/Kuala_Lumpur",
    "Manila":        "Asia/Manila",
    "Jakarta":       "Asia/Jakarta",
    "Lucknow":       "Asia/Kolkata",
    "Karachi":       "Asia/Karachi",
    "Wellington":    "Pacific/Auckland",
    "Toronto":       "America/Toronto",
    "Mexico City":   "America/Mexico_City",
    "São Paulo":     "America/Sao_Paulo",
    "Buenos Aires":  "America/Argentina/Buenos_Aires",
    "Panama City":   "America/Panama",
    "Cape Town":     "Africa/Johannesburg",
    "Lagos":         "Africa/Lagos",
}

# ── Phase 2: bankroll & budget defaults ──────────────────────────────────────
# Live values stored in system_config DB table; these are startup defaults only.
DEFAULT_BANKROLL_USD     = 1000.0
DAILY_BANKROLL_PCT       = 0.15   # 15% of bankroll risked per day (kept for reference)
PHASE1_BUDGET_PCT        = 0.00   # Phase 1 is now observation-only — no capital deployed
PHASE2_BUDGET_PCT        = 1.00   # Phase 2 receives the full daily budget
MIN_BANKROLL_USD         = 700.0  # pause trading below this floor

# Phase 2 fixed daily budget — decoupled from bankroll percentage.
# Set to a fixed dollar amount so budget doesn't balloon as bankroll grows.
# Stored in system_config as "phase2_fixed_daily_usd"; this is the startup default.
#
# BACKTEST (46 trades, May 2–8): calibrated cities + price<30¢ = +$698 at $30/trade.
# Scaling to $45/trade: +$1,048 over 5 days ($210/day avg).
# Budget sized for ~6 trades/day at $45 = $270.
PHASE2_FIXED_DAILY_USD   = 120.0  # $120/day dedicated to Phase 2 trades (live mode start)

# Hard cap per individual Phase 2 trade regardless of budget percentage.
# Prevents any single city from consuming a large fraction of daily budget.
PHASE2_MAX_TRADE_USD     = 15.0

# Calibrated sizing: cities with delta_samples >= PHASE2_CALIBRATION_MIN_SAMPLES
# are considered well-calibrated and receive a larger per-trade size.
# Uncalibrated cities trade in observation-only mode ($0.01) to keep delta
# calibration running without risking capital.
#
# LIVE MODE START: $15/trade for low-risk live testing. Scale up after validation.
PHASE2_CALIBRATED_TRADE_USD    = 15.0   # per-trade size for calibrated cities
# Lowered from 3 → 2 once the hierarchical Bayesian prior is in place:
# a single observation combined with a regional prior is informative enough
# to size up to $15. Cities with n=0,1 stay observation-only.
PHASE2_CALIBRATION_MIN_SAMPLES = 2

# Maximum YES price for Phase 2 trades on calibrated cities.
# BACKTEST: trades under 30¢ have positive EV (+$183 on 23 trades).
#           trades above 30¢ are net negative.
PHASE2_MAX_CALIBRATED_PRICE    = 0.30   # only buy brackets priced under 30¢

# ── Phase 2 NO sweep ─────────────────────────────────────────────────────────
# After a Phase 2 YES trade is placed, buy NO on brackets that are physically
# impossible given the confirmed running maximum temperature.
#
# Safety margin: bracket must be this far BELOW the adjusted running_max
# before we consider it physically locked.
#   8°F / 4.5°C ≈ the width of 4 brackets — well outside delta-correction error.
NO_SWEEP_SAFETY_MARGIN_F  = 8.0    # °F below adjusted running_max
NO_SWEEP_SAFETY_MARGIN_C  = 4.5    # °C equivalent
NO_SWEEP_MIN_LOCAL_HOUR   = 14     # don't sweep before 2 PM local (temp still rising)
NO_SWEEP_MAX_YES_PRICE    = 0.05   # only sweep if YES < 5¢  (NO > 95¢)
NO_SWEEP_MIN_YES_PRICE    = 0.003  # skip if YES < 0.3¢ (no meaningful liquidity)
# NO sweep is in OBSERVATION-ONLY mode as of 2026-05-13.
# Per-trade math: $6.67 staked to win $0.02 (R:R 1:333) needs 99.7%+ true
# accuracy to break even. We have no calibration data showing our locks
# are that precise at extreme brackets, and a single missed-fill taker
# retry fee (1.25%) erases ~10 trades of profit. The cap is set so the
# computed per-bracket size ($0.03 ÷ N) is below the executor's
# "num_tokens < 1" guard, which short-circuits before any CLOB call.
# Sweep signal rows continue to be written for after-the-fact evaluation.
# To re-enable real-money sweeps later: restore CAP to 20.0.
NO_SWEEP_CAP_PER_CITY_USD = 0.03   # observation-only — see comment above
NO_SWEEP_MAX_PER_BRACKET  = 8.0    # max per individual bracket (spread the risk)

# Phase 2 execution thresholds
PHASE2_MIN_CONFIDENCE    = 0.80   # minimum bracket-lock confidence to trade
                                  # raised from 0.70: 0.733-tier was 0/5 (0% win rate)
PHASE2_MAX_YES_PRICE     = 0.85   # don't buy Phase 2 above 85¢ YES price
PHASE2_MIN_LOCAL_HOUR    = 13     # don't trigger before 1 PM local city time
                                  # (was 14: max conf at 14h = 0.697, just below 0.70
                                  #  threshold, so Phase 2 could never fire before 15h
                                  #  local — too late for fast-moving EU/ZA markets)
PHASE2_STABLE_READINGS   = 24     # readings at same running_max before "stable"
                                  # Raised 2026-05-17 from 12 → 24 (60 → 120 min of plateau)
                                  # after 5 of 6 real-money losses since 2026-05-13 were
                                  # premature lock — Phase 2 fired on a 60-min plateau,
                                  # then temp resumed climbing 1.0-1.7°C and blew the
                                  # bracket:
                                  #   Chengdu 26.0→27.5  Paris 14.3→16.0  Wuhan 27.0→28.0
                                  #   Amsterdam 12.0→13.0  Munich 15.0→16.0
                                  # The 60-min plateau was too short during late-spring NH
                                  # warming. 120 min requires a much stronger "the high
                                  # is in" signal before risking real money.
                                  #
                                  # Earlier history (kept for context): raised from 6→12
                                  # after investigating 23 Phase 2 losses where 13 were
                                  # premature locks on a 30-min window — Chongqing (3×),
                                  # Warsaw (2×), Busan, Buenos Aires, Ankara, Wuhan,
                                  # Madrid, NYC, London May 3.

# Morning model probability gate for Phase 2.
# When Phase 2 fires on a locked bracket, we look up the Phase 1 morning
# forecast's model_probability for that exact bracket. If it's below this
# threshold we skip — it means our morning model didn't favor this bracket
# and the afternoon METAR reading is likely biased vs. the resolution station.
#
# Example: Chongqing 30°C at 9.4¢ market price — if Phase 1 said only 12%
# for that bracket in the morning, the market (9.4¢) and model both agree
# it's unlikely. Phase 2 should skip.
#
# Amsterdam 14°C at 7.2¢ — if Phase 1 said 65%+ in the morning, the market
# is wrong and our model is right. Phase 2 should trade.
#
# BACKTEST RESULT (33 resolved trades):
#   threshold=0.0  → $+159.77 (no filter, all trades taken)
#   threshold=0.15 → $+39.63  (skipped trades had $+120.14 P&L — hurts)
#   threshold=0.30 → $+79.17  (skipped trades had $+80.60 P&L  — hurts)
#   threshold=0.40 → $-64.66  (skipped trades had $+224.43 P&L — very bad)
#
# Conclusion: Phase 2 alpha COMES FROM overriding the morning model with real-time
# observations. Amsterdam ($+191.90) and Lagos ($+185.47) were morning model
# outliers (10–30% morning probability) that the afternoon METAR confirmed.
# A model probability gate blocks exactly these high-alpha trades.
# Leave at 0.0 (disabled). Kept as a configurable knob for future research.
PHASE2_MIN_MODEL_PROB    = 0.0   # 0.0 = disabled; >0 = minimum Phase 1 model_probability

