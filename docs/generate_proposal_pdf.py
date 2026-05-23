"""Generate overshoot_proposal.pdf for senior dev review."""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)

OUTPUT = "/Users/macminim4foropenclawesbibot/Desktop/Polymarket Weather Trading System/docs/overshoot_proposal.pdf"

styles = getSampleStyleSheet()
styles.add(ParagraphStyle('DocTitle', parent=styles['Title'], fontSize=20, leading=26,
                          spaceAfter=4, textColor=HexColor('#8B0000')))
styles.add(ParagraphStyle('Subtitle', parent=styles['Normal'], fontSize=10, leading=14,
                          spaceAfter=16, textColor=HexColor('#555555')))
styles.add(ParagraphStyle('H1', parent=styles['Heading1'], fontSize=16, leading=20,
                          spaceBefore=22, spaceAfter=10, textColor=HexColor('#1a1a2e')))
styles.add(ParagraphStyle('H2', parent=styles['Heading2'], fontSize=13, leading=17,
                          spaceBefore=16, spaceAfter=7, textColor=HexColor('#2d3748')))
styles.add(ParagraphStyle('H3', parent=styles['Heading3'], fontSize=11, leading=15,
                          spaceBefore=10, spaceAfter=5, textColor=HexColor('#4a5568'),
                          fontName='Helvetica-Bold'))
styles.add(ParagraphStyle('Body', parent=styles['Normal'], fontSize=10, leading=14,
                          spaceAfter=8, textColor=HexColor('#2d3748')))
styles.add(ParagraphStyle('MyBullet', parent=styles['Normal'], fontSize=10, leading=14,
                          spaceAfter=4, leftIndent=20, bulletIndent=10,
                          textColor=HexColor('#2d3748')))
styles.add(ParagraphStyle('NumberedItem', parent=styles['Normal'], fontSize=10, leading=14,
                          spaceAfter=4, leftIndent=20, textColor=HexColor('#2d3748')))
styles.add(ParagraphStyle('Quote', parent=styles['Normal'], fontSize=10, leading=14,
                          leftIndent=20, rightIndent=20, spaceAfter=8,
                          textColor=HexColor('#5a6478'), fontName='Helvetica-Oblique'))

HEADER_BG = HexColor('#1a1a2e')
HEADER_FG = HexColor('#ffffff')
ROW_ALT = HexColor('#f7fafc')
GRID = HexColor('#cbd5e0')
ALERT_BG = HexColor('#fff5f5')

def make_table(headers, rows, col_widths=None, alert_col=None):
    data = [headers] + rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    cmds = [
        ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
        ('TEXTCOLOR', (0,0), (-1,0), HEADER_FG),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('LEADING', (0,0), (-1,-1), 12),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
        ('RIGHTPADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, GRID),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            cmds.append(('BACKGROUND', (0,i), (-1,i), ROW_ALT))
    t.setStyle(TableStyle(cmds))
    return t

def hr():
    return HRFlowable(width="100%", thickness=1, color=HexColor('#e2e8f0'),
                      spaceBefore=4, spaceAfter=10)

doc = SimpleDocTemplate(OUTPUT, pagesize=letter,
                        leftMargin=0.75*inch, rightMargin=0.75*inch,
                        topMargin=0.75*inch, bottomMargin=0.75*inch)
story = []
W = doc.width

# ── Header ──
story.append(Paragraph("Overshoot Loss Proposal", styles['DocTitle']))
story.append(Paragraph("Request for Senior Dev Review", styles['DocTitle']))
story.append(Spacer(1, 4))
story.append(Paragraph(
    "Date: May 10, 2026 &nbsp;&nbsp;|&nbsp;&nbsp; "
    "Status: Discussion document, no changes implemented yet",
    styles['Subtitle']
))
story.append(hr())

# ── Summary ──
story.append(Paragraph("Summary", styles['H1']))
story.append(Paragraph(
    "After implementing the calibration filter and price cap (May 8), our loss pattern shifted. "
    "Overshoots now dominate. We need a strategy to address them without breaking what's working.",
    styles['Body']
))
story.append(Paragraph(
    "This document presents the data, four proposed solutions, and asks for the senior dev's "
    "input on which to pursue.",
    styles['Body']
))

# ── The Loss Pattern Has Shifted ──
story.append(Paragraph("The Loss Pattern Has Shifted", styles['H1']))
story.append(Paragraph("<b>Original system (pre-filter, all trades):</b>", styles['Body']))
story.append(Paragraph("- 22 undershoots (65% of losses) — temperature kept rising after lock", styles['MyBullet']))
story.append(Paragraph("- 12 overshoots (35%) — delta correction landed us one bracket too high", styles['MyBullet']))
story.append(Spacer(1, 4))
story.append(Paragraph("<b>New system (calibrated cities, price &lt; 30 cents, real-money trades only):</b>", styles['Body']))
story.append(Paragraph("- 2 undershoots — $90 in losses", styles['MyBullet']))
story.append(Paragraph("- <b>10 overshoots — $450 in losses</b>", styles['MyBullet']))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "The recently deployed fixes (rate-of-change check, cloud cover boost) target undershoots. "
    "They will help with $90 of losses but do nothing for the dominant $450 problem.",
    styles['Body']
))

# ── What An Overshoot Looks Like ──
story.append(Paragraph("What An Overshoot Looks Like", styles['H1']))
story.append(Paragraph(
    "For each real-money loss, here is the lock-time data. Note: some delta values shown are "
    "current; historical deltas may have differed at trade time.",
    styles['Body']
))

story.append(make_table(
    ['Date', 'City', 'Lock max', 'Delta', 'Adjusted', 'Bet', 'Actual', 'Result'],
    [
        ['May 5',  'Hong Kong',  '23.2 C', '-0.23', '23.0 C', '24 C', '23 C', 'Over +1'],
        ['May 5',  'Helsinki',   '13.2 C', '-1.13', '12.1 C', '13 C', '12 C', 'Over +1'],
        ['May 6',  'Hong Kong',  '25.4 C', '-0.23', '25.2 C', '25 C', '24 C', 'Over +1'],
        ['May 7',  'Madrid',     '18.1 C', '-0.10', '18.0 C', '19 C', '17 C', 'Over +2'],
        ['May 8',  'Seoul',      '19.0 C', '+0.33', '19.3 C', '21 C', '20 C', 'Over +1'],
        ['May 8',  'Tel Aviv',   '25.0 C', ' 0.00', '25.0 C', '26 C', '25 C', 'Over +1'],
        ['May 8',  'Amsterdam',  '17.0 C', '+0.30', '17.3 C', '19 C', '17 C', 'Over +2'],
        ['May 9',  'Amsterdam',  '18.6 C', '+0.30', '18.9 C', '19 C', '18 C', 'Over +1'],
        ['May 10', 'Wuhan',      '28.0 C', '+0.80', '28.8 C', '29 C', '28 C', 'Over +1'],
        ['May 10', 'Chengdu',    '31.4 C', '+0.72', '32.1 C', '32 C', '31 C', 'Over +1'],
    ],
    col_widths=[0.6*inch, 0.95*inch, 0.75*inch, 0.6*inch, 0.75*inch, 0.5*inch, 0.6*inch, 0.7*inch],
))
story.append(Spacer(1, 10))

story.append(Paragraph("Two patterns visible", styles['H3']))
story.append(Paragraph(
    "<b>Pattern A — Boundary proximity (5 of 10):</b> The adjusted temperature was within 0.3 C "
    "of a bracket boundary. Examples: Wuhan adj 28.8 (boundary at 28.5), Chengdu adj 32.1 "
    "(boundary at 31.5), Amsterdam adj 18.9 (boundary at 18.5).",
    styles['Body']
))
story.append(Paragraph(
    "<b>Pattern B — Persistent positive bias (5 of 10):</b> Cities with delta near zero or "
    "negative still overshoot, suggesting the METAR-to-WU relationship has structural noise we "
    "are not capturing. Tel Aviv (delta=0) overshot. Hong Kong (delta=-0.23) overshot twice.",
    styles['Body']
))

# ── Why Did The Previous Buffer Fail ──
story.append(Paragraph("Why Did the Previous Boundary Buffer Attempt Fail?", styles['H1']))
story.append(Paragraph(
    "We previously implemented an upward boundary buffer that pushed our bracket selection to "
    "the next-higher bracket when within 0.5 C of the boundary. This caused immediate losses "
    "(Seoul, Tel Aviv on May 8) and was reverted.",
    styles['Body']
))
story.append(Paragraph(
    "That failure happened because the buffer pushed us <i>toward</i> the higher bracket. The "
    "current data suggests the opposite is needed: we should be more cautious near the "
    "<i>upper</i> boundary, not aggressive.",
    styles['Body']
))

# ── Four Proposed Solutions ──
story.append(Paragraph("Four Proposed Solutions", styles['H1']))

# Option 1
story.append(Paragraph("Option 1 — Boundary Buffer (Skip Near Edge)", styles['H2']))
story.append(Paragraph(
    "Skip trades when the adjusted temperature is within 0.3 C of a bracket boundary. Example: "
    "if adjusted = 28.8 C and the upper boundary is 28.5 C, we would skip rather than bet on "
    "bracket 29.",
    styles['Body']
))
story.append(Paragraph("<b>Pros:</b>", styles['Body']))
story.append(Paragraph("- Directly addresses Pattern A (5 of 10 overshoots)", styles['MyBullet']))
story.append(Paragraph("- Simple to implement", styles['MyBullet']))
story.append(Paragraph("- No model retraining needed", styles['MyBullet']))
story.append(Paragraph("<b>Cons:</b>", styles['Body']))
story.append(Paragraph("- Reduces trade count (estimated 30-40% of qualifying trades filtered)", styles['MyBullet']))
story.append(Paragraph("- Doesn't help with Pattern B (intrinsic noise overshoots)", styles['MyBullet']))
story.append(Paragraph("- Doesn't address overshoots that are clearly NOT near a boundary", styles['MyBullet']))

# Option 2
story.append(Paragraph("Option 2 — Bayesian Shrinkage of Delta", styles['H2']))
story.append(Paragraph(
    "For cities with small calibration samples (n &lt; 10), shrink the estimated delta toward "
    "zero using a Bayesian prior. As n grows, trust the local delta more.",
    styles['Body']
))
story.append(Paragraph(
    "Formula: <font name='Courier'>effective_delta = (n / (n + K)) * raw_delta</font>, "
    "where K is a shrinkage parameter (suggested K=5).",
    styles['Body']
))
story.append(Paragraph("Example with K=5:", styles['Body']))
story.append(Paragraph("- n=3 samples: use 37.5% of raw delta", styles['MyBullet']))
story.append(Paragraph("- n=5 samples: use 50%", styles['MyBullet']))
story.append(Paragraph("- n=10 samples: use 67%", styles['MyBullet']))
story.append(Paragraph("- n=50 samples: use 91%", styles['MyBullet']))

story.append(Paragraph("<b>Pros:</b>", styles['Body']))
story.append(Paragraph("- Statistically principled — addresses small-sample variance directly", styles['MyBullet']))
story.append(Paragraph("- Self-correcting: cities prove themselves before getting full delta credit", styles['MyBullet']))
story.append(Paragraph("- Recommended by senior dev in earlier review", styles['MyBullet']))
story.append(Paragraph("<b>Cons:</b>", styles['Body']))
story.append(Paragraph("- Will increase undershoot rate for cities with strong positive deltas (Wuhan +1.0, Chongqing +1.0)", styles['MyBullet']))
story.append(Paragraph("- Counterfactual analysis is mixed: only 4 of 10 overshoots prevented at 50% shrinkage", styles['MyBullet']))

# Option 3
story.append(Paragraph("Option 3 — Delta Variance Tracking", styles['H2']))
story.append(Paragraph(
    "Track the standard deviation of delta observations alongside the mean. Use "
    "<font name='Courier'>mean - K*sigma</font> for prediction. Cities with high variance get "
    "smaller effective delta.",
    styles['Body']
))
story.append(Paragraph("<b>Implementation requires:</b>", styles['Body']))
story.append(Paragraph("- Schema change to add delta_variance column", styles['MyBullet']))
story.append(Paragraph("- Update resolver to maintain running variance", styles['MyBullet']))
story.append(Paragraph("- Update prediction logic", styles['MyBullet']))

story.append(Paragraph("<b>Pros:</b>", styles['Body']))
story.append(Paragraph("- Addresses Pattern B (cities with noisy deltas)", styles['MyBullet']))
story.append(Paragraph("- More information-rich than shrinkage alone", styles['MyBullet']))
story.append(Paragraph("- Aligns with senior dev's \"delta-by-condition\" idea (precursor)", styles['MyBullet']))
story.append(Paragraph("<b>Cons:</b>", styles['Body']))
story.append(Paragraph("- More complex implementation (running variance)", styles['MyBullet']))
story.append(Paragraph("- Needs minimum n before variance is meaningful (probably n >= 5)", styles['MyBullet']))
story.append(Paragraph("- Conservative bias may reduce overall trade count significantly", styles['MyBullet']))

# Option 4
story.append(Paragraph("Option 4 — Higher Confidence Threshold", styles['H2']))
story.append(Paragraph(
    "Raise PHASE2_MIN_CONFIDENCE from 0.81 to 0.90. Only trade when extremely confident "
    "(later in day, longer stability, or with cloud cover boost).",
    styles['Body']
))

story.append(Paragraph("<b>Pros:</b>", styles['Body']))
story.append(Paragraph("- Trivial to implement (one config value)", styles['MyBullet']))
story.append(Paragraph("- Reduces trade count and variance", styles['MyBullet']))
story.append(Paragraph("- Naturally combines with cloud cover fix", styles['MyBullet']))
story.append(Paragraph("<b>Cons:</b>", styles['Body']))
story.append(Paragraph("- Doesn't directly address overshoots — they happen at high confidence too", styles['MyBullet']))
story.append(Paragraph("- May filter out high-EV cheap-bracket opportunities", styles['MyBullet']))
story.append(Paragraph("- Crude tool", styles['MyBullet']))

# ── Combined Approach ──
story.append(Paragraph("Combined Approach (My Recommendation)", styles['H1']))
story.append(Paragraph(
    "The cleanest path forward might be combining Options 1 and 2:",
    styles['Body']
))
story.append(Paragraph(
    "1. <b>Boundary buffer with asymmetry:</b> Skip trades when adjusted temp is within 0.3 C "
    "of the <i>upper</i> bracket boundary (overshoot risk side). Don't apply on the lower "
    "boundary side.",
    styles['NumberedItem']
))
story.append(Paragraph(
    "2. <b>Bayesian shrinkage</b> with K=5 to address small-sample delta variance.",
    styles['NumberedItem']
))
story.append(Spacer(1, 6))
story.append(Paragraph("Reasoning:", styles['Body']))
story.append(Paragraph("- Boundary buffer attacks the most identifiable failure mode (Pattern A)", styles['MyBullet']))
story.append(Paragraph("- Shrinkage addresses Pattern B more gradually", styles['MyBullet']))
story.append(Paragraph("- Combined effect: skip ~30% of trades, but those skipped have the highest overshoot risk", styles['MyBullet']))

# ── Counterfactual Limits ──
story.append(Paragraph("Counterfactual Backtest Limitations", styles['H1']))
story.append(Paragraph(
    "I want to be honest about what I can and cannot backtest:",
    styles['Body']
))
story.append(Paragraph(
    "- <b>What I have:</b> Lock-time temperature, current delta values, actual resolution outcomes, bet bracket",
    styles['MyBullet']
))
story.append(Paragraph(
    "- <b>What I do not have:</b> Historical delta values at the time of trade (deltas have evolved), "
    "raw temperature reading history, day-by-day market price snapshots",
    styles['MyBullet']
))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "This means counterfactual estimates are directionally useful but not precise. A proposed "
    "change that \"saves 4 of 10 overshoots\" in backtest might save 2 or 6 in production.",
    styles['Body']
))
story.append(Paragraph(
    "The most reliable test is forward results. We should pick one approach, run it for 14 days, "
    "and compare to the current 35.7% / +$1,476 baseline.",
    styles['Body']
))

# ── Specific Questions ──
story.append(Paragraph("Specific Questions for Senior Dev", styles['H1']))

questions = [
    "<b>Is asymmetric boundary buffer (Option 1) defensible?</b> It treats overshoots as worse "
    "than undershoots, which is true under our payout structure (cheap brackets) but feels ad-hoc.",

    "<b>Bayesian shrinkage (Option 2) — what K value?</b> I suggested K=5. Too aggressive? Too "
    "conservative? What's the typical convention for small-sample bias correction in similar "
    "trading systems?",

    "<b>Should we track delta variance (Option 3)?</b> It's the most informative approach but "
    "requires schema changes and minimum samples. Worth the complexity?",

    "<b>Are these fixes mutually exclusive or stackable?</b> I assume stackable but want validation.",

    "<b>What's the right success metric?</b> I'm proposing 14 days of forward data vs. May 2-10 "
    "baseline. Is that enough? Would you suggest a different evaluation method?",

    "<b>Any hidden risks I'm missing?</b> Each fix reduces trade count, which means longer time "
    "to statistical significance. Is there a way to address overshoots that doesn't reduce sample size?",
]
for i, q in enumerate(questions, 1):
    story.append(Paragraph(f"{i}. {q}", styles['NumberedItem']))
    story.append(Spacer(1, 4))

# ── Already Live ──
story.append(Paragraph("Additional Context: What's Already Live", styles['H1']))
story.append(Paragraph("Three fixes were deployed today (May 10):", styles['Body']))
story.append(Paragraph(
    "1. <b>Bracket matching fallback</b> — Jakarta/KL/Shanghai now find nearest available bracket "
    "within 1 C / 2 F when exact match fails (purely additive, no risk)",
    styles['NumberedItem']
))
story.append(Paragraph(
    "2. <b>Rate-of-change check</b> — Lock requires flat or declining trend over last 6+ readings "
    "(targets undershoots only)",
    styles['NumberedItem']
))
story.append(Paragraph(
    "3. <b>Cloud cover boost</b> — BKN/OVC sky conditions add +0.05/+0.08 to lock confidence "
    "(targets undershoots, marginal)",
    styles['NumberedItem']
))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "None of these address the overshoot problem this document is about.",
    styles['Body']
))

# ── Bankroll status ──
story.append(Paragraph("Bankroll Status", styles['H1']))
story.append(make_table(
    ['Metric', 'Value'],
    [
        ['Starting bankroll (May 2)', '$1,000'],
        ['Current bankroll (May 10)', '$2,415'],
        ['Real-money trades placed',  '17'],
        ['Win rate',                  '29.4%'],
        ['Total P&L',                 '+$1,341'],
        ['ROI on deployed',           '175.3%'],
    ],
    col_widths=[3.0*inch, W - 3.0*inch],
))
story.append(Spacer(1, 8))
story.append(Paragraph(
    "The system is profitable. We are seeking incremental improvement, not rescue.",
    styles['Body']
))

doc.build(story)
print(f"PDF generated: {OUTPUT}")
