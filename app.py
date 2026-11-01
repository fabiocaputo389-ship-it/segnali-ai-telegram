import os
import time
import threading
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Configurazioni Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# Database utenti VIP di esempio
users_db = {
    "123456789": True,
}

@app.route('/check-vip', methods=['GET'])
def check_vip():
    telegram_id = request.args.get('user_id')
    if not telegram_id:
        return jsonify({"status": "error", "message": "Missing user_id"}), 400
    is_vip = users_db.get(telegram_id, False)
    return jsonify({
        "telegram_id": telegram_id,
        "is_vip": is_vip,
        "access_level": "VIP" if is_vip else "FREE"
    })

# --- MOTORE SCANNER AI IN BACKGROUND ---
def run_scanner_background():
    print("Background Scanner AI avviato con successo...")
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "SUIUSDT", "AVAXUSDT"]
    
    # Attende 10 secondi prima della prima scansione per lasciare avviare il server
    time.sleep(10)
    
    while True:
        if not TELEGRAM_BOT_TOKEN or not CHANNEL_ID:
            print("Attesa configurazione variabili TELEGRAM_BOT_TOKEN o CHANNEL_ID...")
            time.sleep(60)
            continue
            
        for symbol in symbols:
            try:
                res = requests.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=25")
                candles = res.json()
                
                closes = [float(c[4]) for c in candles]
                current_price = closes[-1]
                prev_price = closes[0]
                
                change_pct = ((current_price - prev_price) / prev_price) * 100
                score = min(99, max(40, int(50 + change_pct * 150)))
                
                if score >= 85:
                    pair_name = symbol.replace("USDT", " / USDT")
                    rating = "STRONG BUY" if score > 80 else "BUY"
                    
                    decimals = 5 if current_price < 1 else 2
                    sl = current_price * (1 - 0.022)
                    tp1 = current_price * (1 + 0.035)
                    tp2 = current_price * (1 + 0.070)
                    
                    
                    
                    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    payload = {
                        "chat_id": CHANNEL_ID,
                        "text": message,
                        "parse_mode": "Markdown. }
                     
                    
                    requests.post(url, json=payload)
                    time.sleep(2)
            except Exception as e:
                print(f"Errore nello scanner per {symbol}: {e}")
                
        # Riesegue il ciclo ogni 30 minuti
        time.sleep(1800)

# Avvia il thread dello scanner in background all'avvio dell'app Flask
scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()
                    message = (
                        f"🚨 **SEGNALE SCANNER AI (LIVE)** 🚨\n\n"
                        f"🔹 **Asset:** {pair_name}\n"
                        f"📊 **Rating IA:** {rating} (Confluenza: {score}%)\n"
                        f"⚡ **Prezzo Live:** ${current_price:.{decimals}f}\n\n"
                        f"🛑 **Stop Loss:** ${sl:.{decimals}f} (-2.2%)\n"
                        f"🎯 **Take Profit 1:** ${tp1:.{decimals}f} (+3.5%)\n"
                        f"🎯 **Take Profit 2:** ${tp2:.{decimals}f} (+7.0%)"
                    )


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
