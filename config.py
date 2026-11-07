import os

# Safe .env loader without external dependencies
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# Telegram Configuration (Loaded securely from Environment / Secrets)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID") or os.getenv("CHANNEL_ID")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# DevUploads Configuration (Loaded securely from Environment / Secrets)
DEVUPLOADS_API_KEY = os.getenv("DEVUPLOADS_API_KEY")

# Scraper & Flow Settings
SOURCE_BASE_URL = "https://liteapks.com"
MAX_APKS_PER_RUN = int(os.getenv("MAX_APKS_PER_RUN", "10"))
DATABASE_FILE = os.path.join(os.path.dirname(__file__), "sent_apks.json")
CATALOG_FILE = os.path.join(os.path.dirname(__file__), "apps_catalog.json")
MAX_BOT_API_SIZE_MB = int(os.getenv("MAX_BOT_API_SIZE_MB", "48"))
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "")

MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "4000"))

# Telegram MTProto User API Configuration (Cloud-to-Cloud Uploads)
TG_API_ID = int(os.getenv("TG_API_ID", "39817975"))
TG_API_HASH = os.getenv("TG_API_HASH", "1bdda9f3abecd54d80b5a4bd167962ce")
DEVUPLOADS_BOT = os.getenv("DEVUPLOADS_BOT", "@DevUploadsUploaderBot")
