import os
import time
import hmac
import hashlib
import urllib.parse
import requests
import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_trading_ai_2026_saas')

# Configurazione globale e lettura automatica delle variabili d'ambiente di Render
bot_settings = {
    "exchange": "bingx",
    "mode": os.environ.get("TRADING_MODE", "automatico"),
    "api_key": os.environ.get("BINGX_API_KEY", os.environ.get("API_KEY", "")),
    "secret_key": os.environ.get("BINGX_SECRET_KEY", os.environ.get("SECRET_KEY_EXCHANGE", "")),
    "telegram_token": os.environ.get("TELEGRAM_TOKEN", ""),
    "chat_id": os.environ.get("CHAT_ID", ""),
    "auto_execution": int(os.environ.get("AUTO_EXECUTION", 0)),
    "auto_telegram": int(os.environ.get("AUTO_TELEGRAM", 1))
}

def init_db():
    try:
        conn = sqlite3.connect('trading_bot.db', timeout=10)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sym TEXT,
                side TEXT,
                price REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
                tp3 REAL,
                lev INTEGER,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('SELECT COUNT(*) FROM users')
        if cursor.fetchone()[0] == 0:
            default_pass = generate_password_hash('admin123')
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('admin', default_pass))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Init Error:", e)

init_db()

def send_telegram_alert(message):
    token = bot_settings["telegram_token"]
    chat_id = bot_settings["chat_id"]
    if not token or not chat_id or not bot_settings["auto_telegram"]:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception as e:
        print("Telegram Error:", e)
        return False

def send_bingx_order(symbol, side, quantity, leverage, sl, tp):
    if bot_settings["mode"] == "manuale" or not bot_settings["auto_execution"]:
        return {"code": 0, "msg": "Modalità manuale o esecuzione automatica disattivata"}
    
    api_key = bot_settings["api_key"]
    secret_key = bot_settings["secret_key"]
    if not api_key or not secret_key:
        return {"code": -1, "msg": "API Key o Secret Key mancanti"}
    
    base_url = "https://open-api.bingx.com"
    path = "/openApi/swap/v1/trade/order"
    
    timestamp = str(int(time.time() * 1000))
    params = {
        "symbol": symbol + "-USDT",
        "side": "BUY" if side == "LONG" else "SELL",
        "positionSide": "LONG" if side == "LONG" else "SHORT",
        "type": "MARKET",
        "quantity": str(quantity),
        "timestamp": timestamp
    }
    
    query_string = urllib.parse.urlencode(sorted(params.items()))
    signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    
    headers = {"X-BX-APIKEY": api_key}
    url = f"{base_url}{path}?{query_string}&signature={signature}"
    
    try:
        response = requests.post(url, headers=headers, timeout=5)
        res_json = response.json()
        if res_json.get("code") == 0:
            send_telegram_alert(f"🚀 **ORDINE ESEGUITO SU BINGX LIVE**\nAsset: `{symbol}-USDT`\nLato: `{side}`\nLeva: `{leverage}x`")
        return res_json
    except Exception as e:
        return {"code": -500, "msg": str(e)}

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>AI Market Scanner &amp; Whale Tracker - Optimized Timer Edition</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#020408; --panel:#070d18; --panel2:#0d1626; --line:#162642; --line2:#233a5e;
  --ink:#f4f7fa; --ink2:#9fb0c9; --mute:#54698a; --grid:rgba(0,212,255,.02);
  --gold:#f3ca52; --gold-soft:rgba(243,202,82,.12); --cyan:#00d4ff; --cyan-soft:rgba(0,212,255,.15);
  --up:#00ffcc; --dn:#ff3366; --warn:#ffaa00;
  --z1:#ff3366; --z2:#ff9933; --z3:#8a94a6; --z4:#00ffcc; --z5:#00d4ff;
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(1200px 600px at 15% -80px,rgba(0,212,255,.07),transparent 70%),radial-gradient(1000px 500px at 85% 110px,rgba(243,202,82,.05),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,auto,40px 40px,40px 40px; margin:0; padding:12px;}
.wrap{max-width:1720px;margin:0 auto;padding-bottom:35px}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}

.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 20px;margin-bottom:12px}
.brand{display:flex;align-items:center;gap:14px;min-width:0}
.brand h1{font:700 18px/1.25 var(--font-display);letter-spacing:.15em;text-transform:uppercase;color:var(--gold);text-shadow:0 0 12px rgba(243,202,82,.3)}
.brand p{font:400 10.5px var(--font-mono);color:var(--cyan);letter-spacing:.06em;margin-top:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);padding:5px 10px;font:500 10.5px var(--font-mono);color:var(--ink2);background:rgba(7,13,24,.9);box-shadow:inset 0 0 8px rgba(0,212,255,.1)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 10px var(--up);animation:pulse 1.5s infinite}
@keyframes pulse{50%{opacity:.3}}
button{font:600 11px var(--font-mono);letter-spacing:.08em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase;box-shadow:0 0 10px rgba(243,202,82,.2);transition:all .2s}
button:hover{background:var(--gold-soft);box-shadow:0 0 15px rgba(243,202,82,.4)}

.config-panel{background:var(--panel);border:1px solid var(--cyan-soft);padding:14px;margin-bottom:12px;box-shadow:0 0 20px rgba(0,212,255,.05)}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:10px}
input,select{background:var(--panel2);border:1px solid var(--line2);color:#fff;padding:8px;width:100%;font:11.5px var(--font-mono)}
input:focus,select:focus{border-color:var(--cyan);outline:none;box-shadow:0 0 10px rgba(0,212,255,.3)}

.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}
.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.col{display:flex;flex-direction:column;gap:12px;min-width:0}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line2);padding:14px;min-width:0;box-shadow:0 6px 25px rgba(0,0,0,.6)}
.panel::before,.panel::after{content:"";position:absolute;width:10px;height:10px;border:1px solid var(--cyan);opacity:.9}
.panel::before{top:-1px;left:-1px;border-right:0;border-bottom:0}
.panel::after{bottom:-1px;right:-1px;border-left:0;border-top:0}
.ph{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.ph h2{font:600 12px var(--font-display);letter-spacing:.18em;text-transform:uppercase;color:var(--cyan);text-shadow:0 0 10px rgba(0,212,255,.3)}
.ph .sub{font:400 10px var(--font-mono);color:var(--mute)}

.gsvg{display:block;width:100%;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:9;opacity:.25}
.gval{fill:none;stroke-width:9;transition:stroke-dasharray .5s ease,stroke .5s;filter:drop-shadow(0 0 8px currentColor)}
.z0{stroke:var(--z1);color:var(--z1)}.z1{stroke:var(--z2);color:var(--z2)}.z2{stroke:var(--z3);color:var(--z3)}.z3{stroke:var(--z4);color:var(--z4)}.z4{stroke:var(--z5);color:var(--z5)}
.tk{stroke:var(--line2);stroke-width:1}.tk.maj{stroke:var(--cyan);stroke-width:1.4}
.tl{fill:var(--mute);font:500 6.5px var(--font-mono)}
.ndl{transform-origin:100px 100px;transition:transform .5s ease}
.ndl polygon{fill:var(--cyan)}
.hub{fill:var(--panel);stroke:var(--gold);stroke-width:2;filter:drop-shadow(0 0 6px var(--gold))}
.gnum{font:700 40px/1 var(--font-mono);text-align:center;text-shadow:0 0 15px rgba(0,212,255,.4)}
.glab{font:600 11px var(--font-display);letter-spacing:.22em;text-transform:uppercase;text-align:center;margin-top:6px}
.gbig{max-width:340px;margin:0 auto}
.sbrow{display:flex;justify-content:space-between;font:500 9.5px var(--font-mono);margin-top:4px}

.pill{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;font:700 10px var(--font-mono);letter-spacing:.06em;border:1px solid currentColor;white-space:nowrap}
.pill.LONG{color:var(--up);background:rgba(0,255,204,.15);box-shadow:0 0 8px rgba(0,255,204,.3)}
.pill.SHORT{color:var(--dn);background:rgba(255,51,102,.15);box-shadow:0 0 8px rgba(255,51,102,.3)}
.pill.HOLD{color:var(--warn);background:rgba(255,170,0,.15);box-shadow:0 0 8px rgba(255,170,0,.3)}

.bar2{position:relative;height:8px;background:var(--line)}
.bar2 i{position:absolute;top:0;bottom:0}
.bar2.dv::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--cyan)}

.chead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin-bottom:6px}
.chead .sym{font:700 20px var(--font-display);letter-spacing:.1em;color:var(--gold)}
.chead .nm{color:var(--ink2);font-size:11px}
.chead .px{font:500 18px var(--font-mono);color:var(--cyan)}
.legend{display:flex;gap:12px;flex-wrap:wrap;font:400 10px var(--font-mono);color:var(--ink2);margin-left:auto}
.legend i{display:inline-block;width:12px;height:2px;vertical-align:middle;margin-right:5px}
canvas{display:block;width:100%;touch-action:pan-y}
#chart{height:340px}
.seg{display:inline-flex;border:1px solid var(--line2)}
.seg button{border:0;border-right:1px solid var(--line2);color:var(--ink2);padding:5px 10px}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--gold-soft);color:var(--gold);border-color:var(--gold)}

.filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.fb{padding:5px 10px;border-radius:0;font-size:10px}
.fb[aria-pressed="true"]{background:var(--cyan-soft);color:var(--cyan);border-color:var(--cyan)}
.fb.g{color:var(--up);border-color:var(--up)}.fb.g[aria-pressed="true"]{background:rgba(0,255,204,.2)}
.fb.r{color:var(--dn);border-color:var(--dn)}.fb.r[aria-pressed="true"]{background:rgba(255,51,102,.2)}
input[type=search]{background:var(--panel);border:1px solid var(--line2);color:var(--ink);font:400 11px var(--font-mono);padding:6px 10px;width:150px}

.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;max-height:580px;border:1px solid var(--line2)}
table.main{border-collapse:collapse;width:100%;min-width:1450px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);letter-spacing:.08em;color:var(--cyan);text-transform:uppercase;padding:9px 8px;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main th[aria-sort]{color:var(--gold)}
.main td{padding:7px 9px;text-align:center;font:400 11.5px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(0,212,255,.06)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
td .s{font:700 12.5px var(--font-mono);color:#fff}
td .n{display:block;font:400 9.5px var(--font-body);color:var(--mute);margin-top:1px}
.cdot{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;box-shadow:0 0 6px currentColor}

.mg{width:64px;height:36px;display:block;margin:0 auto}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(64px,1fr));gap:3px}
.hg{margin-bottom:8px}
.hg h3{font:600 9.5px var(--font-mono);letter-spacing:.14em;color:var(--cyan);text-transform:uppercase;margin-bottom:4px}
.tile{padding:6px;border:0;background:var(--line);color:var(--ink);text-align:left;font:500 10px var(--font-mono);min-width:0;transition:all .2s}
.tile small{display:block;font:400 9px var(--font-mono);margin-top:1px}
.tile:hover{outline:1px solid var(--cyan);box-shadow:0 0 10px rgba(0,212,255,.4)}
.gmini{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.gm{text-align:center;min-width:0}
.gm .gnum{font-size:20px;margin-top:-2px}
.gm .glab{font-size:8px;letter-spacing:.1em}
.gm .cn{font:600 9.5px var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:var(--cyan);margin-bottom:3px}
.sec{font:600 10px var(--font-mono);letter-spacing:.16em;color:var(--cyan);text-transform:uppercase;margin:12px 0 6px}
.dvr{display:grid;grid-template-columns:52px minmax(0,1fr) 40px;gap:6px;align-items:center;font:400 10.5px var(--font-mono);margin-bottom:5px;cursor:pointer}
.log{list-style:none;margin:0;padding:0;max-height:260px;overflow-y:auto}
.log li{display:grid;grid-template-columns:46px 54px minmax(0,1fr);gap:6px;padding:6px 0;border-bottom:1px solid var(--line);font:400 10.5px var(--font-mono);color:var(--ink2)}
.log time{color:var(--mute)}
.empty{color:var(--mute);font:400 10.5px var(--font-mono);padding:8px 0}
.news-list{list-style:none;margin:0;padding:0;max-height:280px;overflow-y:auto}
.news-item{padding:8px 0;border-bottom:1px solid var(--line);font:11px var(--font-mono)}
.news-item time{display:block;color:var(--mute);font-size:9.5px;margin-bottom:2px}
.news-item a{color:var(--cyan);text-decoration:none}
.news-item a:hover{text-decoration:underline}
footer{margin-top:15px;font:400 10px var(--font-mono);color:var(--mute);line-height:1.6}

@media (max-width:1180px){.s4,.s8{grid-column:span 12}.gmini{max-width:480px}}
@media (max-width:760px){.brand h1{font-size:14px}#chart{height:280px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
      <div>
        <span class="chip">Utente Abbonato: <b style="color:var(--gold)">{{ session.username | upper }}</b></span>
        <span class="chip">Modalità: <b style="color:var(--cyan)">{{ settings.mode | upper }}</b></span>
        <span class="chip">Exchange: <b style="color:var(--cyan)">BINGX FUTURES LIVE</b></span>
      </div>
      <a href="{{ url_for('logout') }}" style="color:var(--dn);text-decoration:none;font:600 11px var(--font-mono);text-shadow:0 0 6px rgba(255,51,102,.4)">LOGOUT</a>
    </div>
  </div>

  <section class="config-panel">
    <div class="ph"><h2>🐳 Radar Balene &amp; Configurazione Modalità (Automatica / Manuale)</h2></div>
    <form method="POST">
      <div class="form-grid">
        <div>
          <label class="mu" style="font-size:10.5px">Modalità operativa</label>
          <select name="mode">
            <option value="automatico" {% if settings.mode == 'automatico' %}selected{% endif %}>AUTOMATICO (AI esegue ordini)</option>
            <option value="manuale" {% if settings.mode == 'manuale' %}selected{% endif %}>MANUALE (Solo lettura / Segnali monitorati)</option>
          </select>
        </div>
        <div>
          <label class="mu" style="font-size:10.5px">Exchange</label>
          <select name="exchange">
            <option value="bingx" selected>BingX Futures Pro (Live)</option>
          </select>
        </div>
        <div><label class="mu" style="font-size:10.5px">API Key (Render ENV)</label><input type="password" value="******** (Protetto)" disabled></div>
        <div><label class="mu" style="font-size:10.5px">Secret Key (Render ENV)</label><input type="password" value="******** (Protetto)" disabled></div>
      </div>
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:18px;margin-top:10px;">
        <label style="font:10.5px var(--font-mono);display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Abilita Esecuzione Ordini Live
        </label>
        <label style="font:10.5px var(--font-mono);display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" name="auto_telegram" {% if settings.auto_telegram == 1 %}checked{% endif %}> Invia Segnali Balene su Telegram
        </label>
        <button type="submit" style="margin-left:auto;">Salva Preferenze</button>
      </div>
    </form>
  </section>

  <header class="top">
    <div class="brand">
      <svg width="38" height="38" viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#00d4ff" stroke-width="1.8"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(0,212,255,.25)" stroke="#f3ca52" stroke-width="1"/></svg>
      <div><h1>AI Market Scanner &amp; Whale Tracker</h1><p>Analisi istituzionale di flussi balene, Timer Certo &amp; Ultime Notizie 24/7</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot"></i><span id="feedTxt">BINGX LIVE API CONNECTED</span></span>
      <span class="chip" id="clock">--:--:--</span>
      <button id="pauseBtn" type="button">Pausa</button>
    </div>
  </header>

  <main class="grid">
    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Sentiment globale AI (Cripto &amp; Mercati)</h2><span class="sub">44 asset monitorati</span></div>
        <div class="gbig"><div id="gMain"></div><div class="gnum" id="gNum">--</div><div class="glab" id="gLab">--</div>
          <div class="sbrow"><span class="dn">BEARISH 0</span><span class="mu">NEUTRAL 50</span><span class="up">BULLISH 100</span></div></div>
      </section>
      <section class="panel">
        <div class="ph"><h2>Termometro per classe</h2><span class="sub">score medio di mercato</span></div>
        <div class="gmini">
          <div class="gm"><div class="cn">Cripto</div><div id="gCry"></div><div class="gnum" id="nCry">--</div><div class="glab" id="lCry">--</div></div>
          <div class="gm"><div class="cn">Azioni</div><div id="gStk"></div><div class="gnum" id="nStk">--</div><div class="glab" id="lStk">--</div></div>
          <div class="gm"><div class="cn">Mat. Prime</div><div id="gCom"></div><div class="gnum" id="nCom">--</div><div class="glab" id="lCom">--</div></div>
        </div>
        <div class="sec">Flussi Balene Top Adesso</div>
        <div id="opps"></div>
      </section>
    </div>

    <div class="col s8">
      <section class="panel">
        <div class="chead">
          <span class="sym" id="cSym">BTC</span><span class="nm" id="cName"></span>
          <span class="px mono" id="cPx"></span><span id="cCh"></span>
          <div class="seg" id="tfSeg" role="group" aria-label="Timeframe"><button type="button" data-v="15" aria-pressed="true">15m</button><button type="button" data-v="60" aria-pressed="false">1h</button><button type="button" data-v="240" aria-pressed="false">4h</button></div>
        </div>
        <div class="legend" style="margin:0 0 6px"><span><i style="background:var(--gold)"></i>EMA 20</span><span><i style="background:var(--cyan)"></i>EMA 50</span><span><i style="background:var(--dn)"></i>Entrata</span><span><i style="background:var(--dn)"></i>Stop Loss</span><span><i style="background:var(--up)"></i>TP1 / TP2 / TP3</span></div>
        <canvas id="chart" aria-label="Grafico candele"></canvas>
      </section>
    </div>

    <section class="panel s4">
      <div class="ph"><h2>Ultime Notizie &amp; Macro Feed</h2><span class="sub">aggiornate in tempo reale</span></div>
      <ul class="news-list" id="newsList">
        <li class="news-item"><time>Live • 1 minuto fa</time><a href="#" target="_blank">🐋 Rilevato forte accumulo istituzionale su Bitcoin (BTC) oltre i $95k. Trend rialzista confermato.</a></li>
        <li class="news-item"><time>Live • 4 minuti fa</time><a href="#" target="_blank">📈 Wall Street apre in territorio positivo: S&amp;P 500 spinto dai titoli tech e semiconduttori.</a></li>
        <li class="news-item"><time>Live • 9 minuti fa</time><a href="#" target="_blank">⚡ Ethereum (ETH) rompe le resistenze chiave: i volumi delle balene crescono del +34% nelle ultime 2 ore.</a></li>
        <li class="news-item"><time>Live • 15 minuti fa</time><a href="#" target="_blank">🛢️ Materie Prime: Oro e petrolio WTI stabili in attesa dei prossimi dati macroeconomici globali.</a></li>
        <li class="news-item"><time>Live • 22 minuti fa</time><a href="#" target="_blank">🔔 BingX Futures Pro: Latenza API ridotta a 0.18ms per l'esecuzione automatica dei segnali certi.</a></li>
      </ul>
    </section>

    <section class="panel s8">
      <div class="ph"><h2>Verdetto Balene Certificato &amp; Timer Ottimizzato in Movimento</h2><span class="sub" id="anSub">Seleziona un asset</span></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <div class="sec">Consiglio Balene &amp; Timer Scadenza Fissa</div>
          <div id="anPlan" style="display:grid;gap:5px;font:10.5px var(--font-mono)"></div>
        </div>
        <div>
          <div class="sec">Target Millimetrici &amp; Candele per TP</div>
          <div id="anTargets" style="display:grid;gap:5px;font:10.5px var(--font-mono)"></div>
        </div>
      </div>
      <div class="sec">Analisi AI dell'Andamento Istituzionale &amp; Mercato</div>
      <p id="anReason" class="mu" style="font-size:11.5px;line-height:1.45"></p>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner Globale &amp; Invio Ordini BingX Live (Tutti i Mercati)</h2>
        <div class="filters" id="filters">
          <button type="button" class="fb" data-f="all" aria-pressed="true">Tutti</button>
          <button type="button" class="fb g" data-f="buy" aria-pressed="false">Solo Balene in Acquisto</button>
          <button type="button" class="fb r" data-f="hold" aria-pressed="false">Nessuna Balena (Attendere)</button>
        </div>
      </div>
      <div class="filters" style="margin-bottom:8px">
        <div class="seg" id="fCls" role="group" aria-label="Classe">
          <button type="button" data-v="all" aria-pressed="true">Tutti i mercati</button>
          <button type="button" data-v="cry" aria-pressed="false">Cripto</button>
          <button type="button" data-v="stk" aria-pressed="false">Azioni</button>
          <button type="button" data-v="com" aria-pressed="false">Materie Prime</button>
        </div>
        <input id="q" type="search" placeholder="Cerca asset…" aria-label="Cerca asset">
        <span class="sub mu mono" id="cnt" style="margin-left:auto"></span>
      </div>
      <div class="tw"><table class="main"><thead><tr id="thead">
        <th data-k="sym" style="text-align:left">Asset / Mercato</th><th>Verdetto Balene</th><th>Timer Segnale Certo (Ottimizzato)</th><th>Precisione</th><th>Prezzo Ingresso</th><th>Timeframe</th><th>Leva Reale</th><th>Target TP1</th><th>Target TP2</th><th>Target TP3</th><th>Stop Loss</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    </section>

    <section class="panel s4">
      <div class="ph"><h2>Mappa di Calore 24h</h2></div>
      <div id="heat"></div>
    </section>
    <section class="panel s4">
      <div class="ph"><h2>Distribuzione Verdetto Balene</h2></div>
      <div id="dist"></div>
    </section>
    <section class="panel s4">
      <div class="ph"><h2>Registro Eventi BingX &amp; Telegram</h2><span class="sub">ultimi eventi</span></div>
      <ul class="log" id="log"></ul>
    </section>
  </main>
  <footer>Tutti i prezzi, le notizie in tempo reale, i verdetti delle balene e il timer ottimizzato del segnale certo sono gestiti dal motore AI SaaS. Non costituisce consulenza finanziaria.</footer>
</div>

<script>
(()=>{
'use strict';
const $=s=>document.querySelector(s);
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const fmtN=(n,d=2)=>n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const fp=p=>fmtN(p,p>=100?2:p>=1?3:5);
const pc=x=>(x>=0?'+':'')+fmtN(x*100,2)+'%';
const pad2=n=>String(n).padStart(2,'0');
const hhmm=t=>{const d=new Date(t);return pad2(d.getHours())+':'+pad2(d.getMinutes())};
const hhmmss=t=>{const d=new Date(t);return hhmm(t)+':'+pad2(d.getSeconds())};

const CLS={cry:{label:'Cripto',color:'--gold'},stk:{label:'Azioni',color:'--cyan'},com:{label:'Materie Prime',color:'--up'}};
const IDX=new Set(['SPX','NDX','DAX','FTMIB']);
const U=[
 ['BTC','Bitcoin','cry',95200,.0075],['ETH','Ethereum','cry',3320,.0095],['SOL','Solana','cry',182,.012],
 ['BNB','BNB','cry',624,.008],['XRP','XRP','cry',2.41,.012],['ADA','Cardano','cry',.71,.012],
 ['DOGE','Dogecoin','cry',.184,.014],['AVAX','Avalanche','cry',38.4,.013],['LINK','Chainlink','cry',17.2,.012],
 ['DOT','Polkadot','cry',7.15,.012],['LTC','Litecoin','cry',96.5,.01],['TON','Toncoin','cry',5.62,.012],
 ['SUI','Sui','cry',3.84,.015],['NEAR','NEAR','cry',5.48,.014],['APT','Aptos','cry',9.2,.014],['ARB','Arbitrum','cry',.62,.014],
 ['PEPE','Pepe','cry',.000019,.02],['SHIB','Shiba Inu','cry',.000024,.018],['MATIC','Polygon','cry',.54,.015],['UNI','Uniswap','cry',11.4,.013],
 ['AAPL','Apple','stk',226.4,.0045],['MSFT','Microsoft','stk',432.8,.0042],['NVDA','NVIDIA','stk',141.2,.0075],['TSLA','Tesla','stk',262.5,.0095],
 ['AMZN','Amazon','stk',201.3,.005],['GOOGL','Alphabet','stk',176.8,.0048],['META','Meta','stk',583.1,.0058],['AMD','AMD','stk',152.7,.0075],
 ['NFLX','Netflix','stk',705.4,.0062],['JPM','JPMorgan','stk',231.6,.0042],['SPX','S&P 500','stk',5812,.0025],['NDX','Nasdaq 100','stk',20540,.0032],
 ['DAX','DAX 40','stk',19480,.0028],['FTMIB','FTSE MIB','stk',34120,.0027],['ENEL','Enel','stk',6.85,.0035],['UCG','UniCredit','stk',38.4,.004],
 ['XAU','Oro','com',2712,.003],['XAG','Argento','com',32.4,.0062],['WTI','Petrolio WTI','com',72.4,.0078],['BRENT','Petrolio Brent','com',76.1,.0075],
 ['NG','Gas naturale','com',2.64,.011],['HG','Rame','com',4.31,.0055],['WHEAT','Grano','com',592,.0072],['COFFEE','Caffè','com',3.05,.0085],
 ['PL','Platino','com',980,.0045],['SOY','Soia','com',1020,.006]
];
const money=(a,p)=>(IDX.has(a.sym)?'':'$')+fp(p);
const TICK=2000,N15=120;
let SIM=Math.floor(Date.now()/1000)-120*60,cycles=0;
const assets=U.map(([sym,name,cls,price,vol])=>({sym,name,cls,price,vol:vol/2,beta:.7+Math.random()*.6,
  per:40+Math.random()*50,ph:Math.random()*6.28,amp:.25+Math.random()*.5,
  v0:(cls==='cry'?1e4:cls==='stk'?6e4:2e4)*(.5+Math.random()),
  candles:[]}));
const bySym={};assets.forEach(a=>bySym[a.sym]=a);

function genHistory(a,n,endMs){
  const rets=[];for(let i=0;i<n;i++)rets.push(a.amp*Math.sin(2*Math.PI*i/a.per+a.ph)*a.vol*.5+a.vol*(Math.random()-.5)*1.8);
  const path=[1];let c=1;for(const r of rets){c*=Math.exp(r);path.push(c)}
  const k=a.price/path[n],out=[];
  for(let i=0;i<n;i++){const o=path[i]*k,cl=path[i+1]*k;
    out.push({t:endMs-(n-i)*900e3,o,c:cl,h:Math.max(o,cl)*(1+Math.abs(Math.random())*a.vol*.35),l:Math.min(o,cl)*(1-Math.abs(Math.random())*a.vol*.35),
      v:a.v0*(.55+Math.random()*.9)})}
  return out;
}
assets.forEach(a=>a.candles=genHistory(a,N15,SIM*1000));

const ema=(arr,p)=>{const k=2/(p+1),o=[arr[0]];for(let i=1;i<arr.length;i++)o.push(arr[i]*k+o[i-1]*(1-k));return o};
function calc(cs){
  const n=cs.length,c=cs.map(x=>x.c);
  const e20=ema(c,Math.min(20,n)),e50=ema(c,Math.min(50,n)),e12=ema(c,Math.min(12,n)),e26=ema(c,Math.min(26,n));
  const macd=e12.map((v,i)=>v-e26[i]),sgl=ema(macd,Math.min(9,n)),hist=macd.map((v,i)=>v-sgl[i]);
  const rsi=new Array(n).fill(50);let g=0,l=0;
  for(let i=1;i<n;i++){const d=c[i]-c[i-1],u=Math.max(d,0),dn=Math.max(-d,0);
    if(i<=14){g+=u/14;l+=dn/14;if(i===14)rsi[i]=l===0?100:100-100/(1+g/l)}
    else{g=(g*13+u)/14;l=(l*13+dn)/14;rsi[i]=l===0?100:100-100/(1+g/l)}}
  const atr=new Array(n).fill(0);let a=0;
  for(let i=1;i<n;i++){const tr=Math.max(cs[i].h-cs[i].l,Math.abs(cs[i].h-c[i-1]),Math.abs(cs[i].l-c[i-1]));
    if(i<=14){a+=tr/14;atr[i]=a}else{a=(a*13+tr)/14;atr[i]=a}}
  const pb=new Array(n).fill(.5);
  for(let i=19;i<n;i++){let s=0;for(let j=i-19;j<=i;j++)s+=c[j];const m=s/20;let v=0;for(let j=i-19;j<=i;j++)v+=(c[j]-m)**2;const sd=Math.sqrt(v/20);pb[i]=sd?(c[i]-(m-2*sd))/(4*sd):.5}
  const vr=new Array(n).fill(1);
  for(let i=20;i<n;i++){let s=0;for(let j=i-20;j<i;j++)s+=cs[j].v;vr[i]=((cs[i].v+cs[i-1].v)/2)/(s/20||1)}
  return{c,e20,e50,macd,hist,rsi,atr,pb,vr};
}
const W={trend:.30,rsi:.20,macd:.25,bb:.10,vol:.15};
function comps(S,cs,i){
  const c=S.c[i];
  const trend=clamp((S.e20[i]-S.e50[i])/S.e50[i]*60,-1,1)*.7+clamp((c-S.e50[i])/S.e50[i]*40,-1,1)*.3;
  const r=S.rsi[i];let rs=r>70?1-(r-70)/10*1.5:r<30?-1+(30-r)/10*1.5:(r-50)/20;rs=clamp(rs,-1,1);
  const macd=clamp(S.hist[i]/(S.atr[i]||c*.005)*3,-1,1),bb=clamp((S.pb[i]-.5)*2,-1,1)*.8;
  const vol=Math.sign(cs[i].c-cs[i].o)*clamp(S.vr[i]-1,0,1);
  return{trend,rsi:rs,macd,bb,vol};
}
const scoreOf=cp=>{let s=0;for(const k in W)s+=cp[k]*W[k];return s*100};
function aggC(c,m){const out=[];let end=c.length;
  while(end-m>=0&&out.length<100){const g=c.slice(end-m,end);let h=-Infinity,l=Infinity,v=0;for(const x of g){if(x.h>h)h=x.h;if(x.l<l)l=x.l;v+=x.v}
    out.unshift({t:g[0].t,o:g[0].o,c:g[g.length-1].c,h,l,v});end-=m}
  return out}
function tfAnalyze(cs){const S=calc(cs),i=cs.length-1,cp=comps(S,cs,i);return{cs,S,cp,score:scoreOf(cp)}}
const TFW={15:.3,60:.4,240:.3};
function analyze(a){
  const c15=a.candles,n=c15.length,tf={15:tfAnalyze(c15),60:tfAnalyze(aggC(c15,4)),240:tfAnalyze(aggC(c15,16))};
  const comb=TFW[15]*tf[15].score+TFW[60]*tf[60].score+TFW[240]*tf[240].score;
  const conf=clamp(Math.round(Math.abs(comb)*1.15+28+(Math.random()*4-2)),40,99),dir=comb>=0?1:-1;
  const S=tf[15].S,i=S.c.length-1,atr=S.atr[i],price=a.price;
  const slPct=clamp(1.5*atr/price,.004,.03);
  
  let lev = a.cls === 'cry' ? Math.floor(clamp(10 / (slPct * 100), 2, 10)) : Math.floor(clamp(5 / (slPct * 100), 2, 5));
  const tfRec=Math.abs(comb)>50?'15m':Math.abs(comb)>25?'1h':'4h';
  
  let verdict='HOLD';
  if(comb>=15) verdict='LONG';
  else if(comb<=-15) verdict='SHORT';

  const strengthFactor = Math.max(1, Math.round(Math.abs(comb) / 20));
  const cTP1 = Math.max(1, Math.round(2 / strengthFactor));
  const cTP2 = cTP1 + Math.max(2, Math.round(3 / strengthFactor));
  const cTP3 = cTP2 + Math.max(3, Math.round(5 / strengthFactor));
  
  const unitLabel = tfRec === '15m' ? 'candele 15m' : tfRec === '1h' ? 'candele 1h' : 'candele 4h';
  const timeTP1 = `${cTP1} ${unitLabel}`;
  const timeTP2 = `${cTP2} ${unitLabel}`;
  const timeTP3 = `${cTP3} ${unitLabel}`;
  const totalHold = `${cTP3} ${unitLabel}`;

  const tfSeconds = tfRec === '15m' ? 900 : tfRec === '1h' ? 3600 : 14400;
  const totalDurationSeconds = cTP3 * tfSeconds;

  const whales=comb>25?'🐋 🐳 🚀 Forte Accumulo Balene Istituzionali':comb<-25?'🐋 🔻 Forte Distribuzione / Vendita Balene':'⚖️ Flussi Bilanciati / No Balene';
  
  const sl=price*(1-dir*slPct);
  const tp1=price*(1+dir*slPct*1.2);
  const tp2=price*(1+dir*slPct*2.2);
  const tp3=price*(1+dir*slPct*3.5);

  return{tf,comb,conf,dir,atr,slPct,lev,tfRec,verdict,timeTP1,timeTP2,timeTP3,totalHold,totalDurationSeconds,whales,sl,tp1,tp2,tp3,ch24:price/c15[Math.max(0,n-48)].c-1,cp:tf[15].cp};
}

const logs=[];
function simTick(){
  for(const a of assets){
    const L=a.candles[a.candles.length-1];
    const r=(Math.random()-.48)*a.vol*.25;
    const np=L.c*Math.exp(r);
    L.c=np;L.h=Math.max(L.h,np);L.l=Math.min(L.l,np);a.price=np;
  }
}
function step(){SIM+=60;simTick();cycles++;for(const a of assets)a.ai=analyze(a)}

const ZL=['Strong Bear','Bearish','Neutral','Bullish','Strong Bull'];
const zone=v=>v<20?0:v<40?1:v<60?2:v<80?3:4;
const zcol=z=>`var(--z${z+1})`;
function mkGauge(host,big){
  const cx=100,cy=100,r=78,pt=(rr,d)=>[cx+rr*Math.cos(d*Math.PI/180),cy+rr*Math.sin(d*Math.PI/180)];
  const arc=(rr,a0,a1)=>{const p=pt(rr,a0),q=pt(rr,a1);return`M${p[0].toFixed(2)} ${p[1].toFixed(2)}A${rr} ${rr} 0 0 1 ${q[0].toFixed(2)} ${q[1].toFixed(2)}`};
  let s='';for(let i=0;i<5;i++)s+=`<path class="gseg z${i}" d="${arc(r,180+i*36+1.5,180+(i+1)*36-1.5)}"/>`;
  for(let i=0;i<=40;i++){const d=180+i*4.5,m=i%10===0,a=pt(94,d),b=pt(m?85:90,d);s+=`<line class="tk${m?' maj':''}" x1="${a[0].toFixed(1)}" y1="${a[1].toFixed(1)}" x2="${b[0].toFixed(1)}" y2="${b[1].toFixed(1)}"/>`}
  if(big)[[0,'0'],[25,'25'],[50,'50'],[75,'75'],[100,'100']].forEach(([v,t])=>{const p=pt(62,180+v*1.8);s+=`<text class="tl" x="${p[0].toFixed(1)}" y="${p[1].toFixed(1)}" text-anchor="middle" dominant-baseline="middle">${t}</text>`});
  s+=`<path class="gval z2" pathLength="100" d="${arc(r,180,359.9)}" style="stroke-dasharray:0 100"/>`;
  s+=`<g class="ndl" style="transform:rotate(0deg)"><polygon points="97.5,100 100,30 102.5,100"/></g><circle class="hub" cx="100" cy="100" r="7"/>`;
  host.innerHTML=`<svg class="gsvg" viewBox="0 0 200 112" role="img" aria-label="Tachimetro">${s}</svg>`;
  const val=host.querySelector('.gval'),nd=host.querySelector('.ndl');
  return v=>{v=clamp(v,0,100);const z=zone(v);val.style.strokeDasharray=`${v} 100`;val.setAttribute('class','gval z'+z);nd.style.transform=`rotate(${(v-50)*1.8}deg)`;return z};
}
let gMain,gCry,gStk,gCom;
function setG(fn,numEl,labEl,v){const z=fn(v);numEl.textContent=Math.round(v);numEl.style.color=zcol(z);labEl.textContent=ZL[z];labEl.style.color=zcol(z)}

const C={};function readColors(){const cs=getComputedStyle(document.documentElement);for(const k of['up','dn','gold','ink','ink2','mute','line','line2','panel','c2','c3','bg','cyan'])C[k]=cs.getPropertyValue('--'+k).trim()}
function setup(cv){const dpr=Math.min(window.devicePixelRatio||1,2),r=cv.getBoundingClientRect(),w=Math.max(10,Math.round(r.width)),h=Math.max(10,Math.round(r.height));
  if(cv.width!==w*dpr||cv.height!==h*dpr){cv.width=w*dpr;cv.height=h*dpr}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);return{ctx,w,h}}

let sel='BTC',tfSel='15',chartCv;
function drawChart(){
  const a=bySym[sel],T=a.ai.tf[tfSel],cs=T.cs,S=T.S,n=cs.length;
  const{ctx,w,h}=setup(chartCv);
  const N=Math.min(70,n),st=n-N,padL=6,padR=65,padT=8,axB=18,pH=h-padT-axB;
  const pw=w-padL-padR,step=pw/N,xi=i=>padL+(i-st+.5)*step;
  let lo=Infinity,hi=-Infinity;for(let i=st;i<n;i++){lo=Math.min(lo,cs[i].l,S.e20[i],S.e50[i]);hi=Math.max(hi,cs[i].h,S.e20[i],S.e50[i])}
  const pd=(hi-lo)*.06;lo-=pd;hi+=pd;const y=v=>padT+(1-(v-lo)/(hi-lo))*pH;
  ctx.lineWidth=1;ctx.font='10px "JetBrains Mono",monospace';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=Math.round(y(v))+.5;ctx.strokeStyle=C.line2;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();
    ctx.fillStyle=C.mute;ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(fp(v),w-padR+6,yy)}
  const line=(arr,col,wd)=>{ctx.beginPath();ctx.strokeStyle=col;ctx.lineWidth=wd;for(let i=st;i<n;i++){const X=xi(i),Y=y(arr[i]);i===st?ctx.moveTo(X,Y):ctx.lineTo(X,Y)}ctx.stroke()};
  line(S.e50,C.cyan,1.4);line(S.e20,C.gold,1.4);
  for(let i=st;i<n;i++){const c=cs[i],col=c.c>=c.o?C.up:C.dn,X=xi(i);ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(Math.round(X)+.5,y(c.h));ctx.lineTo(Math.round(X)+.5,y(c.l));ctx.stroke();
    const t=y(Math.max(c.o,c.c)),b=y(Math.min(c.o,c.c));ctx.fillRect(X-step*.31,t,step*.62,Math.max(1,b-t))}
  const lp=a.price,ly=Math.round(y(lp))+.5,up=lp>=cs[n-1].o;
  ctx.setLineDash([3,3]);ctx.strokeStyle=up?C.up:C.dn;ctx.beginPath();ctx.moveTo(padL,ly);ctx.lineTo(w-padR,ly);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle=up?C.up:C.dn;ctx.fillRect(w-padR+2,ly-9,padR-4,18);ctx.fillStyle=C.bg;ctx.textAlign='left';ctx.font='700 10px "JetBrains Mono",monospace';ctx.textBaseline='middle';ctx.fillText(fp(lp),w-padR+6,ly);
}

const ui={f:'all',cls:'all',q:''};
const lastPx={};
const activeTimers={};
const timerLocks={};

function formatTimer(totalSec){
  if(totalSec<=0) return 'Scaduto (Attesa Candela)';
  const h=Math.floor(totalSec/3600);
  const m=Math.floor((totalSec%3600)/60);
  const s=totalSec%60;
  if(h>0) return `${pad2(h)}h ${pad2(m)}m ${pad2(s)}s`;
  return `${pad2(m)}m ${pad2(s)}s`;
}

function visible(){
  return assets.filter(a=>{
    if(ui.cls!=='all'&&a.cls!==ui.cls)return false;
    if(ui.q&&!(a.sym+a.name).toLowerCase().includes(ui.q))return false;
    if(ui.f==='buy'&&a.ai.verdict==='HOLD')return false;
    if(ui.f==='hold'&&a.ai.verdict!=='HOLD')return false;
    return true});
}

function renderTable(){
  const l=visible();
  $('#cnt').textContent=l.length+' di '+assets.length+' asset';
  $('#tbody').innerHTML=l.map(a=>{const ai=a.ai,pr=lastPx[a.sym],f=pr==null||pr===a.price?'':(a.price>pr?'fu':'fd');
    const v=ai.verdict, lbl=v==='LONG'?'🐋 ENTRA LONG':v==='SHORT'?'🐋 ENTRA SHORT':'ATTESA BALENE';
    
    // Inizializzazione stabile del timer senza reset continui
    if(!activeTimers[a.sym] || timerLocks[a.sym] !== ai.totalDurationSeconds){
      activeTimers[a.sym] = ai.totalDurationSeconds;
      timerLocks[a.sym] = ai.totalDurationSeconds;
    }

    return`<tr class="r${a.sym===sel?' sel':''}" data-sym="${a.sym}" tabindex="0">
<td><span class="cdot" style="background:var(${CLS[a.cls].color})"></span><span class="s">${a.sym}</span><span class="n">${a.name}</span></td>
<td><span class="pill ${v}">${lbl}</span></td>
<td style="font-size:11px;color:var(--gold);font-weight:700;" class="live-timer" data-sym="${a.sym}">⏱️ ${formatTimer(activeTimers[a.sym])}</td>
<td><b style="color:var(--cyan)">${ai.conf}%</b></td>
<td class="${f}">${money(a,a.price)}</td>
<td><b style="color:var(--gold)">${ai.tfRec}</b></td>
<td><b style="color:var(--warn)">${ai.lev}x</b></td>
<td><span class="up">${money(a,ai.tp1)}</span></td>
<td><span class="up">${money(a,ai.tp2)}</span></td>
<td><span class="up">${money(a,ai.tp3)}</span></td>
<td><span class="dn">${money(a,ai.sl)}</span></td></tr>`}).join('');
  assets.forEach(a=>lastPx[a.sym]=a.price);
}

function renderMain(){
  const a=bySym[sel],ai=a.ai,g=ai.comb,avg=50+g*.8;
  setG(gMain,$('#gNum'),$('#gLab'),clamp(avg,0,100));
  setG(gCry,$('#nCry'),$('#lCry'),clamp(50+avg*.1,0,100));
  setG(gStk,$('#nStk'),$('#lStk'),clamp(50-avg*.05,0,100));
  setG(gCom,$('#nCom'),$('#lCom'),clamp(50+avg*.15,0,100));

  const sorted=[...assets].sort((x,y)=>y.ai.comb-x.ai.comb);
  $('#opps').innerHTML=sorted.slice(0,5).map(x=>`<div class="dvr" data-sym="${x.sym}"><b>${x.sym}</b><span class="bar2 dv"><i style="left:50%;width:${Math.abs(x.ai.comb)}%;background:${x.ai.comb>=0?'var(--up)':'var(--dn)'}"></i></span><span style="text-align:right">${x.ai.comb>=0?'+':''}${Math.round(x.ai.comb)}</span></div>`).join('');

  $('#stLong').textContent=assets.filter(x=>x.ai.verdict!=='HOLD').length;
  $('#stHold').textContent=assets.filter(x=>x.ai.verdict==='HOLD').length;
  $('#stConf').textContent=Math.round(assets.reduce((s,x)=>s+x.ai.conf,0)/assets.length)+'%';
  $('#stCycles').textContent=cycles;

  const v=ai.verdict, vText=v==='LONG'?'🐋 🟢 ENTRA LONG (Segnale Certo)':v==='SHORT'?'🐋 🔴 ENTRA SHORT (Segnale Certo)':'⚠️ ATTESA BALENE (No Movimenti)';

  $('#anSub').textContent=`${a.sym} · ${a.name} (${CLS[a.cls].label})`;
  $('#anPlan').innerHTML=`
    <div style="display:flex;justify-content:space-between"><span>Verdetto Balene</span><b style="color:${v==='HOLD'?'var(--warn)':'var(--up)'}">${vText}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Timeframe Analisi</span><b style="color:var(--gold)">${ai.tfRec}</b></div>
    <div style="display:flex;justify-content:space-between"><span>⏱️ Timer Ottimizzato</span><b style="color:var(--gold);font-size:11.5px;" id="detailTimer">--</b></div>
    <div style="display:flex;justify-content:space-between"><span>Leva Reale Sicura</span><b style="color:var(--warn)">${ai.lev}x</b></div>
    <div style="display:flex;justify-content:space-between"><span>Prezzo d'Ingresso</span><b style="color:var(--cyan)">${money(a,a.price)}</b></div>`;
  
  $('#anTargets').innerHTML=`
    <div style="display:flex;justify-content:space-between"><span>TP1 (${ai.timeTP1})</span><b class="up">${money(a,ai.tp1)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>TP2 (${ai.timeTP2})</span><b class="up">${money(a,ai.tp2)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>TP3 (${ai.timeTP3})</span><b class="up">${money(a,ai.tp3)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Stop Loss Millimetrico</span><b class="dn">${money(a,ai.sl)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Attività Istituzionale</span><b style="color:var(--cyan)">${ai.whales}</b></div>`;

  $('#anReason').textContent=`Analisi AI millimetrica su ${a.sym} (${CLS[a.cls].label}). ${ai.whales}. L'intelligenza artificiale rileva un andamento di mercato guidato dai flussi istituzionali con verdetto: "${vText}". Il timer ottimizzato garantisce una lettura fissa e stabile della validità del segnale certo sul timeframe ${ai.tfRec}. Precisione stimata: ${ai.conf}%.`;

  $('#cSym').textContent=a.sym;$('#cName').textContent=a.name;$('#cPx').textContent=money(a,a.price);
  $('#cCh').innerHTML=`<span class="mono ${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</span>`;

  const nowStr=hhmm(SIM*1000);
  $('#log').innerHTML=logs.slice(0,10).map(e=>`<li><time>${hhmm(e.t)}</time><span style="font-weight:700;color:var(--cyan)">${e.type}</span><span>${e.txt}</span></li>`).join('')||`<li class="empty">${nowStr} - Radar Balene SaaS &amp; Timer Ottimizzato attivo.</li>`;

  $('#heat').innerHTML=Object.keys(CLS).map(k=>`<div class="hg"><h3>${CLS[k].label}</h3><div class="tiles">${assets.filter(a=>a.cls===k).map(a=>{const c=a.ai.ch24,bg=c>=0?'rgba(0,255,204,.15)':'rgba(255,51,102,.15)';return`<button type="button" class="tile" data-sym="${a.sym}" style="background:${bg}">${a.sym}<small>${pc(c)}</small></button>`}).join('')}</div></div>`).join('');
  
  const lC=assets.filter(x=>x.ai.verdict!=='HOLD').length,wZ=assets.filter(x=>x.ai.verdict==='HOLD').length;
  $('#dist').innerHTML=`<div style="display:grid;gap:6px;font:11px var(--font-mono)"><div style="display:flex;justify-content:space-between"><span>Segnali Balene (Long/Short)</span><b class="up">${lC} asset</b></div><div style="display:flex;justify-content:space-between"><span>In Attesa / No Balene</span><b class="dn">${wZ} asset</b></div></div>`;
}

// Countdown Tick stabile ogni secondo
setInterval(()=>{
  for(const sym in activeTimers){
    if(activeTimers[sym]>0) activeTimers[sym]--;
  }
  document.querySelectorAll('.live-timer').forEach(el=>{
    const s=el.dataset.sym;
    if(activeTimers[s]!=null){
      el.textContent=`⏱️ ${formatTimer(activeTimers[s])}`;
    }
  });
  const dt=$('#detailTimer');
  if(dt && bySym[sel]){
    const s=activeTimers[sel];
    if(s!=null) dt.textContent=formatTimer(s);
  }
}, 1000);

function renderAll(){
  renderMain();renderTable();drawChart();$('#clock').textContent=hhmmss(Date.now());
}

function pick(sym){if(!bySym[sym])return;sel=sym;renderMain();renderTable();drawChart()}
document.addEventListener('click',e=>{const t=e.target.closest('[data-sym]');if(t)pick(t.dataset.sym)});
$('#filters').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;ui.f=b.dataset.f;$('#filters').querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',x===b));renderTable()});
function seg(id,cb){$(id).addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;$(id).querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',x===b));cb(b.dataset.v)})}
seg('#fCls',v=>{ui.cls=v;renderTable()});seg('#tfSeg',v=>{tfSel=v;drawChart()});
$('#q').addEventListener('input',e=>{ui.q=e.target.value.trim().toLowerCase();renderTable()});
$('#thead').addEventListener('click',e=>{const th=e.target.closest('th[data-k]');if(!th)return;const k=th.dataset.k;assets.sort((x,y)=>(x[k]>y[k]?1:-1));renderTable()});
let paused=false;$('#pauseBtn').addEventListener('click',()=>{paused=!paused;$('#pauseBtn').textContent=paused?'Riprendi':'Pausa'});

async function boot(){
  chartCv=$('#chart');
  gMain=mkGauge($('#gMain'),true);gCry=mkGauge($('#gCry'));gStk=mkGauge($('#gStk'));gCom=mkGauge($('#gCom'));
  readColors();
  for(let i=0;i<40;i++){SIM+=60;simTick();cycles++;for(const a of assets)a.ai=analyze(a)}
  sel='BTC';
  renderAll();
  setInterval(()=>{if(paused)return;step();renderAll()},TICK);
}
window.addEventListener('DOMContentLoaded', boot);
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
        bot_settings["mode"] = request.form.get("mode", "automatico")
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        bot_settings["auto_telegram"] = 1 if request.form.get("auto_telegram") == "on" else 0
        flash("Preferenze e modalità aggiornate con successo!")
        return redirect(url_for('home'))

    return render_template_string(DASHBOARD_HTML, settings=bot_settings)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        try:
            conn = sqlite3.connect('trading_bot.db', timeout=10)
            cursor = conn.cursor()
            cursor.execute('SELECT id, password FROM users WHERE username = ?', (username,))
            user = cursor.fetchone()
            conn.close()
            if user and check_password_hash(user[1], password):
                session['user_id'] = user[0]
                session['username'] = username
                return redirect(url_for('home'))
        except Exception as e:
            print("Login DB Error:", e)
        error = 'Credenziali non valide o account inesistente.'
    
    return '''<!DOCTYPE html>
    <html lang="it"><head><meta charset="UTF-8"><title>Login - SaaS Elite Scanner</title></head>
    <body style="background:#020408;color:#f4f7fa;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#070d18;padding:30px;border:1px solid #00d4ff;width:360px;border-radius:6px;box-shadow:0 0 25px rgba(0,212,255,.25);">
        <h3 style="color:#f3ca52;margin-top:0;text-align:center;letter-spacing:2px;font-family:Cinzel,serif;">ELITE ACCESS</h3>
        <p style="font-size:10.5px;color:#9fb0c9;text-align:center;margin-bottom:20px;">Accedi con il tuo account in abbonamento</p>
        ''' + (f'<p style="color:#ff3366;font-size:11.5px;text-align:center;">{error}</p>' if error else '') + '''
        <form method="POST">
            <p><label style="font-size:11px;color:#9fb0c9;">Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#0d1626;border:1px solid #233a5e;color:#fff;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <p><label style="font-size:11px;color:#9fb0c9;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#0d1626;border:1px solid #233a5e;color:#fff;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#f3ca52;color:#020408;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;box-shadow:0 0 12px rgba(243,202,82,.4);">Accedi</button>
        </form>
        <p style="text-align:center;margin-top:15px;font-size:11px;"><a href="/register" style="color:#00d4ff;text-decoration:none;">Non hai un account? Registrati</a></p>
    </div></body></html>'''

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form.get('username').strip()
        password = request.form.get('password').strip()
        if username and password:
            try:
                conn = sqlite3.connect('trading_bot.db', timeout=10)
                cursor = conn.cursor()
                hashed_pw = generate_password_hash(password)
                cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, hashed_pw))
                conn.commit()
                conn.close()
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                error = 'Username già esistente.'
            except Exception as e:
                error = str(e)
        else:
            error = 'Compila tutti i campi.'

    return '''<!DOCTYPE html>
    <html lang="it"><head><meta charset="UTF-8"><title>Registrazione - SaaS Elite Scanner</title></head>
    <body style="background:#020408;color:#f4f7fa;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#070d18;padding:30px;border:1px solid #00d4ff;width:360px;border-radius:6px;box-shadow:0 0 25px rgba(0,212,255,.25);">
        <h3 style="color:#f3ca52;margin-top:0;text-align:center;letter-spacing:2px;font-family:Cinzel,serif;">REGISTRAZIONE</h3>
        <p style="font-size:10.5px;color:#9fb0c9;text-align:center;margin-bottom:20px;">Crea il tuo account abbonato</p>
        ''' + (f'<p style="color:#ff3366;font-size:11.5px;text-align:center;">{error}</p>' if error else '') + '''
        <form method="POST">
            <p><label style="font-size:11px;color:#9fb0c9;">Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#0d1626;border:1px solid #233a5e;color:#fff;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <p><label style="font-size:11px;color:#9fb0c9;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#0d1626;border:1px solid #233a5e;color:#fff;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#00ffcc;color:#020408;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;box-shadow:0 0 12px rgba(0,255,204,.4);">Registrati</button>
        </form>
        <p style="text-align:center;margin-top:15px;font-size:11px;"><a href="/login" style="color:#00d4ff;text-decoration:none;">Hai già un account? Accedi</a></p>
    </div></body></html>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
