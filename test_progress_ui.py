import asyncio
import os

import cv2
import numpy as np

from safe_repo.core.func import (
    PROGRESS_STICKERS,
    THEME_STYLES,
    ProgressSession,
    progress_bar,
    screenshot,
)


def test_progress_sessions_offer_twelve_visual_styles():
    assert len(THEME_STYLES) == 12
    assert 0 <= ProgressSession().style_index < 12


def test_progress_stickers_show_once_before_the_cycle_repeats():
    session = ProgressSession()
    stickers = [session.next_sticker() for _ in range(len(PROGRESS_STICKERS))]

    assert len(PROGRESS_STICKERS) == 40
    assert len(set(stickers)) == 40
    assert session.next_sticker() != stickers[-1]


def test_transfer_progress_edits_message_and_shows_sticker():
    class ProgressMessage:
        text = None

        async def edit_text(self, text):
            self.text = text

    message = ProgressMessage()
    session = ProgressSession()
    asyncio.run(
        progress_bar(
            50,
            100,
            "📥 Downloading media...",
            message,
            0,
            "video.mp4",
            "https://t.me/example/1",
            session,
        )
    )

    assert message.text is not None
    assert session.stickers[0] in message.text


def test_custom_thumbnail_is_resized_and_reencoded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cv2.imwrite("123.jpg", np.full((600, 800, 3), 255, dtype=np.uint8))

    thumbnail = asyncio.run(screenshot("unused.mp4", 0, 123))
    try:
        image = cv2.imread(thumbnail)
        assert image.shape[0] <= 320
        assert image.shape[1] <= 320
        assert os.path.getsize(thumbnail) <= 200 * 1024
    finally:
        if thumbnail and os.path.exists(thumbnail):
            os.remove(thumbnail)


def test_video_thumbnail_uses_a_frame_and_skips_dark_intro(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_ffmpeg(*args, **kwargs):
        calls.append(args)
        brightness = 0 if len(calls) == 1 else 220
        frame = np.full((480, 640, 3), brightness, dtype=np.uint8)
        assert cv2.imwrite(args[-1], frame)
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_ffmpeg)
    thumbnail = asyncio.run(screenshot("sample.mp4", 20, 456))
    try:
        assert thumbnail
        image = cv2.imread(thumbnail)
        assert image.shape[0] <= 320
        assert image.shape[1] <= 320
        assert len(calls) == 2
        assert calls[0][0] == "ffmpeg"
    finally:
        if thumbnail and os.path.exists(thumbnail):
            os.remove(thumbnail)
