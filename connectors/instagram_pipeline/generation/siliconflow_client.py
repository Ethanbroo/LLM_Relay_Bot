"""Wan2.2 video generation via SiliconFlow API.

SiliconFlow hosts Wan2.2 models with an OpenAI-compatible API.
Flat rate ~$0.29/video regardless of duration.
"""

import asyncio
import logging
import os
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class SiliconFlowClient:
    """Wan2.2 video generation via SiliconFlow API."""

    BASE_URL = "https://api.siliconflow.cn/v1/video/submit"
    STATUS_URL = "https://api.siliconflow.cn/v1/video/status"

    MODELS = {
        "t2v": "Wan-AI/Wan2.2-T2V-A14B",
        "i2v": "Wan-AI/Wan2.2-I2V-A14B",
    }

    def __init__(self):
        self.api_key = self._get_api_key()

    def _get_api_key(self) -> str:
        key = os.environ.get("SILICONFLOW_API_KEY", "")
        if key:
            return key
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "siliconflow-api-key", "-w"],
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    async def generate_t2v(
        self, prompt: str, size: str = "720x1280", seed: Optional[int] = None,
    ) -> str:
        """Text-to-video generation. Returns video URL."""
        import httpx

        payload = {
            "model": self.MODELS["t2v"],
            "prompt": prompt,
            "image_size": size,
        }
        if seed is not None:
            payload["seed"] = seed

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        request_id = data.get("requestId")
        if not request_id:
            from .video_generator import GenerationError
            raise GenerationError(f"No requestId in SiliconFlow response: {data}")

        result = await self._poll_until_complete(request_id)

        videos = result.get("results", {}).get("videos", [])
        if not videos or not videos[0].get("url"):
            from .video_generator import GenerationError
            raise GenerationError(f"No video URL in SiliconFlow result: {result}")

        return videos[0]["url"]

    async def generate_i2v(
        self, prompt: str, image_url: str, size: str = "720x1280",
        seed: Optional[int] = None,
    ) -> str:
        """Image-to-video generation. Returns video URL."""
        import httpx

        payload = {
            "model": self.MODELS["i2v"],
            "prompt": prompt,
            "image_url": image_url,
            "image_size": size,
        }
        if seed is not None:
            payload["seed"] = seed

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        request_id = data.get("requestId")
        if not request_id:
            from .video_generator import GenerationError
            raise GenerationError(f"No requestId in SiliconFlow response: {data}")

        result = await self._poll_until_complete(request_id)

        videos = result.get("results", {}).get("videos", [])
        if not videos or not videos[0].get("url"):
            from .video_generator import GenerationError
            raise GenerationError(f"No video URL in SiliconFlow result: {result}")

        return videos[0]["url"]

    async def _poll_until_complete(
        self, request_id: str, timeout: int = 300, interval: int = 5,
    ) -> dict:
        """Poll status endpoint until job completes or times out."""
        import httpx

        elapsed = 0
        async with httpx.AsyncClient() as client:
            while elapsed < timeout:
                resp = await client.post(
                    self.STATUS_URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"requestId": request_id},
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()

                status = data.get("status", "")
                if status == "Succeed":
                    return data
                elif status in ("Failed", "Error"):
                    from .video_generator import GenerationError
                    raise GenerationError(
                        f"SiliconFlow generation failed: {data.get('reason', status)}"
                    )

                logger.debug("SiliconFlow job %s: %s (%ds)", request_id, status, elapsed)
                await asyncio.sleep(interval)
                elapsed += interval

        from .video_generator import GenerationError
        raise GenerationError(
            f"SiliconFlow generation timed out after {timeout}s for request {request_id}"
        )

    async def download_video(self, url: str, output_path: str) -> str:
        """Download video from URL to local file."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=120.0)
            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)

        logger.info("Downloaded SiliconFlow video to %s (%d bytes)", output_path, os.path.getsize(output_path))
        return output_path
