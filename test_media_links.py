import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safe_repo.core.media_links import (
    add_youtube_catalog_entry,
    save_stream_file,
    append_stream_link,
    read_stream_entries,
)


def test_save_stream_file_creates_public_stream_and_player_urls(tmp_path):
    source = tmp_path / "sample.mp4"
    source.write_bytes(b"test-media")

    cache_dir = tmp_path / "cache"
    result = save_stream_file(
        str(source),
        base_url="https://example.com",
        cache_dir=str(cache_dir),
    )

    assert result is not None
    assert result["token"]
    assert result["stream_url"].startswith("https://example.com/stream/")
    assert result["player_url"].startswith("https://example.com/player/")
    assert os.path.exists(result["file_path"])
    assert os.path.getsize(result["file_path"]) == len(b"test-media")


def test_save_stream_file_rejects_large_files(tmp_path):
    source = tmp_path / "large.mp4"
    source.write_bytes(b"x" * 1024)

    cache_dir = tmp_path / "cache"
    result = save_stream_file(
        str(source),
        base_url="https://example.com",
        cache_dir=str(cache_dir),
        max_size_mb=0,
    )

    assert result is None


def test_append_stream_link_stores_catalog_entry(tmp_path):
    archive_path = tmp_path / "links.txt"
    entry_path = tmp_path / "catalog.json"
    result = append_stream_link(
        "https://example.com/player/demo",
        "https://example.com/stream/demo",
        archive_path=str(archive_path),
        catalog_path=str(entry_path),
        subject="Movie",
        description="A sample description",
        title="Sample title",
        token="demo",
    )

    assert archive_path.exists()
    assert entry_path.exists()
    entries = read_stream_entries(catalog_path=str(entry_path))
    assert len(entries) == 1
    assert entries[0]["subject"] == "Movie"
    assert entries[0]["token"] == "demo"


def test_add_youtube_catalog_entry_is_public_and_idempotent(tmp_path):
    catalog_path = tmp_path / "catalog.json"

    first = add_youtube_catalog_entry(
        "abcdefghijk",
        "Study lesson",
        "Teacher",
        base_url="https://study.example",
        catalog_path=str(catalog_path),
    )
    second = add_youtube_catalog_entry(
        "abcdefghijk",
        "Changed title",
        "Different channel",
        base_url="https://study.example",
        catalog_path=str(catalog_path),
    )

    entries = read_stream_entries(catalog_path=str(catalog_path))
    assert first["added"] is True
    assert second["added"] is False
    assert len(entries) == 1
    assert entries[0]["source"] == "youtube"
    assert entries[0]["youtube_video_id"] == "abcdefghijk"
    assert entries[0]["description"].startswith("Folder: YouTube")
    assert entries[0]["player_url"] == "https://study.example/study/watch/yt-abcdefghijk"


def test_add_youtube_catalog_entry_does_not_overwrite_invalid_catalog(tmp_path):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("{invalid", encoding="utf-8")

    try:
        add_youtube_catalog_entry(
            "abcdefghijk",
            "Study lesson",
            "Teacher",
            catalog_path=str(catalog_path),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid catalog content must not be overwritten")

    assert catalog_path.read_text(encoding="utf-8") == "{invalid"
