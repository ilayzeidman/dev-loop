"""Schema loading and validation."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from ..util import read_json

SCHEMA_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def load_schema(name: str) -> dict[str, Any]:
    """Load a schema by file name (e.g. ``task_contract.v1.json``)."""
    return read_json(SCHEMA_DIR / name)


def _resolver() -> jsonschema.RefResolver:
    # File-based resolver so $ref between schemas works.
    base_uri = SCHEMA_DIR.absolute().as_uri() + "/"
    return jsonschema.RefResolver(base_uri=base_uri, referrer={})


def validate(name: str, instance: Any) -> None:
    """Validate ``instance`` against schema ``name``.

    Raises ``jsonschema.ValidationError`` on failure.
    """
    schema = load_schema(name)
    Draft202012Validator(schema, resolver=_resolver()).validate(instance)


def is_valid(name: str, instance: Any) -> bool:
    try:
        validate(name, instance)
        return True
    except jsonschema.ValidationError:
        return False
