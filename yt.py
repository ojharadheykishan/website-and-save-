import asyncio
import html
import logging
import re
from urllib.parse import parse_qs, urlparse

import requests
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from safe_repo import app
from config import OWNER_ID, YOUTUBE_API_KEY
from safe_repo.core.media_links import add_youtube_catalog_entry, get_public_base_url

logger = logging.getLogger(__name__)
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def parse_youtube_video_id(value):
    value = (value or "").strip()
    if not value:
        return None
    if "://" not in value:
        value = f"https://{value}"

    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in YOUTUBE_HOSTS
        or parsed.username
        or parsed.password
        or port is not None
    ):
        return None

    if parsed.hostname.endswith("youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
    else:
        segments = [segment for segment in parsed.path.split("/") if segment]
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(segments) == 2 and segments[0] in {"shorts", "embed", "live"}:
            video_id = segments[1]
        else:
            return None

    return video_id if VIDEO_ID_PATTERN.fullmatch(video_id) else None


def _request_oembed(video_id):
    response = requests.get(
        "https://www.youtube.com/oembed",
        params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected YouTube preview response")
    return data


def _request_search(query):
    response = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "type": "video",
            "maxResults": 5,
            "q": query,
            "key": YOUTUBE_API_KEY,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Unexpected YouTube search response")
    return data


@app.on_message(filters.command("yt"))
async def youtube_command(_, message):
    arguments = getattr(message, "command", None) or []
    query = " ".join(arguments[1:]).strip()
    if not query:
        await message.reply_text(
            "Usage:\n"
            "• `/yt <YouTube video link>` — show its title and official link\n"
            "• `/yt <search words>` — search YouTube (requires `YOUTUBE_API_KEY`)",
            disable_web_page_preview=True,
        )
        return

    video_id = parse_youtube_video_id(query)
    if video_id:
        if not message.from_user or message.from_user.id not in OWNER_ID:
            await message.reply_text("Public website catalog mein videos add karne ki permission nahi hai.")
            return
        await _publish_youtube_embed(message, video_id)
        return

    if len(query) > 200:
        await message.reply_text("Search query bahut lambi hai; 200 characters tak rakhein.")
        return

    if not YOUTUBE_API_KEY:
        await message.reply_text(
            "YouTube search abhi configure nahi hai. Search ke liye deployment environment mein "
            "`YOUTUBE_API_KEY` set karein, ya `/yt <YouTube video link>` se preview dekhein.",
            disable_web_page_preview=True,
        )
        return

    try:
        data = await asyncio.to_thread(_request_search, query)
        items = data.get("items")
        if not isinstance(items, list):
            items = []
    except (requests.RequestException, ValueError) as error:
        logger.warning("YouTube search request failed (%s)", type(error).__name__)
        await message.reply_text(
            "YouTube search nahi ho payi. `YOUTUBE_API_KEY`, API access, aur quota check karein."
        )
        return

    if not items:
        await message.reply_text("Is search ke liye koi video nahi mila.")
        return

    lines = [f"🔎 <b>YouTube results:</b> {html.escape(query)}"]
    buttons = []
    for index, item in enumerate(items[:5], start=1):
        if not isinstance(item, dict):
            continue
        video_data = item.get("id")
        snippet = item.get("snippet")
        if not isinstance(video_data, dict):
            continue
        if not isinstance(snippet, dict):
            snippet = {}
        video_id = video_data.get("videoId", "")
        if not VIDEO_ID_PATTERN.fullmatch(video_id):
            continue
        title = html.escape(str(snippet.get("title") or "Untitled video")[:200])
        channel = html.escape(str(snippet.get("channelTitle") or "YouTube channel")[:100])
        link = f"https://www.youtube.com/watch?v={video_id}"
        lines.append(f"\n<b>{index}.</b> {title}\n👤 {channel}")
        buttons.append([InlineKeyboardButton(f"▶ Open result {index}", url=link)])

    if not buttons:
        await message.reply_text("YouTube search ne koi valid video link return nahi kiya.")
        return

    await message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _publish_youtube_embed(message, video_id):
    try:
        metadata = await asyncio.to_thread(_request_oembed, video_id)
    except (requests.RequestException, ValueError) as error:
        logger.warning("YouTube preview request failed (%s)", type(error).__name__)
        await message.reply_text(
            "Video preview nahi mil paya. Link check karein; video private ya unavailable ho sakta hai."
        )
        return

    base_url = get_public_base_url()
    if urlparse(base_url).hostname in {"localhost", "127.0.0.1", "::1"}:
        await message.reply_text(
            "Website ka public URL configured nahi hai. Deployment mein `PUBLIC_BASE_URL` set karein, phir dobara try karein."
        )
        return

    try:
        result = await asyncio.to_thread(
            add_youtube_catalog_entry,
            video_id,
            str(metadata.get("title") or "YouTube video"),
            str(metadata.get("author_name") or "YouTube channel"),
            base_url,
        )
    except (OSError, ValueError) as error:
        logger.warning("Could not publish YouTube embed (%s)", type(error).__name__)
        await message.reply_text(
            "Video ko website catalog mein add nahi kar paye. Catalog storage check karke dobara try karein."
        )
        return

    title = html.escape(str(metadata.get("title") or "YouTube video")[:300])
    author = html.escape(str(metadata.get("author_name") or "YouTube channel")[:150])
    await message.reply_text(
        f"✅ <b>YouTube folder mein add ho gaya</b>\n\n"
        f"🎬 {title}\n👤 {author}\n\n"
        f"Website: {html.escape(result['player_url'])}\n"
        f"Original video YouTube par hi play hoga; bot video download ya re-upload nahi karta."
        + ("" if result["added"] else "\n\nYe video pehle se catalog mein tha."),
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("▶ Website par dekhein", url=result["player_url"])]]
        ),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
