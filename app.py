import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

@app.route("/", methods=["GET"])
def home():
    return jsonify(
        {
            "status": "online",
            "message": "Signal Room Server is up and running!",
        }
    ), 200

# Rotta per il webhook JSON (se un domani usi piani superiori)
@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "No JSON data received"}), 400

        symbol = data.get("symbol", "UNKNOWN")
        action = data.get("action", "BUY").upper()
        entry = data.get("entry", "Market")
        sl = data.get("sl", "N/A")
        tp1 = data.get("tp1", "N/A")
        tp2 = data.get("tp2", "N/A")

        direction_icon = "🟢 LONG" if action == "BUY" else "🔴 SHORT"

        message = (
            f"🔔 **NUOVO SEGNALE RILEVATO** 🔔\n\n"
            f"📊 **Asset:** `{symbol}`\n"
            f"📈 **Direzione:** `{direction_icon}`\n"
            f"🎯 **Entry Zone:** `{entry}`\n"
            f"🛡️ **Stop Loss:** `{sl}`\n"
            f"🎯 **Target 1 (TP1):** `{tp1}`\n"
            f"🎯 **Target 2 (TP2):** `{tp2}`\n\n"
            f"⚠️ *Nessun consiglio finanziario.*"
        )

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }

        response = requests.post(url, json=payload)

        if response.status_code == 200:
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error", "telegram_error": response.text}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# NUOVA Rotta per ricevere messaggi di testo (es. via email/webhook gratuiti)
@app.route("/email-signal", methods=["POST"])
def email_signal():
    try:
        # Prende il testo inviato dalla richiesta grezza o form data
        raw_text = request.form.get("text") or request.data.decode("utf-8")
        
        if not raw_text:
            return jsonify({"status": "error", "message": "No text received"}), 400

        message = (
            f"🔔 **SEGNALE DA ALERT EMAIL** 🔔\n\n"
            f"{raw_text}\n\n"
            f"⚠️ *Nessun consiglio finanziario.*"
        )

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }

        response = requests.post(url, json=payload)

        if response.status_code == 200:
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error", "telegram_error": response.text}), 500

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

