"""Capability registry and built-in capabilities."""

from .base import Capability, CapabilityResult
from .registry import CapabilityRegistry, load_default_registry

__all__ = [
    "Capability",
    "CapabilityResult",
    "CapabilityRegistry",
    "load_default_registry",
]
