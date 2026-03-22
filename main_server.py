from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask, jsonify, request

from backups.backup_manager import backup_database, get_backup_history, start_backup_scheduler
from bot.telegram_api import send_signal_to_vip, send_vip_access_message
from config.config import APP_HOST, APP_PORT, WEBHOOK_SHARED_SECRET
from database.db import activate_user, deactivate_user, init_db, list_users, save_user_registration
from logs.logger import errors_logger, signals_logger
from payments.stripe_handler import process_stripe_webhook


app = Flask(__name__)
_backup_scheduler: dict[str, Any] | None = None


def _bootstrap() -> None:
    global _backup_scheduler
    init_db()

    try:
        backup_database()
    except FileNotFoundError:
        pass
    except Exception:
        errors_logger.exception("No se pudo crear el backup inicial.")

    if _backup_scheduler is None:
        _backup_scheduler = start_backup_scheduler()


def _json_error(message: str, status_code: int) -> tuple[Any, int]:
    return jsonify({"ok": False, "error": message}), status_code


def _shared_secret_required() -> bool:
    return bool(WEBHOOK_SHARED_SECRET and WEBHOOK_SHARED_SECRET != "trade-mirror-secret")


def _validate_ninja_secret() -> bool:
    if not _shared_secret_required():
        return True
    return request.headers.get("X-TradeMirror-Secret", "") == WEBHOOK_SHARED_SECRET


@app.get("/health")
def health() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "TradeMirror Signals",
            "users": len(list_users()),
            "backups": len(get_backup_history()),
        }
    )


@app.post("/webhook")
def webhook() -> Any:
    if not _validate_ninja_secret():
        return _json_error("Secret invalido para el webhook de NinjaTrader.", 403)

    payload = request.get_json(silent=True)
    if not payload:
        return _json_error("No se recibio JSON valido en /webhook.", 400)

    try:
        telegram_response = send_signal_to_vip(payload)
        signals_logger.info("Webhook NinjaTrader procesado | tipo=%s", payload.get("event", "NA"))
        return jsonify({"ok": True, "telegram_response": telegram_response})
    except Exception:
        errors_logger.exception("Error procesando la senal de NinjaTrader.")
        return _json_error("No se pudo enviar la senal a Telegram.", 500)


@app.post("/webhook/stripe")
def stripe_webhook() -> Any:
    raw_body = request.get_data()
    stripe_signature = request.headers.get("Stripe-Signature")

    try:
        result = process_stripe_webhook(raw_body, stripe_signature)
        delivery = None
        if result.get("ok") and result.get("action") == "activated" and result.get("email"):
            delivery = send_vip_access_message(result["email"])
        return jsonify({"ok": True, "stripe_result": result, "vip_delivery": delivery})
    except Exception:
        return _json_error("No se pudo procesar el webhook de Stripe.", 500)


@app.get("/users")
def users() -> Any:
    status = request.args.get("status")
    return jsonify({"ok": True, "users": list_users(status=status)})


@app.post("/users/register")
def register_user() -> Any:
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    telegram_id = payload.get("telegram_id")

    if not email:
        return _json_error("El campo email es obligatorio.", 400)

    try:
        user = save_user_registration(email, telegram_id)
        return jsonify({"ok": True, "user": user})
    except Exception as exc:
        errors_logger.exception("Error registrando usuario manualmente.")
        return _json_error(str(exc), 400)


@app.post("/users/activate")
def activate_user_route() -> Any:
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    days = int(payload.get("days", 30))
    if not email:
        return _json_error("El campo email es obligatorio.", 400)

    expiration = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    user = activate_user(email, expiration=expiration)
    delivery = send_vip_access_message(email)
    return jsonify({"ok": True, "user": user, "vip_delivery": delivery})


@app.post("/users/deactivate")
def deactivate_user_route() -> Any:
    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    if not email:
        return _json_error("El campo email es obligatorio.", 400)

    user = deactivate_user(email)
    return jsonify({"ok": True, "user": user})


@app.post("/backups/run")
def run_backup() -> Any:
    try:
        backup_path = backup_database()
        return jsonify({"ok": True, "backup_path": str(backup_path), "history": get_backup_history()})
    except Exception:
        errors_logger.exception("Error ejecutando backup manual.")
        return _json_error("No se pudo ejecutar el backup manual.", 500)


def main() -> None:
    _bootstrap()
    app.run(host=APP_HOST, port=APP_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
