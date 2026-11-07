import asyncio
import http.client
import ipaddress
import logging
import os
import re
import socket
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pyrogram import filters
from pyrogram.types import Message

from safe_repo import app
from safe_repo.core.func import new_progress_session, progress_bar
from safe_repo.core.get_func import get_user_chat_id

logger = logging.getLogger(__name__)
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg",
    ".mpg", ".ts", ".webm",
}


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, **kwargs):
        super().__init__(hostname, **kwargs)
        self.address = address

    def connect(self):
        raw_socket = socket.create_connection(
            (self.address, self.port),
            self.timeout,
            self.source_address,
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _validated_video_url(url):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("The direct file URL is malformed.") from error

    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or not parsed.path
        or parsed.fragment
    ):
        raise ValueError("Use a direct HTTPS video-file URL on the standard port.")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Use the creator's HTTPS hostname, not a raw IP address.")

    extension = Path(parsed.path).suffix.lower()
    if extension not in VIDEO_EXTENSIONS:
        raise ValueError("The URL must point directly to a supported video file.")

    try:
        records = socket.getaddrinfo(
            hostname.encode("idna").decode("ascii"),
            443,
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise ValueError("Could not resolve the direct video URL host.") from error

    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("The direct video URL must resolve only to public internet addresses.")

    request_target = urlunsplit(("", "", parsed.path, parsed.query, ""))
    filename = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(parsed.path).name)[:160]
    return hostname.encode("idna").decode("ascii"), addresses[0], request_target, filename


def _download_direct_video(url):
    hostname, address, request_target, filename = _validated_video_url(url)
    connection = _PinnedHTTPSConnection(hostname, address, timeout=20)
    temporary = tempfile.NamedTemporaryFile(
        prefix="study_archive_",
        suffix=Path(filename).suffix.lower(),
        delete=False,
    )
    file_path = temporary.name
    total = 0

    try:
        connection.request("GET", request_target, headers={"User-Agent": "StudyArchiveBot/1.0"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Direct file server returned HTTP {response.status}.")

        content_type = response.getheader("Content-Type", "").split(";", 1)[0].lower()
        if content_type and not content_type.startswith("video/") and content_type != "application/octet-stream":
            raise ValueError("The direct URL did not return a video file.")

        content_length = response.getheader("Content-Length")
        if content_length:
            try:
                content_length = int(content_length)
            except ValueError as error:
                raise ValueError("The video server returned an invalid file size.") from error
            if content_length > MAX_VIDEO_BYTES:
                raise ValueError("The video is larger than the 2 GB archive limit.")

        with temporary:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise ValueError("The video is larger than the 2 GB archive limit.")
                temporary.write(chunk)

        if total == 0:
            raise ValueError("The direct video file is empty.")
        return file_path, filename
    except Exception:
        temporary.close()
        if os.path.exists(file_path):
            os.remove(file_path)
        raise
    finally:
        connection.close()


async def _archive_uploaded_video(message, destination):
    source = message.reply_to_message
    if source.chat.id != message.chat.id or not (source.video or source.document):
        await message.reply_text("Reply to a video file in this private chat, then send `/archive`.")
        return

    media = source.video or source.document
    mime_type = getattr(media, "mime_type", "") or ""
    filename = getattr(media, "file_name", "") or ""
    extension = Path(filename).suffix.lower()
    if not mime_type.startswith("video/") and extension not in VIDEO_EXTENSIONS:
        await message.reply_text("Reply to a video file to archive it.")
        return

    if getattr(media, "file_size", 0) > MAX_VIDEO_BYTES:
        await message.reply_text("This video is larger than the 2 GB archive limit.")
        return

    status = await message.reply_text("📤 Copying the video to your study group...")
    try:
        result = await app.copy_message(destination, source.chat.id, source.id)
    except Exception:
        logger.exception("Could not copy an authorized video into the archive group")
        await status.edit_text(
            "Could not archive this video. Check that the bot can post in your configured group."
        )
        return

    if not result:
        await status.edit_text("Telegram did not confirm the archive copy.")
        return
    await status.edit_text("✅ Video archived in your configured study group.")


async def _archive_direct_video(message, destination, url):
    status = await message.reply_text("📥 Fetching the creator-provided video file...")
    file_path = None
    try:
        file_path, filename = await asyncio.to_thread(_download_direct_video, url)
        size = os.path.getsize(file_path)
        if Path(filename).suffix.lower() in {".mp4", ".m4v", ".mov"}:
            result = await app.send_video(
                destination,
                file_path,
                file_name=filename,
                caption=f"📚 Study archive: `{filename}`",
                supports_streaming=True,
                progress=progress_bar,
                progress_args=(
                    "📤 Uploading video...",
                    status,
                    time.time(),
                    file_path,
                    url,
                    new_progress_session(),
                ),
            )
        else:
            result = await app.send_document(
                destination,
                file_path,
                file_name=filename,
                caption=f"📚 Study archive: `{filename}`",
                progress=progress_bar,
                progress_args=(
                    "📤 Uploading video...",
                    status,
                    time.time(),
                    file_path,
                    url,
                    new_progress_session(),
                ),
            )
        if not result:
            raise RuntimeError("Telegram did not confirm the upload.")
        await status.edit_text(f"✅ Video archived in your study group ({size:,} bytes).")
    except (OSError, ValueError, http.client.HTTPException, RuntimeError) as error:
        logger.warning("Authorized video archive failed (%s)", type(error).__name__)
        await status.edit_text(f"Could not archive this video: {error}")
    except Exception:
        logger.exception("Unexpected error while archiving an authorized video")
        await status.edit_text(
            "Could not upload this video. Check the bot's group permissions and Telegram file-size limits."
        )
    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)


@app.on_message(filters.command("archive") & filters.private)
async def archive_command(_, message: Message):
    if not message.from_user:
        return

    destination = get_user_chat_id(message.from_user.id)
    if not destination:
        await message.reply_text(
            "Pehle `/settings` → **Set Chat ID** se apna study group configure karein."
        )
        return

    arguments = getattr(message, "command", None) or []
    if len(arguments) > 1:
        url = arguments[1].strip()
        if len(arguments) > 2:
            await message.reply_text("Direct video URL mein spaces nahi hone chahiye.")
            return
        await _archive_direct_video(message, destination, url)
    elif message.reply_to_message:
        await _archive_uploaded_video(message, destination)
    else:
        await message.reply_text(
            "Only archive videos you own or have permission to save.\n\n"
            "• Reply to a video you sent here with `/archive`\n"
            "• Or use `/archive <creator's direct HTTPS video-file URL>`\n\n"
            "YouTube watch links are not direct video-file URLs. For your own YouTube "
            "uploads, download them through YouTube Studio first, then send the file here."
        )
