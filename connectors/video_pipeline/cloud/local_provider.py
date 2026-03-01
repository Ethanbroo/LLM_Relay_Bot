"""Local render providers: single-process and multiprocessing.

LocalProvider: Renders all frames in a single process (existing behavior,
wrapped in the provider interface for uniformity).

LocalMultiprocessProvider: Splits the timeline into chunks and renders them
in parallel using Python's multiprocessing.Pool. Each worker process gets
a copy of the timeline and image cache, renders its chunk independently,
and writes a video segment. The orchestrator concatenates the segments.
"""

import os
import time
import uuid
import shutil
import logging
import subprocess
import multiprocessing
from pathlib import Path
from typing import Optional
from PIL import Image

from .base_provider import AbstractRenderProvider
from .models import RenderChunk, RenderJob, ChunkStatus
from ..schemas import Timeline
from ..compositor import FrameCompositor
from ..encoder import check_ffmpeg

logger = logging.getLogger(__name__)


def _render_chunk_worker(args: dict) -> dict:
    """Worker function for multiprocessing.

    This runs in a separate process. It receives serialized arguments
    (because multiprocessing needs picklable data), reconstructs the
    compositor, renders the frame range, and pipes to FFmpeg.

    Args:
        args: Dict with keys:
            - chunk_id, start_frame, end_frame
            - timeline_dict: Timeline as dict (re-parsed in worker)
            - image_paths: Dict of clip_id -> file path
            - output_path: Where to write this chunk's video segment
            - fps, width, height, codec_preset, output_format

    Returns:
        Dict with: chunk_id, success, output_path, render_time_s, error
    """
    chunk_id = args["chunk_id"]
    start_frame = args["start_frame"]
    end_frame = args["end_frame"]
    output_path = args["output_path"]
    fps = args["fps"]
    width = args["width"]
    height = args["height"]

    start_time = time.time()

    try:
        # Reconstruct timeline from dict
        timeline = Timeline(**args["timeline_dict"])

        # Reconstruct image cache from file paths
        image_cache = {}
        for clip_id, img_path in args["image_paths"].items():
            try:
                image_cache[clip_id] = Image.open(img_path).convert("RGB")
            except Exception as e:
                logger.warning("Worker %s: Failed to load %s: %s", chunk_id, img_path, e)

        compositor = FrameCompositor(timeline, image_cache)

        # Build FFmpeg command for this chunk
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-s", f"{width}x{height}",
            "-pix_fmt", "rgb24",
            "-r", str(fps),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", args.get("codec_preset", "medium"),
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_path,
        ]

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        for frame_num in range(start_frame, end_frame):
            frame = compositor.render_frame(frame_num)
            if frame.mode != "RGB":
                frame = frame.convert("RGB")
            if frame.size != (width, height):
                frame = frame.resize((width, height), Image.LANCZOS)
            process.stdin.write(frame.tobytes())

        process.stdin.close()
        _, stderr = process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode("utf-8", errors="replace")[:500]
            return {
                "chunk_id": chunk_id,
                "success": False,
                "output_path": output_path,
                "render_time_s": time.time() - start_time,
                "error": f"FFmpeg failed: {error_msg}",
            }

        return {
            "chunk_id": chunk_id,
            "success": True,
            "output_path": output_path,
            "render_time_s": time.time() - start_time,
            "error": None,
        }

    except Exception as e:
        return {
            "chunk_id": chunk_id,
            "success": False,
            "output_path": output_path,
            "render_time_s": time.time() - start_time,
            "error": str(e)[:500],
        }


class LocalProvider(AbstractRenderProvider):
    """Single-process local renderer.

    Wraps the existing render path (FrameCompositor + FFmpeg encoder)
    in the provider interface. No parallelization — renders all frames
    sequentially in one process.
    """

    def render_chunk(
        self,
        chunk: RenderChunk,
        timeline: Timeline,
        image_cache: dict[str, Image.Image],
        output_dir: str,
        audio_path: Optional[str] = None,
    ) -> RenderChunk:
        start_time = time.time()
        chunk.status = ChunkStatus.RENDERING
        chunk.attempt += 1

        output_file = str(Path(output_dir) / f"{chunk.chunk_id}.mp4")

        try:
            compositor = FrameCompositor(timeline, image_cache)
            w = timeline.resolution.width
            h = timeline.resolution.height

            cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo", "-vcodec", "rawvideo",
                "-s", f"{w}x{h}", "-pix_fmt", "rgb24",
                "-r", str(timeline.fps), "-i", "-",
                "-c:v", "libx264", "-preset", timeline.codec_preset.value,
                "-crf", "18", "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                output_file,
            ]

            process = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )

            for frame_num in range(chunk.start_frame, chunk.end_frame):
                frame = compositor.render_frame(frame_num)
                if frame.mode != "RGB":
                    frame = frame.convert("RGB")
                if frame.size != (w, h):
                    frame = frame.resize((w, h), Image.LANCZOS)
                process.stdin.write(frame.tobytes())

            process.stdin.close()
            _, stderr = process.communicate()

            if process.returncode != 0:
                error = stderr.decode("utf-8", errors="replace")[:500]
                chunk.status = ChunkStatus.FAILED
                chunk.error_message = error
            else:
                chunk.status = ChunkStatus.COMPLETED
                chunk.output_path = output_file

        except Exception as e:
            chunk.status = ChunkStatus.FAILED
            chunk.error_message = str(e)[:500]

        chunk.render_time_s = time.time() - start_time
        return chunk

    def concatenate(
        self,
        chunk_paths: list[str],
        output_path: str,
        audio_path: Optional[str] = None,
    ) -> str:
        if len(chunk_paths) == 1 and audio_path is None:
            # Single chunk, no audio — just move it
            shutil.move(chunk_paths[0], output_path)
            return output_path

        return _ffmpeg_concatenate(chunk_paths, output_path, audio_path)

    def cleanup(self, job: RenderJob) -> None:
        for chunk in job.chunks:
            if chunk.output_path and Path(chunk.output_path).exists():
                try:
                    Path(chunk.output_path).unlink()
                except OSError:
                    pass


class LocalMultiprocessProvider(AbstractRenderProvider):
    """Parallel local renderer using multiprocessing.Pool.

    Splits the timeline into chunks and renders each chunk in a separate
    process. The number of workers defaults to CPU count - 1 (leave one
    core for the main process and system tasks).

    This is the recommended backend for local rendering of videos longer
    than a few seconds, as it can achieve near-linear speedup on multi-core
    machines.
    """

    def __init__(self, max_workers: Optional[int] = None):
        """
        Args:
            max_workers: Maximum worker processes. Defaults to CPU count - 1.
        """
        cpu_count = os.cpu_count() or 4
        self.max_workers = max_workers or max(1, cpu_count - 1)

    def render_chunk(
        self,
        chunk: RenderChunk,
        timeline: Timeline,
        image_cache: dict[str, Image.Image],
        output_dir: str,
        audio_path: Optional[str] = None,
    ) -> RenderChunk:
        """Render a single chunk (used internally by render_job).

        For multiprocess rendering, use render_all_chunks() instead.
        """
        # Fall back to single-process for individual chunks
        local = LocalProvider()
        return local.render_chunk(chunk, timeline, image_cache, output_dir, audio_path)

    def render_all_chunks(
        self,
        job: RenderJob,
        timeline: Timeline,
        image_cache: dict[str, Image.Image],
        output_dir: str,
    ) -> list[RenderChunk]:
        """Render all chunks in parallel using multiprocessing.

        This is the main entry point for parallel local rendering.

        Args:
            job: Render job with chunk specifications
            timeline: Full timeline specification
            image_cache: Pre-loaded images keyed by clip_id
            output_dir: Directory for chunk video segments

        Returns:
            List of updated RenderChunks with results
        """
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Save images to temp files for worker processes
        # (PIL Images can't be pickled, so workers re-load from disk)
        image_paths = {}
        temp_img_dir = Path(output_dir) / "_chunk_images"
        temp_img_dir.mkdir(exist_ok=True)

        for clip_id, img in image_cache.items():
            img_path = str(temp_img_dir / f"{clip_id}.png")
            img.save(img_path)
            image_paths[clip_id] = img_path

        # Serialize timeline for workers
        timeline_dict = timeline.model_dump()

        # Build worker arguments
        worker_args = []
        for chunk in job.chunks:
            output_path = str(Path(output_dir) / f"{chunk.chunk_id}.mp4")
            worker_args.append({
                "chunk_id": chunk.chunk_id,
                "start_frame": chunk.start_frame,
                "end_frame": chunk.end_frame,
                "timeline_dict": timeline_dict,
                "image_paths": image_paths,
                "output_path": output_path,
                "fps": timeline.fps,
                "width": timeline.resolution.width,
                "height": timeline.resolution.height,
                "codec_preset": timeline.codec_preset.value,
                "output_format": timeline.output_format.value,
            })

        # Dispatch to worker pool
        num_workers = min(self.max_workers, len(job.chunks))
        logger.info(
            "Rendering %d chunks across %d workers",
            len(job.chunks), num_workers
        )

        with multiprocessing.Pool(processes=num_workers) as pool:
            results = pool.map(_render_chunk_worker, worker_args)

        # Update chunk statuses from results
        result_map = {r["chunk_id"]: r for r in results}
        for chunk in job.chunks:
            result = result_map.get(chunk.chunk_id)
            if result is None:
                chunk.status = ChunkStatus.FAILED
                chunk.error_message = "No result returned from worker"
                continue

            if result["success"]:
                chunk.status = ChunkStatus.COMPLETED
                chunk.output_path = result["output_path"]
            else:
                chunk.status = ChunkStatus.FAILED
                chunk.error_message = result.get("error", "Unknown error")

            chunk.render_time_s = result.get("render_time_s", 0.0)
            chunk.attempt += 1

        # Clean up temp images
        shutil.rmtree(str(temp_img_dir), ignore_errors=True)

        return job.chunks

    def concatenate(
        self,
        chunk_paths: list[str],
        output_path: str,
        audio_path: Optional[str] = None,
    ) -> str:
        if len(chunk_paths) == 1 and audio_path is None:
            shutil.move(chunk_paths[0], output_path)
            return output_path

        return _ffmpeg_concatenate(chunk_paths, output_path, audio_path)

    def cleanup(self, job: RenderJob) -> None:
        for chunk in job.chunks:
            if chunk.output_path and Path(chunk.output_path).exists():
                try:
                    Path(chunk.output_path).unlink()
                except OSError:
                    pass


def _ffmpeg_concatenate(
    chunk_paths: list[str],
    output_path: str,
    audio_path: Optional[str] = None,
) -> str:
    """Concatenate video segments using FFmpeg's concat demuxer.

    Args:
        chunk_paths: Ordered list of video file paths
        output_path: Where to write the concatenated video
        audio_path: Optional audio file to mux into the result

    Returns:
        Path to the concatenated video

    Raises:
        RuntimeError: If FFmpeg concat fails
    """
    if not check_ffmpeg():
        raise RuntimeError("FFmpeg required for concatenation")

    # Write concat file list
    concat_dir = Path(output_path).parent
    concat_file = concat_dir / f"_concat_{uuid.uuid4().hex[:8]}.txt"

    try:
        with open(concat_file, "w") as f:
            for path in chunk_paths:
                # FFmpeg concat requires escaped paths
                escaped = str(Path(path).resolve()).replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
        ]

        if audio_path and Path(audio_path).exists():
            cmd.extend(["-i", str(audio_path)])
            cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "192k"])
            cmd.extend(["-shortest"])
        else:
            cmd.extend(["-c", "copy"])

        cmd.append(str(output_path))

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"FFmpeg concat failed (exit {result.returncode}): {result.stderr[:500]}"
            )

        if not Path(output_path).exists():
            raise RuntimeError(f"FFmpeg concat produced no output at {output_path}")

        return str(output_path)

    finally:
        if concat_file.exists():
            concat_file.unlink()
