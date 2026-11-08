"""Google Gemini integration (official ``google-genai`` SDK).

Contract:
* API key read from ``GEMINI_API_KEY`` via :mod:`column_mapping.config`
  (repo-root ``.env`` or process env). The key is passed ONLY to the SDK
  client constructor — never into prompts, logs, JSON output or messages,
  and every surfaced error string passes :func:`column_mapping.config.scrub`.
* Model configurable via ``GEMINI_MODEL`` (default ``gemini-3.8-flash``).
* Structured JSON output via the SDK's ``response_schema`` (a pydantic model),
  with a ``json.loads`` fallback if the SDK returns raw text.
* The prompt carries column names + safe local metadata ONLY (dtype, null
  share, distinct count, value-shape tags, likely-PII flag) — no cell values,
  no customer records.
* The canonical target list embedded in the prompt is the parsed, validated
  mappable set from :mod:`column_mapping.schema_catalog`; the mapper
  re-validates every returned suggestion against the same gate afterwards,
  so even a hallucinated target cannot survive.

Error taxonomy (caught by the mapper, which degrades to ``needs_review``):
    LLMUnavailable  missing/invalid key, SDK not installed, HTTP 401/403
    LLMRateLimit    HTTP 429 after bounded retries
    LLMError        other API/server errors (message scrubbed + truncated)
    LLMBadResponse  response was not valid JSON / violated the schema
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from .config import MAX_COLUMNS_PER_PROMPT, model_name, scrub
from .detect import KINDS, KindDetection
from .schema_catalog import SchemaCatalog

MAX_ATTEMPTS = 3  # 1 try + 2 retries for transient errors
_RETRY_BACKOFF_SECONDS = (1.5, 3.0)


class LLMError(Exception):
    """Base class for Gemini failures (message already scrubbed)."""


class LLMUnavailable(LLMError):
    """No key / bad key / SDK missing — the LLM stage cannot run at all."""


class LLMRateLimit(LLMError):
    """HTTP 429 persisted after retries."""


class LLMBadResponse(LLMError):
    """Model replied with JSON we cannot validate against the schema."""


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------
_ALLOWED_KINDS = tuple(k for k in KINDS)


class _SuggestionSchema(BaseModel):
    source_column: str
    target_table: str | None = None
    target_column: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = ""


class _MappingResponseSchema(BaseModel):
    dataset_kind: str | None = None
    suggestions: list[_SuggestionSchema] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Types exchanged with the mapper
# ---------------------------------------------------------------------------
@dataclass
class PendingColumn:
    """One unresolved source column as handed to the LLM."""

    source_column: str
    metadata: dict[str, Any]  # safe profile metadata, no values
    candidates: list[dict[str, str]] = field(default_factory=list)  # pre-validated


@dataclass
class LLMSuggestion:
    source_column: str
    target_table: str | None
    target_column: str | None
    confidence: float
    explanation: str


@dataclass
class LLMOutcome:
    suggestions: list[LLMSuggestion] = field(default_factory=list)
    dataset_kind: str | None = None  # model's own kind opinion (reviewable)
    error: str | None = None


class LLMClient(Protocol):
    """What the mapper depends on — swap in fakes for tests."""

    model: str

    def suggest(
        self, pending: list[PendingColumn], catalog: SchemaCatalog, detection: KindDetection
    ) -> LLMOutcome: ...


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
def build_prompt(
    pending: list[PendingColumn], catalog: SchemaCatalog, detection: KindDetection
) -> str:
    kind_candidates = ", ".join(
        f"{k} ({detection.scores[k]:.2f})" for k, _ in sorted(
            detection.scores.items(), key=lambda kv: kv[1], reverse=True
        )
    ) or "unknown"
    targets = "\n".join(f"- {line}" for line in catalog.prompt_targets())
    payload = [
        {
            "source_column": p.source_column,
            **p.metadata,
            "validated_candidates": p.candidates,
        }
        for p in pending
    ]
    all_columns = list(detection.column_kinds) or [p.source_column for p in pending]
    resolved = detection.resolved_context
    resolved_lines = "\n".join(
        f"- {src} -> {target}" for src, target in resolved.items()
    ) or "- (none)"
    return f"""You map CSV column names of an e-commerce store onto the canonical schema \
of a Customer Data Platform. You are given column NAMES and safe metadata only — no cell \
values, no customer data. Do not ask for values; reason from names, dtypes and shapes.

Detected file kind candidates (may be wrong; a file can mix entities): {kind_candidates}

FILE CONTEXT — every column header in this file, in order (names only):
{json.dumps(all_columns, ensure_ascii=False)}

Already mapped by documented rules (do not reuse these targets for other columns):
{resolved_lines}

RULES
1. Choose a target ONLY from this exact list of canonical targets (table.column). \
Never invent table names, column names, ids, foreign keys, or ML/derived features. \
Every possible target is listed — if nothing fits, return null for both target fields.
2. `validated_candidates` (when present) are pre-validated options for that column; \
prefer one of them if a candidate fits.
3. confidence is YOUR certainty in the mapping (0.0-1.0): >=0.9 documented synonym, \
0.7-0.9 strong inference from name+dtype, <0.6 guess. Never exceed 0.95.
4. explanation: one short sentence citing the name/dtype evidence.
5. dataset_kind: your overall opinion of this file's entity, one of: \
{", ".join(_ALLOWED_KINDS)} — or null if unsure.
6. Use the FILE CONTEXT. Each target receives at most one source column. A bare \
row counter such as `id` is NOT the business identifier when a more specific \
identifier column exists in the same file (e.g. `message_id`, `order_id`) — return null \
for it. A bare `id` in a file with no other identifier for its entity IS that entity's \
source id.
7. Record-keeping audit columns (`created_at`, `updated_at`, `inserted_at`, ...) and \
per-event detail timestamps (`opened_first_time_at`, `clicked_last_time_at`, ...) are \
not business event times: return null unless a target describes exactly that moment.
8. Pick the table that matches the file's entity: campaign files with plain \
`start_date`/`end_date` dates describe coupon campaigns; files with \
`channel`/`topic`/`started_at` describe marketing (messaging) campaigns. A \
`category` column in a messaging file categorises messages, not products.

CANONICAL TARGETS (upload-mappable fields only)
{targets}

UNRESOLVED SOURCE COLUMNS (JSON, safe metadata)
{json.dumps(payload, ensure_ascii=False)}
"""


# ---------------------------------------------------------------------------
# Real client
# ---------------------------------------------------------------------------
class GeminiColumnClient:
    """Production client backed by the official google-genai SDK."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        # Import late so the rest of the package (and tests) work without the SDK.
        try:
            from google import genai  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise LLMUnavailable(
                "google-genai SDK is not installed "
                "(pip install -r column_mapping/requirements.txt)"
            ) from exc
        from .config import api_key as env_api_key

        key = api_key if api_key is not None else env_api_key()
        if not key:
            raise LLMUnavailable(
                "GEMINI_API_KEY is not set — copy .env.example to .env and fill it, "
                "or export GEMINI_API_KEY, or re-run with --no-llm"
            )
        self._key = key  # never leaves this object except via the SDK constructor
        self.model = model_name(model)
        self._genai = genai
        self._client: Any = None

    @property
    def client(self) -> Any:
        # Lazy: constructing genai.Client can hit credential discovery.
        if self._client is None:
            self._client = self._genai.Client(api_key=self._key)
        return self._client

    def suggest(
        self, pending: list[PendingColumn], catalog: SchemaCatalog, detection: KindDetection
    ) -> LLMOutcome:
        from google.genai import errors, types  # type: ignore

        prompt = build_prompt(pending, catalog, detection)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_MappingResponseSchema,
        )

        last_failure: LLMError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=prompt, config=config
                )
                parsed = self._parse(response)
                return self._to_outcome(parsed, pending)
            except errors.APIError as exc:
                code = getattr(exc, "code", None)
                message = scrub(str(exc))[:300]
                if code in (401, 403):
                    raise LLMUnavailable(
                        f"Gemini rejected the API key (HTTP {code}) — check GEMINI_API_KEY"
                    ) from None
                if code == 429:
                    last_failure = LLMRateLimit(
                        "Gemini rate limit (HTTP 429) — retried and still throttled"
                    )
                else:
                    last_failure = LLMError(f"Gemini API error (HTTP {code}): {message}")
                if code in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS - 1:
                    time.sleep(_RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_failure from None
            except LLMError:
                raise  # LLMBadResponse from _parse passes through untouched
            except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
                raise LLMBadResponse(
                    f"Gemini returned a response that violates the JSON schema: "
                    f"{scrub(str(exc))[:300]}"
                ) from None
            except Exception as exc:  # httpx/network failures, SDK internals
                last_failure = LLMError(
                    f"network/SDK failure calling Gemini: {scrub(str(exc))[:300]}"
                )
                if attempt < MAX_ATTEMPTS - 1:
                    time.sleep(_RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)])
                    continue
                raise last_failure from None
        raise last_failure or LLMError("Gemini call failed without a recorded error")

    # -- helpers ------------------------------------------------------------
    def _parse(self, response: Any) -> _MappingResponseSchema:
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, _MappingResponseSchema):
            return parsed
        text = getattr(response, "text", None)
        if not text:
            raise LLMBadResponse("Gemini response had neither schema-parsed content nor text")
        try:
            return _MappingResponseSchema.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise LLMBadResponse(
                f"Gemini returned invalid JSON: {scrub(str(exc))[:300]}"
            ) from None

    def _to_outcome(
        self, parsed: _MappingResponseSchema, pending: list[PendingColumn]
    ) -> LLMOutcome:
        wanted = {p.source_column for p in pending}
        suggestions = [
            LLMSuggestion(
                source_column=item.source_column,
                target_table=item.target_table,
                target_column=item.target_column,
                confidence=float(item.confidence),
                explanation=item.explanation.strip(),
            )
            # only accept answers for columns we actually asked about
            for item in parsed.suggestions
            if item.source_column in wanted
        ]
        dataset_kind = parsed.dataset_kind if parsed.dataset_kind in _ALLOWED_KINDS else None
        return LLMOutcome(suggestions=suggestions, dataset_kind=dataset_kind)


def make_client(model: str | None = None) -> GeminiColumnClient:
    """Factory used by the CLI (raises LLMUnavailable when key is missing)."""
    return GeminiColumnClient(model=model)
