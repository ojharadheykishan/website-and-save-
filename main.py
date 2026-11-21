#safe_repo

import time
import asyncio
import os
from urllib.parse import urlparse
from pyrogram import filters, Client, raw
from safe_repo import app
from config import (
    API_ID,
    API_HASH,
    DEFAULT_DESTINATION_ID,
    MAX_CONCURRENT_TRANSMISSIONS,
    OWNER_ID,
)
from safe_repo.core.get_func import (
    get_msg,
    get_user_chat_id,
    get_active_named_destination,
    load_named_destinations,
    media_duration,
    load_delete_words,
    load_replacement_words,
    remove_named_destination,
    save_delete_words,
    save_named_destination,
    save_replacement_words,
    save_user_chat_id,
    select_named_destination,
    set_caption_command,
    set_rename_command,
)
from safe_repo.core.func import *
from safe_repo.core.func import parse_telegram_link, set_progress_sticker_file_id
from safe_repo.core.mongo import db
from pyrogram.errors import FloodWait, SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered, MessageIdInvalid
from safe_repo.core import script
from safe_repo.core.media_links import get_public_base_url
from safe_repo.web.study import build_public_study_url, load_catalog_entries
from safe_repo.core.workers import get_worker_manager, register_handler
import logging
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def safe_edit(message, text, **kwargs):
    """Safely edit a message; if MessageIdInvalid, send a new message instead."""
    try:
        return await message.edit_text(text, **kwargs)
    except MessageIdInvalid:
        chat_id = message.chat.id if hasattr(message, "chat") else None
        reply_to = message.id if hasattr(message, "id") else None
        if chat_id:
            return await app.send_message(chat_id, text, reply_to_message_id=reply_to, **kwargs)
        return None
    except Exception:
        logger.exception("Failed to edit message")
        return None


@app.on_message(
    filters.command("setprogresssticker")
    & filters.private
    & filters.user(OWNER_ID)
)
async def set_progress_sticker(_, message):
    command_parts = (message.text or "").split(maxsplit=1)
    if len(command_parts) == 2 and command_parts[1].strip().lower() in {
        "off",
        "disable",
    }:
        try:
            set_progress_sticker_file_id(None)
        except OSError as error:
            logger.exception("Could not save disabled progress sticker setting.")
            await message.reply_text(f"Could not save the setting: {error}")
            return
        await message.reply_text("Progress sticker disabled.")
        return

    sticker_message = getattr(message, "reply_to_message", None)
    sticker = getattr(sticker_message, "sticker", None)
    if sticker is None:
        await message.reply_text(
            "Reply to an animated Telegram sticker with `/setprogresssticker`.\n"
            "To disable it, send `/setprogresssticker off`."
        )
        return
    if not (getattr(sticker, "is_animated", False) or getattr(sticker, "is_video", False)):
        await message.reply_text(
            "That is a static sticker. Reply to an animated (TGS) or video (WEBM) sticker."
        )
        return

    file_id = getattr(sticker, "file_id", None)
    if not file_id:
        await message.reply_text("Telegram did not provide a sticker file ID.")
        return

    try:
        set_progress_sticker_file_id(file_id)
    except OSError as error:
        logger.exception("Could not persist the progress sticker setting.")
        await message.reply_text(
            f"Sticker received, but its setting could not be saved: {error}\n"
            "Check the progress-sticker settings path and Railway volume access."
        )
        return
    await message.reply_text(
        "✅ Animated progress sticker saved. It will appear under transfer progress "
        "messages and will be removed when the transfer completes."
    )


async def _get_forum_topics(chat_id):
    peer = await app.resolve_peer(chat_id)
    channel_id = getattr(peer, "channel_id", None)
    access_hash = getattr(peer, "access_hash", None)
    if channel_id is None or access_hash is None:
        raise RuntimeError("The destination is not a resolvable supergroup channel.")

    channel = raw.types.InputChannel(
        channel_id=channel_id,
        access_hash=access_hash,
    )
    result = await app.invoke(
        raw.functions.channels.GetForumTopics(
            channel=channel,
            offset_date=0,
            offset_id=0,
            offset_topic=0,
            limit=100,
        )
    )
    return list(getattr(result, "topics", ()))


async def _get_forum_topic(chat_id, topic_id):
    peer = await app.resolve_peer(chat_id)
    channel_id = getattr(peer, "channel_id", None)
    access_hash = getattr(peer, "access_hash", None)
    if channel_id is None or access_hash is None:
        raise RuntimeError("The destination is not a resolvable supergroup channel.")

    result = await app.invoke(
        raw.functions.channels.GetForumTopicsByID(
            channel=raw.types.InputChannel(
                channel_id=channel_id,
                access_hash=access_hash,
            ),
            topics=[int(topic_id)],
        )
    )
    return next(
        (
            topic
            for topic in getattr(result, "topics", ())
            if int(getattr(topic, "id", -1)) == int(topic_id)
        ),
        None,
    )



@app.on_message(
    filters.regex(r'https?://[^\s]+')
    & ~filters.command(["batch", "yt"])
    & ~filters.reply
    & ~filters.media
)
async def single_link(_, message):
    if getattr(message, "media", None):
        return
    if not message.from_user or message.chat.id != message.from_user.id:
        await message.reply_text("Please send media links in a private chat with the bot.")
        return
    user_id = message.from_user.id
    link = get_message_link(message)
    if not link:
        await message.reply_text("Please send a valid Telegram message link.")
        return

    queue_status = await message.reply_text("⏳ Adding your media link to the download queue...")
    try:
        manager = get_worker_manager()
        await manager.submit(
            "downloads",
            "single_link",
            {
                "user_id": user_id,
                "chat_id": message.chat.id,
                "request_message_id": message.id,
                "status_message_id": queue_status.id,
                "link": link,
            },
        )
        counts = await manager.counts(user_id)
        await safe_edit(queue_status,
            f"⏳ Added to your queue. {counts['pending']} waiting; "
            f"{counts['running']} processing."
        )
    except Exception as error:
        logger.exception("Could not enqueue single-link task for user %s", user_id)
        await safe_edit(queue_status, f"⚠️ Could not queue this link: {error}")


async def _process_single_link(client, message, link, status_message):
    user_id = message.from_user.id
    userbot = None
    try:
        join = await subscribe(client, message)
        if join == 1:
            return

        msg = status_message
        data = await db.get_data(user_id)
        
        if data and data.get("session"):
            session = data.get("session")
            try:
                userbot = Client(
                    ":userbot:",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session,
                    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
                )
                await userbot.start()                
            except (SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered):
                return await safe_edit(msg, "Login expired /login again...")
            except Exception as e:
                logger.error(f"Session start error: {e}")
                return await safe_edit(msg, "Failed to start session. Please try again.")
        else:
            await safe_edit(msg, "Login in bot first ...")
            return

        try:
            # Invite links (t.me/+, telegram.me/+) -> join the chat
            if '/+' in link or 'joinchat/' in link:
                q = await userbot_join(userbot, link)
                await safe_edit(msg, q)
            else:
                for attempt in range(3):
                    result = await get_msg(userbot, user_id, msg.id, link, 0, message, False)
                    if result != "retry":
                        break
                    if attempt < 2:
                        await safe_edit(msg, f"⚠️ Temporary error. Retrying ({attempt + 1}/2)...")
                        await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"Processing error: {e}")
            await safe_edit(msg, f"Link: `{link}`\n\n**Error:** {str(e)}")
                     
    except FloodWait as fw:
        await safe_edit(msg, f'Try again after {fw.x} seconds due to floodwait from telegram.')
    except Exception as e:
        logger.error(f"Main error: {e}")
        await app.send_message(user_id, f"Link: `{link}`\n\n**Error:** {str(e)}")
    finally:
        # Ensure userbot is properly disconnected
        if userbot:
            try:
                await userbot.stop()
            except:
                pass


users_loop = {}
link_queue_locks = {}


async def _run_single_link_job(payload):
    user_id = int(payload["user_id"])
    chat_id = int(payload["chat_id"])
    request_message = await app.get_messages(chat_id, int(payload["request_message_id"]))
    status_message = await app.get_messages(chat_id, int(payload["status_message_id"]))
    if not request_message or not status_message:
        raise RuntimeError("The queued request or its status message is no longer available.")
    await safe_edit(status_message, "Processing...")
    await _process_single_link(app, request_message, payload["link"], status_message)


register_handler("single_link", _run_single_link_job)


@app.on_message(filters.command("queue") & filters.private)
async def show_queue(_, message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    counts = await get_worker_manager().counts(user_id)
    await message.reply_text(
        f"📥 **Queue Status (Real-Time)**\n\n"
        f"⏳ Waiting: {counts['pending']}\n"
        f"🔄 Processing: {counts['running']}\n"
        f"✅ Completed: {counts['done']}\n"
        f"❌ Failed: {counts['failed']}\n"
        f"🛑 Cancelled: {counts['cancelled']}\n\n"
        f"💡 Multiple links now process simultaneously (no waiting in line)."
    )


@app.on_message(filters.command("status") & filters.private)
async def show_status(_, message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    counts = await get_worker_manager().counts(user_id)
    active = counts['running']
    completed = counts['done']
    total_active = active + completed
    await message.reply_text(
        f"📊 **Real-Time Processing Status**\n\n"
        f"🔄 Currently Processing: {active}\n"
        f"✅ Completed: {completed}\n"
        f"⏳ Waiting: {counts['pending']}\n"
        f"❌ Failed: {counts['failed']}\n"
        f"📋 Total Active Jobs: {total_active}\n\n"
        f"{'🚀 All links processing concurrently!' if active > 0 else '✅ No active processing.'}"
    )


@app.on_message(filters.command("dest") & filters.private)
async def manage_destinations(_, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    command = getattr(message, "command", []) or []
    action = command[1].lower() if len(command) > 1 else "list"
    destinations = load_named_destinations(user_id)

    try:
        if action == "add" and len(command) == 4:
            name, reference = command[2], command[3]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
                raise ValueError("Name must be 1-32 letters, numbers, underscores, or hyphens.")
            try:
                reference = int(reference)
            except ValueError:
                reference = reference if reference.startswith("@") else f"@{reference}"
            target = await app.get_chat(reference)
            chat_type = str(target.type).lower().split(".")[-1]
            if chat_type not in {"group", "supergroup", "channel"}:
                raise ValueError("Choose a group, supergroup, or channel.")
            save_named_destination(user_id, name, target.id)
            await message.reply_text(f"✅ Saved destination **{name}**: {target.title or target.id}")
        elif action == "use" and len(command) == 3:
            select_named_destination(user_id, command[2])
            await message.reply_text(f"✅ Active destination set to **{command[2]}**.")
        elif action == "remove" and len(command) == 3:
            remove_named_destination(user_id, command[2])
            await message.reply_text(f"✅ Removed destination **{command[2]}**.")
        elif action == "list" and len(command) == 2:
            if not destinations:
                await message.reply_text(
                    "No saved destinations yet.\nUse `/dest add <name> <chat_id_or_username>`."
                )
                return
            active = get_active_named_destination(user_id)
            lines = [
                f"{'✅' if name == active else '▫️'} **{name}** — `{chat_id}`"
                for name, chat_id in destinations.items()
            ]
            await message.reply_text("\n".join(lines))
        else:
            await message.reply_text(
                "Commands:\n"
                "`/dest add <name> <chat_id_or_username>`\n"
                "`/dest use <name>`\n"
                "`/dest list`\n"
                "`/dest remove <name>`"
            )
    except ValueError as error:
        await message.reply_text(f"⚠️ {error}")
    except Exception:
        logger.exception("Could not manage destination for user %s", user_id)
        await message.reply_text(
            "⚠️ Couldn't access that destination. Confirm the username/chat ID and add the bot with posting permission."
        )


@app.on_message(filters.command("study") & filters.private)
async def study_search_command(_, message):
    command = getattr(message, "command", []) or []
    if len(command) < 3 or command[1].lower() not in {"search", "folder"}:
        await message.reply_text(
            "Use `/study search <words>` or `/study folder <folder_name>`."
        )
        return

    mode = command[1].lower()
    value = " ".join(command[2:]).strip()
    videos = load_catalog_entries()
    if mode == "search":
        matching = [
            video for video in videos
            if value.casefold() in " ".join(
                str(video.get(key) or "")
                for key in ("title", "subject", "description", "category")
            ).casefold()
        ]
        url = build_public_study_url(get_public_base_url(), q=value)
    else:
        matching = [
            video for video in videos
            if str(video.get("folder") or "General").casefold() == value.casefold()
        ]
        url = build_public_study_url(get_public_base_url(), folder=value)

    lines = [f"📚 Found **{len(matching)}** matching item(s).", f"🌐 [Open on study site]({url})"]
    for video in matching[:5]:
        title = str(video.get("title") or "Untitled")
        player_url = str(video.get("player_url") or "")
        if player_url:
            lines.append(f"• [{title}]({player_url})")
    await message.reply_text("\n".join(lines), disable_web_page_preview=True)


@app.on_message(filters.command("thumbpreview") & filters.private)
async def thumbnail_preview_command(_, message):
    source = message.reply_to_message
    if source is None or not message.from_user:
        await message.reply_text("Reply to a video with `/thumbpreview` to preview its upload thumbnail.")
        return

    media = getattr(source, "video", None) or getattr(source, "document", None)
    mime_type = str(getattr(media, "mime_type", "") or "")
    filename = str(getattr(media, "file_name", "") or "")
    if not media or (not mime_type.startswith("video/") and not filename.lower().endswith(
        (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mpeg", ".mpg")
    )):
        await message.reply_text("Reply to a video file, then send `/thumbpreview`.")
        return

    status = await message.reply_text("🖼️ Creating a thumbnail preview...")
    media_path = None
    thumb_path = None
    try:
        media_path = await source.download()
        if not media_path:
            raise RuntimeError("Telegram did not provide a local video file.")
        thumb_path = await screenshot(media_path, media_duration(source), message.from_user.id)
        if not thumb_path:
            raise RuntimeError("Couldn't extract a readable frame from this video.")
        await app.send_photo(
            message.chat.id,
            thumb_path,
            caption="✅ This is the thumbnail that will be used for your video upload.",
            reply_to_message_id=source.id,
        )
        await status.delete()
    except Exception as error:
        logger.exception("Thumbnail preview failed for user %s", message.from_user.id)
        await safe_edit(status, f"⚠️ Thumbnail preview failed: {error}")
    finally:
        for path in (media_path, thumb_path):
            if path and os.path.exists(path) and path != f"{message.from_user.id}.jpg":
                os.remove(path)

@app.on_message(filters.command("settings"))
async def settings_command(_, message):
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    buttons = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Set Chat ID", callback_data='setchat'), InlineKeyboardButton("Set Rename Tag", callback_data='setrename')],
            [InlineKeyboardButton("Caption", callback_data='setcaption'), InlineKeyboardButton("Replace Words", callback_data='setreplacement')],
            [InlineKeyboardButton("Remove Words", callback_data='delete'), InlineKeyboardButton("Reset", callback_data='reset')],
            [InlineKeyboardButton("Login", callback_data='addsession'), InlineKeyboardButton("Logout", callback_data='logout')],
            [InlineKeyboardButton("Set Thumbnail", callback_data='setthumb'), InlineKeyboardButton("Remove Thumbnail", callback_data='remthumb')],
            [InlineKeyboardButton("Report Errors", url="https://t.me/safe_repo")]
        ]
    )
    
    await message.reply_text(
        text="Customize by your end and Configure your settings ...",
        reply_markup=buttons
    )


@app.on_message(filters.command("cmdhelp"))
async def cmd_help_command(_, message):
    await message.reply_text(text=script.CMD_HELP_TXT, disable_web_page_preview=True)


@app.on_message(filters.command("howtocopy"))
async def how_to_copy_command(_, message):
    copy_guide = """
🎯 **HOW TO COPY MEDIA USING THIS BOT** 🎯

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 **STEP BY STEP GUIDE:**

**1️⃣ LOGIN FIRST (REQUIRED):**
• Send: `/login`
• Follow the instructions to login with your session
• This allows bot to access your private channels

**2️⃣ COPY SINGLE MEDIA:**
• Just paste any Telegram link directly:
  📹 `https://t.me/channel_name/12345`
  🔒 `https://t.me/c/channel_id/12345`
  🤖 `https://t.me/b/bot_username/12345`

**3️⃣ COPY MULTIPLE MEDIA (BATCH):**
• Send: `/batch`
• Bot will ask for start link: `https://t.me/c/12345/100`
• Bot will ask for end link: `https://t.me/c/12345/105`
• Choose whether to send the copied media to a supergroup
• Bot downloads messages 100-105 automatically

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🎬 **SUPPORTED CONTENT TYPES:**
• Videos (MP4, MKV, AVI, etc.)
• Photos & Images
• Documents (PDF, DOC, ZIP, etc.)
• Audio files
• Text messages
• Stickers & Animations

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⚙️ **CUSTOMIZE YOUR OUTPUT:**
• Use `/settings` to configure:
  📝 Custom captions
  🖼️ Thumbnails
  📁 File renaming
  🎯 Target chat ID

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 **TIPS FOR SUCCESS:**

✅ **For Private Channels:**
• Must be logged in with `/login`
• Your account must have access to the channel

✅ **For Public Channels:**
• Works without login
• Just paste the link directly

✅ **For Large Files:**
• Be patient, downloads may take time
• Premium users get faster processing

✅ **For Batch Processing:**
• Use `/cancel` to stop if needed
• Free users: max 100 messages
• Premium users: unlimited

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ **COMMON ISSUES & SOLUTIONS:**

🔸 "Login expired" → Use `/login` again
🔸 "Have you joined the channel?" → Login with correct session
🔸 "Floodwait" → Wait for the specified time
🔸 "File too large" → Contact admin for help

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📞 **NEED HELP?**
Contact: @Radheyojha096

💝 **All copied media includes:**
"BY @Radheyojha096" credit

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🚀 **START NOW:**
1. Send `/login` to login
2. Send `/settings` to customize
3. Paste any media link to copy!

__**Powered by safe_repo**__
"""
    await message.reply_text(copy_guide, disable_web_page_preview=True)


@app.on_callback_query(filters.regex(r'^(setchat|setrename|setcaption|setreplacement|addsession|delete|logout|setthumb|reset|remthumb)$'))
async def callback_query_handler(_, callback_query):
    user_id = callback_query.from_user.id

    async def ask_for_input(prompt, input_filter=filters.text):
        return await app.ask(
            callback_query.message.chat.id,
            text=prompt,
            filters=input_filter,
            user_id=user_id,
            timeout=300,
        )

    await callback_query.answer()
    try:
        if callback_query.data == 'setchat':
            response = await ask_for_input("Send a chat ID or public @username for the output destination.")
            reference = (response.text or "").strip()
            if not reference:
                raise ValueError("Please send a valid chat ID or username.")
            try:
                reference = int(reference)
            except ValueError:
                reference = reference if reference.startswith("@") else f"@{reference}"
            target = await app.get_chat(reference)
            save_user_chat_id(user_id, target.id)
            await response.reply_text(f"✅ Output destination saved: {target.title or target.id}")
        elif callback_query.data == 'setrename':
            response = await ask_for_input("Send the filename prefix to use for future downloads.")
            tag = (response.text or "").strip()
            if not tag:
                raise ValueError("Filename prefix cannot be empty.")
            await set_rename_command(user_id, tag)
            await response.reply_text("✅ Filename prefix saved.")
        elif callback_query.data == 'setcaption':
            response = await ask_for_input("Send the caption text to add to copied media.")
            await set_caption_command(user_id, (response.text or "").strip())
            await response.reply_text("✅ Caption saved.")
        elif callback_query.data == 'setreplacement':
            response = await ask_for_input("Send replacement in this format: old text => new text")
            value = (response.text or "").strip()
            if "=>" not in value:
                raise ValueError("Use this format: old text => new text")
            old_text, new_text = (part.strip() for part in value.split("=>", 1))
            if not old_text:
                raise ValueError("Old text cannot be empty.")
            replacements = load_replacement_words(user_id)
            replacements[old_text] = new_text
            save_replacement_words(user_id, replacements)
            await response.reply_text("✅ Replacement saved.")
        elif callback_query.data == 'delete':
            response = await ask_for_input("Send words separated by spaces to remove them from captions.")
            words = set((response.text or "").split())
            if not words:
                raise ValueError("Send at least one word.")
            save_delete_words(user_id, load_delete_words(user_id) | words)
            await response.reply_text("✅ Caption removal words saved.")
        elif callback_query.data == 'addsession':
            await callback_query.message.reply_text("Use /login to connect your Telegram account.")
        elif callback_query.data == 'logout':
            from safe_repo.modules.login import delete_session_files
            files_deleted = await delete_session_files(user_id)
            if files_deleted:
                await callback_query.message.reply_text("✅ Login session cleared.")
            else:
                await callback_query.message.reply_text("ℹ️ No saved login session was found.")
        elif callback_query.data == 'setthumb':
            response = await ask_for_input("Send the thumbnail photo now.", filters.photo)
            saved_path = await response.download(file_name=f"{user_id}.jpg")
            if not saved_path:
                raise ValueError("Could not save that photo.")
            await response.reply_text("✅ Thumbnail saved.")
        elif callback_query.data == 'reset':
            save_delete_words(user_id, set())
            save_replacement_words(user_id, {})
            await set_caption_command(user_id, "")
            await set_rename_command(user_id, "safe_repo")
            await callback_query.message.reply_text("✅ Caption, replacement, and filename settings reset.")
        elif callback_query.data == 'remthumb':
            import os
            try:
                os.remove(f"{user_id}.jpg")
                await callback_query.message.reply_text("✅ Thumbnail removed.")
            except FileNotFoundError:
                await callback_query.message.reply_text("ℹ️ No thumbnail was saved.")
    except asyncio.TimeoutError:
        await callback_query.message.reply_text("⌛ Settings action timed out. Open /settings to try again.")
    except Exception as error:
        logger.exception("Settings action %s failed", callback_query.data)
        await callback_query.message.reply_text(f"⚠️ Could not update that setting: {error}")


@app.on_message(filters.command("batch"))
async def batch_link(_, message):
    if not message.from_user or message.chat.id != message.from_user.id:
        await message.reply_text("Please run /batch in a private chat with the bot.")
        return
    user_id = message.from_user.id
    
    text = message.text.strip()
    parts = text.split()
    
    if len(parts) >= 3:
        start_link = parts[1]
        end_link = parts[2]
        start_id = get_link(start_link)
        if not start_id:
            await app.send_message(message.chat.id, "Invalid start link format. Please send a valid Telegram message link.")
            return
        last_id = get_link(end_link)
        if not last_id:
            await app.send_message(message.chat.id, "Invalid end link format. Please send a valid Telegram message link.")
            return
    else:
        start = await app.ask(message.chat.id, text="Please send the start link.")
        # Check if user sent a valid message with text
        if not start.text or not start.text.strip():
            await app.send_message(message.chat.id, "No link provided. Please send a valid Telegram message link.")
            return
        
        start_id = get_link(start.text)
        if not start_id:
            await app.send_message(message.chat.id, "Invalid start link format. Please send a valid Telegram message link.")
            return
            
        last = await app.ask(message.chat.id, text="Please send the end link.")
        # Check if user sent a valid message with text
        if not last.text or not last.text.strip():
            await app.send_message(message.chat.id, "No link provided. Please send a valid Telegram message link.")
            return
        
        last_id = get_link(last.text)
        if not last_id:
            await app.send_message(message.chat.id, "Invalid end link format. Please send a valid Telegram message link.")
            return
    
    # Extract message ID from start link (handle query parameters like ?single)
    s = start_id.split("/")[-1]
    if "?" in s:
        s = s.split("?")[0]
    try:
        cs = int(s)
    except ValueError:
        await app.send_message(message.chat.id, "Invalid start link format. Please send a valid Telegram message link.")
        return
    
    # Extract message ID from end link (handle query parameters like ?single)
    l = last_id.split("/")[-1]
    if "?" in l:
        l = l.split("?")[0]
    try:
        cl = int(l)
    except ValueError:
        await app.send_message(message.chat.id, "Invalid end link format. Please send a valid Telegram message link.")
        return

    if cl < cs:
        await app.send_message(message.chat.id, "End link must be the same as or after the start link.")
        return

    destination_chat_id = None
    destination_thread_id = None
    destination_folder = None
    destination_choice = await app.ask(
        message.chat.id,
        text="Do you want to send all batch media to a group, supergroup, or channel? Reply y or n.",
    )
    choice = (destination_choice.text or "").strip().lower()
    if choice in {"y", "yes"}:
        group_response = await app.ask(
            message.chat.id,
            text="Send the group/channel link, topic message link, @username, or chat ID. The bot must be an admin in groups/supergroups and have posting permission in channels.",
        )
        group_ref = (group_response.text or "").strip()
        if not group_ref:
            await app.send_message(message.chat.id, "No destination provided. Batch cancelled.")
            return

        (
            parsed_group,
            parsed_destination_message,
            destination_thread_id,
            _,
        ) = parse_telegram_link(group_ref)
        target_reference = parsed_group
        if target_reference is None:
            parsed_url = urlparse(group_ref if "://" in group_ref else f"https://{group_ref}")
            if parsed_url.hostname in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
                path_parts = [part for part in parsed_url.path.split("/") if part]
                if len(path_parts) == 1 and not path_parts[0].startswith("+"):
                    target_reference = path_parts[0]
            if target_reference is None:
                target_reference = group_ref
        try:
            target_chat = await app.get_chat(target_reference)
            bot_user = await app.get_me()
            membership = await app.get_chat_member(target_chat.id, bot_user.id)
        except Exception as e:
            logger.warning(f"Could not resolve batch destination: {e}")
            await app.send_message(
                message.chat.id,
                "I couldn't verify that destination. Add the bot with posting permission, then send its link or chat ID.",
            )
            return

        target_type = str(target_chat.type).lower().split(".")[-1]
        if target_type not in {"group", "supergroup", "channel"}:
            await app.send_message(message.chat.id, "That link does not point to a group or channel. Batch cancelled.")
            return
        member_status = str(getattr(membership, "status", "")).lower().rsplit(".", 1)[-1]
        if member_status in {"left", "kicked"}:
            await app.send_message(
                message.chat.id,
                "The bot is not a member of that destination. Add it with permission to post and try again.",
            )
            return
        if member_status == "restricted" and not getattr(membership, "can_send_messages", False):
            await app.send_message(
                message.chat.id,
                "The bot cannot send messages in that destination. Update its permissions and try again.",
            )
            return
        if target_type in {"group", "supergroup"} and member_status not in {
            "administrator",
            "owner",
            "creator",
        }:
            await app.send_message(
                message.chat.id,
                "The bot must be an admin in that group/supergroup before batch media can be sent there.",
            )
            return
        if target_type == "channel":
            privileges = getattr(membership, "privileges", None)
            if member_status not in {"administrator", "owner", "creator"} or not getattr(
                privileges, "can_post_messages", False
            ):
                await app.send_message(
                    message.chat.id,
                    "The bot must be a channel admin with permission to post messages.",
                )
                return
        destination_chat_id = target_chat.id

        if target_type == "supergroup":
            forum_topics = None
            try:
                forum_topics = await _get_forum_topics(target_chat.id)
            except Exception as error:
                err_text = str(error).upper()
                if "CHAT_NOT_FORUM" in err_text:
                    forum_topics = None
                else:
                    logger.exception(
                        "Could not inspect forum topics in destination %s",
                        target_chat.id,
                    )
                    forum_topics = None

            # If user provided a topic link (e.g. https://t.me/cet102026/123),
            # parse_telegram_link may set parsed_destination_message but leave
            # destination_thread_id=None. Use parsed_destination_message as the
            # topic ID directly - even if we can't list all forum topics.
            if destination_thread_id is None and parsed_destination_message is not None:
                try:
                    destination_thread_id = int(parsed_destination_message)
                    destination_folder = f"Topic {destination_thread_id}"
                except ValueError:
                    destination_thread_id = None

            # If destination_thread_id is now set (from link or auto-detection),
            # auto-use it without asking.
            if destination_thread_id is not None:
                # Try to verify/confirm the topic exists, but proceed regardless
                if forum_topics:
                    topic_found = next(
                        (
                            topic
                            for topic in forum_topics
                            if int(getattr(topic, "id", -1)) == int(destination_thread_id)
                        ),
                        None,
                    )
                    if topic_found is not None:
                        destination_folder = str(getattr(topic_found, "title", "") or "").strip()
                # If forum_topics is None/empty, still proceed with the provided topic ID

            elif forum_topics:
                # No topic ID in link; list available topics and ask user
                topic_options = "\n".join(
                    f"• `{topic.id}` — {topic.title}"
                    for topic in forum_topics[:30]
                )
                if len(forum_topics) > 30:
                    topic_options += (
                        f"\n…plus {len(forum_topics) - 30} more; "
                        "you can enter any topic ID or exact name."
                    )
                if not topic_options:
                    topic_options = "No forum topics were returned."
                topic_response = await app.ask(
                    message.chat.id,
                    text=(
                        f"**{target_chat.title or target_chat.id}** एक फोरम सुपरग्रुप है।\n"
                        f"{topic_options}\n\n"
                        "बैच वहां भेजने के लिए टॉपिक ID या सटीक टॉपिक नाम का उत्तर दें, "
                        "या `main` लिखें जनरल चैट के लिए।"
                    ),
                    timeout=300,
                )
                topic_choice = (topic_response.text or "").strip()
                if topic_choice.lower() in {"main", "general"}:
                    destination_thread_id = None
                    destination_folder = None
                else:
                    selected_topic = next(
                        (
                            topic
                            for topic in forum_topics
                            if topic_choice == str(getattr(topic, "id", ""))
                            or topic_choice.casefold()
                            == str(getattr(topic, "title", "")).casefold()
                        ),
                        None,
                    )
                    if selected_topic is None:
                        await app.send_message(
                            message.chat.id,
                            "वह टॉपिक नहीं मिला। बैच रद्द किया गया; फिर से /batch शुरू करें और सूचीबद्ध टॉपिक ID या नाम चुनें।",
                        )
                        return
                    destination_thread_id = int(selected_topic.id)
                    destination_folder = str(selected_topic.title).strip()

            # Warn clearly when no topic ID was found in the URL
            if destination_thread_id is None:
                await app.send_message(
                    message.chat.id,
                    "⚠️ **कोई टॉपिक ID लिंक में नहीं मिला।**\n\n"
                    "मीडिया **जनरल/मेन चैट** में भेजा जाएगा।\n\n"
                    "किसी खास टॉपिक में मीडिया भेजने के लिए टॉपिक लिंक दें:\n"
                    "• `https://t.me/cet102026/123` (पब्लिक सुपरग्रुप टॉपिक)\n"
                    "• `https://t.me/c/-100123456789/123` (प्राइवेट सुपरग्रुप टॉपिक)\n"
                    "जहां `123` टॉपिक/थ्रेड आईडी है।",
                )

        topic_label = (
            f"\nForum topic: **{destination_folder or destination_thread_id}** "
            f"(`{destination_thread_id}`)"
            if destination_thread_id is not None
            else (
                "\nDestination: **General/main chat**"
                if target_type == "supergroup"
                else ""
            )
        )
        await app.send_message(
            message.chat.id,
            f"✅ Destination verified: **{target_chat.title or target_chat.id}** "
            f"(`{target_chat.id}`){topic_label}",
        )
        confirmation = await app.ask(
            message.chat.id,
            text="Confirm this batch destination? Reply yes to continue or no to cancel.",
            timeout=300,
        )
        if (confirmation.text or "").strip().lower() not in {"y", "yes"}:
            await app.send_message(message.chat.id, "Batch cancelled; no media was queued.")
            return
    elif choice not in {"n", "no"}:
        await app.send_message(message.chat.id, "Please reply y/yes or n/no. Start /batch again to retry.")
        return
    else:
        destination_chat_id = get_user_chat_id(user_id, DEFAULT_DESTINATION_ID)
        if destination_chat_id is not None:
            try:
                default_chat = await app.get_chat(destination_chat_id)
                bot_user = await app.get_me()
                membership = await app.get_chat_member(default_chat.id, bot_user.id)
            except Exception:
                logger.exception("Could not verify the default batch destination for user %s", user_id)
                await app.send_message(
                    message.chat.id,
                    "The saved/default destination is inaccessible. Update `/settings` or set DEFAULT_DESTINATION_ID.",
                )
                return
            default_type = str(default_chat.type).lower().split(".")[-1]
            member_status = str(getattr(membership, "status", "")).lower().rsplit(".", 1)[-1]
            if (
                default_type not in {"group", "supergroup", "channel"}
                or member_status in {"left", "kicked"}
                or (
                    default_type == "channel"
                    and (
                        member_status not in {"administrator", "owner"}
                        or not getattr(getattr(membership, "privileges", None), "can_post_messages", False)
                    )
                )
            ):
                await app.send_message(
                    message.chat.id,
                    "The saved/default destination is not a group where the bot can post. Update `/settings` or DEFAULT_DESTINATION_ID.",
                )
                return
            destination_chat_id = default_chat.id
            await app.send_message(
                message.chat.id,
                f"📥 Using your saved/default destination: **{default_chat.title or default_chat.id}**.",
            )
        else:
            await app.send_message(
                message.chat.id,
                "No default group is configured; using your saved output destination, or this private chat if none is saved.",
            )

    # Calculate total media to process
    total_messages = cl - cs + 1
    status_message = await app.send_message(
        message.chat.id,
        f"⏳ Batch added to the download queue: {total_messages} message(s).",
    )
    try:     
        await get_worker_manager().submit(
            "downloads",
            "batch_link",
            {
                "user_id": user_id,
                "chat_id": message.chat.id,
                "request_message_id": message.id,
                "status_message_id": status_message.id,
                "start_link": start_id,
                "start_message_id": cs,
                "end_message_id": cl,
                "destination_chat_id": destination_chat_id,
                "destination_thread_id": destination_thread_id,
                "destination_folder": destination_folder,
            },
        )
        counts = await get_worker_manager().counts(user_id)
        await safe_edit(status_message,
            f"⏳ Batch queued: {total_messages} message(s). "
            f"{counts['pending']} waiting; {counts['running']} processing."
        )
    except Exception as error:
        logger.exception("Could not enqueue batch for user %s", user_id)
        await safe_edit(status_message, f"⚠️ Could not queue this batch: {error}")


async def _run_batch_link_job(payload):
    user_id = int(payload["user_id"])
    chat_id = int(payload["chat_id"])
    request_message = await app.get_messages(chat_id, int(payload["request_message_id"]))
    if not request_message:
        raise RuntimeError("The queued batch request is no longer available.")
    status_message = await app.get_messages(chat_id, int(payload["status_message_id"]))
    if not status_message:
        status_message = await app.send_message(chat_id, "📦 Resuming your queued batch...")

    lock = link_queue_locks.setdefault(user_id, asyncio.Lock())
    try:
        async with lock:
            await safe_edit(status_message, "📦 Batch started...")
            users_loop[user_id] = True
            start_id = payload["start_link"]
            start_message_id = int(payload.get("next_message_id", payload["start_message_id"]))
            end_message_id = int(payload["end_message_id"])
            total_messages = end_message_id - int(payload["start_message_id"]) + 1
            destination_chat_id = payload.get("destination_chat_id")
            destination_thread_id = payload.get("destination_thread_id")
            destination_folder = payload.get("destination_folder")
            processed_count = int(payload.get("processed_count", 0))
            failed_message_ids = [
                int(message_id)
                for message_id in payload.get("failed_message_ids", [])
            ]
            userbot = None

            try:
                data = await db.get_data(user_id)
                session = data.get("session") if data else None
                if not session:
                    await safe_edit(status_message, "Login in bot first ...")
                    return

                userbot = Client(
                    ":userbot:",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session,
                    max_concurrent_transmissions=MAX_CONCURRENT_TRANSMISSIONS,
                )
                try:
                    await userbot.start()
                except (SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered):
                    await safe_edit(status_message, "Your login expired ... /login again")
                    return
                except Exception as error:
                    logger.exception("Batch user session start failed for %s", user_id)
                    await safe_edit(status_message, f"Failed to start session: {error}")
                    return

                for message_id in range(start_message_id, end_message_id + 1):
                    if not users_loop.get(user_id):
                        break
                    msg = await app.send_message(
                        chat_id,
                        f"📦 Batch progress: {processed_count}/{total_messages} complete\n"
                        f"Current message: {message_id}\nFailed: {len(failed_message_ids)}",
                    )
                    url = start_id
                    try:
                        b_chat, _, b_thread, b_story = parse_telegram_link(start_id)
                        if b_story or b_chat is None:
                            url = start_id
                        elif b_thread is not None:
                            url = (
                                f"https://t.me/c/{b_chat}/{b_thread}/{message_id}"
                                if isinstance(b_chat, int)
                                else f"https://t.me/{b_chat}/{b_thread}/{message_id}"
                            )
                        else:
                            url = (
                                f"https://t.me/c/{b_chat}/{message_id}"
                                if isinstance(b_chat, int)
                                else f"https://t.me/{b_chat}/{message_id}"
                            )

                        result = "retry"
                        for attempt in range(3):
                            if attempt:
                                await safe_edit(msg,
                                    f"⚠️ Retrying message {message_id} ({attempt}/2)\n"
                                    f"Batch progress: {processed_count}/{total_messages} complete"
                                )
                                await asyncio.sleep(2 ** (attempt - 1))
                            result = await get_msg(
                                userbot,
                                user_id,
                                msg.id,
                                url,
                                0,
                                request_message,
                                True,
                                destination_chat_id,
                                destination_thread_id,
                                destination_folder,
                            )
                            if result != "retry":
                                break

                        if result == "success":
                            processed_count += 1
                        else:
                            failed_message_ids.append(message_id)
                        await safe_edit(msg,
                            f"📦 Batch progress: {processed_count + len(failed_message_ids)}/"
                            f"{total_messages} processed\n"
                            f"✅ Successful: {processed_count}\n"
                            f"⚠️ Failed: {len(failed_message_ids)}"
                        )
                        await asyncio.sleep(10)
                    except Exception as error:
                        logger.exception("Batch message %s failed for user %s", message_id, user_id)
                        if message_id not in failed_message_ids:
                            failed_message_ids.append(message_id)
                        await app.send_message(chat_id, f"Error processing link {url}: {error}")
                    payload.update(
                        next_message_id=message_id + 1,
                        processed_count=processed_count,
                        failed_message_ids=failed_message_ids,
                    )
                    await get_worker_manager().checkpoint(payload["_task_id"], payload)

                failed_summary = (
                    "\n❌ Failed message IDs: " + ", ".join(map(str, failed_message_ids[:50]))
                    if failed_message_ids else ""
                )
                if len(failed_message_ids) > 50:
                    failed_summary += f" (and {len(failed_message_ids) - 50} more)"
                await app.send_message(
                    chat_id,
                    f"✅ **Batch Processing Complete!**\n\n"
                    f"📦 Total processed: {processed_count + len(failed_message_ids)}/{total_messages}\n"
                    f"✅ Successful: {processed_count}\n"
                    f"❌ Failed: {len(failed_message_ids)}"
                    f"{failed_summary}\n\n__**Powered by safe_repo**__",
                )
            except FloodWait as error:
                await app.send_message(
                    chat_id,
                    f"Try again after {error.x} seconds due to floodwait from Telegram.",
                )
            except Exception:
                logger.exception("Batch processing failed for user %s", user_id)
                raise
            finally:
                if userbot:
                    try:
                        await userbot.stop()
                    except Exception:
                        logger.exception("Could not stop batch user session for %s", user_id)
                users_loop.pop(user_id, None)
    finally:
        if not lock.locked():
            link_queue_locks.pop(user_id, None)


register_handler("batch_link", _run_batch_link_job)


@app.on_message(filters.command("cancel"))
async def stop_batch(_, message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    cancelled = await get_worker_manager().cancel_user(user_id)
    if user_id in users_loop:
        users_loop[user_id] = False
    if cancelled or user_id in users_loop:
        await app.send_message(
            message.chat.id,
            f"🛑 Cancelled {cancelled} queued task(s). The active batch will stop after its current item.",
        )
    else:
        await app.send_message(message.chat.id, "No queued or active batch processing to stop.")
