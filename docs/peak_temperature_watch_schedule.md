# Daily Peak-Temperature Watch Schedule

**Purpose:** Follow each city (or group of cities) as they approach and arrive at their typical daily high temperature. Use this to manually identify high-probability bracket trades on the Polymarket UI.

**Reference assumption:** Daily peak temperature occurs at approximately **15:00 local time** in each city. Real peaks vary ±1 hour by season and latitude — tropical cities trend earlier, high-latitude summer trends later.

**Time zone math:** All conversions use IANA timezone database, so DST is handled automatically. The Monterrey column shows the time in your local clock (`America/Monterrey` — UTC−6 year-round; Mexico abolished DST nationally in 2022 except for border municipalities, and Nuevo León is not a border state).

**Calibration column:**
- `✓ n=X δ=±Y°C` — our hierarchical Bayesian station-delta is calibrated (≥2 samples). Trade-worthy when our Phase 2 fires.
- `partial n=X` — fewer than 2 calibration samples. Bot won't trade real money here yet; we're still gathering data.
- `—` — no calibration data; not tracked by our station model.

---

## Watch schedule (Monterrey time)

Read this list top-to-bottom through the day. Each block is a window where a group of cities is approaching their daily high. Set phone alarms or just check Polymarket at the start of each window.

### 🌙 Overnight (00:00 – 04:00 Monterrey) — East / Southeast / South Asia

| Monterrey | UTC | City | Region | Local TZ | Unit | Calibration |
|---|---|---|---|---|---|---|
| **00:00** | 06:00 | **Tokyo** | East Asia | Asia/Tokyo | °C | ✓ n=4 δ=+0.00°C |
| **00:00** | 06:00 | **Seoul** | East Asia | Asia/Seoul | °C | ✓ n=3 δ=+0.33°C |
| **00:00** | 06:00 | Busan | East Asia | Asia/Seoul | °C | partial n=1 |
| **01:00** | 07:00 | **Hong Kong** | East Asia | Asia/Hong_Kong | °C | ✓ n=4 δ=−0.23°C |
| **01:00** | 07:00 | **Beijing** | East Asia | Asia/Shanghai | °C | ✓ n=3 δ=+0.33°C |
| **01:00** | 07:00 | **Guangzhou** | East Asia | Asia/Shanghai | °C | ✓ n=4 δ=+0.30°C |
| **01:00** | 07:00 | **Chengdu** | East Asia | Asia/Shanghai | °C | ✓ n=8 δ=+0.78°C |
| **01:00** | 07:00 | **Chongqing** | East Asia | Asia/Shanghai | °C | ✓ n=5 δ=+1.20°C |
| **01:00** | 07:00 | **Wuhan** | East Asia | Asia/Shanghai | °C | ✓ n=10 δ=+0.57°C |
| **01:00** | 07:00 | **Singapore** | SE Asia | Asia/Singapore | °C | ✓ n=2 δ=+0.00°C |
| **01:00** | 07:00 | Taipei | East Asia | Asia/Taipei | °C | partial n=1 |
| **01:00** | 07:00 | Shanghai | East Asia | Asia/Shanghai | °C | partial n=0 |
| **01:00** | 07:00 | Shenzhen | East Asia | Asia/Shanghai | °C | partial n=1 |
| **01:00** | 07:00 | Kuala Lumpur | SE Asia | Asia/Kuala_Lumpur | °C | partial n=1 |
| **01:00** | 07:00 | Manila | SE Asia | Asia/Manila | °C | partial n=0 |
| **02:00** | 08:00 | Jakarta | SE Asia | Asia/Jakarta | °C | partial n=1 |
| **03:30** | 09:30 | Lucknow | South Asia | Asia/Kolkata | °C | partial n=0 |
| **04:00** | 10:00 | Karachi | South Asia | Asia/Karachi | °C | partial n=0 |

**Notes:** This is the dead-of-night window for you. The bot covers calibrated cities autonomously; you'd only check this for **Hong Kong, Beijing, Guangzhou, Chengdu, Chongqing, Wuhan, Singapore, Tokyo, Seoul** if you happen to be up. Skip Taipei/Shanghai/Shenzhen/Manila — uncalibrated.

---

### ☕ Early morning (06:00 – 08:00 Monterrey) — Middle East / East Europe / Europe / Africa

| Monterrey | UTC | City | Region | Local TZ | Unit | Calibration |
|---|---|---|---|---|---|---|
| **06:00** | 12:00 | **Helsinki** | Europe | Europe/Helsinki | °C | ✓ n=4 δ=−0.80°C |
| **06:00** | 12:00 | **Istanbul** | E. Europe | Europe/Istanbul | °C | ✓ n=2 δ=−0.10°C |
| **06:00** | 12:00 | **Ankara** | E. Europe | Europe/Istanbul | °C | ✓ n=7 δ=+0.84°C |
| **06:00** | 12:00 | **Moscow** | E. Europe | Europe/Moscow | °C | ✓ n=2 δ=−0.45°C |
| **06:00** | 12:00 | **Tel Aviv** | Mideast | Asia/Jerusalem | °C | ✓ n=6 δ=+0.00°C |
| **06:00** | 12:00 | Jeddah | Mideast | Asia/Riyadh | °C | partial n=0 |
| **07:00** | 13:00 | **Paris** | Europe | Europe/Paris | °C | ✓ n=9 δ=+0.47°C |
| **07:00** | 13:00 | **Madrid** | Europe | Europe/Madrid | °C | ✓ n=7 δ=+0.11°C |
| **07:00** | 13:00 | **Munich** | Europe | Europe/Berlin | °C | ✓ n=4 δ=+0.60°C |
| **07:00** | 13:00 | **Milan** | Europe | Europe/Rome | °C | ✓ n=7 δ=+0.57°C |
| **07:00** | 13:00 | **Amsterdam** | Europe | Europe/Amsterdam | °C | ✓ n=12 δ=+0.69°C |
| **07:00** | 13:00 | **Warsaw** | Europe | Europe/Warsaw | °C | ✓ n=6 δ=+0.74°C |
| **07:00** | 13:00 | **Cape Town** | Africa | Africa/Johannesburg | °C | ✓ n=7 δ=+0.19°C |
| **08:00** | 14:00 | **London** | Europe | Europe/London | °C | ✓ n=2 δ=+1.10°C |
| **08:00** | 14:00 | **Lagos** | Africa | Africa/Lagos | °C | ✓ n=8 δ=−0.04°C |

**Notes:** **This is your prime daily window.** Coffee + dashboard. 13 calibrated cities (10 of our most consistent earners) peak between 06:00 and 08:00 Monterrey time. Open positions are most actionable here — by 09:00 Monterrey, you know how Europe + Africa + Middle East resolved or are about to.

**Cape Town note:** During Southern Hemisphere DST it would be 06:00; right now it's winter (no DST in South Africa anyway) so 07:00 is consistent year-round.

---

### 🥪 Lunch (12:00 – 13:00 Monterrey) — South America + East Coast US

| Monterrey | UTC | City | Region | Local TZ | Unit | Calibration |
|---|---|---|---|---|---|---|
| **12:00** | 18:00 | **São Paulo** | S. America | America/Sao_Paulo | °C | ✓ n=2 δ=+7.00°C ⚠ |
| **12:00** | 18:00 | **Buenos Aires** | S. America | America/Argentina/Buenos_Aires | °C | ✓ n=4 δ=+0.00°C |
| **13:00** | 19:00 | **NYC** | N. America | America/New_York | °F | ✓ n=4 δ=+1.30°C |
| **13:00** | 19:00 | **Miami** | N. America | America/New_York | °F | ✓ n=4 δ=+0.28°C |
| **13:00** | 19:00 | **Atlanta** | N. America | America/New_York | °F | ✓ n=3 δ=−0.03°C |
| **13:00** | 19:00 | **Toronto** | N. America | America/Toronto | °C | ✓ n=3 δ=−0.67°C |

**Notes:** Six calibrated cities, all in your work-day window. Easy to check. **São Paulo's δ=+7.00°C looks anomalous** — likely just a tiny sample with a single hot outlier; treat with skepticism until n grows.

---

### 🌆 Afternoon (14:00 – 16:00 Monterrey) — Central US / Mountain US / West Coast

| Monterrey | UTC | City | Region | Local TZ | Unit | Calibration |
|---|---|---|---|---|---|---|
| **14:00** | 20:00 | Chicago | N. America | America/Chicago | °F | partial n=1 |
| **14:00** | 20:00 | Dallas | N. America | America/Chicago | °F | partial n=1 |
| **14:00** | 20:00 | **Houston** | N. America | America/Chicago | °F | ✓ n=2 δ=−0.86°C |
| **14:00** | 20:00 | **Austin** | N. America | America/Chicago | °F | ✓ n=2 δ=−3.66°C ⚠ |
| **14:00** | 20:00 | Panama City | S. America | America/Panama | °C | partial n=0 |
| **15:00** | 21:00 | Denver | N. America | America/Denver | °F | partial n=0 |
| **15:00** | 21:00 | Mexico City | N. America | America/Mexico_City | °C | partial n=1 |
| **16:00** | 22:00 | **Los Angeles** | N. America | America/Los_Angeles | °F | ✓ n=2 δ=−0.23°C |
| **16:00** | 22:00 | **Seattle** | N. America | America/Los_Angeles | °F | ✓ n=6 δ=+1.65°C |
| **16:00** | 22:00 | San Francisco | N. America | America/Los_Angeles | °F | partial n=0 |

**Notes:** Central US (Houston, Austin) peaks at **your lunch time**. Then West Coast hits as your afternoon wraps. **Austin's δ=−3.66°C is suspicious** — could be a station/elevation issue, treat with skepticism. Chicago, Dallas, Denver, SF, Mexico City are uncalibrated — system observes but doesn't trade them yet.

---

### 🌃 Late night (21:00 Monterrey) — Oceania

| Monterrey | UTC | City | Region | Local TZ | Unit | Calibration |
|---|---|---|---|---|---|---|
| **21:00** | 03:00 | **Wellington** | Oceania | Pacific/Auckland | °C | ✓ n=2 δ=+0.00°C |

**Notes:** Wellington's daily peak is in the late Monterrey evening. **In their current winter (May = NZ autumn/winter)** the peak is modest. Most of the year this is the LAST city you'd check before sleeping.

---

## Quick-reference daily routine

| Monterrey time | Action |
|---|---|
| **Morning coffee (06:00–08:00)** | Check Polymarket UI for Europe + Mideast + Africa. 13 calibrated cities resolving. Highest-value window. |
| **Lunch break (12:00–13:00)** | South America + US East Coast. 6 calibrated cities. |
| **Afternoon (14:00–16:00)** | Central US + West Coast US. 4 calibrated cities. |
| **Before bed (21:00)** | Wellington only. Low priority. |
| **Overnight** | Asia. Bot handles automatically — you don't need to be up. |

## How to use this for manual trades

A bracket becomes **high-probability** when:
1. The day is already mostly done (city's local time is past 15:00) — the high is essentially in.
2. The current temperature has been **stable for at least 60 minutes** within a single bracket.
3. The Polymarket market price for YES on that bracket is below what your gut tells you the true probability is (e.g., market = 60¢, your gut says 85%).

**Best manual-trade window:** the bracket is **30-90 minutes after the city's local peak time**. The high is recently set, the market hasn't fully priced it in yet, and temperatures are starting to drop.

## Calibration legend

| Symbol | Meaning |
|---|---|
| **City in bold** | Calibrated (n ≥ 2 samples). Bot trades real money here when Phase 2 fires. |
| City in plain text | Uncalibrated. Bot still observes but won't deploy real money. Could still be manually traded by you. |
| ⚠ | Anomalous delta suggesting calibration may be off; treat with extra skepticism. |

## Regeneration

This document was generated from the system's actual config and live calibration data on **2026-05-16**. The DST-aware time conversions remain accurate year-round.

To regenerate after calibration changes or DST transitions, re-run the generator embedded at the top of this file (see project's `scripts/sync_bracket_blacklist.py` for similar zoneinfo patterns).
