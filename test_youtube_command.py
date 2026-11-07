import asyncio

from safe_repo.modules import yt


def test_parse_youtube_video_urls():
    assert yt.parse_youtube_video_id("https://www.youtube.com/watch?v=abcdefghijk") == "abcdefghijk"
    assert yt.parse_youtube_video_id("https://youtu.be/abcdefghijk?t=42") == "abcdefghijk"
    assert yt.parse_youtube_video_id("youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert yt.parse_youtube_video_id("https://youtube.com/playlist?list=abcdefghijk") is None


def test_parse_youtube_rejects_non_youtube_and_malformed_urls():
    assert yt.parse_youtube_video_id("https://example.com/watch?v=abcdefghijk") is None
    assert yt.parse_youtube_video_id("https://youtube.com:bad/watch?v=abcdefghijk") is None
    assert yt.parse_youtube_video_id("http://youtube.com/watch?v=abcdefghijk") is None


def test_youtube_link_preview_returns_only_official_link(monkeypatch):
    replies = []

    class Message:
        command = ["yt", "https://youtu.be/abcdefghijk"]

        async def reply_text(self, *args, **kwargs):
            replies.append((args[0], kwargs))

    message = Message()
    monkeypatch.setattr(
        yt,
        "_request_oembed",
        lambda video_id: {"title": "Sample <video>", "author_name": "Creator"},
    )

    asyncio.run(yt.youtube_command(None, message))

    assert len(replies) == 1
    text, options = replies[0]
    assert "Sample &lt;video&gt;" in text
    assert options["reply_markup"].inline_keyboard[0][0].url == (
        "https://www.youtube.com/watch?v=abcdefghijk"
    )


def test_youtube_search_displays_results_and_official_links(monkeypatch):
    replies = []

    class Message:
        command = ["yt", "sample", "search"]

        async def reply_text(self, *args, **kwargs):
            replies.append((args[0], kwargs))

    monkeypatch.setattr(yt, "YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(
        yt,
        "_request_search",
        lambda query: {
            "items": [
                {
                    "id": {"videoId": "abcdefghijk"},
                    "snippet": {"title": "Sample <video>", "channelTitle": "Creator"},
                }
            ]
        },
    )

    asyncio.run(yt.youtube_command(None, Message()))

    text, options = replies[0]
    assert "Sample &lt;video&gt;" in text
    assert options["reply_markup"].inline_keyboard[0][0].url == (
        "https://www.youtube.com/watch?v=abcdefghijk"
    )


def test_youtube_search_without_api_key_explains_setup(monkeypatch):
    replies = []

    class Message:
        command = ["yt", "sample", "search"]

        async def reply_text(self, *args, **kwargs):
            replies.append(args[0])

    monkeypatch.setattr(yt, "YOUTUBE_API_KEY", "")
    asyncio.run(yt.youtube_command(None, Message()))

    assert len(replies) == 1
    assert "YOUTUBE_API_KEY" in replies[0]
