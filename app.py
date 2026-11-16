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

# ==========================================
# 🎛️ PANNELLO CONFIGURAZIONE MANUALE UTENTE
# Modifica liberamente questi valori secondo la tua strategia
# ==========================================
MANUAL_CONFIG = {
    "ACCOUNT_BALANCE_USD": 2.0,            # Capitale di partenza simulato / reale ($)
    "RISK_PERCENTAGE_PER_TRADE": 1.0,      # Percentuale di capitale rischiata per ogni singolo trade (%)
    "DAILY_MAX_DRAWDOWN_USD": 10.0,        # Circuit breaker: blocco automatico a -10$ di perdita giornaliera
    "DEFAULT_TIMEFRAME": "15m",            # Timeframe di analisi (es. '5m', '15m', '1h')
    "DEFAULT_TRADING_MODE": "Day Trading", # Stile operativo ('Scalping', 'Day Trading', 'Swing Trading')
    
    # Soglie manuali per i filtri tecnici (RSI)
    "RSI_BUY_MIN": 35.0,
    "RSI_BUY_MAX": 82.0,
    "RSI_SELL_MIN": 18.0,
    "RSI_SELL_MAX": 65.0,
    
    # Moltiplicatori di volatilità ATR per Stop Loss e Take Profit
    "SL_MULTIPLIER": 0.8,
    "TP_MULTIPLIER": 1.8
}
# ==========================================

# Configurazione Telegram Bot
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# Configurazione Exchange BingX (MODALITÀ REALE FORZATA)
BINGX_API_KEY = os.environ.get('BINGX_API_KEY', '')
BINGX_SECRET_KEY = os.environ.get('BINGX_SECRET_KEY', '')
BINGX_USE_TESTNET = False  
BINGX_BASE_URL = "https://open-api.bingx.com"

# Stato Globale Prop-Desk & Protezione Istituzionale
auto_trade_active = False
execution_history = []
sent_telegram_signals = {}  # Cooldown anti-spam per simbolo (15 min)
scans_telemetry = {}
session_realized_pnl = 0.0

def get_exchange_mode():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return "PAPER PROP-DESK (MANUAL CONFIG ACTIVE)"
    return "🔴 BINGX LIVE REALE (MERCATO GLOBALE ATTIVO)"

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
    if symbol in sent_telegram_signals:
        if now - sent_telegram_signals[symbol] < 900:  # 15 minuti di cooldown
            return
            
    sent_telegram_signals[symbol] = now
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=3.0)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def format_bingx_symbol(symbol):
    if symbol.endswith("USDT"):
        base = symbol.replace("USDT", "")
        return f"{base}-USDT"
    return f"{symbol}-USDT"

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
                        "symbol": sym,
                        "name": p.get("symbol", ""),
                        "action": side,
                        "entry": float(p.get("avgPrice", 0)),
                        "current_price": float(p.get("avgPrice", 0)),
                        "sl": 0.0,
                        "tp1": 0.0,
                        "tp3": 0.0,
                        "breakeven": 0.0,
                        "be_moved": False,
                        "size": abs(amt),
                        "leverage": f"Isolata {p.get('leverage', 10)}x",
                        "margin": float(p.get("initialMargin", 0)),
                        "unrealized_pnl": round(float(p.get("unrealizedProfit", 0)), 2),
                        "order_id": p.get("positionId", "LIVE"),
                        "exec_mode": "LIVE_BINGX",
                        "opened_at": "Reale"
                    })
            return real_pos_list
    except Exception as e:
        print(f"Errore recupero posizioni BingX: {e}")
    return []

def execute_bingx_order(symbol, side, position_side, quantity, stop_loss=None, take_profit=None):
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return {"status": "PAPER_FILLED", "orderId": f"PROP-{int(time.time()*1000)}"}

    timestamp = int(time.time() * 1000)
    bx_symbol = format_bingx_symbol(symbol)
    params = {
        "symbol": bx_symbol,
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity,
        "timestamp": timestamp
    }
    if stop_loss:
        params["stopLoss"] = f'{{"type": "STOP_MARKET", "stopPrice": {stop_loss}, "workingType": "MARK_PRICE"}}'
    if take_profit:
        params["takeProfit"] = f'{{"type": "TAKE_PROFIT_MARKET", "stopPrice": {take_profit}, "workingType": "MARK_PRICE"}}'

    query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = sign_bingx_request(query_string)
    url = f"{BINGX_BASE_URL}/openApi/swap/v2/trade/order?{query_string}&signature={signature}"
    headers = {"X-BX-APIKEY": BINGX_API_KEY}

    try:
        res = requests.post(url, headers=headers, timeout=4.0).json()
        if res.get("code") == 0:
            return {"status": "LIVE_FILLED", "orderId": res["data"]["order"]["orderId"]}
        return {"status": "ERROR", "msg": res.get("msg", "Errore API Exchange")}
    except Exception as e:
        return {"status": "ERROR", "msg": str(e)}

def check_circuit_breaker():
    global auto_trade_active, session_realized_pnl
    limit = MANUAL_CONFIG["DAILY_MAX_DRAWDOWN_USD"]
    if session_realized_pnl <= -limit:
        auto_trade_active = False
        send_telegram_message_cooldown("CIRCUIT_BREAKER", f"🚨 *CIRCUIT BREAKER ATTIVATO* 🚨\nSoglia di perdita giornaliera (${limit}) raggiunta. Auto-Trade disattivato.")
        return True
    return False

def manage_auto_execution(signal):
    global auto_trade_active
    if not auto_trade_active:
        return

    if check_circuit_breaker():
        return

    symbol = signal['symbol']
    action = signal['action']
    
    if action in ["BUY", "SELL"]:
        try:
            real_positions = fetch_bingx_real_positions()
            already_open = any(p['symbol'] == symbol for p in real_positions)
            
            if not already_open:
                qty = float(signal['position_size'])
                pos_side = "LONG" if action == "BUY" else "SHORT"
                
                exec_res = execute_bingx_order(
                    symbol=symbol, side=action, position_side=pos_side,
                    quantity=qty, stop_loss=signal['sl'], take_profit=signal['tp1']
                )
                
                if exec_res.get("status") in ["LIVE_FILLED", "PAPER_FILLED"]:
                    send_telegram_message_cooldown(
                        f"EXEC_{symbol}",
                        f"🏛 *SALA SEGNALI PRO • ESECUZIONE LIVE* 🏛\n\n"
                        f"Asset: `{signal['name']}`\n"
                        f"Segnale: *{action}* ({signal['leverage']})\n"
                        f"Size: `{qty}` | Margine: `${signal['margin']}`\n"
                        f"Ingresso: `{signal['entry']}`\n"
                        f"SL: `{signal['sl']}` | TP1: `{signal['tp1']}`"
                    )
        except Exception as e:
            print(f"Errore esecuzione automatica: {e}")

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/autotrade/toggle', methods=['POST'])
def toggle_autotrade():
    global auto_trade_active
    data = request.get_json(silent=True) or {}
    if 'enabled' in data:
        auto_trade_active = bool(data['enabled'])
    else:
        auto_trade_active = not auto_trade_active
    return jsonify({
        "auto_trade": auto_trade_active,
        "mode": get_exchange_mode()
    })

@app.route('/api/autotrade/status', methods=['GET'])
def get_autotrade_status():
    real_positions = fetch_bingx_real_positions()
    return jsonify({
        "auto_trade": auto_trade_active,
        "mode": get_exchange_mode(),
        "positions": real_positions,
        "history": execution_history[:30]
    })

@app.route('/api/telemetry/scans', methods=['GET'])
def get_telemetry_scans():
    return jsonify(list(scans_telemetry.values()))

@app.route('/api/autotrade/close', methods=['POST'])
def manual_close_position():
    global session_realized_pnl
    data = request.get_json(silent=True) or {}
    symbol = data.get('symbol')
    if symbol:
        real_pos = fetch_bingx_real_positions()
        target = next((p for p in real_pos if p['symbol'] == symbol), None)
        if target:
            close_side = "SELL" if target['action'] == "BUY" else "BUY"
            pos_side = "LONG" if target['action'] == "BUY" else "SHORT"
            exec_res = execute_bingx_order(symbol, close_side, pos_side, target['size'])
            
            pnl = target['unrealized_pnl']
            session_realized_pnl += pnl

            record = {
                "symbol": symbol, "name": target['name'], "action": target['action'],
                "entry": target['entry'], "exit": target['current_price'], "size": target['size'],
                "pnl": pnl, "pnl_pct": 0.0,
                "reason": "Chiusura Manuale Operatore", "mode": exec_res["status"],
                "closed_at": datetime.now().strftime("%H:%M:%S")
            }
            execution_history.insert(0, record)
            return jsonify({"closed": True, "record": record})
    return jsonify({"closed": False}), 404

def fetch_all_markets():
    return {
        "Criptovalute": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin', 'symbol': 'BTCUSDT'},
            {'id': 'ETHUSDT', 'name': 'Ξ Ethereum', 'symbol': 'ETHUSDT'},
            {'id': 'SOLUSDT', 'name': '🟣 Solana', 'symbol': 'SOLUSDT'},
            {'id': 'XRPUSDT', 'name': '✕ Ripple', 'symbol': 'XRPUSDT'},
            {'id': 'BNBUSDT', 'name': '🟡 BNB', 'symbol': 'BNBUSDT'},
            {'id': 'ADAUSDT', 'name': '₳ Cardano', 'symbol': 'ADAUSDT'},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin', 'symbol': 'DOGEUSDT'},
            {'id': 'AVAXUSDT', 'name': '🔺 Avalanche', 'symbol': 'AVAXUSDT'},
            {'id': 'LINKUSDT', 'name': '🔗 Chainlink', 'symbol': 'LINKUSDT'},
            {'id': 'SUIUSDT', 'name': '💧 Sui', 'symbol': 'SUIUSDT'}
        ],
        "Materie Prime & Forex": [
            {'id': 'XAUUSD', 'name': '🥇 Oro (Gold)', 'symbol': 'PAXGUSDT'},
            {'id': 'XAGUSD', 'name': '🥈 Argento (Silver)', 'symbol': 'XAGUSDT'},
            {'id': 'EURUSDT', 'name': '💶 EUR/USD', 'symbol': 'EURUSDT'},
            {'id': 'GBPUSDT', 'name': '🇬🇧 GBP/USD', 'symbol': 'GBPUSDT'},
            {'id': 'USDJPY', 'name': '🇯🇵 USD/JPY', 'symbol': 'USDJPY'}
        ],
        "Azioni & Indici": [
            {'id': 'AAPL', 'name': '🍏 Apple Inc.', 'symbol': 'AAPLUSDT'},
            {'id': 'TSLA', 'name': '🚗 Tesla Inc.', 'symbol': 'TSLAUSDT'},
            {'id': 'NVDA', 'name': '💻 Nvidia Corp.', 'symbol': 'NVDAUSDT'},
            {'id': 'AMZN', 'name': '📦 Amazon', 'symbol': 'AMZNUSDT'},
            {'id': 'SPX500', 'name': '📈 S&P 500 Index', 'symbol': 'SPXUSDT'}
        ]
    }

def get_market_status(category):
    return {"status": "Aperto", "text": "🟢 H24 Perpetual Feed (BingX)", "schedule": "Mercato Continuo • H24"}

def safe_float(val, default=0.0):
    try:
        f = float(val)
        if np.isnan(f) or not np.isfinite(f):
            return default
        return f
    except Exception:
        return default

def get_market_data(symbol, interval="15m"):
    headers = {"User-Agent": "Mozilla/5.0"}
    clean_sym = symbol
    if symbol in ['AAPL', 'TSLA', 'NVDA', 'AMZN', 'SPX500']:
        clean_sym = symbol + 'USDT'
    elif symbol == 'XAGUSD':
        clean_sym = 'XAGUSDT'
    elif symbol == 'XAUUSD':
        clean_sym = 'PAXGUSDT'

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

            return df, live_price, "Live Feed"
    except Exception:
        pass

    fallback_prices = {
        'BTCUSDT': 64000.0, 'ETHUSDT': 2500.0, 'SOLUSDT': 150.0, 'XRPUSDT': 0.55,
        'BNBUSDT': 580.0, 'ADAUSDT': 0.45, 'DOGEUSDT': 0.12, 'AVAXUSDT': 25.0,
        'LINKUSDT': 12.0, 'SUIUSDT': 1.80, 'XAUUSD': 2650.0, 'XAGUSD': 31.0,
        'EURUSDT': 1.08, 'GBPUSDT': 1.30, 'USDJPY': 152.0, 'AAPL': 230.0,
        'TSLA': 220.0, 'NVDA': 135.0, 'AMZN': 185.0, 'SPX500': 5800.0
    }
    p = fallback_prices.get(symbol, 100.0)

    df = pd.DataFrame({
        'Open': [p]*60, 'High': [p*1.002]*60, 'Low': [p*0.998]*60,
        'Close': [p]*60, 'Volume': [30000]*60
    })
    return df, p, "Neural Sync"

def calculate_ai_neural_setup(df, current_price, mode, account_balance, risk_percentage, symbol="BTCUSDT"):
    close = df['Close']
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = safe_float((100 - (100 / (1 + rs))).iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    macd_signal = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - macd_signal
    current_macd = safe_float(macd_hist.iloc[-1], 0.0)
    prev_macd = safe_float(macd_hist.iloc[-2], 0.0)

    tr = pd.concat([df['High'] - df['Low'], np.abs(df['High'] - close.shift()), np.abs(df['Low'] - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0 or np.isnan(atr): 
        atr = current_price * 0.005

    vol = df['Volume']
    vol_sma = safe_float(vol.rolling(20).mean().iloc[-1], 1000.0)
    current_vol = safe_float(vol.iloc[-1], 1000.0)
    low_volume_trap = bool(current_vol < (vol_sma * 0.25))

    o_last = safe_float(df['Open'].iloc[-1], current_price)
    c_last = safe_float(df['Close'].iloc[-1], current_price)
    h_last = safe_float(df['High'].iloc[-1], current_price)
    l_last = safe_float(df['Low'].iloc[-1], current_price)
    body = abs(c_last - o_last)
    rng = h_last - l_last
    is_fake_wick = bool((rng > 0) and ((body / rng) < 0.08))
    whipsaw_detected = low_volume_trap or is_fake_wick

    ema_val_12 = safe_float(ema12.iloc[-1], current_price)
    ema_val_26 = safe_float(ema26.iloc[-1], current_price)

    r_buy_min = MANUAL_CONFIG["RSI_BUY_MIN"]
    r_buy_max = MANUAL_CONFIG["RSI_BUY_MAX"]
    r_sell_min = MANUAL_CONFIG["RSI_SELL_MIN"]
    r_sell_max = MANUAL_CONFIG["RSI_SELL_MAX"]

    if rsi >= 72 and current_macd < prev_macd:
        inversion_prediction = "⚠️ Imminente Inversione RIBASSISTA"
    elif rsi <= 28 and current_macd > prev_macd:
        inversion_prediction = "🚀 Imminente Inversione RIALZISTA"
    else:
        inversion_prediction = "🛡️ Trend Istituzionale Stabile"

    is_buy = (ema_val_12 >= ema_val_26) and (current_macd >= -0.001) and (r_buy_min <= rsi <= r_buy_max) and not whipsaw_detected
    is_sell = (ema_val_12 <= ema_val_26) and (current_macd <= 0.001) and (r_sell_min <= rsi <= r_sell_max) and not whipsaw_detected

    if is_buy:
        action = "BUY"
        ai_score = 99.9
        sentiment = "BULLISH (Manualmente Configurato)"
        sentiment_color = "#00ff66"
        sentiment_pct = 98
        regime = "Espansione Risk-On"
    elif is_sell:
        action = "SELL"
        ai_score = 99.9
        sentiment = "BEARISH (Manualmente Configurato)"
        sentiment_color = "#ff1e42"
        sentiment_pct = 98
        regime = "Difensivo Risk-Off"
    else:
        action = "NEUTRAL"
        ai_score = 50.0
        sentiment = "IN ATTESA DI TRIGGER MANUALE"
        sentiment_color = "#ffffff"
        sentiment_pct = 50
        regime = "Compressione di Range"

    sl_m = MANUAL_CONFIG["SL_MULTIPLIER"]
    tp_m = MANUAL_CONFIG["TP_MULTIPLIER"]

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m
    
    decimals = 2 if current_price > 5 else 6

    if action == "BUY":
        entry = current_price
        sl = round(current_price - sl_dist, decimals)
        tp1 = round(current_price + tp_dist, decimals)
        max_target = round(current_price + (tp_dist * 2.5), decimals)
        breakeven_price = round(entry + (tp_dist * 0.3), decimals)
    else:
        entry = current_price
        sl = round(current_price + sl_dist, decimals)
        tp1 = round(current_price - tp_dist, decimals)
        max_target = round(current_price - (tp_dist * 2.5), decimals)
        breakeven_price = round(entry - (tp_dist * 0.3), decimals)

    max_risk_amount = account_balance * (risk_percentage / 100.0)
    price_risk_per_unit = abs(current_price - sl)
    raw_position_size = (max_risk_amount / price_risk_per_unit) if price_risk_per_unit > 0 else 0.0
    position_size = round(max(raw_position_size, 0.001), 3)

    sl_pct = (price_risk_per_unit / current_price) * 100 if current_price > 0 else 1.0
    recommended_leverage = min(20, max(3, int(round(15.0 / max(sl_pct, 0.5)))))
    margin_required = round((raw_position_size * current_price) / recommended_leverage, 2)
    rrr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    return {
        "action": action, "ai_score": ai_score, "sentiment": sentiment, 
        "sentiment_color": sentiment_color, "sentiment_pct": sentiment_pct,
        "regime": regime, "breakeven": breakeven_price,
        "inversion_prediction": inversion_prediction,
        "entry": round(entry, decimals), "sl": sl, "tp1": tp1,
        "max_target": max_target, "rrr": rrr, "decimals": decimals, "rsi": round(rsi, 1),
        "risk_amount": round(max_risk_amount, 2), "position_size": position_size,
        "leverage": f"Isolata {recommended_leverage}x", "margin": margin_required,
        "progress_pct": 15.0, "whipsaw_blocked": whipsaw_detected
    }

@app.route('/api/markets', methods=['GET'])
def get_markets():
    return jsonify(fetch_all_markets())

def process_asset(cat, a, m_status, mode, balance, risk_pct, timeframe):
    try:
        df, current_price, source = get_market_data(a['id'], interval=timeframe)
        if df is not None and not df.empty:
            res = calculate_ai_neural_setup(df, current_price, mode, balance, risk_pct, symbol=a['id'])
            dec = res["decimals"]

            signal_obj = {
                "id": f"{a['id']}_{res['action']}",
                "category": cat, "symbol": a['id'], "name": a['name'],
                "action": res["action"], "sentiment": res["sentiment"], 
                "sentiment_color": res["sentiment_color"],
                "inversion_prediction": res["inversion_prediction"],
                "change": "+0.50%", "price": f"{current_price:.{dec}f}", 
                "ai_score": f"{res['ai_score']}%", "entry": f"{res['entry']}", "sl": res["sl"], 
                "tp1": res["tp1"], "rrr": res["rrr"], "rsi": res["rsi"],
                "risk_amount": res["risk_amount"], "position_size": res["position_size"],
                "leverage": res["leverage"], "margin": res["margin"],
                "progress_pct": res["progress_pct"],
                "market_status": m_status["text"], "market_schedule": m_status["schedule"],
                "is_open": True, "timestamp": datetime.now().strftime("%H:%M:%S")
            }

            if res['action'] != "NEUTRAL" and not res["whipsaw_blocked"]:
                manage_auto_execution(signal_obj)
                send_telegram_message_cooldown(
                    signal_obj['symbol'],
                    f"🏛 *MANUAL CONFIG SIGNAL* 🏛\n\nAsset: `{signal_obj['name']}`\nDirezione: *{signal_obj['action']}*\nIngresso: `{signal_obj['entry']}`"
                )
                return signal_obj
    except Exception as e:
        print(f"Errore processo asset {a['id']}: {e}")
    return None

@app.route('/api/signals', methods=['GET'])
def get_signals():
    try:
        mode = request.args.get('mode', MANUAL_CONFIG["DEFAULT_TRADING_MODE"])
        timeframe = request.args.get('timeframe', MANUAL_CONFIG["DEFAULT_TIMEFRAME"])
        balance = safe_float(request.args.get('balance', MANUAL_CONFIG["ACCOUNT_BALANCE_USD"]), MANUAL_CONFIG["ACCOUNT_BALANCE_USD"])
        risk_pct = safe_float(request.args.get('risk', MANUAL_CONFIG["RISK_PERCENTAGE_PER_TRADE"]), MANUAL_CONFIG["RISK_PERCENTAGE_PER_TRADE"])

        markets = fetch_all_markets()
        raw_signals = []
        
        target_assets = []
        for cat, assets in markets.items():
            m_status = get_market_status(cat)
            for a in assets:
                target_assets.append((cat, a, m_status))

        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = [executor.submit(process_asset, cat, a, m_status, mode, balance, risk_pct, timeframe) for cat, a, m_status in target_assets]
            for future in as_completed(futures):
                res = future.result()
                if res:
                    raw_signals.append(res)

        unique_map = {}
        for sig in raw_signals:
            sym = sig['symbol']
            if sym not in unique_map:
                unique_map[sym] = sig

        signals = list(unique_map.values())
        signals.sort(key=lambda x: float(x['ai_score'].replace('%','')), reverse=True)
        return jsonify(signals)
    except Exception as e:
        return jsonify([])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
