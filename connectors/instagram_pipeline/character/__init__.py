"""Character identity management for Instagram pipeline.

This module handles AI character creation, training, and consistency tracking.
Characters are versioned, hash-anchored, and treated as immutable during production.
"""

from .models import CharacterProfile, IdentityAnchor, StyleDNA
from .registry import CharacterRegistry

__all__ = [
    'CharacterProfile',
    'IdentityAnchor',
    'StyleDNA',
    'CharacterRegistry',
]
