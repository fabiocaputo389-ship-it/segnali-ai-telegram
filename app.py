import os
import time
import threading
import sqlite3
import hmac
import hashlib
import requests
from datetime import datetime
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

BRAND_NAME = "👑 SCANNER TRADING VIP"
ADMIN_USERNAME = "Fabio"

def fetch_all_bingx_symbols():
    default_symbols = [
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", 
        "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT", "NEAR-USDT", 
        "UNI-USDT", "RENDER-USDT", "SUI-USDT", "ARB-USDT", "OP-USDT", 
        "IMX-USDT", "ICP-USDT", "APT-USDT", "FIL-USDT", "PEPE-USDT", 
        "DOGE-USDT", "SHIB-USDT", "ATOM-USDT", "XLM-USDT", "ALGO-USDT", 
        "FET-USDT", "INJ-USDT", "LTC-USDT", "BCH-USDT", "ETC-USDT", 
        "SAND-USDT", "MANA-USDT", "AAVE-USDT", "FTM-USDT", "RUNE-USDT", 
        "ZEC-USDT", "TRX-USDT"
    ]
    try:
        url = "https://open-api.bingx.com/openApi/swap/v2/quote/contracts"
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if isinstance(data, list) and len(data) > 0:
                dynamic_list = []
                for contract in data:
                    sym = contract.get("symbol")
                    status = contract.get("status", 1)
                    if sym and status == 1 and sym.endswith("USDT"):
                        dynamic_list.append(sym)
                if dynamic_list:
                    return dynamic_list
    except Exception as e:
        print(f"Errore caricamento dinamico simboli: {e}")
    return default_symbols

SYMBOLS = fetch_all_bingx_symbols()

current_threshold = 80
MAX_ACTIVE_TRADES = 999

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
        "recommended_tf": "1H / 4H Multi-TF",
        "signal_time": "--:--:--",
        "recommended_margin": 20.0
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
                auto_trading INTEGER DEFAULT 0,
                bingx_api_key TEXT DEFAULT '',
                bingx_secret_key TEXT DEFAULT ''
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Errore DB Init: {e}")

init_db()

def get_user_api_keys(username):
    # Fallback sulle variabili d'ambiente di Render se l'utente non ha impostato le proprie
    env_api = os.environ.get("BINGX_API_KEY", "")
    env_sec = os.environ.get("BINGX_SECRET_KEY", "")
    try:
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT bingx_api_key, bingx_secret_key FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        if row:
            db_api, db_sec = row[0], row[1]
            if db_api and db_sec:
                return db_api, db_sec
    except Exception:
        pass
    return env_api, env_sec

def fetch_bingx_open_positions(api_key, secret_key):
    if not api_key or not secret_key:
        return []
    try:
        url = "https://open-api.bingx.com/openApi/swap/v2/user/positions"
        timestamp = str(int(time.time() * 1000))
        query_string = f"timestamp={timestamp}"
        signature = hmac.new(secret_key.strip().encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": api_key.strip()}
        full_url = f"{url}?{query_string}&signature={signature}"
        res = requests.get(full_url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            return [p for p in data if float(p.get("positionAmt", 0)) != 0]
    except Exception as e:
        print(f"Errore recupero posizioni: {e}")
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
        signature = hmac.new(secret_key.strip().encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": api_key.strip()}
        full_url = f"{url}?{query_string}&signature={signature}"
        requests.post(full_url, headers=headers, timeout=5)
    except Exception as e:
        print(f"Errore leva: {e}")

def execute_bingx_order(api_key, secret_key, symbol, side, leverage=5, quantity=10.0, sl=0.0, tp1=0.0):
    if bot_settings["auto_execution"] == 0:
        return False
    if not api_key or not secret_key or len(api_key.strip()) < 5:
        return False
    try:
        clean_api = api_key.strip()
        clean_sec = secret_key.strip()
        set_bingx_leverage(clean_api, clean_sec, symbol, leverage, side)
        
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
        signature = hmac.new(clean_sec.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": clean_api}
        full_url = f"{url}?{query_string}&signature={signature}"
        response = requests.post(full_url, headers=headers, timeout=5)
        if response.status_code == 200 and response.json().get("code") == 0:
            return True
    except Exception as e:
        print(f"Errore ordine {symbol}: {e}")
    return False

def trigger_auto_trades(symbol, action, leverage, sl, tp1):
    if bot_settings["auto_execution"] == 0:
        return
    side = "BUY" if action == "LONG" else "SELL"
    # Esegue in automatico usando le chiavi dell'admin o configurate globalmente
    api_key, secret_key = get_user_api_keys(ADMIN_USERNAME)
    if api_key and secret_key:
        execute_bingx_order(api_key, secret_key, symbol, side, leverage, 10.0, sl, tp1)

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
        print(f"Errore API klines: {e}")
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

                    bullish_points, bearish_points, total_indicators = 0, 0, 13

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
                    signal_time_str = datetime.now().strftime("%H:%M:%S")
                    
                    recommended_margin = round(max(15.0, current_price * 0.05 / leverage), 2) if current_price < 100 else round(max(20.0, 1000.0 / leverage), 2)

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
                        "recommended_tf": recommended_tf,
                        "signal_time": signal_time_str,
                        "recommended_margin": recommended_margin
                    }

                    if score >= current_threshold and action != "NEUTRAL":
                        if current_time - last_signal_time[symbol] > COOLDOWN_SECONDS:
                            last_signal_time[symbol] = current_time
                            trigger_auto_trades(symbol, action, leverage, sl, tp1)
                            
                            if telegram_token and channel_id:
                                pair_name = symbol.replace("-USDT", " / USDT")
                                decimals = 5 if current_price < 1 else 2
                                dir_text = "🟢 LONG (ACQUISTO)" if action == "LONG" else "🔴 SHORT (VENDITA)"

                                message = (
                                    f"👑 **SCANNER TRADING VIP** 👑\n"
                                    f"⏰ **Orario:** {signal_time_str}\n\n"
                                    f"🔹 **Asset:** {pair_name}\n"
                                    f"📊 **Direzione:** {dir_text}\n"
                                    f"⭐ **Confluenza IA:** {score}% ({rating})\n"
                                    f"⚙️ **Leva:** {leverage}x | **Margine Consigliato:** ${recommended_margin} USDT\n"
                                    f"⚡ **Ingresso:** ${current_price:.{decimals}f}\n\n"
                                    f"🛑 **Stop Loss:** ${sl:.{decimals}f} ({sl_pct}%)\n"
                                    f"🎯 **Take Profit 1:** ${tp1:.{decimals}f} (+{tp1_pct}%)\n"
                                    f"🎯 **Take Profit 2:** ${tp2:.{decimals}f} (+{tp2_pct}%)\n"
                                    f"🎯 **Take Profit 3:** ${tp3:.{decimals}f} (+{tp3_pct}%)\n"
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
        body { background-color: #050505; color: #e5e7eb; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 15px; }
        
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 15px; border-bottom: 2px solid #d4af37; margin-bottom: 20px; flex-wrap: wrap; gap: 10px;}
        .logo { font-size: 20px; font-weight: 900; color: #d4af37; letter-spacing: 1px; text-shadow: 0 0 10px rgba(212,175,55,0.3); }
        .nav a { color: #d4af37; text-decoration: none; margin-left: 10px; background: #121212; padding: 6px 12px; border-radius: 6px; border: 1px solid #d4af37; font-size: 13px; font-weight: bold; transition: all 0.3s;}
        .nav a:hover { background: #d4af37; color: #000; }
        
        .panel { background: #121212; border: 1px solid #d4af37; border-radius: 12px; padding: 18px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.8); }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; font-size: 12px; color: #d4af37; margin-bottom: 4px; font-weight: bold; }
        .form-group input, .form-group select { width: 100%; background: #000; border: 1px solid #333; color: #d4af37; padding: 8px; border-radius: 6px; font-size: 14px; }
        .btn { background: linear-gradient(135deg, #d4af37 0%, #aa8c2c 100%); color: #000; border: none; padding: 9px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%; box-shadow: 0 2px 10px rgba(212,175,55,0.3); }
        .btn:hover { opacity: 0.9; }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
        .card { background: #121212; border: 1px solid #262626; border-radius: 10px; padding: 14px; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.5); transition: border-color 0.3s;}
        .card:hover { border-color: #d4af37; }
        
        .flash { background: #1a1600; color: #d4af37; border: 1px solid #d4af37; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 13px; font-weight: bold; }
        
        .speedometer-container { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; border-bottom: 1px solid #222; padding-bottom: 8px; }
        
        .power-bar-bg { background: #1a1a1a; height: 8px; border-radius: 4px; overflow: hidden; margin: 6px 0 10px 0; border: 1px solid #333; }
        .power-bar-fill { height: 100%; background: linear-gradient(90deg, #aa8c2c, #d4af37); }
        
        .chart-btn { display: inline-block; background: rgba(212, 175, 55, 0.1); color: #d4af37; border: 1px solid #d4af37; padding: 6px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; font-weight: bold; margin-top: 10px; text-align: center; width: 100%; transition: all 0.3s; }
        .chart-btn:hover { background: #d4af37; color: #000; }
        
        .signal-box { background: rgba(212, 175, 55, 0.05); border: 1px solid #d4af37; border-radius: 10px; padding: 14px; margin-bottom: 20px; box-shadow: 0 0 15px rgba(212,175,55,0.1); }
        .positions-box { background: rgba(16, 185, 129, 0.05); border: 1px solid #10b981; border-radius: 10px; padding: 14px; margin-bottom: 20px; }
    </style>
    {refresh_tag}
</head>
<body>
    <div class="header">
        <div class="logo">👑 SCANNER TRADING VIP</div>
        <div class="nav">
            {% if session.get('user_id') %}
                <span>Utente: <strong style="color: #d4af37;">{{ session.get('username') }}</strong></span>
                <a href="/settings">API Key</a>
                <a href="/logout" style="color: #ef4444; border-color: #ef4444;">Esci</a>
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
        
        current_user = session.get('username', ADMIN_USERNAME)
        api_key, secret_key = get_user_api_keys(current_user)
        open_positions = fetch_bingx_open_positions(api_key, secret_key)

        positions_html = ""
        if open_positions:
            positions_html += (
                '<div class="positions-box">'
                '<h3 style="color: #10b981; font-size: 15px; margin-bottom: 10px;">🟢 Posizioni Live Attive su BingX</h3>'
                '<div style="display: flex; flex-direction: column; gap: 6px;">'
            )
            for p in open_positions:
                p_sym = p.get("symbol")
                p_side = p.get("positionSide", "LONG")
                p_amt = p.get("positionAmt")
                p_lev = p.get("leverage")
                p_pnl = float(p.get("unrealizedProfit", 0))
                p_color = "#10b981" if p_pnl >= 0 else "#ef4444"
                positions_html += (
                    f'<div style="background: #000; padding: 8px 12px; border-radius: 6px; border: 1px solid #222; display: flex; justify-content: space-between; align-items: center; font-size: 13px;">'
                    f'<div><strong style="color: #fff;">{p_sym}</strong> <span style="color: #d4af37;">({p_side} {p_lev}x)</span> - Q.tà: {p_amt}</div>'
                    f'<div>PnL: <strong style="color: {p_color};">${p_pnl:.2f}</strong></div>'
                    f'</div>'
                )
            positions_html += "</div></div>"

        signals_html = ""
        if active_signals:
            signals_html += (
                '<div class="signal-box">'
                '<h3 style="color: #d4af37; font-size: 15px; margin-bottom: 10px;">🔥 Segnali VIP ad Alta Confluenza Attivi</h3>'
                '<div style="display: flex; flex-direction: column; gap: 8px;">'
            )
            for sym, dat in active_signals.items():
                dir_color = "#10b981" if dat["action"] == "LONG" else "#ef4444"
                p_val = f"${dat['price']:.5f}" if dat['price'] < 1 else f"${dat['price']:.2f}"
                chart_url = f"https://www.tradingview.com/chart/?symbol=BINGX:{dat['symbol']}USDT"
                signals_html += (
                    f'<div style="background: #000; padding: 10px; border-radius: 8px; border: 1px solid #d4af37; border-left: 4px solid {dir_color}; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
                    f'<div>'
                    f'<strong style="color: #fff; font-size: 14px;">{dat["symbol"]}</strong> - <span style="color: {dir_color}; font-weight: bold;">{dat["action"]}</span> '
                    f'<span style="font-size: 11px; color: #d4af37;">🕒 {dat["signal_time"]}</span>'
                    f'<div style="font-size: 11px; color: #9ca3af; margin-top: 2px;">Score IA: <strong style="color: #d4af37;">{dat["score"]}%</strong> | Leva: {dat["leverage"]}x | Margine Consigliato: <strong style="color: #10b981;">${dat["recommended_margin"]}</strong></div>'
                    f'</div>'
                    f'<div style="display: flex; gap: 10px; align-items: center;">'
                    f'<div style="font-size: 11px; color: #d1d5db; text-align: right;">'
                    f'<div>Prezzo: <strong>{p_val}</strong></div>'
                    f'<div>SL: <span style="color: #ef4444;">${dat["sl"]:.4f}</span> | TP1: <span style="color: #10b981;">${dat["tp1"]:.4f}</span></div>'
                    f'</div>'
                    f'<a href="{chart_url}" target="_blank" style="background: #d4af37; color: #000; padding: 6px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-decoration: none;">📈 Grafico</a>'
                    f'</div>'
                    f'</div>'
                )
            signals_html += "</div></div>"

        is_admin = session.get('username') == ADMIN_USERNAME
        threshold_control = (
            f'<form action="/set-threshold" method="POST" style="display: flex; gap: 8px; align-items: center;">'
            f'<input type="number" name="threshold" min="40" max="99" value="{current_threshold}" style="width: 60px; text-align: center; background: #000; border: 1px solid #d4af37; color: #d4af37; padding: 6px; border-radius: 6px; font-weight: bold;">'
            f'<button type="submit" class="btn" style="width: auto; padding: 6px 12px; font-size: 12px;">Imposta</button>'
            f'</form>'
        ) if is_admin else f'<span style="color: #d4af37; font-weight: bold;">{current_threshold}%</span>'

        toggles_html = f"""
        <div class="panel" style="border: 1px solid #d4af37;">
            <h3 style="color: #d4af37; font-size: 14px; margin-bottom: 10px;">⚙️ Pannello Admin: Moduli IA e Scanner Universale</h3>
            <form action="/toggle-settings" method="POST" style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 13px;">
                <label style="color: #fff;"><input type="checkbox" name="use_trend_following" {"checked" if bot_settings["use_trend_following"]==1 else ""}> Trend-Following</label>
                <label style="color: #fff;"><input type="checkbox" name="use_multi_ema" {"checked" if bot_settings["use_multi_ema"]==1 else ""}> Multi-EMA</label>
                <label style="color: #fff;"><input type="checkbox" name="use_rsi_filter" {"checked" if bot_settings["use_rsi_filter"]==1 else ""}> RSI Filter</label>
                <label style="color: #fff;"><input type="checkbox" name="use_whale_tracker" {"checked" if bot_settings["use_whale_tracker"]==1 else ""}> 🐳 Whale Tracker</label>
                <label style="color: #fff;"><input type="checkbox" name="auto_execution" {"checked" if bot_settings["auto_execution"]==1 else ""}> 🚀 Auto-Trading Illimitato</label>
                <button type="submit" class="btn" style="width: auto; padding: 4px 10px; font-size: 12px;">Aggiorna</button>
            </form>
        </div>
        """ if is_admin else ""

        content = """
        <div class="panel" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
            <div>
                <h3 style="color: #fff; font-size: 16px; margin-bottom: 3px;">Scanner Dinamico Universale (Futures, Crypto, Azioni & Nuove Quotazioni)</h3>
                <p style="font-size: 12px; color: #9ca3af;">Asset Monitorati: <strong style="color: #d4af37;">{total_assets}</strong> | Soglia Minima IA: <strong style="color: #d4af37;">{threshold}%</strong></p>
            </div>
            {threshold_control}
        </div>
        
        {positions_section}
        {toggles_section}
        {signals_section}
        
        <h3 style="margin-bottom: 12px; color: #d4af37; font-size: 16px; font-weight: bold; border-left: 3px solid #d4af37; padding-left: 8px;">📊 Monitoraggio Mercato Live & Tachimetri IA</h3>
        <div class="grid">
            {% for symbol, data in market_data.items() %}
            <div class="card">
                <div class="speedometer-container">
                    <div>
                        <span style="font-weight: bold; color: #fff; font-size: 14px;">{{ data.symbol }}</span>
                        <div style="font-size: 11px; color: {% if data.action == 'LONG' %}#10b981{% elif data.action == 'SHORT' %}#ef4444{% else %}#9ca3af{% endif %}; font-weight: bold;">
                            Direzione: {{ data.action }} <span style="color: #d4af37; font-weight: normal;">({{ data.signal_time }})</span>
                        </div>
                    </div>
                    <!-- Tachimetro SVG con Lancetta Dorata -->
                    <div style="width: 55px; height: 35px; position: relative;">
                        <svg viewBox="0 0 100 60" style="width: 100%; height: 100%;">
                            <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="#222" stroke-width="12" stroke-linecap="round" />
                            <path d="M 10 50 A 40 40 0 0 1 90 50" fill="none" stroke="#d4af37" stroke-width="12" stroke-dasharray="125.6" stroke-dashoffset="{{ 125.6 - (125.6 * data.score / 100) }}" stroke-linecap="round" />
                            {% set angle = -90 + (180 * data.score / 100) %}
                            <line x1="50" y1="50" x2="{{ 50 + 30 * cos(angle * 3.14159 / 180) }}" y2="{{ 50 + 30 * sin(angle * 3.14159 / 180) }}" stroke="#fff" stroke-width="3" stroke-linecap="round" />
                            <circle cx="50" cy="50" r="4" fill="#d4af37" />
                        </svg>
                        <div style="text-align: center; font-size: 10px; font-weight: bold; color: #d4af37; margin-top: -2px;">{{ data.score }}%</div>
                    </div>
                </div>

                <!-- Barra Potenza Segnale -->
                <div style="font-size: 11px; color: #9ca3af; display: flex; justify-content: space-between; margin-top: 4px;">
                    <span>⚡ POTENZA IA & INDICATORI</span>
                    <span style="color: #d4af37; font-weight: bold;">{{ data.indicators_passed }}/{{ data.indicators_total }}</span>
                </div>
                <div class="power-bar-bg">
                    <div class="power-bar-fill" style="width: {{ (data.indicators_passed / data.indicators_total * 100)|int }}%;"></div>
                </div>
                
                <div style="font-size: 11px; color: #9ca3af; display: flex; flex-direction: column; gap: 4px;">
                    <div style="display: flex; justify-content: space-between;"><span>Prezzo Live:</span><strong style="color: #fff;">${{ "%.4f"|format(data.price) if data.price < 1 else "%.2f"|format(data.price) }}</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span>RSI (1H / 4H):</span><span><strong style="color: #d4af37;">{{ data.rsi_1h }}</strong> / <strong style="color: #aa8c2c;">{{ data.rsi_4h }}</strong></span></div>
                    <div style="display: flex; justify-content: space-between;"><span>Margine Consigliato:</span><strong style="color: #10b981;">${{ data.recommended_margin }} USDT ({{ data.leverage }}x)</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span>Whale Tracker:</span><strong style="color: #d4af37;">{{ data.whale_status }}</strong></div>
                    
                    <div style="display: flex; justify-content: space-between; border-top: 1px dashed #222; padding-top: 4px; margin-top: 2px;">
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
                </div>

                <a href="https://www.tradingview.com/chart/?symbol=BINGX:{{ data.symbol }}USDT" target="_blank" class="chart-btn">📈 Apri Grafico Live ({{ data.symbol }})</a>
            </div>
            {% endfor %}
        </div>
        """
        
        import math
        content = content.replace("{threshold}", str(current_threshold))
        content = content.replace("{total_assets}", str(len(SYMBOLS)))
        content = content.replace("{threshold_control}", threshold_control)
        content = content.replace("{positions_section}", positions_html)
        content = content.replace("{toggles_section}", toggles_html)
        content = content.replace("{signals_section}", signals_html)

        refresh_tag = '<meta http-equiv="refresh" content="30">'
        html = BASE_HTML.replace("{refresh_tag}", refresh_tag).replace("{{ content | safe }}", content)
        return render_template_string(html, brand_name=BRAND_NAME, market_data=market_data_cache, sin=math.sin, cos=math.cos)
    except Exception as e:
        return f"Errore interno temporaneo: {e}", 500

@app.route("/settings", methods=["GET", "POST"])
def settings():
    if not session.get("user_id"):
        return redirect(url_for("login"))
    
    username = session.get("username")
    if request.method == "POST":
        api_key = request.form.get("api_key", "").strip()
        secret_key = request.form.get("secret_key", "").strip()
        try:
            conn = sqlite3.connect("trading_bot.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET bingx_api_key = ?, bingx_secret_key = ? WHERE username = ?", (api_key, secret_key, username))
            conn.commit()
            conn.close()
            flash("API Key salvate con successo!")
        except Exception as e:
            flash(f"Errore salvataggio API Key: {e}")
        return redirect(url_for("settings"))
    
    current_api, current_sec = get_user_api_keys(username)
    masked_api = current_api[:6] + "..." if len(current_api) > 6 else current_api
    
    content = f"""
    <div class="panel" style="max-width: 420px; margin: 30px auto; border: 1px solid #d4af37;">
        <h3 style="color: #d4af37; margin-bottom: 15px; text-align: center;">Configurazione API BingX</h3>
        <p style="font-size: 12px; color: #9ca3af; margin-bottom: 15px; text-align: center;">Inserisci le tue chiavi API Futures per abilitare la lettura delle posizioni personali.</p>
        <form method="POST">
            <div class="form-group"><label>BingX API Key</label><input type="text" name="api_key" value="{current_api}" required></div>
            <div class="form-group"><label>BingX Secret Key</label><input type="password" name="secret_key" value="{current_sec}" required></div>
            <button type="submit" class="btn" style="margin-top: 10px;">Salva Chiavi API</button>
        </form>
        <div style="margin-top: 15px; text-align: center;"><a href="/" style="color: #d4af37; font-size: 13px; text-decoration: none;">← Torna alla Dashboard</a></div>
    </div>
    """
    html = BASE_HTML.replace("{refresh_tag}", "").replace("{{ content | safe }}", content)
    return render_template_string(html, brand_name=BRAND_NAME)

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
    <div class="panel" style="max-width: 380px; margin: 30px auto; border: 1px solid #d4af37;">
        <h3 style="color: #d4af37; margin-bottom: 15px; text-align: center;">Registrazione VIP</h3>
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
    <div class="panel" style="max-width: 380px; margin: 30px auto; border: 1px solid #d4af37;">
        <h3 style="color: #d4af37; margin-bottom: 15px; text-align: center;">Accesso Piattaforma</h3>
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
