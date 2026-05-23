"""Claude API commentary on a profile JSON.

- Standard mode: Sonnet 4.6, ~2¢ per call
- Deep mode: Opus 4.7, ~10¢ per call
- Caches the system prompt for 5-minute reuse via Anthropic prompt caching
"""
from __future__ import annotations

import json
from typing import Any

import anthropic

from .config import ANTHROPIC_API_KEY, MODEL_DEEP, MODEL_STANDARD
from .strategy_context import compose_system_prompt


# Per-1M-token prices (May 2026). Adjust if Anthropic changes pricing.
PRICING = {
    MODEL_STANDARD: {"in": 3.0, "out": 15.0, "cache_write": 3.75, "cache_read": 0.30},
    MODEL_DEEP:     {"in": 15.0, "out": 75.0, "cache_write": 18.75, "cache_read": 1.50},
}


def _trim_profile_for_prompt(profile: dict[str, Any]) -> dict[str, Any]:
    """Drop noisy fields, keep the analytically-useful structure."""
    trimmed = {
        "identity": profile.get("identity"),
        "stats": profile.get("stats"),
        "strategy": profile.get("strategy"),
        "by_day": profile.get("by_day", [])[:60],     # last 60 days max
        "open_positions": profile.get("open_positions", [])[:20],
        "weather_dissection": profile.get("weather_dissection"),
        "precise_pnl": profile.get("precise_pnl"),
        "meta": profile.get("meta"),
    }
    return {k: v for k, v in trimmed.items() if v is not None}


def commentary(profile: dict[str, Any], *, mode: str = "standard") -> dict[str, Any]:
    if not ANTHROPIC_API_KEY:
        return {"error": "ANTHROPIC_API_KEY not set", "markdown": "", "model_used": "", "cost_usd": 0.0}

    model = MODEL_DEEP if mode == "deep" else MODEL_STANDARD
    # Pass the trader profile so the system prompt can inject pre-computed
    # comparison facts (validated zones, overlap, anti-precedents, Kelly).
    system_prompt = compose_system_prompt(trader_profile=profile)
    profile_payload = _trim_profile_for_prompt(profile)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=model,
        # 3500 leaves comfortable room for full structured output including
        # up to 15 monitor_positions, even if Claude's verbose on triggers.
        max_tokens=3500,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    "Here is the trader's profile data. Produce the analysis as instructed.\n\n"
                    "```json\n" + json.dumps(profile_payload, default=str)[:90_000] + "\n```"
                ),
            }
        ],
    )

    raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    usage = resp.usage
    cost = _estimate_cost(model, usage)

    # Try to parse as JSON; fall back to raw markdown if model deviated.
    structured, parse_error = _parse_structured(raw)

    return {
        "structured": structured,           # parsed dict, or None on failure
        "markdown": raw,                    # always the raw text, for debugging/fallback
        "parse_error": parse_error,         # None if parse OK
        "model_used": model,
        "mode": mode,
        "cost_usd": round(cost, 4),
        "tokens": {
            "input": getattr(usage, "input_tokens", 0),
            "output": getattr(usage, "output_tokens", 0),
            "cache_read": getattr(usage, "cache_read_input_tokens", 0),
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0),
        },
    }


def _parse_structured(text: str) -> tuple[dict | None, str | None]:
    """Best-effort JSON parse, stripping common LLM wrappers."""
    if not text or not text.strip():
        return None, "empty response"
    s = text.strip()
    # Strip markdown code fences if Claude wrapped despite instructions
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.strip("`")
        s = s.lstrip("json").strip()
    # Find first { and last } so leading/trailing prose can't break us
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None, "no JSON object found"
    try:
        return json.loads(s[i:j + 1]), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse failed: {e}"


def _estimate_cost(model: str, usage) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (inp * p["in"] + out * p["out"] + cw * p["cache_write"] + cr * p["cache_read"]) / 1_000_000
