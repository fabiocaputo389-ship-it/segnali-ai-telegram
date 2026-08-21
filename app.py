import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Leggi i token dalle variabili d'ambiente di Railway (Massima sicurezza)
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


@app.route("/webhook", methods=["POST"])
def webhook():
  try:
    data = request.json
    if not data:
      return jsonify({"status": "error", "message": "No data received"}), 400

    # Estrai i parametri inviati (es. da TradingView)
    symbol = data.get("symbol", "UNKNOWN")
    action = data.get("action", "BUY").upper()
    entry = data.get("entry", "Market")
    sl = data.get("sl", "N/A")
    tp1 = data.get("tp1", "N/A")
    tp2 = data.get("tp2", "N/A")

    # Determina l'emoji in base alla direzione
    direction_icon = "🟢 LONG" if action == "BUY" else "🔴 SHORT"

    # Costruisci il messaggio professionale con Markdown
    message = (
        f"🔔 **NUOVO SEGNALE RILEVATO** 🔔\n\n"
        f"💱 **Asset:** `{symbol}`\n"
        f"📈 **Direzione:** `{direction_icon}`\n"
        f"🎯 **Entry Zone:** `{entry}`\n"
        f"🛡 **Stop Loss:** `{sl}`\n"
        f"🎯 **Target 1 (TP1):** `{tp1}`\n"
        f"🎯 **Target 2 (TP2):** `{tp2}`\n\n"
        f"⚖️ *Ricorda di rischiare solo una percentuale sicura del tuo capitale.*\n"
        f"⚠️ *Nessun consiglio finanziario.*"
    )

    # Invia il messaggio all'API di Telegram
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }

    response = requests.post(url, json=payload)

    if response.status_code == 200:
      return jsonify({"status": "success", "message": "Signal sent"}), 200
    else:
      return (
          jsonify(
              {"status": "error", "telegram_error": response.text}
          ),
          500,
      )

  except Exception as e:
    return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
  
