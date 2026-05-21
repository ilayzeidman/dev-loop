"""Capability registry and built-in capabilities."""

from .base import Capability, CapabilityResult
from .registry import (
    CapabilityRegistry,
    list_capabilities,
    load_default_registry,
    show_capability,
    spec_to_dict,
)

__all__ = [
    "Capability",
    "CapabilityResult",
    "CapabilityRegistry",
    "list_capabilities",
    "load_default_registry",
    "show_capability",
    "spec_to_dict",
]
