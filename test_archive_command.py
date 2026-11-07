import asyncio
import os

from safe_repo.modules import archive


def test_direct_file_urls_must_use_https_video_file_and_public_host(monkeypatch):
    monkeypatch.setattr(
        archive.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))],
    )

    host, address, target, filename = archive._validated_video_url(
        "https://creator.example/study.mp4?token=abc"
    )

    assert (host, address, target, filename) == (
        "creator.example",
        "93.184.216.34",
        "/study.mp4?token=abc",
        "study.mp4",
    )


def test_direct_file_urls_reject_private_hosts_and_non_video_paths(monkeypatch):
    monkeypatch.setattr(
        archive.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )

    try:
        archive._validated_video_url("https://creator.example/video.mp4")
    except ValueError as error:
        assert "public internet" in str(error)
    else:
        raise AssertionError("Private network destinations must be rejected")

    try:
        archive._validated_video_url("https://creator.example/video.html")
    except ValueError as error:
        assert "supported video file" in str(error)
    else:
        raise AssertionError("Non-video paths must be rejected")


def test_direct_video_download_streams_to_a_temporary_file(monkeypatch):
    class Response:
        status = 200

        def __init__(self):
            self.chunks = iter((b"video-", b"data", b""))

        def getheader(self, name, default=""):
            return {
                "Content-Type": "video/mp4",
                "Content-Length": "10",
            }.get(name, default)

        def read(self, _size):
            return next(self.chunks)

    class Connection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(
        archive,
        "_validated_video_url",
        lambda _url: ("creator.example", "93.184.216.34", "/video.mp4", "video.mp4"),
    )
    monkeypatch.setattr(archive, "_PinnedHTTPSConnection", Connection)

    file_path, filename = archive._download_direct_video("https://creator.example/video.mp4")
    try:
        assert filename == "video.mp4"
        with open(file_path, "rb") as video_file:
            assert video_file.read() == b"video-data"
    finally:
        os.remove(file_path)


def test_archive_replied_video_into_configured_group(monkeypatch):
    calls = []

    class Status:
        async def edit_text(self, text):
            calls.append(("status", text))

    class Source:
        id = 321
        chat = type("Chat", (), {"id": 456})()
        video = type("Video", (), {"mime_type": "video/mp4", "file_size": 1024})()
        document = None

    class Message:
        from_user = type("User", (), {"id": 456})()
        chat = type("Chat", (), {"id": 456})()
        reply_to_message = Source()

        async def reply_text(self, _text):
            return Status()

    async def copy_message(destination, source_chat, source_message):
        calls.append(("copy", destination, source_chat, source_message))
        return object()

    monkeypatch.setattr(archive, "get_user_chat_id", lambda _user_id: -100999)
    monkeypatch.setattr(archive.app, "copy_message", copy_message)

    asyncio.run(archive.archive_command(None, Message()))

    assert ("copy", -100999, 456, 321) in calls
    assert calls[-1] == ("status", "✅ Video archived in your configured study group.")
