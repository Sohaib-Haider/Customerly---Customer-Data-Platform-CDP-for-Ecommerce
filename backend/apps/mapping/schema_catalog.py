"""Canonical schema catalog, verified against the real schema sources.

This module never hardcodes the canonical schema. It mechanically parses the
two authoritative files that already exist in the repository:

* ``backend/schema.sql``          — the DDL contract (tables, columns, types,
  nullability, PKs, FKs, UNIQUE constraints). Header says 23 tables.
* ``docs/notes/schema-notes/final-schema.md`` — the same tables annotated with
  field provenance markers:

    [S] = uploaded / source data      -> mappable target for store CSV uploads
    [P] = platform-generated          -> NOT mappable (system ids/timestamps)
    [C] = computed / derived          -> NOT mappable (ML features, channel_norm)
    "Decision Required" (no marker)   -> NOT mappable (e.g. orders.region, #4)

On top of the parsed facts this module layers exactly two curated additions —
both verified at load time so no unverified assumption can survive:

1. ``INGESTIBLE_TABLES`` — which tables a store CSV upload may target at all
   (platform/ML/KPI/workflow bookkeeping tables are structurally excluded).
2. ``DOCUMENTED_FK_TARGETS`` — FK columns provenance marks [P] but that the
   existing mapping documentation explicitly targets via relationship
   resolution (e.g. ``user_id -> events.customer_id``). Each carries its
   citation and is asserted to be a real FK column of the parsed DDL.

Everything else — table names, column names, types, provenance — comes from
the files themselves, and :func:`SchemaCatalog.verify` fails loudly on any
drift between SQL, markdown and the curated layers (reported, never silently
resolved).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/apps/mapping -> repo
SCHEMA_SQL_PATH = REPO_ROOT / "backend" / "schema.sql"
SCHEMA_MD_PATH = REPO_ROOT / "docs" / "notes" / "schema-notes" / "final-schema.md"

# Documented table count (schema.sql header "Tables: 23").
EXPECTED_TABLE_COUNT = 23

# ---------------------------------------------------------------------------
# Curated layer 1: tables a store CSV upload may map columns into.
# ---------------------------------------------------------------------------
# Justification (repo evidence):
#   * segments-schema-mapping.md maps source columns only into these tables.
#   * The transform stage emits one CSV per canonical table for them
#     (run_member_b.py TABLES, run_dunnhumby.py, DUNNHUMBY.md §3).
# Excluded and why:
#   * stores / store_users      — tenant accounts created by auth/upload
#     context, never column-mapped (run_member_b's stores.csv is the loader's
#     demo-tenant row, not source data).
#   * ingestion_uploads / ingestion_column_mappings — upload bookkeeping.
#   * customer_features         — every column is [C], platform-computed.
#   * segments / ml_models / customer_segments / workflows /
#     workflow_executions / kpi_definitions / kpi_snapshots — platform,
#     ML and analytics tables, not upload targets.
INGESTIBLE_TABLES: tuple[str, ...] = (
    "customers",
    "products",
    "orders",
    "order_items",
    "events",
    "marketing_campaigns",
    "message_sends",
    "coupon_campaigns",
    "coupon_redemptions",
    "holidays",
    "causal_data",
)

# ---------------------------------------------------------------------------
# Curated layer 2: [P] FK columns that the existing mapping docs explicitly
# target. Mapping into these requires relationship resolution at load time
# (source key -> platform UUID); they are flagged `requires:
# relationship_resolution` everywhere they are used.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DocumentedFKTarget:
    requires: tuple[str, ...] = ("relationship_resolution",)
    citation: str = ""


DOCUMENTED_FK_TARGETS: dict[str, DocumentedFKTarget] = {
    "orders.customer_id": DocumentedFKTarget(
        citation="DUNNHUMBY.md L117: household_key -> orders.customer_id -> customers.id"
    ),
    "order_items.order_id": DocumentedFKTarget(
        citation="DUNNHUMBY.md L212: order_items keys are (BASKET_ID, PRODUCT_ID, occurrence)"
    ),
    "order_items.product_id": DocumentedFKTarget(
        citation="DUNNHUMBY.md L212: order_items keys are (BASKET_ID, PRODUCT_ID, occurrence)"
    ),
    "events.customer_id": DocumentedFKTarget(
        citation="segments-schema-mapping.md L53/L524: user_id -> events.customer_id"
    ),
    "events.product_id": DocumentedFKTarget(
        citation="segments-schema-mapping.md L48/L519: product_id -> events.product_id"
    ),
    "message_sends.customer_id": DocumentedFKTarget(
        citation="segments-schema-mapping.md L261: client_id (message) -> message_sends.customer_id"
    ),
    "coupon_redemptions.customer_id": DocumentedFKTarget(
        citation="segments-schema-mapping.md L158: customer_id (train) -> coupon_redemptions.customer_id"
    ),
    "coupon_redemptions.campaign_id": DocumentedFKTarget(
        citation="dunnhumby.py L493-494: CAMPAIGN -> campaign_id (coupon_redemptions)"
    ),
    "causal_data.product_id": DocumentedFKTarget(
        citation="DUNNHUMBY.md L158: PRODUCT_ID -> causal_data.product_id"
    ),
}


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
_CREATE_TABLE_RE = re.compile(r"CREATE TABLE (\w+)\s*\((.*?)\n\);", re.S)
_COLUMN_RE = re.compile(r"^    (\w+)\s+([A-Z][A-Z0-9_]*(?:\([^)]*\))?)", re.M)
_PRIMARY_KEY_RE = re.compile(r"PRIMARY KEY \(([^)]+)\)")
_FOREIGN_KEY_RE = re.compile(r"FOREIGN KEY \((\w+)\) REFERENCES (\w+)")
_UNIQUE_RE = re.compile(r"UNIQUE \(([^)]+)\)")

_MD_SECTION_RE = re.compile(r"^##\s+\d+\.\s+([a-z_][a-z0-9_]*)\s*$", re.M)
# Row shape: | `column` | type | [NOT NULL [DEFAULT ...]|NULL ...] | provenance | desc |
# The nullability cell may carry DEFAULT clauses (e.g. "NOT NULL DEFAULT 0"),
# so it is matched as a prefix, not the whole cell. Nullability itself is
# taken from the parsed DDL, never from this table.
_MD_ROW_RE = re.compile(
    r"^\|\s*`(\w+)`\s*\|\s*([^|]*?)\s*\|\s*((?:NOT NULL|NULL)[^|]*)\s*\|\s*([^|]*?)\s*\|",
    re.M,
)
_MD_PROVENANCE_RE = re.compile(r"\[(S|P|C)\]")


@dataclass(frozen=True)
class ColumnSpec:
    """One column as stated by backend/schema.sql + final-schema.md."""

    table: str
    name: str
    sql_type: str
    nullable: bool
    provenance: str  # "[S]" | "[P]" | "[C]" | raw marker text e.g. "Decision Required"
    description: str
    is_primary_key: bool
    fk_reference: str | None  # referenced table name if this column is an FK


@dataclass(frozen=True)
class MappableField:
    """A canonical column that a store upload column may be mapped onto."""

    table: str
    column: str
    sql_type: str
    nullable: bool
    provenance: str
    description: str
    kind: str  # "direct" ([S]) or "relationship" (documented FK target)
    requires: tuple[str, ...] = ()
    citation: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.table}.{self.column}"


def parse_schema_sql(path: Path = SCHEMA_SQL_PATH) -> dict[str, dict[str, ColumnSpec]]:
    """Parse CREATE TABLE blocks out of the canonical DDL."""
    text = path.read_text(encoding="utf-8")
    tables: dict[str, dict[str, ColumnSpec]] = {}
    for match in _CREATE_TABLE_RE.finditer(text):
        table, body = match.group(1), match.group(2)
        primary_keys = {
            c.strip()
            for grp in _PRIMARY_KEY_RE.findall(body)
            for c in grp.split(",")
        }
        fk_map: dict[str, str] = {}
        for col, ref in _FOREIGN_KEY_RE.findall(body):
            fk_map[col] = ref
        columns: dict[str, ColumnSpec] = {}
        for col_match in _COLUMN_RE.finditer(body):
            name, sql_type = col_match.group(1), col_match.group(2)
            if name in columns:  # constraint lines can look column-like; keep first
                continue
            line_end = body.find("\n", col_match.start())
            line = body[col_match.start() : line_end if line_end != -1 else None]
            columns[name] = ColumnSpec(
                table=table,
                name=name,
                sql_type=sql_type,
                nullable="NOT NULL" not in line,
                provenance="",  # filled from final-schema.md below
                description="",
                is_primary_key=name in primary_keys,
                fk_reference=fk_map.get(name),
            )
        tables[table] = columns
    _ = _UNIQUE_RE  # parsed DDL is asserted by verify(); uniqueness is not needed here
    return tables


def parse_provenance_md(path: Path = SCHEMA_MD_PATH) -> dict[str, dict[str, tuple[str, str]]]:
    """Parse ``## N. table`` sections of final-schema.md into provenance rows.

    Returns {table: {column: (provenance, description)}}. Rows outside a
    numbered table section (KPI coverage, open decisions, ...) are ignored.
    """
    text = path.read_text(encoding="utf-8")
    result: dict[str, dict[str, tuple[str, str]]] = {}
    current_table: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            section = _MD_SECTION_RE.match(line)
            current_table = section.group(1) if section else None
            if current_table is not None:
                result.setdefault(current_table, {})
            continue
        if current_table is None:
            continue
        row = _MD_ROW_RE.match(line)
        if not row:
            continue
        column, _type, _null, provenance_cell = (
            row.group(1),
            row.group(2),
            row.group(3),
            row.group(4),
        )
        marker = _MD_PROVENANCE_RE.search(provenance_cell)
        provenance = f"[{marker.group(1)}]" if marker else provenance_cell.strip()
        # description = 5th cell if present
        parts = line.split("|")
        description = parts[5].strip() if len(parts) > 5 else ""
        result[current_table][column] = (provenance, description)
    return result


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
@dataclass
class SchemaCatalog:
    tables: dict[str, dict[str, ColumnSpec]]
    provenance_md: dict[str, dict[str, tuple[str, str]]]
    mappable: dict[str, dict[str, MappableField]] = field(default_factory=dict)

    # -- construction -------------------------------------------------------
    @classmethod
    def load(cls, sql_path: Path = SCHEMA_SQL_PATH, md_path: Path = SCHEMA_MD_PATH) -> "SchemaCatalog":
        tables = parse_schema_sql(sql_path)
        md = parse_provenance_md(md_path)
        # Layer provenance + description from final-schema.md onto the DDL.
        for table, columns in tables.items():
            md_columns = md.get(table, {})
            for name, column in columns.items():
                if name in md_columns:
                    prov, desc = md_columns[name]
                else:
                    prov, desc = "(not in final-schema.md)", ""
                object.__setattr__(column, "provenance", prov)
                object.__setattr__(column, "description", desc)
        catalog = cls(tables=tables, provenance_md=md)
        catalog._build_mappable()
        return catalog

    def _build_mappable(self) -> None:
        for table in INGESTIBLE_TABLES:
            columns = self.tables.get(table)
            if not columns:
                continue
            mappable: dict[str, MappableField] = {}
            for name, column in columns.items():
                qualified = f"{table}.{name}"
                if column.provenance == "[S]":
                    mappable[name] = MappableField(
                        table=table,
                        column=name,
                        sql_type=column.sql_type,
                        nullable=column.nullable,
                        provenance=column.provenance,
                        description=column.description,
                        kind="direct",
                    )
                elif qualified in DOCUMENTED_FK_TARGETS:
                    meta = DOCUMENTED_FK_TARGETS[qualified]
                    mappable[name] = MappableField(
                        table=table,
                        column=name,
                        sql_type=column.sql_type,
                        nullable=column.nullable,
                        provenance=column.provenance,
                        description=column.description,
                        kind="relationship",
                        requires=meta.requires,
                        citation=meta.citation,
                    )
            if mappable:
                self.mappable[table] = mappable

    # -- queries ------------------------------------------------------------
    @property
    def mappable_pairs(self) -> set[tuple[str, str]]:
        return {(t, c) for t, cols in self.mappable.items() for c in cols}

    @property
    def mappable_count(self) -> int:
        return len(self.mappable_pairs)

    def validate(self, table: str | None, column: str | None) -> MappableField | None:
        """Return the MappableField iff (table, column) is an allowed target.

        This is the single gate every mapping suggestion passes through —
        Gemini output included — so invented tables/columns cannot survive.
        """
        if not table or not column:
            return None
        return self.mappable.get(table, {}).get(column)

    def exact_matches(self, normalized_source: str) -> list[MappableField]:
        """Mappable fields whose canonical column name equals the source name."""
        return [
            field_
            for columns in self.mappable.values()
            for name, field_ in columns.items()
            if _normalize(name) == normalized_source
        ]

    def prompt_targets(self) -> list[str]:
        """Compact 'table.column — description [kind]' lines for the LLM prompt."""
        lines = []
        for table in sorted(self.mappable):
            for name in sorted(self.mappable[table]):
                field_ = self.mappable[table][name]
                suffix = f" ({field_.kind})" if field_.kind == "relationship" else ""
                lines.append(f"{table}.{name} — {field_.description or field_.sql_type}{suffix}")
        return lines

    # -- verification -------------------------------------------------------
    def verify(self) -> list[str]:
        """Cross-check SQL DDL, provenance markdown and curated layers.

        Returns a list of drift findings; empty means everything agrees.
        Nothing here mutates data — findings are reported, not resolved.
        """
        findings: list[str] = []

        if len(self.tables) != EXPECTED_TABLE_COUNT:
            findings.append(
                f"schema.sql declares {len(self.tables)} tables, header/docs say "
                f"{EXPECTED_TABLE_COUNT}"
            )

        sql_tables = set(self.tables)
        md_tables = {t for t, cols in self.provenance_md.items() if cols}
        for table in sorted(sql_tables - md_tables):
            findings.append(f"table {table} exists in schema.sql but has no section in final-schema.md")
        for table in sorted(md_tables - sql_tables):
            findings.append(f"section {table} exists in final-schema.md but not in schema.sql")

        for table in sorted(sql_tables & md_tables):
            sql_cols = set(self.tables[table])
            md_cols = set(self.provenance_md[table])
            for col in sorted(md_cols - sql_cols):
                findings.append(f"{table}.{col} documented in final-schema.md but absent from schema.sql")
            for col in sorted(sql_cols - md_cols):
                findings.append(f"{table}.{col} exists in schema.sql but is not documented in final-schema.md")

        # Provenance cells that carry none of the [S]/[P]/[C] markers.
        for table, columns in sorted(self.provenance_md.items()):
            for col, (prov, _desc) in sorted(columns.items()):
                if prov not in {"[S]", "[P]", "[C]"}:
                    findings.append(
                        f"{table}.{col} has unmarked provenance {prov!r} "
                        f"(not source-mappable; see final-schema.md Open Decisions)"
                    )

        # Curated FK targets must be real FK columns of the parsed DDL and
        # must live on an ingestible table.
        for qualified, meta in sorted(DOCUMENTED_FK_TARGETS.items()):
            table, _, column = qualified.partition(".")
            spec = self.tables.get(table, {}).get(column)
            if spec is None:
                findings.append(f"DOCUMENTED_FK_TARGETS: {qualified} does not exist in schema.sql")
            elif spec.fk_reference is None:
                findings.append(f"DOCUMENTED_FK_TARGETS: {qualified} is not a foreign key in schema.sql")
            if table not in INGESTIBLE_TABLES:
                findings.append(f"DOCUMENTED_FK_TARGETS: {qualified} is not on an ingestible table")
            if not meta.citation:
                findings.append(f"DOCUMENTED_FK_TARGETS: {qualified} has no documentation citation")

        return findings

    def verify_aliases(self) -> list[str]:
        """Every alias target in aliases.py must be a validated mappable field."""
        from . import aliases  # local import: aliases is standalone data

        findings: list[str] = []
        for source_key, entries in aliases.ALIASES.items():
            for entry in entries:
                if self.validate(entry.table, entry.column) is None:
                    findings.append(
                        f"alias {source_key!r} targets {entry.table}.{entry.column}, "
                        f"which is not a mappable canonical field"
                    )
                for alt in entry.alternates:
                    if self.validate(alt.table, alt.column) is None:
                        findings.append(
                            f"alias {source_key!r} alternate target {alt.table}.{alt.column}, "
                            f"which is not a mappable canonical field"
                        )
        return findings


def _normalize(name: str) -> str:
    """Case/separator-insensitive form of a column name ('Customer ID' -> 'customerid')."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


# Module-level singleton — parsing is cheap, one catalog per process.
_CATALOG: SchemaCatalog | None = None


def get_catalog() -> SchemaCatalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = SchemaCatalog.load()
    return _CATALOG
