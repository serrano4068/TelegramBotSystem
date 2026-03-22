from flask import Flask, request
import requests
import os

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message
    }
    requests.post(url, data=data)

@app.route("/")
def home():
    return "Servidor activo 🚀"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    message = f"🚨 {data.get('type')} \n{data.get('message')}"
    send_telegram(message)
    return "OK"

app.run(host="0.0.0.0", port=8080)
