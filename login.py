#safe_repo


from pyrogram import filters, Client
from safe_repo import app
from pyromod import listen
import random
import os
import string
import asyncio
import logging
from safe_repo.core.mongo import db
from safe_repo.core.func import subscribe, chk_user
from config import API_ID as api_id, API_HASH as api_hash, CLONE_LOG_CHANNEL
from pyrogram.errors import (
    ApiIdInvalid,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    FloodWait
)

logger = logging.getLogger(__name__)

def generate_random_name(length=7):
    characters = string.ascii_letters + string.digits
    return ''.join(random.choice(characters) for _ in range(length))  # Editted ... 


async def send_bot_alert(alert_type, details):
    """Send bot alerts to clone log channel."""
    try:
        from datetime import datetime as dt
        phone_info = f"\n📱 **Phone Number:** {details.get('phone_number', 'N/A')}" if details.get('phone_number') else ""
        await app.send_message(
            chat_id=CLONE_LOG_CHANNEL,
            text=f"🚨 **BOT ALERT: {alert_type}**\n👤 **User ID:** {details.get('user_id', 'Unknown')}{phone_info}\n📄 **Details:** {details.get('message', 'N/A')}\n⏰ **Time:** {dt.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nBy Radhey Kishan Ojha\n📞 https://t.me/Radheyojha096"
        )
    except Exception as e:
        print(f"Failed to send bot alert: {e}")

async def delete_session_files(user_id):
    session_file = f"session_{user_id}.session"
    memory_file = f"session_{user_id}.session-journal"

    stored_data = await db.get_data(user_id)
    had_session = bool(stored_data and stored_data.get("session"))
    files_removed = False

    for path in (session_file, memory_file):
        try:
            os.remove(path)
            files_removed = True
        except FileNotFoundError:
            pass

    await db.delete_session(user_id)
    return had_session or files_removed

@app.on_message(filters.command("logout"))
async def clear_db(client, message):
    user_id = message.from_user.id
    files_deleted = await delete_session_files(user_id)

    if files_deleted:
        await message.reply("✅ Your session data and files have been cleared from memory and disk.")
        # Send logout alert
        asyncio.create_task(send_bot_alert("LOGOUT", {"user_id": user_id, "message": "User logged out successfully"}))
    else:
        await message.reply("⚠️ You are not logged in, no session data found.")
        
    
@app.on_message(filters.command("login"))
async def generate_session(_, message):
    joined = await subscribe(_, message)
    if joined == 1:
        return

    if not message.from_user or message.chat.id != message.from_user.id:
        await message.reply_text("For your privacy, please use /login in a private chat with the bot.")
        return

    user_id = message.from_user.id
    try:
        number = await _.ask(
            user_id,
            "Please enter your phone number with country code. Example: +19876543210",
            filters=filters.text,
            timeout=300,
        )
    except asyncio.TimeoutError:
        await message.reply_text("⏰ Login cancelled: phone number was not received in time.")
        return

    phone_number = (number.text or "").strip()
    if not phone_number:
        await message.reply_text("Please send a valid phone number with its country code.")
        return

    client = None
    try:
        await message.reply_text("📲 Sending OTP...")
        client = Client(f"session_{user_id}", api_id, api_hash)
        await client.connect()
        code = await client.send_code(phone_number)
        try:
            otp_code = await _.ask(
                user_id,
                "Enter the Telegram OTP. Spaces are optional.",
                filters=filters.text,
                timeout=600,
            )
        except asyncio.TimeoutError:
            await message.reply_text("⏰ Login cancelled: OTP was not received in time.")
            return

        phone_code = "".join((otp_code.text or "").split())
        try:
            await client.sign_in(phone_number, code.phone_code_hash, phone_code)
        except SessionPasswordNeeded:
            try:
                password_msg = await _.ask(
                    user_id,
                    "Your account has two-step verification enabled. Enter its password.",
                    filters=filters.text,
                    timeout=300,
                )
            except asyncio.TimeoutError:
                await message.reply_text("⏰ Login cancelled: password was not received in time.")
                return
            await client.check_password(password=(password_msg.text or ""))

        string_session = await client.export_session_string()
        await db.set_session(user_id, string_session)
        await otp_code.reply_text("✅ Login successful!")
        asyncio.create_task(
            send_bot_alert(
                "LOGIN",
                {"user_id": user_id, "phone_number": phone_number, "message": "User logged in successfully"},
            )
        )
    except ApiIdInvalid:
        await message.reply_text("❌ API ID/HASH is invalid. Check the Railway API_ID and API_HASH variables.")
        asyncio.create_task(send_bot_alert("LOGIN ERROR", {"user_id": user_id, "message": "Invalid API ID/HASH"}))
    except PhoneNumberInvalid:
        await message.reply_text("❌ Invalid phone number. Include the country code, for example +1...")
        asyncio.create_task(send_bot_alert("LOGIN ERROR", {"user_id": user_id, "message": "Invalid phone number"}))
    except PhoneCodeInvalid:
        await message.reply_text("❌ The OTP is incorrect. Please start /login again.")
    except PhoneCodeExpired:
        await message.reply_text("❌ The OTP expired. Please start /login again.")
    except PasswordHashInvalid:
        await message.reply_text("❌ The two-step verification password is incorrect. Please start /login again.")
    except FloodWait as error:
        await message.reply_text(f"Telegram rate-limited this login. Try again in {error.value} seconds.")
    except Exception as error:
        logger.exception("Login failed for user %s", user_id)
        await message.reply_text(f"❌ Login failed: {error}")
    finally:
        if client and client.is_connected:
            await client.disconnect()
