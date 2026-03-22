from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "database" / "trade_mirror.db"
BACKUP_DIRECTORY = PROJECT_ROOT / "backups"
LOG_DIRECTORY = PROJECT_ROOT / "logs"

BOT_TOKEN = os.getenv("BOT_TOKEN", "PEGA_AQUI_TU_BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID", "-1000000000000")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "sk_test_PEGA_AQUI_TU_CLAVE")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://tu-dominio-o-tunnel.com")

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_PEGA_AQUI_TU_WEBHOOK_SECRET")
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "trade-mirror-secret")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "5000"))
DEFAULT_SUBSCRIPTION_DAYS = int(os.getenv("DEFAULT_SUBSCRIPTION_DAYS", "30"))
BACKUP_INTERVAL_SECONDS = int(os.getenv("BACKUP_INTERVAL_SECONDS", "86400"))
TELEGRAM_INVITE_LINK_NAME = os.getenv("TELEGRAM_INVITE_LINK_NAME", "TradeMirror VIP")
TELEGRAM_INVITE_EXPIRATION_HOURS = int(os.getenv("TELEGRAM_INVITE_EXPIRATION_HOURS", "24"))
TELEGRAM_HTTP_TIMEOUT = int(os.getenv("TELEGRAM_HTTP_TIMEOUT", "15"))

for required_directory in (
    DATABASE_PATH.parent,
    BACKUP_DIRECTORY,
    LOG_DIRECTORY,
    PROJECT_ROOT / "backend",
    PROJECT_ROOT / "bot",
    PROJECT_ROOT / "payments",
    PROJECT_ROOT / "config",
    PROJECT_ROOT / "docs",
    PROJECT_ROOT / "ninja_strategy",
):
    required_directory.mkdir(parents=True, exist_ok=True)
