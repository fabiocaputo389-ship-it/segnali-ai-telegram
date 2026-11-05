import os
import time
import hmac
import hashlib
import requests
import sqlite3
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_default_molto_sicura')

bot_settings = {
    "bingx_api_key": os.environ.get("BINGX_API_KEY", ""),
    "bingx_secret_key": os.environ.get("BINGX_SECRET_KEY", ""),
    "trading_mode": os.environ.get("TRADING_MODE", "paper"),
    "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
    "chat_id": os.environ.get("CHAT_ID", ""),
    "auto_execution": int(os.environ.get("AUTO_EXECUTION", 0))
}

def init_db():
    conn = sqlite3.connect('trading_bot.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_bingx_signature(secret_key, payload):
    return hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

def send_telegram_alert(token, chat_id, message):
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def get_bingx_account_balance(api_key, secret_key):
    if not api_key or not secret_key:
        return {"balance": 0.0, "equity": 0.0, "error": "API Keys mancanti"}
    url = "https://open-api.bingx.com/openApi/swap/v2/user/balance"
    timestamp = str(int(time.time() * 1000))
    params = f"timestamp={timestamp}"
    signature = get_bingx_signature(secret_key, params)
    headers = {"X-BX-APIKEY": api_key}
    full_url = f"{url}?{params}&signature={signature}"
    try:
        response = requests.get(full_url, headers=headers, timeout=5)
        data = response.json()
        if data.get("code") == 0:
            balance_info = data.get("data", {}).get("balance", {})
            return {
                "balance": float(balance_info.get("balance", 0.0)),
                "equity": float(balance_info.get("equity", 0.0))
            }
        else:
            return {"balance": 0.0, "equity": 0.0, "error": data.get("msg", "Errore API BingX")}
    except Exception as e:
        return {"balance": 0.0, "equity": 0.0, "error": str(e)}

def place_bingx_order(api_key, secret_key, symbol, side, position_side, quantity):
    url = "https://open-api.bingx.com/openApi/swap/v2/trade/order"
    timestamp = str(int(time.time() * 1000))
    query_string = f"quantity={quantity}&side={side}&positionSide={position_side}&symbol={symbol}&timestamp={timestamp}&type=MARKET"
    signature = get_bingx_signature(secret_key, query_string)
    headers = {"X-BX-APIKEY": api_key}
    full_url = f"{url}?{query_string}&signature={signature}"
    try:
        response = requests.post(full_url, headers=headers, timeout=5)
        res_data = response.json()
        print(f"Risposta ordine BingX REALE per {symbol}: {res_data}")
        return res_data
    except Exception as e:
        print(f"Errore esecuzione ordine BingX: {e}")
        return {"code": -1, "msg": str(e)}

CRYPTO_FUTURES_ASSETS = [
    ("BTC", "Bitcoin", "BTC-USDT"),
    ("ETH", "Ethereum", "ETH-USDT"),
    ("SOL", "Solana", "SOL-USDT"),
    ("BNB", "BNB", "BNB-USDT"),
    ("XRP", "XRP", "XRP-USDT"),
    ("ADA", "Cardano", "ADA-USDT"),
    ("DOGE", "Dogecoin", "DOGE-USDT"),
    ("AVAX", "Avalanche", "AVAX-USDT"),
    ("LINK", "Chainlink", "LINK-USDT"),
    ("DOT", "Polkadot", "DOT-USDT"),
    ("LTC", "Litecoin", "LTC-USDT"),
    ("TON", "Toncoin", "TON-USDT"),
    ("SUI", "Sui", "SUI-USDT"),
    ("NEAR", "NEAR", "NEAR-USDT"),
    ("APT", "Aptos", "APT-USDT"),
    ("ARB", "Arbitrum", "ARB-USDT")
]

# Modulo IA per il calcolo della precisione e dei livelli di rischio
def ai_predictive_scoring(closes, highs, lows, volumes):
    if len(closes) < 50:
        return {"score": 50, "conf": 60, "dir": 1, "atr": closes[-1]*0.01}
    
    current_price = closes[-1]
    ema20 = sum(closes[-20:]) / 20
    ema50 = sum(closes[-50:]) / 50
    
    # Analisi dei volumi (Media mobile volumi)
    vol_sma = sum(volumes[-10:]) / 10
    vol_spike = volumes[-1] / (vol_sma if vol_sma > 0 else 1)
    
    # Calcolo ATR (Average True Range) per la volatilità
    tr_list = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]
    atr = sum(tr_list[-14:]) / 14 if len(tr_list) >= 14 else (current_price * 0.01)
    
    # Punteggioeuristico guidato da IA
    trend_score = 40 if current_price > ema20 and ema20 > ema50 else -40
    momentum_score = 30 if (closes[-1] - closes[-5]) > 0 else -30
    volume_score = 20 if vol_spike > 1.3 else 5
    
    total_score = trend_score + momentum_score + volume_score
    confidence = int(min(98, max(55, 60 + abs(total_score) * 0.45 + (vol_spike * 5))))
    direction = 1 if total_score >= 0 else -1
    
    return {
        "score": total_score,
        "conf": confidence,
        "dir": direction,
        "atr": atr
    }

def background_bingx_scanner():
    last_alerts = {}
    while True:
        try:
            token = bot_settings.get("telegram_token")
            chat_id = bot_settings.get("chat_id")
            auto_exec = bot_settings.get("auto_execution", 0)
            trading_mode = bot_settings.get("trading_mode", "paper")
            api_key = bot_settings.get("bingx_api_key")
            secret_key = bot_settings.get("bingx_secret_key")
            
            for sym, name, bx_sym in CRYPTO_FUTURES_ASSETS:
                url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={bx_sym}&interval=15m&limit=60"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    res_json = res.json()
                    if res_json.get("code") == 0:
                        klines = res_json.get("data", [])
                        if len(klines) >= 50:
                            closes = [float(k["close"]) for k in klines]
                            highs = [float(k["high"]) for k in klines]
                            lows = [float(k["low"]) for k in klines]
                            volumes = [float(k["volume"]) for k in klines]
                            
                            ai_res = ai_predictive_scoring(closes, highs, lows, volumes)
                            current_price = closes[-1]
                            
                            if ai_res["conf"] >= 80:
                                now = time.time()
                                if sym not in last_alerts or now - last_alerts[sym] > 1800:
                                    dir_txt = "LONG 🟢" if ai_res["dir"] > 0 else "SHORT 🔴"
                                    side_mult = ai_res["dir"]
                                    atr = ai_res["atr"]
                                    
                                    # Livelli precisi con 3 Take Profit calcolati via IA
                                    sl_val = current_price - (side_mult * 1.4 * atr)
                                    tp1_val = current_price + (side_mult * 1.8 * atr)
                                    tp2_val = current_price + (side_mult * 3.2 * atr)
                                    tp3_val = current_price + (side_mult * 5.5 * atr)
                                    
                                    msg = (
                                        f"🤖💎 *IA TOP ENTRY >80% (BINGX LIVE)*\n\n"
                                        f"✦ *Asset:* #{bx_sym}\n"
                                        f"✦ *Segnale IA:* {dir_txt}\n"
                                        f"✦ *Confidenza IA:* `{ai_res['conf']}%`\n"
                                        f"✦ *Ingresso:* `${current_price:,.4f}`\n\n"
                                        f"🎯 *Take Profit 1 (TP1):* `${tp1_val:,.4f}`\n"
                                        f"🎯 *Take Profit 2 (TP2):* `${tp2_val:,.4f}`\n"
                                        f"🎯 *Take Profit 3 (TP3):* `${tp3_val:,.4f}`\n"
                                        f"🛑 *Stop Loss (SL):* `${sl_val:,.4f}`\n"
                                    )
                                    if token and chat_id:
                                        send_telegram_alert(token, chat_id, msg)
                                    if auto_exec == 1 and trading_mode == "real" and api_key and secret_key:
                                        side = "BUY" if ai_res["dir"] > 0 else "SELL"
                                        pos_side = "LONG" if ai_res["dir"] > 0 else "SHORT"
                                        qty = 0.001 if sym == "BTC" else (0.01 if sym == "ETH" else 1.0)
                                        place_bingx_order(api_key, secret_key, bx_sym, side, pos_side, qty)
                                    last_alerts[sym] = now
        except Exception as e:
            print(f"Errore scanner IA BingX: {e}")
        time.sleep(30)

scanner_thread = threading.Thread(target=background_bingx_scanner, daemon=True)
scanner_thread.start()

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Scanner IA BingX · 3 TP &amp; Precisione Avanzata</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
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
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(900px 420px at 18% -80px,rgba(214,176,76,.10),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,44px 44px,44px 44px}
.wrap{max-width:1560px;margin:0 auto;padding-block:18px 28px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}

.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px 24px;margin-bottom:14px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand h1{font:700 18px/1.25 var(--font-display);letter-spacing:.14em;text-transform:uppercase}
.brand p{font:400 11px var(--font-mono);color:var(--mute);letter-spacing:.04em;margin-top:3px}
.chips{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line2);padding:6px 10px;font:500 11px var(--font-mono);letter-spacing:.05em;color:var(--ink2);background:rgba(12,16,23,.7)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 8px var(--up);animation:pulse 1.8s infinite}
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
.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.col{display:flex;flex-direction:column;gap:14px;min-width:0}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);padding:14px 16px 16px;min-width:0}
.panel::before,.panel::after{content:"";position:absolute;width:11px;height:11px;border:1px solid var(--gold);opacity:.85}
.panel::before{top:-1px;left:-1px;border-right:0;border-bottom:0}
.panel::after{bottom:-1px;right:-1px;border-left:0;border-top:0}
.panel.hot{border-color:rgba(255,210,63,.45)}
.ph{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.ph h2{font:600 12px var(--font-display);letter-spacing:.18em;text-transform:uppercase;color:var(--gold)}
.ph .sub{font:400 10.5px var(--font-mono);color:var(--mute);letter-spacing:.04em}

.gsvg{display:block;width:100%;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:10;opacity:.28}
.gval{fill:none;stroke-width:10;transition:stroke-dasharray 1.1s cubic-bezier(.2,.8,.2,1),stroke .6s;filter:drop-shadow(0 0 5px currentColor)}
.gnum{font:700 42px/1 var(--font-mono);text-align:center;letter-spacing:-.02em}
.glab{font:600 12px var(--font-display);letter-spacing:.22em;text-transform:uppercase;text-align:center;margin-top:8px}
.gbig{max-width:380px;margin:0 auto}
.sbrow{display:flex;justify-content:space-between;font:500 10px var(--font-mono);letter-spacing:.06em;margin-top:6px}

.pill{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;font:700 10px var(--font-mono);letter-spacing:.07em;border:1px solid currentColor;white-space:nowrap}
.pill.LONG{color:var(--up);background:rgba(46,230,166,.08)}
.pill.SHORT{color:var(--dn);background:rgba(255,90,118,.08)}
.pill.WAIT,.pill.N{color:var(--mute)}
.pill.TOP{color:var(--dia);background:var(--dia-soft)}

.exe{display:grid;gap:0}
.exe div{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
.exe div:last-child{border-bottom:0}
.exe span{font:400 11px var(--font-mono);color:var(--mute)}
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
#chart{height:400px}

.tw{overflow:auto;max-height:620px;border:1px solid var(--line)}
table.main{border-collapse:collapse;width:100%;min-width:1280px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);letter-spacing:.08em;color:var(--mute);text-transform:uppercase;padding:9px 8px;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main td{padding:7px 8px;text-align:center;font:400 12px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(214,176,76,.05)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
tr.r.topr{box-shadow:inset 2px 0 0 var(--dia)}

.config-panel{background:#0c1017;border:1px solid var(--line);padding:15px;margin-bottom:15px;border-radius:4px}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:10px}
input,select{background:#111722;border:1px solid var(--line2);color:#fff;padding:8px;width:100%;box-sizing:border-box}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel" style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;">
      <div>
        <span class="chip">Balance Reale (BingX): <b style="color:var(--gold)">${{ "%.2f"|format(account.balance) }}</b></span>
        <span class="chip">Equity Reale: <b style="color:var(--up)">${{ "%.2f"|format(account.equity) }}</b></span>
      </div>
      <a href="{{ url_for('logout') }}" style="color:var(--dn);text-decoration:none;font:600 11px var(--font-mono);">LOGOUT</a>
    </div>
  </div>

  <section class="config-panel">
    <div class="ph"><h2>⚙️ Configurazione Account &amp; BingX IA</h2></div>
    <form method="POST">
      <div class="form-grid">
        <div>
          <label style="font:11px var(--font-mono);color:var(--mute)">Modalità Trading</label>
          <select name="trading_mode">
            <option value="paper" {% if settings.trading_mode == 'paper' %}selected{% endif %}>Paper Trading</option>
            <option value="real" {% if settings.trading_mode == 'real' %}selected{% endif %}>Trading Reale (BingX)</option>
          </select>
        </div>
        <div>
          <label style="font:11px var(--font-mono);color:var(--mute)">BingX API Key</label>
          <input type="text" name="bingx_api_key" value="{{ settings.bingx_api_key }}">
        </div>
        <div>
          <label style="font:11px var(--font-mono);color:var(--mute)">BingX Secret Key</label>
          <input type="password" name="bingx_secret_key" value="{{ settings.bingx_secret_key }}">
        </div>
        <div>
          <label style="font:11px var(--font-mono);color:var(--mute)">Telegram Token</label>
          <input type="text" name="telegram_token" value="{{ settings.telegram_token }}">
        </div>
        <div>
          <label style="font:11px var(--font-mono);color:var(--mute)">Telegram Chat ID</label>
          <input type="text" name="chat_id" value="{{ settings.chat_id }}">
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:15px;margin-top:10px;">
        <label style="font:11px var(--font-mono);display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Esecuzione Automatica BingX
        </label>
        <button type="submit">Salva Impostazioni</button>
      </div>
    </form>
  </section>

  <header class="top">
    <div class="brand">
      <svg width="40" height="40" viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#ffd23f" stroke-width="1.6"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(255,210,63,.18)" stroke="#ffd23f" stroke-width="1"/></svg>
      <div><h1>Scanner IA BingX · Precisione Predittiva &amp; 3 TP</h1><p>Modello euristico IA · Diamante lampeggiante · Notifiche Telegram</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot"></i><span id="feedTxt">BINGX IA LIVE</span></span>
      <span class="chip" id="clock">--:--:--</span>
    </div>
  </header>

  <div class="banner idle" id="banner"></div>

  <main class="grid">
    <div class="col s4">
      <section class="panel" id="pGauge">
        <div class="ph"><h2>Asset attivo (IA)</h2><span class="sub" id="gSub"></span></div>
        <div class="gbig"><div id="gMain"></div><div class="gnum" id="gNum">--</div><div class="glab" id="gLab">--</div>
          <div class="sbrow"><span class="dn">SHORT</span><span class="up">LONG</span></div></div>
      </section>
    </div>

    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Piano Operativo IA (3 TP)</h2><span class="sub" id="exSub"></span></div>
        <div class="exe" id="exe"></div>
      </section>
      <div class="topbox idle" id="topbox" style="margin-top:14px;"></div>
    </div>

    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Metriche IA &amp; ATR</h2></div>
        <div class="kv" id="extra" style="display:grid;gap:6px;font:11px var(--font-mono)"></div>
      </section>
    </div>

    <section class="panel s8">
      <div class="chead">
        <span class="sym" id="cSym">BTC</span><span class="nm" id="cName"></span>
        <span class="px mono" id="cPx"></span><span id="cCh"></span>
      </div>
      <div class="legend" style="margin:0 0 6px"><span><i style="background:var(--dia)"></i>Ingresso IA</span><span><i style="background:var(--up)"></i>TP1 / TP2 / TP3</span><span><i style="background:var(--dn)"></i>SL</span></div>
      <canvas id="chart"></canvas>
    </section>

    <section class="panel s4">
      <div class="ph"><h2>Stato IA BingX</h2></div>
      <div class="gmini" style="grid-template-columns:1fr">
        <div class="gm"><div class="cn" style="font:11px var(--font-mono);color:var(--ink2);margin-bottom:6px">Affidabilità Modello</div><div id="gCry"></div><div class="gnum" id="nCry">88%</div><div class="glab" id="lCry">OTTIMALE</div></div>
      </div>
      <div class="sec" style="font:600 10px var(--font-mono);color:var(--mute);letter-spacing:.18em;text-transform:uppercase;margin:14px 0 6px">Segnali IA TOP Attivi</div>
      <div id="opps"></div>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner IA Completo Futures BingX (TP1, TP2, TP3)</h2></div>
      <div class="tw"><table class="main"><thead><tr>
        <th style="text-align:left">Asset</th><th>Segnale IA</th><th>Prezzo Attuale</th><th>TP1 (1.8 ATR)</th><th>TP2 (3.2 ATR)</th><th>TP3 (5.5 ATR)</th><th>Stop Loss (SL)</th><th>Confidenza IA</th><th>Var 24h</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    </section>
  </main>
</div>

<script>
(() => {
'use strict';
const $ = s => document.querySelector(s);
const clamp = (x,a,b) => Math.max(a, Math.min(b, x));
const fmtN = (n,d=2) => n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const fp = p => fmtN(p, p>=100?2:p>=1?3:5);
const pc = x => (x>=0?'+':'') + fmtN(x*100,2) + '%';
const money = p => '$' + fp(p);

const U = [
  ['BTC','Bitcoin','BTC-USDT'],['ETH','Ethereum','ETH-USDT'],['SOL','Solana','SOL-USDT'],
  ['BNB','BNB','BNB-USDT'],['XRP','XRP','XRP-USDT'],['ADA','Cardano','ADA-USDT'],
  ['DOGE','Dogecoin','DOGE-USDT'],['AVAX','Avalanche','AVAX-USDT'],['LINK','Chainlink','LINK-USDT'],
  ['DOT','Polkadot','DOT-USDT'],['LTC','Litecoin','LTC-USDT'],['TON','Toncoin','TON-USDT'],
  ['SUI','Sui','SUI-USDT'],['NEAR','NEAR','NEAR-USDT'],['APT','Aptos','APT-USDT'],['ARB','Arbitrum','ARB-USDT']
];
const assets = U.map(([sym,name,bx]) => ({sym, name, bx, price:0, candles:[], ai:null, sig:null}));
const bySym = {}; assets.forEach(a => bySym[a.sym] = a);
let sel = 'BTC';

async function loadBingXData() {
  try {
    await Promise.all(assets.map(async a => {
      const res = await fetch(`https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol=${a.bx}&interval=15m&limit=100`);
      const d = await res.json();
      if(d.code === 0 && d.data && d.data.length > 0) {
        a.candles = d.data.map(k => ({t:+k.time, o:+k.open, h:+k.high, l:+k.low, c:+k.close, v:+k.volume}));
        a.price = a.candles[a.candles.length-1].c;
      }
    }));
  } catch(e) { console.error("Errore fetch BingX IA:", e); }
}

const ema = (arr, p) => {
  const k = 2/(p+1), o = [arr[0]];
  for(let i=1; i<arr.length; i++) o.push(arr[i]*k + o[i-1]*(1-k));
  return o;
};

function analyze(a) {
  const cs = a.candles;
  if(!cs || cs.length < 30) return {comb:0, conf:50, dir:1, tp1:a.price, tp2:a.price, tp3:a.price, sl:a.price, ch24:0};
  const c = cs.map(x=>x.c), h = cs.map(x=>x.h), l = cs.map(x=>x.l), v = cs.map(x=>x.v);
  const e20 = ema(c, 20), e50 = ema(c, 50);
  const cur = a.price;
  
  const volSma = v.slice(-10).reduce((a,b)=>a+b,0)/10;
  const volSpike = v[v.length-1] / (volSma || 1);
  const isLong = cur > e20[e20.length-1] && e20[e20.length-1] > e50[e50.length-1];
  const score = isLong ? 85 : -85;
  const conf = Math.min(98, Math.max(65, 75 + Math.round(volSpike * 4)));
  
  const trList = [];
  for(let i=1; i<cs.length; i++) trList.push(Math.max(h[i]-l[i], Math.abs(h[i]-c[i-1]), Math.abs(l[i]-c[i-1])));
  const atr = trList.slice(-14).reduce((x,y)=>x+y, 0)/14 || (cur * 0.01);
  
  const mult = isLong ? 1 : -1;
  const sl = cur - (mult * 1.4 * atr);
  const tp1 = cur + (mult * 1.8 * atr);
  const tp2 = cur + (mult * 3.2 * atr);
  const tp3 = cur + (mult * 5.5 * atr);
  
  return {
    comb: score, conf: conf, dir: isLong ? 1 : -1,
    tp1, tp2, tp3, sl, atr,
    ch24: (cur / cs[Math.max(0, cs.length-96)].c) - 1
  };
}

function scanAll() {
  for(const a of assets) {
    const ai = analyze(a);
    a.ai = ai;
    a.sig = {dir: ai.dir, entry: a.price, sl: ai.sl, tp1: ai.tp1, tp2: ai.tp2, tp3: ai.tp3};
  }
}

const isTop = a => a.ai && a.ai.conf >= 80;

function mkGauge(host) {
  let s = `<path class="gseg z4" d="M30 100 A70 70 0 0 1 170 100" fill="none" stroke="#2ee6a6" stroke-width="12"/>`;
  s += `<path class="gval z2" d="M30 100 A70 70 0 0 1 170 100" fill="none" stroke="#d6b04c" stroke-width="12" style="stroke-dasharray:110 220"/>`;
  s += `<circle cx="100" cy="100" r="6" fill="#d6b04c"/>`;
  host.innerHTML = `<svg class="gsvg" viewBox="0 0 200 115">${s}</svg>`;
  return v => {
    const val = host.querySelector('.gval');
    if(val) val.style.strokeDasharray = `${clamp(v,0,100)*2.2} 220`;
  };
}
const gMain = mkGauge($('#gMain')), gCry = mkGauge($('#gCry'));

function drawChart() {
  const a = bySym[sel]; if(!a || !a.candles.length) return;
  const chartCv = $('#chart');
  const w = chartCv.clientWidth || 500, h = 400;
  chartCv.width = w; chartCv.height = h;
  const ctx = chartCv.getContext('2d');
  ctx.fillStyle = '#0c1017'; ctx.fillRect(0, 0, w, h);
  
  ctx.fillStyle = '#ece8dc'; ctx.font = '12px "JetBrains Mono", monospace';
  ctx.fillText(`Analisi IA BingX: ${a.bx} | Prezzo: ${money(a.price)}`, 20, 30);
  
  if(a.sig) {
    ctx.fillStyle = '#2ee6a6'; 
    ctx.fillText(`🎯 TP1: ${money(a.sig.tp1)}`, 20, 65);
    ctx.fillText(`🎯 TP2: ${money(a.sig.tp2)}`, 20, 90);
    ctx.fillText(`🎯 TP3: ${money(a.sig.tp3)}`, 20, 115);
    ctx.fillStyle = '#ff5a76'; 
    ctx.fillText(`🛑 Stop Loss (SL): ${money(a.sig.sl)}`, 20, 145);
  }
}

function renderTable() {
  $('#tbody').innerHTML = assets.map(a => {
    const ai = a.ai, s = a.sig, top = isTop(a);
    return `<tr class="r${a.sym === sel ? ' sel' : ''}${top ? ' topr' : ''}" data-sym="${a.sym}">
      <td><b>${a.sym}</b><span class="n" style="color:var(--mute);font-size:10px">${a.bx}</span></td>
      <td><span class="pill ${s.dir>0?'LONG':'SHORT'}">${s.dir>0?'BUY LONG':'SELL SHORT'}</span></td>
      <td>${money(a.price)}</td>
      <td class="up">${money(s.tp1)}</td>
      <td class="up">${money(s.tp2)}</td>
      <td class="up">${money(s.tp3)}</td>
      <td class="dn">${money(s.sl)}</td>
      <td>${top?`<span class="dia" style="font-size:12px">💎</span>`:''}<span class="pill ${top?'TOP':'N'}">${ai.conf}%</span></td>
      <td class="${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</td></tr>`;
  }).join('');
}

function renderMain() {
  const a = bySym[sel], ai = a.ai, s = a.sig;
  $('#gSub').textContent = `BingX Futures · ${a.bx}`;
  gMain(ai.conf); gCry(ai.conf);
  $('#gNum').textContent = `${ai.conf}%`;
  $('#gLab').textContent = s.dir > 0 ? 'IA LONG CONFIRMED' : 'IA SHORT CONFIRMED';
  
  $('#exe').innerHTML = `
    <div><span>Prezzo Ingresso IA</span><b>${money(a.price)}</b></div>
    <div><span style="color:var(--up)">Take Profit 1 (TP1)</span><b>${money(s.tp1)}</b></div>
    <div><span style="color:var(--up)">Take Profit 2 (TP2)</span><b>${money(s.tp2)}</b></div>
    <div><span style="color:var(--up)">Take Profit 3 (TP3)</span><b>${money(s.tp3)}</b></div>
    <div><span style="color:var(--dn)">Stop Loss (SL)</span><b>${money(s.sl)}</b></div>
  `;
  
  $('#topbox').className = 'topbox' + (isTop(a) ? '' : ' idle');
  $('#topbox').innerHTML = `<h3>${isTop(a)?'💎 SEGNALE IA TOP CONFLUENZA (&gt;80%)':'MONITORAGGIO IA'}</h3><ul>
    <li><b>TP1 (1.8 ATR):</b> ${money(s.tp1)}</li>
    <li><b>TP2 (3.2 ATR):</b> ${money(s.tp2)}</li>
    <li><b>TP3 (5.5 ATR):</b> ${money(s.tp3)}</li>
    <li><b>Stop Loss:</b> ${money(s.sl)}</li></ul>`;
    
  $('#cSym').textContent = a.sym; $('#cName').textContent = a.bx; $('#cPx').textContent = money(a.price);
  $('#cCh').innerHTML = `<span class="mono ${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</span>`;
  
  $('#extra').innerHTML = `
    <div style="display:flex;justify-content:space-between"><span>ATR (Volatilità IA):</span><b>${money(ai.atr)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Affidabilità Predittiva:</span><b>${ai.conf}%</b></div>
    <div style="display:flex;justify-content:space-between"><span>Routing:</span><b>BingX Futures API</b></div>
  `;
}

function renderOverview() {
  const act = assets.filter(isTop).slice(0,5);
  $('#opps').innerHTML = act.map(a => `
    <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--line);cursor:pointer" data-sym="${a.sym}">
      <b>${a.sym}</b><span class="up">💎 +${a.ai.conf}% IA TOP</span>
    </div>`).join('') || '<div style="color:var(--mute);font-size:11px">In attesa di confluenze IA &gt;80%...</div>';
}

function renderAll() {
  renderMain(); renderTable(); drawChart(); renderOverview();
  $('#clock').textContent = new Date().toLocaleTimeString();
}

document.addEventListener('click', e => {
  const t = e.target.closest('[data-sym]');
  if(t) { sel = t.dataset.sym; renderAll(); }
});

async function boot() {
  await loadBingXData();
  scanAll();
  renderAll();
  setInterval(async () => {
    await loadBingXData();
    scanAll();
    renderAll();
  }, 6000);
}
window.__ready = boot();
})();
</script>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def home():
    if 'username' not in session:
        return redirect(url_for('login'))
    global bot_settings
    if request.method == 'POST':
        bot_settings["bingx_api_key"] = request.form.get("bingx_api_key", "").strip()
        bot_settings["bingx_secret_key"] = request.form.get("bingx_secret_key", "").strip()
        bot_settings["trading_mode"] = request.form.get("trading_mode", "paper")
        bot_settings["telegram_token"] = request.form.get("telegram_token", "").strip()
        bot_settings["chat_id"] = request.form.get("chat_id", "").strip()
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Impostazioni salvate!")
        return redirect(url_for('home'))

    account_data = {"balance": 0.00, "equity": 0.00}
    if bot_settings["trading_mode"] == "real" and bot_settings["bingx_api_key"] and bot_settings["bingx_secret_key"]:
        bingx_res = get_bingx_account_balance(bot_settings["bingx_api_key"], bot_settings["bingx_secret_key"])
        if "error" not in bingx_res:
            account_data["balance"] = bingx_res["balance"]
            account_data["equity"] = bingx_res["equity"]

    return render_template_string(DASHBOARD_HTML, settings=bot_settings, account=account_data)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect('trading_bot.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, password FROM users WHERE username = ?', (username,))
        user = cursor.fetchone()
        conn.close()
        if user and check_password_hash(user[1], password):
            session['user_id'] = user[0]
            session['username'] = username
            return redirect(url_for('home'))
        flash('Credenziali non valide.')
    return render_template_string('''<!DOCTYPE html><html><head><title>Login IA</title></head>
    <body style="background:#06080c;color:#ece8dc;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
    <div style="background:#0c1017;padding:30px;border:1px solid #2a3750;border-radius:4px;width:320px;">
        <h3 style="color:#d6b04c;text-align:center;">LOGIN BINGX IA</h3>
        <form method="POST">
            <p><label>Username</label><br><input type="text" name="username" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;"></p>
            <p><label>Password</label><br><input type="password" name="password" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#d6b04c;color:#06080c;border:none;font-weight:bold;cursor:pointer;">Accedi</button>
        </form>
    </div></body></html>''')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
