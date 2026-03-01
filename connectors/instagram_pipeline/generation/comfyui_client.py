"""ComfyUI workflow execution via HTTP API.

Supports both local Mac (http://127.0.0.1:8188) and cloud RunPod instances.
ComfyUI exposes a REST API for submitting workflow JSON, polling for
completion, uploading input images, and downloading generated outputs.

API endpoints used:
  POST /prompt          — submit workflow JSON, returns prompt_id
  GET  /history/{id}    — poll for completion status + output filenames
  POST /upload/image    — upload reference/pose images to ComfyUI input/
  GET  /view            — download generated output images by filename
  GET  /system_stats    — health check (server reachable + GPU info)
  GET  /object_info     — list installed nodes (validates required custom nodes)
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ComfyUIError(Exception):
    """ComfyUI execution error."""
    pass


class ComfyUIClient:
    """Async HTTP client for ComfyUI's native API.

    Pattern follows SiliconFlowClient: httpx.AsyncClient + polling loop.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8188",
        client_id: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id or str(uuid.uuid4())

    async def health_check(self) -> bool:
        """Check if ComfyUI server is reachable."""
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/system_stats",
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                # system_stats returns {"system": {...}, "devices": [...]}
                return "system" in data
        except Exception as e:
            logger.warning("ComfyUI health check failed: %s", e)
            return False

    async def check_required_nodes(self, node_types: list[str]) -> dict[str, bool]:
        """Validate that required custom nodes are installed.

        Args:
            node_types: List of class_type values to check
                e.g. ["ApplyPulidFlux", "PulidFluxModelLoader", "ControlNetLoader"]

        Returns:
            Dict mapping each node_type to whether it's available.
        """
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/object_info",
                timeout=30.0,
            )
            resp.raise_for_status()
            available_nodes = resp.json()

        return {node: node in available_nodes for node in node_types}

    async def upload_image(
        self,
        local_path: str,
        subfolder: str = "",
        overwrite: bool = True,
    ) -> str:
        """Upload image to ComfyUI's input/ directory.

        Args:
            local_path: Path to local image file
            subfolder: Subfolder within input/ (e.g. "references")
            overwrite: Whether to overwrite existing file with same name

        Returns:
            Filename as stored in ComfyUI (use this in workflow JSON LoadImage nodes)
        """
        import httpx

        path = Path(local_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {local_path}")

        async with httpx.AsyncClient() as client:
            with open(path, "rb") as f:
                files = {"image": (path.name, f, "image/png")}
                data = {"overwrite": str(overwrite).lower()}
                if subfolder:
                    data["subfolder"] = subfolder

                resp = await client.post(
                    f"{self.base_url}/upload/image",
                    files=files,
                    data=data,
                    timeout=60.0,
                )
                resp.raise_for_status()
                result = resp.json()

        # Response: {"name": "hero_front.png", "subfolder": "references", "type": "input"}
        filename = result.get("name", path.name)
        sub = result.get("subfolder", subfolder)
        full_ref = f"{sub}/{filename}" if sub else filename

        logger.info("Uploaded %s to ComfyUI input/%s", path.name, full_ref)
        return full_ref

    async def submit_workflow(self, workflow_json: dict) -> str:
        """Submit workflow JSON to ComfyUI for execution.

        Args:
            workflow_json: Complete ComfyUI API-format workflow dict
                (node IDs as keys, node definitions as values)

        Returns:
            prompt_id for polling via poll_until_complete()
        """
        import httpx

        payload = {
            "prompt": workflow_json,
            "client_id": self.client_id,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/prompt",
                json=payload,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"No prompt_id in response: {data}")

        logger.info("Submitted workflow, prompt_id=%s", prompt_id)
        return prompt_id

    async def poll_until_complete(
        self,
        prompt_id: str,
        timeout: int = 600,
        interval: int = 3,
    ) -> dict:
        """Poll /history/{prompt_id} until execution completes or times out.

        Args:
            prompt_id: From submit_workflow()
            timeout: Max seconds to wait
            interval: Seconds between polls

        Returns:
            History entry dict with output image filenames

        Raises:
            ComfyUIError: If execution fails
            TimeoutError: If timeout exceeded
        """
        import httpx

        elapsed = 0
        async with httpx.AsyncClient() as client:
            while elapsed < timeout:
                resp = await client.get(
                    f"{self.base_url}/history/{prompt_id}",
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()

                if prompt_id in data:
                    entry = data[prompt_id]
                    status = entry.get("status", {})

                    if status.get("completed", False):
                        return entry

                    status_str = status.get("status_str", "")
                    if status_str == "error":
                        messages = entry.get("status", {}).get("messages", [])
                        raise ComfyUIError(
                            f"Workflow execution failed: {messages}"
                        )

                logger.debug(
                    "Waiting for prompt %s... (%ds/%ds)",
                    prompt_id[:8], elapsed, timeout,
                )
                await asyncio.sleep(interval)
                elapsed += interval

        raise TimeoutError(
            f"ComfyUI workflow timed out after {timeout}s for prompt {prompt_id}"
        )

    async def download_output(
        self,
        filename: str,
        subfolder: str = "",
        output_dir: str = "output/",
    ) -> str:
        """Download a generated image from ComfyUI's output directory.

        Args:
            filename: Output image filename from history entry
            subfolder: Subfolder within output/
            output_dir: Local directory to save to

        Returns:
            Local file path to downloaded image
        """
        import httpx

        params = {
            "filename": filename,
            "type": "output",
        }
        if subfolder:
            params["subfolder"] = subfolder

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        local_path = str(Path(output_dir) / filename)

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/view",
                params=params,
                timeout=60.0,
            )
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(resp.content)

        logger.info(
            "Downloaded output %s to %s (%d bytes)",
            filename, local_path, len(resp.content),
        )
        return local_path

    async def execute_workflow(
        self,
        workflow_json: dict,
        output_dir: str = "output/",
        timeout: int = 600,
    ) -> list[str]:
        """Full pipeline: submit workflow, poll for completion, download all outputs.

        Args:
            workflow_json: Complete ComfyUI API-format workflow
            output_dir: Local directory to save generated images
            timeout: Max seconds to wait for completion

        Returns:
            List of local file paths to generated images
        """
        prompt_id = await self.submit_workflow(workflow_json)
        history = await self.poll_until_complete(prompt_id, timeout=timeout)

        # Extract output image filenames from history
        outputs = history.get("outputs", {})
        image_paths = []

        for node_id, node_output in outputs.items():
            images = node_output.get("images", [])
            for img in images:
                filename = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                if filename:
                    local_path = await self.download_output(
                        filename, subfolder, output_dir,
                    )
                    image_paths.append(local_path)

        if not image_paths:
            raise ComfyUIError(
                f"No output images found in workflow result. "
                f"Output keys: {list(outputs.keys())}"
            )

        return image_paths
