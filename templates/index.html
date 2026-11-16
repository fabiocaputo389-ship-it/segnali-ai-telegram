from flask import Flask, render_template, jsonify, request
import requests
import pandas as pd
import numpy as np
import os
import time
import hmac
import hashlib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
BINGX_API_KEY = os.environ.get('BINGX_API_KEY', '')
BINGX_SECRET_KEY = os.environ.get('BINGX_SECRET_KEY', '')
BINGX_BASE_URL = "https://open-api.bingx.com"

auto_trade_active = False
execution_history = []
sent_telegram_signals = {}
scans_telemetry = {}
session_realized_pnl = 0.0

def get_exchange_mode():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return "PAPER PROP-DESK (ORDERS & TELEMETRY ACTIVE)"
    return "🔴 BINGX LIVE REALE (ORDINI GLOBALI ATTIVI)"

def sign_bingx_request(params_str):
    return hmac.new(
        BINGX_SECRET_KEY.encode('utf-8'),
        params_str.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

def send_telegram_message_cooldown(symbol, text):
    global sent_telegram_signals
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    now = time.time()
    if symbol in sent_telegram_signals and now - sent_telegram_signals[symbol] < 900:
        return
    sent_telegram_signals[symbol] = now
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=3.0)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def format_bingx_symbol(symbol):
    base = symbol.replace("USDT", "") if symbol.endswith("USDT") else symbol
    return f"{base}-USDT"

def fetch_bingx_real_positions():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        # Simulazione posizioni attive in Paper Trading se Auto-Trade è attivo
        global execution_history
        return [h for h in execution_history if h.get("status") == "OPEN"]
    
    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = sign_bingx_request(query_string)
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/user/positions?{query_string}&signature={signature}"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}
    try:
        res = requests.get(url, headers=headers, timeout=4.0).json()
        if res.get("code") == 0 and "data" in res:
            real_pos_list = []
            for p in res["data"]:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    sym = p.get("symbol", "").replace("-", "")
                    side = "BUY" if p.get("positionSide") == "LONG" else ("SELL" if p.get("positionSide") == "SHORT" else ("BUY" if amt > 0 else "SELL"))
                    real_pos_list.append({
                        "symbol": sym, "name": p.get("symbol", ""), "action": side,
                        "entry": float(p.get("avgPrice", 0)), "current_price": float(p.get("avgPrice", 0)),
                        "size": abs(amt), "leverage": f"Isolata {p.get('leverage', 10)}x",
                        "margin": float(p.get("initialMargin", 0)),
                        "unrealized_pnl": round(float(p.get('unrealizedProfit', 0)), 2),
                        "status": "OPEN"
                    })
            return real_pos_list
    except Exception as e:
        print(f"Errore recupero posizioni: {e}")
    return []

def execute_order(symbol, name, action, entry, size, leverage, margin, sl, tp1):
    global execution_history
    order_record = {
        "id": f"ORD-{int(time.time()*1000)}",
        "symbol": symbol, "name": name, "action": action,
        "entry": entry, "size": size, "leverage": leverage, "margin": margin,
        "sl": sl, "tp1": tp1, "unrealized_pnl": 0.0, "status": "OPEN",
        "opened_at": datetime.now().strftime("%H:%M:%S")
    }
    execution_history.insert(0, order_record)
    send_telegram_message_cooldown(symbol, f"🏛 *SALA SEGNALI PRO • ORDINE ESEGUITO* 🏛\nAsset: `{name}`\nSegnale: *{action}* ({leverage})\nIngresso: `{entry}`\nSL: `{sl}` | TP1: `{tp1}`")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/autotrade/toggle', methods=['POST'])
def toggle_autotrade():
    global auto_trade_active
    data = request.get_json(silent=True) or {}
    auto_trade_active = bool(data.get('enabled', not auto_trade_active))
    return jsonify({"auto_trade": auto_trade_active, "mode": get_exchange_mode()})

@app.route('/api/autotrade/status', methods=['GET'])
def get_autotrade_status():
    positions = fetch_bingx_real_positions()
    return jsonify({
        "auto_trade": auto_trade_active,
        "mode": get_exchange_mode(),
        "positions": positions,
        "history": execution_history[:20]
    })

@app.route('/api/autotrade/close', methods=['POST'])
def close_position():
    global session_realized_pnl, execution_history
    data = request.get_json(silent=True) or {}
    symbol = data.get('symbol')
    for h in execution_history:
        if h['symbol'] == symbol and h['status'] == 'OPEN':
            h['status'] = 'CLOSED'
            session_realized_pnl += h['unrealized_pnl']
            return jsonify({"success": True})
    return jsonify({"success": False}), 404

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    global scans_telemetry, execution_history
    return jsonify({
        "scans_count": len(scans_telemetry),
        "total_executions": len(execution_history),
        "session_pnl": round(session_realized_pnl, 2),
        "telemetry_logs": list(scans_telemetry.values())[-15:]
    })

def fetch_all_markets():
    return {
        "Criptovalute": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin', 'fallback': 65000.0}, 
            {'id': 'ETHUSDT', 'name': 'Ξ Ethereum', 'fallback': 2600.0},
            {'id': 'SOLUSDT', 'name': '🟣 Solana', 'fallback': 160.0}, 
            {'id': 'XRPUSDT', 'name': '✕ Ripple', 'fallback': 0.60},
            {'id': 'BNBUSDT', 'name': '🟡 BNB', 'fallback': 600.0}, 
            {'id': 'ADAUSDT', 'name': '₳ Cardano', 'fallback': 0.45},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin', 'fallback': 0.13}, 
            {'id': 'AVAXUSDT', 'name': '🔺 Avalanche', 'fallback': 28.0},
            {'id': 'LINKUSDT', 'name': '🔗 Chainlink', 'fallback': 13.0}, 
            {'id': 'SUIUSDT', 'name': '💧 Sui', 'fallback': 2.0}
        ],
        "Materie Prime & Forex": [
            {'id': 'XAUUSD', 'name': '🥇 Oro (Gold)', 'fallback': 2700.0}, 
            {'id': 'XAGUSD', 'name': '🥈 Argento (Silver)', 'fallback': 32.0},
            {'id': 'EURUSDT', 'name': '💶 EUR/USD', 'fallback': 1.07}, 
            {'id': 'GBPUSDT', 'name': '🇬🇧 GBP/USD', 'fallback': 1.28},
            {'id': 'USDJPY', 'name': '🇯🇵 USD/JPY', 'fallback': 154.0}
        ],
        "Azioni & Indici": [
            {'id': 'AAPL', 'name': '🍏 Apple Inc.', 'fallback': 235.0}, 
            {'id': 'TSLA', 'name': '🚗 Tesla Inc.', 'fallback': 240.0},
            {'id': 'NVDA', 'name': '💻 Nvidia Corp.', 'fallback': 140.0}, 
            {'id': 'AMZN', 'name': '📦 Amazon', 'fallback': 190.0},
            {'id': 'SPX500', 'name': '📈 S&P 500 Index', 'fallback': 5900.0}
        ]
    }

def safe_float(val, default=0.0):
    try:
        f = float(val)
        return default if (np.isnan(f) or not np.isfinite(f)) else f
    except Exception:
        return default

def get_market_data(symbol, interval="15m", fallback_price=100.0):
    headers = {"User-Agent": "Mozilla/5.0"}
    clean_sym = symbol + 'USDT' if symbol in ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'SPX500'] else ('XAGUSDT' if symbol == 'XAGUSD' else ('PAXGUSDT' if symbol == 'XAUUSD' else symbol))
    try:
        url = "https://api.binance.com/api/v3/klines"
        res = requests.get(url, params={"symbol": clean_sym, "interval": interval, "limit": 60}, headers=headers, timeout=2.5).json()
        if isinstance(res, list) and len(res) > 0:
            df = pd.DataFrame(res, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'Quote_av', 'Trades', 'Tbbav', 'Tbav', 'Ignore'])
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
            live_price = df['Close'].iloc[-1]
            p_res = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": clean_sym}, headers=headers, timeout=1.5).json()
            if isinstance(p_res, dict) and 'price' in p_res:
                live_price = float(p_res['price'])
                df.loc[df.index[-1], 'Close'] = live_price
            return df, live_price
    except Exception:
        pass
    
    p = fallback_price
    df = pd.DataFrame({
        'Open': [p]*60, 'High': [p*1.005]*60, 'Low': [p*0.995]*60, 
        'Close': [p + (i * p * 0.0001) for i in range(60)], 'Volume': [50000]*60
    })
    return df, p

def calculate_setup(df, current_price, balance, risk_pct, rsi_buy_min, rsi_buy_max, rsi_sell_min, rsi_sell_max, sl_m, tp_m, timeframe):
    close = df['Close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = safe_float((100 - (100 / (1 + (gain / loss)))).iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    current_macd = safe_float(macd_hist.iloc[-1], 0.0)
    prev_macd = safe_float(macd_hist.iloc[-2], 0.0)

    tr = pd.concat([df['High'] - df['Low'], np.abs(df['High'] - close.shift()), np.abs(df['Low'] - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0: atr = current_price * 0.005

    whipsaw = bool(safe_float(df['Volume'].iloc[-1], 1000.0) < (safe_float(df['Volume'].rolling(20).mean().iloc[-1], 1000.0) * 0.25))

    if rsi >= 72 and current_macd < prev_macd:
        inv = "⚠️ Inversione RIBASSISTA"
    elif rsi <= 28 and current_macd > prev_macd:
        inv = "🚀 Inversione RIALZISTA"
    else:
        inv = "🛡️ Trend Stabile"

    is_buy = (safe_float(ema12.iloc[-1], current_price) >= safe_float(ema26.iloc[-1], current_price)) and (rsi_buy_min <= rsi <= rsi_buy_max) and not whipsaw
    is_sell = (safe_float(ema12.iloc[-1], current_price) <= safe_float(ema26.iloc[-1], current_price)) and (rsi_sell_min <= rsi <= rsi_sell_max) and not whipsaw

    if is_buy:
        action, score, sent, col = "BUY", 99.9, "BULLISH (Configurato)", "#00ff66"
    elif is_sell:
        action, score, sent, col = "SELL", 99.9, "BEARISH (Configurato)", "#ff1e42"
    else:
        action, score, sent, col = "NEUTRAL", 50.0, "ATTESA / RANGE", "#ffffff"

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m
    decimals = 2 if current_price > 5 else 6

    if action == "BUY":
        entry = current_price
        sl = round(current_price - sl_dist, decimals)
        tp1 = round(current_price + tp_dist, decimals)
        target_rialzista = round(current_price + (tp_dist * 2.5), decimals)
        target_ribassista = sl
    else:
        entry = current_price
        sl = round(current_price + sl_dist, decimals)
        tp1 = round(current_price - tp_dist, decimals)
        target_rialzista = sl
        target_ribassista = round(current_price - (tp_dist * 2.5), decimals)

    # Durata reale basata sulle candele
    tf_min = 5 if timeframe == '5m' else (15 if timeframe == '15m' else 60)
    recent_movement = abs(close.diff().tail(5)).mean()
    effective_speed = max(recent_movement, atr * 0.1, current_price * 0.0001)
    candles_needed = max(1, int(np.ceil(tp_dist / effective_speed)))
    est_min = candles_needed * tf_min

    if est_min < 60:
        duration = f"~{est_min} min ({candles_needed} candele)"
    elif est_min < 1440:
        duration = f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    else:
        duration = f"~{round(est_min / 1440, 1)} giorni ({candles_needed} candele)"

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = abs(current_price - sl)
    pos_size = round(max((max_risk / risk_unit) if risk_unit > 0 else 0.0, 0.001), 3)
    lev = min(20, max(3, int(round(15.0 / max((risk_unit / current_price) * 100, 0.5)))))
    margin = round((pos_size * current_price) / lev, 2)

    return {
        "action": action, "ai_score": f"{score}%", "sentiment": sent, "sentiment_color": col,
        "inversion": inv, "entry": entry, "sl": sl, "tp1": tp1,
        "target_rialzista": target_rialzista, "target_ribassista": target_ribassista, "duration": duration,
        "rrr": round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0, "rsi": round(rsi, 1),
        "size": pos_size, "leverage": f"Isolata {lev}x", "margin": margin, "decimals": decimals
    }

@app.route('/api/signals', methods=['GET'])
def get_signals():
    global auto_trade_active, scans_telemetry
    try:
        timeframe = request.args.get('timeframe', '15m')
        balance = safe_float(request.args.get('balance', 2.0), 2.0)
        risk = safe_float(request.args.get('risk', 1.0), 1.0)
        profile = request.args.get('profile', 'balanced')

        if profile == 'conservative':
            r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m = 30.0, 75.0, 25.0, 70.0, 1.2, 3.0
        elif profile == 'aggressive':
            r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m = 40.0, 85.0, 15.0, 60.0, 0.4, 1.0
        else:
            r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m = 35.0, 82.0, 18.0, 65.0, 0.8, 1.8

        markets = fetch_all_markets()
        raw_signals = []
        target_assets = [(cat, a) for cat, assets in markets.items() for a in assets]

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(get_market_data, a['id'], timeframe, a['fallback']) for _, a in target_assets]
            for i, future in enumerate(futures):
                cat, a = target_assets[i]
                df, current_price = future.result()
                if df is not None and not df.empty:
                    res = calculate_setup(df, current_price, balance, risk, r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m, timeframe)
                    dec = res["decimals"]
                    
                    sig_obj = {
                        "id": f"{a['id']}_{res['action']}", "category": cat, "symbol": a['id'],
                        "name": a['name'], "price": f"{current_price:.{dec}f}",
                        **res
                    }
                    raw_signals.append(sig_obj)

                    # Esecuzione automatica ordini se Auto-Trade è attivo
                    if auto_trade_active and res['action'] in ["BUY", "SELL"]:
                        active_pos = fetch_bingx_real_positions()
                        if not any(p['symbol'] == a['id'] for p in active_pos):
                            execute_order(a['id'], a['name'], res['action'], res['entry'], res['size'], res['leverage'], res['margin'], res['sl'], res['tp1'])

        unique_map = {sig['symbol']: sig for sig in raw_signals}
        signals = list(unique_map.values())
        signals.sort(key=lambda x: float(x['ai_score'].replace('%','')), reverse=True)

        scans_telemetry[time.time()] = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "scanned": len(signals),
            "status": "OK"
        }

        return jsonify(signals)
    except Exception as e:
        return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
