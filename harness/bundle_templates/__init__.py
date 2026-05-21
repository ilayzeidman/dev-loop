"""Built-in bundle templates shipped with dev-loop.

Each ``*.json`` in this directory is a regular ``dev-loop-bundle``
payload with one extra top-level key, ``template``, carrying the
metadata the UI strip needs:

    {
      "format": "dev-loop-bundle",
      "format_version": 1,
      "template": {
        "id": "minimal-python",
        "title": "Minimal Python project",
        "summary": "...",
        "tags": ["python", "starter"],
        "order": 10
      },
      "config":  {...},
      "scenarios": [...],
      "playbooks": [...]
    }

The bundle body is the same shape :func:`harness.bundle.build_bundle`
emits, so the same ``preview_apply`` / ``apply_bundle`` pipeline
imports a template into a repo. That keeps the templates strip in
Share & reuse on the same code path as user-supplied bundles — there
is no second importer to maintain.
"""

from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent
