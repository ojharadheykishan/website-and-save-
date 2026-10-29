import os

import app as app_module
from app import app, build_stream_response


def test_build_stream_response_uses_inline_video_mimetype(tmp_path):
    media_path = tmp_path / "sample.mp4"
    media_path.write_bytes(b"fake mp4")

    with app.test_request_context('/'):
        response = build_stream_response(str(media_path), as_attachment=False)

    assert response.mimetype == "video/mp4"
    assert "inline" in response.headers.get("Content-Disposition", "")


def test_build_stream_response_uses_attachment_for_download(tmp_path):
    media_path = tmp_path / "sample.mp4"
    media_path.write_bytes(b"fake mp4")

    with app.test_request_context('/'):
        response = build_stream_response(str(media_path), as_attachment=True)

    assert response.mimetype == "video/mp4"
    assert "attachment" in response.headers.get("Content-Disposition", "")


def test_player_page_offers_1080p_and_extended_playback_controls(monkeypatch):
    token = "a" * 32
    monkeypatch.setattr(app_module, "get_stream_file", lambda _token: {"file_path": "/tmp/video.mp4"})
    monkeypatch.setattr(app_module, "get_public_base_url", lambda: "https://example.com")
    monkeypatch.setattr(app_module, "get_stream_entry", lambda _token: {})

    response = app.test_client().get(f"/player/{token}")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert '<option value="1080p">1080p</option>' in page
    assert '<option value="4">4×</option>' in page
    assert 'id="loop"' in page
    assert 'id="rotate"' in page
    assert 'id="fit"' in page


def test_1080p_quality_endpoint_serves_variant(monkeypatch, tmp_path):
    token = "b" * 32
    variant = tmp_path / "1080p.mp4"
    variant.write_bytes(b"fake mp4")
    monkeypatch.setattr(app_module, "get_stream_file", lambda _token: {"file_path": "/tmp/video.mp4"})
    monkeypatch.setattr(app_module, "build_quality_variant", lambda _path, _token, quality: str(variant) if quality == "1080p" else None)

    response = app.test_client().get(f"/stream/{token}/1080p")

    assert response.status_code == 200
    assert response.mimetype == "video/mp4"
