import os
import time
import threading
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Lista estesa di tutte le principali crypto presenti nello scanner
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", 
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "NEARUSDT", 
    "UNIUSDT", "RENDERUSDT", "SUIUSDT", "ARBUSDT", "OPUSDT", 
    "IMXUSDT", "ICPUSDT", "APTUSDT", "FILUSDT", "PEPEUSDT", 
    "DOGEUSDT", "SHIBUSDT", "ATOMUSDT", "XLMUSDT", "ALGOUSDT", 
    "FETUSDT", "INJUSDT", "LTCUSDT", "BCHUSDT", "ETCUSDT", 
    "SANDUSDT", "MANAUSDT", "AAVEUSDT", "FTMUSDT", "RUNEUSDT", 
    "ZECUSDT", "TRXUSDT"
]

def calculate_rsi(prices, period=14):
    """Calcola l'RSI reale basato sullo storico dei prezzi"""
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

def run_scanner_background():
    """Scanner in background su tutti gli asset di Binance"""
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get("CHANNEL_ID")
    
    print("Scanner multi-asset AI avviato...")

    price_history = {symbol: [] for symbol in SYMBOLS}

    while True:
        for symbol in SYMBOLS:
            try:
                klines_url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=30"
                response = requests.get(klines_url, timeout=10)
                candles = response.json()
                
                if isinstance(candles, list) and len(candles) > 0:
                    closes = [float(candle[4]) for candle in candles]
                    current_price = closes[-1]
                    
                    price_history[symbol] = closes
                    rsi = calculate_rsi(price_history[symbol])
                    
                    # Calcolo della confluenza basato su indicatori reali
                    score = int(min(99, max(40, 50 + abs(50 - rsi) * 1.2)))
                    
                    # Soglia attiva a 75 per inviare i segnali migliori sul canale
                    if score >= 75 and telegram_token and channel_id:
                        pair_name = symbol.replace("USDT", " / USDT")
                        rating = "STRONG BUY" if score >= 90 else "BUY"
                        
                        decimals = 5 if current_price < 1 else 2
                        sl = current_price * (1 - 0.022)
                        tp1 = current_price * (1 + 0.035)
                        tp2 = current_price * (1 + 0.070)

                        telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                        message = (
                            f"🚨 **SEGNALE OCEAN SCANNER (IA)** 🚨\n\n"
                            f"🔹 **Asset:** {pair_name}\n"
                            f"📊 **Rating IA:** {rating} (Confluenza: {score}%)\n"
                            f"📈 **RSI (14):** {rsi}\n"
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
                        time.sleep(1)
                        
            except Exception as e:
                print(f"Errore nello scanner per {symbol}: {e}")
                
            time.sleep(1.5) # Pausa breve tra un asset e l'altro

        time.sleep(60) # Ciclo completo ripetuto ogni minuto

scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

@app.route('/')
def home():
    return "Ocean Future Scanner Backend Online"

@app.route('/test-telegram')
def test_telegram():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("CHANNEL_ID")
    if not token or not chat_id:
        return "Token o Channel ID mancanti!", 400
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "🚨 **TEST OCEAN SCANNER** 🚨\n\nIl monitoraggio multi-asset è attivo.",
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return f"Risposta Telegram: {response.text}", response.status_code

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
