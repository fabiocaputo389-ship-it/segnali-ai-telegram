import os
import time
import threading
import sqlite3
import hmac
import hashlib
import requests
from flask import (
    Flask,
    request,
    redirect,
    render_template_string,
    session,
    url_for,
    flash,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chiave_segreta_super_sicura_vip")

BRAND_NAME = "SCANNER TRADING VIP"
ADMIN_USERNAME = "Fabio"

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

current_threshold = 80
MAX_ACTIVE_TRADES = 2

bot_settings = {
    "use_trend_following": 1,
    "use_multi_ema": 1,
    "use_rsi_filter": 1,
    "use_whale_tracker": 1,
    "auto_execution": 1
}

market_data_cache = {
    symbol: {
        "symbol": symbol.replace("-USDT", ""),
        "pair": symbol.replace("-USDT", "/USDT"),
        "price": 100.0,
        "rsi_1h": 50.0,
        "rsi_4h": 50.0,
        "ema_9": 0.0,
        "ema_21": 0.0,
        "ema_50": 0.0,
        "whale_status": "NEUTRAL",
        "score": 50,
        "rating": "NEUTRAL",
        "action": "NEUTRAL",
        "indicators_passed": 0,
        "indicators_total": 13,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,
        "sl_pct": 0.0,
        "tp1_pct": 0.0,
        "tp2_pct": 0.0,
        "tp3_pct": 0.0,
        "leverage": 5,
        "recommended_tf": "1H / 4H Multi-TF"
    } for symbol in SYMBOLS
}

def init_db():
    try:
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                bingx_api_key TEXT,
                bingx_secret_key TEXT,
                auto_trading INTEGER DEFAULT 0
            )
        """)
        for col, col_type in [("bingx_api_key", "TEXT"), ("bingx_secret_key", "TEXT"), ("auto_trading", "INTEGER DEFAULT 0")]:
            try:
                cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Errore DB Init: {e}")

init_db()

def get_open_positions(api_key, secret_key):
    try:
        url = "https://open-api.bingx.com/openApi/swap/v2/user/positions"
        timestamp = str(int(time.time() * 1000))
        query_string = f"timestamp={timestamp}"
        signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": api_key}
        full_url = f"{url}?{query_string}&signature={signature}"
        response = requests.get(full_url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json().get("data", [])
            if isinstance(data, list):
                return data
    except Exception as e:
        print(f"Errore recupero posizioni BingX: {e}")
    return []

def set_bingx_leverage(api_key, secret_key, symbol, leverage, side):
    try:
        url = "https://open-api.bingx.com/openApi/swap/v2/trade/leverage"
        timestamp = str(int(time.time() * 1000))
        position_side = "LONG" if side == "BUY" else "SHORT"
        params = {
            "symbol": symbol,
            "leverage": str(leverage),
            "positionSide": position_side,
            "timestamp": timestamp
        }
        query_string = "&".join([f"{key}={params[key]}" for key in sorted(params.keys())]) + f"&timestamp={timestamp}"
        signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": api_key}
        full_url = f"{url}?{query_string}&signature={signature}"
        requests.post(full_url, headers=headers, timeout=5)
    except Exception as e:
        print(f"Errore impostazione leva BingX: {e}")

def execute_bingx_order(api_key, secret_key, symbol, side, leverage=5, quantity=10.0, sl=0.0, tp1=0.0):
    if bot_settings["auto_execution"] == 0:
        return False
    try:
        open_positions = get_open_positions(api_key, secret_key)
        if len(open_positions) >= MAX_ACTIVE_TRADES:
            print(f"Limite massimo di {MAX_ACTIVE_TRADES} posizioni raggiunto. Ordine saltato.")
            return False

        set_bingx_leverage(api_key, secret_key, symbol, leverage, side)
        
        url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
        timestamp = str(int(time.time() * 1000))
        
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": "LONG" if side == "BUY" else "SHORT",
            "type": "MARKET",
            "quantity": str(quantity),
            "timestamp": timestamp
        }

        query_string = "&".join([f"{key}={params[key]}" for key in sorted(params.keys())]) + f"&timestamp={timestamp}"
        signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": api_key}
        full_url = f"{url}?{query_string}&signature={signature}"
        response = requests.post(full_url, headers=headers, timeout=5)
        
        print(f"Risposta ordine BingX per {symbol}: {response.text}")
        return response.status_code == 200
    except Exception as e:
        print(f"Errore esecuzione ordine BingX: {e}")
        return False

def trigger_auto_trades(symbol, action, leverage, sl, tp1):
    if bot_settings["auto_execution"] == 0:
        return
    side = "BUY" if action == "LONG" else "SELL"
    owner_api = os.environ.get("BINGX_API_KEY")
    owner_secret = os.environ.get("BINGX_SECRET_KEY")
    if owner_api and owner_secret:
        execute_bingx_order(owner_api, owner_secret, symbol, side, leverage, 10.0, sl, tp1)
    try:
        init_db()
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT bingx_api_key, bingx_secret_key FROM users WHERE auto_trading = 1 AND bingx_api_key IS NOT NULL AND bingx_secret_key IS NOT NULL")
        users = cursor.fetchall()
        conn.close()
        for u_api, u_sec in users:
            execute_bingx_order(u_api, u_sec, symbol, side, leverage, 10.0, sl, tp1)
    except Exception as e:
        print(f"Errore recupero utenti auto-trading: {e}")

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
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
    return round(100 - (100 / (1 + (avg_gain / avg_loss))), 2)

def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

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
    return sum(trs[-period:]) / period

def analyze_whale_volume(candles):
    if len(candles) < 10:
        return "NEUTRAL", 0
    volumes = [float(c['volume']) for c in candles]
    avg_volume = sum(volumes[:-1]) / len(volumes[:-1]) if len(volumes) > 1 else volumes[-1]
    last_volume = volumes[-1]
    is_whale_activity = last_volume > (avg_volume * 1.8)
    last_close = float(candles[-1]['close'])
    prev_close = float(candles[-1]['open'])
    
    if is_whale_activity:
        if last_close > prev_close:
            return "WHALE BUY 🐳", 1
        else:
            return "WHALE SELL 🐳", -1
    return "NORMAL", 0

def fetch_bingx_klines(symbol, interval):
    try:
        url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval={interval}&limit=50"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if isinstance(data, list) and len(data) > 0:
                return sorted(data, key=lambda x: int(x['time']))
    except Exception as e:
        print(f"Errore API BingX {symbol}: {e}")
    return []

def run_scanner_background():
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get("CHANNEL_ID")
    last_signal_time = {symbol: 0 for symbol in SYMBOLS}
    COOLDOWN_SECONDS = 3600

    while True:
        global current_threshold, market_data_cache, bot_settings
        current_time = time.time()
        for symbol in SYMBOLS:
            try:
                candles_1h = fetch_bingx_klines(symbol, "1h")
                candles_4h = fetch_bingx_klines(symbol, "4h")
                if candles_1h and candles_4h:
                    closes_1h = [float(c['close']) for c in candles_1h]
                    closes_4h = [float(c['close']) for c in candles_4h]
                    current_price = closes_1h[-1]
                    
                    rsi_1h = calculate_rsi(closes_1h)
                    rsi_4h = calculate_rsi(closes_4h)
                    ema_9 = calculate_ema(closes_1h, 9)
                    ema_21 = calculate_ema(closes_1h, 21)
                    ema_50 = calculate_ema(closes_1h, 50)
                    atr = calculate_atr(candles_1h)
                    whale_txt, whale_score_val = analyze_whale_volume(candles_1h)

                    bullish_points = 0
                    bearish_points = 0
                    total_indicators = 13

                    if bot_settings["use_rsi_filter"] == 1:
                        if rsi_1h > 50: bullish_points += 1
                        else: bearish_points += 1
                        if rsi_4h > 50: bullish_points += 1
                        else: bearish_points += 1
                        if rsi_1h > 55: bullish_points += 1
                        elif rsi_1h < 45: bearish_points += 1
                        if rsi_4h > 52: bullish_points += 1
                        elif rsi_4h < 48: bearish_points += 1
                    else:
                        bullish_points += 2
                        bearish_points += 2

                    if bot_settings["use_multi_ema"] == 1:
                        if ema_9 > ema_21: bullish_points += 1
                        else: bearish_points += 1
                        if current_price > ema_50: bullish_points += 1
                        else: bearish_points += 1
                        if ema_21 > ema_50: bullish_points += 1
                        else: bearish_points += 1
                        if current_price > ema_9: bullish_points += 1
                        else: bearish_points += 1
                    else:
                        bullish_points += 2
                        bearish_points += 2

                    if bot_settings["use_trend_following"] == 1:
                        if closes_1h[-1] > closes_1h[-2]: bullish_points += 1
                        else: bearish_points += 1
                        if closes_1h[-2] > closes_1h[-3]: bullish_points += 1
                        else: bearish_points += 1
                        if closes_4h[-1] > closes_4h[-2]: bullish_points += 1
                        else: bearish_points += 1
                    else:
                        bullish_points += 2
                        bearish_points += 1

                    if bot_settings["use_whale_tracker"] == 1:
                        if whale_score_val == 1: bullish_points += 2
                        elif whale_score_val == -1: bearish_points += 2
                        else:
                            bullish_points += 1
                            bearish_points += 1
                    else:
                        bullish_points += 1
                        bullish_points += 1

                    action, score, rating, passed_indicators = "NEUTRAL", 50, "NEUTRAL", 0

                    if bullish_points >= 8 and (rsi_1h >= 53 or bot_settings["use_rsi_filter"] == 0):
                        action = "LONG"
                        passed_indicators = bullish_points
                        score = min(99, max(60, int((bullish_points / total_indicators) * 100)))
                        rating = "STRONG BUY" if score >= 85 else "BUY"
                    elif bearish_points >= 8 and (rsi_1h <= 47 or bot_settings["use_rsi_filter"] == 0):
                        action = "SHORT"
                        passed_indicators = bearish_points
                        score = min(99, max(60, int((bearish_points / total_indicators) * 100)))
                        rating = "STRONG SELL" if score >= 85 else "SELL"

                    base_step = atr if atr > 0 else current_price * 0.015
                    leverage = 10 if score >= 90 else (5 if score >= 80 else 3)
                    recommended_tf = "1H / 4H (Conferme Avanzate)" if score >= 85 else "1H (Momentum)"

                    # MODIFICA: 3 Take Profit più ravvicinati e realistici
                    if action == "LONG":
                        sl = current_price - (base_step * 1.2)
                        tp1 = current_price + (base_step * 0.8)
                        tp2 = current_price + (base_step * 1.6)
                        tp3 = current_price + (base_step * 2.5)
                    elif action == "SHORT":
                        sl = current_price + (base_step * 1.2)
                        tp1 = current_price - (base_step * 0.8)
                        tp2 = current_price - (base_step * 1.6)
                        tp3 = current_price - (base_step * 2.5)
                    else:
                        sl, tp1, tp2, tp3 = current_price * 0.98, current_price * 1.01, current_price * 1.02, current_price * 1.03

                    sl_pct = round(((sl - current_price) / current_price) * 100, 2)
                    tp1_pct = round(((tp1 - current_price) / current_price) * 100, 2)
                    tp2_pct = round(((tp2 - current_price) / current_price) * 100, 2)
                    tp3_pct = round(((tp3 - current_price) / current_price) * 100, 2)

                    market_data_cache[symbol] = {
                        "symbol": symbol.replace("-USDT", ""),
                        "pair": symbol.replace("-USDT", "/USDT"),
                        "price": current_price,
                        "rsi_1h": rsi_1h,
                        "rsi_4h": rsi_4h,
                        "ema_9": ema_9,
                        "ema_21": ema_21,
                        "ema_50": ema_50,
                        "whale_status": whale_txt,
                        "score": score,
                        "rating": rating,
                        "action": action,
                        "indicators_passed": passed_indicators if action != "NEUTRAL" else int(total_indicators / 2),
                        "indicators_total": total_indicators,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "tp3": tp3,
                        "sl_pct": sl_pct,
                        "tp1_pct": tp1_pct,
                        "tp2_pct": tp2_pct,
                        "tp3_pct": tp3_pct,
                        "leverage": leverage,
                        "recommended_tf": recommended_tf
                    }

                    if score >= current_threshold and action != "NEUTRAL" and telegram_token and channel_id:
                        if current_time - last_signal_time[symbol] > COOLDOWN_SECONDS:
                            last_signal_time[symbol] = current_time
                            trigger_auto_trades(symbol, action, leverage, sl, tp1)
                            
                            pair_name = symbol.replace("-USDT", " / USDT")
                            decimals = 5 if current_price < 1 else 2
                            dir_text = "🟢 LONG (ACQUISTO)" if action == "LONG" else "🔴 SHORT (VENDITA)"

                            message = (
                                f"🔥 **SEGNALE VIP ISTITUZIONALE ({BRAND_NAME})** 🔥\n\n"
                                f"🔹 **Asset:** {pair_name}\n"
                                f"📊 **Direzione:** {dir_text}\n"
                                f"⭐ **Confluenza IA:** {score}% ({rating})\n"
                                f"📈 **Potenza Segnale:** {passed_indicators} su {total_indicators} indicatori allineati\n"
                                f"🐳 **Flusso Balene:** {whale_txt}\n"
                                f"⏳ **Timeframe:** {recommended_tf}\n"
                                f"⚙️ **Leva:** {leverage}x | **RSI 1H:** {rsi_1h} | **RSI 4H:** {rsi_4h}\n"
                                f"⚡ **Ingresso:** ${current_price:.{decimals}f}\n\n"
                                f"🛑 **Stop Loss:** ${sl:.{decimals}f} ({sl_pct}%)\n"
                                f"🎯 **Take Profit 1:** ${tp1:.{decimals}f} ({tp1_pct}%)\n"
                                f"🎯 **Take Profit 2:** ${tp2:.{decimals}f} ({tp2_pct}%)\n"
                                f"🎯 **Take Profit 3:** ${tp3:.{decimals}f} ({tp3_pct}%)\n\n"
                                f"🚀 *Eseguito in automatico con leva {leverage}x!*"
                            )
                            requests.post(f"https://api.telegram.org/bot{telegram_token}/sendMessage", json={"chat_id": channel_id, "text": message, "parse_mode": "Markdown"})
                            time.sleep(1)
            except Exception as e:
                print(f"Errore scanner: {e}")
            time.sleep(0.3)
        time.sleep(15)

scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

BASE_HTML = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ brand_name }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background-color: #0b0e14; color: #d1d5db; font-family: sans-serif; padding: 15px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 15px; border-bottom: 1px solid #1f2937; margin-bottom: 20px; flex-wrap: wrap; gap: 10px;}
        .logo { font-size: 18px; font-weight: bold; color: #38bdf8; }
        .nav a { color: #38bdf8; text-decoration: none; margin-left: 10px; background: #111827; padding: 6px 10px; border-radius: 6px; border: 1px solid #374151; font-size: 13px;}
        .panel { background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 15px; margin-bottom: 20px; }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; font-size: 12px; color: #9ca3af; margin-bottom: 4px; }
        .form-group input, .form-group select { width: 100%; background: #0b0e14; border: 1px solid #374151; color: #fff; padding: 8px; border-radius: 6px; font-size: 14px; }
        .btn { background: #2563eb; color: #fff; border: none; padding: 8px 14px; border-radius: 6px; font-weight: 600; cursor: pointer; width: 100%; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 12px; position: relative; }
        .flash { background: #1e3a8a; color: #bfdbfe; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 13px; }
        .badge-long { background: rgba(16, 185, 129, 0.2); color: #34d399; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
        .badge-short { background: rgba(239, 68, 68, 0.2); color: #f87171; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
        .badge-neutral { background: rgba(156, 163, 175, 0.2); color: #9ca3af; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }
        .power-bar-bg { background: #1f2937; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 6px; margin-bottom: 10px; }
        .power-bar-fill { height: 100%; background: linear-gradient(90deg, #38bdf8, #10b981); }
        .levels-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; text-align: center; background: #0b0e14; padding: 6px; border-radius: 6px; border: 1px solid #1f2937; margin-top: 8px; }
        .level-item span { display: block; font-size: 9px; color: #9ca3af; text-transform: uppercase; }
        .level-item b { font-size: 11px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo">⚡ {{ brand_name }}</div>
        <div class="nav">
            <a href="/">Scanner</a>
            <a href="/settings">Configura API</a>
            {% if session.get('username') == 'Fabio' %}<a href="/admin">Admin Panel</a>{% endif %}
            {% if session.get('user_id') %}<a href="/logout">Logout</a>{% else %}<a href="/login">Login</a>{% endif %}
        </div>
    </div>
    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
</body>
</html>
"""

@app.route("/")
def index():
    cards_html = ""
    for symbol, data in market_data_cache.items():
        action_class = "badge-neutral"
        if data["action"] == "LONG":
            action_class = "badge-long"
        elif data["action"] == "SHORT":
            action_class = "badge-short"
            
        decimals = 5 if data["price"] < 1 else 2
        power_pct = int((data["indicators_passed"] / data["indicators_total"]) * 100)

        cards_html += f"""
        <div class="card" style="border-left: 4px solid {'#34d399' if data['action']=='LONG' else ('#f87171' if data['action']=='SHORT' else '#374151')};">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                <b style="font-size: 15px; color: #fff;">{data['symbol']} <span style="font-size: 11px; color: #9ca3af;">• {data['leverage']}x</span></b>
                <span class="{action_class}">{data['action']} ({data['score']}%)</span>
            </div>
            
            <div style="font-size: 12px; color: #9ca3af; margin-bottom: 4px; display: flex; justify-content: space-between;">
                <span>⚡ POTENZA SEGNALE</span>
                <span style="color: #38bdf8; font-weight: bold;">{data['indicators_passed']}/{data['indicators_total']} ({power_pct}%)</span>
            </div>
            <div class="power-bar-bg">
                <div class="power-bar-fill" style="width: {power_pct}%;"></div>
            </div>

            <div style="font-size: 12px; margin-bottom: 6px; display: flex; justify-content: space-between;">
                <span>Ingresso: <b style="color: #fff;">${data['price']:.{decimals}f}</b></span>
                <span style="color: #fbbf24;">{data['whale_status']}</span>
            </div>

            <div class="levels-grid">
                <div class="level-item">
                    <span>Stop Loss</span>
                    <b style="color: #f87171;">${data['sl']:.{decimals}f}</b>
                </div>
                <div class="level-item" style="border-left: 1px solid #1f2937;">
                    <span>TP 1</span>
                    <b style="color: #34d399;">${data['tp1']:.{decimals}f}</b>
                </div>
                <div class="level-item" style="border-left: 1px solid #1f2937;">
                    <span>TP 2</span>
                    <b style="color: #34d399;">${data['tp2']:.{decimals}f}</b>
                </div>
                <div class="level-item" style="border-left: 1px solid #1f2937;">
                    <span>TP 3</span>
                    <b style="color: #34d399;">${data['tp3']:.{decimals}f}</b>
                </div>
            </div>
        </div>
        """

    page_content = f"""
    <div class="panel">
        <h2 style="font-size: 16px; color: #fff; margin-bottom: 5px;">Plancia Segnali di Mercato</h2>
        <p style="font-size: 12px; color: #9ca3af;">Monitoraggio in tempo reale con livelli chiave, 3 Take Profit scalettati e potenze di trading.</p>
    </div>
    <div class="grid">
        {cards_html}
    </div>
    """
    return render_template_string(BASE_HTML, brand_name=BRAND_NAME, content=page_content)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, password FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user and check_password_hash(user[2], password):
            session["user_id"] = user[0]
            session["username"] = user[1]
            flash("Login effettuato con successo!")
            return redirect(url_for("settings"))
        else:
            flash("Credenziali non valide.")
    
    form_content = """
    <div class="panel" style="max-width: 400px; margin: 0 auto;">
        <h2 style="font-size: 16px; color: #fff; margin-bottom: 15px;">Accedi al Pannello</h2>
        <form method="post">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required>
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit" class="btn">Login</button>
        </form>
    </div>
    """
    return render_template_string(BASE_HTML, brand_name=BRAND_NAME, content=form_content)

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "user_id" not in session:
        return redirect(url_for("login"))
    
    conn = sqlite3.connect("trading_bot.db")
    cursor = conn.cursor()
    if request.method == "POST":
        api_key = request.form.get("api_key")
        secret_key = request.form.get("secret_key")
        auto_trading = int(request.form.get("auto_trading", 0))
        cursor.execute("UPDATE users SET bingx_api_key = ?, bingx_secret_key = ?, auto_trading = ? WHERE id = ?", (api_key, secret_key, auto_trading, session["user_id"]))
        conn.commit()
        flash("Impostazioni salvate con successo!")
        
    cursor.execute("SELECT bingx_api_key, bingx_secret_key, auto_trading FROM users WHERE id = ?", (session["user_id"],))
    user_data = cursor.fetchone()
    conn.close()
    
    api_key = user_data[0] if user_data and user_data[0] else ""
    secret_key = user_data[1] if user_data and user_data[1] else ""
    auto_trading = user_data[2] if user_data and len(user_data) > 2 and user_data[2] is not None else 0
    
    form_content = f"""
    <div class="panel" style="max-width: 500px; margin: 0 auto;">
        <h2 style="font-size: 16px; color: #fff; margin-bottom: 15px;">Configurazione API BingX</h2>
        <form method="post">
            <div class="form-group">
                <label>BingX API Key</label>
                <input type="text" name="api_key" value="{api_key}">
            </div>
            <div class="form-group">
                <label>BingX Secret Key</label>
                <input type="password" name="secret_key" value="{secret_key}">
            </div>
            <div class="form-group">
                <label>Auto Trading</label>
                <select name="auto_trading">
                    <option value="1" {'selected' if auto_trading == 1 else ''}>Attivo (Esecuzione Automatica)</option>
                    <option value="0" {'selected' if auto_trading == 0 else ''}>Disattivo (Solo Monitoraggio)</option>
                </select>
            </div>
            <button type="submit" class="btn">Salva Impostazioni</button>
        </form>
    </div>
    """
    return render_template_string(BASE_HTML, brand_name=BRAND_NAME, content=form_content)

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if session.get("username") != ADMIN_USERNAME:
        return redirect(url_for("index"))
    
    global current_threshold, bot_settings
    if request.method == "POST":
        try:
            current_threshold = int(request.form.get("threshold", 80))
            bot_settings["use_trend_following"] = int(request.form.get("use_trend_following", 1))
            bot_settings["use_multi_ema"] = int(request.form.get("use_multi_ema", 1))
            bot_settings["use_rsi_filter"] = int(request.form.get("use_rsi_filter", 1))
            bot_settings["use_whale_tracker"] = int(request.form.get("use_whale_tracker", 1))
            bot_settings["auto_execution"] = int(request.form.get("auto_execution", 1))
            flash("Parametri di amministrazione aggiornati con successo!")
        except Exception as e:
            flash(f"Errore aggiornamento: {e}")

    admin_content = f"""
    <div class="panel" style="max-width: 600px; margin: 0 auto;">
        <h2 style="font-size: 16px; color: #fff; margin-bottom: 15px;">Pannello di Controllo Admin</h2>
        <form method="post">
            <div class="form-group">
                <label>Soglia Minima Segnale (%)</label>
                <input type="number" name="threshold" value="{current_threshold}">
            </div>
            <div class="form-group">
                <label>Filtro Trend Following</label>
                <select name="use_trend_following">
                    <option value="1" {'selected' if bot_settings["use_trend_following"]==1 else ''}>Attivo</option>
                    <option value="0" {'selected' if bot_settings["use_trend_following"]==0 else ''}>Disattivo</option>
                </select>
            </div>
            <div class="form-group">
                <label>Filtro Multi-EMA</label>
                <select name="use_multi_ema">
                    <option value="1" {'selected' if bot_settings["use_multi_ema"]==1 else ''}>Attivo</option>
                    <option value="0" {'selected' if bot_settings["use_multi_ema"]==0 else ''}>Disattivo</option>
                </select>
            </div>
            <div class="form-group">
                <label>Filtro RSI</label>
                <select name="use_rsi_filter">
                    <option value="1" {'selected' if bot_settings["use_rsi_filter"]==1 else ''}>Attivo</option>
                    <option value="0" {'selected' if bot_settings["use_rsi_filter"]==0 else ''}>Disattivo</option>
                </select>
            </div>
            <div class="form-group">
                <label>Tracciamento Balene (Whale Tracker)</label>
                <select name="use_whale_tracker">
                    <option value="1" {'selected' if bot_settings["use_whale_tracker"]==1 else ''}>Attivo</option>
                    <option value="0" {'selected' if bot_settings["use_whale_tracker"]==0 else ''}>Disattivo</option>
                </select>
            </div>
            <div class="form-group">
                <label>Esecuzione Automatica ordini reali</label>
                <select name="auto_execution">
                    <option value="1" {'selected' if bot_settings["auto_execution"]==1 else ''}>Attivo</option>
                    <option value="0" {'selected' if bot_settings["auto_execution"]==0 else ''}>Disattivo</option>
                </select>
            </div>
            <button type="submit" class="btn">Salva Parametri Admin</button>
        </form>
    </div>
    """
    return render_template_string(BASE_HTML, brand_name=BRAND_NAME, content=admin_content)

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout effettuato.")
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
