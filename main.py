#safe_repo

import time
import asyncio
from urllib.parse import urlparse
from pyrogram import filters, Client
from safe_repo import app
from config import API_ID, API_HASH
from safe_repo.core.get_func import (
    get_msg,
    get_user_chat_id,
    load_delete_words,
    load_replacement_words,
    save_delete_words,
    save_replacement_words,
    save_user_chat_id,
    set_caption_command,
    set_rename_command,
)
from safe_repo.core.func import *
from safe_repo.core.func import parse_telegram_link
from safe_repo.core.mongo import db
from pyrogram.errors import FloodWait, SessionRevoked, AuthKeyDuplicated, AuthKeyUnregistered
from safe_repo.core import script
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



@app.on_message(filters.regex(r'https?://[^\s]+') & ~filters.command("batch") & ~filters.reply)
async def single_link(_, message):
    if not message.from_user or message.chat.id != message.from_user.id:
        await message.reply_text("Please send media links in a private chat with the bot.")
        return
    user_id = message.from_user.id
    
    link = get_link(message.text) 
    
    userbot = None
    try:
        join = await subscribe(_, message)
        if join == 1:
            return
     
        msg = await message.reply("Processing...")
        data = await db.get_data(user_id)
        
        if data and data.get("session"):
            session = data.get("session")
            try:
                userbot = Client(":userbot:", api_id=API_ID, api_hash=API_HASH, session_string=session)
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
                # Let get_msg handle all message/story/topic/bot links via the
                # robust parser. It will reply with a clear error if the link
                # cannot be parsed.
                await get_msg(userbot, user_id, msg.id, link, 0, message, False)
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
    destination_choice = await app.ask(
        message.chat.id,
        text="Do you want to send all batch media to a supergroup? Reply y or n.",
    )
    choice = (destination_choice.text or "").strip().lower()
    if choice in {"y", "yes"}:
        group_response = await app.ask(
            message.chat.id,
            text="Send the supergroup link, public message link, @username, or chat ID. The bot must already be a member with permission to post.",
        )
        group_ref = (group_response.text or "").strip()
        if not group_ref:
            await app.send_message(message.chat.id, "No supergroup link provided. Batch cancelled.")
            return

        parsed_group, _, _, _ = parse_telegram_link(group_ref)
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
                "I couldn't access that group. Add the bot to the supergroup first, then send its public link or chat ID.",
            )
            return

        if str(target_chat.type).lower().split(".")[-1] not in {"group", "supergroup"}:
            await app.send_message(message.chat.id, "That link does not point to a group. Batch cancelled.")
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
    try:     
        data = await db.get_data(user_id)
        
        if data and data.get("session"):
            session = data.get("session")
            try:
                userbot = Client(":userbot:", api_id=API_ID, api_hash=API_HASH, session_string=session)
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
                    remaining = total_messages - processed_count
                    msg = await app.send_message(message.chat.id, f"Processing! ({processed_count + 1}/{total_messages})\nRemaining: {remaining} messages")
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

                        # Process the message
                        await get_msg(
                            userbot,
                            user_id,
                            msg.id,
                            url,
                            0,
                            message,
                            True,
                            destination_chat_id,
                        )
                        
                        # Increment processed count
                        processed_count += 1
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
        if processed_count > 0:
            await app.send_message(message.chat.id, 
                f"✅ **Batch Processing Complete!**\n\n📦 Total files processed: {processed_count}\n\nPowered by Radhey Kishan Ojha clone\n📞 https://t.me/Radheyojha096\n\n__**Powered by safe_repo**__")


@app.on_message(filters.command("cancel"))
async def stop_batch(_, message):
    user_id = message.from_user.id if message.from_user else message.chat.id
    if user_id in users_loop:
        users_loop[user_id] = False
        await app.send_message(message.chat.id, "Batch processing stopped.")
    else:
        await app.send_message(message.chat.id, "No active batch processing to stop.")

