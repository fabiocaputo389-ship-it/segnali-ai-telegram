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
MAX_ACTIVE_TRADES = 999  # Posizioni illimitate

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
    
    # 1. Legge le variabili d'ambiente di Render se configurate lì
    owner_api = os.environ.get("BINGX_API_KEY")
    owner_secret = os.environ.get("BINGX_SECRET_KEY")
    
    if owner_api and owner_secret:
        execute_bingx_order(owner_api, owner_secret, symbol, side, leverage, 10.0, sl, tp1)
        
    # 2. Legge anche gli utenti dal database che hanno attivato l'auto-trading
    try:
        init_db()
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT bingx_api_key, bingx_secret_key FROM users WHERE auto_trading = 1 AND bingx_api_key IS NOT NULL AND bingx_secret_key IS NOT NULL")
        users = cursor.fetchall()
        conn.close()
        for u_api, u_sec in users:
            # Evita di duplicare se l'utente usa le stesse chiavi di Render
            if u_api != owner_api:
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
    # Legge il token Telegram e la chat ID direttamente dalle variabili d'ambiente di Render
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
                        bearish_points += 1

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
                                f"📈 **Raccomandazione:** {passed_indicators} su {total_indicators} indicatori allineati\n"
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
        .flash { background: #1e3a8a; color: #93c5fd; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 13px; }
        
        .gauge-container { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; border-bottom: 1px solid #1f2937; padding-bottom: 8px; }
        .gauge { width: 45px; height: 45px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: bold; font-size: 11px; border: 3px solid #374151; }
        .gauge-long { border-color: #10b981; color: #10b981; background: rgba(16,185,129,0.1); }
        .gauge-short { border-color: #ef4444; color: #ef4444; background: rgba(239,68,68,0.1); }
        .gauge-neutral { border-color: #6b7280; color: #9ca3af; background: rgba(107,114,128,0.1); }
        
        .power-bar-bg { background: #1f2937; height: 8px; border-radius: 4px; overflow: hidden; margin: 6px 0 10px 0; }
        .power-bar-fill { height: 100%; background: linear-gradient(90deg, #38bdf8, #10b981); }
        
        .chart-btn { display: inline-block; background: rgba(56, 189, 248, 0.1); color: #38bdf8; border: 1px solid #38bdf8; padding: 4px 8px; border-radius: 4px; font-size: 11px; text-decoration: none; font-weight: bold; margin-top: 8px; text-align: center; width: 100%; }
        .chart-btn:hover { background: #38bdf8; color: #000; }
        
        .signal-box { background: rgba(56, 189, 248, 0.05); border: 1px solid #38bdf8; border-radius: 8px; padding: 12px; margin-bottom: 20px; }
    </style>
    {refresh_tag}
</head>
<body>
    <div class="header">
        <div class="logo">⚡ {{ brand_name }}</div>
        <div class="nav">
            {% if session.get('user_id') %}
                <span>Utente: <strong>{{ session.get('username') }}</strong></span>
                <a href="/settings">⚙️ API</a>
                <a href="/logout" style="color: #ef4444;">Esci</a>
            {% else %}
                <a href="/login">Accedi</a>
                <a href="/register">Registrati</a>
            {% endif %}
        </div>
    </div>
    {% with messages = get_flashed_messages() %}
      {% if messages %}{% for message in messages %}<div class="flash">{{ message }}</div>{% endfor %}{% endif %}
    {% endwith %}
    {{ content | safe }}
</body>
</html>
"""

@app.route("/")
def home():
    try:
        active_signals = {s: d for s, d in market_data_cache.items() if d["score"] >= current_threshold and d["action"] != "NEUTRAL"}

        signals_html = ""
        if active_signals:
            signals_html += (
                '<div class="signal-box">'
                '<h3 style="color: #38bdf8; font-size: 15px; margin-bottom: 10px;">🔥 Scanner Grafico: Segnali ad Alta Confluenza Attivi</h3>'
                '<div style="display: flex; flex-direction: column; gap: 8px;">'
            )
            for sym, dat in active_signals.items():
                dir_color = "#10b981" if dat["action"] == "LONG" else "#ef4444"
                p_val = f"${dat['price']:.5f}" if dat['price'] < 1 else f"${dat['price']:.2f}"
                chart_url = f"https://www.tradingview.com/chart/?symbol=BINGX:{dat['symbol']}USDT"
                signals_html += (
                    f'<div style="background: #111827; padding: 10px; border-radius: 6px; border-left: 4px solid {dir_color}; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
                    f'<div>'
                    f'<strong style="color: #fff; font-size: 14px;">{dat["symbol"]}</strong> - <span style="color: {dir_color}; font-weight: bold;">{dat["action"]}</span> '
                    f'<span style="font-size: 12px; color: #9ca3af;">({dat["indicators_passed"]}/{dat["indicators_total"]} indicatori | {dat["whale_status"]})</span>'
                    f'<div style="font-size: 11px; color: #38bdf8; margin-top: 2px;">Score IA: {dat["score"]}% | Leva: {dat["leverage"]}x</div>'
                    f'</div>'
                    f'<div style="display: flex; gap: 10px; align-items: center;">'
                    f'<div style="font-size: 11px; color: #d1d5db; text-align: right;">'
                    f'<div>Prezzo: <strong>{p_val}</strong></div>'
                    f'<div>SL: <span style="color: #ef4444;">${dat["sl"]:.4f}</span> | TP1: <span style="color: #10b981;">${dat["tp1"]:.4f}</span></div>'
                    f'</div>'
                    f'<a href="{chart_url}" target="_blank" style="background: #38bdf8; color: #000; padding: 6px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-decoration: none;">📈 Grafico</a>'
                    f'</div>'
                    f'</div>'
                )
            signals_html += "</div></div>"

        is_admin = session.get('username') == ADMIN_USERNAME
        
        if is_admin:
            threshold_control = (
                f'<form action="/set-threshold" method="POST" style="display: flex; gap: 8px; align-items: center;">'
                f'<input type="number" name="threshold" min="40" max="99" value="{current_threshold}" style="width: 60px; text-align: center; background: #0b0e14; border: 1px solid #374151; color: #fff; padding: 6px; border-radius: 6px;">'
                f'<button type="submit" class="btn" style="width: auto; padding: 6px 12px;">Imposta</button>'
                f'</form>'
            )
            
            toggles_html = f"""
            <div class="panel" style="background: #1a2332; border: 1px solid #38bdf8;">
                <h3 style="color: #38bdf8; font-size: 14px; margin-bottom: 10px;">⚙️ Pannello Admin: Moduli IA e Scanner Avanzato</h3>
                <form action="/toggle-settings" method="POST" style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 13px;">
                    <label><input type="checkbox" name="use_trend_following" {"checked" if bot_settings["use_trend_following"]==1 else ""}> Trend-Following</label>
                    <label><input type="checkbox" name="use_multi_ema" {"checked" if bot_settings["use_multi_ema"]==1 else ""}> Multi-EMA</label>
                    <label><input type="checkbox" name="use_rsi_filter" {"checked" if bot_settings["use_rsi_filter"]==1 else ""}> RSI Filter</label>
                    <label><input type="checkbox" name="use_whale_tracker" {"checked" if bot_settings["use_whale_tracker"]==1 else ""}> 🐳 Whale Tracker</label>
                    <label><input type="checkbox" name="auto_execution" {"checked" if bot_settings["auto_execution"]==1 else ""}> 🚀 Auto-Trading Illimitato</label>
                    <button type="submit" class="btn" style="width: auto; padding: 4px 10px; font-size: 12px;">Aggiorna Stato</button>
                </form>
            </div>
            """
        else:
            threshold_control = (
                f'<span style="background: #0b0e14; border: 1px solid #374151; color: #f97316; padding: 6px 12px; border-radius: 6px; font-weight: bold; font-size: 14px;">{current_threshold}% (Admin)</span>'
            )
            toggles_html = ""

        content = """
        <div class="panel" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
            <div>
                <h3 style="color: #fff; font-size: 15px; margin-bottom: 3px;">Scanner Grafico & Intelligenza Istituzionale</h3>
                <p style="font-size: 12px; color: #9ca3af;">Soglia Minima Segnale: <strong style="color: #f97316;">{threshold}%</strong> (Posizioni Illimitate su BingX)</p>
            </div>
            {threshold_control}
        </div>
        
        {toggles_section}
        {signals_section}
        
        <h3 style="margin-bottom: 12px; color: #fff; font-size: 15px;">Monitoraggio Mercato Live (Tutti i Parametri & Grafici)</h3>
        <div class="grid">
            {% for symbol, data in market_data.items() %}
            <div class="card">
                <div class="gauge-container">
                    <div>
                        <span style="font-weight: bold; color: #fff; font-size: 14px;">{{ data.symbol }}</span>
                        <div style="font-size: 11px; color: {% if data.action == 'LONG' %}#10b981{% elif data.action == 'SHORT' %}#ef4444{% else %}#9ca3af{% endif %}; font-weight: bold;">
                            Direzione: {{ data.action }}
                        </div>
                    </div>
                    <div class="gauge {% if data.action == 'LONG' %}gauge-long{% elif data.action == 'SHORT' %}gauge-short{% else %}gauge-neutral{% endif %}">
                        {{ data.score }}%
                    </div>
                </div>

                <!-- Barra Potenza Segnale -->
                <div style="font-size: 11px; color: #9ca3af; display: flex; justify-content: space-between; margin-top: 4px;">
                    <span>⚡ POTENZA IA & INDICATORI</span>
                    <span style="color: #38bdf8; font-weight: bold;">{{ data.indicators_passed }}/{{ data.indicators_total }}</span>
                </div>
                <div class="power-bar-bg">
                    <div class="power-bar-fill" style="width: {{ (data.indicators_passed / data.indicators_total * 100)|int }}%;"></div>
                </div>
                
                <div style="font-size: 11px; color: #9ca3af; display: flex; flex-direction: column; gap: 4px;">
                    <div style="display: flex; justify-content: space-between;"><span>Prezzo Live:</span><strong style="color: #fff;">${{ "%.4f"|format(data.price) if data.price < 1 else "%.2f"|format(data.price) }}</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span>RSI (1H / 4H):</span><span><strong style="color: #38bdf8;">{{ data.rsi_1h }}</strong> / <strong style="color: #f97316;">{{ data.rsi_4h }}</strong></span></div>
                    <div style="display: flex; justify-content: space-between;"><span>EMA (9/21/50):</span><span style="color: #93c5fd;">{{ "%.2f"|format(data.ema_9) }} / {{ "%.2f"|format(data.ema_21) }} / {{ "%.2f"|format(data.ema_50) }}</span></div>
                    <div style="display: flex; justify-content: space-between;"><span>Whale Tracker:</span><strong style="color: #f59e0b;">{{ data.whale_status }}</strong></div>
                    
                    <div style="display: flex; justify-content: space-between; border-top: 1px dashed #1f2937; padding-top: 4px; margin-top: 2px;">
                        <span style="color: #ef4444;">Stop Loss:</span>
                        <strong style="color: #ef4444;">${{ "%.4f"|format(data.sl) if data.price < 1 else "%.2f"|format(data.sl) }} ({{ data.sl_pct }}%)</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #10b981;">Take Profit 1:</span>
                        <strong style="color: #10b981;">${{ "%.4f"|format(data.tp1) if data.price < 1 else "%.2f"|format(data.tp1) }} (+{{ data.tp1_pct }}%)</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #34d399;">Take Profit 2:</span>
                        <strong style="color: #34d399;">${{ "%.4f"|format(data.tp2) if data.price < 1 else "%.2f"|format(data.tp2) }} (+{{ data.tp2_pct }}%)</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #6ee7b7;">Take Profit 3:</span>
                        <strong style="color: #6ee7b7;">${{ "%.4f"|format(data.tp3) if data.price < 1 else "%.2f"|format(data.tp3) }} (+{{ data.tp3_pct }}%)</strong>
                    </div>
                </div>

                <!-- Pulsante Grafico Dedicato -->
                <a href="https://www.tradingview.com/chart/?symbol=BINGX:{{ data.symbol }}USDT" target="_blank" class="chart-btn">📈 Apri Grafico Live ({{ data.symbol }})</a>
            </div>
            {% endfor %}
        </div>
        """
        
        content = content.replace("{threshold}", str(current_threshold))
        content = content.replace("{threshold_control}", threshold_control)
        content = content.replace("{toggles_section}", toggles_html)
        content = content.replace("{signals_section}", signals_html)

        refresh_tag = '<meta http-equiv="refresh" content="15">'
        html = BASE_HTML.replace("{refresh_tag}", refresh_tag).replace("{{ content | safe }}", content)
        return render_template_string(html, brand_name=BRAND_NAME, market_data=market_data_cache)
    except Exception as e:
        return f"Errore interno temporaneo: {e}", 500

@app.route("/set-threshold", methods=["POST"])
def set_threshold():
    global current_threshold
    if session.get("username") == ADMIN_USERNAME:
        try:
            val = int(request.form.get("threshold"))
            if 40 <= val <= 99:
                current_threshold = val
        except ValueError:
            pass
    return redirect(url_for("home"))

@app.route("/toggle-settings", methods=["POST"])
def toggle_settings():
    global bot_settings
    if session.get("username") == ADMIN_USERNAME:
        bot_settings["use_trend_following"] = 1 if request.form.get("use_trend_following") == "on" else 0
        bot_settings["use_multi_ema"] = 1 if request.form.get("use_multi_ema") == "on" else 0
        bot_settings["use_rsi_filter"] = 1 if request.form.get("use_rsi_filter") == "on" else 0
        bot_settings["use_whale_tracker"] = 1 if request.form.get("use_whale_tracker") == "on" else 0
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
    return redirect(url_for("home"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = generate_password_hash(request.form.get("password"))
        init_db()
        try:
            conn = sqlite3.connect("trading_bot.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
            conn.close()
            flash("Registrazione completata! Effettua il login.")
            return redirect(url_for("login"))
        except Exception:
            flash("Username già esistente o errore di salvataggio.")
    content = """
    <div class="panel" style="max-width: 380px; margin: 30px auto;">
        <h3 style="color: #fff; margin-bottom: 15px; text-align: center;">Registrazione VIP</h3>
        <form method="POST">
            <div class="form-group"><label>Username</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Password</label><input type="password" name="password" required></div>
            <button type="submit" class="btn" style="margin-top: 10px;">Registrati</button>
        </form>
    </div>
    """
    html = BASE_HTML.replace("{refresh_tag}", "").replace("{{ content | safe }}", content)
    return render_template_string(html, brand_name=BRAND_NAME)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        init_db()
        try:
            conn = sqlite3.connect("trading_bot.db")
            cursor = conn.cursor()
            cursor.execute("SELECT id, password FROM users WHERE username = ?", (username,))
            user = cursor.fetchone()
            conn.close()
            if user and check_password_hash(user[1], password):
                session["user_id"] = user[0]
                session["username"] = username
                flash("Login effettuato con successo!")
                return redirect(url_for("home"))
        except Exception:
            pass
        flash("Credenziali non valide.")
    content = """
    <div class="panel" style="max-width: 380px; margin: 30px auto;">
        <h3 style="color: #fff; margin-bottom: 15px; text-align: center;">Accesso Piattaforma</h3>
        <form method="POST">
            <div class="form-group"><label>Username</label><input type="text" name="username" required></div>
            <div class="form-group"><label>Password</label><input type="password" name="password" required></div>
            <button type="submit" class="btn" style="margin-top: 10px;">Accedi</button>
        </form>
    </div>
    """
    html = BASE_HTML.replace("{refresh_tag}", "").replace("{{ content | safe }}", content)
    return render_template_string(html, brand_name=BRAND_NAME)

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout effettuato.")
    return redirect(url_for("home"))

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if "user_id" not in session:
        return redirect(url_for("login"))
    init_db()
    try:
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        if request.method == "POST":
            api_key = request.form.get("api_key")
            secret_key = request.form.get("secret_key")
            auto_trading = int(request.form.get("auto_trading", 0))
            
            if not secret_key:
                cursor.execute("SELECT bingx_secret_key FROM users WHERE id = ?", (session["user_id"],))
                old_sec = cursor.fetchone()
                if old_sec and old_sec[0]:
                    secret_key = old_sec[0]

            cursor.execute("UPDATE users SET bingx_api_key = ?, bingx_secret_key = ?, auto_trading = ? WHERE id = ?", (api_key, secret_key, auto_trading, session["user_id"]))
            conn.commit()
            flash("Configurazione API salvata in modo persistente!")
            
        cursor.execute("SELECT bingx_api_key, bingx_secret_key, auto_trading FROM users WHERE id = ?", (session["user_id"],))
        u_data = cursor.fetchone()
        conn.close()
        
        u_api = u_data[0] if u_data and u_data[0] else ''
        u_sec = u_data[1] if u_data and u_data[1] else ''
        u_auto = u_data[2] if u_data and len(u_data) > 2 and u_data[2] is not None else 0
    except Exception as e:
        print(f"Errore pagina settings: {e}")
        u_api, u_sec, u_auto = '', '', 0
    
    content = f"""
    <div class="panel" style="max-width: 450px; margin: 20px auto;">
        <h3 style="color: #fff; margin-bottom: 12px;">Gestione API BingX Personali</h3>
        <p style="font-size: 12px; color: #9ca3af; margin-bottom: 15px;">Inserisci le tue chiavi ufficiali di BingX o sfrutta quelle globali di Render.</p>
        <form method="POST">
            <div class="form-group"><label>API Key</label><input type="text" name="api_key" value="{u_api}" placeholder="Incolla qui la tua API Key di BingX"></div>
            <div class="form-group"><label>Secret Key</label><input type="password" name="secret_key" value="{u_sec}" placeholder="Incolla qui la tua Secret Key"></div>
            <div class="form-group"><label>Stato Auto-Trading</label>
                <select name="auto_trading">
                    <option value="0" {"selected" if u_auto == 0 else ""}>Disattivato</option>
                    <option value="1" {"selected" if u_auto == 1 else ""}>Attivato (Esecuzione Automatica)</option>
                </select>
            </div>
            <button type="submit" class="btn" style="background: #059669; margin-top: 10px;">Salva Chiavi e Attiva</button>
        </form>
    </div>
    """
    html = BASE_HTML.replace("{refresh_tag}", "").replace("{{ content | safe }}", content)
    return render_template_string(html, brand_name=BRAND_NAME)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
