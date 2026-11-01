import os
import time
import threading
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Lista di asset da monitorare su Binance
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

def run_scanner_background():
    """Background thread per scansionare il mercato e inviare segnali al canale Telegram"""
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get("CHANNEL_ID")
    
    print("Background Scanner AI avviato con successo...")

    while True:
        if not telegram_token or not channel_id:
            print("Telegram Token o Channel ID mancanti nelle variabili d'ambiente.")
            time.sleep(60)
            continue

        for symbol in SYMBOLS:
            try:
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
                response = requests.get(url, timeout=10)
                data = response.json()
                
                if "price" in data:
                    current_price = float(data["price"])
                    
                    # Simulazione logica di confluenza basata sul prezzo
                    # (Sostituibile in futuro con indicatori tecnici avanzati)
                    import random
                    score = random.randint(75, 92) # Genera un punteggio di test
                    
                    if score >= 85:
                        pair_name = symbol.replace("USDT", " / USDT")
                        rating = "STRONG BUY" if score > 80 else "BUY"
                        
                        decimals = 5 if current_price < 1 else 2
                        sl = current_price * (1 - 0.022)
                        tp1 = current_price * (1 + 0.035)
                        tp2 = current_price * (1 + 0.070)

                        telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                        
                        message = (
                            f"🚨 **SEGNALE SCANNER AI (LIVE)** 🚨\n\n"
                            f"🔹 **Asset:** {pair_name}\n"
                            f"📊 **Rating IA:** {rating} (Confluenza: {score}%)\n"
                            f"⚡ **Prezzo Live:** ${current_price:.{decimals}f}\n\n"
                            f"🛑 **Stop Loss:** ${sl:.{decimals}f} (-2.2%)\n"
                            f"🎯 **Take Profit 1:** ${tp1:.{decimals}f} (+3.5%)\n"
                            f"🎯 **Take Profit 2:** ${tp2:.{decimals}f} (+7.0%)"
                        )
                        
                        payload = {
                            "chat_id": channel_id,
                            "text": message,
                            "parse_mode": "Markdown"
                        }
                        
                        requests.post(telegram_url, json=payload)
                        time.sleep(2)
                        
            except Exception as e:
                print(f"Errore nello scanner per {symbol}: {e}")
                
            # Pausa breve tra un asset e l'altro
            time.sleep(5)

        # Riesegue il ciclo di scansione principale ogni 30 minuti
        time.sleep(1800)

# Avvia il thread dello scanner in background all'avvio dell'app Flask
scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

@app.route('/')
def home():
    return "Scanner Trading AI Mini App Backend Online"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
