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
session_realized_pnl = 0.0

def get_exchange_mode():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return "PAPER PROP-DESK (REAL CANDLE DURATION ACTIVE)"
    return "🔴 BINGX LIVE REALE (MERCATI GLOBALI ATTIVI)"

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

def fetch_bingx_real_positions():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return []
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
                        "unrealized_pnl": round(float(p.get("unrealizedProfit", 0)), 2)
                    })
            return real_pos_list
    except Exception as e:
        print(f"Errore posizioni: {e}")
    return []

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
    return jsonify({
        "auto_trade": auto_trade_active,
        "mode": get_exchange_mode(),
        "positions": fetch_bingx_real_positions()
    })

def fetch_all_markets():
    return {
        "Criptovalute": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin'}, {'id': 'ETHUSDT', 'name': 'Ξ Ethereum'},
            {'id': 'SOLUSDT', 'name': '🟣 Solana'}, {'id': 'XRPUSDT', 'name': '✕ Ripple'},
            {'id': 'BNBUSDT', 'name': '🟡 BNB'}, {'id': 'ADAUSDT', 'name': '₳ Cardano'},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin'}, {'id': 'AVAXUSDT', 'name': '🔺 Avalanche'},
            {'id': 'LINKUSDT', 'name': '🔗 Chainlink'}, {'id': 'SUIUSDT', 'name': '💧 Sui'}
        ],
        "Materie Prime & Forex": [
            {'id': 'XAUUSD', 'name': '🥇 Oro (Gold)'}, {'id': 'XAGUSD', 'name': '🥈 Argento (Silver)'},
            {'id': 'EURUSDT', 'name': '💶 EUR/USD'}, {'id': 'GBPUSDT', 'name': '🇬🇧 GBP/USD'},
            {'id': 'USDJPY', 'name': '🇯🇵 USD/JPY'}
        ],
        "Azioni & Indici": [
            {'id': 'AAPL', 'name': '🍏 Apple Inc.'}, {'id': 'TSLA', 'name': '🚗 Tesla Inc.'},
            {'id': 'NVDA', 'name': '💻 Nvidia Corp.'}, {'id': 'AMZN', 'name': '📦 Amazon'},
            {'id': 'SPX500', 'name': '📈 S&P 500 Index'}
        ]
    }

def safe_float(val, default=0.0):
    try:
        f = float(val)
        return default if (np.isnan(f) or not np.isfinite(f)) else f
    except Exception:
        return default

def get_market_data(symbol, interval="15m"):
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
    p = 100.0
    return pd.DataFrame({'Open': [p]*60, 'High': [p*1.002]*60, 'Low': [p*0.998]*60, 'Close': [p]*60, 'Volume': [30000]*60}), p

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

    # --- ANALISI ESATTA DELLE CANDELE STORICHE PER LA DURATA ---
    tf_min = 5 if timeframe == '5m' else (15 if timeframe == '15m' else 60)
    
    # Calcolo della reale escursione media (High - Low) delle candele storiche nel DataFrame
    candle_ranges = df['High'] - df['Low']
    avg_candle_span = safe_float(candle_ranges.mean(), atr)
    if avg_candle_span <= 0:
        avg_candle_span = atr

    # Numero di candele reali necessarie per raggiungere il TP basato sulla volatilità delle candele storiche
    candles_needed = max(1, round(tp_dist / avg_candle_span))
    est_min = candles_needed * tf_min

    if est_min < 60:
        duration = f"~{est_min} min ({candles_needed} candele)"
    elif est_min < 1440:
        duration = f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    else:
        duration = f"~{round(est_min / 1440, 1)} giorni ({candles_needed} candele)"
    # -----------------------------------------------------------

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = abs(current_price - sl)
    pos_size = round(max((max_risk / risk_unit) if risk_unit > 0 else 0.0, 0.001), 3)
    lev = min(20, max(3, int(round(15.0 / max((risk_unit / current_price) * 100, 0.5)))))
    margin = round((pos_size * current_price) / lev, 2)

    return {
        "action": action, "ai_score": f"{score}%", "sentiment": sent, "sentiment_color": col,
        "inversion": inv, "entry": f"{entry:.{decimals}f}", "sl": f"{sl:.{decimals}f}",
        "tp1": f"{tp1:.{decimals}f}", "target_rialzista": f"{target_rialzista:.{decimals}f}",
        "target_ribassista": f"{target_ribassista:.{decimals}f}", "duration": duration,
        "rrr": round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0, "rsi": round(rsi, 1),
        "size": pos_size, "leverage": f"Isolata {lev}x", "margin": margin
    }

@app.route('/api/signals', methods=['GET'])
def get_signals():
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
            futures = [executor.submit(get_market_data, a['id'], timeframe) for _, a in target_assets]
            for i, future in enumerate(futures):
                cat, a = target_assets[i]
                df, current_price = future.result()
                if df is not None and not df.empty:
                    res = calculate_setup(df, current_price, balance, risk, r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m, timeframe)
                    raw_signals.append({
                        "id": f"{a['id']}_{res['action']}", "category": cat, "symbol": a['id'],
                        "name": a['name'], "price": f"{current_price:.2f}" if current_price > 5 else f"{current_price:.6f}",
                        **res
                    })

        unique_map = {sig['symbol']: sig for sig in raw_signals}
        signals = list(unique_map.values())
        signals.sort(key=lambda x: float(x['ai_score'].replace('%','')), reverse=True)
        return jsonify(signals)
    except Exception as e:
        return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
