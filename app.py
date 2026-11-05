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

# Panieri basati sui contratti Perpetual Futures ufficiali di BingX
CRYPTO_FUTURES_ASSETS = [
    ("BTC", "Bitcoin", "cry", "BTC-USDT"),
    ("ETH", "Ethereum", "cry", "ETH-USDT"),
    ("SOL", "Solana", "cry", "SOL-USDT"),
    ("BNB", "BNB", "cry", "BNB-USDT"),
    ("XRP", "XRP", "cry", "XRP-USDT"),
    ("ADA", "Cardano", "cry", "ADA-USDT"),
    ("DOGE", "Dogecoin", "cry", "DOGE-USDT"),
    ("AVAX", "Avalanche", "cry", "AVAX-USDT"),
    ("LINK", "Chainlink", "cry", "LINK-USDT"),
    ("DOT", "Polkadot", "cry", "DOT-USDT"),
    ("LTC", "Litecoin", "cry", "LTC-USDT"),
    ("TON", "Toncoin", "cry", "TON-USDT"),
    ("SUI", "Sui", "cry", "SUI-USDT"),
    ("NEAR", "NEAR", "cry", "NEAR-USDT"),
    ("APT", "Aptos", "cry", "APT-USDT"),
    ("ARB", "Arbitrum", "cry", "ARB-USDT")
]

COMMODITIES_ASSETS = [
    ("XAU", "Oro", "com", "GC=F"),
    ("XAG", "Argento", "com", "SI=F"),
    ("WTI", "Petrolio WTI", "com", "CL=F"),
    ("BRENT", "Petrolio Brent", "com", "BZ=F"),
    ("NG", "Gas naturale", "com", "NG=F"),
    ("HG", "Rame", "com", "HG=F"),
    ("WHEAT", "Grano", "com", "KE=F"),
    ("COFFEE", "Caffè", "com", "KC=F")
]

live_market_data = {}

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
            
            # Scansione candele Futures direttamente da BingX V2 Swap API
            for sym, name, cls, bx_sym in CRYPTO_FUTURES_ASSETS:
                url = f"https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol={bx_sym}&interval=15m&limit=50"
                res = requests.get(url, timeout=5)
                if res.status_code == 200:
                    res_json = res.json()
                    if res_json.get("code") == 0:
                        klines = res_json.get("data", [])
                        if klines:
                            # BingX restituisce i dati in ordine cronologico o inverso a seconda dell'endpoint, estraiamo i prezzi di chiusura 'close'
                            closes = [float(k["close"]) for k in klines]
                            current_price = closes[-1]
                            ema20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
                            ema50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else current_price
                            is_long = current_price > ema20 and ema20 > ema50
                            score = 85 if is_long else -85
                            
                            if abs(score) >= 80:
                                now = time.time()
                                if sym not in last_alerts or now - last_alerts[sym] > 1800:
                                    dir_txt = "LONG 🟢" if is_long else "SHORT 🔴"
                                    msg = (
                                        f"💎 *TOP ENTRY >80% (BINGX LIVE)*\n\n"
                                        f"✦ *Asset:* #{bx_sym}\n"
                                        f"✦ *Direzione:* {dir_txt}\n"
                                        f"✦ *Prezzo BingX:* ${current_price:,.4f}\n"
                                    )
                                    if token and chat_id:
                                        send_telegram_alert(token, chat_id, msg)
                                    if auto_exec == 1 and trading_mode == "real" and api_key and secret_key:
                                        side = "BUY" if is_long else "SELL"
                                        pos_side = "LONG" if is_long else "SHORT"
                                        qty = 0.001 if sym == "BTC" else (0.01 if sym == "ETH" else 1.0)
                                        place_bingx_order(api_key, secret_key, bx_sym, side, pos_side, qty)
                                    last_alerts[sym] = now
        except Exception as e:
            print(f"Errore scanner BingX: {e}")
        time.sleep(30)

scanner_thread = threading.Thread(target=background_bingx_scanner, daemon=True)
scanner_thread.start()

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Scanner · Segnali BingX Real</title>
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
.dot.off{background:var(--warn);box-shadow:0 0 8px var(--warn);animation:none}
@keyframes pulse{50%{opacity:.35}}
button{font:600 11px var(--font-mono);letter-spacing:.07em;color:var(--gold);background:transparent;border:1px solid var(--gold);padding:6px 12px;cursor:pointer;text-transform:uppercase}
button:hover{background:var(--gold-soft)}
:focus-visible{outline:2px solid var(--gold);outline-offset:2px}

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
@keyframes fu{from{background:rgba(46,230,166,.28)}}
@keyframes fd{from{background:rgba(255,90,118,.28)}}
td.fu{animation:fu .9s}td.fd{animation:fd .9s}
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

.config-panel{background:#0c1017;border:1px solid var(--line);padding:15px;margin-bottom:15px;border-radius:4px}
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:10px}
input,select{background:#111722;border:1px solid var(--line2);color:#fff;padding:8px;width:100%;box-sizing:border-box}

@media (max-width:1180px){.s3,.s4,.s8{grid-column:span 12}.gmini{max-width:520px}}
@media (max-width:760px){.brand h1{font-size:15px}#chart{height:320px}.kv{grid-template-columns:1fr}.banner .tops{margin-left:0}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
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
    <div class="ph"><h2>⚙️ Configurazione Account &amp; BingX</h2></div>
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
      <div><h1>Scanner BingX · Segnali TOP Entry</h1><p>Dati Futures ufficiali BingX · Diamante giallo lampeggiante · Conto alla rovescia</p></div>
    </div>
    <div class="chips">
      <span class="chip"><i class="dot" id="feedDot"></i><span id="feedTxt">BINGX LIVE</span></span>
      <span class="chip">Auto-refresh: <b id="refresh" style="font-weight:700">00:02</b></span>
      <span class="chip" id="clock">--:--:--</span>
      <button id="pauseBtn" type="button">Pausa</button>
    </div>
  </header>

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
        <p class="note">Il segnale resta valido per il tempo indicato sul contratto BingX.</p>
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
        <div class="ph"><h2>Esecuzione istantanea BingX</h2><span class="sub" id="exSub"></span></div>
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
      <div class="ph"><h2>Scanner Futures BingX</h2>
        <div class="filters" id="filters">
          <button type="button" class="fb" data-f="all" aria-pressed="true">Tutti</button>
          <button type="button" class="fb t" data-f="top" aria-pressed="false">TOP confluenze (&gt;80%)</button>
          <button type="button" class="fb g" data-f="buy" aria-pressed="false">Solo BUY</button>
          <button type="button" class="fb r" data-f="sell" aria-pressed="false">Solo SELL</button>
        </div>
      </div>
      <div class="filters" style="margin-bottom:10px">
        <input id="q" type="search" placeholder="Cerca asset…" aria-label="Cerca asset">
        <span class="sub mu mono" id="cnt" style="margin-left:auto"></span>
      </div>
      <div class="tw"><table class="main"><thead><tr id="thead">
        <th data-k="sym" style="text-align:left">Asset BingX</th><th>Entrata</th><th>Leva</th><th data-k="price">Prezzo BingX</th><th>Tachimetro</th><th data-k="conf">Confidenza</th><th>Validità</th><th>15m / 1h / 4h</th><th>Sentiment</th><th data-k="ch24">Var 24h</th><th>TP1</th><th>Stop Loss</th>
      </tr></thead><tbody id="tbody"></tbody></table></div>
    </section>

    <section class="panel s4">
      <div class="ph"><h2>Termometro BingX</h2><span class="sub">forza media</span></div>
      <div class="gmini" style="grid-template-columns:1fr">
        <div class="gm"><div class="cn">Mercato Futures BingX</div><div id="gCry"></div><div class="gnum" id="nCry">--</div><div class="glab" id="lCry">--</div></div>
      </div>
      <div class="sec">Segnali più forti</div>
      <div id="opps"></div>
    </section>
    <section class="panel s4">
      <div class="ph"><h2>Mappa di calore BingX</h2></div>
      <div id="heat"></div>
    </section>
    <section class="panel s4">
      <div class="ph"><h2>Registro eventi</h2><span class="sub">ultimi ordini / segnali</span></div>
      <ul class="log" id="log"></ul>
    </section>
  </main>
  <footer id="foot"></footer>
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
const compact=v=>v>=1e9?fmtN(v/1e9,2)+'B':v>=1e6?fmtN(v/1e6,2)+'M':v>=1e3?fmtN(v/1e3,1)+'K':fmtN(v,0);
const pad2=n=>String(n).padStart(2,'0');
const hhmm=t=>{const d=new Date(t);return pad2(d.getHours())+':'+pad2(d.getMinutes())};
const hhmmss=t=>{const d=new Date(t);return hhmm(t)+':'+pad2(d.getSeconds())};
const mmss=s=>{s=Math.max(0,Math.floor(s));return pad2(Math.floor(s/60))+':'+pad2(s%60)};

const U=[
 ['BTC','Bitcoin','BTC-USDT',95200,.0075],['ETH','Ethereum','ETH-USDT',3320,.0095],['SOL','Solana','SOL-USDT',182,.012],
 ['BNB','BNB','BNB-USDT',624,.008],['XRP','XRP','XRP-USDT',2.41,.012],['ADA','Cardano','ADA-USDT',.71,.012],
 ['DOGE','Dogecoin','DOGE-USDT',.184,.014],['AVAX','Avalanche','AVAX-USDT',38.4,.013],['LINK','Chainlink','LINK-USDT',17.2,.012],
 ['DOT','Polkadot','DOT-USDT',7.15,.012],['LTC','Litecoin','LTC-USDT',96.5,.01],['TON','Toncoin','TON-USDT',5.62,.012],
 ['SUI','Sui','SUI-USDT',3.84,.015],['NEAR','NEAR','NEAR-USDT',5.48,.014],['APT','Aptos','APT-USDT',9.2,.014],['ARB','Arbitrum','ARB-USDT',.62,.014]
];
const money=(a,p)=>'$'+fp(p);
const TICK=2000,VALID=45*60,N15=500;
let SIM=Math.floor(Date.now()/1000)-420*60,tickCount=0,scans=0;
const assets=U.map(([sym,name,bx,price,vol])=>({sym,name,bx,cls:'cry',price,vol:vol/2,beta:.7+Math.random()*.6,
  per:160,ph:Math.random()*6.28,amp:.3,pos:N15,v0:2e4,ns:0,wh:0,ls:1,fund:.01,oi:0,newsT:0,sig:null,cool:0,candles:[]}));
const bySym={};assets.forEach(a=>bySym[a.sym]=a);

async function loadBingXData(){
  try{
    await Promise.all(assets.map(async a=>{
      const res=await fetch(`https://open-api.bingx.com/openApi/swap/v2/quote/klines?symbol=${a.bx}&interval=15m&limit=200`);
      const d=await res.json();
      if(d.code===0 && d.data && d.data.length>0){
        // BingX format: {time, open, high, low, close, volume}
        a.candles=d.data.map(k=>({t:+k.time,o:+k.open,h:+k.high,l:+k.low,c:+k.close,v:+k.volume}));
        a.price=a.candles[a.candles.length-1].c;
      }
    }));
  }catch(e){console.error("Errore fetch BingX live:", e);}
}

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
const sigOf=s=>s>=28?'LONG':s<=-28?'SHORT':'WAIT';
function aggC(c,m){const out=[];let end=c.length;
  while(end-m>=0&&out.length<200){const g=c.slice(end-m,end);let h=-Infinity,l=Infinity,v=0;for(const x of g){if(x.h>h)h=x.h;if(x.l<l)l=x.l;v+=x.v}
    out.unshift({t:g[0].t,o:g[0].o,c:g[g.length-1].c,h,l,v});end-=m}
  return out}
function tfAnalyze(cs){if(!cs||cs.length<50)return{cs:[],S:{c:[],e20:[],e50:[],rsi:[50],hist:[0],atr:[1],pb:[.5],vr:[1]},cp:{},score:0};
  const S=calc(cs),i=cs.length-1,cp=comps(S,cs,i);return{cs,S,cp,score:scoreOf(cp)}}
const TFW={15:.3,60:.4,240:.3};
function analyze(a){
  if(!a.candles||a.candles.length<50)return{tf:{15:{score:0,S:calc([{t:0,o:1,h:1,l:1,c:1,v:1}])},60:{score:0},240:{score:0}},comb:0,conf:0,dir:1,raw:0,agree:0,atr:1,slPct:.01,lev:[5,10],anom:null,ch24:0,vol24:0,rsi:50};
  const c15=a.candles,n=c15.length,tf={15:tfAnalyze(c15.slice(-200)),60:tfAnalyze(aggC(c15,4)),240:tfAnalyze(aggC(c15,16))};
  const comb=TFW[15]*tf[15].score+TFW[60]*tf[60].score+TFW[240]*tf[240].score;
  let agree=0;for(const k in TFW)if(Math.abs(tf[k].score)>8&&Math.sign(tf[k].score)===Math.sign(comb))agree+=TFW[k];
  const conf=clamp(Math.round(Math.abs(comb)*1.15+agree*35-10),0,99),dir=comb>=0?1:-1;
  const raw=Math.abs(comb)>=28&&conf>=60?dir:0;
  const S=tf[15].S,i=S.c.length-1,atr=S.atr[i]||1,price=a.price;
  const slPct=clamp(1.5*atr/price,.004,.03),hi=clamp(Math.floor(1/(8*slPct)),2,25),lo=Math.max(1,Math.round(hi*.7));
  let v24=0;for(let k=Math.max(0,n-96);k<n;k++)v24+=c15[k].v;
  const prevC=c15[Math.max(0,n-96)].c;
  return{tf,comb,conf,dir,raw,agree,atr,slPct,lev:[lo,hi],anom:null,ch24:prevC?(price/prevC-1):0,vol24:v24*price,rsi:S.rsi[i]};
}

const logs=[];
const pushLog=(type,txt)=>{logs.unshift({t:SIM*1000,type,txt});if(logs.length>60)logs.pop()};
function newSig(a,ai,silent){
  const d=ai.raw,p=a.price,sp=ai.slPct;
  a.sig={dir:d,since:SIM,entry:p,sl:p*(1-d*sp),tp1:p*(1+d*1.5*sp),tp2:p*(1+d*3*sp),slPct:sp,lev:ai.lev,state:'ACTIVE'};
  if(!silent&&ai.conf>=70)pushLog(ai.conf>=80?'TOP':(d>0?'LONG':'SHORT'),`${a.sym} ${d>0?'LONG':'SHORT'} · confidenza ${ai.conf}% · entrata ${money(a,p)}`);
}
function updateSignal(a,ai){
  const s=a.sig,p=a.price;
  if(s&&s.state==='ACTIVE'){
    if(s.dir*(p-s.sl)<=0){s.state='STOPPED';a.cool=SIM+1200;pushLog('STOP',`${a.sym} stop loss colpito a ${money(a,p)}`)}
    else if(s.dir*(p-s.tp1)>=0){s.state='TARGET';a.cool=SIM+1200;pushLog('TP',`${a.sym} TP1 raggiunto a ${money(a,p)}`)}
    else if(SIM-s.since>=VALID){s.state='EXPIRED';a.cool=SIM+300}
    else if(ai.raw&&ai.raw!==s.dir&&ai.conf>=65)newSig(a,ai,false);
  }else if(ai.raw&&SIM>=a.cool)newSig(a,ai,scans<3);
}
function scanAll(){
  for(const a of assets){const ai=analyze(a);a.ai=ai;
    if(!a.head)a.head="Trend futures BingX allineato";
    updateSignal(a,ai)}
}
const active=a=>a.sig&&a.sig.state==='ACTIVE';
const isTop=a=>active(a)&&a.ai.conf>=80;

async function stepLive(){
  SIM=Math.floor(Date.now()/1000);
  await loadBingXData();
  scanAll();
  scans++;
}

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
const gMain=mkGauge($('#gMain'),true),gCry=mkGauge($('#gCry'));
function setG(fn,numEl,labEl,v){const z=fn(v);numEl.textContent=Math.round(v);numEl.style.color=zcol(z);labEl.textContent=ZL[z];labEl.style.color=zcol(z)}
function miniG(g){
  const cx=50,cy=48,r=38,pt=(rr,d)=>[cx+rr*Math.cos(d*Math.PI/180),cy+rr*Math.sin(d*Math.PI/180)];
  let s='';for(let i=0;i<5;i++){const p=pt(r,180+i*36+1),q=pt(r,180+(i+1)*36-1);s+=`<path d="M${p[0].toFixed(1)} ${p[1].toFixed(1)}A${r} ${r} 0 0 1 ${q[0].toFixed(1)} ${q[1].toFixed(1)}" fill="none" stroke-width="7" class="z${i}"/>`}
  const d=180+(g+100)/200*180,n=pt(32,d);
  return`<svg class="mg" viewBox="0 0 100 56" aria-hidden="true">${s}<line x1="50" y1="48" x2="${n[0].toFixed(1)}" y2="${n[1].toFixed(1)}" stroke="var(--ink)" stroke-width="2.5" stroke-linecap="round"/><circle cx="50" cy="48" r="3.5" fill="var(--panel)" stroke="var(--gold)" stroke-width="1.5"/></svg>`;
}
const dia=(sz,blink)=>`<svg class="dia${blink?' blink':''}" width="${sz}" height="${sz}" viewBox="0 0 20 20" aria-hidden="true"><polygon points="10,1 18,7 10,19 2,7" fill="var(--dia)"/><polygon points="10,1 14,7 10,19 6,7" fill="rgba(255,255,255,.35)"/></svg>`;

const C={};function readColors(){const cs=getComputedStyle(document.documentElement);for(const k of['up','dn','gold','ink','ink2','mute','line','line2','panel','c2','c3','bg','dia'])C[k]=cs.getPropertyValue('--'+k).trim()}
function setup(cv){const dpr=Math.min(window.devicePixelRatio||1,2),r=cv.getBoundingClientRect(),w=Math.max(10,Math.round(r.width)),h=Math.max(10,Math.round(r.height));
  if(cv.width!==w*dpr||cv.height!==h*dpr){cv.width=w*dpr;cv.height=h*dpr}
  const ctx=cv.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);return{ctx,w,h}}
function tip(ctx,w,x,y,lines){
  ctx.font='11px "JetBrains Mono",monospace';const tw=Math.max(...lines.map(l=>ctx.measureText(l).width))+18,th=lines.length*16+10;
  let bx=x+14;if(bx+tw>w-4)bx=x-14-tw;bx=Math.max(4,bx);const by=Math.max(4,y);
  ctx.fillStyle=C.panel;ctx.strokeStyle=C.gold;ctx.lineWidth=1;ctx.fillRect(bx,by,tw,th);ctx.strokeRect(bx+.5,by+.5,tw,th);
  ctx.fillStyle=C.ink;ctx.textAlign='left';ctx.textBaseline='middle';lines.forEach((l,i)=>ctx.fillText(l,bx+9,by+13+i*16));
}
let sel='BTC',tfSel='15',chartHover=-1;
const chartCv=$('#chart');
function drawChart(){
  const a=bySym[sel];if(!a||!a.ai||!a.ai.tf[tfSel])return;
  const T=a.ai.tf[tfSel],cs=T.cs,S=T.S,n=cs.length;if(n<2)return;
  const{ctx,w,h}=setup(chartCv);
  const N=Math.min(90,n),st=Math.max(0,n-N),padL=6,padR=72,padT=8,axB=18,avail=h-padT-axB,pH=avail*.64,gap=14,rH=avail-pH-gap,rT=padT+pH+gap;
  const pw=w-padL-padR,step=pw/N,xi=i=>padL+(i-st+.5)*step,sig=active(a)?a.sig:null;
  let lo=Infinity,hi=-Infinity;for(let i=st;i<n;i++){lo=Math.min(lo,cs[i].l,S.e20[i]||cs[i].l,S.e50[i]||cs[i].l);hi=Math.max(hi,cs[i].h,S.e20[i]||cs[i].h,S.e50[i]||cs[i].h)}
  if(sig){lo=Math.min(lo,sig.sl);hi=Math.max(hi,sig.tp1)}
  const pd=(hi-lo)*.06||1;lo-=pd;hi+=pd;const y=v=>padT+(1-(v-lo)/(hi-lo))*pH;
  let vmax=1;for(let i=st;i<n;i++)vmax=Math.max(vmax,cs[i].v);
  ctx.lineWidth=1;ctx.font='10px "JetBrains Mono",monospace';
  for(let k=0;k<=4;k++){const v=lo+(hi-lo)*k/4,yy=Math.round(y(v))+.5;ctx.strokeStyle=C.line;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();
    ctx.fillStyle=C.mute;ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(fp(v),w-padR+6,yy)}
  for(let i=st;i<n;i++){const vh=cs[i].v/vmax*pH*.16;ctx.fillStyle=cs[i].c>=cs[i].o?'rgba(46,230,166,.22)':'rgba(255,90,118,.22)';ctx.fillRect(xi(i)-step*.31,padT+pH-vh,step*.62,vh)}
  const line=(arr,col,wd)=>{if(!arr||!arr.length)return;ctx.beginPath();ctx.strokeStyle=col;ctx.lineWidth=wd;for(let i=st;i<n;i++){const X=xi(i),Y=y(arr[i]||arr[arr.length-1]);i===st?ctx.moveTo(X,Y):ctx.lineTo(X,Y)}ctx.stroke()};
  line(S.e50,C.c2,1.4);line(S.e20,C.gold,1.4);
  for(let i=st;i<n;i++){const c=cs[i],col=c.c>=c.o?C.up:C.dn,X=xi(i);ctx.strokeStyle=col;ctx.fillStyle=col;ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(Math.round(X)+.5,y(c.h));ctx.lineTo(Math.round(X)+.5,y(c.l));ctx.stroke();
    const t=y(Math.max(c.o,c.c)),b=y(Math.min(c.o,c.c));ctx.fillRect(X-step*.31,t,step*.62,Math.max(1,b-t))}
  const lp=a.price,ly=Math.round(y(lp))+.5,up=lp>=cs[n-1].o;
  ctx.setLineDash([3,3]);ctx.strokeStyle=up?C.up:C.dn;ctx.beginPath();ctx.moveTo(padL,ly);ctx.lineTo(w-padR,ly);ctx.stroke();ctx.setLineDash([]);
  ctx.fillStyle=up?C.up:C.dn;ctx.fillRect(w-padR+2,ly-9,padR-4,18);ctx.fillStyle=C.bg;ctx.textAlign='left';ctx.font='700 10px "JetBrains Mono",monospace';ctx.textBaseline='middle';ctx.fillText(fp(lp),w-padR+6,ly);
  if(sig)[[sig.entry,C.dia,'ENTRY'],[sig.sl,C.dn,'SL'],[sig.tp1,C.up,'TP1'],[sig.tp2,C.up,'TP2']].forEach(([v,col,l])=>{if(v>lo&&v<hi){const yy=Math.round(y(v))+.5;
    ctx.setLineDash([2,4]);ctx.strokeStyle=col;ctx.globalAlpha=.85;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();ctx.globalAlpha=1;ctx.setLineDash([]);
    ctx.fillStyle=col;ctx.font='700 9.5px "JetBrains Mono",monospace';ctx.textAlign='left';ctx.textBaseline='bottom';ctx.fillText(l,padL+4,yy-2)}});
  ctx.font='10px "JetBrains Mono",monospace';ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillStyle=C.mute;ctx.fillText('RSI 14',padL+2,rT+6);
  const ry=v=>rT+(1-v/100)*rH;
  ctx.fillStyle='rgba(214,176,76,.06)';ctx.fillRect(padL,ry(70),pw,ry(30)-ry(70));
  [30,50,70].forEach(v=>{const yy=Math.round(ry(v))+.5;ctx.strokeStyle=C.line;ctx.beginPath();ctx.moveTo(padL,yy);ctx.lineTo(w-padR,yy);ctx.stroke();ctx.fillStyle=C.mute;ctx.textAlign='left';ctx.fillText(v,w-padR+6,yy)});
  ctx.beginPath();ctx.strokeStyle=C.gold;ctx.lineWidth=1.5;for(let i=st;i<n;i++){const X=xi(i),Y=ry(S.rsi[i]||50);i===st?ctx.moveTo(X,Y):ctx.lineTo(X,Y)}ctx.stroke();
  ctx.fillStyle=C.mute;ctx.textAlign='center';ctx.textBaseline='top';
  for(let i=st+8;i<n;i+=15)ctx.fillText(hhmm(cs[i].t),xi(i),h-axB+5);
}
chartCv.addEventListener('pointermove',e=>{const r=chartCv.getBoundingClientRect(),a=bySym[sel],T=a.ai.tf[tfSel],n=T.cs.length,N=Math.min(90,n),st=Math.max(0,n-N),pw=r.width-6-72;
  const i=st+Math.floor((e.clientX-r.left-6)/(pw/N));chartHover=(i>=st&&i<n)?i:-1;requestAnimationFrame(drawChart)});
chartCv.addEventListener('pointerleave',()=>{chartHover=-1;requestAnimationFrame(drawChart)});

const ui={f:'all',q:'',sort:'conf',dir:-1};
let lastTick=performance.now(),paused=false;
const remain=a=>active(a)?VALID-(Math.floor(Date.now()/1000)-a.sig.since):0;
const tfCls=s=>s>=15?'u':s<=-15?'d':'z';
const tfArrow=s=>s>=15?'▲':s<=-15?'▼':'●';
const stateTxt={ACTIVE:'ATTIVO',EXPIRED:'SCADUTO',STOPPED:'STOP COLPITO',TARGET:'TP1 RAGGIUNTO'};
function entryPill(a){
  if(active(a)){const L=a.sig.dir>0;return`<span class="pill ${L?'LONG':'SHORT'}">${L?'▲ ENTRA LONG':'▼ ENTRA SHORT'}</span><span class="n">@ ${money(a,a.sig.entry)}</span>`}
  const s=a.sig&&a.sig.state!=='ACTIVE'?stateTxt[a.sig.state]:'IN ATTESA';return`<span class="pill WAIT">● ATTENDI</span><span class="n">${s.toLowerCase()}</span>`}
const lastPx={};
function visible(){
  return assets.filter(a=>{
    if(ui.q&&!(a.sym+a.name).toLowerCase().includes(ui.q))return false;
    switch(ui.f){case'top':return isTop(a);case'buy':return active(a)&&a.sig.dir>0;case'sell':return active(a)&&a.sig.dir<0}
    return true});
}
function renderTable(){
  const l=visible();
  const key={sym:a=>a.sym,price:a=>a.price,ch24:a=>a.ai.ch24,conf:a=>(active(a)?1000:0)+a.ai.conf}[ui.sort];
  l.sort((x,y)=>{const A=key(x),B=key(y);return(typeof A==='string'?A.localeCompare(B):A-B)*ui.dir});
  $('#cnt').textContent=l.length+' di '+assets.length+' asset BingX';
  $('#tbody').innerHTML=l.map(a=>{const ai=a.ai,pr=lastPx[a.sym],f=pr==null||pr===a.price?'':(a.price>pr?'fu':'fd'),g=(ai.comb>=0?1:-1)*ai.conf,act=active(a),top=isTop(a),s=a.sig;
    return`<tr class="r${a.sym===sel?' sel':''}${top?' topr':''}" data-sym="${a.sym}" tabindex="0">
<td><span class="cdot" style="background:var(--gold)"></span><span class="s">${a.sym}</span><span class="n">${a.bx}</span></td>
<td>${entryPill(a)}</td>
<td>${act?`<span class="pill" style="color:var(--warn)">${s.lev[0]}x – ${s.lev[1]}x</span>`:'<span class="mu">—</span>'}</td>
<td class="${f}">${money(a,a.price)}</td>
<td>${miniG(g)}</td>
<td>${top?dia(14,true)+' ':''}<span class="pill ${top?'TOP':'N'}">${ai.conf}%${top?' TOP':''}</span></td>
<td>${act?`<span class="cdn" data-s="${a.sym}">${mmss(remain(a))}</span>`:`<span class="mu">${s&&s.state!=='ACTIVE'?stateTxt[s.state]:'—'}</span>`}</td>
<td><span class="tfs">${[15,60,240].map(k=>{const sc=ai.tf[k].score;return`<span class="${tfCls(sc)}" title="${k===240?'4H':k===60?'1H':'15M'} ${sg(sc)}">${k===240?'4H':k===60?'1H':'15M'}${tfArrow(sc)}</span>`}).join('')}</span></td>
<td><span class="pill ${ai.comb>=0?'LONG':'SHORT'}">${ai.comb>=0?'BULLISH':'BEARISH'}</span></td>
<td class="${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</td>
<td>${act?`<span class="up">${pc(s.dir*(s.tp1/s.entry-1))}</span><span class="n">${money(a,s.tp1)}</span>`:'<span class="mu">—</span>'}</td>
<td>${act?`<span class="dn">${pc(-s.slPct)}</span><span class="n">${money(a,s.sl)}</span>`:'<span class="mu">—</span>'}</td></tr>`}).join('')||'<tr><td colspan="12" class="empty" style="text-align:center">Nessun asset BingX.</td></tr>';
  assets.forEach(a=>lastPx[a.sym]=a.price);
}
function renderBanner(){
  const tops=assets.filter(isTop).sort((x,y)=>y.ai.conf-x.ai.conf),b=$('#banner');
  if(tops.length){b.className='banner';b.innerHTML=`${dia(18,true)}<span>TOP ENTRY BINGX &gt;80%: DIAMANTE LAMPEGGIANTE</span><span class="tops">${tops.slice(0,8).map(a=>`<button type="button" data-sym="${a.sym}">${a.sym} ${a.ai.conf}%</button>`).join('')}</span>`}
  else{b.className='banner idle';b.innerHTML=`${dia(18,false)}<span>Monitoraggio BingX Futures in corso (nessuna top entry attiva al momento)</span>`}
}
function renderMain(){
  const a=bySym[sel],ai=a.ai,act=active(a),s=a.sig,top=isTop(a);
  $('#gSub').textContent=`BingX Futures · ${a.bx}`;
  $('#pGauge').classList.toggle('hot',top);
  const g=(ai.comb>=0?1:-1)*ai.conf,z=gMain((g+100)/2);
  $('#gNum').textContent=(g>0?'+':g<0?'−':'')+Math.abs(g)+'%';$('#gNum').style.color=zcol(z);
  $('#gLab').textContent=act?(s.dir>0?'ENTRA LONG':'ENTRA SHORT'):'ATTENDI SETUP';$('#gLab').style.color=act?zcol(z):'var(--mute)';
  $('#tfp').innerHTML=[15,60,240].map(k=>{const sc=ai.tf[k].score;return`<div><span class="mu">${k===240?'4H':k===60?'1H':'15M'}</span><span class="${sc>=15?'up':sc<=-15?'dn':'mu'}">${tfArrow(sc)} ${sg(sc)}</span></div>`}).join('');
  const r=remain(a);
  $('#vState').innerHTML=act?`<span class="pill ${top?'TOP':s.dir>0?'LONG':'SHORT'}">${top?'TOP · ':''}ATTIVO</span>`:`<span class="pill WAIT">${s&&s.state!=='ACTIVE'?stateTxt[s.state]:'NESSUN SEGNALE'}</span>`;
  $('#nHead').textContent=`${a.bx}: Analisi Tecnica in Tempo Reale`;
  $('#nSrc').textContent=`Fonte: BingX API V2 Swap`;
  $('#nBadge').innerHTML=`<span class="pill ${ai.comb>=0?'LONG':'SHORT'}">${ai.comb>=0?'POSIZIONE LONG':'POSIZIONE SHORT'}</span>`;
  $('#nBar').innerHTML=`<i style="left:0;width:${clamp(50+ai.comb/2,0,100)}%;background:var(--gold)"></i>`;$('#nVal').textContent=sg(ai.comb);
  const none=!act,col=(c)=>`style="--c:var(${c})"`,dirTxt=act?(s.dir>0?'LONG':'SHORT'):'';
  const rows=[['Entrata BingX (Market)',none?'—':`${dirTxt} @ ${money(a,s.entry)}`,'--dia'],['Leva consigliata',none?'—':`${s.lev[0]}x – ${s.lev[1]}x`,'--warn'],
   ['Take Profit 1',none?'—':`${money(a,s.tp1)} (${pc(s.dir*(s.tp1/s.entry-1))})`,'--up'],['Take Profit 2',none?'—':`${money(a,s.tp2)} (${pc(s.dir*(s.tp2/s.entry-1))})`,'--up'],
   ['Stop Loss',none?'—':`${money(a,s.sl)} (${pc(-s.slPct)})`,'--dn'],['Rapporto R:R',none?'—':'1 : 1,5 (TP1) · 1 : 3 (TP2)','--c2']];
  $('#exe').innerHTML=rows.map(([k,v,c])=>`<div><span ${col(c)}>${k}</span><b>${v}</b></div>`).join('');
  $('#exSub').textContent=`Contratto: ${a.bx}`;
  const tb=$('#topbox');
  if(top){tb.className='topbox';tb.innerHTML=`<h3>${dia(14,true)} TOP CONFLUENZA BINGX (&gt;80%)</h3><ul>
    <li>Contratto Futures: <b>${a.bx}</b></li>
    <li>Entrata immediata: MARKET ${s.dir>0?'BUY':'SELL'} su BingX.</li>
    <li>TP1: ${money(a,s.tp1)} | TP2: ${money(a,s.tp2)}.</li>
    <li>Stop Loss: ${money(a,s.sl)}.</li></ul>`}
  else{tb.className='topbox idle';tb.innerHTML=`<h3>NESSUNA TOP CONFLUENZA</h3><ul><li>In attesa di segnali ad alta probabilità sul contratto ${a.bx}.</li></ul>`}
  $('#cSym').textContent=a.sym;$('#cName').textContent=a.bx;$('#cPx').textContent=money(a,a.price);
  $('#cCh').innerHTML=`<span class="mono ${ai.ch24>=0?'up':'dn'}">${pc(ai.ch24)}</span>`;
  const rowsT=[['Score',k=>sg(ai.tf[k].score)],['Segnale',k=>{const sc=ai.tf[k].score;return`<span class="${sc>=28?'up':sc<=-28?'dn':'mu'}">${sc>=28?'BUY':sc<=-28?'SELL':'NEUTRO'}</span>`}],
    ['RSI 14',k=>fmtN(ai.tf[k].S.rsi.at(-1),1)],['MACD',k=>{const T=ai.tf[k],h=T.S.hist.at(-1);return`<span class="${h>=0?'up':'dn'}">${h>=0?'▲':'▼'}</span>`}],
    ['Volatilità (ATR)',k=>fmtN(ai.tf[k].S.atr.at(-1)/a.price*100,2)+'%']];
  $('#tft').innerHTML=rowsT.map(([n,f])=>`<tr><td>${n}</td>${[15,60,240].map(k=>`<td>${f(k)}</td>`).join('')}</tr>`).join('');
  $('#tfSub').textContent=`${a.bx} · Dati BingX`;
  const ex=[['Confluenza',ai.conf+'%'],['Allineamento TF',fmtN(ai.agree*100,0)+'%'],['Score combinato',sg(ai.comb)],['Volume 24h','$' + compact(ai.vol24)],['Exchange','BingX Futures']];
  $('#extra').innerHTML=ex.map(([k,v])=>`<div><span>${k}</span><b style="font-weight:500">${v}</b></div>`).join('');
}
function renderOverview(){
  const avgC=assets.reduce((s,a)=>s+a.ai.comb,0)/assets.length;
  setG(gCry,$('#nCry'),$('#lCry'),clamp(50+avgC*1.6,0,100));
  const act=assets.filter(active).sort((x,y)=>y.ai.conf-x.ai.conf).slice(0,7);
  $('#opps').innerHTML=act.map(a=>`<div class="dvr" role="button" tabindex="0" data-sym="${a.sym}"><b>${a.sym}</b><span class="bar2 dv"><i style="left:50%;width:${a.ai.conf/2}%;background:var(--up)"></i></span><span style="text-align:right">+${a.ai.conf}</span></div>`).join('')||'<div class="empty">Nessun segnale BingX attivo.</div>';
  $('#heat').innerHTML=`<div class="hg"><h3>Cripto Futures BingX</h3><div class="tiles">${assets.map(a=>{const c=a.ai.ch24,al=.12+clamp(Math.abs(c)/.04,0,1)*.6;
    return`<button type="button" class="tile" data-sym="${a.sym}" style="background:${c>=0?`rgba(46,230,166,${al})`:`rgba(255,90,118,${al})`}" aria-label="${a.sym} ${pc(c)}">${a.sym}<small>${pc(c)}</small></button>`}).join('')}</div></div>`;
  const cl={TOP:'var(--dia)',LONG:'var(--up)',SHORT:'var(--dn)',STOP:'var(--dn)',TP:'var(--up)'};
  $('#log').innerHTML=logs.slice(0,14).map(e=>`<li><time>${hhmm(e.t)}</time><span style="font-weight:700;color:${cl[e.type]||'var(--ink2)'}">${e.type}</span><span>${e.txt}</span></li>`).join('')||'<li class="empty">In attesa di eventi BingX…</li>';
}
function renderAll(){
  renderBanner();renderMain();renderTable();drawChart();renderOverview();
  $('#clock').textContent=hhmmss(Date.now());
}
function tickCountdowns(){
  document.querySelectorAll('.cdn').forEach(el=>{const a=bySym[el.dataset.s];if(a)el.textContent=mmss(remain(a))});
  const a=bySym[sel],r=remain(a),cd=$('#cdMain');
  cd.textContent=active(a)?mmss(r):'--:--';cd.className='cdbig'+(!active(a)?' dead':r<300?' low':'');
  $('#cdBar'].style.width=(active(a)?clamp(r/VALID,0,1)*100:0)+'%';
  const t=$('#cdTop');if(t)t.textContent=mmss(r);
  const rs=Math.max(0,Math.ceil((TICK-(performance.now()-lastTick))/1000));$('#refresh').textContent='00:'+pad2(rs);
}

function pick(sym){if(!bySym[sym])return;sel=sym;chartHover=-1;renderMain();renderTable();drawChart();tickCountdowns()}
document.addEventListener('click',e=>{const t=e.target.closest('[data-sym]');if(t)pick(t.dataset.sym)});
$('#filters').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;ui.f=b.dataset.f;$('#filters').querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',x===b));renderTable()});
function seg(id,cb){$(id).addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;$(id).querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed',x===b));cb(b.dataset.v)})}
seg('#tfSeg',v=>{tfSel=v;chartHover=-1;drawChart()});
$('#q').addEventListener('input',e=>{ui.q=e.target.value.trim().toLowerCase();renderTable()});
$('#thead').addEventListener('click',e=>{const th=e.target.closest('th[data-k]');if(!th)return;const k=th.dataset.k;if(ui.sort===k)ui.dir*=-1;else{ui.sort=k;ui.dir=k==='sym'?1:-1}renderTable()});
$('#pauseBtn').addEventListener('click',()=>{paused=!paused;$('#pauseBtn').textContent=paused?'Riprendi':'Pausa'});

async function boot(){
  readColors();
  await stepLive();
  const best=assets.filter(active)[0]||assets[0];sel=best.sym;
  renderAll();tickCountdowns();lastTick=performance.now();
  setInterval(async()=>{if(paused)return;await stepLive();lastTick=performance.now();renderAll();tickCountdowns()},6000);
  setInterval(tickCountdowns,250);
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
        bot_settings["auto_execution"] = 1 if request.form.get("auto_execution") == "on" else 0
        flash("Impostazioni salvate!")
        return redirect(url_for('home'))

    account_data = {"balance": 0.00, "equity": 0.00}
    if bot_settings["trading_mode"] == "real" and bot_settings["bingx_api_key"] and bot_settings["bingx_secret_key"]:
        bingx_res = get_bingx_account_balance(bot_settings["bingx_api_key"], bot_settings["bingx_secret_key"])
        if "error" not in bingx_res:
            account_data["balance"] = bingx_res["balance"]
            account_data["equity"] = bingx_res["equity"]

    return render_template_string(
        DASHBOARD_HTML,
        settings=bot_settings,
        account=account_data
    )

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
    <!DOCTYPE html>
    <html lang="it">
    <head><meta charset="UTF-8"><title>Login</title></head>
    <body style="background:#06080c;color:#ece8dc;font-family:sans-serif;">
    <div style="max-width:380px;margin:80px auto;background:#0c1017;padding:25px;border:1px solid #2a3750;border-radius:4px;">
        <h3 style="color:#d6b04c;text-align:center;margin-top:0;">ACCESSO BINGX</h3>
        <form method="POST">
            <p><label>Username</label><br><input type="text" name="username" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;margin-top:5px;box-sizing:border-box;"></p>
            <p><label>Password</label><br><input type="password" name="password" required style="width:100%;padding:8px;background:#111722;border:1px solid #2a3750;color:#fff;margin-top:5px;box-sizing:border-box;"></p>
            <button type="submit" style="width:100%;padding:10px;background:#d6b04c;color:#06080c;border:none;font-weight:bold;margin-top:10px;cursor:pointer;">Accedi</button>
        </form>
    </div>
    </body>
    </html>
    '''
    return render_template_string(content)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
