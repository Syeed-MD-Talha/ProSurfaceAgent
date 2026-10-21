"""Central configuration: credentials, models, paths, and limits.

Single place for everything previously scattered across agent.py, web.py,
and the stores. Import from here instead of reading os.getenv directly.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Fireworks credentials / models -------------------------------------
FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "")
FIREWORKS_MODEL = os.getenv("FIREWORKS_MODEL", "")
FIREWORKS_VISION_MODEL = os.getenv(
    "FIREWORKS_VISION_MODEL",
    "accounts/fireworks/models/glm-5p3-flash",
)
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"

# --- Access control --------------------------------------------------------
# Web UI login: the chat page and /api/chat require a session created via
# POST /api/login. Credentials live in .env (WEB_LOGIN_USERNAME /
# WEB_LOGIN_PASSWORD). Missing/empty = login always fails (fail closed).
# GET /api/health stays open for the restart helper.
# (Legacy ADMIN_PASSWORD env is ignored if still present.)
WEB_LOGIN_USERNAME = os.getenv("WEB_LOGIN_USERNAME", "")
WEB_LOGIN_PASSWORD = os.getenv("WEB_LOGIN_PASSWORD", "")


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive integer setting, falling back safely on invalid input."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Web chat usage limits. The minute limit is per signed-in browser session;
# the daily limit is shared by the whole server and resets at midnight UTC.
WEB_CHAT_SESSION_LIMIT_PER_MINUTE = _positive_int_env(
    "WEB_CHAT_SESSION_LIMIT_PER_MINUTE", 30
)
WEB_CHAT_SERVER_DAILY_LIMIT = _positive_int_env("WEB_CHAT_SERVER_DAILY_LIMIT", 200)


def load_credentials() -> tuple[str, str]:
    """Return (api_key, model), exiting with the usual message if missing."""
    if not FIREWORKS_API_KEY or not FIREWORKS_MODEL:
        print("Error: FIREWORKS_API_KEY and FIREWORKS_MODEL must be set in .env")
        sys.exit(1)
    return FIREWORKS_API_KEY, FIREWORKS_MODEL


# --- Owner email notifications ----------------------------------------------
# The bot emails the owner when a booking is made, a customer shares a phone
# number/email in chat, or the bot needs the owner's answer (ask_owner).
# Disabled (every alert becomes a no-op) until these are set in .env.
# For Gmail: SMTP_USER = your Gmail address, SMTP_PASSWORD = an App Password
# (Google Account > Security > 2-Step Verification > App passwords).
OWNER_NOTIFY_EMAIL = os.getenv("OWNER_NOTIFY_EMAIL", "")  # your inbox
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")  # Gmail address used to send
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Gmail App Password

# Set to the final HTTPS website address when deploying (e.g. https://chat.example.com).
# Empty uses this server's local address. Never derive email links from request headers.
OWNER_REPLY_BASE_URL = os.getenv("OWNER_REPLY_BASE_URL", "").strip().rstrip("/")
OWNER_REPLY_TOKEN_TTL_HOURS = _positive_int_env("OWNER_REPLY_TOKEN_TTL_HOURS", 168)


def web_state_dir() -> Path:
    """New web conversation/owner queue state; separate from customer workspace."""
    default = Path(__file__).resolve().parents[1] / ".prosurface-agent" / "web-state"
    return Path(os.getenv("WEB_STATE_DIR", str(default)))

# --- Model behavior -------------------------------------------------------
MODEL_MAX_TOKENS = 128000  # fallback context window estimate
COMPRESSION_THRESHOLD = 0.45
CHAT_MAX_TOKENS = 1024  # per reply in the web (non-streaming) loop
TERMINAL_MAX_TOKENS = 4096  # per reply in the terminal (streaming) loop
MAX_TOOL_ROUNDS = 6  # tool iterations per web turn

FALLBACK_REPLY = "I'm little bit busy now i'll answer you later"

# --- Token hygiene ----------------------------------------------------------
# Tool results (data_search, skill_view) are large and accumulate in history.
# Before each API call, tool messages from *previous* turns are truncated to
# this many chars; the current turn's results stay intact. Full results are
# always kept in the session DB — pruning affects only what is re-sent.
TOOL_HISTORY_KEEP_CHARS = 300

# --- Images ---------------------------------------------------------------
ALLOWED_IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # raw decoded bytes
MAX_IMAGE_B64_CHARS = 11 * 1024 * 1024  # base64 payload for an 8 MiB image
# Customer photos are analysed in memory and NOT stored by default
# (privacy-friendly; PDPA-safe). Set SAVE_UPLOADS=1 to keep an audit trail.
SAVE_UPLOADS = os.getenv("SAVE_UPLOADS", "0") == "1"

# --- PDFs -----------------------------------------------------------------
ALLOWED_PDF_MIMES = ("application/pdf",)
MAX_PDF_BYTES = 10 * 1024 * 1024  # raw decoded bytes
MAX_PDF_B64_CHARS = 14 * 1024 * 1024  # base64 payload in data URLs (4/3 overhead)

# --- Other document uploads -----------------------------------------------
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_B64_CHARS = 14 * 1024 * 1024

# --- Paths ----------------------------------------------------------------
# All runtime paths derive from the process working directory (repo root).


def project_root() -> Path:
    return Path.cwd()


def kb_pages_dir() -> Path:
    candidate = project_root() / "prosurfaceworks_knowledge_base" / "pages"
    if candidate.is_dir():
        return candidate
    return (
        Path(__file__).resolve().parents[1]
        / "prosurfaceworks_knowledge_base"
        / "pages"
    )


def workspace_dir() -> Path:
    path = project_root() / "workspace"
    path.mkdir(exist_ok=True)
    return path


def uploads_dir() -> Path:
    path = workspace_dir() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path
