"""Customerly CDP — standalone LLM-assisted column mapping component.

Framework-free (no Django/DRF), does not modify ``backend/schema.sql`` or any
existing transformation script. It maps the columns of a future store-upload
CSV onto the canonical schema, using exact/alias matching seeded from the
existing mapping documentation and Google Gemini (official ``google-genai``
SDK) for the remainder, then gates everything behind explicit human approval.

Typical flow::

    python -m apps.mapping verify
    python -m apps.mapping map data.csv -o data.mapping.json
    python -m apps.mapping review data.mapping.json --approve-suggested
    python -m apps.mapping apply data.mapping.json data.csv -o out/

See README.md in this package for details.
"""

from __future__ import annotations

from .models import ColumnMapping, MappingResult
from .mapper import map_csv
from .schema_catalog import SchemaCatalog, get_catalog

__all__ = [
    "ColumnMapping",
    "MappingResult",
    "SchemaCatalog",
    "get_catalog",
    "map_csv",
]

__version__ = "0.1.0"
