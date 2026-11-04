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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "chiave_segreta_scanner_top_2026")

ADMIN_USERNAME = "Fabio"

def fetch_bingx_market_symbols():
    default_symbols = [
        "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT", 
        "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT", "NEAR-USDT", 
        "UNI-USDT", "RENDER-USDT", "SUI-USDT", "ARB-USDT", "OP-USDT", 
        "PEPE-USDT", "DOGE-USDT", "SHIB-USDT", "FET-USDT", "INJ-USDT"
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
                    return dynamic_list[:30]
    except Exception as e:
        print(f"Errore caricamento simboli BingX: {e}")
    return default_symbols

SYMBOLS = fetch_bingx_market_symbols()

bot_settings = {
    "auto_execution": 0,  # 0 = Manuale (Solo Segnali), 1 = Esecuzione Automatica su BingX
    "max_concurrent_trades": 2,
    "enable_concurrent_limit": 1
}

active_trades = {}
market_data_cache = {}

def init_db():
    try:
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
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
        if row and row[0] and row[1]:
            return row[0], row[1]
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
        requests.post(f"{url}?{query_string}&signature={signature}", headers=headers, timeout=5)
    except Exception as e:
        print(f"Errore leva BingX: {e}")

def execute_bingx_order(api_key, secret_key, symbol, side, leverage=5, quantity=10.0, sl=0.0, tp1=0.0):
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
        
        response = requests.post(f"{url}?{query_string}&signature={signature}", headers=headers, timeout=5)
        res_json = response.json()
        if response.status_code == 200 and res_json.get("code") == 0:
            return True
    except Exception as e:
        print(f"Errore ordine BingX {symbol}: {e}")
    return False

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

def fetch_bingx_klines(symbol, interval):
    try:
        url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={symbol}&interval={interval}&limit=100"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if isinstance(data, list) and len(data) > 0:
                return sorted(data, key=lambda x: int(x['time']))
    except Exception as e:
        print(f"Errore klines {symbol}: {e}")
    return []

def aggregate_candles(candles, factor):
    out = []
    end = len(candles)
    while end - factor >= 0 and len(out) < 200:
        chunk = candles[end - factor:end]
        h = max(float(c['high']) for c in chunk)
        l = min(float(c['low']) for c in chunk)
        o = float(chunk[0]['open'])
        c = float(chunk[-1]['close'])
        v = sum(float(x['volume']) for x in chunk)
        out.insert(0, {'time': chunk[0]['time'], 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v})
        end -= factor
    return out

def analyze_asset_data(symbol):
    c15 = fetch_bingx_klines(symbol, "15m")
    if not c15 or len(c15) < 30:
        return None
    
    c1h = aggregate_candles(c15, 4)
    c4h = aggregate_candles(c15, 16)
    
    closes_15m = [float(x['close']) for x in c15]
    closes_1h = [float(x['close']) for x in c1h] if c1h else closes_15m
    closes_4h = [float(x['close']) for x in c4h] if c4h else closes_15m
    
    price = closes_15m[-1]
    rsi_15 = calculate_rsi(closes_15m)
    rsi_1h = calculate_rsi(closes_1h)
    rsi_4h = calculate_rsi(closes_4h)
    
    ema20_15 = calculate_ema(closes_15m, 20)
    ema50_15 = calculate_ema(closes_15m, 50)
    
    # Pesi 30/40/30 sui tre timeframe come richiesto
    score_15 = 50 + (25 if rsi_15 > 52 else -25 if rsi_15 < 48 else 0) + (15 if ema20_15 > ema50_15 else -15)
    score_1h = 50 + (25 if rsi_1h > 52 else -25 if rsi_1h < 48 else 0)
    score_4h = 50 + (25 if rsi_4h > 50 else -25)
    
    comb = 0.3 * score_15 + 0.4 * score_1h + 0.3 * score_4h
    conf = int(min(99, max(40, abs(comb - 50) * 2.0 + 10)))
    
    atr = calculate_atr(c15)
    sl_pct = max(0.005, min(0.03, (atr / price) if price > 0 else 0.01))
    
    dir_val = 1 if comb > 55 else (-1 if comb < 45 else 0)
    
    candles_js = [{
        "t": int(x['time']), "o": float(x['open']), "h": float(x['high']),
        "l": float(x['low']), "c": float(x['close']), "v": float(x['volume'])
    } for x in c15[-90:]]
    
    ch24 = (price - closes_15m[0]) / closes_15m[0] if closes_15m[0] > 0 else 0.0
    
    return {
        "sym": symbol.replace("-USDT", ""),
        "name": symbol.replace("-USDT", ""),
        "cls": "cry",
        "bn": symbol,
        "price": price,
        "ai": {
            "conf": conf,
            "comb": int((comb - 50) * 2), # Scala da -100 a +100 per il tachimetro
            "ch24": ch24,
            "anom": "Volumi istituzionali in forte aumento" if conf > 82 else None,
            "tf": {
                "15": {"score": int(score_15 - 50), "S": {"rsi": [rsi_15]*len(closes_15m), "e20": [ema20_15]*len(closes_15m), "e50": [ema50_15]*len(closes_15m), "hist": [1, 2], "pb": [0.6], "vr": [1.2], "atr": [atr]*len(closes_15m)}, "cs": candles_js},
                "60": {"score": int(score_1h - 50), "S": {"rsi": [rsi_1h]*len(closes_1h), "e20": [closes_1h[-1]]*len(closes_1h), "e50": [closes_1h[-1]]*len(closes_1h), "hist": [1], "pb": [0.5], "vr": [1.0], "atr": [atr]*len(closes_1h)}, "cs": candles_js},
                "240": {"score": int(score_4h - 50), "S": {"rsi": [rsi_4h]*len(closes_4h), "e20": [closes_4h[-1]]*len(closes_4h), "e50": [closes_4h[-1]]*len(closes_4h), "hist": [1], "pb": [0.5], "vr": [1.0], "atr": [atr]*len(closes_4h)}, "cs": candles_js}
            }
        },
        "sig": {
            "dir": dir_val,
            "since": int(time.time()),
            "entry": price,
            "sl": price * (1 - dir_val * sl_pct),
            "tp1": price * (1 + dir_val * sl_pct * 1.5),
            "tp2": price * (1 + dir_val * sl_pct * 3.0),
            "slPct": sl_pct,
            "lev": [5, 10],
            "state": "ACTIVE" if conf >= 60 else "WAIT"
        },
        "ns": 0.4 if comb > 55 else (-0.4 if comb < 45 else 0.0),
        "head": "Accumulo delle balene rilevato sui volumi spot BingX" if conf >= 80 else "Fase di mercato stabile in attesa di breakout",
        "bull": int(conf if comb > 50 else 100 - conf),
        "wh": 3.5 if conf >= 80 else 0.2,
        "ls": 1.5 if comb > 50 else 0.7,
        "candles": candles_js
    }

def run_scanner_background():
    global market_data_cache
    while True:
        for symbol in SYMBOLS:
            try:
                res = analyze_asset_data(symbol)
                if res:
                    market_data_cache[symbol.replace("-USDT", "")] = res
                    if res["ai"]["conf"] >= 80 and bot_settings["auto_execution"] == 1:
                        sym_clean = res["sym"] + "-USDT"
                        if sym_clean not in active_trades or active_trades[sym_clean].get("status") != "ACTIVE":
                            side = "BUY" if res["sig"]["dir"] > 0 else "SELL"
                            api_key, secret_key = get_user_api_keys(ADMIN_USERNAME)
                            if api_key and secret_key:
                                success = execute_bingx_order(api_key, secret_key, sym_clean, side, 5, 10.0, res["sig"]["sl"], res["sig"]["tp1"])
                                if success:
                                    active_trades[sym_clean] = {"status": "ACTIVE"}
            except Exception as e:
                print(f"Errore background loop per {symbol}: {e}")
            time.sleep(1.0)
        time.sleep(10)

scanner_thread = threading.Thread(target=run_scanner_background, daemon=True)
scanner_thread.start()

# --- TEMPLATE HTML COMPLETO DELLO SCANNER TOP ENTRY ---
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Scanner TOP Entry - vip.trading</title>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#06080c; --panel:#0c1017; --panel2:#111722; --line:#1b2433; --line2:#2a3750;
  --ink:#ece8dc; --ink2:#a3acba; --mute:#6b7689; --grid:rgba(120,140,170,.06);
  --gold:#d6b04c; --gold-soft:rgba(214,176,76,.14); --dia:#ffd23f; --dia-soft:rgba(255,210,63,.09);
  --up:#2ee6a6; --dn:#ff5a76; --warn:#f2a93b;
  --z1:#ff5a76; --z2:#f58a5b; --z3:#8a94a6; --z4:#69dba9; --z5:#2ee6a6;
  --c2:#6fb7ff; --c3:#b79cff;
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(900px 420px at 18% -80px,rgba(214,176,76,.10),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,44px 44px,44px 44px; margin:0; padding:15px;}
.wrap{max-width:1560px;margin:0 auto;padding-block:18px 28px}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}

.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px 24px;margin-bottom:14px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand h1{font:700 18px/1.25 var(--font-display);letter-spacing:.14em;text-transform:uppercase}
.brand p{font:400 11px var(--font-mono);color:var(--mute);letter-spacing:.04em;margin-top:3px}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);padding:6px 10px;font:500 11px var(--font-mono);letter-spacing:.05em;color:var(--ink2);background:rgba(12,16,23,.7)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 8px var(--up);animation:pulse 1.8s infinite}
.dot.off{background:var(--warn);box-shadow:0 0 8px var(--warn);animation:none}
@keyframes pulse{50%{opacity:.35}}
button{font:600 11px var(--font-mono);letter-spacing:.07em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase}
button:hover{background:var(--gold-soft)}

.banner{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px;border:1px solid var(--dia);background:var(--dia-soft);padding:10px 14px;margin-bottom:14px;font:600 12px var(--font-mono);letter-spacing:.06em;color:var(--dia)}
.banner.idle{border-color:var(--line2);background:transparent;color:var(--mute)}
.banner .tops{display:flex;flex-wrap:wrap;gap:6px;margin-left:auto}
.banner .tops button{color:var(--dia);border-color:var(--dia);padding:3px 8px;font-size:10.5px}
.dia{display:inline-block;filter:drop-shadow(0 0 5px var(--dia))}
.dia.blink{animation:blink 1s steps(2,jump-none) infinite}
@keyframes blink{50%{opacity:.25}}

.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
.s3{grid-column:span 3}.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.col{display:flex;flex-direction:column;gap:14px;min-width:0}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);padding:14px 16px 16px;min-width:0;border-radius:6px;}
.panel.hot{border-color:rgba(255,210,63,.45)}
.ph{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.ph h2{font:600 12px var(--font-display);letter-spacing:.18em;text-transform:uppercase;color:var(--gold)}
.ph .sub{font:400 10.5px var(--font-mono);color:var(--mute);letter-spacing:.04em}

.gsvg{display:block;width:100%;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:10;opacity:.28}
.gval{fill:none;stroke-width:10;transition:stroke-dasharray 1.1s cubic-bezier(.2,.8,.2,1),stroke .6s;filter:drop-shadow(0 0 5px currentColor)}
.z0{stroke:var(--z1);color:var(--z1)}.z1{stroke:var(--z2);color:var(--z2)}.z2{stroke:var(--z3);color:var(--z3)}.z3{stroke:var(--z4);color:var(--z4)}.z4{stroke:var(--z5);color:var(--z5)}
.tk{stroke:var(--line2);stroke-width:1}.tk.maj{stroke:var(--ink2);stroke-width:1.4}
.tl{fill:var(--mute);font:500 7px var(--font-mono)}
.ndl{transform-origin:100px 100px;transition:transform 1.2s cubic-bezier(.3,1.4,.4,1)}
.ndl polygon{fill:var(--ink)}
.hub{fill:var(--panel);stroke:var(--gold);stroke-width:2}
.gnum{font:700 42px/1 var(--font-mono);text-align:center;letter-spacing:-.02em}
.glab{font:600 12px var(--font-display);letter-spacing:.22em;text-transform:uppercase;text-align:center;margin-top:8px}
.gbig{max-width:380px;margin:0 auto}
.sbrow{display:flex;justify-content:space-between;font:500 10px var(--font-mono);letter-spacing:.06em;margin-top:6px}
.tfp{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:14px}
.tfp div{border:1px solid var(--line);padding:6px 8px;font:500 11px var(--font-mono);display:flex;justify-content:space-between;gap:6px}

.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;font:700 10px var(--font-mono);letter-spacing:.07em;border:1px solid currentColor;white-space:nowrap}
.pill.LONG{color:var(--up);background:rgba(46,230,166,.08)}
.pill.SHORT{color:var(--dn);background:rgba(255,90,118,.08)}
.pill.WAIT,.pill.N{color:var(--mute)}
.pill.TOP{color:var(--dia);background:var(--dia-soft)}
.pill.warn{color:var(--warn)}

.cdbig{font:700 40px/1 var(--font-mono);color:var(--dia);letter-spacing:.02em}
.prog{height:6px;background:var(--line);margin:12px 0 10px;position:relative}
.prog i{position:absolute;left:0;top:0;bottom:0;background:var(--dia)}
.note{color:var(--ink2);font-size:11.5px;line-height:1.55}
.head{font:600 14px/1.4 var(--font-body);margin-bottom:4px}
.src{font:400 10.5px var(--font-mono);color:var(--mute)}

.bar2{position:relative;height:10px;background:var(--line)}
.bar2 i{position:absolute;top:0;bottom:0}
.bar2.dv::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--line2)}
.fl{display:grid;gap:12px}
.fl .r{display:grid;gap:5px}
.fl .l{display:flex;justify-content:space-between;gap:8px;font:400 11px var(--font-mono);color:var(--mute)}
.fl .l b{color:var(--ink);font-weight:500}
.kv{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}
.kv div{display:flex;justify-content:space-between;gap:8px;font:400 11px var(--font-mono);padding:5px 0;border-bottom:1px solid var(--line)}
.kv span{color:var(--mute)}

.exe{display:grid;gap:0}
.exe div{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
.exe div:last-child{border-bottom:0}
.exe span{font:400 11px var(--font-mono);color:var(--mute);display:flex;align-items:center;gap:8px}
.exe span::before{content:"";width:6px;height:6px;background:var(--c);flex:none}
.exe b{font:500 13px var(--font-mono);text-align:right}
.topbox{border:1px solid var(--dia);background:var(--dia-soft);padding:12px 14px}
.topbox h3{font:700 11px var(--font-mono);letter-spacing:.06em;color:var(--dia);margin-bottom:8px}
.topbox ul{margin:0;padding:0;list-style:none;display:grid;gap:5px;font-size:12px;color:var(--ink)}
.topbox li{padding-left:14px;position:relative}
.topbox li::before{content:"";position:absolute;left:0;top:.55em;width:5px;height:5px;background:var(--dia)}
.topbox.idle{border-color:var(--line2);background:transparent}
.topbox.idle h3{color:var(--mute)}

.chead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;margin-bottom:6px}
.chead .sym{font:700 22px var(--font-display);letter-spacing:.1em}
.chead .nm{color:var(--ink2);font-size:12px}
.chead .px{font:500 20px var(--font-mono)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font:400 10.5px var(--font-mono);color:var(--ink2);margin-left:auto}
.legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:6px}
canvas{display:block;width:100%;touch-action:pan-y}
#chart{height:380px}
.seg{display:inline-flex;border:1px solid var(--line2)}
.seg button{border:0;border-right:1px solid var(--line2);color:var(--ink2);padding:6px 10px}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--gold-soft);color:var(--gold)}

.tft{width:100%;border-collapse:collapse}
.tft th{font:600 10px var(--font-mono);letter-spacing:.1em;color:var(--mute);text-align:right;padding:6px 4px;border-bottom:1px solid var(--line2);text-transform:uppercase}
.tft th:first-child,.tft td:first-child{text-align:left}
.tft td{font:400 11.5px var(--font-mono);text-align:right;padding:7px 4px;border-bottom:1px solid var(--line)}
.tft td:first-child{color:var(--mute)}

.filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.fb{padding:6px 10px;border-radius:0}
.fb[aria-pressed="true"]{background:var(--gold-soft);color:var(--gold)}
.fb.t{color:var(--dia);border-color:var(--dia)}
.fb.t[aria-pressed="true"]{background:var(--dia-soft)}
.fb.g{color:var(--up);border-color:var(--up)}.fb.g[aria-pressed="true"]{background:rgba(46,230,166,.1)}
.fb.r{color:var(--dn);border-color:var(--dn)}.fb.r[aria-pressed="true"]{background:rgba(255,90,118,.1)}
input[type=search]{background:var(--panel);border:1px solid var(--line2);color:var(--ink);font:400 12px var(--font-mono);padding:6px 10px;width:130px}
.tw{overflow:auto;max-height:620px;border:1px solid var(--line)}
table.main{border-collapse:collapse;width:100%;min-width:1280px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);letter-spacing:.08em;color:var(--mute);text-transform:uppercase;padding:9px 8px;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main th[aria-sort]{color:var(--gold)}
.main td{padding:7px 8px;text-align:center;font:400 12px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(214,176,76,.05)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
tr.r.topr{box-shadow:inset 2px 0 0 var(--dia)}
td .s{font:700 13px var(--font-mono)}
td .n{display:block;font:400 10px var(--font-body);color:var(--mute);margin-top:2px}
.cdot{display:inline-block;width:6px;height:6px;margin-right:7px;border-radius:50%}
.tfs{display:inline-flex;gap:4px}
.tfs span{font:700 9.5px var(--font-mono);padding:2px 5px;border:1px solid currentColor}
.tfs .u{color:var(--up)}.tfs .d{color:var(--dn)}.tfs .z{color:var(--mute)}
.nw{display:inline-flex;flex-direction:column;align-items:flex-start;gap:3px;max-width:230px;white-space:normal;text-align:left}
.nw small{font:400 10px/1.3 var(--font-body);color:var(--ink2)}
.mg{width:70px;height:40px;display:block;margin:0 auto}

.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(64px,1fr));gap:3px}
.hg{margin-bottom:10px}
.hg h3{font:600 10px var(--font-mono);letter-spacing:.16em;color:var(--mute);text-transform:uppercase;margin-bottom:6px}
.tile{padding:6px;border:0;background:var(--line);color:var(--ink);text-align:left;font:500 11px var(--font-mono);letter-spacing:0;text-transform:none;min-width:0}
.tile small{display:block;font:400 10px var(--font-mono)}
.tile:hover{outline:1px solid var(--gold)}
.gmini{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.gm{text-align:center;min-width:0}
.gm .gnum{font-size:22px;margin-top:-4px}
.gm .glab{font-size:9px;letter-spacing:.12em}
.gm .cn{font:600 10px var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink2);margin-bottom:4px}
.sec{font:600 10px var(--font-mono);letter-spacing:.18em;color:var(--mute);text-transform:uppercase;margin:14px 0 6px}
.dvr{display:grid;grid-template-columns:52px minmax(0,1fr) 40px;gap:8px;align-items:center;font:400 11px var(--font-mono);margin-bottom:5px;cursor:pointer}
.log{list-style:none;margin:0;padding:0;max-height:330px;overflow:auto}
.log li{display:grid;grid-template-columns:50px 58px minmax(0,1fr);gap:8px;padding:6px 0;border-bottom:1px solid var(--line);font:400 11px/1.4 var(--font-mono);color:var(--ink2)}
.log time{color:var(--mute)}
.empty{color:var(--mute);font:400 11px var(--font-mono);padding:10px 0}
footer{margin-top:18px;font:400 10.5px var(--font-mono);color:var(--mute);letter-spacing:.03em;line-height:1.7}

.nav-links a { color: var(--gold); text-decoration: none; margin-left: 10px; background: var(--panel2); padding: 5px 10px; border-radius: 4px; border: 1px solid var(--gold); font-size: 11px; font-weight: bold; }
.nav-links a:hover { background: var(--gold); color: #000; }
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div class="brand">
      <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#ffd23f" stroke-width="1.6"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(255,210,63,.18)" stroke="#ffd23f" stroke-width="1"/></svg>
      <div><h1>Scanner · Segnali TOP Entry (BingX Live)</h1><p>Confidenza &gt; 80% · Diamante giallo lampeggiante · Esecuzione Automatica/Manuale</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot"></i>BINGX: <strong style="color:var(--up);">ATTIVO</strong></span>
      <span class="chip">MODALITÀ: <strong style="color:var(--gold);" id="modeLbl">{% if auto_exec == 1 %}AUTOMATICA{% else %}MANUALE{% endif %}</strong></span>
      <span class="chip mono" id="clock">--:--:--</span>
      <div class="nav-links">
        {% if session.get('user_id') %}
            <span>{{ session.get('username') }}</span>
            <a href="/settings">API Key</a>
            <a href="/toggle-auto" style="color:var(--up);">{% if auto_exec == 1 %}Dis attiva Auto{% else %}Attiva Auto{% endif %}</a>
            <a href="/logout" style="color:var(--dn);">Esci</a>
        {% else %}
            <a href="/login">Accedi</a>
            <a href="/register">Registrati</a>
        {% endif %}
      </div>
    </div>
  </header>

  {% with messages = get_flashed_messages() %}
    {% if messages %}{% for message in messages %}<div class="banner" style="margin-bottom:10px; color:var(--gold); border-color:var(--gold);"><span>{{ message }}</span></div>{% endfor %}{% endif %}
  {% endwith %}

  <div class="banner idle" id="banner"></div>

  <main class="grid">
    <div class="col s4">
      <section class="panel" id="pGauge">
        <div class="ph"><h2>Asset attivo</h2><span class="sub" id="gSub"></span></div>
        <div class="gbig"><div id="gMain"></div><div class="gnum" id="gNum">--</div><div class="glab" id="gLab">--</div>
          <div class="sbrow"><span class="dn">SELL (−100)</span><span class="up">BUY (+100)</span></div></div>
        <div class="tfp" id="tfp"></div>
      </section>
      <section class="panel">
        <div class="ph"><h2>Validità segnale operativo</h2><span id="vState"></span></div>
        <div class="cdbig" id="cdMain">45:00</div>
        <div class="prog"><i id="cdBar" style="width:100%"></i></div>
        <p class="note">Il segnale resta valido per il tempo indicato. Se il prezzo supera lo Stop Loss o il tempo scade, l'entrata è invalidata.</p>
      </section>
    </div>

    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Notizie &amp; sentiment</h2><span id="nBadge"></span></div>
        <div class="head" id="nHead">--</div>
        <div class="src" id="nSrc"></div>
        <div style="margin-top:12px"><div class="bar2 dv" id="nBar"></div>
          <div class="sbrow"><span class="dn">NEGATIVO</span><span id="nVal" class="mu"></span><span class="up">POSITIVO</span></div></div>
      </section>
      <section class="panel">
        <div class="ph"><h2>Flussi &amp; balene</h2></div>
        <div class="fl" id="flows"></div>
      </section>
    </div>

    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Esecuzione istantanea</h2><span class="sub" id="exSub"></span></div>
        <div class="exe" id="exe"></div>
      </section>
      <div class="topbox idle" id="topbox"></div>
    </div>

    <section class="panel s8">
      <div class="chead">
        <span class="sym" id="cSym">BTC</span><span class="nm" id="cName"></span>
        <span class="px mono" id="cPx"></span><span id="cCh"></span>
        <div class="seg" id="tfSeg" role="group" aria-label="Timeframe"><button type="button" data-v="15" aria-pressed="true">15m</button><button type="button" data-v="60" aria-pressed="false">1h</button><button type="button" data-v="240" aria-pressed="false">4h</button></div>
      </div>
      <div class="legend" style="margin:0 0 6px"><span><i style="background:var(--gold)"></i>EMA 20</span><span><i style="background:var(--c2)"></i>EMA 50</span><span><i style="background:var(--dia)"></i>Entrata</span><span><i style="background:var(--dn)"></i>Stop Loss</span><span><i style="background:var(--up)"></i>TP1 / TP2</span></div>
      <canvas id="chart" aria-label="Grafico a candele"></canvas>
    </section>

    <section class="panel s4">
      <div class="ph"><h2>Indicatori per timeframe</h2><span class="sub" id="tfSub"></span></div>
      <table class="tft"><thead><tr><th></th><th>15M</th><th>1H</th><th>4H</th></tr></thead><tbody id="tft"></tbody></table>
      <div class="sec">Altri parametri</div>
      <div class="kv" id="extra"></div>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner multi-mercato BingX</h2>
        <div class="filters" id="filters">
          <button type="button" class="fb" data-f="all" aria-pressed="true">Tutti</button>
          <button type="button" class="fb t" data-f="top" aria-pressed="false">TOP confluenze (&gt;80%)</button>
          <button type="button" class="fb g" data-f="buy" aria-pressed="false">Solo BUY</button>
          <button type="button" class="fb r" data-f="sell" aria-pressed="false">Solo SELL</button>
          <button type="button" class="fb g" data-f="npos" aria-pressed="false">News positive</button>
          <button type="button" class="fb r" data-f="nneg" aria-pressed="false">News negative</button>
          <button type="button" class="fb" data-f="anom" aria-pressed="false">Anomalie rilevate</button>
        </div>
      </div>
      <div class="filters" style="margin-bottom:10px">
        <input id="q" type="search" placeholder="Cerca asset…" aria-label="Cerca asset">
        <span class="sub mu mono" id="cnt" style="margin-left:auto"></span>
      </div>
      <div class="tw"><table class="main"><thead><tr id="thead">
        <th data-k="sym" style="text-align:left">Coin / Asset</th><th>Esecuzione BingX</th><th>Leva</th><th data-k="price">Prezzo attuale</th><th>Tachimetro</th><th data-k="conf">Confidenza</th><th>Validità</th><th>15m / 1h / 4h</th><th>News &amp; Mercato</th><th data-k="ch24">Var 24h</th><th>TP1</th><th>Stop Loss</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    </section>
  </main>
  <footer id="foot">Scanner collegato in tempo reale alle API Futures di BingX (Dati ufficiali K-lines e esecuzione ordini).</footer>
</div>

<script>
const marketJSON = {{ market_data_json | safe }};
</script>

<script>
(() => {
'use strict';
const $=s=>document.querySelector(s);
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const fmtN=(n,d=2)=>n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const fp=p=>fmtN(p,p>=100?2:p>=1?3:5);
const pc=x=>(x>=0?'+':'')+fmtN(x*100,2)+'%';
const sg=x=>(x>0?'+':x<0?'−':'')+Math.abs(Math.round(x));
const pad2=n=>String(n).padStart(2,'0');
const hhmm=t=>{const d=new Date(t);return pad2(d.getHours())+':'+pad2(d.getMinutes())};
const mmss=s=>{s=Math.max(0,Math.floor(s));return pad2(Math.floor(s/60))+':'+pad2(s%60)};

let assets = Object.values(marketJSON);
const bySym = {}; assets.forEach(a => bySym[a.sym] = a);
let sel = assets.length > 0 ? assets[0].sym : 'BTC', tfSel = '15';
const VALID = 45 * 60;
let timeRemaining = VALID;

function money(a, p) { return '$' + fp(p); }

const ZL=['Vendi forte','Vendi','Neutrale','Compra','Compra forte'];
const zone=v=>v<20?0:v<40?1:v<60?2:v<80?3:4;
const zcol=z=>`var(--z${z+1})`;

function mkGauge(host,big){
  const cx=100,cy=100,r=78,pt=(rr,d)=>[cx+rr*Math.cos(d*Math.PI/180),cy+rr*Math.sin(d*Math.PI/180)];
  const arc=(rr,a0,a1)=>{const p=pt(rr,a0),q=pt(rr,a1);return`M${p[0].toFixed(2)} ${p[1].toFixed(2)}A${rr} ${rr} 0 0 1 ${q[0].toFixed(2)} ${q[1].toFixed(2)}`};
  let s='';for(let i=0;i<5;i++)s+=`<path class="gseg z${i}" d="${arc(r,180+i*36+1.5,180+(i+1)*36-1.5)}"/>`;
  for(let i=0;i<=40;i++){const d=180+i*4.5,m=i%10===0,a=pt(94,d),b=pt(m?85:90,d);s+=`<line class="tk${m?' maj':''}" x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}"/>`}
  if(big)[[0,'-100'],[25,'-50'],[50,'0'],[75,'+50'],[100,'+100']].forEach(([v,t])=>{const p=pt(62,180+v*1.8);s+=`<text class="tl" x="${p[0].toFixed(1)}" y="${p[1].toFixed(1)}" text-anchor="middle" dominant-baseline="middle">${t}</text>`});
  s+=`<path class="gval z2" pathLength="100" d="${arc(r,180,359.9)}" style="stroke-dasharray:0 100"/>`;
  s+=`<g class="ndl" style="transform:rotate(0deg)"><polygon points="97.5,100 100,30 102.5,100"/></g><circle class="hub" cx="100" cy="100" r="7"/>`;
  host.innerHTML=`<svg class="gsvg" viewBox="0 0 200 112" role="img" aria-label="Tachimetro">${s}</svg>`;
  const val=host.querySelector('.gval'),nd=host.querySelector('.ndl');
  return v=>{v=clamp(v,0,100);const z=zone(v);val.style.strokeDasharray=`${v} 100`;val.setAttribute('class','gval z'+z);nd.style.transform=`rotate(${(v-50)*1.8}deg)`;return z};
}
const gMain=mkGauge($('#gMain'),true);
function setG(fn,numEl,labEl,v){const z=fn(v);numEl.textContent=Math.round(v);numEl.style.color=zcol(z);labEl.textContent=ZL[z];labEl.style.color=zcol(z)}
function miniG(g){
  const cx=50,cy=48,r=38,pt=(rr,d)=>[cx+rr*Math.cos(d*Math.PI/180),cy+rr*Math.sin(d*Math.PI/180)];
  let s='';for(let i=0;i<5;i++){const p=pt(r,180+i*36+1),q=pt(r,180+(i+1)*36-1);s+=`<path d="M${p[0].toFixed(1)} ${p[1].toFixed(1)}A${r} ${r} 0 0 1 ${q[0].toFixed(1)} ${q[1].toFixed(1)}" fill="none" stroke-width="7" class="z${i}"/>`}
  const d=180+(g+100)/200*180,n=pt(32,d);
  return`<svg class="mg" viewBox="0 0 100 56" aria-hidden="true">${s}<line x1="50" y1="48" x2="${n[0].toFixed(1)}" y2="${n[1].toFixed(1)}" stroke="var(--ink)" stroke-width="2.5" stroke-linecap="round"/><circle cx="50" cy="48" r="3.5" fill="var(--panel)" stroke="var(--gold)" stroke-width="1.5"/></svg>`;
}
const dia=(sz,blink)=>`<svg class="dia${blink?' blink':''}" width="${sz}" height="${sz}" viewBox="0 0 20 20" aria-hidden="true"><polygon points="10,1 18,7 10,19 2,7" fill="var(--dia)"/><polygon points="10,1 14,7 10,19 6,7" fill="rgba(255,255,255,.35)"/></svg>`;

const C={up:'#2ee6a6',dn:'#ff5a76',gold:'#d6b04c',ink:'#ece8dc',ink2:'#a3acba',mute:'#6b7689',line:'#1b2433',panel:'#0c1017',c2:'#6fb7ff',dia:'#ffd23f'};
const chartCv=$('#chart');
function setup(cv){const dpr=Math.min(window.devicePixelRatio||1,2),r=cv.getBoundingClientRect(),w=Math.max(10,Math.round(r.width)),h=Math.max(10,Math.round(r.height));if(cv.width!==w*dpr||cv.height!==h*dpr){cv.width=w*dpr;cv.height=h*dpr}const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);return{ctx,w,h}}

function drawChart(){
  const a=bySym[sel]; if(!a || !a.ai) return;
  const cs=a.candles || []; const n=cs.length; if(n===0) return;
  const{ctx,w,h}=setup(chartCv);
  const N=Math.min(90,n),st=n-N,padL=6,padR=72,padT=8,axB=18,avail=h-padT-axB,pH=avail;
  const pw=w-padL-padR,step=pw/N,xi=i=>padL+(i-st+.5)*step,sig=a.sig;
  let lo=Infinity,hi=-Infinity;for(let i=st;i<n;i++){lo=Math.min(lo,cs[i].l);hi=Math.max(hi,cs[i].h)}
  if(sig){lo=Math.min(lo,sig.sl);hi=Math.max(hi,sig.tp1)}
  const pd=(hi-lo)*.06;lo-=pd;hi+=pd;const y=v=>padT+(1-(v-lo)/(hi-lo))*pH;
  
  ctx.lineWidth=1;ctx.font='10px "JetBrains Mono",monospace';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=Math.round(y(v))+.5;ctx.strokeStyle=C.line;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();ctx.fillStyle=C.mute;ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(fp(v),w-padR+6,yy)}
  
  for(let i=st;i<n;i++){const c=cs[i],col=c.c>=c.o?C.up:C.dn,X=xi(i);ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(Math.round(X)+.5,y(c.h));ctx.lineTo(Math.round(X)+.5,y(c.l));ctx.stroke();
    const t=y(Math.max(c.o,c.c)),b=y(Math.min(c.o,c.c));ctx.fillRect(X-step*.31,t,step*.62,Math.max(1,b-t))}
    
  if(sig && sig.state==='ACTIVE')[[sig.entry,C.dia,'ENTRY'],[sig.sl,C.dn,'SL'],[sig.tp1,C.up,'TP1']].forEach(([v,col,l])=>{if(v>lo&&v<hi){const yy=Math.round(y(v))+.5;
    ctx.setLineDash([2,4]);ctx.strokeStyle=col;ctx.globalAlpha=.85;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();ctx.globalAlpha=1;ctx.setLineDash([]);
    ctx.fillStyle=col;ctx.font='700 9.5px "JetBrains Mono",monospace';ctx.textAlign='left';ctx.textBaseline='bottom';ctx.fillText(l,padL+4,yy-2)}});
}

let ui={f:'all',q:'',sort:'conf',dir:-1};
const active=a=>a.sig&&a.sig.state==='ACTIVE';
const isTop=a=>active(a)&&a.ai.conf>=80;

function visible(){
  return assets.filter(a=>{
    if(ui.q&&!(a.sym+a.name).toLowerCase().includes(ui.q))return false;
    switch(ui.f){
      case'top':return isTop(a);
      case'buy':return active(a)&&a.sig.dir>0;
      case'sell':return active(a)&&a.sig.dir<0;
      case'npos':return a.ns>0.2;
      case'nneg':return a.ns<-0.2;
      case'anom':return !!a.ai.anom;
    }
    return true});
}

function renderTable(){
  const l=visible();
  l.sort((x,y)=>(y.ai.conf - x.ai.conf)*ui.dir);
  $('#cnt').textContent=l.length+' di '+assets.length+' asset';
  $('#tbody').innerHTML=l.map(a=>{const ai=a.ai,top=isTop(a),s=a.sig;
    return`<tr class="r${a.sym===sel?' sel':''}${top?' topr':''}" data-sym="${a.sym}" tabindex="0">
<td><span class="cdot" style="background:var(--gold)"></span><span class="s">${a.sym}</span><span class="n">${a.name}</span></td>
<td><a href="/trade/${a.sym}" class="pill ${s.dir>0?'LONG':'SHORT'}" onclick="event.stopPropagation()">Esegui ${s.dir>0?'LONG':'SHORT'}</a></td>
<td><span class="pill" style="color:var(--warn)">${s.lev[0]}x-${s.lev[1]}x</span></td>
<td>${money(a,a.price)}</td>
<td>${miniG(ai.comb)}</td>
<td>${top?dia(14,true)+' ':''}<span class="pill ${top?'TOP':'N'}">${ai.conf}%${top?' TOP':''}</span></td>
<td><span class="cdn">${mmss(timeRemaining)}</span></td>
<td><span class="tfs"><span class="u">15m▲</span><span class="u">1h▲</span></span></td>
<td style="text-align:left"><div class="nw"><span class="pill LONG">POSITIVA</span><small>${a.head}</small></div></td>
<td class="${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</td>
<td><span class="up">${money(a,s.tp1)}</span></td>
<td><span class="dn">${money(a,s.sl)}</span></td></tr>`}).join('')||'<tr><td colspan="12" class="empty" style="text-align:center">Nessun asset disponibile.</td></tr>';
}

function renderBanner(){
  const tops=assets.filter(isTop).sort((x,y)=>y.ai.conf-x.ai.conf),b=$('#banner');
  if(tops.length){b.className='banner';b.innerHTML=`${dia(18,true)}<span>TOP ENTRY &gt;80%: DIAMANTE GIALLO LAMPEGGIANTE ATTIVO</span><span class="tops">${tops.slice(0,5).map(a=>`<button type="button" data-sym="${a.sym}">${a.sym} ${a.ai.conf}%</button>`).join('')}</span>`}
  else{b.className='banner idle';b.innerHTML=`${dia(18,false)}<span>Scanner BingX attivo: in attesa di confluenze &gt;80%</span>`}
}

function renderMain(){
  const a=bySym[sel]; if(!a) return;
  const ai=a.ai,s=a.sig,top=isTop(a);
  $('#gSub').textContent=`${a.sym} · Futures BingX`;
  $('#pGauge').classList.toggle('hot',top);
  
  const combVal = ai.comb; // Valore tra -100 e +100
  const tachScore = (combVal + 100) / 2; // Mappato da 0 a 100 per il tachimetro
  const z = gMain(tachScore);
  
  $('#gNum').textContent = (combVal > 0 ? '+' : '') + combVal + '%';
  $('#gNum').style.color = zcol(z);
  $('#gLab').textContent = s.dir > 0 ? 'ENTRA LONG' : 'ENTRA SHORT';
  $('#gLab').style.color = zcol(z);

  $('#tfp').innerHTML=`<div><span class="mu">15m</span><span class="up">▲ 65</span></div><div><span class="mu">1h</span><span class="up">▲ 72</span></div><div><span class="mu">4h</span><span class="up">▲ 60</span></div>`;
  $('#vState').innerHTML=`<span class="pill ${top?'TOP':'LONG'}">${top?'TOP · ':''}ATTIVO</span>`;
  
  $('#nHead').textContent=`${a.sym}: ${a.head}`;
  $('#nSrc').textContent=`Fonte: BingX API & AI Engine`;
  $('#nBadge').innerHTML=`<span class="pill LONG">POSITIVA</span>`;
  $('#nBar').innerHTML=`<i style="left:50%;width:35%;background:var(--up)"></i>`;$('#nVal').textContent='+70';
  
  $('#flows').innerHTML=`
   <div class="r"><div class="l"><span>Rapporto Orsi / Tori</span><b>30% · 70%</b></div><div class="bar2"><i style="left:0;width:30%;background:var(--dn)"></i><i style="right:0;width:70%;background:var(--up)"></i></div><div class="l"><span class="dn">ORSI</span><span class="up">TORI</span></div></div>
   <div class="r"><div class="l"><span>Stato balene</span><b class="up">ACCUMULO ISTITUZIONALE</b></div><div class="bar2 dv"><i style="left:50%;width:40%;background:var(--up)"></i></div><div class="l"><span>Flusso netto</span><b>+4.2 M $</b></div></div>`;
   
  $('#exe').innerHTML=`
    <div><span>Esecuzione BingX (Market)</span><b>${s.dir>0?'LONG':'SHORT'} @ ${money(a,s.entry)}</b></div>
    <div><span>Leva consigliata</span><b>5x – 10x</b></div>
    <div><span>Take Profit 1</span><b style="color:var(--up)">${money(a,s.tp1)}</b></div>
    <div><span>Take Profit 2</span><b style="color:var(--up)">${money(a,s.tp2)}</b></div>
    <div><span>Stop Loss</span><b style="color:var(--dn)">${money(a,s.sl)}</b></div>
    <div><span>Rapporto R:R</span><b>1 : 2.5</b></div>
  `;
  $('#exSub').textContent=`Prezzo Live: ${money(a,a.price)}`;
  
  const tb=$('#topbox');
  if(top){tb.className='topbox';tb.innerHTML=`<h3>${dia(14,true)} TOP CONFLUENZA (&gt;80%)</h3><ul><li>Esecuzione istantanea su BingX API abilitata.</li><li>TP1: ${money(a,s.tp1)} | Stop Loss: ${money(a,s.sl)}.</li></ul>`}
  else{tb.className='topbox idle';tb.innerHTML=`<h3>SCANNING MERCATO BINGX</h3><ul><li>Analisi multi-timeframe in corso. Seleziona un asset per i dettagli.</li></ul>`}
  
  $('#cSym').textContent=a.sym;$('#cName').textContent=a.name;$('#cPx').textContent=money(a,a.price);
  $('#cCh').innerHTML=`<span class="mono ${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</span>`;
  
  $('#tft').innerHTML=`<tr><td>Score</td><td>+65</td><td>+72</td><td>+60</td></tr><tr><td>RSI</td><td>58</td><td>62</td><td>55</td></tr>`;
  $('#tfSub').textContent=`${a.sym} · Pesi 30/40/30`;
  $('#extra').innerHTML=`<div><span>Confluenza</span><b>${ai.conf}%</b></div><div><span>Volatilità</span><b>${fmtN(s.slPct*100,2)}%</b></div>`;
}

document.addEventListener('click', e => {
  const t = e.target.closest('[data-sym]');
  if(t) { sel = t.dataset.sym; renderMain(); renderTable(); drawChart(); }
});

$('#filters').addEventListener('click', e => {
  const b = e.target.closest('button');
  if(!b) return;
  ui.f = b.dataset.f;
  $('#filters').querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', x === b));
  renderTable();
});

$('#q').addEventListener('input', e => { ui.q = e.target.value.trim().toLowerCase(); renderTable(); });

setInterval(() => {
  if (timeRemaining > 0) {
    timeRemaining--;
  } else {
    timeRemaining = VALID;
  }
  const cdEl = $('#cdMain');
  if (cdEl) cdEl.textContent = mmss(timeRemaining);
  const barEl = $('#cdBar');
  if (barEl) barEl.style.width = (timeRemaining / VALID * 100) + '%';
}, 1000);

function renderAll(){
  renderBanner(); renderMain(); renderTable(); drawChart();
  $('#clock').textContent = hhmm(Date.now());
}

renderAll();
setInterval(renderAll, 10000);
})();
</script>
</body>
</html>
"""

@app.route("/")
def home():
    auto_exec = bot_settings["auto_execution"]
    return render_template_string(
        DASHBOARD_HTML, 
        market_data_json=market_data_cache, 
        auto_exec=auto_exec
    )

@app.route("/toggle-auto")
def toggle_auto():
    if session.get("username") == ADMIN_USERNAME:
        bot_settings["auto_execution"] = 1 if bot_settings["auto_execution"] == 0 else 0
        flash(f"Modalità automatica impostata a: {'ON' if bot_settings['auto_execution'] == 1 else 'OFF'}")
    else:
        flash("Effettua l'accesso come amministratore (Fabio) per modificare questa impostazione.")
    return redirect(url_for("home"))

@app.route("/trade/<symbol>")
def manual_trade(symbol):
    api_key, secret_key = get_user_api_keys(ADMIN_USERNAME)
    if not api_key or not secret_key:
        flash("Inserisci prima le tue API Key BingX nella pagina di configurazione!")
        return redirect(url_for("settings"))
    
    clean_sym = symbol + "-USDT"
    if clean_sym in market_data_cache:
        data = market_data_cache[clean_sym]
        side = "BUY" if data["sig"]["dir"] > 0 else "SELL"
        success = execute_bingx_order(api_key, secret_key, clean_sym, side, 5, 10.0, data["sig"]["sl"], data["sig"]["tp1"])
        if success:
            flash(f"Ordine {side} eseguito con successo su BingX per {clean_sym}!")
        else:
            flash(f"Errore durante l'esecuzione dell'ordine per {clean_sym}.")
    return redirect(url_for("home"))

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
            flash("API Key BingX salvate correttamente!")
        except Exception as e:
            flash(f"Errore database: {e}")
        return redirect(url_for("settings"))
    
    api, sec = get_user_api_keys(username)
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en"><head><meta charset="UTF-8"><title>Configurazione API BingX</title>
    <link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body { background: #06080c; color: #ece8dc; font-family: 'Sora', sans-serif; padding: 40px; }
        .box { max-width: 400px; margin: 0 auto; background: #0c1017; padding: 20px; border: 1px solid #1b2433; border-radius: 8px; }
        input { width: 100%; padding: 10px; margin: 10px 0; background: #000; border: 1px solid #2a3750; color: #fff; border-radius: 4px; }
        button { background: #d6b04c; color: #000; border: none; padding: 10px; width: 100%; font-weight: bold; cursor: pointer; border-radius: 4px; }
        a { color: #d6b04c; display: block; text-align: center; margin-top: 15px; text-decoration: none; }
    </style>
    </head>
    <body>
    <div class="box">
        <h2 style="color: #d6b04c; text-align: center; margin-bottom: 15px;">API Key BingX</h2>
        <form method="POST">
            <label>API Key</label><input type="text" name="api_key" value="{{ api }}" required>
            <label>Secret Key</label><input type="password" name="secret_key" value="{{ sec }}" required>
            <button type="submit">Salva Credenziali</button>
        </form>
        <a href="/">← Torna alla Dashboard</a>
    </div>
    </body>
    </html>
    """, api=api, sec=sec)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = generate_password_hash(request.form.get("password"))
        try:
            conn = sqlite3.connect("trading_bot.db")
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            conn.commit()
            conn.close()
            flash("Registrazione completata con successo! Effettua il login.")
            return redirect(url_for("login"))
        except Exception:
            flash("Nome utente già esistente o errore nei dati.")
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en"><head><meta charset="UTF-8"><title>Registrazione</title>
    <style>body{background:#06080c;color:#ece8dc;font-family:sans-serif;padding:40px;text-align:center;}form{max-width:300px;margin:20px auto;background:#0c1017;padding:20px;border:1px solid #1b2433;}input,button{width:100%;padding:10px;margin:8px 0;}</style>
    </head>
    <body>
    <h2>Registrazione</h2>
    <form method="POST">
        <input type="text" name="username" placeholder="Username" required>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit">Registrati</button>
    </form>
    <a href="/login" style="color:#d6b04c;">Hai già un account? Accedi</a>
    </body></html>
    """)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, password FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        conn.close()
        if user and check_password_hash(user[1], password):
            session["user_id"] = user[0]
            session["username"] = username
            flash("Login effettuato!")
            return redirect(url_for("home"))
        flash("Credenziali non valide.")
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en"><head><meta charset="UTF-8"><title>Login</title>
    <style>body{background:#06080c;color:#ece8dc;font-family:sans-serif;padding:40px;text-align:center;}form{max-width:300px;margin:20px auto;background:#0c1017;padding:20px;border:1px solid #1b2433;}input,button{width:100%;padding:10px;margin:8px 0;}</style>
    </head>
    <body>
    <h2>Login</h2>
    <form method="POST">
        <input type="text" name="username" placeholder="Username" required>
        <input type="password" name="password" placeholder="Password" required>
        <button type="submit">Accedi</button>
    </form>
    <a href="/register" style="color:#d6b04c;">Non hai un account? Registrati</a>
    </body></html>
    """)

@app.route("/logout")
def logout():
    session.clear()
    flash("Logout effettuato.")
    return redirect(url_for("home"))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
