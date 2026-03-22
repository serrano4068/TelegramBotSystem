from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, request


app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PORT = int(os.getenv("PORT", "8080"))


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
        "🚨 TRADE SIGNAL\n\n"
        f"Tipo: {signal['type']}\n"
        f"Precio: {signal['price']:.2f}\n"
        f"Contratos: {signal['contracts']}\n"
        f"Estado: {signal['status']}\n"
        f"Hora: {current_time}"
    )


def send_telegram_message(message: str) -> dict[str, Any]:
    if not BOT_TOKEN or not CHAT_ID:
        print("telegram no configurado: faltan BOT_TOKEN o CHAT_ID")
        return {"ok": False, "error": "BOT_TOKEN o CHAT_ID no configurados"}

    telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": CHAT_ID, "text": message}).encode("utf-8")
    request_data = Request(
        telegram_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request_data, timeout=10) as response:
            raw_response = response.read().decode("utf-8")
            telegram_response = json.loads(raw_response) if raw_response else {}
        print(f"mensaje enviado: {message}")
        return {"ok": True, "telegram_response": telegram_response}
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="ignore")
        print(f"error enviando a Telegram (HTTP): {exc.code} | {error_body}")
        return {"ok": False, "error": f"HTTP {exc.code}", "details": error_body}
    except URLError as exc:
        print(f"error enviando a Telegram (URL): {exc}")
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        print(f"error enviando a Telegram: {exc}")
        return {"ok": False, "error": str(exc)}


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "service": "trade-signal-webhook"})


@app.post("/webhook")
def webhook() -> Any:
    payload = request.get_json(silent=True) or {}
    signal = normalize_signal_payload(payload)

    print(f"senal recibida: {signal}")

    message = format_signal_message(signal)
    telegram_result = send_telegram_message(message)

    return jsonify(
        {
            "ok": True,
            "signal": signal,
            "telegram": telegram_result,
        }
    )


def main() -> None:
    app.run(host="0.0.0.0", port=PORT, debug=False)


if __name__ == "__main__":
    main()
