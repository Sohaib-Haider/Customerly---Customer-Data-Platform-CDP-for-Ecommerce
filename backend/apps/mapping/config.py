"""Environment loading for the mapping app (column mapping).

Reads the repo-root ``.env`` (gitignored) and process environment. The API key
is handled as an opaque secret: it is never logged, never printed, never
embedded in source, and every error string passing through this module is
scrubbed with :func:`scrub` before it can reach stdout/stderr/JSON output.

Nothing in this module writes to the environment except :func:`load_env`,
which only *sets missing* variables (an explicitly exported shell variable
always wins over the file).
"""

from __future__ import annotations

import os
from pathlib import Path

# This package lives at backend/apps/mapping/ inside the Django project.
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
# backend/.env is the project's documented secrets file; a repo-root .env is
# still honoured as a fallback. Both are gitignored.
ENV_FILE = BACKEND_DIR / ".env"
FALLBACK_ENV_FILE = REPO_ROOT / ".env"

# Default documented in .env.example. The Gemini API reports model
# availability per account; if a run returns HTTP 404 for the model, set
# GEMINI_MODEL to a model your key can use (the API error names one).
DEFAULT_MODEL = "gemini-3.8-flash"

# Mapping behaviour thresholds (kept here so tests and CLI share one source).
LLM_CONFIDENCE_HIGH = 0.85  # >= this => status "suggested"; below => "needs_review"
LLM_CONFIDENCE_MIN = 0.50  # below this a suggestion is discarded as "unmapped"
MAX_COLUMNS_PER_PROMPT = 40  # LLM call chunk size
PROFILE_SAMPLE_ROWS = 2000  # rows scanned locally for safe metadata
ROW_SCAN_CAP = 50_000  # row-count scan cap for very large files


def load_env(path: Path | None = None) -> Path | None:
    """Parse a .env file into os.environ for variables not already set.

    Deliberately tiny: KEY=VALUE lines, optional ``export`` prefix, optional
    surrounding quotes, ``#`` comments. Returns the path read, or None.
    """
    if path is not None:
        env_path = Path(path)
    else:
        env_path = ENV_FILE if ENV_FILE.is_file() else FALLBACK_ENV_FILE
    if not env_path.is_file():
        return None
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
    return env_path


def api_key() -> str | None:
    """Return the Gemini API key from the environment, or None.

    Loads .env first so a local run needs no shell export. The value is
    returned as-is and must only be handed to the SDK client constructor.
    """
    load_env()
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key or None


def model_name(override: str | None = None) -> str:
    """Gemini model to use: explicit override > GEMINI_MODEL > default."""
    if override:
        return override
    load_env()
    return os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_MODEL


def scrub(text: str) -> str:
    """Replace the API key anywhere it accidentally appears in text.

    Defence in depth: nothing in this package interpolates the key into
    messages, but SDK exception text is third-party, so anything surfaced to
    the user passes through here first.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key and key in text:
        return text.replace(key, "[REDACTED]")
    return text
