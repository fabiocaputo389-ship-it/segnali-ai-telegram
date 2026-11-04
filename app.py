import os
import time
import threading
import sqlite3
import hmac
import hashlib
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chiave_segreta_super_sicura_live_status")

BRAND_NAME = "👑 SCANNER TRADING VIP [LIVE ENGINE MONITOR]"
ADMIN_USERNAME = "Fabio"

# Variabile globale per tracciare lo stato di lavoro in tempo reale del bot
bot_live_status = {
    "last_scan_time": "--:--:--",
    "scanned_count": 0,
    "active_thread": "Attivo e Sincronizzato",
    "current_action_symbol": "In attesa..."
}

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

current_threshold = 82

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
        "ai_grade": "C (Analisi in corso)",
        "indicators_passed": 0,
        "indicators_total": 20,
        "sl": 0.0,
        "tp1": 0.0,
        "tp2": 0.0,
        "tp3": 0.0,
        "sl_pct": 0.0,
        "tp1_pct": 0.0,
        "tp2_pct": 0.0,
        "tp3_pct": 0.0,
        "leverage": 5,
        "signal_time": "--:--:--",
        "recommended_tf": "1h",
        "recommended_margin": 30.0
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
        position_side = "LONG" if side == "BUY" else "SHORT"
        
        params = {
            "symbol": symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(quantity),
            "timestamp": timestamp
        }
        
        if sl > 0:
            params["stopLossPrice"] = str(sl)
        if tp1 > 0:
            params["takeProfitPrice"] = str(tp1)

        query_string = "&".join([f"{key}={params[key]}" for key in sorted(params.keys())]) + f"&timestamp={timestamp}"
        signature = hmac.new(clean_sec.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {"X-BX-APIKEY": clean_api}
        full_url = f"{url}?{query_string}&signature={signature}"
        
        response = requests.post(full_url, headers=headers, timeout=5)
        res_json = response.json()
        if response.status_code == 200 and res_json.get("code") == 0:
            return True
        else:
            print(f"Errore API BingX ordine {symbol}: {res_json}")
    except Exception as e:
        print(f"Errore esecuzione ordine {symbol}: {e}")
    return False

def trigger_auto_trades(symbol, action, leverage, sl, tp1):
    if bot_settings["auto_execution"] == 0:
        return
    side = "BUY" if action == "LONG" else "SELL"
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
    is_whale_activity = last_volume > (avg_volume * 1.5)
    last_close = float(candles[-1]['close'])
    prev_close = float(candles[-1]['open'])
    
    if is_whale_activity:
        if last_close >= prev_close:
            return "INSTITUTIONAL ACCUMULATION 🐳", 3
        else:
            return "INSTITUTIONAL DISTRIBUTION 🐋", -3
    return "NORMAL LIQUIDITY", 0

def fetch_bingx_klines(symbol, interval):
    try:
        url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval={interval}&limit=60"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if isinstance(data, list) and len(data) > 0:
                return sorted(data, key=lambda x: int(x['time']))
    except Exception as e:
        print(f"Errore API klines {symbol}: {e}")
    return []

def determine_optimal_timeframe(candles_15m, candles_1h, candles_4h):
    try:
        atr_15 = calculate_atr(candles_15m) / float(candles_15m[-1]['close']) if candles_15m else 0
        atr_1 = calculate_atr(candles_1h) / float(candles_1h[-1]['close']) if candles_1h else 0
        atr_4 = calculate_atr(candles_4h) / float(candles_4h[-1]['close']) if candles_4h else 0

        if atr_15 > atr_1 * 1.3:
            return "15m"
        elif atr_4 > atr_1 * 1.6:
            return "4h"
        else:
            return "1h"
    except Exception:
        return "1h"

def run_scanner_background():
    global bot_live_status
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel_id = os.environ.get("CHANNEL_ID")
    last_signal_time = {symbol: 0 for symbol in SYMBOLS}
    COOLDOWN_SECONDS = 3600

    while True:
        global current_threshold, market_data_cache, bot_settings
        current_time = time.time()
        scanned_counter = 0
        
        for symbol in SYMBOLS:
            try:
                bot_live_status["current_action_symbol"] = symbol.replace("-USDT", "")
                candles_15m = fetch_bingx_klines(symbol, "15m")
                candles_1h = fetch_bingx_klines(symbol, "1h")
                candles_4h = fetch_bingx_klines(symbol, "4h")
                
                if candles_15m and candles_1h and candles_4h:
                    recommended_tf = determine_optimal_timeframe(candles_15m, candles_1h, candles_4h)
                    active_candles = candles_15m if recommended_tf == "15m" else (candles_4h if recommended_tf == "4h" else candles_1h)
                    closes = [float(c['close']) for c in active_candles]
                    current_price = closes[-1]
                    
                    rsi_15m = calculate_rsi([float(c['close']) for c in candles_15m])
                    rsi_1h = calculate_rsi([float(c['close']) for c in candles_1h])
                    rsi_4h = calculate_rsi([float(c['close']) for c in candles_4h])
                    
                    ema_9 = calculate_ema(closes, 9)
                    ema_21 = calculate_ema(closes, 21)
                    ema_50 = calculate_ema(closes, 50)
                    atr = calculate_atr(active_candles)
                    whale_txt, whale_score_val = analyze_whale_volume(active_candles)

                    bullish_points, bearish_points, total_indicators = 0, 0, 20

                    if bot_settings["use_rsi_filter"] == 1:
                        if rsi_15m >= 45: bullish_points += 3
                        else: bearish_points += 3
                        
                        if rsi_1h >= 48: bullish_points += 4
                        else: bearish_points += 4
                        
                        if rsi_4h >= 48: bullish_points += 3
                        else: bearish_points += 3
                    else:
                        bullish_points += 5
                        bearish_points += 5

                    if bot_settings["use_multi_ema"] == 1:
                        if ema_9 >= ema_21: bullish_points += 4
                        else: bearish_points += 4
                        if current_price >= ema_50: bullish_points += 2
                        else: bearish_points += 2
                    else:
                        bullish_points += 3
                        bearish_points += 3

                    if bot_settings["use_trend_following"] == 1:
                        if closes[-1] >= closes[-2]: bullish_points += 2
                        else: bearish_points += 2
                        if closes_4h[-1] >= closes_4h[-2]: bullish_points += 1
                        else: bearish_points += 1
                    else:
                        bullish_points += 2
                        bearish_points += 2

                    if bot_settings["use_whale_tracker"] == 1:
                        if whale_score_val >= 0: bullish_points += 3
                        else: bearish_points += 3
                    else:
                        bullish_points += 1
                        bullish_points += 1

                    action, score, rating, passed_indicators = "NEUTRAL", 50, "NEUTRAL", 0

                    if bullish_points >= 11 and rsi_1h >= 46:
                        action = "LONG"
                        passed_indicators = bullish_points
                        score = min(99, max(68, int((bullish_points / total_indicators) * 100) + 15))
                        rating = "ALPHA BUY" if score >= 88 else "QUANTUM BUY"
                    elif bearish_points >= 11 and rsi_1h <= 54:
                        action = "SHORT"
                        passed_indicators = bearish_points
                        score = min(99, max(68, int((bearish_points / total_indicators) * 100) + 15))
                        rating = "ALPHA SELL" if score >= 88 else "QUANTUM SELL"

                    if score >= 90:
                        ai_grade = "A+ (Elite Quantum)"
                    elif score >= 82:
                        ai_grade = "A (High Setup)"
                    elif score >= 75:
                        ai_grade = "B+ (Stable Momentum)"
                    elif score >= 68:
                        ai_grade = "B (Moderate)"
                    else:
                        ai_grade = "C (Standby)"

                    base_step = atr if atr > 0 else current_price * 0.008
                    leverage = 10 if score >= 90 else (5 if score >= 82 else 3)
                    
                    signal_time_str = datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M:%S")
                    recommended_margin = round(max(25.0, current_price * 0.03 / leverage), 2) if current_price < 100 else round(max(30.0, 1500.0 / leverage), 2)

                    if action == "LONG":
                        sl = current_price - (base_step * 0.5)
                        tp1 = current_price + (base_step * 0.6)
                        tp2 = current_price + (base_step * 1.2)
                        tp3 = current_price + (base_step * 1.8)
                    elif action == "SHORT":
                        sl = current_price + (base_step * 0.5)
                        tp1 = current_price - (base_step * 0.6)
                        tp2 = current_price - (base_step * 1.2)
                        tp3 = current_price - (base_step * 1.8)
                    else:
                        sl, tp1, tp2, tp3 = current_price * 0.99, current_price * 1.005, current_price * 1.01, current_price * 1.015

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
                        "ai_grade": ai_grade,
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
                        "signal_time": signal_time_str,
                        "recommended_tf": recommended_tf,
                        "recommended_margin": recommended_margin
                    }
                    scanned_counter += 1

                    if score >= current_threshold and action != "NEUTRAL":
                        if current_time - last_signal_time[symbol] > COOLDOWN_SECONDS:
                            last_signal_time[symbol] = current_time
                            trigger_auto_trades(symbol, action, leverage, sl, tp1)
                            
                            if telegram_token and channel_id:
                                pair_name = symbol.replace("-USDT", " / USDT")
                                decimals = 5 if current_price < 1 else 2
                                dir_text = "🟢 LONG ALPHA" if action == "LONG" else "🔴 SHORT ALPHA"

                                message = (
                                    f"👑 **SCANNER TRADING VIP [ALPHA DYNAMIC TF: {recommended_tf.upper()}]** 👑\n"
                                    f"⏰ **Orario:** {signal_time_str} | ⏱️ **TF Consigliato:** {recommended_tf}\n\n"
                                    f"🔹 **Asset:** {pair_name}\n"
                                    f"📊 **Direzione:** {dir_text}\n"
                                    f"⭐ **Confluenza IA:** {score}% | **Grado:** {ai_grade}\n"
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
        
        bot_live_status["last_scan_time"] = datetime.now(ZoneInfo("Europe/Rome")).strftime("%H:%M:%S")
        bot_live_status["scanned_count"] = scanned_counter
        bot_live_status["current_action_symbol"] = "In attesa ciclo successivo..."
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
        body { background-color: #000000; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; padding: 15px; }
        
        .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 15px; border-bottom: 2px solid #00ff66; margin-bottom: 20px; flex-wrap: wrap; gap: 10px;}
        .logo { font-size: 20px; font-weight: 900; color: #ffffff; letter-spacing: 1px; }
        .nav a { color: #ffffff; text-decoration: none; margin-left: 10px; background: #0a0a0a; padding: 6px 12px; border-radius: 6px; border: 1px solid #00ff66; font-size: 13px; font-weight: bold; transition: all 0.3s;}
        .nav a:hover { background: #00ff66; color: #000000; }
        
        .panel { background: #0a0a0a; border: 1px solid #222222; border-radius: 12px; padding: 18px; margin-bottom: 20px; box-shadow: 0 4px 20px rgba(0,255,102,0.1); }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; font-size: 12px; color: #00ff66; margin-bottom: 4px; font-weight: bold; }
        .form-group input, .form-group select { width: 100%; background: #000000; border: 1px solid #333333; color: #ffffff; padding: 8px; border-radius: 6px; font-size: 14px; }
        
        .btn { background: #00ff66; color: #000000; border: none; padding: 9px 16px; border-radius: 6px; font-weight: bold; cursor: pointer; width: 100%; box-shadow: 0 2px 10px rgba(0,255,102,0.3); }
        .btn:hover { opacity: 0.85; }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
        .card { background: #0a0a0a; border: 1px solid #222222; border-radius: 10px; padding: 14px; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.7); transition: border-color 0.3s;}
        .card:hover { border-color: #00ff66; }
        
        .flash { background: #111111; color: #00ff66; border: 1px solid #00ff66; padding: 10px; border-radius: 6px; margin-bottom: 15px; font-size: 13px; font-weight: bold; }
        
        .analog-gauge { width: 110px; height: 110px; border-radius: 50%; background: radial-gradient(circle, #111111 0%, #000000 100%); border: 2px solid #333333; position: relative; margin: 10px auto; display: flex; align-items: center; justify-content: center; box-shadow: inset 0 0 10px rgba(0,0,0,0.8); }
        .gauge-center { width: 10px; height: 10px; background: #00ff66; border-radius: 50%; position: absolute; z-index: 3; box-shadow: 0 0 6px #00ff66; }
        .gauge-needle { width: 2px; height: 45px; background: #ff3333; position: absolute; bottom: 50%; left: calc(50% - 1px); transform-origin: bottom center; z-index: 2; transition: transform 0.5s ease-in-out; box-shadow: 0 0 4px #ff3333; }
        .gauge-value-text { position: absolute; bottom: 12px; font-size: 11px; font-weight: bold; color: #ffffff; z-index: 4; text-shadow: 0 1px 3px #000000; }
        
        .power-bar-bg { background: #1a1a1a; height: 6px; border-radius: 3px; overflow: hidden; margin: 4px 0 8px 0; border: 1px solid #333333; }
        .power-bar-fill { height: 100%; background: #00ff66; }
        
        .chart-btn { display: inline-block; background: rgba(0,255,102,0.1); color: #00ff66; border: 1px solid #00ff66; padding: 6px 10px; border-radius: 6px; font-size: 11px; text-decoration: none; font-weight: bold; margin-top: 10px; text-align: center; width: 100%; transition: all 0.3s; }
        .chart-btn:hover { background: #00ff66; color: #000000; }
        
        .signal-box { background: rgba(0,255,102,0.03); border: 1px solid #00ff66; border-radius: 10px; padding: 14px; margin-bottom: 20px; }
        .live-status-box { background: #000000; border: 1px dashed #00ff66; border-radius: 8px; padding: 12px; margin-bottom: 15px; font-size: 12px; color: #aaaaaa; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
    </style>
    {refresh_tag}
</head>
<body>
    <div class="header">
        <div class="logo">👑 SCANNER TRADING VIP</div>
        <div class="nav">
            {% if session.get('user_id') %}
                <span>Utente: <strong style="color: #00ff66;">{{ session.get('username') }}</strong></span>
                <a href="/settings">API Key</a>
                <a href="/logout" style="color: #ff3333; border-color: #ff3333;">Esci</a>
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
                '<h3 style="color: #00ff66; font-size: 15px; margin-bottom: 10px;">🔥 Segnali Alpha Istituzionali Attivi</h3>'
                '<div style="display: flex; flex-direction: column; gap: 8px;">'
            )
            for sym, dat in active_signals.items():
                dir_color = "#00ff66" if dat["action"] == "LONG" else "#ff3333"
                p_val = f"${dat['price']:.5f}" if dat['price'] < 1 else f"${dat['price']:.2f}"
                chart_url = f"https://www.tradingview.com/chart/?symbol=BINGX:{dat['symbol']}USDT"
                signals_html += (
                    f'<div style="background: #000000; padding: 10px; border-radius: 8px; border: 1px solid #333333; border-left: 4px solid {dir_color}; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
                    f'<div>'
                    f'<strong style="color: #ffffff; font-size: 14px;">{dat["symbol"]} - {dat["action"]}</strong> '
                    f'<span style="font-size: 11px; color: #aaaaaa;">🕒 {dat["signal_time"]} | ⏱️ TF Dinamico: <strong style="color: #00ff66;">{dat["recommended_tf"]}</strong></span>'
                    f'<div style="font-size: 11px; color: #aaaaaa; margin-top: 2px;">Score IA: <strong style="color: #00ff66;">{dat["score"]}%</strong> | Grado: <strong style="color: #00ff66;">{dat["ai_grade"]}</strong> | Leva: {dat["leverage"]}x</div>'
                    f'</div>'
                    f'<div style="display: flex; gap: 10px; align-items: center;">'
                    f'<div style="font-size: 11px; color: #ffffff; text-align: right;">'
                    f'<div>Prezzo: <strong>{p_val}</strong></div>'
                    f'<div>SL: <span style="color: #ff3333;">${dat["sl"]:.4f}</span> | TP1: <span style="color: #00ff66;">${dat["tp1"]:.4f}</span></div>'
                    f'</div>'
                    f'<a href="{chart_url}" target="_blank" style="background: #00ff66; color: #000000; padding: 6px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-decoration: none;">📈 Grafico</a>'
                    f'</div>'
                    f'</div>'
                )
            signals_html += "</div></div>"

        is_admin = session.get('username') == ADMIN_USERNAME
        threshold_control = (
            f'<form action="/set-threshold" method="POST" style="display: flex; gap: 8px; align-items: center;">'
            f'<input type="number" name="threshold" min="40" max="99" value="{current_threshold}" style="width: 60px; text-align: center; background: #000000; border: 1px solid #00ff66; color: #00ff66; padding: 6px; border-radius: 6px; font-weight: bold;">'
            f'<button type="submit" class="btn" style="width: auto; padding: 6px 12px; font-size: 12px;">Imposta</button>'
            f'</form>'
        ) if is_admin else f'<span style="color: #00ff66; font-weight: bold;">{current_threshold}%</span>'

        toggles_html = f"""
        <div class="panel" style="border: 1px solid #333333;">
            <h3 style="color: #ffffff; font-size: 14px; margin-bottom: 10px;">⚙️ Pannello Admin: Motore IA Dynamic Timeframe Engine</h3>
            <form action="/toggle-settings" method="POST" style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center; font-size: 13px;">
                <label style="color: #ffffff;"><input type="checkbox" name="use_trend_following" {"checked" if bot_settings["use_trend_following"]==1 else ""}> Trend-Following</label>
                <label style="color: #ffffff;"><input type="checkbox" name="use_multi_ema" {"checked" if bot_settings["use_multi_ema"]==1 else ""}> Multi-EMA</label>
                <label style="color: #ffffff;"><input type="checkbox" name="use_rsi_filter" {"checked" if bot_settings["use_rsi_filter"]==1 else ""}> RSI Filter</label>
                <label style="color: #ffffff;"><input type="checkbox" name="use_whale_tracker" {"checked" if bot_settings["use_whale_tracker"]==1 else ""}> 🐳 Whale Tracker</label>
                <label style="color: #ffffff;"><input type="checkbox" name="auto_execution" {"checked" if bot_settings["auto_execution"]==1 else ""}> 🚀 Auto-Trading H24</label>
                <button type="submit" class="btn" style="width: auto; padding: 4px 10px; font-size: 12px;">Aggiorna</button>
            </form>
        </div>
        """ if is_admin else ""

        # Pannello di visione in tempo reale di come sta lavorando il bot
        live_status_html = f"""
        <div class="live-status-box">
            <div>🟢 <strong>STATO MOTORE:</strong> <span style="color: #00ff66;">{bot_live_status["active_thread"]}</span></div>
            <div>🔄 <strong>ULTIMO CICLO:</strong> <span style="color: #ffffff;">{bot_live_status["last_scan_time"]}</span></div>
            <div>📡 <strong>COIN ANALIZZATA:</strong> <span style="color: #00ff66;">{bot_live_status["current_action_symbol"]}</span></div>
            <div>📊 <strong>ASSET SCANSIONATI:</strong> <span style="color: #ffffff;">{bot_live_status["scanned_count"]} / {len(SYMBOLS)}</span></div>
        </div>
        """

        content = """
        <div class="panel" style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
            <div>
                <h3 style="color: #ffffff; font-size: 16px; margin-bottom: 3px;">Scanner Universale Multi-Crypto (Dynamic Timeframe Engine Attivo)</h3>
                <p style="font-size: 12px; color: #aaaaaa;">Asset Monitorati: <strong style="color: #00ff66;">{total_assets}</strong> | Soglia Minima IA: <strong style="color: #00ff66;">{threshold}%</strong></p>
            </div>
            {threshold_control}
        </div>
        
        {live_status_section}
        {toggles_section}
        {signals_section}
        
        <h3 style="margin-bottom: 12px; color: #ffffff; font-size: 16px; font-weight: bold; border-left: 3px solid #00ff66; padding-left: 8px;">📊 Monitoraggio Mercato Live & Tachimetri Analogici IA</h3>
        <div class="grid">
            {% for symbol, data in market_data.items() %}
            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1a1a1a; padding-bottom: 8px; margin-bottom: 6px;">
                    <div>
                        <span style="font-weight: bold; color: #ffffff; font-size: 14px;">{{ data.symbol }}</span>
                        <div style="font-size: 11px; color: {% if data.action == 'LONG' %}#00ff66{% elif data.action == 'SHORT' %}#ff3333{% else %}#aaaaaa{% endif %}; font-weight: bold;">
                            Direzione: {{ data.action }} <span style="color: #aaaaaa; font-weight: normal;">({{ data.signal_time }})</span>
                        </div>
                    </div>
                    <div style="text-align: right;">
                        <span style="font-size: 11px; color: #00ff66; font-weight: bold;">Grado IA:</span>
                        <div style="font-size: 11px; font-weight: bold; color: #ffffff;">{{ data.ai_grade }}</div>
                    </div>
                </div>

                <div class="analog-gauge">
                    <div class="gauge-center"></div>
                    <div class="gauge-needle" style="transform: rotate({{ (data.score * 1.8) - 90 }}deg);"></div>
                    <div class="gauge-value-text">{{ data.score }}%</div>
                </div>

                <div style="font-size: 11px; color: #aaaaaa; display: flex; justify-content: space-between; margin-top: 4px;">
                    <span>⚡ POTENZA & INDICATORI</span>
                    <span style="color: #00ff66; font-weight: bold;">{{ data.indicators_passed }}/{{ data.indicators_total }}</span>
                </div>
                <div class="power-bar-bg">
                    <div class="power-bar-fill" style="width: {{ (data.indicators_passed / data.indicators_total * 100)|int }}%;"></div>
                </div>
                
                <div style="font-size: 11px; color: #aaaaaa; display: flex; flex-direction: column; gap: 4px; margin-top: 8px;">
                    <div style="display: flex; justify-content: space-between;"><span>Timeframe Ideale (IA):</span><strong style="color: #00ff66;">Grafico {{ data.recommended_tf }}</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span>Prezzo Live:</span><strong style="color: #ffffff;">${{ "%.4f"|format(data.price) if data.price < 1 else "%.2f"|format(data.price) }}</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span>RSI (1H / 4H):</span><span><strong style="color: #ffffff;">{{ data.rsi_1h }}</strong> / <strong style="color: #aaaaaa;">{{ data.rsi_4h }}</strong></span></div>
                    <div style="display: flex; justify-content: space-between;"><span>Margine Consigliato:</span><strong style="color: #00ff66;">${{ data.recommended_margin }} USDT ({{ data.leverage }}x)</strong></div>
                    <div style="display: flex; justify-content: space-between;"><span>Whale Tracker:</span><strong style="color: #ffffff;">{{ data.whale_status }}</strong></div>
                    
                    <div style="display: flex; justify-content: space-between; border-top: 1px dashed #222222; padding-top: 4px; margin-top: 2px;">
                        <span style="color: #ff3333;">Stop Loss:</span>
                        <strong style="color: #ff3333;">${{ "%.4f"|format(data.sl) if data.price < 1 else "%.2f"|format(data.sl) }} ({{ data.sl_pct }}%)</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between;">
                        <span style="color: #00ff66;">Take Profit 1:</span>
                        <strong style="color: #00ff66;">${{ "%.4f"|format(data.tp1) if data.price < 1 else "%.2f"|format(data.tp1) }} (+{{ data.tp1_pct }}%)</strong>
                    </div>
                </div>

                <a href="https://www.tradingview.com/chart/?symbol=BINGX:{{ data.symbol }}USDT" target="_blank" class="chart-btn">📈 Apri Grafico Live ({{ data.symbol }})</a>
            </div>
            {% endfor %}
        </div>
        """
        
        content = content.replace("{threshold}", str(current_threshold))
        content = content.replace("{total_assets}", str(len(SYMBOLS)))
        content = content.replace("{threshold_control}", threshold_control)
        content = content.replace("{live_status_section}", live_status_html)
        content = content.replace("{toggles_section}", toggles_html)
        content = content.replace("{signals_section}", signals_html)

        refresh_tag = '<meta http-equiv="refresh" content="30">'
        html = BASE_HTML.replace("{refresh_tag}", refresh_tag).replace("{{ content | safe }}", content)
        return render_template_string(html, brand_name=BRAND_NAME, market_data=market_data_cache)
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
    
    content = f"""
    <div class="panel" style="max-width: 420px; margin: 30px auto; border: 1px solid #333333;">
        <h3 style="color: #00ff66; margin-bottom: 15px; text-align: center;">Configurazione API BingX</h3>
        <p style="font-size: 12px; color: #aaaaaa; margin-bottom: 15px; text-align: center;">Inserisci le tue chiavi API Futures per abilitare l'auto-trading autonomo.</p>
        <form method="POST">
            <div class="form-group"><label>BingX API Key</label><input type="text" name="api_key" value="{current_api}" required></div>
            <div class="form-group"><label>BingX Secret Key</label><input type="password" name="secret_key" value="{current_sec}" required></div>
            <button type="submit" class="btn" style="margin-top: 10px;">Salva Chiavi API</button>
        </form>
        <div style="margin-top: 15px; text-align: center;"><a href="/" style="color: #00ff66; font-size: 13px; text-decoration: none;">← Torna alla Dashboard</a></div>
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
    <div class="panel" style="max-width: 380px; margin: 30px auto; border: 1px solid #333333;">
        <h3 style="color: #00ff66; margin-bottom: 15px; text-align: center;">Registrazione VIP</h3>
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
    <div class="panel" style="max-width: 380px; margin: 30px auto; border: 1px solid #333333;">
        <h3 style="color: #00ff66; margin-bottom: 15px; text-align: center;">Accesso Piattaforma</h3>
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
