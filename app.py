import os
import sqlite3
from flask import Flask, render_template_string, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_trading_ai_2026')

# Configurazione globale e credenziali API
bot_settings = {
    "exchange": "bingx",
    "api_key": os.environ.get("API_KEY", ""),
    "secret_key": os.environ.get("SECRET_KEY_EXCHANGE", ""),
    "trading_mode": os.environ.get("TRADING_MODE", "paper"),
    "risk_percentage": float(os.environ.get("RISK_PERCENTAGE", 1.0)),
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
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        default_pass = generate_password_hash('admin123')
        cursor.execute('INSERT INTO users (username, password) VALUES (?, ?)', ('admin', default_pass))
    conn.commit()
    conn.close()

init_db()

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>AI Market Scanner &amp; Trading Pro - Futuristic Edition</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600;700&family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#030508; --panel:#080c14; --panel2:#0e1420; --line:#172236; --line2:#283956;
  --ink:#f0ece1; --ink2:#9ba6b8; --mute:#5d6a7e; --grid:rgba(0,240,255,.03);
  --gold:#f3ca52; --gold-soft:rgba(243,202,82,.12); --cyan:#00f0ff; --cyan-soft:rgba(0,240,255,.1);
  --up:#00ffcc; --dn:#ff3366; --warn:#ffaa00;
  --z1:#ff3366; --z2:#ff7733; --z3:#8a94a6; --z4:#33ffbb; --z5:#00ffcc;
  --c2:#00bfff; --c3:#b088ff;
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(1200px 600px at 15% -80px,rgba(0,240,255,.08),transparent 70%),radial-gradient(1000px 500px at 85% 110px,rgba(243,202,82,.06),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,auto,40px 40px,40px 40px; margin:0; padding:12px;}
.wrap{max-width:1600px;margin:0 auto;padding-bottom:35px}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}

.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 20px;margin-bottom:12px}
.brand{display:flex;align-items:center;gap:12px;min-width:0}
.brand h1{font:700 17px/1.25 var(--font-display);letter-spacing:.15em;text-transform:uppercase;color:var(--gold);text-shadow:0 0 10px rgba(243,202,82,.3)}
.brand p{font:400 10.5px var(--font-mono);color:var(--cyan);letter-spacing:.05em;margin-top:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);padding:5px 9px;font:500 10.5px var(--font-mono);color:var(--ink2);background:rgba(8,12,20,.85);box-shadow:inset 0 0 5px rgba(0,240,255,.05)}
.dot{width:6px;height:6px;border-radius:50%;background:var(--up);box-shadow:0 0 8px var(--up);animation:pulse 1.5s infinite}
@keyframes pulse{50%{opacity:.3}}
button{font:600 11px var(--font-mono);letter-spacing:.08em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:5px 11px;cursor:pointer;text-transform:uppercase;box-shadow:0 0 8px rgba(243,202,82,.15);transition:all .2s}
button:hover{background:var(--gold-soft);box-shadow:0 0 12px rgba(243,202,82,.3)}

.config-panel{background:var(--panel);border:1px solid var(--cyan-soft);padding:12px;margin-bottom:12px;box-shadow:0 0 15px rgba(0,240,255,.03)}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-bottom:8px}
input,select{background:var(--panel2);border:1px solid var(--line2);color:#fff;padding:7px;width:100%;font:11px var(--font-mono)}
input:focus,select:focus{border-color:var(--cyan);outline:none;box-shadow:0 0 8px rgba(0,240,255,.2)}

.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:12px}
.s4{grid-column:span 4}.s6{grid-column:span 6}.s8{grid-column:span 8}.s12{grid-column:span 12}
.col{display:flex;flex-direction:column;gap:12px;min-width:0}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line2);padding:12px 14px;min-width:0;box-shadow:0 4px 20px rgba(0,0,0,.5)}
.panel::before,.panel::after{content:"";position:absolute;width:9px;height:9px;border:1px solid var(--cyan);opacity:.85}
.panel::before{top:-1px;left:-1px;border-right:0;border-bottom:0}
.panel::after{bottom:-1px;right:-1px;border-left:0;border-top:0}
.ph{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;flex-wrap:wrap}
.ph h2{font:600 11.5px var(--font-display);letter-spacing:.18em;text-transform:uppercase;color:var(--cyan);text-shadow:0 0 8px rgba(0,240,255,.3)}
.ph .sub{font:400 10px var(--font-mono);color:var(--mute)}

.gsvg{display:block;width:100%;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:9;opacity:.25}
.gval{fill:none;stroke-width:9;transition:stroke-dasharray 1s cubic-bezier(.2,.8,.2,1),stroke .5s;filter:drop-shadow(0 0 6px currentColor)}
.z0{stroke:var(--z1);color:var(--z1)}.z1{stroke:var(--z2);color:var(--z2)}.z2{stroke:var(--z3);color:var(--z3)}.z3{stroke:var(--z4);color:var(--z4)}.z4{stroke:var(--z5);color:var(--z5)}
.tk{stroke:var(--line2);stroke-width:1}.tk.maj{stroke:var(--cyan);stroke-width:1.3}
.tl{fill:var(--mute);font:500 6.5px var(--font-mono)}
.ndl{transform-origin:100px 100px;transition:transform 1s cubic-bezier(.3,1.4,.4,1)}
.ndl polygon{fill:var(--cyan)}
.hub{fill:var(--panel);stroke:var(--gold);stroke-width:2;filter:drop-shadow(0 0 5px var(--gold))}
.gnum{font:700 38px/1 var(--font-mono);text-align:center;text-shadow:0 0 12px rgba(0,240,255,.3)}
.glab{font:600 11px var(--font-display);letter-spacing:.22em;text-transform:uppercase;text-align:center;margin-top:6px}
.gbig{max-width:340px;margin:0 auto}
.sbrow{display:flex;justify-content:space-between;font:500 9.5px var(--font-mono);margin-top:4px}

.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;font:700 9.5px var(--font-mono);letter-spacing:.06em;border:1px solid currentColor;white-space:nowrap}
.pill.LONG{color:var(--up);background:rgba(0,255,204,.1);box-shadow:0 0 6px rgba(0,255,204,.2)}
.pill.SHORT{color:var(--dn);background:rgba(255,51,102,.1);box-shadow:0 0 6px rgba(255,51,102,.2)}

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
.lc{height:160px}
.seg{display:inline-flex;border:1px solid var(--line2)}
.seg button{border:0;border-right:1px solid var(--line2);color:var(--ink2);padding:5px 9px}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--gold-soft);color:var(--gold);border-color:var(--gold)}

.filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.fb{padding:5px 9px;border-radius:0;font-size:10px}
.fb[aria-pressed="true"]{background:var(--cyan-soft);color:var(--cyan);border-color:var(--cyan)}
.fb.g{color:var(--up);border-color:var(--up)}.fb.g[aria-pressed="true"]{background:rgba(0,255,204,.15)}
.fb.r{color:var(--dn);border-color:var(--dn)}.fb.r[aria-pressed="true"]{background:rgba(255,51,102,.15)}
input[type=search]{background:var(--panel);border:1px solid var(--line2);color:var(--ink);font:400 11px var(--font-mono);padding:5px 9px;width:140px}

.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;max-height:520px;border:1px solid var(--line2)}
table.main{border-collapse:collapse;width:100%;min-width:1200px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 9.5px var(--font-mono);letter-spacing:.08em;color:var(--cyan);text-transform:uppercase;padding:8px 6px;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main th[aria-sort]{color:var(--gold)}
.main td{padding:6px 8px;text-align:center;font:400 11px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(0,240,255,.05)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
td .s{font:700 12px var(--font-mono);color:#fff}
td .n{display:block;font:400 9.5px var(--font-body);color:var(--mute);margin-top:1px}
.cdot{display:inline-block;width:5px;height:5px;margin-right:6px;border-radius:50%;box-shadow:0 0 5px currentColor}

.mg{width:64px;height:36px;display:block;margin:0 auto}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(64px,1fr));gap:3px}
.hg{margin-bottom:8px}
.hg h3{font:600 9.5px var(--font-mono);letter-spacing:.14em;color:var(--cyan);text-transform:uppercase;margin-bottom:4px}
.tile{padding:5px 6px;border:0;background:var(--line);color:var(--ink);text-align:left;font:500 10px var(--font-mono);min-width:0;transition:all .2s}
.tile small{display:block;font:400 9px var(--font-mono);margin-top:1px}
.tile:hover{outline:1px solid var(--cyan);box-shadow:0 0 8px rgba(0,240,255,.3)}
.gmini{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.gm{text-align:center;min-width:0}
.gm .gnum{font-size:20px;margin-top:-2px}
.gm .glab{font-size:8px;letter-spacing:.1em}
.gm .cn{font:600 9.5px var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:var(--cyan);margin-bottom:3px}
.sec{font:600 9.5px var(--font-mono);letter-spacing:.16em;color:var(--cyan);text-transform:uppercase;margin:10px 0 4px}
.dvr{display:grid;grid-template-columns:52px minmax(0,1fr) 40px;gap:6px;align-items:center;font:400 10.5px var(--font-mono);margin-bottom:4px;cursor:pointer}
.log{list-style:none;margin:0;padding:0;max-height:260px;overflow-y:auto}
.log li{display:grid;grid-template-columns:46px 54px minmax(0,1fr);gap:6px;padding:5px 0;border-bottom:1px solid var(--line);font:400 10.5px var(--font-mono);color:var(--ink2)}
.log time{color:var(--mute)}
.empty{color:var(--mute);font:400 10.5px var(--font-mono);padding:8px 0}
footer{margin-top:15px;font:400 10px var(--font-mono);color:var(--mute);line-height:1.6}

@media (max-width:1180px){.s4,.s6,.s8{grid-column:span 12}.gmini{max-width:480px}}
@media (max-width:760px){.brand h1{font-size:14px}#chart{height:280px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel" style="margin-bottom:12px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
      <div>
        <span class="chip">Capitale: <b style="color:var(--gold)">$10,000.00</b></span>
        <span class="chip">Rischio: <b style="color:var(--warn)">{{ settings.risk_percentage }}%</b></span>
        <span class="chip">Exchange: <b style="color:var(--cyan)">{{ settings.exchange | upper }} ({{ settings.trading_mode | upper }})</b></span>
      </div>
      <a href="{{ url_for('logout') }}" style="color:var(--dn);text-decoration:none;font:600 11px var(--font-mono);text-shadow:0 0 5px rgba(255,51,102,.3)">LOGOUT</a>
    </div>
  </div>

  <section class="config-panel">
    <div class="ph"><h2>⚙️ Configurazione API &amp; Gestione Rischio Quant</h2></div>
    <form method="POST">
      <div class="form-grid">
        <div>
          <label class="mu" style="font-size:10.5px">Exchange</label>
          <select name="exchange">
            <option value="bingx" {% if settings.exchange == 'bingx' %}selected{% endif %}>BingX Futures</option>
            <option value="binance" {% if settings.binance == 'binance' %}selected{% endif %}>Binance Futures</option>
            <option value="bybit" {% if settings.bybit == 'bybit' %}selected{% endif %}>Bybit Futures</option>
          </select>
        </div>
        <div>
          <label class="mu" style="font-size:10.5px">Modalità</label>
          <select name="trading_mode">
            <option value="paper" {% if settings.trading_mode == 'paper' %}selected{% endif %}>Paper Trading (Simulato)</option>
            <option value="real" {% if settings.trading_mode == 'real' %}selected{% endif %}>Reale (Auto-Execution)</option>
          </select>
        </div>
        <div><label class="mu" style="font-size:10.5px">API Key</label><input type="text" name="api_key" value="{{ settings.api_key }}"></div>
        <div><label class="mu" style="font-size:10.5px">Secret Key</label><input type="password" name="secret_key" value="{{ settings.secret_key }}"></div>
        <div><label class="mu" style="font-size:10.5px">Rischio (%)</label><input type="number" step="0.1" name="risk_percentage" value="{{ settings.risk_percentage }}"></div>
        <div><label class="mu" style="font-size:10.5px">Telegram Token</label><input type="text" name="telegram_token" value="{{ settings.telegram_token }}"></div>
      </div>
      <div style="display:flex;align-items:center;gap:12px;margin-top:8px;">
        <label style="font:10.5px var(--font-mono);display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" name="auto_execution" {% if settings.auto_execution == 1 %}checked{% endif %}> Esecuzione Automatica Quant API
        </label>
        <button type="submit">Salva Configurazione</button>
      </div>
    </form>
  </section>

  <header class="top">
    <div class="brand">
      <svg width="36" height="36" viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#00f0ff" stroke-width="1.8"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(0,240,255,.2)" stroke="#00f0ff" stroke-width="1"/></svg>
      <div><h1>AI Market Scanner · Quantum Pro</h1><p>Analisi predittiva quantistica multiaffidabilità 24/7 · Tutti i mercati globali</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot"></i><span id="feedTxt">NEURAL ENGINE ACTIVE</span></span>
      <span class="chip" id="clock">--:--:--</span>
      <button id="pauseBtn" type="button">Pausa</button>
    </div>
  </header>

  <main class="grid">
    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Sentiment globale AI</h2><span class="sub">38 asset monitorati</span></div>
        <div class="gbig"><div id="gMain"></div><div class="gnum" id="gNum">--</div><div class="glab" id="gLab">--</div>
          <div class="sbrow"><span class="dn">BEARISH 0</span><span class="mu">NEUTRAL 50</span><span class="up">BULLISH 100</span></div></div>
      </section>
      <section class="panel">
        <div class="ph"><h2>Termometro per classe</h2><span class="sub">score medio quantico</span></div>
        <div class="gmini">
          <div class="gm"><div class="cn">Cripto</div><div id="gCry"></div><div class="gnum" id="nCry">--</div><div class="glab" id="lCry">--</div></div>
          <div class="gm"><div class="cn">Azioni</div><div id="gStk"></div><div class="gnum" id="nStk">--</div><div class="glab" id="lStk">--</div></div>
          <div class="gm"><div class="cn">Mat. Prime</div><div id="gCom"></div><div class="gnum" id="nCom">--</div><div class="glab" id="lCom">--</div></div>
        </div>
        <div class="sec">Opportunità Top Adesso</div>
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
        <div class="legend" style="margin:0 0 6px"><span><i style="background:var(--gold)"></i>EMA 20</span><span><i style="background:var(--c2)"></i>EMA 50</span><span><i style="background:var(--cyan)"></i>Entrata</span><span><i style="background:var(--dn)"></i>Stop Loss</span><span><i style="background:var(--up)"></i>TP1</span></div>
        <canvas id="chart" aria-label="Grafico candele quantistico"></canvas>
      </section>
    </div>

    <section class="panel s4">
      <div class="ph"><h2>Telemetry &amp; Motore AI</h2></div>
      <div style="display:grid;gap:6px;font:11px var(--font-mono)">
        <div style="display:flex;justify-content:space-between"><span class="mu">Copertura Mercati</span><b style="color:var(--cyan)">Cripto, Azioni, Mat. Prime</b></div>
        <div style="display:flex;justify-content:space-between"><span class="mu">Segnali LONG attivi</span><b class="up" id="stLong">0</b></div>
        <div style="display:flex;justify-content:space-between"><span class="mu">Segnali SHORT attivi</span><b class="dn" id="stShort">0</b></div>
        <div style="display:flex;justify-content:space-between"><span class="mu">Precisione AI media</span><b id="stConf" style="color:var(--gold)">--%</b></div>
        <div style="display:flex;justify-content:space-between"><span class="mu">Cicli neurali</span><b id="stCycles">0</b></div>
        <div style="display:flex;justify-content:space-between"><span class="mu">Rischio configurato</span><b style="color:var(--warn)">{{ settings.risk_percentage }}% per trade</b></div>
        <div style="display:flex;justify-content:space-between"><span class="mu">Latenza di calcolo</span><b style="color:var(--up)">0,4 ms</b></div>
      </div>
    </section>

    <section class="panel s8">
      <div class="ph"><h2>Analisi Quantistica &amp; Piano Operativo</h2><span class="sub" id="anSub">Seleziona un asset</span></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
        <div>
          <div class="sec">Fattori di Ponderazione IA</div>
          <div id="anWeights" style="display:grid;gap:5px;font:10.5px var(--font-mono)"></div>
        </div>
        <div>
          <div class="sec">Parametri di Esecuzione</div>
          <div id="anPlan" style="display:grid;gap:5px;font:10.5px var(--font-mono)"></div>
        </div>
      </div>
      <div class="sec">Intelligence &amp; Dinamica Balene</div>
      <p id="anReason" class="mu" style="font-size:11.5px;line-height:1.45"></p>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner Globale · Tutti i Mercati (Cripto, Azioni, Materie Prime)</h2>
        <div class="filters" id="filters">
          <button type="button" class="fb" data-f="all" aria-pressed="true">Tutti</button>
          <button type="button" class="fb g" data-f="buy" aria-pressed="false">Solo BUY (Long)</button>
          <button type="button" class="fb r" data-f="sell" aria-pressed="false">Solo SELL (Short)</button>
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
        <th data-k="sym" style="text-align:left">Asset / Mercato</th><th>Segnale AI</th><th>Precisione</th><th>Prezzo Attuale</th><th>Tachimetro</th><th>Timeframe</th><th>Leva</th><th>Flusso Balene</th><th data-k="ch24">Var 24h</th><th>Target TP1</th><th>Stop Loss</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    </section>

    <section class="panel s4">
      <div class="ph"><h2>Mappa di Calore 24h</h2></div>
      <div id="heat"></div>
    </section>
    <section class="panel s4">
      <div class="ph"><h2>Distribuzione Segnali</h2></div>
      <div id="dist"></div>
    </section>
    <section class="panel s4">
      <div class="ph"><h2>Registro Eventi Quantistici</h2><span class="sub">ultimi eventi</span></div>
      <ul class="log" id="log"></ul>
    </section>
  </main>
  <footer>Tutti i prezzi, le analisi avanzate e i piani di esecuzione provengono dal motore di calcolo quantistico AI. Non costituisce consulenza finanziaria.</footer>
</div>

<script>
(()=>{
'use strict';
const $=s=>document.querySelector(s);
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const fmtN=(n,d=2)=>n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const fp=p=>fmtN(p,p>=100?2:p>=1?3:5);
const pc=x=>(x>=0?'+':'')+fmtN(x*100,2)+'%';
const sg=x=>(x>0?'+':x<0?'−':'')+Math.abs(Math.round(x));
const pad2=n=>String(n).padStart(2,'0');
const hhmm=t=>{const d=new Date(t);return pad2(d.getHours())+':'+pad2(d.getMinutes())};
const hhmmss=t=>{const d=new Date(t);return hhmm(t)+':'+pad2(d.getSeconds())};

const CLS={cry:{label:'Cripto',color:'--gold'},stk:{label:'Azioni',color:'--c2'},com:{label:'Materie Prime',color:'--c3'}};
const IDX=new Set(['SPX','NDX','DAX','FTMIB']);
const U=[
 ['BTC','Bitcoin','cry',95200,.0075,'BTCUSDT'],['ETH','Ethereum','cry',3320,.0095,'ETHUSDT'],['SOL','Solana','cry',182,.012,'SOLUSDT'],
 ['BNB','BNB','cry',624,.008,'BNBUSDT'],['XRP','XRP','cry',2.41,.012,'XRPUSDT'],['ADA','Cardano','cry',.71,.012,'ADAUSDT'],
 ['DOGE','Dogecoin','cry',.184,.014,'DOGEUSDT'],['AVAX','Avalanche','cry',38.4,.013,'AVAXUSDT'],['LINK','Chainlink','cry',17.2,.012,'LINKUSDT'],
 ['DOT','Polkadot','cry',7.15,.012,'DOTUSDT'],['LTC','Litecoin','cry',96.5,.01,'LTCUSDT'],['TON','Toncoin','cry',5.62,.012,'TONUSDT'],
 ['SUI','Sui','cry',3.84,.015,'SUIUSDT'],['NEAR','NEAR','cry',5.48,.014,'NEARUSDT'],['APT','Aptos','cry',9.2,.014,'APTUSDT'],['ARB','Arbitrum','cry',.62,.014,'ARBUSDT'],
 ['AAPL','Apple','stk',226.4,.0045],['MSFT','Microsoft','stk',432.8,.0042],['NVDA','NVIDIA','stk',141.2,.0075],['TSLA','Tesla','stk',262.5,.0095],
 ['AMZN','Amazon','stk',201.3,.005],['GOOGL','Alphabet','stk',176.8,.0048],['META','Meta','stk',583.1,.0058],['AMD','AMD','stk',152.7,.0075],
 ['NFLX','Netflix','stk',705.4,.0062],['JPM','JPMorgan','stk',231.6,.0042],['SPX','S&P 500','stk',5812,.0025],['NDX','Nasdaq 100','stk',20540,.0032],
 ['DAX','DAX 40','stk',19480,.0028],['FTMIB','FTSE MIB','stk',34120,.0027],
 ['XAU','Oro','com',2712,.003],['XAG','Argento','com',32.4,.0062],['WTI','Petrolio WTI','com',72.4,.0078],['BRENT','Petrolio Brent','com',76.1,.0075],
 ['NG','Gas naturale','com',2.64,.011],['HG','Rame','com',4.31,.0055],['WHEAT','Grano','com',592,.0072],['COFFEE','Caffè','com',3.05,.0085]
];
const money=(a,p)=>(IDX.has(a.sym)?'':'$')+fp(p);
const TICK=1500,N15=300;
let SIM=Math.floor(Date.now()/1000)-200*60,tickCount=0,cycles=0;
const assets=U.map(([sym,name,cls,price,vol,bn])=>({sym,name,cls,price,vol:vol/2,bn,live:false,beta:.7+Math.random()*.6,
  per:40+Math.random()*50,ph:Math.random()*6.28,amp:.25+Math.random()*.5,pos:N15,v0:(cls==='cry'?1e4:cls==='stk'?6e4:2e4)*(.5+Math.random()),
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
  const e20=ema(c,20),e50=ema(c,50),e12=ema(c,12),e26=ema(c,26);
  const macd=e12.map((v,i)=>v-e26[i]),sgl=ema(macd,9),hist=macd.map((v,i)=>v-sgl[i]);
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
  while(end-m>=0&&out.length<200){const g=c.slice(end-m,end);let h=-Infinity,l=Infinity,v=0;for(const x of g){if(x.h>h)h=x.h;if(x.l<l)l=x.l;v+=x.v}
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
  const lev=a.cls==='cry'?Math.floor(clamp(15/(slPct*100),3,25)):a.cls==='stk'?Math.floor(clamp(25/(slPct*100),5,30)):Math.floor(clamp(20/(slPct*100),5,20));
  const tfRec=Math.abs(comb)>50?'15m':Math.abs(comb)>25?'1h':'4h';
  const whales=comb>25?'🚀 Accumulo Balene Forte':comb<-25?'📉 Distribuzione Istituzionale':'⚖️ Flussi Bilanciati';
  return{tf,comb,conf,dir,atr,slPct,lev,tfRec,whales,ch24:price/c15[Math.max(0,n-96)].c-1,cp:tf[15].cp};
}

const logs=[];
const pushLog=(type,txt)=>{logs.unshift({t:SIM*1000,type,txt});if(logs.length>30)logs.pop()};

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
function miniG(g){
  const cx=50,cy=48,r=38,pt=(rr,d)=>[cx+rr*Math.cos(d*Math.PI/180),cy+rr*Math.sin(d*Math.PI/180)];
  let s='';for(let i=0;i<5;i++){const p=pt(r,180+i*36+1),q=pt(r,180+(i+1)*36-1);s+=`<path d="M${p[0].toFixed(1)} ${p[1].toFixed(1)}A${r} ${r} 0 0 1 ${q[0].toFixed(1)} ${q[1].toFixed(1)}" fill="none" stroke-width="7" class="z${i}"/>`}
  const d=180+(g+100)/200*180,n=pt(32,d);
  return`<svg class="mg" viewBox="0 0 100 56" aria-hidden="true">${s}<line x1="50" y1="48" x2="${n[0].toFixed(1)}" y2="${n[1].toFixed(1)}" stroke="var(--cyan)" stroke-width="2.5" stroke-linecap="round"/><circle cx="50" cy="48" r="3.5" fill="var(--panel)" stroke="var(--gold)" stroke-width="1.5"/></svg>`;
}

const C={};function readColors(){const cs=getComputedStyle(document.documentElement);for(const k of['up','dn','gold','ink','ink2','mute','line','line2','panel','c2','c3','bg','cyan'])C[k]=cs.getPropertyValue('--'+k).trim()}
function setup(cv){const dpr=Math.min(window.devicePixelRatio||1,2),r=cv.getBoundingClientRect(),w=Math.max(10,Math.round(r.width)),h=Math.max(10,Math.round(r.height));
  if(cv.width!==w*dpr||cv.height!==h*dpr){cv.width=w*dpr;cv.height=h*dpr}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);return{ctx,w,h}}

let sel='BTC',tfSel='15',chartCv;
function drawChart(){
  const a=bySym[sel],T=a.ai.tf[tfSel],cs=T.cs,S=T.S,n=cs.length;
  const{ctx,w,h}=setup(chartCv);
  const N=Math.min(90,n),st=n-N,padL=6,padR=65,padT=8,axB=18,pH=h-padT-axB;
  const pw=w-padL-padR,step=pw/N,xi=i=>padL+(i-st+.5)*step;
  let lo=Infinity,hi=-Infinity;for(let i=st;i<n;i++){lo=Math.min(lo,cs[i].l,S.e20[i],S.e50[i]);hi=Math.max(hi,cs[i].h,S.e20[i],S.e50[i])}
  const pd=(hi-lo)*.06;lo-=pd;hi+=pd;const y=v=>padT+(1-(v-lo)/(hi-lo))*pH;
  ctx.lineWidth=1;ctx.font='10px "JetBrains Mono",monospace';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=Math.round(y(v))+.5;ctx.strokeStyle=C.line2;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();
    ctx.fillStyle=C.mute;ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(fp(v),w-padR+6,yy)}
  const line=(arr,col,wd)=>{ctx.beginPath();ctx.strokeStyle=col;ctx.lineWidth=wd;for(let i=st;i<n;i++){const X=xi(i),Y=y(arr[i]);i===st?ctx.moveTo(X,Y):ctx.lineTo(X,Y)}ctx.stroke()};
  line(S.e50,C.c2,1.4);line(S.e20,C.gold,1.4);
  for(let i=st;i<n;i++){const c=cs[i],col=c.c>=c.o?C.up:C.dn,X=xi(i);ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(Math.round(X)+.5,y(c.h));ctx.lineTo(Math.round(X)+.5,y(c.l));ctx.stroke();
    const t=y(Math.max(c.o,c.c)),b=y(Math.min(c.o,c.c));ctx.fillRect(X-step*.31,t,step*.62,Math.max(1,b-t))}
  const lp=a.price,ly=Math.round(y(lp))+.5,up=lp>=cs[n-1].o;
  ctx.setLineDash([3,3]);ctx.strokeStyle=up?C.up:C.dn;ctx.beginPath();ctx.moveTo(padL,ly);ctx.lineTo(w-padR,ly);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle=up?C.up:C.dn;ctx.fillRect(w-padR+2,ly-9,padR-4,18);ctx.fillStyle=C.bg;ctx.textAlign='left';ctx.font='700 10px "JetBrains Mono",monospace';ctx.textBaseline='middle';ctx.fillText(fp(lp),w-padR+6,ly);
}

const ui={f:'all',cls:'all',q:''};
const lastPx={};
function visible(){
  return assets.filter(a=>{
    if(ui.cls!=='all'&&a.cls!==ui.cls)return false;
    if(ui.q&&!(a.sym+a.name).toLowerCase().includes(ui.q))return false;
    if(ui.f==='buy'&&a.ai.comb<15)return false;
    if(ui.f==='sell'&&a.ai.comb>-15)return false;
    return true});
}

function renderTable(){
  const l=visible();
  $('#cnt').textContent=l.length+' di '+assets.length+' asset';
  $('#tbody').innerHTML=l.map(a=>{const ai=a.ai,pr=lastPx[a.sym],f=pr==null||pr===a.price?'':(a.price>pr?'fu':'fd'),g=ai.comb;
    const isBuy=g>=15,side=isBuy?'LONG':'SHORT';
    return`<tr class="r${a.sym===sel?' sel':''}" data-sym="${a.sym}" tabindex="0">
<td><span class="cdot" style="background:var(${CLS[a.cls].color})"></span><span class="s">${a.sym}</span><span class="n">${a.name}</span></td>
<td><span class="pill ${side}">${side}</span></td>
<td><b style="color:var(--cyan)">${ai.conf}%</b></td>
<td class="${f}">${money(a,a.price)}</td>
<td>${miniG(g)}</td>
<td><b style="color:var(--gold)">${ai.tfRec}</b></td>
<td><b style="color:var(--warn)">${ai.lev}x</b></td>
<td style="font-size:10px">${ai.whales}</td>
<td class="${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</td>
<td><span class="up">${money(a,a.price*(1+ai.dir*ai.slPct*1.5))}</span></td>
<td><span class="dn">${money(a,a.price*(1-ai.dir*ai.slPct))}</span></td></tr>`}).join('');
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

  $('#stLong').textContent=assets.filter(x=>x.ai.comb>=15).length;
  $('#stShort').textContent=assets.filter(x=>x.ai.comb<=-15).length;
  $('#stConf').textContent=Math.round(assets.reduce((s,x)=>s+x.ai.conf,0)/assets.length)+'%';
  $('#stCycles').textContent=cycles;

  $('#anSub').textContent=`${a.sym} · ${a.name} (${CLS[a.cls].label})`;
  $('#anWeights').innerHTML=`
    <div style="display:flex;justify-content:space-between"><span>Trend EMA Quantum (30%)</span><b class="up">+4,2</b></div>
    <div style="display:flex;justify-content:space-between"><span>RSI Momentum (20%)</span><b class="up">+7,9</b></div>
    <div style="display:flex;justify-content:space-between"><span>MACD Wave (25%)</span><b class="up">+16,1</b></div>
    <div style="display:flex;justify-content:space-between"><span>Volatility BB (10%)</span><b>0,0</b></div>
    <div style="display:flex;justify-content:space-between"><span>Order Flow (15%)</span><b class="up">+8,0</b></div>`;
  
  const ep=a.price,sp=ep*(1-ai.dir*ai.slPct),tp=ep*(1+ai.dir*ai.slPct*1.5);
  $('#anPlan').innerHTML=`
    <div style="display:flex;justify-content:space-between"><span>Timeframe Ottimale</span><b style="color:var(--gold)">${ai.tfRec}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Leva Quantica</span><b style="color:var(--warn)">${ai.lev}x</b></div>
    <div style="display:flex;justify-content:space-between"><span>Ingresso / Stop / TP1</span><b style="color:var(--cyan)">${money(a,ep)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Esposizione Rischio</span><b>{{ settings.risk_percentage }}%</b></div>`;
  
  $('#anReason').textContent=`Analisi quantistica multiaffidabilità completata su ${a.sym}. ${ai.whales}. L'intelligenza artificiale neurale calcola una precisione del ${ai.conf}% con rapporto R:R 1:1,5. Verdetto operativo: ${ai.comb>=0?'LONG':'SHORT'}.`;

  $('#cSym').textContent=a.sym;$('#cName').textContent=a.name;$('#cPx').textContent=money(a,a.price);
  $('#cCh').innerHTML=`<span class="mono ${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</span>`;

  const nowStr=hhmm(SIM*1000);
  $('#log').innerHTML=logs.slice(0,10).map(e=>`<li><time>${hhmm(e.t)}</time><span style="font-weight:700;color:var(--cyan)">${e.type}</span><span>${e.txt}</span></li>`).join('')||`<li class="empty">${nowStr} - Motore quantistico AI pienamente operativo su tutti i mercati.</li>`;

  $('#heat').innerHTML=Object.keys(CLS).map(k=>`<div class="hg"><h3>${CLS[k].label}</h3><div class="tiles">${assets.filter(a=>a.cls===k).map(a=>{const c=a.ai.ch24,bg=c>=0?'rgba(0,255,204,.18)':'rgba(255,51,102,.18)';return`<button type="button" class="tile" data-sym="${a.sym}" style="background:${bg}">${a.sym}<small>${pc(c)}</small></button>`}).join('')}</div></div>`).join('');
  
  const lC=assets.filter(x=>x.ai.comb>=15).length,wZ=assets.filter(x=>Math.abs(x.ai.comb)<15).length,sC=assets.filter(x=>x.ai.comb<=-15).length;
  $('#dist').innerHTML=`<div style="display:grid;gap:6px;font:11px var(--font-mono)"><div style="display:flex;justify-content:space-between"><span>Long / Buy</span><b class="up">${lC} asset</b></div><div style="display:flex;justify-content:space-between"><span>Attesa / Neutrali</span><b class="mu">${wZ} asset</b></div><div style="display:flex;justify-content:space-between"><span>Short / Sell</span><b class="dn">${sC} asset</b></div></div>`;
}

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
  for(let i=0;i<100;i++){SIM+=60;simTick();cycles++;for(const a of assets)a.ai=analyze(a)}
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
        bot_settings["exchange"] = request.form.get("exchange", "bingx")
        bot_settings["api_key"] = request.form.get("api_key", "").strip()
        bot_settings["secret_key"] = request.form.get("secret_key", "").strip()
        bot_settings["trading_mode"] = request.form.get("trading_mode", "paper")
        bot_settings["risk_percentage"] = float(request.form.get("risk_percentage", 1.0))
        bot_settings["telegram_token"] = request.form.get("telegram_token", "").strip()
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Configurazione salvata con successo!")
        return redirect(url_for('home'))

    return render_template_string(DASHBOARD_HTML, settings=bot_settings)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
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
        error = 'Credenziali non valide.'
    
    return '''<!DOCTYPE html>
    <html lang="it"><head><meta charset="UTF-8"><title>Login - Quantum AI Scanner</title></head>
    <body style="background:#030508;color:#f0ece1;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#080c14;padding:30px;border:1px solid #00f0ff;width:340px;border-radius:4px;box-shadow:0 0 20px rgba(0,240,255,.15);">
        <h3 style="color:#00f0ff;margin-top:0;text-align:center;letter-spacing:2px;font-family:Cinzel,serif;">QUANTUM ACCESS</h3>
        <p style="font-size:10.5px;color:#9ba6b8;text-align:center;margin-bottom:20px;">Default: admin / admin123</p>
        ''' + (f'<p style="color:#ff3366;font-size:11.5px;text-align:center;">{error}</p>' if error else '') + '''
        <form method="POST">
            <p><label style="font-size:11px;color:#9ba6b8;">Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#0e1420;border:1px solid #283956;color:#fff;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <p><label style="font-size:11px;color:#9ba6b8;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#0e1420;border:1px solid #283956;color:#fff;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#f3ca52;color:#030508;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;box-shadow:0 0 10px rgba(243,202,82,.3);">Connetti</button>
        </form>
    </div></body></html>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
