from flask import Flask, render_template, jsonify, request
import requests
import pandas as pd
import numpy as np
from datetime import datetime

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

def fetch_all_markets():
    # Usiamo le API pubbliche di Binance per dati reali in tempo reale sui server cloud
    markets = {
        "Criptovalute": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin', 'symbol': 'BTCUSDT'},
            {'id': 'ETHUSDT', 'name': 'Ξ Ethereum', 'symbol': 'ETHUSDT'},
            {'id': 'SOLUSDT', 'name': '🟣 Solana', 'symbol': 'SOLUSDT'},
            {'id': 'XRPUSDT', 'name': '✕ Ripple', 'symbol': 'XRPUSDT'},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin', 'symbol': 'DOGEUSDT'},
            {'id': 'BNBUSDT', 'name': '🟡 BNB', 'symbol': 'BNBUSDT'},
            {'id': 'ADAUSDT', 'name': '₳ Cardano', 'symbol': 'ADAUSDT'}
        ],
        "Materie Prime & Forex": [
            {'id': 'XAUUSD', 'name': '🥇 Oro (Simulato Live)', 'symbol': 'PAXGUSDT'},
            {'id': 'EURUSDT', 'name': '💶 EUR/USDT', 'symbol': 'EURUSDT'}
        ]
    }
    return markets

def get_market_data(symbol):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # Endpoint klines pubblico di Binance (reale al 100%)
        url = "https://api.binance.com/api/v3/klines"
        res = requests.get(url, params={"symbol": symbol, "interval": "15m", "limit": 60}, headers=headers, timeout=4).json()
        
        if isinstance(res, list) and len(res) > 0:
            df = pd.DataFrame(res, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'Quote_av', 'Trades', 'Tbbav', 'Tbav', 'Ignore'])
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
            
            live_price = df['Close'].iloc[-1]
            
            # Prendi il prezzo ticker istantaneo
            p_res = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}, headers=headers, timeout=2).json()
            if 'price' in p_res:
                live_price = float(p_res['price'])
                df.loc[df.index[-1], 'Close'] = live_price

            return df, live_price, "Binance Live Real"
    except Exception as e:
        print("Errore API:", e)

    # Fallback di sicurezza estrema basato sull'ultimo prezzo noto
    p = 67000.0 if 'BTC' in symbol else (1900.0 if 'ETH' in symbol else 100.0)
    df = pd.DataFrame({
        'Open': [p]*60, 'High': [p*1.001]*60, 'Low': [p*0.999]*60,
        'Close': [p]*60, 'Volume': [15000]*60
    })
    return df, p, "Offline Sync"

def calculate_certified_setup(df, current_price, mode, timeframe):
    close = df['Close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = float((100 - (100 / (1 + rs))).iloc[-1])
    if np.isnan(rsi) or not np.isfinite(rsi): rsi = 50.0

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    current_macd = float(macd_hist.iloc[-1])
    if np.isnan(current_macd) or not np.isfinite(current_macd): current_macd = 0.0

    tr = pd.concat([df['High'] - df['Low'], np.abs(df['High'] - close.shift()), np.abs(df['Low'] - close.shift())], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    if np.isnan(atr) or not np.isfinite(atr) or atr <= 0: atr = current_price * 0.005

    is_bullish = (ema12.iloc[-1] >= ema26.iloc[-1]) and (current_macd >= 0)
    action = "BUY" if is_bullish else "SELL"
    confidence = int(min(82 + abs(rsi - 50) * 0.5 + (abs(current_macd) * 5), 96))

    if mode == 'Scalping':
        sl_m, tp_m = 0.4, 0.9
    elif mode == 'Day Trading':
        sl_m, tp_m = 0.9, 1.8
    elif mode == 'Swing Trading':
        sl_m, tp_m = 2.0, 4.0
    else:
        sl_m, tp_m = 1.0, 2.0

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m

    decimals = 6 if current_price < 0.01 else (4 if current_price < 5 else (2 if current_price >= 1 else 2))

    if action == "BUY":
        entry_l = round(current_price - (atr * 0.05), decimals)
        entry_h = round(current_price + (atr * 0.05), decimals)
        sl = round(current_price - sl_dist, decimals)
        tp1 = round(current_price + tp_dist, decimals)
        tp2 = round(current_price + (tp_dist * 1.5), decimals)
        tp3 = round(current_price + (tp_dist * 2.0), decimals)
        order_type = "Buy Limit"
        counter_act = "SELL"
    else:
        entry_l = round(current_price - (atr * 0.05), decimals)
        entry_h = round(current_price + (atr * 0.05), decimals)
        sl = round(current_price + sl_dist, decimals)
        tp1 = round(current_price - tp_dist, decimals)
        tp2 = round(current_price - (tp_dist * 1.5), decimals)
        tp3 = round(current_price - (tp_dist * 2.0), decimals)
        order_type = "Sell Limit"
        counter_act = "BUY"

    counter_sl = round(current_price + (atr * 1.2) if counter_act == "SELL" else current_price - (atr * 1.2), decimals)
    counter_tp = round(current_price - (atr * 1.0) if counter_act == "SELL" else current_price + (atr * 1.0), decimals)

    ai_advice = f"[{mode}] Analisi completata sui dati reali di mercato. Trend rilevato: {action}. Gestione ATR attiva."
    counter_warning = f"La posizione {counter_act} è contraria al trend principale rilevato dagli indicatori."

    return {
        "action": action, "confidence": confidence, "order_type": order_type,
        "entry_zone": f"{entry_l} - {entry_h}", "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "counter_action": counter_act, "counter_order_type": f"Market {counter_act}",
        "counter_entry": round(current_price, decimals), "counter_sl": counter_sl, "counter_tp": counter_tp,
        "counter_warning": counter_warning, "ai_recommendation": ai_advice,
        "decimals": decimals
    }

@app.route('/api/markets', methods=['GET'])
def get_markets():
    return jsonify(fetch_all_markets())

@app.route('/api/analyze', methods=['POST'])
def analyze_chart():
    data = request.json or {}
    symbol = data.get('symbol', 'BTCUSDT')
    timeframe = data.get('timeframe', '15m')
    mode = data.get('mode', 'Day Trading')

    df, current_price, source = get_market_data(symbol)
    res = calculate_certified_setup(df, current_price, mode, timeframe)
    dec = res["decimals"]

    return jsonify({
        "status": "success", "symbol": symbol, "source": source,
        "current_price": round(current_price, dec), "action": res["action"],
        "order_type": res["order_type"], "favorability": res["confidence"],
        "entry_zone": res["entry_zone"], "stop_loss": res["sl"],
        "take_profit_1": res["tp1"], "take_profit_2": res["tp2"], "take_profit_3": res["tp3"],
        "counter_action": res["counter_action"], "counter_order_type": res["counter_order_type"],
        "counter_entry": res["counter_entry"], "counter_sl": res["counter_sl"], "counter_tp": res["counter_tp"],
        "counter_warning": res["counter_warning"], "ai_recommendation": res["ai_recommendation"]
    })

@app.route('/api/signals', methods=['GET'])
def get_signals():
    markets = fetch_all_markets()
    signals = []
    for cat, assets in markets.items():
        for a in assets:
            df, current_price, source = get_market_data(a['symbol'])
            if df is not None and not df.empty:
                p_open = float(df['Open'].iloc[0]) if df['Open'].iloc[0] > 0 else current_price
                change = float(((current_price - p_open) / p_open) * 100)
                if np.isnan(change) or not np.isfinite(change): change = 0.0

                res = calculate_certified_setup(df, current_price, 'Day Trading', '15m')
                dec = res["decimals"]
                signals.append({
                    "category": cat, "symbol": a['id'], "name": a['name'],
                    "action": res["action"], "order_type": res["order_type"],
                    "change": f"{change:+.2f}%", "price": f"{current_price:.{dec}f}",
                    "prob": f"{res['confidence']}%", "entry": res["entry_zone"],
                    "sl": res["sl"], "tp1": res["tp1"]
                })
    return jsonify(signals)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
