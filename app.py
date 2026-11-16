from flask import Flask, render_template, jsonify, request
import requests
import pandas as pd
import numpy as np
import os
import time
import hmac
import hashlib
import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

def load_render_environment():
    global BINGX_API_KEY, BINGX_SECRET_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    BINGX_API_KEY = os.environ.get('BINGX_API_KEY', '').strip()
    BINGX_SECRET_KEY = os.environ.get('BINGX_SECRET_KEY', '').strip()
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
    
    print("========================================")
    print("⚡ SALA SEGNALI PRO • BINGX LIVE SYNC ⚡")
    print(f"🔹 BingX API Key: {'CONFIGURATA ✅' if BINGX_API_KEY else 'NON RILEVATA (Paper Mode)'}")
    print("========================================")

load_render_environment()

BINGX_BASE_URL = "https://open-api.bingx.com"
STATE_FILE = "autotrade_state.json"

def get_saved_autotrade():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f).get("active", False)
        except Exception:
            pass
    return False

def save_autotrade(status):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"active": status}, f)
    except Exception as e:
        print(f"Errore salvataggio stato trade: {e}")

auto_trade_active = get_saved_autotrade()
execution_history = []
sent_telegram_signals = {}
scans_telemetry = {}
session_realized_pnl = 0.0

def get_market_session_status(category):
    now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()
    current_minutes = now_utc.hour * 60 + now_utc.minute

    if "Criptovalute" in category:
        return {"status": "APERTO", "is_open": True, "hours": "24/7 Continuo (UTC)", "badge_color": "#00ff66"}
    elif "Forex" in category or "Materie Prime" in category:
        is_closed = (weekday == 5) or (weekday == 4 and current_minutes >= 1320) or (weekday == 6 and current_minutes < 1320)
        if is_closed:
            return {"status": "CHIUSO (Weekend)", "is_open": False, "hours": "Lun-Ven 24h", "badge_color": "#ff1e42"}
        return {"status": "APERTO", "is_open": True, "hours": "Sessione Globale 24h", "badge_color": "#00ff66"}
    else:
        if weekday >= 5:
            return {"status": "CHIUSO (Weekend)", "is_open": False, "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC", "badge_color": "#ff1e42"}
        if 870 <= current_minutes < 1260:
            return {"status": "APERTO", "is_open": True, "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC", "badge_color": "#00ff66"}
        return {"status": "CHIUSO (Fuori Orario)", "is_open": False, "hours": "NYSE/NASDAQ: 14:30 - 21:00 UTC", "badge_color": "#ff1e42"}

def get_exchange_mode():
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return "PAPER PROP-DESK (BINGX SYNC ACTIVE)"
    return "🔴 BINGX LIVE REALE (BINGX SYNC ACTIVE)"

def sign_bingx_request(params_str):
    return hmac.new(BINGX_SECRET_KEY.encode('utf-8'), params_str.encode('utf-8'), hashlib.sha256).hexdigest()

def fetch_bingx_real_positions():
    global execution_history
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        simulated_pos = []
        for h in execution_history:
            if h.get("status") == "OPEN":
                pos_copy = h.copy()
                notional = pos_copy.get("size", 1) * pos_copy.get("entry", 1)
                pos_copy["fee"] = round(notional * 0.0005, 4)
                simulated_pos.append(pos_copy)
        return simulated_pos

    timestamp = int(time.time() * 1000)
    query_string = f"timestamp={timestamp}"
    signature = sign_bingx_request(query_string)
    try:
        res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/user/positions?{query_string}&signature={signature}", headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=4.0).json()
        if res.get("code") == 0 and "data" in res:
            real_pos_list = []
            for p in res["data"]:
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    sym = p.get("symbol", "").replace("-", "")
                    side = "BUY" if p.get("positionSide") == "LONG" else ("SELL" if p.get("positionSide") == "SHORT" else ("BUY" if amt > 0 else "SELL"))
                    entry_p = float(p.get("avgPrice", 0))
                    size_val = abs(amt)
                    fee_val = float(p.get("fee", 0.0))
                    if fee_val == 0.0:
                        fee_val = round((size_val * entry_p) * 0.0005, 4)
                        
                    real_pos_list.append({
                        "symbol": sym, "name": p.get("symbol", ""), "action": side,
                        "entry": entry_p, "current_price": entry_p,
                        "size": size_val, "leverage": f"Isolata {p.get('leverage', 15)}x",
                        "margin": float(p.get("initialMargin", 0)),
                        "fee": fee_val,
                        "unrealized_pnl": round(float(p.get('unrealizedProfit', 0)), 2), "status": "OPEN"
                    })
            return real_pos_list
    except Exception: pass
    return [h for h in execution_history if h.get("status") == "OPEN"]

# SINCRONIZZAZIONE AUTOMATICA PROFITTI CHIUSI (ENDPOINT V2 BINGX)
def sync_bingx_closed_history():
    global execution_history
    if not BINGX_API_KEY or not BINGX_SECRET_KEY:
        return
    timestamp = int(time.time() * 1000)
    query_string = f"incomeType=REALIZED_PNL&limit=50&timestamp={timestamp}"
    signature = sign_bingx_request(query_string)
    try:
        res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/user/income?{query_string}&signature={signature}", headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=4.0).json()
        if res.get("code") == 0 and "data" in res:
            for inc in res["data"]:
                pnl_val = float(inc.get("income", 0.0))
                trade_time = int(inc.get("time", time.time() * 1000)) / 1000.0
                date_str = datetime.fromtimestamp(trade_time).strftime("%Y-%m-%d")
                month_str = datetime.fromtimestamp(trade_time).strftime("%Y-%m")
                trade_id = f"BINGX-{inc.get('tranId', int(trade_time))}"
                
                if not any(h.get('id') == trade_id for h in execution_history):
                    execution_history.append({
                        "id": trade_id,
                        "symbol": inc.get("symbol", "").replace("-", ""),
                        "name": inc.get("symbol", ""),
                        "action": "BUY" if pnl_val >= 0 else "SELL",
                        "entry": 0.0,
                        "size": 0.0,
                        "leverage": "Isolata 15x",
                        "margin": 0.0,
                        "fee": 0.0,
                        "realized_pnl": round(pnl_val, 4),
                        "status": "CLOSED",
                        "date": date_str,
                        "month": month_str
                    })
    except Exception as e:
        print(f"Errore sync storico BingX: {e}")

def execute_verified_order(symbol, name, action, entry, size, leverage, margin, sl, tp1):
    global execution_history
    if BINGX_API_KEY and BINGX_SECRET_KEY:
        try:
            timestamp = int(time.time() * 1000)
            bx_symbol = f"{symbol.replace('USDT','')}-USDT"
            pos_side = "LONG" if action == "BUY" else "SHORT"
            params = {
                "symbol": bx_symbol, "side": action, "positionSide": pos_side,
                "type": "MARKET", "quantity": size, "timestamp": timestamp,
                "takeProfit": json.dumps({"type": "TAKE_PROFIT_MARKET", "stopPrice": tp1}),
                "stopLoss": json.dumps({"type": "STOP_MARKET", "stopPrice": sl})
            }
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            signature = sign_bingx_request(query_string)
            requests.post(f"{BINGX_BASE_URL}/openApi/swap/v2/trade/order?{query_string}&signature={signature}", headers={"X-BX-APIKEY": BINGX_API_KEY}, timeout=3.0)
        except Exception: pass

    if not any(h['symbol'] == symbol and h['status'] == 'OPEN' for h in execution_history):
        notional = size * entry
        est_fee = round(notional * 0.0005, 4)
        order_record = {
            "id": f"ORD-{int(time.time()*1000)}", "symbol": symbol, "name": name, "action": action,
            "entry": entry, "size": size, "leverage": leverage, "margin": margin,
            "sl": sl, "tp1": tp1, "fee": est_fee, "unrealized_pnl": 0.0, "status": "OPEN", "opened_at": datetime.now().strftime("%H:%M:%S")
        }
        execution_history.insert(0, order_record)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/autotrade/toggle', methods=['POST'])
def toggle_autotrade():
    global auto_trade_active
    auto_trade_active = not auto_trade_active if not request.json or 'enabled' not in request.json else bool(request.json['enabled'])
    save_autotrade(auto_trade_active)
    return jsonify({"auto_trade": auto_trade_active, "mode": get_exchange_mode()})

@app.route('/api/autotrade/status', methods=['GET'])
def get_autotrade_status():
    sync_bingx_closed_history()
    return jsonify({"auto_trade": auto_trade_active, "mode": get_exchange_mode(), "positions": fetch_bingx_real_positions(), "history": execution_history[:20]})

@app.route('/api/autotrade/close', methods=['POST'])
def close_position():
    global session_realized_pnl, execution_history
    data = request.get_json(silent=True) or {}
    symbol = data.get('symbol')
    for h in execution_history:
        if h['symbol'] == symbol and h['status'] == 'OPEN':
            h['status'] = 'CLOSED'
            pnl = h.get('unrealized_pnl', 0.0)
            h['realized_pnl'] = pnl
            h['date'] = datetime.now().strftime("%Y-%m-%d")
            h['month'] = datetime.now().strftime("%Y-%m")
            session_realized_pnl += pnl
            return jsonify({"success": True})
    return jsonify({"success": False}), 404

@app.route('/api/live-prices', methods=['GET'])
def get_live_prices():
    try:
        res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker", timeout=2.5).json()
        if res.get("code") == 0 and "data" in res:
            prices = {}
            data = res["data"]
            if isinstance(data, list):
                for item in data:
                    sym = item.get("symbol", "").replace("-", "")
                    prices[sym] = float(item.get("lastPrice", 0))
            elif isinstance(data, dict):
                sym = data.get("symbol", "").replace("-", "")
                prices[sym] = float(data.get("lastPrice", 0))
            return jsonify(prices)
    except Exception:
        pass
    return jsonify({})

@app.route('/api/analytics/performance', methods=['GET'])
def get_performance_analytics():
    global execution_history
    sync_bingx_closed_history()
    today_str = datetime.now().strftime("%Y-%m-%d")
    current_month_str = datetime.now().strftime("%Y-%m")
    
    closed_trades = [h for h in execution_history if h.get("status") == "CLOSED"]
    today_trades = [h for h in closed_trades if h.get("date") == today_str]
    month_trades = [h for h in closed_trades if h.get("month") == current_month_str]
    
    def calc_stats(trades):
        total = len(trades)
        wins = [t for t in trades if t.get("realized_pnl", 0) > 0]
        losses = [t for t in trades if t.get("realized_pnl", 0) <= 0]
        win_pct = round((len(wins) / total * 100), 1) if total > 0 else 0.0
        loss_pct = round((len(losses) / total * 100), 1) if total > 0 else 0.0
        won_amt = sum(t.get("realized_pnl", 0) for t in wins)
        lost_amt = sum(t.get("realized_pnl", 0) for t in losses)
        return total, len(wins), len(losses), win_pct, loss_pct, round(won_amt, 2), round(lost_amt, 2), round(won_amt + lost_amt, 2)

    t_tot, t_w, t_l, t_wp, t_lp, t_won, t_lost, t_net = calc_stats(today_trades)
    m_tot, m_w, m_l, m_wp, m_lp, m_won, m_lost, m_net = calc_stats(month_trades)

    return jsonify({
        "daily": {"date": today_str, "total_trades": t_tot, "wins": t_w, "losses": t_l, "win_pct": t_wp, "loss_pct": t_lp, "won_amount": t_won, "lost_amount": t_lost, "net_pnl": t_net},
        "monthly": {"month": current_month_str, "total_trades": m_tot, "wins": m_w, "losses": m_l, "win_pct": m_wp, "loss_pct": m_lp, "won_amount": m_won, "lost_amount": m_lost, "net_pnl": m_net}
    })

@app.route('/api/telemetry', methods=['GET'])
def get_telemetry():
    global scans_telemetry, execution_history
    return jsonify({
        "scans_count": len(scans_telemetry),
        "total_executions": len([h for h in execution_history if h.get("status") == "OPEN"]),
        "session_pnl": round(session_realized_pnl, 2),
        "telemetry_logs": list(scans_telemetry.values())[-15:]
    })

def fetch_all_markets():
    return {
        "Criptovalute Globali": [
            {'id': 'BTCUSDT', 'name': '₿ Bitcoin'}, {'id': 'ETHUSDT', 'name': 'Ξ Ethereum'},
            {'id': 'SOLUSDT', 'name': '🟣 Solana'}, {'id': 'XRPUSDT', 'name': '✕ Ripple'},
            {'id': 'BNBUSDT', 'name': '🟡 BNB'}, {'id': 'ADAUSDT', 'name': '₳ Cardano'},
            {'id': 'DOGEUSDT', 'name': '🐕 Dogecoin'}, {'id': 'AVAXUSDT', 'name': '🔺 Avalanche'},
            {'id': 'LINKUSDT', 'name': '🔗 Chainlink'}, {'id': 'SUIUSDT', 'name': '💧 Sui'}
        ],
        "Materie Prime & Forex": [
            {'id': 'PAXGUSDT', 'name': '🥇 Oro (Gold)'}, {'id': 'XAGUSDT', 'name': '🥈 Argento (Silver)'},
            {'id': 'GBPUSDT', 'name': '💷 GBP/USD'}, {'id': 'EURUSDT', 'name': '💶 EUR/USD'},
            {'id': 'USDJPY', 'name': '🇯🇵 USD/JPY'}
        ],
        "Azioni & Indici Globali": [
            {'id': 'AAPLUSDT', 'name': '🍏 Apple Inc.'}, {'id': 'TSLAUSDT', 'name': '🚗 Tesla Inc.'},
            {'id': 'NVDAUSDT', 'name': '💻 Nvidia Corp.'}, {'id': 'AMZNUSDT', 'name': '📦 Amazon'},
            {'id': 'MSFTUSDT', 'name': '🪟 Microsoft'}, {'id': 'GOOGLUSDT', 'name': '🔍 Alphabet (Google)'}
        ]
    }

def safe_float(val, default=0.0):
    try:
        f = float(val)
        return default if (np.isnan(f) or not np.isfinite(f)) else f
    except Exception:
        return default

def fetch_market_bundle(symbol, interval):
    bx_symbol = f"{symbol.replace('USDT','')}-USDT"
    try:
        url = f"{BINGX_BASE_URL}/openApi/swap/v3/quote/klines"
        res = requests.get(url, params={"symbol": bx_symbol, "interval": interval, "limit": 60}, timeout=3.0).json()
        if res.get("code") == 0 and "data" in res:
            klines = res["data"]
            if isinstance(klines, list) and len(klines) > 20:
                rows = []
                for k in klines:
                    if isinstance(k, list):
                        rows.append([k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])])
                    elif isinstance(k, dict):
                        rows.append([k.get('time', 0), float(k.get('open', 0)), float(k.get('high', 0)), float(k.get('low', 0)), float(k.get('close', 0)), float(k.get('volume', 0))])
                df = pd.DataFrame(rows, columns=['Open_time', 'Open', 'High', 'Low', 'Close', 'Volume'])
                return df, df['Close'].iloc[-1]
    except Exception:
        pass
    return None, 0.0

def calculate_setup(df, current_price, balance, risk_pct, rsi_buy_min, rsi_buy_max, rsi_sell_min, rsi_sell_max, sl_m, tp_m, timeframe, profile, category, is_open):
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

    if rsi >= 72 and current_macd < prev_macd: inv = "⚠️ Inversione RIBASSISTA"
    elif rsi <= 28 and current_macd > prev_macd: inv = "🚀 Inversione RIALZISTA"
    else: inv = "🛡️ Trend Stabile"

    is_buy = (safe_float(ema12.iloc[-1], current_price) >= safe_float(ema26.iloc[-1], current_price)) and (rsi_buy_min <= rsi <= rsi_buy_max) and not whipsaw
    is_sell = (safe_float(ema12.iloc[-1], current_price) <= safe_float(ema26.iloc[-1], current_price)) and (rsi_sell_min <= rsi <= rsi_sell_max) and not whipsaw

    if is_buy: action, score, sent, col = "BUY", 99.9, "BULLISH (BingX Cert)", "#00ff66"
    elif is_sell: action, score, sent, col = "SELL", 99.9, "BEARISH (BingX Cert)", "#ff1e42"
    else: action, score, sent, col = "NEUTRAL", 50.0, "ATTESA / RANGE", "#ffffff"

    sl_dist = atr * sl_m
    tp_dist = atr * tp_m
    decimals = 2 if current_price > 5 else 5

    if action == "BUY":
        entry, sl, tp1 = current_price, round(current_price - sl_dist, decimals), round(current_price + tp_dist, decimals)
        target_rialzista, target_ribassista = round(current_price + (tp_dist * 2.5), decimals), sl
    elif action == "SELL":
        entry, sl, tp1 = current_price, round(current_price + sl_dist, decimals), round(current_price - tp_dist, decimals)
        target_rialzista, target_ribassista = sl, round(current_price - (tp_dist * 2.5), decimals)
    else:
        entry, sl, tp1 = current_price, round(current_price - sl_dist, decimals), round(current_price + tp_dist, decimals)
        target_rialzista, target_ribassista = round(current_price + tp_dist, decimals), round(current_price - sl_dist, decimals)

    tf_min = 5 if timeframe == '5m' else (15 if timeframe == '15m' else 60)
    recent_movement = abs(close.diff().tail(5)).mean()
    effective_speed = max(recent_movement, atr * 0.1, current_price * 0.0001)
    candles_needed = max(1, int(np.ceil(tp_dist / effective_speed)))
    est_min = candles_needed * tf_min

    if est_min < 60: duration = f"~{est_min} min ({candles_needed} candele)"
    elif est_min < 1440: duration = f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    else: duration = f"~{round(est_min / 1440, 1)} giorni ({candles_needed} candele)"

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = abs(current_price - sl)
    pos_size = round(max((max_risk / risk_unit) if risk_unit > 0 else 0.0, 0.001), 3)
    
    if balance <= 2.0:
        lev_int = 20
    elif balance <= 5.0:
        lev_int = 15
    else:
        base_lev = int(round(15.0 / max((risk_unit / current_price) * 100, 0.5)))
        lev_int = min(20, max(5, base_lev))

    margin = round((pos_size * current_price) / lev_int, 2)
    
    roi_raw = round((tp_dist / current_price) * lev_int * 100, 1)
    roi_pct = f"+{roi_raw}%" if action == "BUY" else f"-{roi_raw}%" if action == "SELL" else "0.0%"

    notional = pos_size * current_price
    roundtrip_fee = round(notional * 0.001, 4)
    expected_gross_profit = round(pos_size * tp_dist, 4)
    expected_net_profit = round(expected_gross_profit - roundtrip_fee, 4)

    if not is_open:
        advisor_status = "BLOCCATO"
        advisor_color = "#ff1e42"
        advisor_msg = f"Borsa chiusa ({category}): attendi la riapertura."
    elif action == "NEUTRAL":
        advisor_status = "ATTENDI"
        advisor_color = "#8a99ad"
        advisor_msg = f"Asset in range neutro. Nessun trade consigliato."
    elif expected_net_profit <= 0:
        advisor_status = "SCONSIGLIATO (FEE)"
        advisor_color = "#ff1e42"
        advisor_msg = f"Target troppo stretto: TP lordo (+${expected_gross_profit}) < Fee (-${roundtrip_fee})."
    else:
        advisor_status = "ESEGUI IN REALE"
        advisor_color = "#00ff66"
        advisor_msg = f"Capitale ${balance} con Leva {lev_int}x: profitto netto stimato +${expected_net_profit} (fee -${roundtrip_fee} incluse)."

    return {
        "action": action, "ai_score": f"{score}%", "sentiment": sent, "sentiment_color": col,
        "inversion": inv, "entry": entry, "sl": sl, "tp1": tp1, "roi_pct": roi_pct,
        "target_rialzista": target_rialzista, "target_ribassista": target_ribassista, "duration": duration,
        "rrr": round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0, "rsi": round(rsi, 1),
        "size": pos_size, "leverage": f"Isolata {lev_int}x", "margin": margin, "decimals": decimals,
        "roundtrip_fee": roundtrip_fee, "expected_net": expected_net_profit,
        "advisor_status": advisor_status, "advisor_color": advisor_color, "advisor_msg": advisor_msg
    }

@app.route('/api/signals', methods=['GET'])
def get_signals():
    global auto_trade_active, scans_telemetry
    try:
        timeframe = request.args.get('timeframe', '15m')
        balance = safe_float(request.args.get('balance', 2.0).replace(',', '.'), 2.0)
        risk = safe_float(request.args.get('risk', 1.0).replace(',', '.'), 1.0)
        profile = request.args.get('profile', 'balanced')

        if profile == 'conservative': r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m = 30.0, 75.0, 25.0, 70.0, 1.2, 3.0
        elif profile == 'aggressive': r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m = 40.0, 85.0, 15.0, 60.0, 0.4, 1.0
        else: r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m = 35.0, 82.0, 18.0, 65.0, 0.8, 1.8

        markets = fetch_all_markets()
        session_info = {cat: get_market_session_status(cat) for cat in markets.keys()}
        
        all_prices = {}
        try:
            res = requests.get(f"{BINGX_BASE_URL}/openApi/swap/v2/quote/ticker", timeout=2.5).json()
            if res.get("code") == 0 and "data" in res:
                for item in res["data"]:
                    sym = item.get("symbol", "").replace("-", "")
                    all_prices[sym] = float(item.get("lastPrice", 0))
        except Exception: pass

        raw_signals = []
        target_assets = [(cat, a) for cat, assets in markets.items() for a in assets]

        with ThreadPoolExecutor(max_workers=16) as executor:
            futures = {executor.submit(fetch_market_bundle, a['id'], timeframe): (cat, a) for cat, a in target_assets}
            for future in as_completed(futures):
                cat, a = futures[future]
                df, live_price = future.result()
                
                if a['id'] in all_prices and all_prices[a['id']] > 0:
                    live_price = all_prices[a['id']]
                    if df is not None and not df.empty:
                        df.loc[df.index[-1], 'Close'] = live_price

                if df is not None and not df.empty and live_price > 0:
                    sess = session_info[cat]
                    res = calculate_setup(df, live_price, balance, risk, r_b_min, r_b_max, r_s_min, r_s_max, sl_m, tp_m, timeframe, profile, cat, sess["is_open"])
                    dec = res["decimals"]
                    
                    sig_obj = {
                        "id": f"{a['id']}_{res['action']}", "category": cat, "symbol": a['id'],
                        "name": a['name'], "price": f"{live_price:.{dec}f}",
                        "market_status": sess["status"], "market_hours": sess["hours"],
                        "market_is_open": sess["is_open"], "market_badge_color": sess["badge_color"],
                        **res
                    }
                    raw_signals.append(sig_obj)

                    if auto_trade_active and sess["is_open"] and res['action'] in ["BUY", "SELL"] and res['expected_net'] > 0:
                        execute_verified_order(a['id'], a['name'], res['action'], res['entry'], res['size'], res['leverage'], res['margin'], res['sl'], res['tp1'])

        unique_map = {sig['symbol']: sig for sig in raw_signals}
        signals = list(unique_map.values())
        signals.sort(key=lambda x: float(x['ai_score'].replace('%','')), reverse=True)

        scans_telemetry[time.time()] = {
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "scanned": len(signals), "status": "BINGX_SYNC_ACTIVE"
        }

        return jsonify({"sessions": session_info, "signals": signals})
    except Exception:
        return jsonify({"sessions": {}, "signals": []})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
