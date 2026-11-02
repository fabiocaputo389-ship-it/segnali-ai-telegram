import os
import time
import threading
import requests
from flask import Flask, request, redirect, render_template_string

app = Flask(__name__)

# Lista estesa di tutte le principali crypto monitorate dallo scanner
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

# Variabile globale per la soglia (modificabile live dalla dashboard)
current_threshold = 75

# Memoria in tempo reale dei dati calcolati per la grafica
market_data_cache = {}

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
        global current_threshold, market_data_cache
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
                    rating = "STRONG BUY" if score >= 85 else ("BUY" if score >= 65 else "NEUTRAL")
                    
                    # Salva i dati in cache per aggiornare la dashboard HTML
                    market_data_cache[symbol] = {
                        "symbol": symbol.replace("USDT", ""),
                        "pair": symbol.replace("USDT", "/USDT"),
                        "price": current_price,
                        "rsi": rsi,
                        "score": score,
                        "rating": rating
                    }
                    
                    # Controlla la soglia per l'invio immediato su Telegram
                    if score >= current_threshold and telegram_token and channel_id:
                        pair_name = symbol.replace("USDT", " / USDT")
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
                
            time.sleep(1.2)

        time.sleep(30)

scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

# Interfaccia Grafica Completa (Ocean's Future Scanner Theme)
FULL_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OCEAN'S FUTURE SCANNER - AI Trading Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background-color: #0b0e14; color: #d1d5db; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid #1f2937; margin-bottom: 20px; }
        .logo { font-size: 22px; font-weight: bold; color: #38bdf8; text-transform: uppercase; letter-spacing: 1px; }
        .status-badge { background: #064e3b; color: #34d399; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        .status-dot { width: 8px; height: 8px; background: #34d399; border-radius: 50%; }
        
        /* Box Controllo Soglia */
        .control-panel { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 15px 25px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        .control-title { font-size: 15px; color: #9ca3af; }
        .control-title strong { color: #f97316; font-size: 18px; }
        .control-form { display: flex; align-items: center; gap: 10px; }
        .control-form input { background: #0b0e14; border: 1px solid #374151; color: #fff; padding: 8px 12px; border-radius: 6px; width: 80px; text-align: center; font-size: 16px; font-weight: bold; }
        .control-form button { background: #2563eb; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; transition: 0.2s; }
        .control-form button:hover { background: #1d4ed8; }
        .btn-test { background: #374151; color: #38bdf8; text-decoration: none; padding: 8px 14px; border-radius: 6px; font-size: 14px; font-weight: 500; }
        
        /* Table Grid Asset */
        .grid-container { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 15px; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 15px; transition: transform 0.2s; }
        .card:hover { border-color: #38bdf8; transform: translateY(-2px); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .asset-symbol { font-size: 18px; font-weight: bold; color: #fff; }
        .badge-score { padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 12px; }
        .score-high { background: rgba(16, 185, 129, 0.2); color: #10b981; }
        .score-mid { background: rgba(245, 158, 11, 0.2); color: #f59e0b; }
        
        .card-body { display: flex; flex-direction: column; gap: 8px; font-size: 13px; color: #9ca3af; }
        .data-row { display: flex; justify-content: space-between; }
        .price-val { color: #fff; font-weight: 600; }
        .rsi-val { color: #38bdf8; font-weight: 600; }
    </style>
    <meta http-equiv="refresh" content="10">
</head>
<body>
    <div class="header">
        <div class="logo">🌊 Ocean's Future Scanner</div>
        <div class="status-badge"><div class="status-dot"></div> SCANNER ONLINE</div>
    </div>

    <!-- Pannello Controllo Soglia Telegram -->
    <div class="control-panel">
        <div class="control-title">
            Soglia Confluenza Telegram Attuale: <strong>{{ threshold }}%</strong>
        </div>
        <form class="control-form" action="/set-threshold" method="POST">
            <input type="number" name="threshold" min="40" max="99" value="{{ threshold }}" required>
            <button type="submit">Imposta Soglia</button>
        </form>
        <a class="btn-test" href="/test-telegram">🔔 Test Telegram</a>
    </div>

    <!-- Griglia Crypto Assets -->
    <h3 style="margin-bottom: 15px; color: #fff;">Top Crypto Assets Live (Binance API)</h3>
    <div class="grid-container">
        {% for symbol, data in market_data.items() %}
        <div class="card">
            <div class="card-header">
                <span class="asset-symbol">{{ data.symbol }}</span>
                <span class="badge-score {% if data.score >= 75 %}score-high{% else %}score-mid{% endif %}">
                    {{ data.score }}% {{ data.rating }}
                </span>
            </div>
            <div class="card-body">
                <div class="data-row">
                    <span>Prezzo Live:</span>
                    <span class="price-val">${{ "%.4f"|format(data.price) if data.price < 1 else "%.2f"|format(data.price) }}</span>
                </div>
                <div class="data-row">
                    <span>RSI (14):</span>
                    <span class="rsi-val">{{ data.rsi }}</span>
                </div>
            </div>
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    global current_threshold, market_data_cache
    return render_template_string(FULL_DASHBOARD_HTML, threshold=current_threshold, market_data=market_data_cache)

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
        "text": f"🚨 **TEST OCEAN SCANNER** 🚨\n\nSoglia attuale notifiche impostata a: *{current_threshold}%*",
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return f"Risposta Telegram: {response.text}", response.status_code

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
    
