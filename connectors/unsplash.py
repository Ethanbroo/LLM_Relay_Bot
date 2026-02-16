"""Unsplash connector for deterministic image search and download.

CLOSED WORLD: Only implements unsplash.search_photos with deterministic scoring.
"""

import json
import requests
import base64
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from connectors.base import BaseConnector, ConnectorRequest, ConnectorContext
from connectors.results import ConnectorResult, RollbackResult, ConnectorStatus, ExecutionArtifact
from connectors.errors import ConnectorError, SecretUnavailableError
from connectors.blog_errors import BlogError, BlogErrorCode
from connectors.blog_utils import tokenize


# HTTP Policy
HTTP_TIMEOUT_SECONDS = 15
MAX_RETRIES = 1
RETRYABLE_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)

# Unsplash Config
SEARCH_PER_PAGE = 30
MIN_SCORE = 6
MAX_DOWNLOAD_SIZE = 6 * 1024 * 1024  # 6MB


@dataclass
class UnsplashConfig:
    """Unsplash API configuration."""
    access_key: str
    application_id: str


@dataclass
class ScoredImage:
    """Image with deterministic score."""
    image_id: str
    score: int
    likes: int
    url_regular: str
    download_location: str
    description: str
    alt_description: str


class UnsplashConnector(BaseConnector):
    """Connector for Unsplash image search with deterministic scoring.

    Supported actions (CLOSED):
    - unsplash.search_photos

    Scoring formula: 2×title_token_overlap + 1×keyword_overlap
    Tie-break: score DESC, likes DESC, image_id ASC (deterministic)
    Min score: 6

    Authentication: Unsplash Access Key
    """

    def __init__(self):
        """Initialize Unsplash connector."""
        self._connected = False
        self._config: Optional[UnsplashConfig] = None
        self._session: Optional[requests.Session] = None

    def get_connector_type(self) -> str:
        """Return connector type identifier."""
        return "unsplash"

    def connect(self, ctx: ConnectorContext) -> None:
        """Establish connection to Unsplash API.

        Args:
            ctx: Connector context with secrets provider

        Raises:
            ConnectorError: If connection fails or secrets unavailable
        """
        if ctx.secrets_provider is None:
            raise ConnectorError("SecretsProvider required for Unsplash connector")

        try:
            # Resolve secrets
            access_key = ctx.secrets_provider.resolve_string("secret:unsplash_access_key")
            application_id = ctx.secrets_provider.resolve_string("secret:unsplash_application_id")

            self._config = UnsplashConfig(
                access_key=access_key,
                application_id=application_id
            )

            # Create session with auth
            self._session = requests.Session()
            self._session.headers.update({
                'Authorization': f'Client-ID {access_key}',
                'Accept-Version': 'v1',
                'User-Agent': 'LLM-Relay/1.0'
            })

            self._connected = True

        except SecretUnavailableError as e:
            raise ConnectorError(f"Secret unavailable: {e}")
        except Exception as e:
            raise ConnectorError(f"Unsplash connection failed: {e}")

    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        """Execute Unsplash operation.

        Args:
            req: Connector request

        Returns:
            ConnectorResult with operation outcome

        Raises:
            ConnectorError: If execution fails
        """
        if not self._connected or self._config is None or self._session is None:
            raise ConnectorError("Unsplash connector not connected")

        # Parse payload
        try:
            payload = json.loads(req.payload_canonical)
        except json.JSONDecodeError as e:
            raise ConnectorError(f"Invalid payload JSON: {e}")

        # Route to action handler (CLOSED WORLD)
        action = req.action

        if action == "unsplash.search_photos":
            return self._search_photos(req, payload)
        else:
            # CLOSED WORLD: Reject unknown actions
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Unknown Unsplash action: {action}",
                retryable=False
            )
            return self._error_result(error)

    def rollback(self, req: ConnectorRequest, artifact: Optional[ExecutionArtifact]) -> RollbackResult:
        """Rollback Unsplash operation.

        Args:
            req: Original connector request
            artifact: ExecutionArtifact from execute (if any)

        Returns:
            RollbackResult
        """
        # Read-only operation, no rollback needed
        return RollbackResult(
            success=True,
            message="No rollback needed for read-only Unsplash search"
        )

    def disconnect(self) -> None:
        """Disconnect from Unsplash API."""
        if self._session:
            self._session.close()
            self._session = None
        self._config = None
        self._connected = False

    # Action handlers

    def _search_photos(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Search Unsplash photos with deterministic scoring.

        Scoring:
        - title_tokens: tokenized post title
        - keywords: top 5 keywords from title+excerpt
        - For each image: tokenize description+alt_description
        - Score = 2×(title tokens in image) + 1×(keywords in image)
        - Tie-break: score DESC, likes DESC, image_id ASC
        - Min score: 6

        Args:
            req: Connector request
            payload: {title_tokens: [str], keywords: [str]}

        Returns:
            ConnectorResult with best image or ERR_IMAGE_LOW_CONFIDENCE
        """
        # Extract fields
        title_tokens = payload.get("title_tokens", [])
        keywords = payload.get("keywords", [])

        if not title_tokens and not keywords:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message="Missing required fields: title_tokens and/or keywords",
                retryable=False
            )
            return self._error_result(error)

        # Build search query from keywords
        query = " ".join(keywords[:5]) if keywords else " ".join(title_tokens[:5])

        # Search Unsplash API
        url = "https://api.unsplash.com/search/photos"
        params = {
            "query": query,
            "per_page": SEARCH_PER_PAGE,
            "orientation": "landscape"  # Prefer landscape for blog featured images
        }

        try:
            response = self._http_get(url, params)
            if isinstance(response, BlogError):
                return self._error_result(response)

            results = response.get("results", [])
            if len(results) == 0:
                error = BlogError(
                    code=BlogErrorCode.ERR_IMAGE_LOW_CONFIDENCE,
                    message="No images found for query",
                    retryable=False
                )
                return self._error_result(error)

            # Score all images deterministically
            scored_images = []
            for result in results:
                scored_image = self._score_image(result, title_tokens, keywords)
                if scored_image:
                    scored_images.append(scored_image)

            if len(scored_images) == 0:
                error = BlogError(
                    code=BlogErrorCode.ERR_IMAGE_LOW_CONFIDENCE,
                    message="No scoreable images in results",
                    retryable=False
                )
                return self._error_result(error)

            # Sort by score DESC, likes DESC, image_id ASC (deterministic tie-breaker)
            scored_images.sort(key=lambda x: (-x.score, -x.likes, x.image_id))

            # Take best image
            best_image = scored_images[0]

            # Check minimum score
            if best_image.score < MIN_SCORE:
                error = BlogError(
                    code=BlogErrorCode.ERR_IMAGE_LOW_CONFIDENCE,
                    message=f"Best image score {best_image.score} below minimum {MIN_SCORE}",
                    retryable=False
                )
                return self._error_result(error)

            # Download image
            download_result = self._download_image(best_image)
            if isinstance(download_result, BlogError):
                return self._error_result(download_result)

            image_data_b64, mime_type, file_size = download_result

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                message=f"Found image {best_image.image_id} with score {best_image.score}",
                artifacts=[
                    ExecutionArtifact(
                        artifact_type="unsplash_search_result",
                        content_type="application/json",
                        data=json.dumps({
                            "image_id": best_image.image_id,
                            "score": best_image.score,
                            "likes": best_image.likes,
                            "url": best_image.url_regular,
                            "description": best_image.description,
                            "alt_description": best_image.alt_description,
                            "image_data": image_data_b64,
                            "mime_type": mime_type,
                            "file_size": file_size
                        })
                    )
                ],
                external_effects={
                    "image_id": best_image.image_id,
                    "score": best_image.score,
                    "download_tracked": True
                }
            )

        except Exception as e:
            error = BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Unsplash search failed: {e}",
                retryable=False
            )
            return self._error_result(error)

    def _score_image(self, result: Dict[str, Any], title_tokens: List[str], keywords: List[str]) -> Optional[ScoredImage]:
        """Score single image deterministically.

        Score = 2×title_token_overlap + 1×keyword_overlap

        Args:
            result: Unsplash API result object
            title_tokens: Tokenized post title
            keywords: Top keywords

        Returns:
            ScoredImage or None if image cannot be scored
        """
        image_id = result.get("id", "")
        if not image_id:
            return None

        # Extract image text fields
        description = result.get("description") or ""
        alt_description = result.get("alt_description") or ""
        combined_text = f"{description} {alt_description}"

        # Tokenize image text
        image_tokens = set(tokenize(combined_text))

        # Calculate overlaps
        title_token_set = set(title_tokens)
        keyword_set = set(keywords)

        title_overlap = len(title_token_set & image_tokens)
        keyword_overlap = len(keyword_set & image_tokens)

        # Calculate score
        score = 2 * title_overlap + 1 * keyword_overlap

        # Extract metadata
        likes = result.get("likes", 0)
        urls = result.get("urls", {})
        url_regular = urls.get("regular", "")
        links = result.get("links", {})
        download_location = links.get("download_location", "")

        if not url_regular or not download_location:
            return None

        return ScoredImage(
            image_id=image_id,
            score=score,
            likes=likes,
            url_regular=url_regular,
            download_location=download_location,
            description=description,
            alt_description=alt_description
        )

    def _download_image(self, image: ScoredImage) -> tuple[str, str, int] | BlogError:
        """Download image and return base64 data.

        Args:
            image: ScoredImage to download

        Returns:
            Tuple of (base64_data, mime_type, file_size) or BlogError
        """
        # Track download (required by Unsplash API guidelines)
        try:
            track_response = self._http_get(image.download_location, {})
            if isinstance(track_response, BlogError):
                return track_response

            # Get actual download URL from tracking response
            download_url = track_response.get("url", image.url_regular)

        except Exception as e:
            return BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Download tracking failed: {e}",
                retryable=False
            )

        # Download image
        try:
            response = self._http_get_binary(download_url)
            if isinstance(response, BlogError):
                return response

            image_bytes, content_type = response

            # Validate size
            if len(image_bytes) > MAX_DOWNLOAD_SIZE:
                return BlogError(
                    code=BlogErrorCode.ERR_VALIDATION,
                    message=f"Image size {len(image_bytes)} exceeds max {MAX_DOWNLOAD_SIZE}",
                    retryable=False
                )

            # Validate mime type (CLOSED WORLD: only jpeg and png)
            if content_type not in ["image/jpeg", "image/png"]:
                return BlogError(
                    code=BlogErrorCode.ERR_VALIDATION,
                    message=f"Invalid mime type '{content_type}': only image/jpeg and image/png allowed",
                    retryable=False
                )

            # Encode to base64
            image_b64 = base64.b64encode(image_bytes).decode('ascii')

            return image_b64, content_type, len(image_bytes)

        except Exception as e:
            return BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Image download failed: {e}",
                retryable=False
            )

    # HTTP helpers with retry policy

    def _http_get(self, url: str, params: Optional[Dict] = None) -> Any | BlogError:
        """HTTP GET with retry policy.

        Args:
            url: Request URL
            params: Query parameters

        Returns:
            Response JSON or BlogError
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)

                # Handle rate limiting
                if response.status_code == 429:
                    return BlogError(
                        code=BlogErrorCode.ERR_RATE_LIMITED,
                        message="Unsplash API rate limit exceeded",
                        retryable=False  # Never retry 429
                    )

                # Handle auth errors
                if response.status_code in [401, 403]:
                    return BlogError(
                        code=BlogErrorCode.ERR_SECRET_UNAVAILABLE,
                        message=f"Unsplash authentication failed: {response.status_code}",
                        retryable=False
                    )

                # Handle client errors
                if 400 <= response.status_code < 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"Unsplash API client error: {response.status_code} {response.text[:200]}",
                        retryable=False
                    )

                # Handle server errors
                if response.status_code >= 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"Unsplash API server error: {response.status_code}",
                        retryable=False
                    )

                response.raise_for_status()
                return response.json()

            except RETRYABLE_ERRORS as e:
                if attempt < MAX_RETRIES:
                    continue  # Retry once
                return BlogError(
                    code=BlogErrorCode.ERR_HTTP,
                    message=f"HTTP request failed after {MAX_RETRIES + 1} attempts: {e}",
                    retryable=False
                )
            except requests.exceptions.RequestException as e:
                return BlogError(
                    code=BlogErrorCode.ERR_HTTP,
                    message=f"HTTP request failed: {e}",
                    retryable=False
                )

    def _http_get_binary(self, url: str) -> tuple[bytes, str] | BlogError:
        """HTTP GET for binary content (images).

        Args:
            url: Request URL

        Returns:
            Tuple of (bytes, content_type) or BlogError
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.get(url, timeout=HTTP_TIMEOUT_SECONDS, stream=True)

                # Handle rate limiting
                if response.status_code == 429:
                    return BlogError(
                        code=BlogErrorCode.ERR_RATE_LIMITED,
                        message="Unsplash API rate limit exceeded",
                        retryable=False
                    )

                # Handle auth errors
                if response.status_code in [401, 403]:
                    return BlogError(
                        code=BlogErrorCode.ERR_SECRET_UNAVAILABLE,
                        message=f"Unsplash authentication failed: {response.status_code}",
                        retryable=False
                    )

                # Handle client errors
                if 400 <= response.status_code < 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"Unsplash API client error: {response.status_code}",
                        retryable=False
                    )

                # Handle server errors
                if response.status_code >= 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"Unsplash API server error: {response.status_code}",
                        retryable=False
                    )

                response.raise_for_status()

                # Read content
                content = response.content
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip()

                return content, content_type

            except RETRYABLE_ERRORS as e:
                if attempt < MAX_RETRIES:
                    continue  # Retry once
                return BlogError(
                    code=BlogErrorCode.ERR_HTTP,
                    message=f"HTTP request failed after {MAX_RETRIES + 1} attempts: {e}",
                    retryable=False
                )
            except requests.exceptions.RequestException as e:
                return BlogError(
                    code=BlogErrorCode.ERR_HTTP,
                    message=f"HTTP request failed: {e}",
                    retryable=False
                )

    def _error_result(self, error: BlogError) -> ConnectorResult:
        """Create error ConnectorResult from BlogError.

        Args:
            error: BlogError

        Returns:
            ConnectorResult with FAILED status
        """
        return ConnectorResult(
            status=ConnectorStatus.FAILED,
            message=error.message,
            artifacts=[
                ExecutionArtifact(
                    artifact_type="blog_error",
                    content_type="application/json",
                    data=json.dumps(error.to_dict())
                )
            ],
            external_effects={"error_code": error.code.value, "retryable": error.retryable}
        )
