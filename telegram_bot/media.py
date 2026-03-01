"""
File download/upload/chunking utilities for Telegram media handling.

Inbound: Downloads files (photos, documents, audio, voice, video) from
Telegram messages to the VPS workspace so the pipeline can access them.

Outbound: Sends built project files back to the user via Telegram.
Handles single files, ZIP archives, and split-ZIP for oversized projects.

Telegram limits:
  - Download (inbound): 20 MB max via Bot API get_file()
  - Document upload (outbound): 50 MB max
  - Photo upload (outbound): 10 MB max
  - Video upload (outbound): 2 GB max via send_video()
"""

from __future__ import annotations

import os
import uuid
import logging
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional

from telegram import Message, PhotoSize
from telegram.constants import FileSizeLimit

logger = logging.getLogger(__name__)

# Base directory for downloaded files. Each build session gets a subdirectory.
WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_PATH", "/workspace"))


# ---------------------------------------------------------------------------
# Inbound: Downloading files from Telegram
# ---------------------------------------------------------------------------

async def download_telegram_file(
    message: Message,
    session_id: str,
    context,
) -> Optional[Path]:
    """Download any file attached to a Telegram message.

    Handles photos, documents, audio, voice, and video.
    Files are saved to /workspace/{session_id}/inbound/{original_or_generated_filename}.

    Returns the local path to the downloaded file, or None if no file was attached.
    """
    inbound_dir = WORKSPACE_BASE / session_id / "inbound"
    inbound_dir.mkdir(parents=True, exist_ok=True)

    file_obj = None
    filename = None

    if message.photo:
        # Photos arrive as a list of sizes. Take the largest (last).
        photo: PhotoSize = message.photo[-1]
        file_obj = await context.bot.get_file(photo.file_id)
        filename = f"photo_{uuid.uuid4().hex[:8]}.jpg"

    elif message.document:
        doc = message.document
        file_obj = await context.bot.get_file(doc.file_id)
        # Preserve original filename if available
        filename = doc.file_name or f"document_{uuid.uuid4().hex[:8]}"

    elif message.voice:
        voice = message.voice
        file_obj = await context.bot.get_file(voice.file_id)
        filename = f"voice_{uuid.uuid4().hex[:8]}.ogg"

    elif message.audio:
        audio = message.audio
        file_obj = await context.bot.get_file(audio.file_id)
        filename = audio.file_name or f"audio_{uuid.uuid4().hex[:8]}.mp3"

    elif message.video:
        video = message.video
        file_obj = await context.bot.get_file(video.file_id)
        filename = video.file_name or f"video_{uuid.uuid4().hex[:8]}.mp4"

    if file_obj is None:
        return None

    destination = inbound_dir / filename
    await file_obj.download_to_drive(destination)

    logger.info("Downloaded file: %s (%d bytes)", destination, destination.stat().st_size)
    return destination


# ---------------------------------------------------------------------------
# Outbound: Sending files to user via Telegram
# ---------------------------------------------------------------------------

async def send_project_files(
    chat_id: int,
    project_dir: Path,
    context,
    caption: str = "",
) -> None:
    """Send project files to the user via Telegram.

    Strategy:
    1. If the project is a single file under 50MB, send it directly.
    2. If the project is multiple files, create a ZIP and send that.
    3. If the ZIP exceeds 50MB, split into chunks and send multiple ZIPs.
    4. For image files (screenshots, diagrams), send as photos for inline preview.
    """
    # Collect all files in the project directory
    all_files = [f for f in project_dir.rglob("*") if f.is_file()]

    # Filter out common junk (node_modules, __pycache__, .git)
    all_files = [
        f for f in all_files
        if not any(part in f.parts for part in ["node_modules", "__pycache__", ".git", "venv"])
    ]

    if not all_files:
        await context.bot.send_message(chat_id, "\U0001f4ed No output files to deliver.")
        return

    # Single file: send directly
    if len(all_files) == 1:
        f = all_files[0]
        if f.stat().st_size <= FileSizeLimit.FILESIZE_UPLOAD:  # 50 MB
            file_data = BytesIO(f.read_bytes())
            file_data.name = f.name
            await context.bot.send_document(
                chat_id,
                document=file_data,
                filename=f.name,
                caption=caption or f"\U0001f4ce {f.name}",
            )
            return

    # Multiple files or oversized single file: ZIP and send
    zip_path = project_dir / f"{project_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in all_files:
            zf.write(f, f.relative_to(project_dir))

    zip_size = zip_path.stat().st_size

    if zip_size <= FileSizeLimit.FILESIZE_UPLOAD:  # 50 MB
        zip_data = BytesIO(zip_path.read_bytes())
        zip_data.name = zip_path.name
        await context.bot.send_document(
            chat_id,
            document=zip_data,
            filename=zip_path.name,
            caption=caption or f"\U0001f4e6 {len(all_files)} files ({zip_size / 1024 / 1024:.1f} MB)",
        )
    else:
        # Split into chunks. Each chunk is a separate ZIP under 49 MB.
        chunk_size = 49 * 1024 * 1024  # 49 MB to leave headroom
        await _send_split_zip(chat_id, all_files, project_dir, chunk_size, context)

    # Clean up ZIP
    zip_path.unlink(missing_ok=True)


async def send_screenshot(chat_id: int, screenshot_path: Path, context, caption: str = "") -> None:
    """Send a screenshot as a Telegram photo (inline preview)."""
    file_data = BytesIO(screenshot_path.read_bytes())
    file_data.name = screenshot_path.name
    if screenshot_path.stat().st_size <= 10 * 1024 * 1024:  # 10 MB photo limit
        await context.bot.send_photo(
            chat_id, photo=file_data, caption=caption,
        )
    else:
        # Oversized for photo: send as document instead
        await context.bot.send_document(
            chat_id, document=file_data, filename=screenshot_path.name, caption=caption,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _send_split_zip(chat_id, files, base_dir, chunk_size, context):
    """Split files into multiple ZIP archives under chunk_size bytes each."""
    chunk_num = 1
    current_chunk_files = []
    current_size = 0

    for f in sorted(files, key=lambda x: x.stat().st_size):
        fsize = f.stat().st_size
        if current_size + fsize > chunk_size and current_chunk_files:
            await _send_chunk(chat_id, current_chunk_files, base_dir, chunk_num, context)
            chunk_num += 1
            current_chunk_files = []
            current_size = 0
        current_chunk_files.append(f)
        current_size += fsize

    if current_chunk_files:
        await _send_chunk(chat_id, current_chunk_files, base_dir, chunk_num, context)


async def _send_chunk(chat_id, files, base_dir, chunk_num, context):
    """Create and send a single chunk ZIP."""
    chunk_path = base_dir / f"{base_dir.name}_part{chunk_num}.zip"
    with zipfile.ZipFile(chunk_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(base_dir))

    chunk_data = BytesIO(chunk_path.read_bytes())
    chunk_data.name = chunk_path.name
    await context.bot.send_document(
        chat_id,
        document=chunk_data,
        filename=chunk_path.name,
        caption=f"\U0001f4e6 Part {chunk_num} ({len(files)} files)",
    )
    chunk_path.unlink(missing_ok=True)
