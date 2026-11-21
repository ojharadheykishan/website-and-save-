# safe_repo
import os


def _required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set.")
    return value


API_HASH = _required_env("API_HASH")
API_ID = int(_required_env("API_ID"))
BOT_TOKEN = _required_env("BOT_TOKEN")
CHANNEL_ID = -1003818713803
LOG_GROUP = -1005121908460
CLONE_LOG_CHANNEL = -1003886456761
# Optional YouTube Data API v3 key for /yt search; URL previews work without it.
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
# Public channel where cloned media is also posted so users get an
# MX Player / VLC streamable link (t.me/Link09660/MSGID)
STREAM_CHANNEL = -1004332477626
STREAM_CHANNEL_USERNAME = "Link09660"
# Owner(s) of the bot. Keep as a list for filters.user compatibility.
OWNER_ID = [8552899459]

# Website streaming URL (port 8080 for online video playback)
WEBSITE_URL = os.environ.get("WEBSITE_URL", "https://2.website-and-save-production-75d9879e.up.railway.app:8080").strip()

MONGO_DB = (os.environ.get("MONGODB_URI") or os.environ.get("MONGO_DB") or "").strip()

CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or CHANNEL_ID)
DEFAULT_DESTINATION_ID = (
    int(os.environ["DEFAULT_DESTINATION_ID"])
    if os.environ.get("DEFAULT_DESTINATION_ID", "").strip()
    else None
)


def _positive_int(name, default):
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive integer.") from error
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


PYROGRAM_WORKERS = _positive_int("PYROGRAM_WORKERS", 10)
MAX_CONCURRENT_TRANSMISSIONS = _positive_int("MAX_CONCURRENT_TRANSMISSIONS", 2)
