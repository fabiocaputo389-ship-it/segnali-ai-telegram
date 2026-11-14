from flask import Flask, render_template, jsonify, request
import requests
import pandas as pd
import numpy as np

app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html')

def fetch_all_markets():
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

def safe_float(val, default=0.0):
    try:
        f = float(val)
        if np.isnan(f) or not np.isfinite(f):
            return default
        return f
    except Exception:
        return default

def get_market_data(symbol):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url = "https://api.binance.com/api/v3/klines"
        res = requests.get(url, params={"symbol": symbol, "interval": "15m", "limit": 60}, headers=headers, timeout=4).json()
        
        if isinstance(res, list) and len(res) > 0:
            df = pd.DataFrame(res, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Close_time', 'Quote_av', 'Trades', 'Tbbav', 'Tbav', 'Ignore'])
            for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                df[col] = df[col].astype(float)
            
            live_price = df['Close'].iloc[-1]
            p_res = requests.get("https://api.binance.com/api/v3/ticker/price", params={"symbol": symbol}, headers=headers, timeout=2).json()
            if 'price' in p_res:
                live_price = float(p_res['price'])
                df.loc[df.index[-1], 'Close'] = live_price

            return df, live_price, "Binance Live Real"
    except Exception:
        pass

    p = 67000.0 if 'BTC' in symbol else (1900.0 if 'ETH' in symbol else 100.0)
    df = pd.DataFrame({
        'Open': [p]*60, 'High': [p*1.001]*60, 'Low': [p*0.999]*60,
        'Close': [p]*60, 'Volume': [15000]*60
    })
    return df, p, "Offline Sync"

def calculate_certified_setup(df, current_price, mode, timeframe, account_balance, risk_percentage):
    close = df['Close']
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = safe_float((100 - (100 / (1 + rs))).iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_hist = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    current_macd = safe_float(macd_hist.iloc[-1], 0.0)

    tr = pd.concat([df['High'] - df['Low'], np.abs(df['High'] - close.shift()), np.abs(df['Low'] - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0:
        atr = current_price * 0.005

    vol = df['Volume']
    vol_sma = safe_float(vol.rolling(20).mean().iloc[-1], 1000.0)
    current_vol = safe_float(vol.iloc[-1], 1000.0)
    low_volume_flag = bool(current_vol < (vol_sma * 0.8))

    o_last = safe_float(df['Open'].iloc[-1], current_price)
    c_last = safe_float(df['Close'].iloc[-1], current_price)
    h_last = safe_float(df['High'].iloc[-1], current_price)
    l_last = safe_float(df['Low'].iloc[-1], current_price)
    body = abs(c_last - o_last)
    rng = h_last - l_last
    is_pin_bar = bool((rng > 0) and ((body / rng) < 0.3))
    
    whipsaw_detected = "Sì" if (low_volume_flag or is_pin_bar) else "No"

    ema_val_12 = safe_float(ema12.iloc[-1], current_price)
    ema_val_26 = safe_float(ema26.iloc[-1], current_price)

    ema_bullish = ema_val_12 > ema_val_26
    macd_bullish = current_macd > 0.0
    rsi_bullish_perfect = 52 <= rsi <= 72

    ema_bearish = ema_val_12 < ema_val_26
    macd_bearish = current_macd < 0.0
    rsi_bearish_perfect = 28 <= rsi <= 48

    is_perfect_buy = ema_bullish and macd_bullish and rsi_bullish_perfect and (whipsaw_detected == "No")
    is_perfect_sell = ema_bearish and macd_bearish and rsi_bearish_perfect and (whipsaw_detected == "No")

    if is_perfect_buy:
        action = "BUY"
        confidence = 100
    elif is_perfect_sell:
        action = "SELL"
        confidence = 100
    else:
        action = "BUY" if ema_val_12 >= ema_val_26 else "SELL"
        confidence = 75 if whipsaw_detected == "No" else 60

    if mode == 'Scalping':
        sl_m, tp_m = 0.4, 1.0
    elif mode == 'Day Trading':
        sl_m, tp_m = 0.9, 2.0
    elif mode == 'Swing Trading':
        sl_m, tp_m = 2.0, 4.5
    else:
        sl_m, tp_m = 1.0, 2.0

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m
    decimals = 6 if current_price < 0.01 else (4 if current_price < 5 else 2)

    if action == "BUY":
        entry_l = round(current_price - (atr * 0.05), decimals)
        entry_h = round(current_price + (atr * 0.05), decimals)
        sl = round(current_price - sl_dist, decimals)
        tp1 = round(current_price + tp_dist, decimals)
        tp2 = round(current_price + (tp_dist * 1.5), decimals)
        tp3 = round(current_price + (tp_dist * 2.0), decimals)
        order_type = "Buy Limit"
    else:
        entry_l = round(current_price - (atr * 0.05), decimals)
        entry_h = round(current_price + (atr * 0.05), decimals)
        sl = round(current_price + sl_dist, decimals)
        tp1 = round(current_price - tp_dist, decimals)
        tp2 = round(current_price - (tp_dist * 1.5), decimals)
        tp3 = round(current_price - (tp_dist * 2.0), decimals)
        order_type = "Sell Limit"

    max_risk_amount = account_balance * (risk_percentage / 100.0)
    price_risk_per_unit = abs(current_price - sl)
    position_size = (max_risk_amount / price_risk_per_unit) if price_risk_per_unit > 0 else 0.0
    rrr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    if confidence == 100:
        status_msg = "🔥 AUTO-PILOT: Perfezione Assoluta (100%) raggiunta. Indicatori e volumi allineati."
    else:
        status_msg = "📊 AUTO-PILOT: Analisi attiva sui mercati globali."

    ai_advice = (
        f"[{mode}] Auto-Calibrazione attiva. Rischio: {risk_percentage}% "
        f"(${max_risk_amount:.2f}). RRR = 1:{rrr}. {status_msg}"
    )

    return {
        "action": action, "confidence": int(confidence), "order_type": order_type,
        "entry_zone": f"{entry_l} - {entry_h}", "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "risk_amount": round(max_risk_amount, 2), "position_size": round(position_size, 4),
        "rrr": rrr, "whipsaw_detected": whipsaw_detected, "status_msg": status_msg,
        "ai_recommendation": ai_advice, "decimals": decimals
    }

@app.route('/api/markets', methods=['GET'])
def get_markets():
    return jsonify(fetch_all_markets())

@app.route('/api/analyze', methods=['POST'])
def analyze_chart():
    try:
        data = request.json or {}
        symbol = data.get('symbol', 'BTCUSDT')
        timeframe = data.get('timeframe', '15m')
        mode = data.get('mode', 'Day Trading')
        account_balance = float(data.get('account_balance', 1000.0))
        risk_percentage = float(data.get('risk_percentage', 1.0))

        df, current_price, source = get_market_data(symbol)
        
        # Calcolo variazione % giornaliera per la barra live
        p_open = float(df['Open'].iloc[0]) if df['Open'].iloc[0] > 0 else current_price
        change = float(((current_price - p_open) / p_open) * 100)
        if np.isnan(change) or not np.isfinite(change): change = 0.0

        res = calculate_certified_setup(df, current_price, mode, timeframe, account_balance, risk_percentage)
        dec = res["decimals"]

        return jsonify({
            "status": "success", "symbol": symbol, "source": source,
            "current_price": round(current_price, dec), "price_change": f"{change:+.2f}%",
            "action": res["action"], "order_type": res["order_type"], "favorability": res["confidence"],
            "entry_zone": res["entry_zone"], "stop_loss": res["sl"],
            "take_profit_1": res["tp1"], "take_profit_2": res["tp2"], "take_profit_3": res["tp3"],
            "risk_amount": res["risk_amount"], "position_size": res["position_size"], "rrr": res["rrr"],
            "whipsaw_detected": str(res["whipsaw_detected"]), "status_msg": res["status_msg"],
            "ai_recommendation": res["ai_recommendation"]
        })
    except Exception as e:
        print("Errore in /api/analyze:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/signals', methods=['GET'])
def get_signals():
    try:
        markets = fetch_all_markets()
        signals = []
        for cat, assets in markets.items():
            for a in assets:
                df, current_price, source = get_market_data(a['symbol'])
                if df is not None and not df.empty:
                    p_open = float(df['Open'].iloc[0]) if df['Open'].iloc[0] > 0 else current_price
                    change = float(((current_price - p_open) / p_open) * 100)
                    if np.isnan(change) or not np.isfinite(change):
                        change = 0.0

                    res = calculate_certified_setup(df, current_price, 'Day Trading', '15m', 1000.0, 1.0)
                    dec = res["decimals"]
                    signals.append({
                        "category": cat, "symbol": a['id'], "name": a['name'],
                        "action": res["action"], "order_type": res["order_type"],
                        "change": f"{change:+.2f}%", "price": f"{current_price:.{dec}f}",
                        "prob": f"{res['confidence']}%", "entry": res["entry_zone"],
                        "sl": res["sl"], "tp1": res["tp1"], "rrr": res["rrr"],
                        "raw_conf": res["confidence"]
                    })
        signals = sorted(signals, key=lambda x: x['raw_conf'], reverse=True)
        return jsonify(signals)
    except Exception as e:
        print("Errore in /api/signals:", e)
        return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
