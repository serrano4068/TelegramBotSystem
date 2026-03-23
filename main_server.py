from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request


app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
PORT = int(os.getenv("PORT", "8080"))
TELEGRAM_POLL_TIMEOUT = 25
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
SIGNAL_ICON = "\U0001F6A8"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError, AttributeError):
        return default


ADMIN_ID = _env_int("1876945257", 0)

users: dict[str, dict[str, Any]] = {}
pending_email_users: set[str] = set()
state_lock = threading.Lock()
polling_lock = threading.Lock()
telegram_polling_started = False
telegram_update_offset = 0


def _safe_text(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_PATTERN.match(normalize_email(email)))


def normalize_signal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": _safe_text(payload.get("type"), "UNKNOWN").upper(),
        "price": _safe_float(payload.get("price"), 0.0),
        "contracts": _safe_int(payload.get("contracts"), 0),
        "status": _safe_text(payload.get("status"), "PENDING").upper(),
    }


def format_signal_message(signal: dict[str, Any]) -> str:
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"{SIGNAL_ICON} TRADE SIGNAL\n\n"
        f"Tipo: {signal['type']}\n"
        f"Precio: {signal['price']:.2f}\n"
        f"Contratos: {signal['contracts']}\n"
        f"Estado: {signal['status']}\n"
        f"Hora: {current_time}"
    )


def telegram_api_call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not BOT_TOKEN:
        return {"ok": False, "error": "BOT_TOKEN no configurado"}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    body = json.dumps(payload).encode("utf-8")
    request_data = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request_data, timeout=30) as response:
            raw_response = response.read().decode("utf-8")
        data = json.loads(raw_response) if raw_response else {}
        if not data.get("ok"):
            print(f"telegram api error: {method} | {data}")
        return data
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        print(f"telegram http error: {method} | {exc.code} | {details}")
        return {"ok": False, "error": f"HTTP {exc.code}", "details": details}
    except URLError as exc:
        print(f"telegram url error: {method} | {exc}")
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        print(f"telegram error: {method} | {exc}")
        return {"ok": False, "error": str(exc)}


def send_message(chat_id: str | int, text: str) -> dict[str, Any]:
    result = telegram_api_call("sendMessage", {"chat_id": str(chat_id), "text": text})
    if result.get("ok"):
        print(f"mensaje enviado: {text}")
    return result


def create_chat_invite_link() -> dict[str, Any]:
    if not CHAT_ID:
        return {"ok": False, "error": "CHAT_ID no configurado"}

    payload = {
        "chat_id": CHAT_ID,
        "name": "VIP Access",
        "member_limit": 1,
        "expire_date": int(time.time()) + 86400,
    }
    return telegram_api_call("createChatInviteLink", payload)


def remove_user_from_channel(telegram_id: str | int) -> dict[str, Any]:
    if not CHAT_ID:
        return {"ok": False, "error": "CHAT_ID no configurado"}

    safe_user_id = _safe_int(telegram_id, 0)
    if safe_user_id <= 0:
        return {"ok": False, "error": "telegram_id invalido"}

    ban_result = telegram_api_call(
        "banChatMember",
        {
            "chat_id": CHAT_ID,
            "user_id": safe_user_id,
            "revoke_messages": False,
        },
    )
    if ban_result.get("ok"):
        telegram_api_call(
            "unbanChatMember",
            {
                "chat_id": CHAT_ID,
                "user_id": safe_user_id,
                "only_if_banned": True,
            },
        )
    return ban_result


def find_email_by_telegram_id(telegram_id: str | int) -> str | None:
    safe_telegram_id = str(telegram_id).strip()
    with state_lock:
        for email, user in users.items():
            if str(user.get("telegram_id", "")).strip() == safe_telegram_id:
                return email
    return None


def get_user(email: str) -> dict[str, Any] | None:
    safe_email = normalize_email(email)
    with state_lock:
        user = users.get(safe_email)
        if user is None:
            return None
        return {
            "telegram_id": str(user.get("telegram_id", "")).strip(),
            "active": bool(user.get("active", False)),
        }


def upsert_user(email: str, telegram_id: str | int | None = None, active: bool | None = None) -> dict[str, Any]:
    safe_email = normalize_email(email)
    if not safe_email:
        raise ValueError("email requerido")

    with state_lock:
        current_user = users.get(safe_email, {"telegram_id": "", "active": False})
        safe_telegram_id = current_user.get("telegram_id", "")
        if telegram_id is not None:
            safe_telegram_id = str(telegram_id).strip()
            for existing_email, existing_user in users.items():
                if existing_email != safe_email and str(existing_user.get("telegram_id", "")).strip() == safe_telegram_id:
                    existing_user["telegram_id"] = ""

        safe_active = current_user.get("active", False) if active is None else bool(active)
        users[safe_email] = {
            "telegram_id": safe_telegram_id,
            "active": safe_active,
        }
        return {
            "email": safe_email,
            "telegram_id": users[safe_email]["telegram_id"],
            "active": users[safe_email]["active"],
        }


def activate_user(email: str, telegram_id: str | int | None = None) -> dict[str, Any]:
    return upsert_user(email, telegram_id=telegram_id, active=True)


def deactivate_user(email: str) -> dict[str, Any]:
    return upsert_user(email, active=False)


def send_vip_access(email: str, message_text: str) -> dict[str, Any]:
    user = get_user(email)
    if not user:
        return {"ok": False, "error": "usuario no encontrado"}

    telegram_id = str(user.get("telegram_id", "")).strip()
    if not telegram_id:
        print(f"usuario activo sin telegram_id: {email}")
        return {"ok": False, "error": "telegram_id no registrado"}

    confirmation_result = send_message(telegram_id, message_text)
    invite_result = create_chat_invite_link()
    invite_link = ((invite_result.get("result") or {}).get("invite_link")) if invite_result.get("ok") else ""

    link_result: dict[str, Any] | None = None
    if invite_link:
        link_result = send_message(telegram_id, f"Tu link VIP: {invite_link}")

    return {
        "ok": bool(confirmation_result.get("ok")) and bool(invite_result.get("ok")),
        "confirmation": confirmation_result,
        "invite_result": invite_result,
        "link_result": link_result,
        "invite_link": invite_link,
    }


def revoke_vip_access(email: str, message_text: str) -> dict[str, Any]:
    user = get_user(email)
    if not user:
        return {"ok": False, "error": "usuario no encontrado"}

    telegram_id = str(user.get("telegram_id", "")).strip()
    removal_result: dict[str, Any] | None = None
    message_result: dict[str, Any] | None = None

    if telegram_id:
        removal_result = remove_user_from_channel(telegram_id)
        message_result = send_message(telegram_id, message_text)

    return {
        "ok": True,
        "removal_result": removal_result,
        "message_result": message_result,
    }


def stripe_api_get(path: str) -> dict[str, Any]:
    if not STRIPE_SECRET_KEY:
        raise RuntimeError("STRIPE_SECRET_KEY no configurado")

    request_data = Request(
        f"https://api.stripe.com{path}",
        headers={
            "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="GET",
    )

    with urlopen(request_data, timeout=30) as response:
        raw_response = response.read().decode("utf-8")
    return json.loads(raw_response) if raw_response else {}


def validate_stripe_event(event: dict[str, Any]) -> bool:
    event_id = _safe_text(event.get("id"), "")
    event_type = _safe_text(event.get("type"), "")
    if not event_id or not event_type:
        return False

    try:
        stripe_event = stripe_api_get(f"/v1/events/{event_id}")
        return stripe_event.get("id") == event_id and stripe_event.get("type") == event_type
    except Exception as exc:
        print(f"error validando evento stripe: {exc}")
        return False


def get_customer_email(customer_id: str) -> str:
    if not customer_id:
        return ""

    try:
        customer = stripe_api_get(f"/v1/customers/{customer_id}")
        return normalize_email(customer.get("email", ""))
    except Exception as exc:
        print(f"error obteniendo customer stripe: {exc}")
        return ""


def extract_email_from_stripe_object(data_object: dict[str, Any]) -> str:
    customer_details = data_object.get("customer_details") or {}
    for candidate in (
        customer_details.get("email"),
        data_object.get("customer_email"),
        data_object.get("receipt_email"),
        data_object.get("email"),
    ):
        safe_candidate = normalize_email(_safe_text(candidate, ""))
        if safe_candidate:
            return safe_candidate

    return get_customer_email(_safe_text(data_object.get("customer"), ""))


def handle_checkout_completed(event: dict[str, Any]) -> dict[str, Any]:
    data_object = ((event.get("data") or {}).get("object") or {})
    email = extract_email_from_stripe_object(data_object)
    if not email:
        return {"ok": False, "error": "email no encontrado"}

    user = activate_user(email)
    delivery = send_vip_access(email, "Pago confirmado. Acceso VIP activado")
    return {"ok": True, "email": email, "user": user, "delivery": delivery}


def handle_invoice_paid(event: dict[str, Any]) -> dict[str, Any]:
    data_object = ((event.get("data") or {}).get("object") or {})
    email = extract_email_from_stripe_object(data_object)
    if not email:
        return {"ok": False, "error": "email no encontrado"}

    user = activate_user(email)
    delivery = send_vip_access(email, "Pago confirmado. Acceso VIP activado")
    return {"ok": True, "email": email, "user": user, "delivery": delivery}


def handle_subscription_deleted(event: dict[str, Any]) -> dict[str, Any]:
    data_object = ((event.get("data") or {}).get("object") or {})
    email = extract_email_from_stripe_object(data_object)
    if not email:
        return {"ok": False, "error": "email no encontrado"}

    user = deactivate_user(email)
    removal = revoke_vip_access(email, "Tu acceso VIP ha expirado")
    return {"ok": True, "email": email, "user": user, "removal": removal}


def normalize_command(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    parts = text.strip().split(maxsplit=1)
    command = parts[0].split("@", 1)[0].lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return command, args


def is_admin(telegram_id: str | int) -> bool:
    return _safe_int(telegram_id, 0) == ADMIN_ID and ADMIN_ID > 0


def parse_admin_target(arguments: str) -> tuple[str, str]:
    telegram_id = ""
    email = ""
    for token in arguments.split():
        if "@" in token and is_valid_email(token):
            email = normalize_email(token)
        else:
            candidate_id = str(_safe_int(token, 0))
            if candidate_id != "0":
                telegram_id = candidate_id
    return telegram_id, email


def handle_start_command(chat_id: str | int, telegram_id: str | int) -> None:
    safe_telegram_id = str(telegram_id).strip()
    with state_lock:
        pending_email_users.add(safe_telegram_id)
    send_message(chat_id, "Envia tu email para vincular tu acceso VIP.")


def handle_email_registration(chat_id: str | int, telegram_id: str | int, text: str) -> None:
    safe_telegram_id = str(telegram_id).strip()
    safe_email = normalize_email(text)
    if not is_valid_email(safe_email):
        send_message(chat_id, "Email invalido. Intenta otra vez.")
        return

    user = upsert_user(safe_email, telegram_id=safe_telegram_id)
    with state_lock:
        pending_email_users.discard(safe_telegram_id)

    send_message(chat_id, "Usuario registrado correctamente")
    if user.get("active"):
        send_vip_access(safe_email, "Pago confirmado. Acceso VIP activado")


def handle_addvip_command(chat_id: str | int, sender_id: str | int, arguments: str) -> None:
    if not is_admin(sender_id):
        send_message(chat_id, "No autorizado.")
        return

    telegram_id, email = parse_admin_target(arguments)
    if not email and telegram_id:
        email = find_email_by_telegram_id(telegram_id) or ""

    if not email:
        send_message(chat_id, "Uso: /addvip email@dominio.com o /addvip 123456789 email@dominio.com")
        return

    user = activate_user(email, telegram_id=telegram_id or None)
    delivery = send_vip_access(email, "Acceso VIP activado manualmente")
    send_message(chat_id, f"Acceso VIP activado manualmente para {user['email']}")
    if not delivery.get("ok"):
        send_message(chat_id, "Usuario activado, pero aun no tiene telegram_id enlazado o no se pudo enviar el link.")


def handle_removevip_command(chat_id: str | int, sender_id: str | int, arguments: str) -> None:
    if not is_admin(sender_id):
        send_message(chat_id, "No autorizado.")
        return

    telegram_id, email = parse_admin_target(arguments)
    if not email and telegram_id:
        email = find_email_by_telegram_id(telegram_id) or ""

    if not email:
        send_message(chat_id, "Uso: /removevip email@dominio.com o /removevip 123456789")
        return

    user = deactivate_user(email)
    revoke_vip_access(email, "Acceso VIP removido")
    send_message(chat_id, f"Acceso VIP removido para {user['email']}")


def process_telegram_update(update: dict[str, Any]) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    text = _safe_text(message.get("text"), "")
    chat_id = chat.get("id")
    telegram_id = from_user.get("id")

    if not text or chat_id is None or telegram_id is None:
        return

    command, arguments = normalize_command(text)
    if command == "/start":
        handle_start_command(chat_id, telegram_id)
        return
    if command == "/addvip":
        handle_addvip_command(chat_id, telegram_id, arguments)
        return
    if command == "/removevip":
        handle_removevip_command(chat_id, telegram_id, arguments)
        return

    with state_lock:
        waiting_for_email = str(telegram_id).strip() in pending_email_users
    if waiting_for_email:
        handle_email_registration(chat_id, telegram_id, text)


def telegram_polling_loop() -> None:
    global telegram_update_offset

    if not BOT_TOKEN:
        print("telegram polling omitido: BOT_TOKEN no configurado")
        return

    telegram_api_call("deleteWebhook", {"drop_pending_updates": False})
    print("telegram polling iniciado")

    while True:
        try:
            response = telegram_api_call(
                "getUpdates",
                {
                    "offset": telegram_update_offset,
                    "timeout": TELEGRAM_POLL_TIMEOUT,
                    "allowed_updates": ["message"],
                },
            )
            for update in response.get("result", []):
                telegram_update_offset = max(telegram_update_offset, _safe_int(update.get("update_id"), 0) + 1)
                process_telegram_update(update)
        except Exception as exc:
            print(f"error en telegram polling: {exc}")
            time.sleep(5)


def start_telegram_polling() -> None:
    global telegram_polling_started

    with polling_lock:
        if telegram_polling_started:
            return
        telegram_polling_started = True

    polling_thread = threading.Thread(target=telegram_polling_loop, daemon=True, name="telegram-polling")
    polling_thread.start()


@app.get("/health")
def health() -> Any:
    with state_lock:
        active_users = sum(1 for user in users.values() if user.get("active"))
        total_users = len(users)
    return jsonify(
        {
            "ok": True,
            "service": "trade-signal-webhook",
            "users_total": total_users,
            "users_active": active_users,
        }
    )


@app.post("/webhook")
def webhook() -> Any:
    payload = request.get_json(silent=True) or {}
    signal = normalize_signal_payload(payload)

    print(f"senal recibida: {signal}")

    message = format_signal_message(signal)
    telegram_result = send_message(CHAT_ID, message)

    return jsonify({"ok": True, "signal": signal, "telegram": telegram_result})


@app.post("/webhook/stripe")
def stripe_webhook() -> Any:
    try:
        event = request.get_json(silent=True) or {}
        print(f"stripe recibido: {event.get('type', 'UNKNOWN')}")

        if not validate_stripe_event(event):
            return jsonify({"ok": False, "error": "evento stripe invalido"}), 400

        event_type = _safe_text(event.get("type"), "")
        if event_type == "checkout.session.completed":
            result = handle_checkout_completed(event)
        elif event_type == "invoice.payment_succeeded":
            result = handle_invoice_paid(event)
        elif event_type == "customer.subscription.deleted":
            result = handle_subscription_deleted(event)
        else:
            result = {"ok": True, "action": "ignored", "event_type": event_type}

        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        print(f"error procesando stripe webhook: {exc}")
        return jsonify({"ok": False, "error": "error procesando stripe webhook"}), 500


def main() -> None:
    start_telegram_polling()
    app.run(host="0.0.0.0", port=PORT, debug=False)


if __name__ == "__main__":
    main()
