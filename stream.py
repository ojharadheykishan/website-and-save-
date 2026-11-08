# safe_repo
# Extra module: post cloned media to a public channel and return a
# MX Player / VLC streamable link (t.me/Link09660/MSGID).
# This module does NOT modify any existing upload/download logic.

import asyncio
import logging
import os
import hashlib
import time
import uuid
from datetime import datetime
from pyrogram import filters
from safe_repo import app
from safe_repo.core.func import new_progress_session, progress_bar
from config import STREAM_CHANNEL, STREAM_CHANNEL_USERNAME, CLONE_LOG_CHANNEL
from safe_repo.core.media_links import (
    append_stream_link,
    find_stream_entry_by_content_hash,
    get_public_base_url,
    is_public_base_url_configured,
    save_stream_file,
    read_stream_links,
)
from safe_repo.web.study import build_public_study_url

logger = logging.getLogger(__name__)
LINK_PROGRESS_STICKERS = ("🎬", "🎞️", "🍿", "📽️", "✨")
LINK_PROGRESS_SPINNERS = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def format_progress_bar(percent, title="Processing", note="Please wait", frame=0):
    """Create a compact link-generation bar, independent of transfer progress."""
    percent = max(0, min(100, int(percent)))
    filled = max(1, int(round(percent / 10)))
    empty = 10 - filled
    bar = "█" * filled + "░" * empty
    return (
        f"{LINK_PROGRESS_SPINNERS[frame % len(LINK_PROGRESS_SPINNERS)]} "
        f"{LINK_PROGRESS_STICKERS[frame % len(LINK_PROGRESS_STICKERS)]} **{title}**\n"
        f"[{bar}] {percent}%\n"
        f"{note}"
    )


def format_failure_message(reason="Stream setup failed"):
    """Return a friendly failure message for link generation issues."""
    return (
        "⚠️ Stream link generate nahi ho paya.\n\n"
        f"Reason: {reason}\n\n"
        "Large files ke liye bot ko public stream channel mein post permission "
        "chahiye. Website player ke liye `PUBLIC_BASE_URL` bhi set hona chahiye."
    )


def get_archive_chat_ids():
    """Return chat IDs that should receive archived stream links."""
    configured_ids = []
    for raw_value in (os.environ.get("ARCHIVE_CHAT_ID"), os.environ.get("CLONE_LOG_CHANNEL"), str(CLONE_LOG_CHANNEL)):
        if not raw_value:
            continue
        for chunk in str(raw_value).replace(" ", "").split(","):
            if not chunk:
                continue
            try:
                configured_ids.append(int(chunk))
            except ValueError:
                configured_ids.append(chunk)
    return configured_ids


def extract_stream_metadata(message, fallback_title=None):
    """Extract a subject/description/title from an incoming media message."""
    caption = getattr(message, "caption", None) or getattr(message, "text", None) or ""
    caption = str(caption).strip()
    title = fallback_title or "Untitled"
    description = caption
    subject = "General"
    category = "General"
    folder = "General"
    date = datetime.now().strftime("%Y-%m-%d")

    if caption:
        lines = [line.strip() for line in caption.splitlines() if line.strip()]
        if lines:
            title = lines[0]
        for line in lines:
            label, separator, value = line.partition(":")
            if not separator:
                continue
            label = label.strip().lower()
            value = value.strip()
            if label in {"subject", "topic"} and value:
                subject = value[:120]
            elif label in {"class", "category"} and value:
                category = value[:120]
            elif label == "folder" and value:
                folder = value[:120]
            elif label == "date" and len(value) == 10:
                try:
                    date = datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
                except ValueError:
                    logger.warning("Ignoring invalid study date metadata: %s", value)

    return {
        "subject": subject,
        "category": category,
        "folder": folder,
        "description": description,
        "title": title[:300],
        "date": date,
    }


def calculate_content_hash(file_path):
    """Return a SHA-256 digest without loading the complete media file into memory."""
    digest = hashlib.sha256()
    with open(file_path, "rb") as media_file:
        for chunk in iter(lambda: media_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_media_fingerprint(message):
    """Return a stable Telegram file fingerprint when the file is not downloadable."""
    for field in ("video", "document", "photo", "audio", "animation", "sticker", "voice", "video_note"):
        media = getattr(message, field, None)
        if not media:
            continue
        if isinstance(media, (list, tuple)):
            media = media[-1] if media else None
        unique_id = getattr(media, "file_unique_id", None)
        if unique_id:
            return f"telegram:{unique_id}"
    return None


def build_local_file_name(message):
    """Create a safe local filename based on the incoming message."""
    ext = ".mp4"
    if getattr(message, "document", None) is not None:
        mime = getattr(message.document, "mime_type", "") or ""
        if "image" in mime:
            ext = ".jpg"
        elif "audio" in mime:
            ext = ".mp3"
        elif "pdf" in mime:
            ext = ".pdf"
        else:
            ext = ".bin"
    elif getattr(message, "video", None) is not None:
        ext = ".mp4"
    elif getattr(message, "photo", None) is not None:
        ext = ".jpg"

    msg_id = getattr(message, "message_id", None)
    if msg_id is None:
        msg_id = getattr(message, "id", None)
    if msg_id is None:
        msg_id = "unknown"

    return f"stream_{msg_id}_{uuid.uuid4().hex[:8]}{ext}"


def has_media_payload(message):
    """Return True for direct or forwarded media messages."""
    forward_origin = getattr(message, "forward_origin", None)
    is_forwarded = bool(
        getattr(message, "forwarded", False)
        or getattr(message, "is_forwarded", False)
        or forward_origin is not None
    )
    return bool(
        getattr(message, "media", False)
        or getattr(message, "video", None) is not None
        or getattr(message, "document", None) is not None
        or getattr(message, "photo", None) is not None
        or getattr(message, "audio", None) is not None
        or getattr(message, "animation", None) is not None
        or getattr(message, "sticker", None) is not None
        or is_forwarded
    )


async def download_media_payload(
    client,
    message,
    progress_sender=None,
    progress_message_id=None,
    progress_message=None,
):
    """Download media with a fallback for forwarded files."""
    file_name = build_local_file_name(message)
    media_file = None
    status_message = progress_message
    if status_message is None and progress_sender and progress_message_id:
        try:
            status_message = await app.get_messages(progress_sender, progress_message_id)
        except Exception as error:
            logger.warning("Could not load media progress message: %s", error)

    progress_args = (
        "📥 Downloading media...",
        status_message,
        time.time(),
        file_name,
        "",
        new_progress_session(),
    )

    try:
        media_file = await client.download_media(
            message,
            file_name=file_name,
            progress=progress_bar if status_message else None,
            progress_args=progress_args,
        )
    except Exception as error:
        logger.warning("Primary Telegram media download failed: %s", error)
        media_file = None

    if not media_file:
        try:
            media_file = await message.download(
                file_name=file_name,
                progress=progress_bar if status_message else None,
                progress_args=progress_args,
            )
        except Exception as error:
            logger.warning("Fallback Telegram media download failed: %s", error)
            media_file = None

    if progress_sender and progress_message_id and media_file:
        try:
            await app.edit_message_text(
                progress_sender,
                progress_message_id,
                format_progress_bar(100, "Download complete", "Preparing the website link"),
            )
        except Exception:
            pass

    return media_file


async def post_to_stream_channel(message):
    """Clone the given message to the public stream channel and return the streamable links.

    This is the fallback path for forwarded media and very large files when local
    caching is not possible or practical.
    """
    try:
        if message is None:
            return None

        forwarded = None
        for action in ("copy", "forward"):
            try:
                if action == "copy":
                    forwarded = await message.copy(STREAM_CHANNEL)
                else:
                    forwarded = await message.forward(STREAM_CHANNEL)
                if forwarded is not None:
                    break
            except Exception as inner_error:
                logger.debug(f"Stream channel {action} attempt failed: {inner_error}")

        if forwarded is None:
            return None
        msg_id = getattr(forwarded, "id", None)
        if msg_id is None:
            return None
        base = f"https://t.me/{STREAM_CHANNEL_USERNAME}/{msg_id}"
        return {
            "link": base,
            "embed": base + "?embed=1",
        }
    except Exception as e:
        logger.error(f"Stream channel post failed: {e}")
        return None


async def build_public_stream_link(message, media_file=None):
    """Build a public stream link from local cache when possible, else fall back to a Telegram channel post for large or unsupported files."""
    cache_error = None
    if not is_public_base_url_configured():
        cache_error = "PUBLIC_BASE_URL is not configured for the website player."
    elif media_file and os.path.exists(media_file):
        try:
            saved = await asyncio.to_thread(save_stream_file, media_file)
            if saved:
                return {
                    "source": "cache",
                    "player_url": saved["player_url"],
                    "stream_url": saved["stream_url"],
                    "token": saved["token"],
                    "file_path": saved["file_path"],
                }
            cache_error = "File exceeds MAX_STREAM_FILE_SIZE_MB or cannot be cached."
        except (OSError, ValueError) as error:
            cache_error = f"Local stream cache failed: {error}"
            logger.warning(cache_error)
    else:
        cache_error = "Telegram could not download the media for local website caching."

    result = await post_to_stream_channel(message)
    if result:
        return {
            "source": "channel",
            "player_url": result["embed"],
            "stream_url": result["link"],
            "token": None,
            "fallback_reason": cache_error,
        }
    if cache_error:
        raise RuntimeError(
            f"{cache_error} Telegram-channel fallback also failed. "
            f"Check bot posting access in @{STREAM_CHANNEL_USERNAME}."
        )
    raise RuntimeError(
        f"Could not post this media to @{STREAM_CHANNEL_USERNAME}. "
        "Check that the bot is an administrator with posting permission."
    )


def build_public_study_link(metadata=None, base_url=None):
    """Create a public study-site URL that can jump to the subject/date catalog view."""
    metadata = metadata or {}
    subject = str(metadata.get("subject") or "").strip() or None
    date = str(metadata.get("date") or "").strip() or None
    title = str(metadata.get("title") or "").strip() or None
    folder = str(metadata.get("folder") or "").strip() or None

    if not base_url:
        base_url = get_public_base_url()

    if subject and date and title:
        return build_public_study_url(base_url, subject=subject, date=date, q=title, folder=folder)

    if subject and date:
        return build_public_study_url(base_url, subject=subject, date=date, folder=folder)

    if subject:
        return build_public_study_url(base_url, subject=subject, folder=folder)

    return build_public_study_url(base_url, folder=folder)


def build_stream_reply_text(public_link, study_url=None):
    """Build a Telegram reply that includes both player and website links."""
    lines = [
        "✅ **Stream link ready**",
        "",
        f"📺 Player: {public_link['player_url']}",
        f"🔗 Direct Stream: {public_link['stream_url']}",
    ]
    if public_link.get("source") == "channel":
        lines.extend([
            "",
            "ℹ️ File is served from the public Telegram channel because local website "
            "caching was unavailable. VLC/MX Player can open the direct link.",
        ])
    if study_url:
        lines.extend([
            "",
            f"🌐 Website link: {study_url}",
            "Is website link par click karke subject/date wise videos dekh sakte ho.",
        ])
    else:
        lines.extend([
            "",
            "🌐 Website link abhi generate nahi ho saka."
        ])
    return "\n".join(lines)


async def archive_stream_link(message, player_url, stream_url, study_url=None):
    """Send the generated stream links to the configured archive chats."""
    try:
        text = (
            "🎬 **Stream link ready**\n\n"
            f"📺 Player: {player_url}\n"
            f"🔗 Stream: {stream_url}"
        )
        if study_url:
            text = text + f"\n🌐 Study site: {study_url}"
        for chat_id in get_archive_chat_ids():
            if not chat_id:
                continue
            try:
                await app.send_message(chat_id=chat_id, text=text)
            except Exception as inner_error:
                logger.warning(f"Failed to archive stream link to {chat_id}: {inner_error}")
    except Exception as e:
        logger.warning(f"archive_stream_link failed: {e}")


async def send_stream_link(sender, message, caption_prefix="🎬 **Stream Link:**"):
    """Post the uploaded message to the public stream channel and send the
    streamable link(s) back to the user as an extra message.
    """
    try:
        result = await post_to_stream_channel(message)
        if not result:
            # Post failed (likely bot is not admin in the public channel).
            await app.send_message(
                chat_id=sender,
                text="⚠️ **Stream link generate nahi ho paya.**\n"
                     f"Bot ko channel @{STREAM_CHANNEL_USERNAME} mein **admin** banaayein "
                     "(post permission ke sath) taaki stream link mile.",
            )
            return
        text = (
            f"{caption_prefix}\n"
            f"🔗 Direct link: {result['link']}\n\n"
            "Is link ko VLC / MX Player ya kisi bhi media player mein open kar sakte ho."
        )
        await app.send_message(chat_id=sender, text=text)
    except Exception as e:
        logger.error(f"send_stream_link failed: {e}")


@app.on_message(filters.media | filters.forwarded)
async def handle_direct_media(client, message):
    """Generate a public stream link for any media sent directly to the bot."""
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    status_message = None
    media_file = None
    try:
        if not has_media_payload(message):
            return

        if chat_id is None:
            raise ValueError("The incoming media message has no chat ID.")
        status_message = await app.send_message(
            chat_id,
            format_progress_bar(15, "Processing media", "Starting download"),
        )
        media_file = await download_media_payload(
            client,
            message,
            progress_sender=chat_id,
            progress_message_id=status_message.id,
            progress_message=status_message,
        )
        content_hash = (
            await asyncio.to_thread(calculate_content_hash, media_file)
            if media_file
            else get_media_fingerprint(message)
        )
        existing_entry = await asyncio.to_thread(
            find_stream_entry_by_content_hash, content_hash
        )
        if existing_entry:
            if media_file and os.path.exists(media_file):
                os.remove(media_file)
            await app.edit_message_text(
                chat_id,
                status_message.id,
                "♻️ Ye media pehle se study library mein hai.\n"
                f"🌐 Existing link: {existing_entry.get('player_url', 'N/A')}",
            )
            return

        if not media_file:
            await app.edit_message_text(
                chat_id,
                status_message.id,
                format_progress_bar(
                    55,
                    "Trying channel fallback",
                    "Download nahi hua; public channel par media copy kar rahe hain",
                ),
            )
        else:
            await app.edit_message_text(
                chat_id,
                status_message.id,
                format_progress_bar(60, "Preparing stream link", "Generating public link"),
            )
        link_task = asyncio.create_task(build_public_stream_link(message, media_file))
        frame = 0
        while not link_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(link_task), timeout=0.8)
            except asyncio.TimeoutError:
                frame += 1
                await app.edit_message_text(
                    chat_id,
                    status_message.id,
                    format_progress_bar(
                        min(60 + frame, 95),
                        "Preparing stream link",
                        "Generating public link",
                        frame=frame,
                    ),
                )
        public_link = await link_task

        metadata = extract_stream_metadata(
            message,
            fallback_title=os.path.basename(media_file) if media_file else "Telegram media",
        )
        study_url = (
            build_public_study_link(metadata)
            if is_public_base_url_configured()
            else None
        )
        catalog_result = await asyncio.to_thread(
            append_stream_link,
            public_link['player_url'],
            public_link['stream_url'],
            label="direct_media",
            subject=metadata['subject'],
            description=metadata['description'],
            title=metadata['title'],
            token=public_link.get('token'),
            category=metadata['category'],
            folder=metadata['folder'],
            date=metadata['date'],
            content_hash=content_hash,
        )
        if isinstance(catalog_result, dict) and catalog_result.get("duplicate"):
            existing_entry = catalog_result.get("entry", {})
            for path in (media_file, public_link.get("file_path")):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        logger.warning("Could not remove duplicate media cache file %s", path)
            await app.edit_message_text(
                chat_id,
                status_message.id,
                "♻️ This media is already in your study library.\n"
                f"🌐 Existing link: {existing_entry.get('player_url', public_link['player_url'])}",
            )
            return
        await archive_stream_link(message, public_link['player_url'], public_link['stream_url'], study_url=study_url)

        text = build_stream_reply_text(public_link, study_url=study_url)
        await app.edit_message_text(chat_id, status_message.id, text)

        try:
            os.remove(media_file)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"handle_direct_media failed: {e}")
        if status_message:
            await app.edit_message_text(
                chat_id,
                status_message.id,
                format_failure_message(str(e)),
            )
        elif chat_id is not None:
            await app.send_message(chat_id, f"⚠️ Error: {str(e)}")
    finally:
        if media_file and os.path.exists(media_file):
            try:
                os.remove(media_file)
            except OSError as cleanup_error:
                logger.warning("Could not remove temporary media file %s: %s", media_file, cleanup_error)


@app.on_message(filters.command("links"))
async def export_stream_links(_, message):
    """Send a text file containing all generated stream links."""
    try:
        content = read_stream_links()
        if not content.strip():
            await app.send_message(message.chat.id, "📄 Abhi tak koi stream link archive nahi hui.")
            return

        archive_path = os.path.join(os.getcwd(), "stream_links.txt")
        with open(archive_path, "w", encoding="utf-8") as handle:
            handle.write(content)

        await app.send_document(
            chat_id=message.chat.id,
            document=archive_path,
            caption="📄 Generated stream links archive",
        )
    except Exception as e:
        logger.error(f"export_stream_links failed: {e}")
        await app.send_message(message.chat.id, f"⚠️ Error: {str(e)}")


@app.on_message(filters.command("exportlinks"))
async def export_stream_links_alias(_, message):
    await export_stream_links(_, message)
