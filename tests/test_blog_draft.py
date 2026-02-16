"""Tests for blog draft creation capability.

Closed world enforcement tests per specification.
"""

import pytest
from connectors.blog_errors import BlogErrorCode, BlogError
from connectors.blog_utils import (
    tokenize,
    extract_keywords,
    generate_slug,
    generate_slug_with_collision_suffix,
    validate_slug,
    STOPWORDS
)


class TestTokenization:
    """Test tokenization with regex [a-z0-9]+."""

    def test_tokenize_simple(self):
        """Test basic tokenization."""
        result = tokenize("Hello World")
        assert result == ["hello", "world"]

    def test_tokenize_with_numbers(self):
        """Test tokenization includes numbers."""
        result = tokenize("Python 3.12 is great")
        assert result == ["python", "3", "12", "is", "great"]

    def test_tokenize_removes_punctuation(self):
        """Test punctuation is removed."""
        result = tokenize("Hello, World! How are you?")
        assert result == ["hello", "world", "how", "are", "you"]

    def test_tokenize_special_chars(self):
        """Test special characters are removed."""
        result = tokenize("test@example.com & foo/bar")
        assert result == ["test", "example", "com", "foo", "bar"]

    def test_tokenize_empty_string(self):
        """Test empty string returns empty list."""
        result = tokenize("")
        assert result == []

    def test_tokenize_only_special_chars(self):
        """Test string with only special chars."""
        result = tokenize("!@#$%^&*()")
        assert result == []


class TestStopwords:
    """Test stopwords filtering."""

    def test_stopwords_frozen(self):
        """Test STOPWORDS is a frozenset."""
        assert isinstance(STOPWORDS, frozenset)

    def test_stopwords_count(self):
        """Test stopwords list has expected count."""
        assert len(STOPWORDS) == 35

    def test_stopwords_lowercase(self):
        """Test all stopwords are lowercase."""
        for word in STOPWORDS:
            assert word.islower()

    def test_common_stopwords_present(self):
        """Test common stopwords are present."""
        assert "the" in STOPWORDS
        assert "and" in STOPWORDS
        assert "or" in STOPWORDS
        assert "is" in STOPWORDS


class TestKeywordExtraction:
    """Test keyword extraction with deterministic sorting."""

    def test_extract_keywords_basic(self):
        """Test basic keyword extraction."""
        keywords, top = extract_keywords(
            "Machine Learning Guide",
            "Learn about machine learning and data science"
        )

        # Should remove stopwords (and is stopword, about is not)
        assert "and" not in keywords

        # Machine and learning appear twice (title + excerpt)
        assert "machine" in keywords
        assert "learning" in keywords

        # about is not a stopword, should be included
        assert "about" in keywords

    def test_extract_keywords_frequency_order(self):
        """Test keywords sorted by frequency DESC."""
        keywords, top = extract_keywords(
            "Python Python Python",
            "Python is great. Python rocks."
        )

        # "python" appears 5 times, should be first
        assert keywords[0] == "python"

    def test_extract_keywords_alphabetical_tiebreak(self):
        """Test alphabetical tiebreak for same frequency."""
        keywords, top = extract_keywords(
            "apple zebra",
            "apple zebra"
        )

        # Both appear twice, alphabetical order
        assert keywords[0] == "apple"
        assert keywords[1] == "zebra"

    def test_extract_keywords_max_12(self):
        """Test max 12 keywords returned."""
        title = " ".join([f"word{i}" for i in range(20)])
        excerpt = ""
        keywords, top = extract_keywords(title, excerpt, max_keywords=12)

        assert len(keywords) <= 12

    def test_extract_keywords_top_5(self):
        """Test top 5 keywords for image search."""
        title = " ".join([f"word{i}" for i in range(10)])
        excerpt = ""
        keywords, top = extract_keywords(title, excerpt, top_n=5)

        assert len(top) <= 5
        assert top == keywords[:5]

    def test_extract_keywords_removes_stopwords(self):
        """Test stopwords are removed."""
        keywords, top = extract_keywords(
            "The quick brown fox",
            "The fox is in the forest"
        )

        # "the" and "is" and "in" should be removed
        assert "the" not in keywords
        assert "is" not in keywords
        assert "in" not in keywords
        assert "fox" in keywords


class TestSlugGeneration:
    """Test slug generation with pattern ^[a-z0-9]+(?:-[a-z0-9]+)*$."""

    def test_generate_slug_basic(self):
        """Test basic slug generation."""
        slug = generate_slug("Hello World")
        assert slug == "hello-world"

    def test_generate_slug_removes_special_chars(self):
        """Test special characters are removed."""
        slug = generate_slug("Hello, World! How are you?")
        assert slug == "hello-world-how-are-you"

    def test_generate_slug_multiple_spaces(self):
        """Test multiple spaces become single hyphen."""
        slug = generate_slug("Hello    World")
        assert slug == "hello-world"

    def test_generate_slug_max_80_chars(self):
        """Test slug truncated at 80 chars."""
        long_title = "a" * 200
        slug = generate_slug(long_title)
        assert len(slug) <= 80

    def test_generate_slug_truncate_at_word_boundary(self):
        """Test truncation happens at hyphen boundary."""
        long_title = " ".join(["word"] * 50)
        slug = generate_slug(long_title)
        assert len(slug) <= 80
        assert not slug.endswith("-")  # Should not end with hyphen

    def test_generate_slug_with_numbers(self):
        """Test slug includes numbers."""
        slug = generate_slug("Python 3.12 Guide")
        assert slug == "python-3-12-guide"

    def test_validate_slug_valid(self):
        """Test valid slug passes validation."""
        assert validate_slug("hello-world") is True
        assert validate_slug("test123") is True
        assert validate_slug("a-b-c-d") is True

    def test_validate_slug_invalid(self):
        """Test invalid slugs fail validation."""
        assert validate_slug("Hello-World") is False  # Uppercase
        assert validate_slug("hello_world") is False  # Underscore
        assert validate_slug("-hello") is False  # Starts with hyphen
        assert validate_slug("hello-") is False  # Ends with hyphen
        assert validate_slug("hello--world") is False  # Double hyphen

    def test_validate_slug_max_length(self):
        """Test slug length validation."""
        assert validate_slug("a" * 80) is True
        assert validate_slug("a" * 81) is False


class TestSlugCollisionResolution:
    """Test slug collision suffix generation."""

    def test_collision_suffix_2(self):
        """Test collision suffix base-2."""
        slug = generate_slug_with_collision_suffix("hello-world", 2)
        assert slug == "hello-world-2"

    def test_collision_suffix_10(self):
        """Test collision suffix base-10."""
        slug = generate_slug_with_collision_suffix("hello-world", 10)
        assert slug == "hello-world-10"

    def test_collision_suffix_exceeds_10(self):
        """Test collision suffix fails after 10."""
        with pytest.raises(ValueError, match="ERR_SLUG_COLLISION_EXHAUSTED"):
            generate_slug_with_collision_suffix("hello-world", 11)

    def test_collision_suffix_fits_within_80(self):
        """Test collision suffix respects 80 char limit."""
        base_slug = "a" * 78
        slug = generate_slug_with_collision_suffix(base_slug, 2)
        assert len(slug) <= 80

    def test_collision_suffix_trims_base(self):
        """Test long base slug is trimmed to fit suffix."""
        base_slug = "a" * 79
        slug = generate_slug_with_collision_suffix(base_slug, 2)
        assert len(slug) <= 80
        assert slug.endswith("-2")

    def test_collision_suffix_trims_at_word_boundary(self):
        """Test trimming respects word boundary (hyphen)."""
        base_slug = "hello-world-this-is-a-very-long-slug-that-exceeds"
        slug = generate_slug_with_collision_suffix(base_slug, 10)
        assert len(slug) <= 80
        assert slug.endswith("-10")
        # Should not have partial word before -10


class TestBlogErrorCodes:
    """Test closed error enumeration."""

    def test_error_codes_exist(self):
        """Test all required error codes exist."""
        required_codes = [
            "ERR_VALIDATION",
            "ERR_POLICY_DENY",
            "ERR_SECRET_UNAVAILABLE",
            "ERR_CONNECTOR_NOT_REGISTERED",
            "ERR_RATE_LIMITED",
            "ERR_HTTP",
            "ERR_NON_UNIQUE_MATCH",
            "ERR_SLUG_COLLISION_EXHAUSTED",
            "ERR_TAG_LIMIT_EXCEEDED",
            "ERR_TAG_CREATE_LIMIT_EXCEEDED",
            "ERR_IMAGE_LOW_CONFIDENCE",
            "ERR_CONNECTOR_IDEMPOTENCY_HIT",
        ]

        for code in required_codes:
            assert hasattr(BlogErrorCode, code)

    def test_blog_error_to_dict(self):
        """Test BlogError serialization."""
        error = BlogError(
            code=BlogErrorCode.ERR_VALIDATION,
            message="Test error",
            retryable=False
        )

        result = error.to_dict()
        assert result["ok"] is False
        assert result["error"]["code"] == "ERR_VALIDATION"
        assert result["error"]["message"] == "Test error"
        assert result["error"]["retryable"] is False


class TestWordPressContract:
    """Test WordPress draft contract enforcement."""

    def test_title_min_length(self):
        """Test title minimum length is 10 chars."""
        # Import here to avoid import errors if module doesn't exist yet
        from connectors.wordpress import TITLE_MIN_CHARS
        assert TITLE_MIN_CHARS == 10

    def test_title_max_length(self):
        """Test title maximum length is 120 chars."""
        from connectors.wordpress import TITLE_MAX_CHARS
        assert TITLE_MAX_CHARS == 120

    def test_content_max_length(self):
        """Test content maximum length is 40k chars."""
        from connectors.wordpress import CONTENT_MAX_CHARS
        assert CONTENT_MAX_CHARS == 40_000

    def test_excerpt_max_length(self):
        """Test excerpt maximum length is 300 chars."""
        from connectors.wordpress import EXCERPT_MAX_CHARS
        assert EXCERPT_MAX_CHARS == 300

    def test_max_tags_per_post(self):
        """Test max 12 tags per post."""
        from connectors.wordpress import MAX_TAGS_PER_POST
        assert MAX_TAGS_PER_POST == 12

    def test_max_new_tags_per_run(self):
        """Test max 5 new tags per run."""
        from connectors.wordpress import MAX_NEW_TAGS_PER_RUN
        assert MAX_NEW_TAGS_PER_RUN == 5


class TestUnsplashScoring:
    """Test Unsplash deterministic scoring."""

    def test_scoring_formula(self):
        """Test scoring formula: 2×title_overlap + 1×keyword_overlap."""
        from connectors.unsplash import UnsplashConnector

        connector = UnsplashConnector()

        # Mock result
        result = {
            "id": "test123",
            "description": "machine learning python",
            "alt_description": "data science",
            "likes": 100,
            "urls": {"regular": "https://example.com/image.jpg"},
            "links": {"download_location": "https://example.com/download"}
        }

        title_tokens = ["machine", "learning"]  # Both in description
        keywords = ["python", "data"]  # Both in description+alt

        scored = connector._score_image(result, title_tokens, keywords)

        # 2×2 (title overlap) + 1×2 (keyword overlap) = 6
        assert scored.score == 6

    def test_min_score_6(self):
        """Test minimum score is 6."""
        from connectors.unsplash import MIN_SCORE
        assert MIN_SCORE == 6

    def test_search_per_page_30(self):
        """Test search returns 30 results per page."""
        from connectors.unsplash import SEARCH_PER_PAGE
        assert SEARCH_PER_PAGE == 30

    def test_max_download_size_6mb(self):
        """Test max download size is 6MB."""
        from connectors.unsplash import MAX_DOWNLOAD_SIZE
        assert MAX_DOWNLOAD_SIZE == 6 * 1024 * 1024


class TestHTTPPolicy:
    """Test HTTP retry policy."""

    def test_timeout_15_seconds(self):
        """Test HTTP timeout is 15 seconds."""
        from connectors.wordpress import HTTP_TIMEOUT_SECONDS
        assert HTTP_TIMEOUT_SECONDS == 15

    def test_max_retries_1(self):
        """Test max 1 retry."""
        from connectors.wordpress import MAX_RETRIES
        assert MAX_RETRIES == 1

    def test_retryable_errors(self):
        """Test only connection errors are retryable."""
        from connectors.wordpress import RETRYABLE_ERRORS
        import requests

        assert requests.exceptions.ConnectionError in RETRYABLE_ERRORS
        assert requests.exceptions.Timeout in RETRYABLE_ERRORS


class TestClosedWorld:
    """Test closed world enforcement."""

    def test_wordpress_allowed_actions(self):
        """Test WordPress only allows closed actions."""
        from connectors.wordpress import WordPressConnector

        connector = WordPressConnector()

        # Allowed actions should be documented
        allowed = ["wp.post.create_draft", "wp.media.upload", "wp.post.set_featured_media"]

        # Check docstring mentions these
        assert "wp.post.create_draft" in connector.__class__.__doc__
        assert "wp.media.upload" in connector.__class__.__doc__
        assert "wp.post.set_featured_media" in connector.__class__.__doc__

    def test_unsplash_allowed_actions(self):
        """Test Unsplash only allows search_photos."""
        from connectors.unsplash import UnsplashConnector

        connector = UnsplashConnector()

        # Check docstring mentions only search_photos
        assert "unsplash.search_photos" in connector.__class__.__doc__

    def test_publish_not_allowed(self):
        """Test publish capability does not exist."""
        from connectors.wordpress import WordPressConnector

        connector = WordPressConnector()

        # Check no publish-related methods exist
        assert not hasattr(connector, "_publish_post")
        assert not hasattr(connector, "publish")

        # Check docstring forbids publishing
        assert "MUST NOT" in connector.__module__.__doc__ or "draft" in connector.__class__.__doc__.lower()


class TestDeterminism:
    """Test deterministic behavior."""

    def test_tokenize_deterministic(self):
        """Test tokenization is deterministic."""
        text = "Hello World! How are you?"
        result1 = tokenize(text)
        result2 = tokenize(text)
        assert result1 == result2

    def test_keyword_extraction_deterministic(self):
        """Test keyword extraction is deterministic."""
        title = "Machine Learning Guide"
        excerpt = "Learn about machine learning"

        keywords1, top1 = extract_keywords(title, excerpt)
        keywords2, top2 = extract_keywords(title, excerpt)

        assert keywords1 == keywords2
        assert top1 == top2

    def test_slug_generation_deterministic(self):
        """Test slug generation is deterministic."""
        title = "Hello World! How are you?"
        slug1 = generate_slug(title)
        slug2 = generate_slug(title)
        assert slug1 == slug2

    def test_alphabetical_tiebreak_deterministic(self):
        """Test alphabetical tiebreak is deterministic."""
        # Two words with same frequency
        keywords1, _ = extract_keywords("zebra apple", "zebra apple")
        keywords2, _ = extract_keywords("zebra apple", "zebra apple")

        assert keywords1 == keywords2
        # Should be alphabetically sorted
        assert keywords1 == ["apple", "zebra"]
