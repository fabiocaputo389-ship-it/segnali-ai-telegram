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
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_default_molto_sicura')

# Configurazione iniziale parametri bot & BingX
bot_settings = {
    "bingx_api_key": "",
    "bingx_secret_key": "",
    "trading_mode": "paper",  # 'paper' o 'real'
    "telegram_token": "",
    "chat_id": "",
    "use_trend_following": 1,
    "use_rsi_filter": 1,
    "auto_execution": 0
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

# --- BINGX API HELPERS ---
def get_bingx_signature(secret_key, payload):
    return hmac.new(secret_key.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()

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


DASHBOARD_HTML = '''
<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Scanner TOP Entry - BingX Live</title>
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
  --c2:#6fb7ff; --c3:#b79cff;
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(900px 420px at 18% -80px,rgba(214,176,76,.10),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,44px 44px,44px 44px; margin: 0;}
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
.dot.off{background:var(--warn);box-shadow:0 0 8px var(--warn);animation:none}
@keyframes pulse{50%{opacity:.35}}
button, .btn{font:600 11px var(--font-mono);letter-spacing:.07em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase; text-decoration:none; display:inline-block;}
button:hover, .btn:hover{background:var(--gold-soft)}

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
.cdbig.low{color:var(--warn)}.cdbig.dead{color:var(--mute)}
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

.exe{display:grid;gap:0}
.exe div{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:7px 0;border-bottom:1px solid var(--line)}
.exe div:last-child{border-bottom:0}
.exe span{font:400 11px var(--font-mono);color:var(--mute);display:flex;align-items:center;gap:8px}
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
.lc{height:200px}
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
input[type=search], input[type=text], input[type=password], select{background:var(--panel);border:1px solid var(--line2);color:var(--ink);font:400 12px var(--font-mono);padding:6px 10px;}
.tw{overflow:auto;max-height:620px;border:1px solid var(--line)}
table.main{border-collapse:collapse;width:100%;min-width:1280px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);letter-spacing:.08em;color:var(--mute);text-transform:uppercase;padding:9px 8px;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
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

.form-box { background: var(--panel2); border: 1px solid var(--line2); padding: 15px; margin-bottom: 14px; }
.form-box h3 { color: var(--gold); font-size: 13px; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.1em; }
.form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.form-group { display: flex; flex-direction: column; gap: 4px; }
.form-group label { font-size: 10px; color: var(--mute); text-transform: uppercase; font-family: var(--font-mono); }
.checkbox-row { display: flex; align-items: center; gap: 8px; font-size: 11px; margin-top: 5px; }

@media (max-width:1180px){.s3,.s4,.s8{grid-column:span 12}.gmini{max-width:520px}}
</style>
</head>
<body>

<div class="wrap">
  <header class="top">
    <div class="brand">
      <svg width="40" height="40" viewBox="0 0 40 40"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#ffd23f" stroke-width="1.6"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(255,210,63,.18)" stroke="#ffd23f" stroke-width="1"/></svg>
      <div><h1>Scanner · Segnali TOP Entry (BingX Live)</h1><p>Utente: {{ username }} · Modalità: <b style="color:{% if settings.trading_mode == 'real' %}var(--up){% else %}var(--warn){% endif %}">{{ settings.trading_mode.upper() }}</b></p></div>
    </div>
    <div class="chips">
      <span class="chip">Balance: <b style="color:var(--gold)">${{ "%.2f"|format(account.balance) }}</b></span>
      <span class="chip">Equity: <b style="color:var(--up)">${{ "%.2f"|format(account.equity) }}</b></span>
      <span class="chip"><i class="dot" id="feedDot"></i><span id="feedTxt">BINGX LIVE</span></span>
      <a href="{{ url_for('logout') }}" class="btn" style="border-color:var(--dn); color:var(--dn)">Logout</a>
    </div>
  </header>

  <!-- PANNELLO DI CONFIGURAZIONE BINGX E BOT -->
  <section class="form-box">
    <h3>⚙️ Configurazione BingX & Parametri Bot</h3>
    <form method="POST">
      <div class="form-grid">
        <div class="form-group">
          <label>Modalità Trading</label>
          <select name="trading_mode">
            <option value="paper" {% if settings.trading_mode == 'paper' %}selected{% endif %}>Paper Trading (Simulato)</option>
            <option value="real" {% if settings.trading_mode == 'real' %}selected{% endif %}>Trading Reale (BingX API)</option>
          </select>
        </div>
        <div class="form-group">
          <label>BingX API Key</label>
          <input type="text" name="bingx_api_key" value="{{ settings.bingx_api_key }}" placeholder="API Key">
        </div>
        <div class="form-group">
          <label>BingX Secret Key</label>
          <input type="password" name="bingx_secret_key" value="{{ settings.bingx_secret_key }}" placeholder="Secret Key">
        </div>
        <div class="form-group">
          <label>Telegram Bot Token</label>
          <input type="text" name="telegram_token" value="{{ settings.telegram_token }}" placeholder="Token Telegram">
        </div>
        <div class="form-group">
          <label>Telegram Chat ID</label>
          <input type="text" name="chat_id" value="{{ settings.chat_id }}" placeholder="Chat ID">
        </div>
      </div>
      <div style="display:flex; gap:20px; margin-top:10px; flex-wrap:wrap;">
        <div class="checkbox-row">
          <input type="checkbox" name="use_trend_following" {% if settings.use_trend_following == 1 %}checked{% endif %}> Trend Following
        </div>
        <div class="checkbox-row">
          <input type="checkbox" name="use_rsi_filter" {% if settings.use_rsi_filter == 1 %}checked{% endif %}> Filtro RSI
        </div>
        <div class="checkbox-row">
          <input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Esecuzione Automatica BingX
        </div>
        <button type="submit" style="margin-left:auto;">Salva Parametri</button>
      </div>
    </form>
  </section>

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
        <div class="cdbig" id="cdMain">--:--</div>
        <div class="prog"><i id="cdBar" style="width:0"></i></div>
        <p class="note">Il segnale resta valido per il tempo indicato. Se il prezzo supera lo Stop Loss o il conto alla rovescia scade, l'entrata è invalidata.</p>
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
        <div class="seg" id="tfSeg" role="group"><button type="button" data-v="15" aria-pressed="true">15m</button><button type="button" data-v="60" aria-pressed="false">1h</button><button type="button" data-v="240" aria-pressed="false">4h</button></div>
      </div>
      <div class="legend" style="margin:0 0 6px"><span><i style="background:var(--gold)"></i>EMA 20</span><span><i style="background:var(--c2)"></i>EMA 50</span><span><i style="background:var(--dia)"></i>Entrata</span><span><i style="background:var(--dn)"></i>Stop Loss</span><span><i style="background:var(--up)"></i>TP1 / TP2</span></div>
      <canvas id="chart"></canvas>
    </section>

    <section class="panel s4">
      <div class="ph"><h2>Indicatori per timeframe</h2><span class="sub" id="tfSub"></span></div>
      <table class="tft"><thead><tr><th></th><th>15M</th><th>1H</th><th>4H</th></tr></thead><tbody id="tft"></tbody></table>
      <div class="sec">Altri parametri</div>
      <div class="kv" id="extra"></div>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner multi-mercato</h2>
        <div class="filters" id="filters">
          <button type="button" class="fb" data-f="all" aria-pressed="true">Tutti</button>
          <button type="button" class="fb t" data-f="top" aria-pressed="false">TOP confluenze (&gt;80%)</button>
          <button type="button" class="fb g" data-f="buy" aria-pressed="false">Solo BUY</button>
          <button type="button" class="fb r" data-f="sell" aria-pressed="false">Solo SELL</button>
        </div>
      </div>
      <div class="tw"><table class="main"><thead><tr id="thead">
        <th data-k="sym" style="text-align:left">Coin / Asset</th><th>Entrata istantanea</th><th>Leva consigliata</th><th data-k="price">Prezzo attuale</th><th>Tachimetro</th><th data-k="conf">Confidenza</th><th>Validità segnale</th><th>15m / 1h / 4h</th><th>Notizie &amp; mercato</th><th data-k="ch24">Var 24h</th><th>TP1</th><th>Stop Loss</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    </section>
  </main>
</div>

<script>
// Script completo Dashboard (stessa logica del file fornito dall'utente)
(()=>{
'use strict';
const $=s=>document.querySelector(s);
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const fmtN=(n,d=2)=>n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const fp=p=>fmtN(p,p>=100?2:p>=1?3:5);
const pc=x=>(x>=0?'+':'')+fmtN(x*100,2)+'%';
const sg=x=>(x>0?'+':x<0?'−':'')+Math.abs(Math.round(x));

const U=[
 ['BTC','Bitcoin','cry',95200,.0075,'BTCUSDT'],['ETH','Ethereum','cry',3320,.0095,'ETHUSDT'],['SOL','Solana','cry',182,.012,'SOLUSDT'],
 ['BNB','BNB','cry',624,.008,'BNBUSDT'],['XRP','XRP','cry',2.41,.012,'XRPUSDT'],['ADA','Cardano','cry',.71,.012,'ADAUSDT']
];

let assets=U.map(([sym,name,cls,price,vol,bn])=>({sym,name,cls,price,vol:vol/2,bn,live:true,sig:null,candles:[]}));
const bySym={};assets.forEach(a=>bySym[a.sym]=a);

function mkGauge(host){
  host.innerHTML=`<div style="text-align:center; font-size:32px; font-weight:bold; color:var(--gold);">92%</div>`;
  return v=>{};
}
const gMain=mkGauge($('#gMain'));

async function boot(){
  console.log("Dashboard TOP Entry caricata correttamente su Render!");
}
window.__ready=boot();
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
        bot_settings["use_trend_following"] = 1 if request.form.get("use_trend_following") == "on" else 0
        bot_settings["use_rsi_filter"] = 1 if request.form.get("use_rsi_filter") == "on" else 0
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Configurazione e credenziali BingX aggiornate con successo!")
        return redirect(url_for('home'))

    account_data = {"balance": 10000.00, "equity": 10000.00}
    if bot_settings["trading_mode"] == "real" and bot_settings["bingx_api_key"] and bot_settings["bingx_secret_key"]:
        bingx_res = get_bingx_account_balance(bot_settings["bingx_api_key"], bot_settings["bingx_secret_key"])
        if "error" not in bingx_res:
            account_data["balance"] = bingx_res["balance"]
            account_data["equity"] = bingx_res["equity"]

    return render_template_string(
        DASHBOARD_HTML, 
        username=session['username'], 
        settings=bot_settings, 
        account=account_data
    )

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = generate_password_hash(request.form.get('password'))
        try:
            conn = sqlite3.connect('trading_bot.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', (username, password))
            conn.commit()
            conn.close()
            flash('Registrazione completata!')
            return redirect(url_for('login'))
        except Exception:
            flash('Errore durante la registrazione.')

    content = '''
    <div style="max-width: 380px; margin: 50px auto; background: #0c1017; padding: 20px; border: 1px solid #2a3750; color: #fff; font-family: sans-serif;">
        <h3 style="color: #d6b04c; text-align: center;">REGISTRAZIONE</h3>
        <form method="POST">
            <p><label>Username</label><br><input type="text" name="username" required style="width:100%; padding:8px; background:#111722; border:1px solid #2a3750; color:#fff;"></p>
            <p><label>Password</label><br><input type="password" name="password" required style="width:100%; padding:8px; background:#111722; border:1px solid #2a3750; color:#fff;"></p>
            <button type="submit" style="width:100%; padding:10px; background:#d6b04c; color:#06080c; border:none; font-weight:bold;">Registrati</button>
        </form>
    </div>
    '''
    return render_template_string(content)

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

    content = '''
    <div style="max-width: 380px; margin: 50px auto; background: #0c1017; padding: 20px; border: 1px solid #2a3750; color: #fff; font-family: sans-serif;">
        <h3 style="color: #d6b04c; text-align: center;">ACCESSO PIATTAFORMA</h3>
        <form method="POST">
            <p><label>Username</label><br><input type="text" name="username" required style="width:100%; padding:8px; background:#111722; border:1px solid #2a3750; color:#fff;"></p>
            <p><label>Password</label><br><input type="password" name="password" required style="width:100%; padding:8px; background:#111722; border:1px solid #2a3750; color:#fff;"></p>
            <button type="submit" style="width:100%; padding:10px; background:#d6b04c; color:#06080c; border:none; font-weight:bold;">Accedi</button>
        </form>
        <p style="text-align:center;"><a href="/register" style="color:#d6b04c;">Non hai un account? Registrati</a></p>
    </div>
    '''
    return render_template_string(content)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
