import asyncio

from safe_repo.modules import stream as stream_module
from safe_repo.modules.stream import build_local_file_name, has_media_payload, get_archive_chat_ids, format_progress_bar
from safe_repo.core import get_func
from safe_repo.core.get_func import trigger_stream_link_notifications


class DummyMessage:
    message_id = 42

    class DummyMedia:
        pass

    video = DummyMedia()


def test_build_local_file_name_uses_video_extension():
    filename = build_local_file_name(DummyMessage())
    assert filename.startswith("stream_42_")
    assert filename.endswith(".mp4")


def test_build_local_file_name_falls_back_to_id():
    class MessageWithId:
        id = 77
        video = object()

    filename = build_local_file_name(MessageWithId())
    assert filename.startswith("stream_77_")
    assert filename.endswith(".mp4")


def test_has_media_payload_for_forwarded_message():
    class ForwardedMessage:
        forwarded = True
        media = False

    assert has_media_payload(ForwardedMessage())


def test_has_media_payload_for_forward_origin_field():
    class ForwardOriginMessage:
        forward_origin = object()
        media = False

    assert has_media_payload(ForwardOriginMessage())


def test_get_archive_chat_ids_includes_configured_channel(monkeypatch):
    monkeypatch.delenv("ARCHIVE_CHAT_ID", raising=False)
    monkeypatch.delenv("CLONE_LOG_CHANNEL", raising=False)
    ids = get_archive_chat_ids()
    assert -1003886456761 in ids


def test_format_progress_bar_contains_percentage_and_label():
    text = format_progress_bar(60, "Downloading media", "Please wait")
    assert "60%" in text
    assert "Downloading media" in text
    assert "Please wait" in text
    assert any(sticker in text for sticker in stream_module.LINK_PROGRESS_STICKERS)


def test_link_progress_bar_animates_its_own_sticker_and_spinner():
    first = format_progress_bar(60, "Preparing stream link", "Generating", frame=0)
    second = format_progress_bar(60, "Preparing stream link", "Generating", frame=1)

    assert first != second
    assert "📥" not in first
    assert "📤" not in first


def test_trigger_stream_link_notifications_schedules_both_paths(monkeypatch):
    calls = []

    async def fake_share(sender, result_msg):
        calls.append(("share", sender, result_msg))

    async def fake_notify(sender, file_path):
        calls.append(("notify", sender, file_path))

    monkeypatch.setattr("safe_repo.core.get_func.share_stream_link", fake_share)
    monkeypatch.setattr("safe_repo.core.get_func.notify_stream_links", fake_notify)

    asyncio.run(trigger_stream_link_notifications(123, "message", "/tmp/file"))

    assert calls == [
        ("share", 123, "message"),
        ("notify", 123, "/tmp/file"),
    ]


def test_download_media_payload_reports_transfer_progress(monkeypatch):
    class ProgressMessage:
        async def edit_text(self, text):
            self.text = text

    class Client:
        async def download_media(self, message, **kwargs):
            self.kwargs = kwargs
            return "/tmp/video.mp4"

    async def fake_edit_message_text(*args):
        pass

    monkeypatch.setattr(stream_module.app, "edit_message_text", fake_edit_message_text)
    client = Client()
    result = asyncio.run(
        stream_module.download_media_payload(
            client,
            DummyMessage(),
            progress_sender=123,
            progress_message_id=456,
            progress_message=ProgressMessage(),
        )
    )

    assert result == "/tmp/video.mp4"
    assert client.kwargs["progress"] is stream_module.progress_bar
    assert client.kwargs["progress_args"][0] == "📥 Downloading media..."


def test_forwarded_media_generates_a_website_link(tmp_path, monkeypatch):
    media_file = tmp_path / "forwarded.mp4"
    media_file.write_bytes(b"video")
    updates = []

    class IncomingMessage:
        chat = type("Chat", (), {"id": 123})()
        id = 456
        forward_origin = object()
        caption = "Forwarded lesson"

    class StatusMessage:
        id = 789

    async def fake_send_message(chat_id, text):
        assert chat_id == 123
        updates.append(text)
        return StatusMessage()

    async def fake_edit_message_text(chat_id, message_id, text):
        assert (chat_id, message_id) == (123, 789)
        updates.append(text)

    async def fake_download(*args, **kwargs):
        return str(media_file)

    async def fake_build_link(*args, **kwargs):
        return {
            "player_url": "https://example.com/player/video",
            "stream_url": "https://example.com/stream/video",
            "token": "video",
        }

    async def fake_archive(*args, **kwargs):
        pass

    monkeypatch.setattr(stream_module.app, "send_message", fake_send_message)
    monkeypatch.setattr(stream_module.app, "edit_message_text", fake_edit_message_text)
    monkeypatch.setattr(stream_module, "download_media_payload", fake_download)
    monkeypatch.setattr(stream_module, "build_public_stream_link", fake_build_link)
    monkeypatch.setattr(
        stream_module,
        "extract_stream_metadata",
        lambda *args, **kwargs: {
            "subject": "General",
            "category": "General",
            "folder": "General",
            "description": "Forwarded lesson",
            "title": "Forwarded lesson",
            "date": "2026-11-08",
        },
    )
    monkeypatch.setattr(stream_module, "calculate_content_hash", lambda _path: "digest")
    monkeypatch.setattr(
        stream_module,
        "build_public_study_link",
        lambda *args, **kwargs: "https://example.com/study",
    )
    monkeypatch.setattr(stream_module, "append_stream_link", lambda *args, **kwargs: "archive")
    monkeypatch.setattr(stream_module, "archive_stream_link", fake_archive)

    asyncio.run(stream_module.handle_direct_media(object(), IncomingMessage()))

    assert any("https://example.com/player/video" in update for update in updates)
    assert not media_file.exists()


def test_private_message_forward_uses_saved_output_destination(monkeypatch):
    forwarded_to = []
    link_notifications = []

    class SourceMessage:
        media = True
        service = None
        empty = None
        has_protected_content = False
        pinned_message = None
        caption = ""

    class ForwardedMessage:
        id = 99

    class Userbot:
        async def get_messages(self, chat, message_id, **kwargs):
            return SourceMessage()

        async def forward_messages(self, target, chat, message_id):
            forwarded_to.append(target)
            return ForwardedMessage()

        async def send_message(self, *args, **kwargs):
            pass

        async def download_media(self, message, **kwargs):
            return "/tmp/forwarded-media.mp4"

    class RequestMessage:
        class Chat:
            id = 123

        chat = Chat()

    class ProgressMessage:
        id = 1

        async def edit_text(self, text):
            pass

    async def fake_edit_message_text(*args):
        return ProgressMessage()

    async def fake_log_clone_operation(*args, **kwargs):
        pass

    async def fake_trigger_stream_link_notifications(sender, result_msg, file_path):
        link_notifications.append((sender, result_msg.id, file_path))

    monkeypatch.setattr(get_func, "get_user_chat_id", lambda user_id, default: -100987)
    monkeypatch.setattr(get_func.app, "edit_message_text", fake_edit_message_text)
    monkeypatch.setattr(get_func, "log_clone_operation", fake_log_clone_operation)
    monkeypatch.setattr(
        get_func, "trigger_stream_link_notifications", fake_trigger_stream_link_notifications
    )
    get_func.user_chat_ids.clear()

    async def run():
        await get_func.get_msg(
            Userbot(),
            123,
            1,
            "https://t.me/c/123456/10",
            0,
            RequestMessage(),
            is_batch=True,
        )
        await asyncio.sleep(0)

    asyncio.run(run())

    assert forwarded_to == [-100987]
    assert link_notifications == [(123, 99, "/tmp/forwarded-media.mp4")]


def test_public_message_copy_targets_selected_forum_topic(monkeypatch):
    copy_options = {}

    class SourceMessage:
        media = True
        caption = "Lesson"
        pinned_message = None
        file_name = "lesson.mp4"

    class ResultMessage:
        async def copy(self, chat_id):
            pass

    class Client:
        async def get_messages(self, chat_id, message_id, **kwargs):
            return SourceMessage()

        async def copy_message(self, chat_id, from_chat_id, message_id, **kwargs):
            copy_options.update(kwargs)
            return ResultMessage()

    async def fake_log_clone_operation(*args, **kwargs):
        pass

    async def fake_share_stream_link(*args, **kwargs):
        pass

    monkeypatch.setattr(get_func, "get_user_caption_preference", lambda sender: "")
    monkeypatch.setattr(get_func, "load_delete_words", lambda sender: set())
    monkeypatch.setattr(get_func, "load_replacement_words", lambda sender: {})
    monkeypatch.setattr(get_func, "log_clone_operation", fake_log_clone_operation)
    monkeypatch.setattr(get_func, "share_stream_link", fake_share_stream_link)

    async def run():
        await get_func.copy_message_with_chat_id(
            Client(),
            123,
            "publicgroup",
            10,
            is_batch=True,
            destination_chat_id=-100987,
            destination_thread_id=55,
        )
        await asyncio.sleep(0)

    asyncio.run(run())

    assert copy_options["reply_to_message_id"] == 55
