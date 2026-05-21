"""Secret redaction for evidence packets sent to the agent.

Raw artifacts are kept by the harness on disk. Anything that crosses the
trust boundary to the LLM gets redacted by ``redact()``.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

# Conservative pattern set. False positives are preferred over leaks.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{36,}\b")),
    ("github_token_fine_grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("private_key_block", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----.*?-----END (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
        re.DOTALL)),
    # generic Bearer tokens
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")),
    # Authorization header values, including in JSON
    ("authorization_header", re.compile(
        r'(?i)("?authorization"?\s*[:=]\s*"?)([^"\n,}]+)')),
    # Cookie / Set-Cookie
    ("cookie_header", re.compile(
        r'(?i)("?(?:set-)?cookie"?\s*[:=]\s*"?)([^"\n]+)')),
    # password / secret / token key=value style
    ("kv_secret", re.compile(
        r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token|auth|cred|credential)\s*[:=]\s*['\"]?([^'\"\s,;]+)")),
    # Postgres-ish connection strings
    ("conn_string", re.compile(
        r"(?i)\b(?:postgres|postgresql|mysql|mongodb)(?:\+[a-z]+)?://[^\s\"']+")),
    # Signed URLs with X-Amz-Signature or sig= parameters
    ("signed_url", re.compile(
        r"(?i)https?://[^\s\"']+[?&](?:X-Amz-Signature|Signature|sig)=[^&\s\"']+[^\s\"']*")),
    # Kubernetes secret values (base64 blocks following "data:" in YAML)
    ("kube_secret_value", re.compile(
        r"(?m)^(\s*[A-Za-z0-9_.\-]+:\s*)([A-Za-z0-9+/=]{40,})\s*$")),
]


def redact_text(text: str) -> str:
    """Return a redacted copy of ``text``."""
    out = text
    for label, pat in _PATTERNS:
        if label in ("authorization_header", "cookie_header"):
            out = pat.sub(lambda m: m.group(1) + REDACTED, out)
        elif label == "kv_secret":
            out = pat.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
        elif label == "kube_secret_value":
            out = pat.sub(lambda m: m.group(1) + REDACTED, out)
        else:
            out = pat.sub(REDACTED, out)
    return out


def redact(value: Any) -> Any:
    """Recursively redact strings inside any JSON-like structure."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value
