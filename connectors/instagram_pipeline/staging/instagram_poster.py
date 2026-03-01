"""Instagram publisher using Meta Graph API.

Publishes content to Instagram using the official Meta Graph API.
Supports single images, carousels, and reels.
"""

import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class InstagramPoster:
    """
    Publishes content to Instagram via Meta Graph API.

    Requires:
    - Instagram Business Account
    - Facebook Page linked to Instagram account
    - Instagram Basic Display API or Instagram Graph API access
    - Long-lived access token

    Supported formats:
    - Single image posts
    - Carousel posts (2-10 images)
    - Reels (video)
    """

    def __init__(
        self,
        access_token: str,
        instagram_business_account_id: str,
    ):
        """
        Initialize Instagram poster.

        Args:
            access_token: Meta Graph API access token (long-lived)
            instagram_business_account_id: Instagram Business Account ID

        How to get these:
        1. Access Token: https://developers.facebook.com/docs/instagram-api/getting-started
        2. Business Account ID: Graph API Explorer → me/accounts → instagram_business_account
        """
        self.access_token = access_token
        self.account_id = instagram_business_account_id
        self.api_version = "v18.0"  # Update as Meta releases new versions
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

    def publish_single_image(
        self,
        image_url: str,
        caption: str,
        location_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Publish a single image post.

        Args:
            image_url: Publicly accessible URL to image (HTTPS required)
            caption: Post caption (max 2,200 characters)
            location_id: Optional location ID for geotagging

        Returns:
            Dict with:
                - media_id: Instagram media ID
                - permalink: Public URL to the post

        Raises:
            RuntimeError: If publishing fails
        """
        import httpx

        logger.info("Publishing single image to Instagram")

        # Step 1: Create media container
        create_url = f"{self.base_url}/{self.account_id}/media"
        create_params = {
            "image_url": image_url,
            "caption": caption,
            "access_token": self.access_token,
        }

        if location_id:
            create_params["location_id"] = location_id

        logger.debug("Creating media container")
        response = httpx.post(create_url, params=create_params, timeout=30.0)
        response.raise_for_status()
        creation_data = response.json()

        container_id = creation_data.get("id")
        if not container_id:
            raise RuntimeError(f"Failed to create media container: {creation_data}")

        logger.debug("Media container created: %s", container_id)

        # Step 2: Publish media container
        publish_url = f"{self.base_url}/{self.account_id}/media_publish"
        publish_params = {
            "creation_id": container_id,
            "access_token": self.access_token,
        }

        # Wait a moment for media to be processed
        time.sleep(2)

        logger.debug("Publishing media container")
        response = httpx.post(publish_url, params=publish_params, timeout=30.0)
        response.raise_for_status()
        publish_data = response.json()

        media_id = publish_data.get("id")
        if not media_id:
            raise RuntimeError(f"Failed to publish media: {publish_data}")

        # Step 3: Get permalink
        permalink = self._get_permalink(media_id)

        logger.info(
            "Single image published successfully: %s",
            permalink
        )

        return {
            "media_id": media_id,
            "permalink": permalink,
        }

    def publish_carousel(
        self,
        image_urls: list[str],
        caption: str,
        location_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Publish a carousel post (2-10 images).

        Args:
            image_urls: List of publicly accessible image URLs (2-10 images)
            caption: Post caption (max 2,200 characters)
            location_id: Optional location ID for geotagging

        Returns:
            Dict with:
                - media_id: Instagram media ID
                - permalink: Public URL to the post

        Raises:
            ValueError: If image count is invalid
            RuntimeError: If publishing fails
        """
        import httpx

        if len(image_urls) < 2 or len(image_urls) > 10:
            raise ValueError(
                f"Carousel must have 2-10 images, got {len(image_urls)}"
            )

        logger.info("Publishing carousel with %d images to Instagram", len(image_urls))

        # Step 1: Create media containers for each image
        children_ids = []

        for i, image_url in enumerate(image_urls, 1):
            logger.debug("Creating container for image %d/%d", i, len(image_urls))

            create_url = f"{self.base_url}/{self.account_id}/media"
            create_params = {
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": self.access_token,
            }

            response = httpx.post(create_url, params=create_params, timeout=30.0)
            response.raise_for_status()
            data = response.json()

            child_id = data.get("id")
            if not child_id:
                raise RuntimeError(f"Failed to create carousel item {i}: {data}")

            children_ids.append(child_id)
            time.sleep(0.5)  # Rate limiting

        # Step 2: Create carousel container
        logger.debug("Creating carousel container with %d children", len(children_ids))

        carousel_url = f"{self.base_url}/{self.account_id}/media"
        carousel_params = {
            "media_type": "CAROUSEL",
            "children": ",".join(children_ids),
            "caption": caption,
            "access_token": self.access_token,
        }

        if location_id:
            carousel_params["location_id"] = location_id

        response = httpx.post(carousel_url, params=carousel_params, timeout=30.0)
        response.raise_for_status()
        carousel_data = response.json()

        carousel_id = carousel_data.get("id")
        if not carousel_id:
            raise RuntimeError(f"Failed to create carousel container: {carousel_data}")

        # Step 3: Publish carousel
        publish_url = f"{self.base_url}/{self.account_id}/media_publish"
        publish_params = {
            "creation_id": carousel_id,
            "access_token": self.access_token,
        }

        # Wait for carousel to be fully processed
        time.sleep(3)

        logger.debug("Publishing carousel")
        response = httpx.post(publish_url, params=publish_params, timeout=30.0)
        response.raise_for_status()
        publish_data = response.json()

        media_id = publish_data.get("id")
        if not media_id:
            raise RuntimeError(f"Failed to publish carousel: {publish_data}")

        # Step 4: Get permalink
        permalink = self._get_permalink(media_id)

        logger.info(
            "Carousel published successfully: %s (%d images)",
            permalink,
            len(image_urls)
        )

        return {
            "media_id": media_id,
            "permalink": permalink,
        }

    def publish_reel(
        self,
        video_url: str,
        caption: str,
        cover_url: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Publish a reel (video).

        Args:
            video_url: Publicly accessible URL to video (HTTPS, MP4)
            caption: Post caption (max 2,200 characters)
            cover_url: Optional custom cover image URL
            location_id: Optional location ID for geotagging

        Returns:
            Dict with:
                - media_id: Instagram media ID
                - permalink: Public URL to the post

        Raises:
            RuntimeError: If publishing fails
        """
        import httpx

        logger.info("Publishing reel to Instagram")

        # Create reel container
        create_url = f"{self.base_url}/{self.account_id}/media"
        create_params = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",  # Also show in feed
            "access_token": self.access_token,
        }

        if cover_url:
            create_params["cover_url"] = cover_url

        if location_id:
            create_params["location_id"] = location_id

        logger.debug("Creating reel container")
        response = httpx.post(create_url, params=create_params, timeout=60.0)
        response.raise_for_status()
        creation_data = response.json()

        container_id = creation_data.get("id")
        if not container_id:
            raise RuntimeError(f"Failed to create reel container: {creation_data}")

        # Wait for video processing (can take 10-30 seconds)
        logger.debug("Waiting for video processing...")
        time.sleep(15)

        # Publish reel
        publish_url = f"{self.base_url}/{self.account_id}/media_publish"
        publish_params = {
            "creation_id": container_id,
            "access_token": self.access_token,
        }

        logger.debug("Publishing reel")
        response = httpx.post(publish_url, params=publish_params, timeout=60.0)
        response.raise_for_status()
        publish_data = response.json()

        media_id = publish_data.get("id")
        if not media_id:
            raise RuntimeError(f"Failed to publish reel: {publish_data}")

        # Get permalink
        permalink = self._get_permalink(media_id)

        logger.info("Reel published successfully: %s", permalink)

        return {
            "media_id": media_id,
            "permalink": permalink,
        }

    def _get_permalink(self, media_id: str) -> str:
        """
        Get permalink for published media.

        Args:
            media_id: Instagram media ID

        Returns:
            Public URL to the post
        """
        import httpx

        url = f"{self.base_url}/{media_id}"
        params = {
            "fields": "permalink",
            "access_token": self.access_token,
        }

        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        return data.get("permalink", f"https://www.instagram.com/p/{media_id}/")

    def get_account_info(self) -> Dict[str, Any]:
        """
        Get Instagram Business Account information.

        Useful for verifying connection and getting account details.

        Returns:
            Dict with account info (username, id, profile_picture_url, etc.)
        """
        import httpx

        url = f"{self.base_url}/{self.account_id}"
        params = {
            "fields": "id,username,name,profile_picture_url,followers_count,follows_count,media_count",
            "access_token": self.access_token,
        }

        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()

        return response.json()

    def verify_connection(self) -> bool:
        """
        Verify Instagram API connection is working.

        Returns:
            True if connection is valid, False otherwise
        """
        try:
            info = self.get_account_info()
            logger.info(
                "Instagram connection verified: @%s (%d followers)",
                info.get("username", "unknown"),
                info.get("followers_count", 0)
            )
            return True
        except Exception as e:
            logger.error("Instagram connection failed: %s", e)
            return False
