#safe_repo

import time
import asyncio
import os
from urllib.parse import urlparse
from pyrogram import filters, Client
from safe_repo import app
from config import API_ID, API_HASH
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
from safe_repo.core.func import parse_telegram_link
from safe_repo.core.mongo import db
from pyrogram.errors import FloodWait, SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered
from safe_repo.core import script
from safe_repo.core.media_links import get_public_base_url
from safe_repo.web.study import build_public_study_url, load_catalog_entries
import logging
import re

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



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

    lock = link_queue_locks.setdefault(user_id, asyncio.Lock())
    waiting = lock.locked()
    if waiting:
        link_queue_waiters[user_id] = link_queue_waiters.get(user_id, 0) + 1
    task = asyncio.current_task()
    link_queue_tasks.setdefault(user_id, set()).add(task)
    queue_status = await message.reply_text(
        f"⏳ Added to your queue (position {link_queue_waiters.get(user_id, 0) + 1})."
        if waiting else "⏳ Preparing your media..."
    )

    try:
        async with lock:
            if waiting:
                link_queue_waiters[user_id] = max(0, link_queue_waiters.get(user_id, 1) - 1)
                waiting = False
            await queue_status.edit_text("Processing...")
            await _process_single_link(_, message, link, queue_status)
    except asyncio.CancelledError:
        try:
            await queue_status.edit_text("🛑 This queued media task was cancelled.")
        except Exception:
            pass
        raise
    finally:
        if waiting:
            link_queue_waiters[user_id] = max(0, link_queue_waiters.get(user_id, 1) - 1)
        tasks = link_queue_tasks.get(user_id)
        if tasks:
            tasks.discard(task)
            if not tasks:
                link_queue_tasks.pop(user_id, None)
        if not lock.locked() and not link_queue_waiters.get(user_id):
            link_queue_locks.pop(user_id, None)
            link_queue_waiters.pop(user_id, None)


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
                    max_concurrent_transmissions=2,
                )
                await userbot.start()                
            except (SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered):
                return await msg.edit_text("Login expired /login again...")
            except Exception as e:
                logger.error(f"Session start error: {e}")
                return await msg.edit_text("Failed to start session. Please try again.")
        else:
            await msg.edit_text("Login in bot first ...")
            return

        try:
            # Invite links (t.me/+, telegram.me/+) -> join the chat
            if '/+' in link or 'joinchat/' in link:
                q = await userbot_join(userbot, link)
                await msg.edit_text(q)
            else:
                for attempt in range(3):
                    result = await get_msg(userbot, user_id, msg.id, link, 0, message, False)
                    if result != "retry":
                        break
                    if attempt < 2:
                        await msg.edit_text(
                            f"⚠️ Temporary error. Retrying ({attempt + 1}/2)..."
                        )
                        await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error(f"Processing error: {e}")
            await msg.edit_text(f"Link: `{link}`\n\n**Error:** {str(e)}")
                     
    except FloodWait as fw:
        await msg.edit_text(f'Try again after {fw.x} seconds due to floodwait from telegram.')
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
link_queue_waiters = {}
link_queue_tasks = {}


@app.on_message(filters.command("queue") & filters.private)
async def show_queue(_, message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    active = bool(link_queue_locks.get(user_id) and link_queue_locks[user_id].locked())
    waiting = link_queue_waiters.get(user_id, 0)
    await message.reply_text(
        f"📥 Link queue: {waiting} waiting"
        + ("; 1 processing." if active else "; idle.")
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
        await status.edit_text(f"⚠️ Thumbnail preview failed: {error}")
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
    destination_choice = await app.ask(
        message.chat.id,
        text="Do you want to send all batch media to a group, supergroup, or channel? Reply y or n.",
    )
    choice = (destination_choice.text or "").strip().lower()
    if choice in {"y", "yes"}:
        group_response = await app.ask(
            message.chat.id,
            text="Send the group/channel link, topic message link, @username, or chat ID. The bot must be a member with permission to post.",
        )
        group_ref = (group_response.text or "").strip()
        if not group_ref:
            await app.send_message(message.chat.id, "No destination provided. Batch cancelled.")
            return

        parsed_group, _, destination_thread_id, _ = parse_telegram_link(group_ref)
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
        except Exception as e:
            logger.warning(f"Could not resolve batch destination: {e}")
            await app.send_message(
                message.chat.id,
                "I couldn't access that destination. Add the bot with posting permission, then send its link or chat ID.",
            )
            return

        if str(target_chat.type).lower().split(".")[-1] not in {"group", "supergroup", "channel"}:
            await app.send_message(message.chat.id, "That link does not point to a group or channel. Batch cancelled.")
            return
        destination_chat_id = target_chat.id
    elif choice not in {"n", "no"}:
        await app.send_message(message.chat.id, "Please reply y/yes or n/no. Start /batch again to retry.")
        return

    # Calculate total media to process
    total_messages = cl - cs + 1
    await app.send_message(message.chat.id, f"Total messages to process: {total_messages}")

    userbot = None
    processed_count = 0
    failed_message_ids = []
    try:     
        data = await db.get_data(user_id)
        
        if data and data.get("session"):
            session = data.get("session")
            try:
                userbot = Client(
                    ":userbot:",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session,
                    max_concurrent_transmissions=2,
                )
                await userbot.start()                
            except (SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered):
                return await app.send_message(message.chat.id, "Your login expired ... /login again")
            except Exception as e:
                logger.error(f"Session start error: {e}")
                return await app.send_message(message.chat.id, "Failed to start session. Please try again.")
        else:
            await app.send_message(message.chat.id, "Login in bot first ...")
            return

        try:
            users_loop[user_id] = True
            
            for i in range(int(s), int(l) + 1):
                if user_id in users_loop and users_loop[user_id]:
                    msg = await app.send_message(
                        message.chat.id,
                        f"📦 Batch progress: {processed_count}/{total_messages} complete\n"
                        f"Current message: {i}\nFailed: {len(failed_message_ids)}",
                    )
                    try:
                        # Rebuild the link for batch iteration while preserving
                        # supergroup topic/thread context from the start link.
                        b_chat, b_start, b_thread, b_story = parse_telegram_link(start_id)
                        if b_story or b_chat is None:
                            url = start_id
                        elif b_thread is not None:
                            url = f"https://t.me/c/{b_chat}/{b_thread}/{i}" if isinstance(b_chat, int) else f"https://t.me/{b_chat}/{b_thread}/{i}"
                        else:
                            url = f"https://t.me/c/{b_chat}/{i}" if isinstance(b_chat, int) else f"https://t.me/{b_chat}/{i}"

                        result = "retry"
                        for attempt in range(3):
                            if attempt:
                                await msg.edit_text(
                                    f"⚠️ Retrying message {i} ({attempt}/2)\n"
                                    f"Batch progress: {processed_count}/{total_messages} complete"
                                )
                                await asyncio.sleep(2 ** (attempt - 1))
                            result = await get_msg(
                                userbot,
                                user_id,
                                msg.id,
                                url,
                                0,
                                message,
                                True,
                                destination_chat_id,
                                destination_thread_id,
                            )
                            if result != "retry":
                                break

                        if result == "success":
                            processed_count += 1
                        else:
                            failed_message_ids.append(i)
                        await msg.edit_text(
                            f"📦 Batch progress: {processed_count + len(failed_message_ids)}/"
                            f"{total_messages} processed\n"
                            f"✅ Successful: {processed_count}\n"
                            f"⚠️ Failed: {len(failed_message_ids)}"
                        )
                        sleep_msg = await app.send_message(message.chat.id, "Sleeping for 10 seconds to avoid flood...")
                        await asyncio.sleep(8)
                        await sleep_msg.delete()
                        await asyncio.sleep(2)                                                
                    except Exception as e:
                        logger.error(f"Error processing link {url}: {e}")
                        await app.send_message(message.chat.id, f"Error processing link {url}: {str(e)}")
                        continue
                else:
                    break
        except Exception as e:
            logger.error(f"Batch processing error: {e}")
            await app.send_message(message.chat.id, f"Error: {str(e)}")
                     
    except FloodWait as fw:
        await app.send_message(message.chat.id, f'Try again after {fw.x} seconds due to floodwait from Telegram.')
    except Exception as e:
        logger.error(f"Main batch error: {e}")
        await app.send_message(message.chat.id, f"Error: {str(e)}")
    finally:
        # Ensure userbot is properly disconnected
        if userbot:
            try:
                await userbot.stop()
            except:
                pass
        users_loop.pop(user_id, None)
        
        # Show completion message after batch processing
        if processed_count or failed_message_ids:
            failed_summary = (
                "\n❌ Failed message IDs: " + ", ".join(map(str, failed_message_ids[:50]))
                if failed_message_ids else ""
            )
            if len(failed_message_ids) > 50:
                failed_summary += f" (and {len(failed_message_ids) - 50} more)"
            await app.send_message(
                message.chat.id,
                f"✅ **Batch Processing Complete!**\n\n"
                f"📦 Total processed: {processed_count + len(failed_message_ids)}/{total_messages}\n"
                f"✅ Successful: {processed_count}\n"
                f"❌ Failed: {len(failed_message_ids)}"
                f"{failed_summary}\n\n__**Powered by safe_repo**__",
            )


@app.on_message(filters.command("cancel"))
async def stop_batch(_, message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    queued_tasks = set(link_queue_tasks.get(user_id, set()))
    for task in queued_tasks:
        task.cancel()
    if queued_tasks:
        await app.send_message(
            message.chat.id,
            f"🛑 Cancelled {len(queued_tasks)} active/queued link task(s).",
        )
    if user_id in users_loop:
        users_loop[user_id] = False
        await app.send_message(message.chat.id, "Batch processing stopped.")
    elif not queued_tasks:
        await app.send_message(message.chat.id, "No active batch processing to stop.")
