"""Blog Workflow Orchestrator.

Sequences: generate content (Claude) -> search image (Unsplash)
           -> upload image (WordPress) -> create draft (WordPress)
           -> set featured media (WordPress) -> notify (email).

All steps are deterministic. Same input always produces the same
idempotency keys, so re-running after a crash is safe.
"""

import json
import hashlib
import os
import uuid
import logging
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from connectors.wordpress import WordPressConnector
from connectors.unsplash import UnsplashConnector
from connectors.email_connector import EmailConnector
from connectors.base import ConnectorRequest, ConnectorContext, CoordinationProof
from connectors.secrets import SecretsProvider
from connectors.blog_utils import generate_slug, tokenize
from workflows.visual_intent import classify_tone_and_intent

logger = logging.getLogger(__name__)


@dataclass
class BlogWorkflowInput:
    """Minimal input needed to run the blog workflow."""
    title: str
    content: str          # HTML-safe body, max 40000 chars
    excerpt: str          # max 300 chars
    tags: list[str] = field(default_factory=list)   # hint tags, top 5 used
    keywords: list[str] = field(default_factory=list)  # for image search
    image_data_b64: Optional[str] = None  # Pre-generated image (base64), skips Unsplash
    image_mime_type: Optional[str] = None  # MIME type for pre-generated image


@dataclass
class BlogDraftResult:
    """Final result of the blog workflow."""
    success: bool
    post_id: Optional[str] = None
    slug: Optional[str] = None
    post_link: Optional[str] = None
    featured_media_id: Optional[str] = None
    featured_media_url: Optional[str] = None
    tone: Optional[str] = None  # Narrative tone (for bandit context)
    image_id: Optional[str] = None  # Unsplash image ID
    image_description: Optional[str] = None  # Image description for archetype tracking
    error_stage: Optional[str] = None
    error_message: Optional[str] = None


def _make_idempotency_key(run_id: str, stage: str, payload_fragment: str) -> str:
    spec = json.dumps({"run_id": run_id, "stage": stage, "frag": payload_fragment},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(spec.encode()).hexdigest()


def _make_req(run_id: str, action: str, payload: dict, idempotency_key: str,
              action_version: str = "1.0.0") -> ConnectorRequest:
    payload_canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload_hash = hashlib.sha256(payload_canonical.encode()).hexdigest()
    config_hash = hashlib.sha256(b"blog_workflow_v1").hexdigest()
    proof = CoordinationProof(
        coordination_id=f"workflow-{run_id}",
        lock_ids=[],
        approval_id=None,
        coordination_event_seq=0,
    )
    return ConnectorRequest(
        run_id=run_id,
        task_id=f"{action}-{run_id[:8]}",
        attempt=1,
        action=action,
        action_version=action_version,
        payload_canonical=payload_canonical,
        payload_hash=payload_hash,
        config_hash=config_hash,
        principal="blog_workflow",
        idempotency_key=idempotency_key,
        coordination_proof=proof,
    )


def _extract_keywords(content: str, title: str, hint_keywords: list[str]) -> list[str]:
    """Derive search keywords from content + title tokens."""
    if hint_keywords:
        return hint_keywords[:5]
    tokens = tokenize(title) + tokenize(content[:500])
    seen = set()
    result = []
    for t in tokens:
        if t not in seen and len(t) > 3:
            seen.add(t)
            result.append(t)
            if len(result) >= 5:
                break
    return result


def _send_notification(ctx: ConnectorContext, run_id: str,
                        result: BlogDraftResult, email_conn: EmailConnector) -> None:
    """Send completion email notification."""
    if result.success:
        subject = f"Blog Draft Ready: {result.slug}"
        body = (
            f"Your LLM Relay Bot has created a new WordPress draft.\n\n"
            f"Post ID:  {result.post_id}\n"
            f"Slug:     {result.slug}\n"
            f"Link:     {result.post_link}\n"
            f"Image ID: {result.featured_media_id}\n\n"
            f"Please review and publish when ready."
        )
    else:
        subject = f"Blog Workflow Failed at stage: {result.error_stage}"
        body = (
            f"The blog workflow failed.\n\n"
            f"Stage:   {result.error_stage}\n"
            f"Reason:  {result.error_message}\n"
        )

    idem = _make_idempotency_key(run_id, "email.notify", subject)
    req = _make_req(run_id, "email.notify", {"subject": subject, "body": body}, idem)
    try:
        email_conn.connect(ctx)
        email_conn.execute(req)
    except Exception as e:
        logger.warning("Email notification failed: %s", e)
    finally:
        email_conn.disconnect()


def run_blog_workflow(inp: BlogWorkflowInput) -> BlogDraftResult:
    """Run the complete blog creation workflow.

    Steps:
    1. Generate slug + extract keywords
    2. Search Unsplash for cover image
    3. Upload image to WordPress media library
    4. Create WordPress draft
    5. Set featured media on draft
    6. Send email notification
    """
    run_id = str(uuid.uuid4())
    secrets = SecretsProvider()
    ctx = ConnectorContext(
        task_id=f"blog-workflow-{run_id[:8]}",
        attempt=1,
        workspace_root="/tmp/blog_workflow",
        secrets_provider=secrets,
    )

    slug = generate_slug(inp.title)
    keywords = _extract_keywords(inp.content, inp.title, inp.keywords)
    primary_keyword = keywords[0] if keywords else inp.title
    tags = inp.tags[:5] if inp.tags else keywords[:3]
    logger.info(
        "Blog workflow started: run_id=%s slug=%s keywords=%s",
        run_id[:8],
        slug,
        keywords[:5] if keywords else [],
    )

    wp = WordPressConnector()
    unsplash = UnsplashConnector()
    email_conn = EmailConnector()

    image_data_b64: Optional[str] = None
    mime_type: Optional[str] = None
    media_id: Optional[str] = None
    media_url: Optional[str] = None
    image_id: Optional[str] = None
    image_description: Optional[str] = None
    tone: Optional[str] = None

    if inp.image_data_b64 and inp.image_mime_type:
        # ── Pre-generated image provided — skip Unsplash entirely ─────────
        image_data_b64 = inp.image_data_b64
        mime_type = inp.image_mime_type
        logger.info(
            "Using pre-generated image (%s, %d bytes base64) — skipping Unsplash",
            mime_type,
            len(image_data_b64),
        )
    else:
        # ── Visual Intent Layer: blog → tone → narrative-aligned search terms ───
        # Search for "how the blog feels" not just "what it's about"
        visual_intent = classify_tone_and_intent(
            title=inp.title,
            excerpt=inp.excerpt,
            content_preview=inp.content[:500],
        )
        visual_intent_keywords = visual_intent.visual_intent_keywords
        tone = visual_intent.tone
        logger.info(
            "Visual intent: tone=%s search_phrases=%s",
            tone,
            visual_intent_keywords[:3],
        )

        # ── Stage 1: Unsplash image search ───────────────────────────────────
        unsplash_idem = _make_idempotency_key(
            run_id, "unsplash.search_photos", ",".join(visual_intent_keywords[:2])
        )
        unsplash_payload = {
            "title_tokens": tokenize(inp.title),
            "keywords": keywords,
            "visual_intent_keywords": visual_intent_keywords,
            "per_page": 10,
        }
        unsplash_req = _make_req(run_id, "unsplash.search_photos", unsplash_payload, unsplash_idem)

        try:
            unsplash.connect(ctx)
            unsplash_result = unsplash.execute(unsplash_req)
            unsplash.disconnect()

            if unsplash_result.status.value == "success":
                meta = unsplash_result.output_metadata or {}
                image_data_b64 = meta.get("image_data_b64")
                mime_type = meta.get("mime_type")
                image_id = unsplash_result.external_transaction_id
                image_description = meta.get("alt_description") or meta.get("description", "")
                if image_data_b64 and mime_type:
                    logger.info(
                        "Unsplash: found image (id=%s), %d bytes base64",
                        image_id,
                        len(image_data_b64),
                    )
                else:
                    logger.warning(
                        "Unsplash succeeded but output_metadata missing image_data_b64/mime_type — continuing without image"
                    )
            else:
                logger.warning("Unsplash search failed: %s", unsplash_result.error_message)
        except Exception as e:
            logger.warning("Unsplash stage failed: %s — continuing without image", e)

    # ── Stage 2: Upload image to WordPress ───────────────────────────────────
    if image_data_b64 and mime_type:
        ext = "jpg" if "jpeg" in mime_type else mime_type.split("/")[-1]
        filename = f"{slug}-cover.{ext}"
        logger.info("Uploading image to WordPress: %s (%s)", filename, mime_type)
        upload_idem = _make_idempotency_key(run_id, "wp.media.upload", slug)
        upload_payload = {
            "image_data": image_data_b64,
            "mime_type": mime_type,
            "filename": filename,
        }
        upload_req = _make_req(run_id, "wp.media.upload", upload_payload, upload_idem)
        try:
            wp.connect(ctx)
            upload_result = wp.execute(upload_req)
            wp.disconnect()
            if upload_result.status.value == "success":
                media_id = upload_result.external_transaction_id
                meta = upload_result.output_metadata or {}
                media_url = meta.get("media_url", "")
                logger.info("Media uploaded: id=%s url=%s", media_id, media_url or "(none)")
            else:
                logger.warning("Media upload failed: %s", upload_result.error_message)
        except Exception as e:
            logger.warning("Media upload stage failed: %s", e)
    else:
        logger.info("Skipping image upload (no image data from Unsplash)")

    # ── Stage 3: Create WordPress draft ──────────────────────────────────────
    draft_idem = _make_idempotency_key(run_id, "wp.post.create_draft", slug)
    draft_payload = {
        "title": inp.title,
        "content": inp.content,
        "excerpt": inp.excerpt[:300],
        "slug": slug,
        "tags": tags,
        "status": "draft",
    }
    draft_req = _make_req(run_id, "wp.post.create_draft", draft_payload, draft_idem)

    post_id: Optional[str] = None
    post_link: Optional[str] = None
    try:
        logger.info("Creating WordPress draft: title=%r slug=%s", inp.title[:50], slug)
        wp.connect(ctx)
        draft_result = wp.execute(draft_req)
        wp.disconnect()
        if draft_result.status.value == "success":
            post_id = draft_result.external_transaction_id
            meta = draft_result.output_metadata or {}
            post_link = meta.get("post_link", "")
            logger.info("Draft created: post_id=%s link=%s", post_id, post_link or "(none)")
        else:
            result = BlogDraftResult(
                success=False,
                error_stage="wp.post.create_draft",
                error_message=draft_result.error_message,
            )
            _send_notification(ctx, run_id, result, email_conn)
            return result
    except Exception as e:
        result = BlogDraftResult(
            success=False,
            error_stage="wp.post.create_draft",
            error_message=str(e),
        )
        _send_notification(ctx, run_id, result, email_conn)
        return result

    # ── Stage 4: Set featured media ──────────────────────────────────────────
    if post_id and media_id:
        logger.info("Setting featured media: post_id=%s media_id=%s", post_id, media_id)
        feat_idem = _make_idempotency_key(run_id, "wp.post.set_featured_media",
                                          f"{post_id}:{media_id}")
        feat_payload = {"post_id": int(post_id), "media_id": int(media_id)}
        feat_req = _make_req(run_id, "wp.post.set_featured_media", feat_payload, feat_idem)
        try:
            wp.connect(ctx)
            feat_result = wp.execute(feat_req)
            wp.disconnect()
            if feat_result.status.value == "success":
                logger.info("Featured media set successfully")
            else:
                logger.warning("Set featured media failed: %s — draft still created",
                               feat_result.error_message)
        except Exception as e:
            logger.warning("Set featured media stage failed: %s — draft still created", e)

    result = BlogDraftResult(
        success=True,
        post_id=post_id,
        slug=slug,
        post_link=post_link,
        featured_media_id=media_id,
        featured_media_url=media_url,
        tone=tone,
        image_id=image_id,
        image_description=image_description,
    )
    logger.info(
        "Blog workflow complete: success=True post_id=%s slug=%s featured_media_id=%s",
        post_id,
        slug,
        media_id,
    )
    _send_notification(ctx, run_id, result, email_conn)
    return result
