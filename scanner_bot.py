import os
import requests
import time

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

def scan_and_broadcast_signals():
    if not TELEGRAM_BOT_TOKEN or not CHANNEL_ID:
        print("Variabili d'ambiente TELEGRAM_BOT_TOKEN o CHANNEL_ID mancanti su Render!")
        return

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "SUIUSDT", "AVAXUSDT"]
    print("Avvio scansione intelligente del mercato...")
    
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
                
                message = (
                    f"🚨 **SEGNALE SCANNER AI (LIVE)** 🚨\n\n"
                    f"🔹 **Asset:** {pair_name}\n"
                    f"📊 **Rating IA:** {rating} (Confluence: {score}%)\n"
                    f"⚡ **Prezzo Live:** ${current_price:.{decimals}f}\n\n"
                    f"🛑 **Stop Loss:** ${sl:.{decimals}f} (-2.2%)\n"
                    f"🎯 **Take Profit 1:** ${tp1:.{decimals}f} (+3.5%)\n"
                    f"🎯 **Take Profit 2:** ${tp2:.{decimals}f} (+7.0%)\n\n"
                    f"👉 *Apri la Mini App nel bot per analizzare i grafici live e i timeframe!*"
                )
                
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                payload = {
                    "chat_id": CHANNEL_ID,
                    "text": message,
                    "parse_mode": "Markdown"
                }
                
                response = requests.post(url, json=payload)
                if response.status_code == 200:
                    print(f"Segnale inviato con successo per {pair_name}!")
                else:
                    print(f"Errore Telegram per {pair_name}: {response.text}")
                    
                time.sleep(1)
                
        except Exception as e:
            print(f"Errore nello scanner per {symbol}: {e}")

if __name__ == "__main__":
    while True:
        scan_and_broadcast_signals()
        time.sleep(1800)
      
