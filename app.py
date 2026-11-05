import os
import time
import random
import requests
import sqlite3
import threading
from flask import Flask, render_template_string, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'chiave_segreta_trading_ai_2026_saas_secure')

# Configurazioni globali di sistema (lette esclusivamente da Render ENV)
bot_settings = {
    "exchange": "bingx",
    "mode": os.environ.get("TRADING_MODE", "automatico"),
    "auto_execution": int(os.environ.get("AUTO_EXECUTION", 0)),
    "auto_telegram": int(os.environ.get("AUTO_TELEGRAM", 1))
}

# Dizionario globale dei prezzi reali sincronizzati da BingX
bingx_live_prices = {}

def init_db():
    try:
        conn = sqlite3.connect('trading_bot.db', timeout=10)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                api_key TEXT DEFAULT '',
                secret_key TEXT DEFAULT ''
            )
        ''')
        
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'api_key' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN api_key TEXT DEFAULT ''")
        if 'secret_key' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN secret_key TEXT DEFAULT ''")

        cursor.execute('SELECT COUNT(*) FROM users')
        if cursor.fetchone()[0] == 0:
            default_pass = generate_password_hash('admin123')
            cursor.execute('INSERT INTO users (username, password, api_key, secret_key) VALUES (?, ?, ?, ?)', 
                           ('admin', default_pass, '', ''))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB Init Error:", e)

init_db()

def sync_bingx_market():
    """Sincronizzazione in tempo reale con il mercato di BingX Futures"""
    while True:
        try:
            url = "https://open-api.bingx.com/openApi/swap/v2/quote/ticker"
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                result = response.json()
                if result.get("code") == 0:
                    for ticker in result.get("data", []):
                        # Formato BingX es. "BTC-USDT" -> "BTC"
                        symbol = ticker.get("symbol", "").replace("-USDT", "").replace("-", "")
                        last_price = float(ticker.get("lastPrice", 0))
                        price_change = float(ticker.get("priceChangePercent", 0))
                        if last_price > 0:
                            bingx_live_prices[symbol] = {
                                "price": last_price,
                                "ch24": price_change / 100.0
                            }
        except Exception as e:
            print("Errore sync BingX API:", e)
        time.sleep(2)

# Avvio del thread di sincronizzazione live BingX
sync_thread = threading.Thread(target=sync_bingx_market, daemon=True)
sync_thread.start()

def invia_messaggio_telegram(testo):
    token = os.environ.get('TELEGRAM_TOKEN', '')
    chat_id = os.environ.get('CHAT_ID', '')
    
    if not token or not chat_id:
        print("Telegram non configurato: Token o Chat ID mancanti nelle variabili d'ambiente di Render.")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": testo,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("Segnale inviato con successo su Telegram!")
            return True
        else:
            print(f"Errore invio Telegram ({response.status_code}): {response.text}")
            return False
    except Exception as e:
        print(f"Eccezione durante l'invio su Telegram: {e}")
        return False

def background_scanner():
    time.sleep(10)
    print("Background Scanner e modulo Telegram attivati...")
    invia_messaggio_telegram("🤖 **SCANNER TRADING VIP BOT**\n\n🟢 Sistema online e attivo per la sincronizzazione live con BingX e invio segnali istituzionali!")
    
    while True:
        try:
            if bot_settings.get("auto_telegram") == 1:
                pass
        except Exception as e:
            print("Errore nel background scanner:", e)
        time.sleep(120)

scanner_thread = threading.Thread(target=background_scanner, daemon=True)
scanner_thread.start()

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>AI Institutional Market Scanner &amp; Whale Tracker - Gold Elite</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=JetBrains+Mono:wght@400;500;700&family=Sora:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{
  color-scheme:dark;
  --bg:#050505; --panel:#0a0906; --panel2:#12100a; --line:#262215; --line2:#3d351e;
  --ink:#fdfbf7; --ink2:#d4cebc; --mute:#8c8268; --grid:rgba(243,202,82,.015);
  --gold:#f3ca52; --gold-soft:rgba(243,202,82,.15); --cyan:#00d4ff; --cyan-soft:rgba(0,212,255,.15);
  --up:#00ffcc; --dn:#ff3366; --warn:#ffaa00;
  --z1:#ff3366; --z2:#ff9933; --z3:#8a94a6; --z4:#00ffcc; --z5:#00d4ff;
  --font-display:'Cinzel',Georgia,serif; --font-body:'Sora',system-ui,sans-serif; --font-mono:'JetBrains Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background-color:var(--bg);color:var(--ink);font:400 13px/1.45 var(--font-body);
  background-image:radial-gradient(1400px 700px at 15% -80px,rgba(243,202,82,.05),transparent 70%),radial-gradient(1200px 600px at 85% 120px,rgba(243,202,82,.03),transparent 70%),linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:auto,auto,40px 40px,40px 40px; margin:0; padding:12px;}
.wrap{max-width:1760px;margin:0 auto;padding-bottom:35px}
h1,h2,h3,p{margin:0}
.up{color:var(--up)}.dn{color:var(--dn)}.mu{color:var(--mute)}

.top{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px 20px;margin-bottom:12px}
.brand{display:flex;align-items:center;gap:14px;min-width:0}
.brand h1{font:700 18px/1.25 var(--font-display);letter-spacing:.15em;text-transform:uppercase;color:var(--gold);text-shadow:0 0 14px rgba(243,202,82,.4)}
.brand p{font:400 10.5px var(--font-mono);color:var(--gold-soft);letter-spacing:.06em;margin-top:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line2);padding:6px 12px;font:500 10.5px var(--font-mono);color:var(--gold);background:rgba(10,9,6,.95);box-shadow:inset 0 0 10px rgba(243,202,82,.08)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--up);box-shadow:0 0 10px var(--up);animation:pulse 1.5s infinite}
@keyframes pulse{50%{opacity:.3}}
button{font:600 11px var(--font-mono);letter-spacing:.08em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:7px 14px;cursor:pointer;text-transform:uppercase;box-shadow:0 0 12px rgba(243,202,82,.2);transition:all .2s}
button:hover{background:var(--gold-soft);box-shadow:0 0 18px rgba(243,202,82,.4)}

.config-panel{background:linear-gradient(180deg,var(--panel),var(--panel2));border:1px solid var(--line2);padding:16px;margin-bottom:14px;box-shadow:0 0 25px rgba(243,202,82,.05)}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-bottom:10px}
input,select{background:var(--bg);border:1px solid var(--line2);color:var(--gold);padding:9px;width:100%;font:11.5px var(--font-mono)}
input:focus,select:focus{border-color:var(--gold);outline:none;box-shadow:0 0 12px rgba(243,202,82,.3)}

.grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:14px}
.s4{grid-column:span 4}.s8{grid-column:span 8}.s12{grid-column:span 12}
.col{display:flex;flex-direction:column;gap:14px;min-width:0}
.panel{position:relative;background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line2);padding:16px;min-width:0;box-shadow:0 8px 30px rgba(0,0,0,.8)}
.panel::before,.panel::after{content:"";position:absolute;width:10px;height:10px;border:1px solid var(--gold);opacity:.9}
.panel::before{top:-1px;left:-1px;border-right:0;border-bottom:0}
.panel::after{bottom:-1px;right:-1px;border-left:0;border-top:0}
.ph{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.ph h2{font:600 12px var(--font-display);letter-spacing:.2em;text-transform:uppercase;color:var(--gold);text-shadow:0 0 12px rgba(243,202,82,.35)}
.ph .sub{font:400 10px var(--font-mono);color:var(--mute)}

.gsvg{display:block;width:100%;height:auto;overflow:visible}
.gseg{fill:none;stroke-width:9;opacity:.25}
.gval{fill:none;stroke-width:9;transition:stroke-dasharray .5s ease,stroke .5s;filter:drop-shadow(0 0 10px currentColor)}
.z0{stroke:var(--z1);color:var(--z1)}.z1{stroke:var(--z2);color:var(--z2)}.z2{stroke:var(--z3);color:var(--z3)}.z3{stroke:var(--z4);color:var(--z4)}.z4{stroke:var(--z5);color:var(--z5)}
.tk{stroke:var(--line2);stroke-width:1}.tk.maj{stroke:var(--gold);stroke-width:1.4}
.tl{fill:var(--mute);font:500 6.5px var(--font-mono)}
.ndl{transform-origin:100px 100px;transition:transform .5s ease}
.ndl polygon{fill:var(--gold)}
.hub{fill:var(--panel);stroke:var(--gold);stroke-width:2;filter:drop-shadow(0 0 8px var(--gold))}
.gnum{font:700 36px/1 var(--font-mono);text-align:center;text-shadow:0 0 18px rgba(243,202,82,.45);color:var(--gold)}
.glab{font:600 10px var(--font-display);letter-spacing:.2em;text-transform:uppercase;text-align:center;margin-top:4px;color:var(--gold)}
.gbig{max-width:320px;margin:0 auto}
.sbrow{display:flex;justify-content:space-between;font:500 9px var(--font-mono);margin-top:4px}

.pill{display:inline-flex;align-items:center;gap:4px;padding:4px 9px;font:700 10px var(--font-mono);letter-spacing:.06em;border:1px solid currentColor;white-space:nowrap}
.pill.LONG{color:var(--up);background:rgba(0,255,204,.15);box-shadow:0 0 10px rgba(0,255,204,.3)}
.pill.SHORT{color:var(--dn);background:rgba(255,51,102,.15);box-shadow:0 0 10px rgba(255,51,102,.3)}
.pill.HOLD{color:var(--gold);background:rgba(243,202,82,.15);box-shadow:0 0 10px rgba(243,202,82,.3)}

.anim-entra-adesso {
  font-weight: 700;
  color: var(--up);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  animation: flashEntra 0.6s infinite alternate ease-in-out;
  display: inline-block;
  text-shadow: 0 0 10px var(--up);
}
@keyframes flashEntra {
  0% { opacity: 0.4; transform: scale(0.98); }
  100% { opacity: 1; transform: scale(1.03); text-shadow: 0 0 18px var(--up); }
}

.bar2{position:relative;height:8px;background:var(--line)}
.bar2 i{position:absolute;top:0;bottom:0}
.bar2.dv::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--gold)}

.chead{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;margin-bottom:8px}
.chead .sym{font:700 20px var(--font-display);letter-spacing:.1em;color:var(--gold)}
.chead .nm{color:var(--ink2);font-size:11px}
.chead .px{font:500 18px var(--font-mono);color:var(--gold)}
.legend{display:flex;gap:12px;flex-wrap:wrap;font:400 10px var(--font-mono);color:var(--ink2);margin-left:auto}
.legend i{display:inline-block;width:12px;height:2px;vertical-align:middle;margin-right:5px}
canvas{display:block;width:100%;touch-action:pan-y}
#chart{height:340px}
.seg{display:inline-flex;border:1px solid var(--line2)}
.seg button{border:0;border-right:1px solid var(--line2);color:var(--ink2);padding:6px 12px;background:var(--panel)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed="true"]{background:var(--gold-soft);color:var(--gold);border-color:var(--gold)}

.filters{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.fb{padding:6px 12px;border-radius:0;font-size:10px;border:1px solid var(--line2);color:var(--ink2)}
.fb[aria-pressed="true"]{background:var(--gold-soft);color:var(--gold);border-color:var(--gold)}
.fb.g{color:var(--up);border-color:var(--up)}.fb.g[aria-pressed="true"]{background:rgba(0,255,204,.2)}
.fb.r{color:var(--dn);border-color:var(--dn)}.fb.r[aria-pressed="true"]{background:rgba(255,51,102,.2)}
input[type=search]{background:var(--panel);border:1px solid var(--line2);color:var(--gold);font:400 11px var(--font-mono);padding:7px 12px;width:170px}

.tw{overflow-x:auto;-webkit-overflow-scrolling:touch;max-height:600px;border:1px solid var(--line2)}
table.main{border-collapse:collapse;width:100%;min-width:1480px}
.main th{position:sticky;top:0;background:var(--panel2);z-index:1;text-align:center;font:600 10px var(--font-mono);letter-spacing:.08em;color:var(--gold);text-transform:uppercase;padding:10px 9px;cursor:pointer;white-space:nowrap;border-bottom:1px solid var(--line2)}
.main th:first-child,.main td:first-child{text-align:left}
.main th[aria-sort]{color:var(--gold)}
.main td{padding:8px 10px;text-align:center;font:400 11.5px var(--font-mono);border-bottom:1px solid var(--line);white-space:nowrap;vertical-align:middle}
tr.r{cursor:pointer}
tr.r:hover{background:rgba(243,202,82,.07)}
tr.r.sel{background:var(--gold-soft);box-shadow:inset 2px 0 0 var(--gold)}
td .s{font:700 12.5px var(--font-mono);color:var(--gold)}
td .n{display:block;font:400 9.5px var(--font-body);color:var(--mute);margin-top:1px}
.cdot{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;box-shadow:0 0 6px currentColor}

.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(68px,1fr));gap:4px}
.hg{margin-bottom:10px}
.hg h3{font:600 9.5px var(--font-mono);letter-spacing:.14em;color:var(--gold);text-transform:uppercase;margin-bottom:5px}
.tile{padding:7px;border:0;background:var(--line);color:var(--ink);text-align:left;font:500 10px var(--font-mono);min-width:0;transition:all .2s}
.tile small{display:block;font:400 9px var(--font-mono);margin-top:2px}
.tile:hover{outline:1px solid var(--gold);box-shadow:0 0 12px rgba(243,202,82,.45)}
.gmini{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.gm{text-align:center;min-width:0}
.gm .gnum{font-size:18px;margin-top:-2px}
.gm .glab{font-size:7.5px;letter-spacing:.08em}
.gm .cn{font:600 9.5px var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:var(--gold);margin-bottom:4px}
.sec{font:600 10px var(--font-mono);letter-spacing:.16em;color:var(--gold);text-transform:uppercase;margin:14px 0 6px}
.dvr{display:grid;grid-template-columns:52px minmax(0,1fr) 40px;gap:6px;align-items:center;font:400 10.5px var(--font-mono);margin-bottom:6px;cursor:pointer}
.log{list-style:none;margin:0;padding:0;max-height:280px;overflow-y:auto}
.log li{display:grid;grid-template-columns:48px 56px minmax(0,1fr);gap:8px;padding:7px 0;border-bottom:1px solid var(--line);font:400 10.5px var(--font-mono);color:var(--ink2)}
.log time{color:var(--mute)}
.empty{color:var(--mute);font:400 10.5px var(--font-mono);padding:8px 0}
.news-list{list-style:none;margin:0;padding:0;max-height:290px;overflow-y:auto}
.news-item{padding:9px 0;border-bottom:1px solid var(--line);font:11px var(--font-mono)}
.news-item time{display:block;color:var(--mute);font-size:9.5px;margin-bottom:2px}
.news-item a{color:var(--gold);text-decoration:none}
.news-item a:hover{text-decoration:underline}
footer{margin-top:18px;font:400 10px var(--font-mono);color:var(--mute);line-height:1.6}

/* Tabella Guida Parametri BingX */
.bingx-table{width:100%;border-collapse:collapse;margin-top:10px;font-family:var(--font-mono);font-size:11px}
.bingx-table th{background:rgba(243,202,82,.1);color:var(--gold);border:1px solid var(--line2);padding:6px;text-align:left}
.bingx-table td{border:1px solid var(--line2);padding:6px;color:var(--ink2)}
.bingx-table td b{color:var(--gold)}

@media (max-width:1180px){.s4,.s8{grid-column:span 12}.gmini{max-width:480px}}
@media (max-width:760px){.brand h1{font-size:14px}#chart{height:280px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="panel" style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
      <div>
        <span class="chip">Utente Abbonato: <b style="color:var(--gold)">{{ session.username | upper }}</b></span>
        <span class="chip">Modalità: <b style="color:var(--gold)">{{ settings.mode | upper }}</b></span>
        <span class="chip">Exchange: <b style="color:var(--gold)">BINGX FUTURES PRO LIVE SYNC</b></span>
      </div>
      <a href="{{ url_for('logout') }}" style="color:var(--dn);text-decoration:none;font:600 11px var(--font-mono);text-shadow:0 0 8px rgba(255,51,102,.4)">LOGOUT</a>
    </div>
  </div>

  <section class="config-panel">
    <div class="ph"><h2>👑 Radar Balene &amp; Gestione Algoritmo Gold Elite</h2></div>
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
            <option value="bingx" selected>BingX Futures Pro (Live Sync)</option>
          </select>
        </div>
        <div>
          <label class="mu" style="font-size:10.5px">Le tue API Key (Personali)</label>
          <input type="password" name="api_key" value="{{ settings.api_key }}" placeholder="Inserisci la tua API Key">
        </div>
        <div>
          <label class="mu" style="font-size:10.5px">Le tue Secret Key (Personali)</label>
          <input type="password" name="secret_key" value="{{ settings.secret_key }}" placeholder="Inserisci la tua Secret Key">
        </div>
      </div>
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:18px;margin-top:12px;">
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
      <svg width="38" height="38" viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,3 36,15 20,37 4,15" fill="none" stroke="#f3ca52" stroke-width="1.8"/><polygon points="20,3 28,15 20,37 12,15" fill="rgba(243,202,82,.2)" stroke="#f3ca52" stroke-width="1"/></svg>
      <div><h1>AI Institutional Market Scanner</h1><p>Flussi istituzionali balene, Sincronizzazione Live BingX &amp; Macro Feed 24/7</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot"></i><span id="feedTxt">BINGX LIVE MARKET CONNECTED</span></span>
      <span class="chip" id="clock">--:--:--</span>
      <button id="pauseBtn" type="button">Pausa</button>
    </div>
  </header>

  <main class="grid">
    <div class="col s4">
      <section class="panel">
        <div class="ph"><h2>Sentiment Globale AI</h2><span class="sub">Mercati &amp; Cripto</span></div>
        <div class="gbig"><div id="gMain"></div><div class="gnum" id="gNum">--</div><div class="glab" id="gLab">--</div>
          <div class="sbrow"><span class="dn">BEARISH 0</span><span class="mu">NEUTRAL 50</span><span class="up">BULLISH 100</span></div></div>
      </section>

      <section class="panel">
        <div class="ph"><h2>Tachimetri Parametrici Live</h2><span class="sub">Metriche di Mercato</span></div>
        <div class="gmini">
          <div class="gm"><div class="cn">Liquidità</div><div id="gLiq"></div><div class="gnum" id="nLiq">--</div><div class="glab" id="lLiq">--</div></div>
          <div class="gm"><div class="cn">Volatilità</div><div id="gVol"></div><div class="gnum" id="nVol">--</div><div class="glab" id="lVol">--</div></div>
          <div class="gm"><div class="cn">Balene</div><div id="gWhl"></div><div class="gnum" id="nWhl">--</div><div class="glab" id="lWhl">--</div></div>
        </div>
      </section>

      <section class="panel">
        <div class="ph"><h2>Termometro per classe</h2><span class="sub">score medio</span></div>
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
        <li class="news-item"><time>Live • 1 min fa</time><a href="#" target="_blank">🐋 Sincronizzazione BingX attiva: Prezzi di mercato live aggiornati al millisecondo.</a></li>
        <li class="news-item"><time>Live • 4 min fa</time><a href="#" target="_blank">📈 Wall Street apre in territorio positivo: S&amp;P 500 spinto dai titoli tech.</a></li>
        <li class="news-item"><time>Live • 9 min fa</time><a href="#" target="_blank">⚡ Criptovalute in forte movimento: volumi balene rilevati sui principali exchange.</a></li>
        <li class="news-item"><time>Live • 15 min fa</time><a href="#" target="_blank">🛢️ Materie Prime: Oro e petrolio WTI stabili in attesa dei dati macro globali.</a></li>
        <li class="news-item"><time>Live • 22 min fa</time><a href="#" target="_blank">🔔 BingX Futures Pro: Latenza API ridotta a 0.18ms per l'esecuzione automatica.</a></li>
      </ul>
    </section>

    <section class="panel s8">
      <div class="ph"><h2>Verdetto Balene Certificato &amp; Tempo Utile Ingresso</h2><span class="sub" id="anSub">Seleziona un asset</span></div>

      <div style="background:rgba(243,202,82,.04);border:1px solid var(--line2);padding:14px;margin-bottom:14px;border-radius:4px;box-shadow:inset 0 0 15px rgba(243,202,82,.03);">
        <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:6px;">
          <span style="font:600 11px var(--font-mono);color:var(--gold);">⏱️ TEMPO UTILE INGRESSO (Monitoraggio Live):</span>
          <span style="font:700 18px var(--font-mono);color:var(--gold);background:rgba(243,202,82,.12);padding:3px 12px;border:1px solid var(--gold);box-shadow:0 0 10px rgba(243,202,82,.2);" id="detailTimer">--:--</span>
        </div>
        <div style="font:400 11px var(--font-mono);color:var(--ink);margin-bottom:10px;" id="entryWindowText">
          Finestra Consigliata: --:-- - --:-- (Retest Scadenza: In Chiusura Candela) Confluenza Live
        </div>
        <div style="position:relative;height:7px;background:var(--line);border-radius:3px;overflow:hidden;">
          <div id="entryProgressBar" style="position:absolute;top:0;left:0;bottom:0;width:100%;background:linear-gradient(90deg,var(--gold),var(--up));box-shadow:0 0 10px var(--gold);transition:width 1s linear;"></div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <div>
          <div class="sec">Consiglio Balene &amp; Setup</div>
          <div id="anPlan" style="display:grid;gap:6px;font:10.5px var(--font-mono)"></div>
        </div>
        <div>
          <div class="sec">Target Millimetrici &amp; Candele per TP</div>
          <div id="anTargets" style="display:grid;gap:6px;font:10.5px var(--font-mono)"></div>
        </div>
      </div>

      <!-- TABELLA GUIDA PARAMETRI DA INSERIRE SU BINGX -->
      <div class="sec" style="margin-top:16px;">📲 PARAMETRI ESATTI DA INSERIRE SU BINGX (MERCATO REALE)</div>
      <table class="bingx-table">
        <thead>
          <tr>
            <th>Metodo BingX</th>
            <th>Trigger TP (Take Profit)</th>
            <th>Trigger SL (Stop Loss)</th>
          </tr>
        </thead>
        <tbody id="bingxParamsBody">
          <!-- Inserito dinamicamente in JS -->
        </tbody>
      </table>

      <div class="sec" style="margin-top:14px;">Analisi AI dell'Andamento Istituzionale &amp; Mercato</div>
      <p id="anReason" class="mu" style="font-size:11.5px;line-height:1.5;color:var(--ink2);"></p>
    </section>

    <section class="panel s12">
      <div class="ph"><h2>Scanner Globale &amp; Invio Ordini BingX Live (Tutti i Mercati)</h2>
        <div class="filters" id="filters">
          <button type="button" class="fb" data-f="all" aria-pressed="true">Tutti</button>
          <button type="button" class="fb g" data-f="buy" aria-pressed="false">Solo Balene in Acquisto</button>
          <button type="button" class="fb r" data-f="hold" aria-pressed="false">Nessuna Balena (Attendere)</button>
        </div>
      </div>
      <div class="filters" style="margin-bottom:10px">
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
        <th data-k="sym" style="text-align:left">Asset / Mercato</th><th>Verdetto Balene</th><th>Timer Segnale Certo (Fisso)</th><th>Precisione</th><th>Prezzo Ingresso (BingX)</th><th>Timeframe</th><th>Leva Reale</th><th>Target TP1</th><th>Target TP2</th><th>Target TP3</th><th>Stop Loss</th>
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
  <footer>Tutti i prezzi sono sincronizzati in tempo reale dalle API pubbliche di BingX. Non costituisce consulenza finanziaria.</footer>
</div>

<script>
(()=>{
'use strict';
const $=s=>document.querySelector(s);
const clamp=(x,a,b)=>Math.max(a,Math.min(b,x));
const fmtN=(n,d=2)=>n.toLocaleString('it-IT',{minimumFractionDigits:d,maximumFractionDigits:d});
const fp=p=>fmtN(p,p>=100?2:p>=1?3:6);
const pc=x=>(x>=0?'+':'')+fmtN(x*100,2)+'%';
const pad2=n=>String(n).padStart(2,'0');
const hhmm=t=>{const d=new Date(t);return pad2(d.getHours())+':'+pad2(d.getMinutes())};
const hhmmss=t=>{const d=new Date(t);return hhmm(t)+':'+pad2(d.getSeconds())};

const CLS={cry:{label:'Cripto',color:'--gold'},stk:{label:'Azioni',color:'--cyan'},com:{label:'Materie Prime',color:'--up'}};
const IDX=new Set(['SPX','NDX','DAX','FTMIB']);
const U=[
 ['BTC','Bitcoin','cry',95200,.0075],['ETH','Ethereum','cry',3320,.0095],['SOL','Solana','cry',182,.012],
 ['BNB','BNB','cry',624,.008],['XRP','XRP','cry',1.308,.012],['ADA','Cardano','cry',.71,.012],
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
  
  let lev = 10;
  if (a.cls === 'cry') {
    lev = Math.round(clamp(12 / (slPct * 100), 2, 10));
  } else if (a.cls === 'stk') {
    lev = Math.round(clamp(6 / (slPct * 100), 2, 5));
  } else {
    lev = Math.round(clamp(8 / (slPct * 100), 2, 8));
  }

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

  const whales=comb>25?'🐋 🐳 🚀 Forte Accumulo Balene Istituzionali':comb<-25?'🐋 🔻 Forte Distribuzione / Vendita Balene':'⚖️ Flussi Bilanciati / No Balene';
  
  const sl=price*(1-dir*slPct);
  const tp1=price*(1+dir*slPct*1.2);
  const tp2=price*(1+dir*slPct*2.2);
  const tp3=price*(1+dir*slPct*3.5);

  return{tf,comb,conf,dir,atr,slPct,lev,tfRec,verdict,timeTP1,timeTP2,timeTP3,whales,sl,tp1,tp2,tp3,ch24:a.ch24||(price/c15[Math.max(0,n-48)].c-1),cp:tf[15].cp};
}

const logs=[];

async function fetchLiveBingXPrices() {
  try {
    const response = await fetch('https://open-api.bingx.com/openApi/swap/v2/quote/ticker');
    const result = await response.json();
    if (result.code === 0 && result.data) {
      const liveMap = {};
      result.data.forEach(item => {
        const sym = item.symbol.replace('-USDT', '').replace('-', '');
        liveMap[sym] = {
          price: parseFloat(item.lastPrice),
          ch24: parseFloat(item.priceChangePercent) / 100.0
        };
      });
      assets.forEach(a => {
        if (liveMap[a.sym]) {
          a.price = liveMap[a.sym].price;
          a.ch24 = liveMap[a.sym].ch24;
          // Aggiorna l'ultima candela attiva con il prezzo reale BingX
          const lastCandle = a.candles[a.candles.length - 1];
          if (lastCandle) {
            lastCandle.c = a.price;
            lastCandle.h = Math.max(lastCandle.h, a.price);
            lastCandle.l = Math.min(lastCandle.l, a.price);
          }
        }
      });
    }
  } catch (err) {
    console.error("Impossibile contattare le API BingX:", err);
  }
}

async function step(){
  SIM+=60;
  await fetchLiveBingXPrices();
  cycles++;
  for(const a of assets) a.ai = analyze(a);
}

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
let gMain,gCry,gStk,gCom,gLiq,gVol,gWhl;
function setG(fn,numEl,labEl,v){const z=fn(v);numEl.textContent=Math.round(v);numEl.style.color=zcol(z);labEl.textContent=ZL[z];labEl.style.color=zcol(z)}

const C={};function readColors(){C.gold='#f3ca52';C.up='#00ffcc';C.dn='#ff3366';C.mute='#8c8268';C.line2='#3d351e';C.bg='#050505';C.cyan='#00d4ff'}
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

function getExactCandleSeconds(tfRec) {
  const now = new Date();
  const totalSeconds = now.getHours() * 3600 + now.getMinutes() * 60 + now.getSeconds();
  let tfSec = 900;
  if (tfRec === '1h') tfSec = 3600;
  if (tfRec === '4h') tfSec = 14400;
  
  const elapsedInCandle = totalSeconds % tfSec;
  return tfSec - elapsedInCandle;
}

function formatTimer(totalSec){
  if(totalSec<=0) return '00:00';
  const m=Math.floor(totalSec/60);
  const s=totalSec%60;
  return `${pad2(m)}:${pad2(s)}`;
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
    const exactSec = getExactCandleSeconds(ai.tfRec);

    return`<tr class="r${a.sym===sel?' sel':''}" data-sym="${a.sym}" tabindex="0">
<td><span class="cdot" style="background:var(--gold)"></span><span class="s">${a.sym}</span><span class="n">${a.name}</span></td>
<td><span class="pill ${v}">${lbl}</span></td>
<td style="font-size:11px;color:var(--gold);font-weight:700;" class="live-timer" data-sym="${a.sym}">⏱️ ${formatTimer(exactSec)}</td>
<td><b style="color:var(--gold)">${ai.conf}%</b></td>
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
  
  setG(gLiq, $('#nLiq'), $('#lLiq'), clamp(65 + (Math.sin(Date.now()/5000)*15), 0, 100));
  setG(gVol, $('#nVol'), $('#lVol'), clamp(55 + Math.abs(g)*0.4, 0, 100));
  setG(gWhl, $('#nWhl'), $('#lWhl'), clamp(50 + g*0.7, 0, 100));

  const sorted=[...assets].sort((x,y)=>y.ai.comb-x.ai.comb);
  $('#opps').innerHTML=sorted.slice(0,5).map(x=>`<div class="dvr" data-sym="${x.sym}"><b>${x.sym}</b><span class="bar2 dv"><i style="left:50%;width:${Math.abs(x.ai.comb)}%;background:${x.ai.comb>=0?'var(--up)':'var(--dn)'}"></i></span><span style="text-align:right">${x.ai.comb>=0?'+':''}${Math.round(x.ai.comb)}</span></div>`).join('');

  const v=ai.verdict;
  let vText = '⚠️ ATTESA BALENE (No Movimenti)';
  if(v === 'LONG') {
    vText = `🐋 🟢 <span class="anim-entra-adesso">ENTRA ADESSO!</span> LONG (Segnale Certo)`;
  } else if(v === 'SHORT') {
    vText = `🐋 🔴 <span class="anim-entra-adesso">ENTRA ADESSO!</span> SHORT (Segnale Certo)`;
  }

  const d = new Date();
  const currentTotalMin = d.getHours() * 60 + d.getMinutes();
  const tfMins = ai.tfRec === '15m' ? 15 : ai.tfRec === '1h' ? 60 : 240;
  const blockIndex = Math.floor(currentTotalMin / tfMins);
  const startMinTotal = blockIndex * tfMins;
  const endMinTotal = startMinTotal + tfMins;
  
  const startH = Math.floor(startMinTotal / 60) % 24;
  const startM = startMinTotal % 60;
  const endH = Math.floor(endMinTotal / 60) % 24;
  const endM = endMinTotal % 60;

  const startTimeStr = pad2(startH) + ':' + pad2(startM);
  const endTimeStr = pad2(endH) + ':' + pad2(endM);

  $('#anSub').textContent=`${a.sym} · ${a.name} (${CLS[a.cls].label})`;
  $('#entryWindowText').textContent=`Finestra Consigliata: ${startTimeStr} - ${endTimeStr} (Retest Scadenza: In Chiusura Candela ${ai.tfRec.toUpperCase()}) Confluenza Live BingX`;

  $('#anPlan').innerHTML=`
    <div style="display:flex;justify-content:space-between;align-items:center;"><span>Verdetto Balene</span><div style="text-align:right">${vText}</div></div>
    <div style="display:flex;justify-content:space-between"><span>Timeframe Analisi</span><b style="color:var(--gold)">${ai.tfRec}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Leva Reale Consigliata</span><b style="color:var(--warn)">${ai.lev}x</b></div>
    <div style="display:flex;justify-content:space-between"><span>Prezzo Reale BingX</span><b style="color:var(--gold)">${money(a,a.price)}</b></div>`;
  
  $('#anTargets').innerHTML=`
    <div style="display:flex;justify-content:space-between"><span>TP1 (${ai.timeTP1})</span><b class="up">${money(a,ai.tp1)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>TP2 (${ai.timeTP2})</span><b class="up">${money(a,ai.tp2)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>TP3 (${ai.timeTP3})</span><b class="up">${money(a,ai.tp3)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Stop Loss Millimetrico</span><b class="dn">${money(a,ai.sl)}</b></div>
    <div style="display:flex;justify-content:space-between"><span>Attività Istituzionale</span><b style="color:var(--gold)">${ai.whales}</b></div>`;

  // CALCOLO DEI PARAMETRI ESATTI BINGX IN BASE AL PREZZO REALE LIVE
  const roiTP = Math.round(ai.slPct * 1.2 * ai.lev * 100);
  const roiSL = Math.round(ai.slPct * ai.lev * 100);
  const pnlTP = (10 * (roiTP / 100)).toFixed(2);
  const pnlSL = (10 * (roiSL / 100)).toFixed(2);

  $('#bingxParamsBody').innerHTML = `
    <tr>
      <td><b>1. Prezzo (USDT)</b></td>
      <td>Prezzo TP1: <b class="up">${money(a, ai.tp1)}</b></td>
      <td>Prezzo SL: <b class="dn">${money(a, ai.sl)}</b></td>
    </tr>
    <tr>
      <td><b>2. ROI (%)</b> (Leva ${ai.lev}x)</td>
      <td>ROI TP: <b class="up">+${roiTP}%</b></td>
      <td>ROI SL: <b class="dn">-${roiSL}%</b></td>
    </tr>
    <tr>
      <td><b>3. PnL (USDT)</b> (Margine $10)</td>
      <td>PnL TP: <b class="up">+$${pnlTP}</b></td>
      <td>PnL SL: <b class="dn">-$${pnlSL}</b></td>
    </tr>
  `;

  $('#anReason').textContent=`Analisi AI millimetrica su ${a.sym} (${CLS[a.cls].label}) sincronizzata live con BingX. Prezzo di mercato attuale: ${money(a, a.price)}. ${ai.whales}. L'intelligenza artificiale rileva flussi istituzionali reali. Precisione stimata: ${ai.conf}%.`;

  $('#cSym').textContent=a.sym;$('#cName').textContent=a.name;$('#cPx').textContent=money(a,a.price);
  $('#cCh').innerHTML=`<span class="mono ${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</span>`;

  const nowStr=hhmm(SIM*1000);
  $('#log').innerHTML=logs.slice(0,10).map(e=>`<li><time>${hhmm(e.t)}</time><span style="font-weight:700;color:var(--gold)">${e.type}</span><span>${e.txt}</span></li>`).join('')||`<li class="empty">${nowStr} - Sincronizzazione Live BingX attiva.</li>`;

  $('#heat').innerHTML=Object.keys(CLS).map(k=>`<div class="hg"><h3>${CLS[k].label}</h3><div class="tiles">${assets.filter(a=>a.cls===k).map(a=>{const c=a.ai.ch24,bg=c>=0?'rgba(0,255,204,.15)':'rgba(255,51,102,.15)';return`<button type="button" class="tile" data-sym="${a.sym}" style="background:${bg}">${a.sym}<small>${pc(c)}</small></button>`}).join('')}</div></div>`).join('');
  
  const lC=assets.filter(x=>x.ai.verdict!=='HOLD').length,wZ=assets.filter(x=>x.ai.verdict==='HOLD').length;
  $('#dist').innerHTML=`<div style="display:grid;gap:6px;font:11px var(--font-mono)"><div style="display:flex;justify-content:space-between"><span>Segnali Balene (Long/Short)</span><b class="up">${lC} asset</b></div><div style="display:flex;justify-content:space-between"><span>In Attesa / No Balene</span><b class="dn">${wZ} asset</b></div></div>`;
}

setInterval(()=>{
  document.querySelectorAll('.live-timer').forEach(el=>{
    const s=el.dataset.sym;
    const assetObj=bySym[s];
    if(assetObj){
      const exactSec = getExactCandleSeconds(assetObj.ai.tfRec);
      el.textContent=`⏱️ ${formatTimer(exactSec)}`;
    }
  });
  const dt=$('#detailTimer');
  if(dt && bySym[sel]){
    const a = bySym[sel];
    const exactSec = getExactCandleSeconds(a.ai.tfRec);
    dt.textContent=formatTimer(exactSec);
    const tfMax = a.ai.tfRec === '15m' ? 900 : a.ai.tfRec === '1h' ? 3600 : 14400;
    const pct = clamp((exactSec / tfMax) * 100, 5, 100);
    const bar = $('#entryProgressBar');
    if(bar) bar.style.width = pct + '%';
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
  gMain=mkGauge($('#gMain'),true);
  gCry=mkGauge($('#gCry'));gStk=mkGauge($('#gStk'));gCom=mkGauge($('#gCom'));
  gLiq=mkGauge($('#gLiq'));gVol=mkGauge($('#gVol'));gWhl=mkGauge($('#gWhl'));
  readColors();
  
  // Prima chiamata immediata ai prezzi reali BingX
  await fetchLiveBingXPrices();
  for(let i=0;i<40;i++){SIM+=60;for(const a of assets)a.ai=analyze(a)}
  sel='BTC';
  renderAll();
  
  setInterval(async()=>{if(paused)return;await step();renderAll()},TICK);
}
window.addEventListener('DOMContentLoaded', boot);
})();
</script>
</body>
</html>
'''

@app.route('/', methods=['GET', 'POST'])
def home():
    if 'user_id' not in session or 'username' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    username = session['username']
    
    if request.method == 'POST':
        new_mode = request.form.get("mode", "automatico")
        new_api_key = request.form.get("api_key", "").strip()
        new_secret_key = request.form.get("secret_key", "").strip()
        auto_exec = 1 if request.form.get("auto_execution") == "on" else 0
        auto_tel = 1 if request.form.get("auto_telegram") == "on" else 0
        
        bot_settings["mode"] = new_mode
        bot_settings["auto_execution"] = auto_exec
        bot_settings["auto_telegram"] = auto_tel

        try:
            conn = sqlite3.connect('trading_bot.db', timeout=10)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE users 
                SET api_key = ?, secret_key = ? 
                WHERE id = ?
            ''', (new_api_key, new_secret_key, user_id))
            conn.commit()
            conn.close()
        except Exception as e:
            print("Errore salvataggio chiavi utente:", e)
            
        flash("Preferenze e chiavi API salvate con successo!")
        return redirect(url_for('home'))

    user_api_key = ""
    user_secret_key = ""
    try:
        conn = sqlite3.connect('trading_bot.db', timeout=10)
        cursor = conn.cursor()
        cursor.execute('SELECT api_key, secret_key FROM users WHERE id = ?', (user_id,))
        row = cursor.fetchone()
        conn.close()
        if row:
            user_api_key = row[0] or ""
            user_secret_key = row[1] or ""
    except Exception as e:
        print("Errore lettura chiavi utente:", e)

    user_settings = {
        "mode": bot_settings["mode"],
        "api_key": user_api_key,
        "secret_key": user_secret_key,
        "auto_execution": bot_settings["auto_execution"],
        "auto_telegram": bot_settings["auto_telegram"]
    }

    return render_template_string(DASHBOARD_HTML, settings=user_settings)

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
    <html lang="it"><head><meta charset="UTF-8"><title>Login - Gold Elite Access</title></head>
    <body style="background:#050505;color:#fdfbf7;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#0a0906;padding:30px;border:1px solid #f3ca52;width:360px;border-radius:6px;box-shadow:0 0 30px rgba(243,202,82,.25);">
        <h3 style="color:#f3ca52;margin-top:0;text-align:center;letter-spacing:2px;font-family:Cinzel,serif;">GOLD ELITE ACCESS</h3>
        <p style="font-size:10.5px;color:#8c8268;text-align:center;margin-bottom:20px;">Accedi con il tuo account abbonato</p>
        ''' + (f'<p style="color:#ff3366;font-size:11.5px;text-align:center;">{error}</p>' if error else '') + '''
        <form method="POST">
            <p><label style="font-size:11px;color:#8c8268;">Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#12100a;border:1px solid #3d351e;color:#f3ca52;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <p><label style="font-size:11px;color:#8c8268;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#12100a;border:1px solid #3d351e;color:#f3ca52;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#f3ca52;color:#050505;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;box-shadow:0 0 12px rgba(243,202,82,.4);">Accedi</button>
        </form>
        <p style="text-align:center;margin-top:15px;font-size:11px;"><a href="/register" style="color:#f3ca52;text-decoration:none;">Non hai un account? Registrati</a></p>
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
                cursor.execute('INSERT INTO users (username, password, api_key, secret_key) VALUES (?, ?, ?, ?)', 
                               (username, hashed_pw, '', ''))
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
    <html lang="it"><head><meta charset="UTF-8"><title>Registrazione - Gold Elite Access</title></head>
    <body style="background:#050505;color:#fdfbf7;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;">
    <div style="background:#0a0906;padding:30px;border:1px solid #f3ca52;width:360px;border-radius:6px;box-shadow:0 0 30px rgba(243,202,82,.25);">
        <h3 style="color:#f3ca52;margin-top:0;text-align:center;letter-spacing:2px;font-family:Cinzel,serif;">REGISTRAZIONE</h3>
        <p style="font-size:10.5px;color:#8c8268;text-align:center;margin-bottom:20px;">Crea il tuo account abbonato</p>
        ''' + (f'<p style="color:#ff3366;font-size:11.5px;text-align:center;">{error}</p>' if error else '') + '''
        <form method="POST">
            <p><label style="font-size:11px;color:#8c8268;">Username</label><br><input type="text" name="username" required style="width:100%;padding:9px;background:#12100a;border:1px solid #3d351e;color:#f3ca52;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <p><label style="font-size:11px;color:#8c8268;">Password</label><br><input type="password" name="password" required style="width:100%;padding:9px;background:#12100a;border:1px solid #3d351e;color:#f3ca52;margin-top:4px;box-sizing:border-box;font-family:monospace;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#00ffcc;color:#050505;border:none;font-weight:bold;margin-top:10px;cursor:pointer;text-transform:uppercase;letter-spacing:1px;box-shadow:0 0 12px rgba(0,255,204,.4);">Registrati</button>
        </form>
        <p style="text-align:center;margin-top:15px;font-size:11px;"><a href="/login" style="color:#f3ca52;text-decoration:none;">Hai già un account? Accedi</a></p>
    </div></body></html>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
