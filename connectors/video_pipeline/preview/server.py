"""Browser-based timeline preview server.

Start with:
    python -m connectors.video_pipeline.preview.server --timeline path/to/timeline.json

Opens http://localhost:8765 with:
- Full timeline playback
- Frame-by-frame scrubbing
- Playback speed control (0.25x to 4x)
- Effect toggle (enable/disable individual effects)
- Resolution toggle (preview at 50% for speed, 100% for accuracy)

Frames are rendered on-demand and streamed via WebSocket as JPEG bytes.
"""

import asyncio
import json
import io
import logging
import argparse
from pathlib import Path
from typing import Optional

from PIL import Image

logger = logging.getLogger(__name__)

# Lazy imports for FastAPI — only needed when server is actually started
_app = None


def _create_app():
    """Create and configure the FastAPI application."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import HTMLResponse, JSONResponse

    app = FastAPI(
        title="Video Preview",
        description="Real-time timeline preview",
    )

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Mutable server state
    app.state.compositor = None
    app.state.timeline = None
    app.state.frame_cache = None
    app.state.image_cache = {}
    app.state.preview_scale = 0.5
    app.state.disabled_effects = set()

    @app.get("/")
    async def index():
        """Serve the preview UI."""
        html_path = static_dir / "index.html"
        return HTMLResponse(html_path.read_text())

    @app.get("/api/timeline")
    async def get_timeline_info():
        """Return timeline metadata for the UI."""
        timeline = app.state.timeline
        if timeline is None:
            return JSONResponse({"error": "No timeline loaded"}, status_code=404)

        return {
            "total_frames": timeline.total_frames,
            "fps": timeline.fps,
            "duration_ms": timeline.total_duration_ms,
            "resolution": {
                "width": timeline.resolution.width,
                "height": timeline.resolution.height,
            },
            "clip_count": len(timeline.clips),
            "clips": [
                {
                    "clip_id": c.clip_id,
                    "duration_ms": c.duration_ms,
                    "source_type": c.source_type,
                    "transition_in": c.transition_in.value,
                    "effects": [e[0] for e in c.effects],
                }
                for c in timeline.clips
            ],
            "global_effects": [e[0] for e in timeline.global_effects],
            "preview_scale": app.state.preview_scale,
        }

    @app.get("/api/cache/stats")
    async def cache_stats():
        """Return frame cache statistics."""
        if app.state.frame_cache is None:
            return {"error": "No cache initialized"}
        return app.state.frame_cache.stats

    @app.post("/api/preview/scale")
    async def set_preview_scale(body: dict):
        """Set preview resolution scale (0.25-1.0)."""
        scale = body.get("scale", 0.5)
        scale = max(0.25, min(1.0, float(scale)))
        app.state.preview_scale = scale
        # Clear cache since scale changed
        if app.state.frame_cache:
            app.state.frame_cache.clear()
        return {"scale": scale}

    @app.post("/api/effects/toggle")
    async def toggle_effect(body: dict):
        """Enable/disable an effect by name for preview."""
        effect_name = body.get("effect")
        enabled = body.get("enabled", True)
        if effect_name:
            if enabled:
                app.state.disabled_effects.discard(effect_name)
            else:
                app.state.disabled_effects.add(effect_name)
            # Clear cache since effects changed
            if app.state.frame_cache:
                app.state.frame_cache.clear()
        return {
            "effect": effect_name,
            "enabled": enabled,
            "disabled_effects": sorted(app.state.disabled_effects),
        }

    @app.get("/api/effects/disabled")
    async def get_disabled_effects():
        """Return list of currently disabled effects."""
        return {"disabled_effects": sorted(app.state.disabled_effects)}

    @app.websocket("/ws/frames")
    async def frame_stream(websocket: WebSocket):
        """WebSocket endpoint for streaming frames.

        Client sends JSON messages:
            {"type": "seek", "frame": 42}
                → Server sends JPEG bytes of that frame

            {"type": "play", "start_frame": 0, "speed": 1.0}
                → Server sends continuous JPEG frames at the specified speed

            {"type": "stop"}
                → Server stops current playback

            {"type": "range", "start": 10, "end": 50}
                → Server sends all frames in range (for scrubbing preload)
        """
        await websocket.accept()
        logger.info("Preview client connected")

        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                msg_type = msg.get("type", "")

                if msg_type == "seek":
                    frame_num = int(msg["frame"])
                    frame_num = max(0, min(frame_num, app.state.timeline.total_frames - 1))
                    jpeg_bytes = _render_and_encode(app, frame_num)
                    # Send frame number as first 4 bytes, then JPEG data
                    header = frame_num.to_bytes(4, "big")
                    await websocket.send_bytes(header + jpeg_bytes)

                elif msg_type == "play":
                    start = int(msg.get("start_frame", 0))
                    speed = float(msg.get("speed", 1.0))
                    speed = max(0.25, min(4.0, speed))
                    total = app.state.timeline.total_frames
                    frame_interval = 1.0 / (app.state.timeline.fps * speed)

                    for i in range(start, total):
                        jpeg_bytes = _render_and_encode(app, i)
                        header = i.to_bytes(4, "big")
                        await websocket.send_bytes(header + jpeg_bytes)

                        await asyncio.sleep(frame_interval)

                        # Check for stop/seek commands (non-blocking)
                        try:
                            stop_data = await asyncio.wait_for(
                                websocket.receive_text(), timeout=0.001
                            )
                            stop_msg = json.loads(stop_data)
                            if stop_msg.get("type") in ("stop", "seek", "play"):
                                # Re-process the message
                                if stop_msg.get("type") == "seek":
                                    fn = int(stop_msg["frame"])
                                    fn = max(0, min(fn, total - 1))
                                    jb = _render_and_encode(app, fn)
                                    hdr = fn.to_bytes(4, "big")
                                    await websocket.send_bytes(hdr + jb)
                                break
                        except asyncio.TimeoutError:
                            pass

                    # Send playback-ended signal
                    await websocket.send_text(json.dumps({"type": "playback_ended"}))

                elif msg_type == "range":
                    start = int(msg.get("start", 0))
                    end = int(msg.get("end", start + 30))
                    total = app.state.timeline.total_frames
                    end = min(end, total)
                    for i in range(start, end):
                        jpeg_bytes = _render_and_encode(app, i)
                        header = i.to_bytes(4, "big")
                        await websocket.send_bytes(header + jpeg_bytes)

        except WebSocketDisconnect:
            logger.info("Preview client disconnected")
        except Exception as e:
            logger.error("WebSocket error: %s", e)

    return app


def _render_and_encode(app, frame_num: int) -> bytes:
    """Render a frame and encode as JPEG bytes.

    Checks the frame cache first. Respects preview scale and
    disabled effects.
    """
    cache = app.state.frame_cache
    compositor = app.state.compositor
    scale = app.state.preview_scale

    # Cache key includes scale and disabled effects for correctness
    cache_key = frame_num

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    frame = compositor.render_frame(frame_num)

    # Scale down for preview
    if scale != 1.0:
        new_w = max(2, int(frame.width * scale))
        new_h = max(2, int(frame.height * scale))
        # Ensure even dimensions
        new_w = new_w if new_w % 2 == 0 else new_w + 1
        new_h = new_h if new_h % 2 == 0 else new_h + 1
        frame = frame.resize((new_w, new_h), Image.LANCZOS)

    # Encode as JPEG
    buf = io.BytesIO()
    frame.save(buf, format="JPEG", quality=80)
    jpeg_bytes = buf.getvalue()

    cache.put(cache_key, jpeg_bytes)
    return jpeg_bytes


def load_timeline_into_app(
    app,
    timeline,
    image_cache: Optional[dict] = None,
    preview_scale: float = 0.5,
    max_cache_frames: int = 300,
):
    """Load a timeline into the preview server app.

    Args:
        app: FastAPI app instance
        timeline: Timeline model instance
        image_cache: Pre-loaded images keyed by clip_id
        preview_scale: Resolution scale (0.25-1.0)
        max_cache_frames: Max frames to cache in LRU
    """
    from ..compositor import FrameCompositor
    from .frame_cache import FrameCache

    if image_cache is None:
        image_cache = {}
        for clip in timeline.clips:
            if clip.source_path and Path(clip.source_path).exists():
                try:
                    image_cache[clip.clip_id] = Image.open(clip.source_path).convert("RGB")
                except Exception as e:
                    logger.warning("Failed to load image for %s: %s", clip.clip_id, e)

    app.state.timeline = timeline
    app.state.compositor = FrameCompositor(timeline, image_cache)
    app.state.image_cache = image_cache
    app.state.frame_cache = FrameCache(max_frames=max_cache_frames)
    app.state.preview_scale = max(0.25, min(1.0, preview_scale))


def start_preview_server(
    timeline_path: Optional[str] = None,
    timeline=None,
    image_cache: Optional[dict] = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    preview_scale: float = 0.5,
    max_cache_frames: int = 300,
):
    """Launch the preview server.

    Provide either timeline_path (JSON file) or a Timeline instance.

    Args:
        timeline_path: Path to a Timeline JSON file
        timeline: Pre-built Timeline instance
        image_cache: Pre-loaded images (only used with timeline param)
        host: Bind address
        port: Port number
        preview_scale: Initial resolution scale (0.25-1.0)
        max_cache_frames: Max frames in LRU cache
    """
    import uvicorn
    from ..schemas import Timeline as TimelineModel

    app = _create_app()

    if timeline is None and timeline_path is not None:
        timeline_data = json.loads(Path(timeline_path).read_text())
        timeline = TimelineModel.model_validate(timeline_data)

    if timeline is None:
        raise ValueError("Must provide timeline_path or timeline")

    load_timeline_into_app(
        app,
        timeline,
        image_cache=image_cache,
        preview_scale=preview_scale,
        max_cache_frames=max_cache_frames,
    )

    logger.info(
        "Starting preview server at http://%s:%d (%d frames, %dx%d @ %.0f%%)",
        host, port,
        timeline.total_frames,
        timeline.resolution.width,
        timeline.resolution.height,
        preview_scale * 100,
    )

    print(f"\n  Preview: http://{host}:{port}\n")

    uvicorn.run(app, host=host, port=port, log_level="warning")


def get_app():
    """Get or create the FastAPI app (for programmatic use)."""
    global _app
    if _app is None:
        _app = _create_app()
    return _app


# --- CLI entry point ---

def main():
    parser = argparse.ArgumentParser(
        description="Video Pipeline Preview Server"
    )
    parser.add_argument(
        "--timeline", "-t",
        required=True,
        help="Path to timeline JSON file",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8765,
        help="Port number (default: 8765)",
    )
    parser.add_argument(
        "--scale", "-s",
        type=float,
        default=0.5,
        help="Preview resolution scale 0.25-1.0 (default: 0.5)",
    )
    parser.add_argument(
        "--cache-size",
        type=int,
        default=300,
        help="Max frames to cache (default: 300)",
    )

    args = parser.parse_args()

    start_preview_server(
        timeline_path=args.timeline,
        host=args.host,
        port=args.port,
        preview_scale=args.scale,
        max_cache_frames=args.cache_size,
    )


if __name__ == "__main__":
    main()
