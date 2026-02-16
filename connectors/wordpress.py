"""WordPress connector for blog draft creation only.

CLOSED WORLD: Only implements wp.post.create_draft, wp.media.upload, wp.post.set_featured_media.
Publish capability MUST NOT EXIST.
"""

import json
import hashlib
import requests
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from connectors.base import BaseConnector, ConnectorRequest, ConnectorContext
from connectors.results import ConnectorResult, RollbackResult, ConnectorStatus, ExecutionArtifact
from connectors.errors import ConnectorError, SecretUnavailableError
from connectors.blog_errors import BlogError, BlogErrorCode
from connectors.blog_utils import generate_slug, generate_slug_with_collision_suffix, validate_slug, tokenize


# HTTP Policy
HTTP_TIMEOUT_SECONDS = 15
MAX_RETRIES = 1
RETRYABLE_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)

# WordPress Contract Limits
TITLE_MIN_CHARS = 10
TITLE_MAX_CHARS = 120
CONTENT_MAX_CHARS = 40_000
EXCERPT_MAX_CHARS = 300
MAX_TAGS_PER_POST = 12
MAX_NEW_TAGS_PER_RUN = 5


@dataclass
class WordPressConfig:
    """WordPress connection configuration."""
    base_url: str
    username: str
    app_password: str


class WordPressConnector(BaseConnector):
    """Connector for WordPress draft creation only.

    Supported actions (CLOSED):
    - wp.post.create_draft
    - wp.media.upload
    - wp.post.set_featured_media

    Authentication: WordPress Application Password (Basic Auth)
    """

    def __init__(self):
        """Initialize WordPress connector."""
        self._connected = False
        self._config: Optional[WordPressConfig] = None
        self._session: Optional[requests.Session] = None

    def get_connector_type(self) -> str:
        """Return connector type identifier."""
        return "wordpress"

    def connect(self, ctx: ConnectorContext) -> None:
        """Establish connection to WordPress.

        Args:
            ctx: Connector context with secrets provider

        Raises:
            ConnectorError: If connection fails or secrets unavailable
        """
        if ctx.secrets_provider is None:
            raise ConnectorError("SecretsProvider required for WordPress connector")

        try:
            # Resolve secrets
            base_url = ctx.secrets_provider.resolve_string("secret:wp_base_url")
            username = ctx.secrets_provider.resolve_string("secret:wp_username")
            app_password = ctx.secrets_provider.resolve_string("secret:wp_app_password")

            # Validate base URL format
            if not base_url.startswith(('http://', 'https://')):
                raise ConnectorError("WP_BASE_URL must start with http:// or https://")

            # Strip trailing slash
            base_url = base_url.rstrip('/')

            self._config = WordPressConfig(
                base_url=base_url,
                username=username,
                app_password=app_password
            )

            # Create session with auth
            self._session = requests.Session()
            self._session.auth = (username, app_password)
            self._session.headers.update({
                'Content-Type': 'application/json',
                'User-Agent': 'LLM-Relay/1.0'
            })

            self._connected = True

        except SecretUnavailableError as e:
            raise ConnectorError(f"Secret unavailable: {e}")
        except Exception as e:
            raise ConnectorError(f"WordPress connection failed: {e}")

    def execute(self, req: ConnectorRequest) -> ConnectorResult:
        """Execute WordPress operation.

        Args:
            req: Connector request

        Returns:
            ConnectorResult with operation outcome

        Raises:
            ConnectorError: If execution fails
        """
        if not self._connected or self._config is None or self._session is None:
            raise ConnectorError("WordPress connector not connected")

        # Parse payload
        try:
            payload = json.loads(req.payload_canonical)
        except json.JSONDecodeError as e:
            raise ConnectorError(f"Invalid payload JSON: {e}")

        # Route to action handler (CLOSED WORLD)
        action = req.action

        if action == "wp.post.create_draft":
            return self._create_draft(req, payload)
        elif action == "wp.media.upload":
            return self._upload_media(req, payload)
        elif action == "wp.post.set_featured_media":
            return self._set_featured_media(req, payload)
        else:
            # CLOSED WORLD: Reject unknown actions
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Unknown WordPress action: {action}",
                retryable=False
            )
            return self._error_result(error)

    def rollback(self, req: ConnectorRequest, artifact: Optional[ExecutionArtifact]) -> RollbackResult:
        """Rollback WordPress operation.

        Args:
            req: Original connector request
            artifact: ExecutionArtifact from execute (if any)

        Returns:
            RollbackResult
        """
        action = req.action

        # Draft operations are safe to leave (won't be published)
        if action == "wp.post.create_draft":
            return RollbackResult(
                success=True,
                message="Draft rollback not required (status=draft, unpublished)"
            )
        elif action == "wp.media.upload":
            # Media cleanup could be implemented here
            return RollbackResult(
                success=True,
                message="Media rollback: uploaded media remains (orphaned if post rolled back)"
            )
        elif action == "wp.post.set_featured_media":
            return RollbackResult(
                success=True,
                message="Featured media rollback: metadata remains (safe for draft)"
            )
        else:
            return RollbackResult(
                success=True,
                message=f"No rollback needed for {action}"
            )

    def disconnect(self) -> None:
        """Disconnect from WordPress."""
        if self._session:
            self._session.close()
            self._session = None
        self._config = None
        self._connected = False

    # Action handlers

    def _create_draft(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Create or update WordPress draft.

        Search-before-write logic:
        1. Search by slug (exact match)
        2. If no match, search by title (exact match)
        3. If 1 match found, update existing
        4. If >1 match, return ERR_NON_UNIQUE_MATCH
        5. If no match, create new draft

        Args:
            req: Connector request
            payload: Action payload

        Returns:
            ConnectorResult
        """
        # Extract and validate fields
        title = payload.get("title", "")
        content = payload.get("content", "")
        excerpt = payload.get("excerpt", "")
        tags = payload.get("tags", [])
        status = payload.get("status", "draft")

        # CLOSED WORLD: Status MUST be "draft"
        if status != "draft":
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Invalid status '{status}': only 'draft' allowed",
                retryable=False
            )
            return self._error_result(error)

        # Validate title
        if len(title) < TITLE_MIN_CHARS or len(title) > TITLE_MAX_CHARS:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Title length {len(title)} outside range [{TITLE_MIN_CHARS}, {TITLE_MAX_CHARS}]",
                retryable=False
            )
            return self._error_result(error)

        # Validate content
        if len(content) > CONTENT_MAX_CHARS:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Content length {len(content)} exceeds max {CONTENT_MAX_CHARS}",
                retryable=False
            )
            return self._error_result(error)

        # Validate excerpt
        if len(excerpt) > EXCERPT_MAX_CHARS:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Excerpt length {len(excerpt)} exceeds max {EXCERPT_MAX_CHARS}",
                retryable=False
            )
            return self._error_result(error)

        # Validate tags count
        if len(tags) > MAX_TAGS_PER_POST:
            error = BlogError(
                code=BlogErrorCode.ERR_TAG_LIMIT_EXCEEDED,
                message=f"Tag count {len(tags)} exceeds max {MAX_TAGS_PER_POST}",
                retryable=False
            )
            return self._error_result(error)

        # Generate slug
        try:
            base_slug = generate_slug(title)
        except ValueError as e:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Slug generation failed: {e}",
                retryable=False
            )
            return self._error_result(error)

        # Search by slug (exact match)
        slug_matches = self._search_posts_by_slug(base_slug)
        if isinstance(slug_matches, BlogError):
            return self._error_result(slug_matches)

        if len(slug_matches) == 1:
            # Update existing post
            return self._update_existing_draft(slug_matches[0], title, content, excerpt, tags, base_slug)
        elif len(slug_matches) > 1:
            error = BlogError(
                code=BlogErrorCode.ERR_NON_UNIQUE_MATCH,
                message=f"Multiple posts found with slug '{base_slug}'",
                retryable=False
            )
            return self._error_result(error)

        # No slug match - search by title (exact match)
        title_matches = self._search_posts_by_title(title)
        if isinstance(title_matches, BlogError):
            return self._error_result(title_matches)

        if len(title_matches) == 1:
            # Update existing post
            return self._update_existing_draft(title_matches[0], title, content, excerpt, tags, base_slug)
        elif len(title_matches) > 1:
            error = BlogError(
                code=BlogErrorCode.ERR_NON_UNIQUE_MATCH,
                message=f"Multiple posts found with title '{title}'",
                retryable=False
            )
            return self._error_result(error)

        # No match - create new draft with collision-safe slug
        final_slug = self._find_available_slug(base_slug)
        if isinstance(final_slug, BlogError):
            return self._error_result(final_slug)

        return self._create_new_draft(title, content, excerpt, tags, final_slug)

    def _search_posts_by_slug(self, slug: str) -> List[Dict[str, Any]] | BlogError:
        """Search posts by slug (exact match).

        Args:
            slug: Post slug

        Returns:
            List of matching posts or BlogError
        """
        url = f"{self._config.base_url}/wp-json/wp/v2/posts"
        params = {"slug": slug, "status": "draft,publish,pending,private"}

        try:
            response = self._http_get(url, params)
            if isinstance(response, BlogError):
                return response
            return response
        except Exception as e:
            return BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Slug search failed: {e}",
                retryable=False
            )

    def _search_posts_by_title(self, title: str) -> List[Dict[str, Any]] | BlogError:
        """Search posts by title (exact match).

        Args:
            title: Post title

        Returns:
            List of matching posts or BlogError
        """
        url = f"{self._config.base_url}/wp-json/wp/v2/posts"
        params = {"search": title, "status": "draft,publish,pending,private"}

        try:
            response = self._http_get(url, params)
            if isinstance(response, BlogError):
                return response

            # Filter for exact title match (WordPress search is fuzzy)
            exact_matches = [post for post in response if post.get("title", {}).get("rendered", "") == title]
            return exact_matches
        except Exception as e:
            return BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Title search failed: {e}",
                retryable=False
            )

    def _find_available_slug(self, base_slug: str) -> str | BlogError:
        """Find available slug with collision resolution.

        Tries: base, base-2, base-3, ... base-10

        Args:
            base_slug: Base slug

        Returns:
            Available slug or BlogError
        """
        # Try base slug first
        matches = self._search_posts_by_slug(base_slug)
        if isinstance(matches, BlogError):
            return matches
        if len(matches) == 0:
            return base_slug

        # Try collision suffixes 2-10
        for collision_num in range(2, 11):
            try:
                candidate_slug = generate_slug_with_collision_suffix(base_slug, collision_num)
            except ValueError:
                return BlogError(
                    code=BlogErrorCode.ERR_SLUG_COLLISION_EXHAUSTED,
                    message="Slug collision limit exhausted (tried base through base-10)",
                    retryable=False
                )

            matches = self._search_posts_by_slug(candidate_slug)
            if isinstance(matches, BlogError):
                return matches
            if len(matches) == 0:
                return candidate_slug

        # Exhausted all attempts
        return BlogError(
            code=BlogErrorCode.ERR_SLUG_COLLISION_EXHAUSTED,
            message="Slug collision limit exhausted (tried base through base-10)",
            retryable=False
        )

    def _create_new_draft(self, title: str, content: str, excerpt: str, tags: List[str], slug: str) -> ConnectorResult:
        """Create new WordPress draft.

        Args:
            title: Post title
            content: Post content (HTML)
            excerpt: Post excerpt
            tags: Tag names
            slug: Post slug

        Returns:
            ConnectorResult
        """
        # Resolve tag names to tag IDs (create missing tags)
        tag_ids_result = self._resolve_tags(tags)
        if isinstance(tag_ids_result, BlogError):
            return self._error_result(tag_ids_result)
        tag_ids = tag_ids_result

        # Create post
        url = f"{self._config.base_url}/wp-json/wp/v2/posts"
        post_data = {
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "slug": slug,
            "status": "draft",
            "tags": tag_ids
        }

        try:
            response = self._http_post(url, post_data)
            if isinstance(response, BlogError):
                return self._error_result(response)

            post_id = response.get("id")
            post_link = response.get("link", "")

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                message=f"Created WordPress draft: {title}",
                artifacts=[
                    ExecutionArtifact(
                        artifact_type="wordpress_draft",
                        content_type="application/json",
                        data=json.dumps({
                            "post_id": post_id,
                            "title": title,
                            "slug": slug,
                            "status": "draft",
                            "link": post_link,
                            "operation": "create"
                        })
                    )
                ],
                external_effects={"post_id": post_id, "operation": "create"}
            )
        except Exception as e:
            error = BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Draft creation failed: {e}",
                retryable=False
            )
            return self._error_result(error)

    def _update_existing_draft(self, post: Dict[str, Any], title: str, content: str, excerpt: str, tags: List[str], slug: str) -> ConnectorResult:
        """Update existing WordPress draft.

        Args:
            post: Existing post object
            title: New title
            content: New content
            excerpt: New excerpt
            tags: New tags
            slug: New slug

        Returns:
            ConnectorResult
        """
        post_id = post.get("id")

        # Resolve tag names to tag IDs (create missing tags)
        tag_ids_result = self._resolve_tags(tags)
        if isinstance(tag_ids_result, BlogError):
            return self._error_result(tag_ids_result)
        tag_ids = tag_ids_result

        # Update post
        url = f"{self._config.base_url}/wp-json/wp/v2/posts/{post_id}"
        post_data = {
            "title": title,
            "content": content,
            "excerpt": excerpt,
            "slug": slug,
            "status": "draft",  # Force draft status
            "tags": tag_ids
        }

        try:
            response = self._http_post(url, post_data)
            if isinstance(response, BlogError):
                return self._error_result(response)

            post_link = response.get("link", "")

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                message=f"Updated WordPress draft: {title}",
                artifacts=[
                    ExecutionArtifact(
                        artifact_type="wordpress_draft",
                        content_type="application/json",
                        data=json.dumps({
                            "post_id": post_id,
                            "title": title,
                            "slug": slug,
                            "status": "draft",
                            "link": post_link,
                            "operation": "update"
                        })
                    )
                ],
                external_effects={"post_id": post_id, "operation": "update"}
            )
        except Exception as e:
            error = BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Draft update failed: {e}",
                retryable=False
            )
            return self._error_result(error)

    def _resolve_tags(self, tag_names: List[str]) -> List[int] | BlogError:
        """Resolve tag names to tag IDs, creating missing tags.

        Max 5 new tags per run.

        Args:
            tag_names: List of tag names

        Returns:
            List of tag IDs or BlogError
        """
        tag_ids = []
        created_count = 0

        for tag_name in tag_names:
            # Search for existing tag
            url = f"{self._config.base_url}/wp-json/wp/v2/tags"
            params = {"search": tag_name}

            try:
                response = self._http_get(url, params)
                if isinstance(response, BlogError):
                    return response

                # Check for exact match
                exact_match = None
                for tag in response:
                    if tag.get("name", "").lower() == tag_name.lower():
                        exact_match = tag
                        break

                if exact_match:
                    tag_ids.append(exact_match["id"])
                else:
                    # Create new tag
                    if created_count >= MAX_NEW_TAGS_PER_RUN:
                        return BlogError(
                            code=BlogErrorCode.ERR_TAG_CREATE_LIMIT_EXCEEDED,
                            message=f"New tag creation limit exceeded (max {MAX_NEW_TAGS_PER_RUN} per run)",
                            retryable=False
                        )

                    create_url = f"{self._config.base_url}/wp-json/wp/v2/tags"
                    create_data = {"name": tag_name}

                    create_response = self._http_post(create_url, create_data)
                    if isinstance(create_response, BlogError):
                        return create_response

                    tag_ids.append(create_response["id"])
                    created_count += 1

            except Exception as e:
                return BlogError(
                    code=BlogErrorCode.ERR_HTTP,
                    message=f"Tag resolution failed: {e}",
                    retryable=False
                )

        return tag_ids

    def _upload_media(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Upload media to WordPress.

        Args:
            req: Connector request
            payload: {image_data: base64, filename: str, mime_type: str}

        Returns:
            ConnectorResult
        """
        import base64

        image_data_b64 = payload.get("image_data", "")
        filename = payload.get("filename", "")
        mime_type = payload.get("mime_type", "")

        # Validate mime type (CLOSED WORLD: only jpeg and png)
        if mime_type not in ["image/jpeg", "image/png"]:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Invalid mime_type '{mime_type}': only image/jpeg and image/png allowed",
                retryable=False
            )
            return self._error_result(error)

        # Decode image data
        try:
            image_bytes = base64.b64decode(image_data_b64)
        except Exception as e:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Invalid base64 image_data: {e}",
                retryable=False
            )
            return self._error_result(error)

        # Validate size (max 6MB)
        max_size = 6 * 1024 * 1024
        if len(image_bytes) > max_size:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message=f"Image size {len(image_bytes)} exceeds max {max_size} bytes",
                retryable=False
            )
            return self._error_result(error)

        # Upload to WordPress
        url = f"{self._config.base_url}/wp-json/wp/v2/media"

        # Create multipart upload
        files = {
            'file': (filename, image_bytes, mime_type)
        }

        try:
            # Use custom HTTP call for multipart/form-data
            response = self._http_post_multipart(url, files)
            if isinstance(response, BlogError):
                return self._error_result(response)

            media_id = response.get("id")
            media_url = response.get("source_url", "")

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                message=f"Uploaded media: {filename}",
                artifacts=[
                    ExecutionArtifact(
                        artifact_type="wordpress_media",
                        content_type="application/json",
                        data=json.dumps({
                            "media_id": media_id,
                            "filename": filename,
                            "url": media_url
                        })
                    )
                ],
                external_effects={"media_id": media_id}
            )
        except Exception as e:
            error = BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Media upload failed: {e}",
                retryable=False
            )
            return self._error_result(error)

    def _set_featured_media(self, req: ConnectorRequest, payload: dict) -> ConnectorResult:
        """Set featured media for a post.

        Args:
            req: Connector request
            payload: {post_id: int, media_id: int}

        Returns:
            ConnectorResult
        """
        post_id = payload.get("post_id")
        media_id = payload.get("media_id")

        if not post_id or not media_id:
            error = BlogError(
                code=BlogErrorCode.ERR_VALIDATION,
                message="Missing required fields: post_id and media_id",
                retryable=False
            )
            return self._error_result(error)

        # Update post featured_media
        url = f"{self._config.base_url}/wp-json/wp/v2/posts/{post_id}"
        post_data = {"featured_media": media_id}

        try:
            response = self._http_post(url, post_data)
            if isinstance(response, BlogError):
                return self._error_result(response)

            return ConnectorResult(
                status=ConnectorStatus.SUCCESS,
                message=f"Set featured media {media_id} for post {post_id}",
                artifacts=[
                    ExecutionArtifact(
                        artifact_type="wordpress_featured_media",
                        content_type="application/json",
                        data=json.dumps({
                            "post_id": post_id,
                            "media_id": media_id
                        })
                    )
                ],
                external_effects={"post_id": post_id, "media_id": media_id}
            )
        except Exception as e:
            error = BlogError(
                code=BlogErrorCode.ERR_HTTP,
                message=f"Set featured media failed: {e}",
                retryable=False
            )
            return self._error_result(error)

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
                        message="WordPress API rate limit exceeded",
                        retryable=False  # Never retry 429
                    )

                # Handle auth errors
                if response.status_code in [401, 403]:
                    return BlogError(
                        code=BlogErrorCode.ERR_SECRET_UNAVAILABLE,
                        message=f"WordPress authentication failed: {response.status_code}",
                        retryable=False
                    )

                # Handle client errors
                if 400 <= response.status_code < 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"WordPress API client error: {response.status_code} {response.text[:200]}",
                        retryable=False
                    )

                # Handle server errors
                if response.status_code >= 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"WordPress API server error: {response.status_code}",
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

    def _http_post(self, url: str, data: Dict) -> Any | BlogError:
        """HTTP POST with retry policy.

        Args:
            url: Request URL
            data: Request body (JSON)

        Returns:
            Response JSON or BlogError
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._session.post(url, json=data, timeout=HTTP_TIMEOUT_SECONDS)

                # Handle rate limiting
                if response.status_code == 429:
                    return BlogError(
                        code=BlogErrorCode.ERR_RATE_LIMITED,
                        message="WordPress API rate limit exceeded",
                        retryable=False
                    )

                # Handle auth errors
                if response.status_code in [401, 403]:
                    return BlogError(
                        code=BlogErrorCode.ERR_SECRET_UNAVAILABLE,
                        message=f"WordPress authentication failed: {response.status_code}",
                        retryable=False
                    )

                # Handle client errors
                if 400 <= response.status_code < 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"WordPress API client error: {response.status_code} {response.text[:200]}",
                        retryable=False
                    )

                # Handle server errors
                if response.status_code >= 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"WordPress API server error: {response.status_code}",
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

    def _http_post_multipart(self, url: str, files: Dict) -> Any | BlogError:
        """HTTP POST multipart/form-data with retry policy.

        Args:
            url: Request URL
            files: Files dict for multipart upload

        Returns:
            Response JSON or BlogError
        """
        for attempt in range(MAX_RETRIES + 1):
            try:
                # Remove Content-Type header for multipart (requests sets it automatically)
                headers = dict(self._session.headers)
                headers.pop('Content-Type', None)

                response = self._session.post(
                    url,
                    files=files,
                    headers=headers,
                    timeout=HTTP_TIMEOUT_SECONDS
                )

                # Handle rate limiting
                if response.status_code == 429:
                    return BlogError(
                        code=BlogErrorCode.ERR_RATE_LIMITED,
                        message="WordPress API rate limit exceeded",
                        retryable=False
                    )

                # Handle auth errors
                if response.status_code in [401, 403]:
                    return BlogError(
                        code=BlogErrorCode.ERR_SECRET_UNAVAILABLE,
                        message=f"WordPress authentication failed: {response.status_code}",
                        retryable=False
                    )

                # Handle client errors
                if 400 <= response.status_code < 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"WordPress API client error: {response.status_code} {response.text[:200]}",
                        retryable=False
                    )

                # Handle server errors
                if response.status_code >= 500:
                    return BlogError(
                        code=BlogErrorCode.ERR_HTTP,
                        message=f"WordPress API server error: {response.status_code}",
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
