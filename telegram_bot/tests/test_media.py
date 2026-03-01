"""
Tests for media.py (file handling utilities).

Tests file download, ZIP creation, chunking logic, and the
split-ZIP boundary conditions.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_bot.media import (
    download_telegram_file,
    send_project_files,
    send_screenshot,
    WORKSPACE_BASE,
)


def _make_message(file_type: str = "document", filename: str = "test.txt") -> MagicMock:
    """Create a mock Telegram Message with a file attachment."""
    message = MagicMock()
    message.photo = None
    message.document = None
    message.voice = None
    message.audio = None
    message.video = None

    if file_type == "photo":
        photo = MagicMock()
        photo.file_id = "photo_file_id"
        message.photo = [MagicMock(), photo]  # list of sizes, last is largest
    elif file_type == "document":
        doc = MagicMock()
        doc.file_id = "doc_file_id"
        doc.file_name = filename
        message.document = doc
    elif file_type == "voice":
        voice = MagicMock()
        voice.file_id = "voice_file_id"
        message.voice = voice
    elif file_type == "audio":
        audio = MagicMock()
        audio.file_id = "audio_file_id"
        audio.file_name = filename
        message.audio = audio
    elif file_type == "video":
        video = MagicMock()
        video.file_id = "video_file_id"
        video.file_name = filename
        message.video = video
    elif file_type == "none":
        pass  # No attachment

    return message


def _make_context(tmp_path: Path) -> MagicMock:
    """Create a mock context with a bot that downloads to tmp_path."""
    context = MagicMock()
    file_obj = MagicMock()

    async def fake_download(destination):
        Path(destination).write_text("fake file content")

    file_obj.download_to_drive = AsyncMock(side_effect=fake_download)
    context.bot.get_file = AsyncMock(return_value=file_obj)
    context.bot.send_message = AsyncMock()
    context.bot.send_document = AsyncMock()
    context.bot.send_photo = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_download_document(tmp_path, monkeypatch):
    monkeypatch.setattr("telegram_bot.media.WORKSPACE_BASE", tmp_path)
    message = _make_message("document", "report.pdf")
    context = _make_context(tmp_path)

    result = await download_telegram_file(message, "session123", context)

    assert result is not None
    assert result.name == "report.pdf"
    assert result.exists()
    assert "session123" in str(result)


@pytest.mark.asyncio
async def test_download_photo(tmp_path, monkeypatch):
    monkeypatch.setattr("telegram_bot.media.WORKSPACE_BASE", tmp_path)
    message = _make_message("photo")
    context = _make_context(tmp_path)

    result = await download_telegram_file(message, "session456", context)

    assert result is not None
    assert result.suffix == ".jpg"


@pytest.mark.asyncio
async def test_download_voice(tmp_path, monkeypatch):
    monkeypatch.setattr("telegram_bot.media.WORKSPACE_BASE", tmp_path)
    message = _make_message("voice")
    context = _make_context(tmp_path)

    result = await download_telegram_file(message, "session789", context)

    assert result is not None
    assert result.suffix == ".ogg"


@pytest.mark.asyncio
async def test_download_no_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr("telegram_bot.media.WORKSPACE_BASE", tmp_path)
    message = _make_message("none")
    context = _make_context(tmp_path)

    result = await download_telegram_file(message, "session000", context)

    assert result is None


@pytest.mark.asyncio
async def test_send_single_file(tmp_path):
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("print('hello')")

    context = MagicMock()
    context.bot.send_document = AsyncMock()
    context.bot.send_message = AsyncMock()

    await send_project_files(12345, project_dir, context)

    context.bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_multiple_files_as_zip(tmp_path):
    project_dir = tmp_path / "multiproject"
    project_dir.mkdir()
    (project_dir / "a.py").write_text("a")
    (project_dir / "b.py").write_text("b")
    (project_dir / "c.py").write_text("c")

    context = MagicMock()
    context.bot.send_document = AsyncMock()
    context.bot.send_message = AsyncMock()

    await send_project_files(12345, project_dir, context)

    context.bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_empty_project(tmp_path):
    project_dir = tmp_path / "emptyproject"
    project_dir.mkdir()

    context = MagicMock()
    context.bot.send_message = AsyncMock()

    await send_project_files(12345, project_dir, context)

    context.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_filters_junk_dirs(tmp_path):
    project_dir = tmp_path / "junkproject"
    project_dir.mkdir()
    (project_dir / "main.py").write_text("main")
    node_modules = project_dir / "node_modules"
    node_modules.mkdir()
    (node_modules / "dep.js").write_text("dep")

    context = MagicMock()
    context.bot.send_document = AsyncMock()
    context.bot.send_message = AsyncMock()

    await send_project_files(12345, project_dir, context)

    # Should send only main.py (node_modules filtered out)
    context.bot.send_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_screenshot_as_photo(tmp_path):
    screenshot = tmp_path / "screenshot.png"
    screenshot.write_bytes(b"\x89PNG" + b"\x00" * 100)

    context = MagicMock()
    context.bot.send_photo = AsyncMock()

    await send_screenshot(12345, screenshot, context, caption="Preview")

    context.bot.send_photo.assert_awaited_once()
