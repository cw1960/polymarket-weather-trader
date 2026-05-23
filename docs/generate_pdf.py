"""Generate system_overview.pdf from the markdown content."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable
)

OUTPUT = "/Users/macminim4foropenclawesbibot/Desktop/Polymarket Weather Trading System/docs/system_overview.pdf"

# ── Styles ────────────────────────────────────────────────────────────────────

styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    'DocTitle', parent=styles['Title'],
    fontSize=20, leading=26, spaceAfter=4,
    textColor=HexColor('#1a1a2e'),
))
styles.add(ParagraphStyle(
    'Subtitle', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=16,
    textColor=HexColor('#555555'),
))
styles.add(ParagraphStyle(
    'H1', parent=styles['Heading1'],
    fontSize=16, leading=20, spaceBefore=24, spaceAfter=10,
    textColor=HexColor('#1a1a2e'),
))
styles.add(ParagraphStyle(
    'H2', parent=styles['Heading2'],
    fontSize=13, leading=17, spaceBefore=18, spaceAfter=8,
    textColor=HexColor('#2d3748'),
))
styles.add(ParagraphStyle(
    'Body', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=8,
    textColor=HexColor('#2d3748'),
))
styles.add(ParagraphStyle(
    'BodyBold', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=8,
    textColor=HexColor('#2d3748'),
    fontName='Helvetica-Bold',
))
styles.add(ParagraphStyle(
    'Bullet', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=4,
    leftIndent=20, bulletIndent=10,
    textColor=HexColor('#2d3748'),
))
styles.add(ParagraphStyle(
    'NumberedItem', parent=styles['Normal'],
    fontSize=10, leading=14, spaceAfter=4,
    leftIndent=20,
    textColor=HexColor('#2d3748'),
))
styles.add(ParagraphStyle(
    'Code', parent=styles['Normal'],
    fontSize=9, leading=12, spaceAfter=8,
    fontName='Courier', leftIndent=20,
    textColor=HexColor('#4a5568'),
    backColor=HexColor('#f7fafc'),
))
styles.add(ParagraphStyle(
    'Caption', parent=styles['Normal'],
    fontSize=8, leading=10,
    textColor=HexColor('#888888'),
    alignment=TA_CENTER,
))

# ── Table helper ──────────────────────────────────────────────────────────────

HEADER_BG = HexColor('#1a1a2e')
HEADER_FG = HexColor('#ffffff')
ROW_ALT   = HexColor('#f7fafc')
GRID_CLR  = HexColor('#cbd5e0')

def make_table(headers, rows, col_widths=None):
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND',  (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR',   (0, 0), (-1, 0), HEADER_FG),
        ('FONTNAME',    (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',    (0, 0), (-1, 0), 9),
        ('FONTSIZE',    (0, 1), (-1, -1), 9),
        ('LEADING',     (0, 0), (-1, -1), 12),
        ('ALIGN',       (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN',      (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING',  (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('GRID',        (0, 0), (-1, -1), 0.5, GRID_CLR),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0, i), (-1, i), ROW_ALT))
    t.setStyle(TableStyle(style_cmds))
    return t

def hr():
    return HRFlowable(width="100%", thickness=1, color=HexColor('#e2e8f0'),
                      spaceBefore=6, spaceAfter=12)

# ── Build document ────────────────────────────────────────────────────────────

doc = SimpleDocTemplate(
    OUTPUT, pagesize=letter,
    leftMargin=0.75*inch, rightMargin=0.75*inch,
    topMargin=0.75*inch, bottomMargin=0.75*inch,
)

story = []
W = doc.width  # usable width

# Title block
story.append(Paragraph("Polymarket Weather Trading System", styles['DocTitle']))
story.append(Paragraph("Technical Overview", styles['DocTitle']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Date: May 9, 2026 &nbsp;&nbsp;|&nbsp;&nbsp; "
    "Status: Live (paper trading with real-money sizing) &nbsp;&nbsp;|&nbsp;&nbsp; "
    "Bankroll: $2,032 (started $1,000 on May 2)",
    styles['Subtitle']
))
story.append(hr())

# ── Section 1 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("1. What the System Does", styles['H1']))
story.append(Paragraph(
    "This system trades daily high-temperature markets on Polymarket. Each day, "
    "Polymarket lists ~50 cities with markets like <i>\"Will the highest temperature "
    "in Amsterdam be 14 degrees C on May 5?\"</i> Each bracket (e.g., 14 degrees C, 15 degrees C, 16 degrees C) trades as a "
    "binary YES/NO contract that resolves to $1 or $0 based on what Weather "
    "Underground reports as the official high.",
    styles['Body']
))
story.append(Paragraph(
    "The system identifies brackets where the market price is wrong -- specifically, "
    "brackets that are cheap (under 30 cents) but where we have high-confidence "
    "evidence the temperature will land there. We buy YES at 5-25 cents and collect "
    "$1 if correct, for a 4-20x payout.",
    styles['Body']
))
story.append(hr())

# ── Section 2 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("2. Two-Phase Architecture", styles['H1']))

story.append(Paragraph("Phase 1: Morning Forecast (Observation Only)", styles['H2']))
story.append(Paragraph(
    "<b>Runs:</b> 4x daily at GFS model run times (03:30, 09:30, 15:30, 21:30 UTC)<br/>"
    "<b>Data source:</b> Open-Meteo ensemble API",
    styles['Body']
))
story.append(Paragraph("- GFS ensemble: 31 members", styles['Bullet']))
story.append(Paragraph("- ECMWF IFS025 ensemble: 51 members", styles['Bullet']))
story.append(Paragraph("- 6 deterministic models: GFS, ECMWF, ICON, MeteoFrance, UKMO, GEM", styles['Bullet']))

story.append(Spacer(1, 4))
story.append(Paragraph("<b>Process:</b>", styles['Body']))
story.append(Paragraph("1. Fetch ensemble forecasts for all 50 cities", styles['NumberedItem']))
story.append(Paragraph("2. For each city, build a \"ladder\" of bracket probabilities by counting how many of the 82 ensemble members fall into each temperature bracket", styles['NumberedItem']))
story.append(Paragraph("3. Compute model probability, market price, and expected value for each bracket", styles['NumberedItem']))
story.append(Paragraph("4. Store all signals in trade_signals table with signal_phase = 'phase1'", styles['NumberedItem']))
story.append(Paragraph("5. No capital deployed -- Phase 1 is purely observational ($0.01 symbolic position)", styles['NumberedItem']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "<b>Purpose:</b> Phase 1 provides the morning probability baseline and feeds the "
    "delta calibration pipeline (see Section 4). It does NOT drive trading decisions.",
    styles['Body']
))

story.append(Paragraph("Phase 2: Afternoon Confirmation (Real Money)", styles['H2']))
story.append(Paragraph(
    "<b>Runs:</b> Every 5 minutes via temp_monitor.py cron job<br/>"
    "<b>Data source:</b> Real-time METAR aviation weather reports (ICAO stations)",
    styles['Body']
))
story.append(Paragraph("<b>Process:</b>", styles['Body']))
story.append(Paragraph("1. Every 5 minutes, poll METAR data for all 50 cities via aviationweather.gov", styles['NumberedItem']))
story.append(Paragraph("2. Track each city's running daily maximum temperature", styles['NumberedItem']))
story.append(Paragraph("3. When a city's running max has been stable for 12 consecutive readings (60 minutes) AND it is past 1 PM local time, consider the bracket \"locked\"", styles['NumberedItem']))
story.append(Paragraph("4. Compute lock confidence based on time of day, stability duration, and number of readings", styles['NumberedItem']))
story.append(Paragraph("5. Look up the corresponding Polymarket bracket and current YES price", styles['NumberedItem']))
story.append(Paragraph("6. Apply trading filters (see Section 3)", styles['NumberedItem']))
story.append(Paragraph("7. If all filters pass, place a $45 YES trade on the locked bracket", styles['NumberedItem']))

story.append(Spacer(1, 4))
story.append(Paragraph(
    "<b>The edge:</b> By the time Phase 2 fires, we have 60+ minutes of stable real-time "
    "temperature data showing the daily high has likely peaked. The market is still "
    "pricing brackets based on morning forecasts and general uncertainty. We are buying "
    "at morning odds with afternoon information.",
    styles['Body']
))
story.append(hr())

# ── Section 3 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("3. Trading Filters (Current Configuration)", styles['H1']))
story.append(Paragraph("All three filters must pass for a real-money trade:", styles['Body']))

story.append(make_table(
    ['Filter', 'Threshold', 'Rationale'],
    [
        ['Calibration', 'delta_samples >= 3',
         'Only trade cities with enough historical data to know the station bias. '
         'Backtest: calibrated = +$272 (36% WR, 69.5% ROI); uncalibrated = -$202 (14% WR).'],
        ['Price cap', 'YES price < 30 cents',
         'Only buy cheap brackets where payout asymmetry works in our favor. '
         'Backtest: under 30 cents = +$183 (positive EV); above 30 cents = net negative.'],
        ['Confidence', 'lock confidence >= 0.81',
         'Temperature must be stable for 60+ minutes past 1 PM local time.'],
    ],
    col_widths=[1.1*inch, 1.4*inch, W - 2.5*inch],
))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "<b>Position sizing:</b> $45 flat per qualifying trade. $350/day budget cap.",
    styles['Body']
))
story.append(Paragraph(
    "<b>Observation mode:</b> Cities that fail the calibration or price filter still get a "
    "$0.01 symbolic trade. This keeps the delta calibration pipeline running (see Section 4) "
    "without risking capital.",
    styles['Body']
))
story.append(hr())

# ── Section 4 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("4. Delta Calibration System", styles['H1']))

story.append(Paragraph("The Problem", styles['H2']))
story.append(Paragraph(
    "Polymarket resolves temperature markets using Weather Underground, which sources from "
    "specific weather stations. Our real-time data comes from METAR (aviation weather reports) "
    "at nearby airports. These two sources often disagree by 0.5-2.0 degrees C due to:",
    styles['Body']
))
story.append(Paragraph("- Different physical station locations (airport vs. city center)", styles['Bullet']))
story.append(Paragraph("- Different sensor equipment and reporting standards", styles['Bullet']))
story.append(Paragraph("- Weather Underground's proprietary data processing", styles['Bullet']))
story.append(Paragraph("- Microclimate differences between station sites", styles['Bullet']))
story.append(Paragraph(
    "Since Polymarket brackets are 1 degree C wide, even a 1 degree C systematic bias means we "
    "consistently pick the wrong bracket.",
    styles['Body']
))

story.append(Paragraph("The Solution: Adaptive Delta Correction", styles['H2']))
story.append(Paragraph(
    "Each city has a learned <font name='Courier'>delta_c</font> value stored in the "
    "<font name='Courier'>resolution_stations</font> table. This represents the systematic "
    "bias: <font name='Courier'>resolution_temp = METAR_temp + delta_c</font>.",
    styles['Body']
))
story.append(Paragraph("<b>How it's computed:</b>", styles['Body']))
story.append(Paragraph("1. When a Phase 2 trade resolves, we know the METAR temperature at lock time and the actual resolution temperature (inferred from the winning bracket)", styles['NumberedItem']))
story.append(Paragraph("2. observed_delta = resolution_temp - METAR_lock_temp", styles['NumberedItem']))
story.append(Paragraph("3. Update via exponential smoothing: new_delta = old_delta * (1 - alpha) + observed_delta * alpha", styles['NumberedItem']))
story.append(Paragraph("4. alpha = max(0.20, 1 / (1 + samples)) -- high weight early, stabilizes after 5+ observations", styles['NumberedItem']))

story.append(Spacer(1, 4))
story.append(Paragraph("<b>How it's applied:</b> When Phase 2 locks a bracket, it adds delta_c to the running max before looking up which bracket to bet on:", styles['Body']))
story.append(Paragraph("adjusted_temp = running_max_metar + delta_c", styles['Code']))
story.append(Paragraph("bracket = find_bracket(adjusted_temp)", styles['Code']))

story.append(Paragraph("Current Calibration State (14 cities qualified)", styles['H2']))
story.append(make_table(
    ['City', 'Delta', 'Samples', 'Interpretation'],
    [
        ['Amsterdam',  '+0.80 C', '5', 'WU reads ~0.8 C warmer than METAR'],
        ['Ankara',     '+1.00 C', '4', 'WU reads ~1.0 C warmer'],
        ['Cape Town',  '+0.00 C', '3', 'METAR and WU agree'],
        ['Chengdu',    '+1.00 C', '5', 'WU reads ~1.0 C warmer'],
        ['Chongqing',  '+1.00 C', '4', 'WU reads ~1.0 C warmer'],
        ['Helsinki',   '-1.13 C', '3', 'WU reads ~1.1 C cooler than METAR'],
        ['Hong Kong',  '-0.67 C', '3', 'WU reads ~0.7 C cooler'],
        ['Lagos',      '-0.10 C', '4', 'Nearly identical'],
        ['Madrid',     '-0.10 C', '3', 'Nearly identical'],
        ['Miami',      '+0.12 C', '3', 'Nearly identical'],
        ['NYC',        '+0.71 C', '3', 'WU reads ~0.7 C warmer'],
        ['Seoul',      '+0.33 C', '3', 'WU reads ~0.3 C warmer'],
        ['Tel Aviv',   '+0.00 C', '5', 'METAR and WU agree'],
        ['Wuhan',      '+1.00 C', '5', 'WU reads ~1.0 C warmer'],
    ],
    col_widths=[1.1*inch, 0.8*inch, 0.7*inch, W - 2.6*inch],
))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "<b>Uncalibrated cities (36)</b> continue running in observation mode. Each resolved "
    "observation trade adds a calibration sample. Cities automatically graduate to real "
    "trading when they accumulate 3+ samples.",
    styles['Body']
))

story.append(Paragraph("Default Delta for Uncalibrated Cities", styles['H2']))
story.append(Paragraph(
    "Cities with fewer than 3 calibration samples use a default delta of +1.0 C. This reflects "
    "the general pattern that Weather Underground tends to read warmer than METAR for most "
    "cities. However, some cities (Helsinki, Hong Kong) have negative deltas, which is why "
    "the default is only a rough approximation and real calibration data is required before "
    "deploying capital.",
    styles['Body']
))
story.append(hr())

# ── Section 5 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("5. Bracket Locking Logic", styles['H1']))
story.append(Paragraph(
    "The temp_monitor.py script runs every 5 minutes and tracks each city's temperature "
    "throughout the day. The locking decision uses:",
    styles['Body']
))
story.append(Paragraph(
    "<b>Stability requirement:</b> The running daily maximum must remain unchanged for 12 "
    "consecutive 5-minute readings (60 minutes of stability). This was increased from 6 readings "
    "(30 minutes) after analysis showed 13 out of 23 early Phase 2 losses were \"premature locks\" "
    "-- the temperature continued rising after the system bet.",
    styles['Body']
))
story.append(Paragraph(
    "<b>Time gate:</b> Must be past 1 PM local city time. This prevents betting during the "
    "morning warm-up when temperatures are still rising rapidly.",
    styles['Body']
))
story.append(Paragraph(
    "<b>Confidence formula:</b> Combines time-of-day (later = higher confidence) and stability "
    "duration into a 0-1 score. Must reach 0.81 to trigger Phase 2.",
    styles['Body']
))
story.append(Paragraph(
    "<b>METAR sources:</b> Primary source is aviationweather.gov METAR reports via ICAO station "
    "codes (e.g., EHAM for Amsterdam Schiphol, KLGA for NYC LaGuardia). Fallback to Open-Meteo "
    "current weather API for cities without reliable METAR coverage.",
    styles['Body']
))
story.append(hr())

# ── Section 6 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("6. Performance Data", styles['H1']))

story.append(Paragraph("Backtest Results (May 2-8, 2026 -- 46 trades, adjusted to current strategy)", styles['H2']))
story.append(make_table(
    ['Metric', 'Value'],
    [
        ['Real trades (calibrated + price < 30 cents)', '13'],
        ['Win rate', '30.8% (4W / 9L)'],
        ['Total P&L', '+$1,047.70'],
        ['ROI on deployed capital', '179.1%'],
        ['Average win', '+$363.17'],
        ['Average loss', '-$45.00 (always the full stake)'],
        ['Profitable days', '4 out of 5 (80%)'],
        ['Best single day', '+$575.69'],
        ['Worst single day', '-$135.00'],
    ],
    col_widths=[3.0*inch, W - 3.0*inch],
))

story.append(Paragraph("Why 30% Win Rate is Profitable", styles['H2']))
story.append(Paragraph(
    "The system buys YES at 5-25 cents. A loss costs exactly the stake ($45). A win pays "
    "$45 / price -- at 7 cents, that's $45 / 0.07 = $643 payout minus $45 cost = $598 profit. "
    "The breakeven win rate at 7 cents is ~7%. At 25 cents it's ~25%. Our 30.8% win rate "
    "exceeds breakeven at every price point in our range.",
    styles['Body']
))

story.append(Paragraph("Loss Pattern Analysis", styles['H2']))
story.append(Paragraph("Of 34 total losses across all Phase 2 trades (before filtering):", styles['Body']))
story.append(Paragraph("- <b>97% were off-by-one bracket misses</b> -- the system picked an adjacent bracket", styles['Bullet']))
story.append(Paragraph("- <b>22 losses (65%) were undershoots</b> -- premature lock; temperature continued rising after we bet", styles['Bullet']))
story.append(Paragraph("- <b>12 losses (35%) were overshoots</b> -- delta correction was too aggressive for that city", styles['Bullet']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "This confirms the system is identifying the correct <i>region</i> of temperature nearly every "
    "time. The remaining error is split between timing (premature lock) and station bias (delta "
    "accuracy), both of which improve as calibration data accumulates.",
    styles['Body']
))
story.append(hr())

# ── Section 7 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("7. Infrastructure", styles['H1']))
story.append(make_table(
    ['Component', 'Details'],
    [
        ['VPS', 'Vultr Ubuntu 22.04'],
        ['Database', 'Supabase (PostgreSQL) -- tables: trade_signals, ladders, temp_readings, ensemble_forecasts, resolution_stations, bankroll_snapshots, system_config'],
        ['Frontend', 'React + Vite + Tailwind, deployed on Netlify'],
        ['Cron schedule', 'signal_engine.py 4x/day (GFS windows), temp_monitor.py every 5 min, bankroll reconciliation daily at 02:30 UTC'],
        ['API dependencies', 'Open-Meteo (forecasts), aviationweather.gov (METAR), Gamma API (Polymarket prices)'],
        ['Execution', 'Paper mode -- signals written to DB with correct sizing; executor module exists for live trading'],
    ],
    col_widths=[1.3*inch, W - 1.3*inch],
))
story.append(hr())

# ── Section 8 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("8. Key Questions for Review", styles['H1']))

questions = [
    ("<b>Is the delta calibration approach sound?</b> Exponential smoothing with "
     "alpha = max(0.20, 1/(1+n)) on the METAR-to-WU bias. Is there a better estimator "
     "given small sample sizes (3-5 observations)?"),
    ("<b>Is the 30-cent price cap justified?</b> The backtest shows positive EV only below "
     "30 cents. Is this a real structural edge (payout asymmetry) or an artifact of small "
     "sample size and a few large wins?"),
    ("<b>Premature lock mitigation:</b> 65% of losses are premature locks (temp still rising). "
     "Current mitigation is 60 minutes of stability. Are there better signals for \"temperature "
     "has peaked\" -- e.g., comparing to forecast peak hour, or using rate-of-change?"),
    ("<b>Sample size concern:</b> 13 real trades is a small sample. The two largest wins "
     "(Lagos +$642, Amsterdam +$576) represent ~116% of total profit. How much confidence "
     "should we place in a 179% ROI from 13 trades?"),
    ("<b>Compounding plan:</b> Currently flat $45/trade. Would a Kelly criterion or fractional "
     "Kelly approach to position sizing be more appropriate as the sample grows?"),
]
for i, q in enumerate(questions, 1):
    story.append(Paragraph(f"{i}. {q}", styles['NumberedItem']))
    story.append(Spacer(1, 4))

story.append(hr())

# ── Section 9 ─────────────────────────────────────────────────────────────────
story.append(Paragraph("9. Risk Factors", styles['H1']))

risks = [
    "<b>Concentration risk:</b> Two cities (Lagos, Amsterdam) generated most of the profit. "
    "If those cities' delta calibrations are wrong, forward performance will differ significantly.",
    "<b>Small sample:</b> 13 trades over 5 days. Edge may not persist.",
    "<b>Resolution source risk:</b> If Weather Underground changes their data source or processing "
    "for any city, our delta calibrations become invalid instantly.",
    "<b>Liquidity:</b> Polymarket temperature markets are thin. At $45/trade we're fine; at "
    "$200+/trade we may move the market.",
    "<b>Regulatory:</b> Polymarket's legal status varies by jurisdiction.",
]
for r in risks:
    story.append(Paragraph(f"- {r}", styles['Bullet']))

# ── Build ─────────────────────────────────────────────────────────────────────
doc.build(story)
print(f"PDF generated: {OUTPUT}")
