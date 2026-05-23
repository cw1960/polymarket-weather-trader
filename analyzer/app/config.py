"""Runtime config — env vars, paths, model IDs."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

AUTH_TOKEN = os.environ.get("ANALYZER_AUTH_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

STRATEGY_CONTEXT_FILE = Path(
    os.environ.get("STRATEGY_CONTEXT_FILE", ROOT / "strategy_context.md")
)

HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8001"))
CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "3600"))

MODEL_STANDARD = "claude-sonnet-4-6"
MODEL_DEEP = "claude-opus-4-7"

# Vendored toolkit path
VENDOR_PNL = ROOT / "vendored" / "polymarket-toolkit" / "polymarket-pnl"
