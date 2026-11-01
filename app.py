import os
import time
import threading
import requests
from flask import Flask, request, redirect, render_template_string

app = Flask(__name__)

# Lista estesa di tutte le principali crypto monitorate
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

# Variabile globale per la soglia (modificabile dalla dashboard)
current_threshold = 75

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
    
    print("Scanner multi-asset AI avviato con soglia dinamica...")

    price_history = {symbol: [] for symbol in SYMBOLS}

    while True:
        global current_threshold
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
                    
                    score = int(min(99, max(40, 50 + abs(50 - rsi) * 1.2)))
                    
                    # Usa la soglia dinamica impostata dalla dashboard
                    if score >= current_threshold and telegram_token and channel_id:
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
                
            time.sleep(1.5)

        time.sleep(60)

scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

# Dashboard HTML integrata con stile scuro coerente
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ocean's Future Scanner - Dashboard</title>
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: Arial, sans-serif; text-align: center; padding: 50px; }
        .card { background: #161b22; padding: 30px; border-radius: 12px; display: inline-block; box-shadow: 0 4px 15px rgba(0,0,0,0.5); max-width: 400px; width: 100%; }
        h1 { color: #58a6ff; font-size: 22px; }
        p { color: #8b949e; }
        input[type="number"] { padding: 10px; font-size: 18px; width: 80px; text-align: center; background: #0d1117; color: #fff; border: 1px solid #30363d; border-radius: 6px; }
        button { background: #238636; color: white; border: none; padding: 10px 20px; font-size: 16px; border-radius: 6px; cursor: pointer; margin-top: 10px; }
        button:hover { background: #2ea043; }
        .links { margin-top: 20px; }
        a { color: #58a6ff; text-decoration: none; margin: 0 10px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🌊 Ocean's Future Scanner</h1>
        <p>Stato Backend: <strong style="color: #3fb950;">ONLINE</strong></p>
        <p>Soglia Confluenza Attuale: <strong style="color: #f0883e;">{{ threshold }}%</strong></p>
        
        <form action="/set-threshold" method="POST">
            <label for="threshold">Nuova Soglia (40 - 99):</label><br><br>
            <input type="number" id="threshold" name="threshold" min="40" max="99" value="{{ threshold }}" required><br>
            <button type="submit">Aggiorna Soglia</button>
        </form>
        
        <div class="links">
            <br>
            <a href="/test-telegram">Test Telegram</a>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    global current_threshold
    return render_template_string(HOME_TEMPLATE, threshold=current_threshold)

@app.route('/set-threshold', methods=['POST'])
def set_threshold():
    global current_threshold
    try:
        new_val = int(request.form.get('threshold'))
        if 40 <= new_val <= 99:
            current_threshold = new_val
    except ValueError:
        pass
    return redirect('/')

@app.route('/test-telegram')
def test_telegram():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("CHANNEL_ID")
    if not token or not chat_id:
        return "Token o Channel ID mancanti!", 400
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"🚨 **TEST OCEAN SCANNER** 🚨\n\nSoglia attuale impostata a: *{current_threshold}%*",
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return f"Risposta Telegram: {response.text}", response.status_code

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
