"""Blog utility functions for tokenization, keyword extraction, and slug generation.

Deterministic implementations following specification.
"""

import re
from typing import List, Tuple
from collections import Counter


# Frozen stopwords list
STOPWORDS = frozenset([
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for', 'from',
    'has', 'have', 'if', 'in', 'into', 'is', 'it', 'its', 'of', 'on', 'or',
    'that', 'the', 'their', 'then', 'there', 'these', 'this', 'to', 'was',
    'will', 'with', 'you', 'your'
])


def tokenize(text: str) -> List[str]:
    """Tokenize text using regex: [a-z0-9]+

    Args:
        text: Input text

    Returns:
        List of lowercase tokens
    """
    # Lowercase first
    text_lower = text.lower()

    # Extract tokens matching [a-z0-9]+
    tokens = re.findall(r'[a-z0-9]+', text_lower)

    return tokens


def extract_keywords(title: str, excerpt: str, max_keywords: int = 12, top_n: int = 5) -> Tuple[List[str], List[str]]:
    """Extract keywords from title and excerpt.

    Args:
        title: Post title
        excerpt: Post excerpt
        max_keywords: Maximum keywords to extract (default 12)
        top_n: Number of top keywords for image search (default 5)

    Returns:
        Tuple of (all_keywords, top_keywords_for_search)
    """
    # Combine title and excerpt
    combined = f"{title} {excerpt}"

    # Tokenize
    tokens = tokenize(combined)

    # Remove stopwords
    filtered_tokens = [t for t in tokens if t not in STOPWORDS]

    # Count frequency
    token_counts = Counter(filtered_tokens)

    # Sort by frequency DESC, then token ASC
    sorted_tokens = sorted(
        token_counts.items(),
        key=lambda x: (-x[1], x[0])  # -freq for DESC, token for ASC
    )

    # Take first max_keywords
    keywords = [token for token, count in sorted_tokens[:max_keywords]]

    # Top N for image search
    top_keywords = keywords[:top_n]

    return keywords, top_keywords


def generate_slug(text: str, max_length: int = 80) -> str:
    """Generate slug from text.

    Regex: ^[a-z0-9]+(?:-[a-z0-9]+)*$
    Max length: 80

    Args:
        text: Input text

    Returns:
        Slug string

    Raises:
        ValueError: If slug cannot be generated
    """
    # Lowercase
    text_lower = text.lower()

    # Replace non-alphanumeric with spaces
    text_clean = re.sub(r'[^a-z0-9]+', ' ', text_lower)

    # Split into tokens
    tokens = text_clean.split()

    # Join with hyphens
    slug = '-'.join(tokens)

    # Trim to max length at word boundary
    if len(slug) > max_length:
        # Find last hyphen before max_length
        truncated = slug[:max_length]
        last_hyphen = truncated.rfind('-')
        if last_hyphen > 0:
            slug = truncated[:last_hyphen]
        else:
            slug = truncated

    # Validate regex: ^[a-z0-9]+(?:-[a-z0-9]+)*$
    if not re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', slug):
        raise ValueError(f"Generated slug '{slug}' does not match required pattern")

    return slug


def generate_slug_with_collision_suffix(base_slug: str, collision_number: int, max_length: int = 80) -> str:
    """Generate slug with collision number suffix.

    base-2, base-3, ... base-10

    Args:
        base_slug: Base slug
        collision_number: Collision number (2-10)
        max_length: Maximum length

    Returns:
        Slug with suffix

    Raises:
        ValueError: If collision_number > 10
    """
    if collision_number > 10:
        raise ValueError("ERR_SLUG_COLLISION_EXHAUSTED")

    # Add suffix
    slug_with_suffix = f"{base_slug}-{collision_number}"

    # Ensure it fits within max_length
    if len(slug_with_suffix) > max_length:
        # Trim base slug to make room for suffix
        suffix = f"-{collision_number}"
        available_length = max_length - len(suffix)
        if available_length < 1:
            raise ValueError("ERR_SLUG_COLLISION_EXHAUSTED")

        # Trim at word boundary
        trimmed_base = base_slug[:available_length]
        last_hyphen = trimmed_base.rfind('-')
        if last_hyphen > 0:
            trimmed_base = trimmed_base[:last_hyphen]

        slug_with_suffix = f"{trimmed_base}{suffix}"

    return slug_with_suffix


def validate_slug(slug: str) -> bool:
    """Validate slug matches required pattern.

    Args:
        slug: Slug to validate

    Returns:
        True if valid, False otherwise
    """
    if len(slug) > 80:
        return False

    return bool(re.match(r'^[a-z0-9]+(?:-[a-z0-9]+)*$', slug))
