import os
import time
import threading
import requests
from flask import Flask, request, redirect, render_template_string

app = Flask(__name__)

BRAND_NAME = "SCANNER TRADING VIP"

# Simboli supportati da BingX (formato standard es. BTC-USDT)
SYMBOLS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", 
    "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT", "NEAR-USDT", 
    "UNI-USDT", "RENDER-USDT", "SUI-USDT", "ARB-USDT", "OP-USDT", 
    "IMX-USDT", "ICP-USDT", "APT-USDT", "FIL-USDT", "PEPE-USDT", 
    "DOGE-USDT", "SHIB-USDT", "ATOM-USDT", "XLM-USDT", "ALGO-USDT", 
    "FET-USDT", "INJ-USDT", "LTC-USDT", "BCH-USDT", "ETC-USDT", 
    "SAND-USDT", "MANA-USDT", "AAVE-USDT", "FTM-USDT", "RUNE-USDT", 
    "ZEC-USDT", "TRX-USDT"
]

current_threshold = 75

market_data_cache = {
    symbol: {
        "symbol": symbol.replace("-USDT", ""),
        "pair": symbol.replace("-USDT", "/USDT"),
        "price": 100.0,
        "rsi": 50.0,
        "score": 50,
        "rating": "NEUTRAL",
        "action": "NEUTRAL"
    } for symbol in SYMBOLS
}

def calculate_rsi(prices, period=14):
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

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        high = float(candles[i]['high'])
        low = float(candles[i]['low'])
        prev_close = float(candles[i-1]['close'])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    atr = sum(trs[-period:]) / period
    return atr

def run_scanner_background():
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get("CHANNEL_ID")
    
    print(f"Scanner {BRAND_NAME} collegato alle API live di BingX...")
    price_history = {symbol: [] for symbol in SYMBOLS}
    
    last_signal_time = {symbol: 0 for symbol in SYMBOLS}
    COOLDOWN_SECONDS = 1 * 3600

    while True:
        global current_threshold, market_data_cache
        current_time = time.time()
        
        for symbol in SYMBOLS:
            try:
                # Endpoint API pubblico di BingX per i klines (candele a 1 ora)
                klines_url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval=1h&limit=30"
                response = requests.get(klines_url, timeout=5)
                
                if response.status_code == 200:
                    data_json = response.json()
                    candles = data_json.get("data", [])
                    if isinstance(candles, list) and len(candles) > 0:
                        # BingX restituisce i dati in ordine cronologico inverso o diretto, ordiniamo per timestamp se necessario
                        candles = sorted(candles, key=lambda x: int(x['time']))
                        
                        closes = [float(candle['close']) for candle in candles]
                        current_price = closes[-1]
                        
                        price_history[symbol] = closes
                        rsi = calculate_rsi(price_history[symbol])
                        atr = calculate_atr(candles)
                        
                        if rsi <= 35:
                            score = int(min(99, max(40, 50 + (35 - rsi) * 1.5)))
                            rating = "STRONG BUY" if rsi <= 25 else "BUY"
                            action = "LONG"
                        elif rsi >= 65:
                            score = int(min(99, max(40, 50 + (rsi - 65) * 1.5)))
                            rating = "STRONG SELL" if rsi >= 75 else "SELL"
                            action = "SHORT"
                        else:
                            score = 50
                            rating = "NEUTRAL"
                            action = "NEUTRAL"
                        
                        market_data_cache[symbol] = {
                            "symbol": symbol.replace("-USDT", ""),
                            "pair": symbol.replace("-USDT", "/USDT"),
                            "price": current_price,
                            "rsi": rsi,
                            "score": score,
                            "rating": rating,
                            "action": action
                        }
                        
                        if score >= current_threshold and action != "NEUTRAL" and telegram_token and channel_id:
                            if current_time - last_signal_time[symbol] > COOLDOWN_SECONDS:
                                last_signal_time[symbol] = current_time
                                
                                pair_name = symbol.replace("-USDT", " / USDT")
                                decimals = 5 if current_price < 1 else 2
                                
                                if atr <= 0:
                                    base_step = current_price * 0.015
                                else:
                                    base_step = atr
                                
                                if action == "LONG":
                                    sl = current_price - (base_step * 1.2)
                                    tp1 = current_price + (base_step * 2.0)
                                    tp2 = current_price + (base_step * 3.5)
                                    
                                    sl_pct = ((sl - current_price) / current_price) * 100
                                    tp1_pct = ((tp1 - current_price) / current_price) * 100
                                    tp2_pct = ((tp2 - current_price) / current_price) * 100
                                    
                                    dir_text = "🟢 LONG (ACQUISTO)"
                                    sl_text = f"${sl:.{decimals}f} ({sl_pct:.2f}%)"
                                    tp1_text = f"${tp1:.{decimals}f} (+{tp1_pct:.2f}%)"
                                    tp2_text = f"${tp2:.{decimals}f} (+{tp2_pct:.2f}%)"
                                else:
                                    sl = current_price + (base_step * 1.2)
                                    tp1 = current_price - (base_step * 2.0)
                                    tp2 = current_price - (base_step * 3.5)
                                    
                                    sl_pct = ((sl - current_price) / current_price) * 100
                                    tp1_pct = ((current_price - tp1) / current_price) * 100
                                    tp2_pct = ((current_price - tp2) / current_price) * 100
                                    
                                    dir_text = "🔴 SHORT (VENDITA)"
                                    sl_text = f"${sl:.{decimals}f} (+{abs(sl_pct):.2f}%)"
                                    tp1_text = f"${tp1:.{decimals}f} (-{tp1_pct:.2f}%)"
                                    tp2_text = f"${tp2:.{decimals}f} (-{tp2_pct:.2f}%)"

                                telegram_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                                message = (
                                    f"🚨 **SEGNALE {BRAND_NAME}** 🚨\n\n"
                                    f"🔹 **Asset:** {pair_name}\n"
                                    f"📊 **Direzione:** {dir_text}\n"
                                    f"⭐ **Rating IA:** {rating} (Confluenza: {score}%)\n"
                                    f"📈 **RSI (14):** {rsi}\n"
                                    f"⚡ **Prezzo Live (BingX):** ${current_price:.{decimals}f}\n\n"
                                    f"🛑 **Stop Loss:** {sl_text}\n"
                                    f"🎯 **Take Profit 1:** {tp1_text}\n"
                                    f"🎯 **Take Profit 2:** {tp2_text}"
                                )
                                
                                payload = {
                                    "chat_id": channel_id,
                                    "text": message,
                                    "parse_mode": "Markdown"
                                }
                                requests.post(telegram_url, json=payload)
                                time.sleep(1)
            except Exception as e:
                print(f"Errore nello scanner BingX per {symbol}: {e}")
                
            time.sleep(0.3)

        time.sleep(15)

scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

FULL_DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ brand_name }} - AI Trading Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background-color: #0b0e14; color: #d1d5db; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 20px; border-bottom: 1px solid #1f2937; margin-bottom: 20px; }
        .logo { font-size: 20px; font-weight: bold; color: #38bdf8; text-transform: uppercase; letter-spacing: 1px; }
        .status-badge { background: #064e3b; color: #34d399; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
        .status-dot { width: 8px; height: 8px; background: #34d399; border-radius: 50%; }
        
        .control-panel { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 15px; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 15px; }
        .control-title { font-size: 14px; color: #9ca3af; }
        .control-title strong { color: #f97316; font-size: 16px; }
        .control-form { display: flex; align-items: center; gap: 10px; }
        .control-form input { background: #0b0e14; border: 1px solid #374151; color: #fff; padding: 8px; border-radius: 6px; width: 70px; text-align: center; font-size: 16px; font-weight: bold; }
        .control-form button { background: #2563eb; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-weight: 600; cursor: pointer; }
        .btn-test { background: #374151; color: #38bdf8; text-decoration: none; padding: 8px 12px; border-radius: 6px; font-size: 13px; font-weight: 500; }
        
        .grid-container { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 12px; }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
        .asset-symbol { font-size: 16px; font-weight: bold; color: #fff; }
        .badge-score { padding: 3px 6px; border-radius: 4px; font-weight: bold; font-size: 11px; }
        .score-long { background: rgba(16, 185, 129, 0.2); color: #10b981; }
        .score-short { background: rgba(239, 68, 68, 0.2); color: #ef4444; }
        .score-neutral { background: rgba(107, 114, 128, 0.2); color: #9ca3af; }
        
        .card-body { display: flex; flex-direction: column; gap: 6px; font-size: 12px; color: #9ca3af; }
        .data-row { display: flex; justify-content: space-between; }
        .price-val { color: #fff; font-weight: 600; }
        .rsi-val { color: #38bdf8; font-weight: 600; }
    </style>
    <meta http-equiv="refresh" content="5">
</head>
<body>
    <div class="header">
        <div class="logo">⚡ {{ brand_name }} (BingX Live)</div>
        <div class="status-badge"><div class="status-dot"></div> ONLINE</div>
    </div>

    <div class="control-panel">
        <div class="control-title">
            Soglia Telegram: <strong>{{ threshold }}%</strong>
        </div>
        <form class="control-form" action="/set-threshold" method="POST">
            <input type="number" name="threshold" min="40" max="99" value="{{ threshold }}" required>
            <button type="submit">Aggiorna</button>
        </form>
        <a class="btn-test" href="/test-telegram">🔔 Test Telegram</a>
    </div>

    <h3 style="margin-bottom: 15px; color: #fff; font-size: 16px;">Crypto Assets Live (BingX)</h3>
    <div class="grid-container">
        {% for symbol, data in market_data.items() %}
        <div class="card">
            <div class="card-header">
                <span class="asset-symbol">{{ data.symbol }}</span>
                <span class="badge-score {% if data.action == 'LONG' %}score-long{% elif data.action == 'SHORT' %}score-short{% else %}score-neutral{% endif %}">
                    {{ data.score }}% {{ data.rating }}
                </span>
            </div>
            <div class="card-body">
                <div class="data-row">
                    <span>Prezzo:</span>
                    <span class="price-val">${{ "%.4f"|format(data.price) if data.price < 1 else "%.2f"|format(data.price) }}</span>
                </div>
                <div class="data-row">
                    <span>RSI:</span>
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
    global current_threshold, market_data_cache, BRAND_NAME
    return render_template_string(FULL_DASHBOARD_HTML, brand_name=BRAND_NAME, threshold=current_threshold, market_data=market_data_cache)

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
        "text": f"🚨 **TEST {BRAND_NAME} (BingX)** 🚨\n\nSoglia attuale impostata a: *{current_threshold}%*",
        "parse_mode": "Markdown"
    }
    response = requests.post(url, json=payload)
    return f"Risposta Telegram: {response.text}", response.status_code

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
