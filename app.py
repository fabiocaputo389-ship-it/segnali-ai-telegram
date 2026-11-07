import time
import hmac
import hashlib
import threading
import requests
from datetime import datetime
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# Endpoint ufficiali BingX
BINGX_VST_URL = "https://open-api-vst.bingx.com"
BINGX_LIVE_URL = "https://open-api.bingx.com"

# Stato globale dello Scanner Live in background
scanner_state = {
    "running": True,
    "last_scan": "Inizializzazione...",
    "total_contracts": 0,
    "assets": [],
    "logs": [],
    "bingx_config": {
        "env": "VST",          # "VST" per Demo, "LIVE" per Reale
        "api_key": "",
        "secret_key": "",
        "margin_usdt": 100.0,
        "leverage": 10,
        "auto_bot": False
    },
    "executed_symbols": set()  # Evita doppi ordini sullo stesso segnale
}

def add_log(msg, level="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    scanner_state["logs"].insert(0, {"time": timestamp, "msg": msg, "level": level})
    scanner_state["logs"] = scanner_state["logs"][:60]

def sign_bingx(params, secret_key):
    query_string = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string, signature

def execute_bingx_trade(symbol, price, is_long, sl, tp1, is_auto=False):
    cfg = scanner_state["bingx_config"]
    if not cfg["api_key"] or not cfg["secret_key"]:
        add_log(f"⚠️ Ordine bloccato su {symbol}: inserisci API Key e Secret Key BingX.", "ERROR")
        return {"code": -1, "msg": "API Key mancante"}

    base_url = BINGX_LIVE_URL if cfg["env"] == "LIVE" else BINGX_VST_URL
    side = "BUY" if is_long else "SELL"
    pos_side = "LONG" if is_long else "SHORT"

    notional = cfg["margin_usdt"] * cfg["leverage"]
    qty = round(notional / price, 3 if price > 50 else 1)
    if qty <= 0:
        qty = 1

    decimals = 2 if price > 10 else (4 if price > 0.1 else 6)
    sl_price = round(sl, decimals)
    tp_price = round(tp1, decimals)

    params = {
        "symbol": symbol,
        "side": side,
        "positionSide": pos_side,
        "type": "MARKET",
        "quantity": str(qty),
        "stopLoss": f'{{"type":"STOP_MARKET","stopPrice":{sl_price},"workingType":"MARK_PRICE"}}',
        "takeProfit": f'{{"type":"TAKE_PROFIT_MARKET","stopPrice":{tp_price},"workingType":"MARK_PRICE"}}',
        "timestamp": str(int(time.time() * 1000))
    }

    prefix = "🤖 [AUTO-BOT]" if is_auto else "⚡ [MANUALE]"
    add_log(f"{prefix} Invio {pos_side} su {symbol} | Qty: {qty} | Entry: ${price} | SL: ${sl_price} | TP1: ${tp_price}", "WARN")

    try:
        query_string, signature = sign_bingx(params, cfg["secret_key"])
        url = f"{base_url}/openApi/swap/v2/trade/order?{query_string}&signature={signature}"
        headers = {"X-BX-APIKEY": cfg["api_key"], "Content-Type": "application/json"}
        res = requests.post(url, headers=headers, timeout=10).json()

        if res.get("code") == 0:
            order_id = res.get("data", {}).get("order", {}).get("orderId", "OK")
            add_log(f"{prefix} ✅ ESEGUITO SU BINGX ({cfg['env']})! Order ID: {order_id}", "SUCCESS")
        else:
            add_log(f"{prefix} ❌ Rifiuto BingX (Code {res.get('code')}): {res.get('msg')}", "ERROR")
        return res
    except Exception as e:
        add_log(f"{prefix} ❌ Errore connessione BingX: {str(e)}", "ERROR")
        return {"code": -1, "msg": str(e)}

# Motore di scansione continua dietro le quinte su BingX
def bingx_background_scanner():
    add_log("🚀 Motore Scanner BingX avviato in background. Interrogazione contratti reali...", "SUCCESS")
    while scanner_state["running"]:
        try:
            res = requests.get(f"{BINGX_LIVE_URL}/openApi/swap/v2/quote/ticker", timeout=8).json()
            if res.get("code") == 0 and isinstance(res.get("data"), list):
                raw_tickers = res["data"]
                scanner_state["total_contracts"] = len(raw_tickers)

                # Filtra i contratti USDT più liquidi e attivi su BingX
                usdt_pairs = [
                    t for t in raw_tickers
                    if t.get("symbol", "").endswith("-USDT") and float(t.get("quoteVolume", 0)) > 5000000
                ]
                # Ordina per volume 24h decrescente e analizza i top 40 contratti
                usdt_pairs.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
                top_pairs = usdt_pairs[:40]

                processed = []
                for t in top_pairs:
                    sym = t["symbol"]
                    price = float(t["lastPrice"])
                    high24 = float(t["highPrice"])
                    low24 = float(t["lowPrice"])
                    change_pct = float(t["priceChangePercent"])
                    vol24 = float(t["quoteVolume"])

                    if price <= 0:
                        continue

                    # Calcolo reale della volatilità (ATR stimato sul range 24h)
                    real_range = max(high24 - low24, price * 0.008)
                    atr = real_range * 0.35

                    is_long = change_pct >= 0
                    entry = price
                    sl = (entry - atr * 1.1) if is_long else (entry + atr * 1.1)
                    tp1 = (entry + atr * 1.8) if is_long else (entry - atr * 1.8)
                    tp2 = (entry + atr * 3.2) if is_long else (entry - atr * 3.2)

                    # Calcolo Potenza Diamante e Confluenza Dollaro su dati reali BingX
                    abs_chg = abs(change_pct)
                    vol_bonus = 8 if vol24 > 100000000 else (4 if vol24 > 30000000 else 0)
                    diamond_score = min(99, max(45, int(60 + abs_chg * 3.8 + vol_bonus)))
                    dollar_conf = round(min(98.9, max(50.0, 58.0 + abs_chg * 3.5 + vol_bonus)), 1)

                    entra_ora = diamond_score >= 86 and dollar_conf >= 84.0

                    decimals = 2 if price >= 10 else (4 if price >= 0.1 else (6 if price >= 0.001 else 8))

                    asset_obj = {
                        "symbol": sym,
                        "price": price,
                        "decimals": decimals,
                        "change24h": change_pct,
                        "volume24h": round(vol24 / 1_000_000, 1),
                        "is_long": is_long,
                        "diamond": diamond_score,
                        "dollar": dollar_conf,
                        "entra_ora": entra_ora,
                        "entry": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2
                    }
                    processed.append(asset_obj)

                    # Esecuzione Automatica dietro le quinte se AUTO-BOT è attivo
                    if entra_ora and scanner_state["bingx_config"]["auto_bot"]:
                        if sym not in scanner_state["executed_symbols"]:
                            scanner_state["executed_symbols"].add(sym)
                            execute_bingx_trade(sym, entry, is_long, sl, tp1, is_auto=True)

                # Ordina mettendo prima i segnali ENTRA ORA
                processed.sort(key=lambda x: (x["entra_ora"], x["diamond"]), reverse=True)
                scanner_state["assets"] = processed
                scanner_state["last_scan"] = datetime.now().strftime("%H:%M:%S CET")

        except Exception as e:
            add_log(f"⚠️ Errore ciclo scanner BingX: {str(e)}", "ERROR")

        time.sleep(5)  # Scansiona BingX ogni 5 secondi

# Avvia il thread dello scanner in background all'avvio del server
threading.Thread(target=bingx_background_scanner, daemon=True).start()

# --- ROTTE API FLASK ---

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/state", methods=["GET"])
def get_state():
    return jsonify({
        "last_scan": scanner_state["last_scan"],
        "total_contracts": scanner_state["total_contracts"],
        "assets": scanner_state["assets"],
        "logs": scanner_state["logs"],
        "config": {
            "env": scanner_state["bingx_config"]["env"],
            "has_keys": bool(scanner_state["bingx_config"]["api_key"]),
            "margin_usdt": scanner_state["bingx_config"]["margin_usdt"],
            "leverage": scanner_state["bingx_config"]["leverage"],
            "auto_bot": scanner_state["bingx_config"]["auto_bot"]
        }
    })

@app.route("/api/config", methods=["POST"])
def update_config():
    data = request.json
    cfg = scanner_state["bingx_config"]
    if "env" in data:
        cfg["env"] = data["env"]
    if data.get("api_key"):
        cfg["api_key"] = data["api_key"].strip()
    if data.get("secret_key"):
        cfg["secret_key"] = data["secret_key"].strip()
    if "margin_usdt" in data:
        cfg["margin_usdt"] = float(data["margin_usdt"])
    if "leverage" in data:
        cfg["leverage"] = int(data["leverage"])
    if "auto_bot" in data:
        cfg["auto_bot"] = bool(data["auto_bot"])
        state_str = "ATTIVATO" if cfg["auto_bot"] else "DISATTIVATO"
        add_log(f"🤖 AUTO-BOT BINGX {state_str} sul server.", "SUCCESS" if cfg["auto_bot"] else "INFO")

    add_log(f"⚙️ Configurazione aggiornata: Ambiente {cfg['env']} | Margine {cfg['margin_usdt']} USDT | Leva {cfg['leverage']}x", "INFO")
    return jsonify({"status": "ok"})

@app.route("/api/manual-trade", methods=["POST"])
def manual_trade():
    data = request.json
    sym = data.get("symbol")
    asset = next((a for a in scanner_state["assets"] if a["symbol"] == sym), None)
    if not asset:
        return jsonify({"code": -1, "msg": "Asset non trovato"})
    res = execute_bingx_trade(sym, asset["entry"], asset["is_long"], asset["sl"], asset["tp1"], is_auto=False)
    return jsonify(res)

# --- INTERFACCIA WEB (BIANCO, NERO, VERDE, ROSSO) ---

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro • BingX Background Scanner</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] },
          colors: { neonGreen: '#00FF66', neonRed: '#FF334B' }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    .box { background: #08080A; border: 1px solid #1E1E24; }
    @keyframes pulseGlow { 0%, 100% { box-shadow: 0 0 10px rgba(0, 255, 102, 0.3); } 50% { box-shadow: 0 0 22px rgba(0, 255, 102, 0.75); } }
    .live-glow { animation: pulseGlow 2s infinite ease-in-out; }
  </style>
</head>
<body class="min-h-screen flex flex-col p-3 sm:p-5 gap-4 max-w-[1920px] mx-auto w-full">

  <!-- HEADER -->
  <header class="box rounded-2xl p-4 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <div class="w-10 h-10 rounded-xl bg-white text-black flex items-center justify-center font-extrabold text-lg">⚡</div>
      <div>
        <div class="flex items-center gap-2">
          <h1 class="text-base sm:text-lg font-extrabold text-white">ApexScan <span class="text-neonGreen">BINGX LIVE</span></h1>
          <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-neonGreen/15 text-neonGreen border border-neonGreen/40 live-glow">
            ● SCANNER ATTIVO 24/7
          </span>
        </div>
        <p class="text-[11px] font-mono text-slate-400 mt-0.5" id="statusSub">Connessione al motore Python BingX...</p>
      </div>
    </div>

    <div class="flex flex-wrap items-center gap-2">
      <input type="text" id="searchInput" placeholder="Cerca contratto..." class="bg-black border border-[#1E1E24] focus:border-white rounded-xl px-3 py-1.5 text-xs text-white outline-none w-40"/>
      
      <button id="autoBotBtn" onclick="toggleAutoBot()" class="px-3.5 py-1.5 rounded-xl text-xs font-mono font-bold border border-[#1E1E24] bg-black text-slate-400 transition-all">
        AUTO-BOT: OFF
      </button>

      <button onclick="openModal()" class="px-3.5 py-1.5 rounded-xl text-xs font-bold bg-white text-black hover:bg-slate-200 transition-all">
        ⚙️ Chiavi API BingX
      </button>
    </div>
  </header>

  <!-- KPI -->
  <section class="grid grid-cols-2 lg:grid-cols-4 gap-3">
    <div class="box rounded-2xl p-4">
      <span class="text-[10px] uppercase font-bold text-slate-400">Contratti Scansionati</span>
      <div id="kpiTotal" class="text-xl sm:text-2xl font-extrabold font-mono text-white mt-1">--</div>
    </div>
    <div class="box rounded-2xl p-4">
      <span class="text-[10px] uppercase font-bold text-slate-400">Segnali 💎 ENTRA ORA</span>
      <div id="kpiEnterNow" class="text-xl sm:text-2xl font-extrabold font-mono text-neonGreen mt-1">--</div>
    </div>
    <div class="box rounded-2xl p-4">
      <span class="text-[10px] uppercase font-bold text-slate-400">Stato API BingX</span>
      <div id="kpiApiStatus" class="text-base sm:text-lg font-extrabold font-mono text-white mt-1">Non Connesso</div>
    </div>
    <div class="box rounded-2xl p-4">
      <span class="text-[10px] uppercase font-bold text-slate-400">Rischio &amp; Leva</span>
      <div id="kpiRisk" class="text-base sm:text-lg font-extrabold font-mono text-white mt-1">100 USDT • 10x</div>
    </div>
  </section>

  <!-- TABELLA PARAMETRI REALI IN CHIARO -->
  <section class="box rounded-2xl overflow-hidden">
    <div class="px-4 py-3 border-b border-[#1E1E24] flex items-center justify-between">
      <h2 class="text-sm font-bold text-white">Screener Live BingX Perpetuals • Parametri Completi in Chiaro</h2>
      <span class="text-[11px] font-mono text-slate-400" id="lastScanLabel">Ultimo scan: --</span>
    </div>
    <div class="overflow-x-auto">
      <table class="w-full text-left border-collapse font-mono text-xs">
        <thead>
          <tr class="border-b border-[#1E1E24] text-[10px] uppercase text-slate-400 bg-black">
            <th class="py-3 pl-4">Contratto</th>
            <th class="py-3 px-2.5 text-right">Prezzo Reale</th>
            <th class="py-3 px-2.5 text-right">24h %</th>
            <th class="py-3 px-2.5 text-center">💎 Diamante</th>
            <th class="py-3 px-2.5 text-center">💲 Dollaro</th>
            <th class="py-3 px-2.5 text-right">Entry</th>
            <th class="py-3 px-2.5 text-right">Stop Loss</th>
            <th class="py-3 px-2.5 text-right">TP 1</th>
            <th class="py-3 px-2.5 text-right">TP 2</th>
            <th class="py-3 px-3 text-right pr-4">BingX</th>
          </tr>
        </thead>
        <tbody id="tableBody" class="divide-y divide-[#121216]"></tbody>
      </table>
    </div>
  </section>

  <!-- CONSOLE LOG SERVER -->
  <section class="box rounded-2xl overflow-hidden">
    <div class="px-4 py-2.5 border-b border-[#1E1E24] flex items-center justify-between">
      <h3 class="text-xs font-bold uppercase text-white">Log Operativo Motore Background BingX</h3>
    </div>
    <div id="logsContainer" class="p-3.5 bg-black font-mono text-[11px] space-y-1 max-h-52 overflow-y-auto"></div>
  </section>

  <!-- MODALE CONFIGURAZIONE -->
  <div id="configModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 p-4">
    <div class="box rounded-2xl w-full max-w-md p-5 space-y-3 text-xs">
      <div class="flex justify-between items-center border-b border-[#1E1E24] pb-2">
        <h2 class="text-sm font-bold text-white">Collega Account BingX</h2>
        <button onclick="closeModal()" class="text-slate-400 hover:text-white">✕</button>
      </div>
      <div>
        <label class="block text-slate-400 mb-1">Ambiente</label>
        <select id="cfgEnv" class="w-full bg-black border border-[#1E1E24] rounded-xl p-2 text-white font-mono">
          <option value="VST">Demo VST (Consigliato per test)</option>
          <option value="LIVE">Live Reale</option>
        </select>
      </div>
      <div>
        <label class="block text-slate-400 mb-1">API Key BingX</label>
        <input type="password" id="cfgKey" placeholder="Incolla API Key..." class="w-full bg-black border border-[#1E1E24] rounded-xl p-2 text-white font-mono"/>
      </div>
      <div>
        <label class="block text-slate-400 mb-1">Secret Key BingX</label>
        <input type="password" id="cfgSecret" placeholder="Incolla Secret Key..." class="w-full bg-black border border-[#1E1E24] rounded-xl p-2 text-white font-mono"/>
      </div>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-slate-400 mb-1">Margine (USDT)</label>
          <input type="number" id="cfgMargin" value="100" class="w-full bg-black border border-[#1E1E24] rounded-xl p-2 text-white font-mono"/>
        </div>
        <div>
          <label class="block text-slate-400 mb-1">Leva (x)</label>
          <input type="number" id="cfgLev" value="10" class="w-full bg-black border border-[#1E1E24] rounded-xl p-2 text-white font-mono"/>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button onclick="closeModal()" class="px-4 py-2 rounded-xl bg-[#121216] text-slate-300">Chiudi</button>
        <button onclick="saveConfig()" class="px-4 py-2 rounded-xl bg-white text-black font-bold">Salva &amp; Collega</button>
      </div>
    </div>
  </div>

  <script>
    let currentState = { assets: [], config: {} };

    function fmt(val, dec) {
      return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
    }

    async function syncFromServer() {
      try {
        const res = await fetch('/api/state');
        const data = await res.json();
        currentState = data;

        document.getElementById('kpiTotal').textContent = data.total_contracts + ' Perpetuals';
        document.getElementById('lastScanLabel').textContent = 'Ultimo scan BingX: ' + data.last_scan;
        document.getElementById('statusSub').textContent = `Scanner Python connesso a BingX • Aggiornato alle ${data.last_scan}`;

        const enterNowList = data.assets.filter(a => a.entra_ora);
        document.getElementById('kpiEnterNow').textContent = enterNowList.length;

        const cfg = data.config;
        document.getElementById('kpiApiStatus').textContent = cfg.has_keys ? `Connesso (${cfg.env})` : `In attesa chiavi (${cfg.env})`;
        document.getElementById('kpiApiStatus').className = cfg.has_keys ? 'text-base sm:text-lg font-extrabold font-mono text-neonGreen mt-1' : 'text-base sm:text-lg font-extrabold font-mono text-neonRed mt-1';
        document.getElementById('kpiRisk').textContent = `${cfg.margin_usdt} USDT • ${cfg.leverage}x`;

        const botBtn = document.getElementById('autoBotBtn');
        if (cfg.auto_bot) {
          botBtn.textContent = '🤖 AUTO-BOT: ON';
          botBtn.className = 'px-3.5 py-1.5 rounded-xl text-xs font-mono font-bold border border-neonGreen bg-neonGreen/15 text-neonGreen';
        } else {
          botBtn.textContent = 'AUTO-BOT: OFF';
          botBtn.className = 'px-3.5 py-1.5 rounded-xl text-xs font-mono font-bold border border-[#1E1E24] bg-black text-slate-400';
        }

        renderTable();
        renderLogs(data.logs);
      } catch (e) { console.error(e); }
    }

    function renderTable() {
      const q = document.getElementById('searchInput').value.toLowerCase();
      const filtered = currentState.assets.filter(a => a.symbol.toLowerCase().includes(q));
      const tbody = document.getElementById('tableBody');

      tbody.innerHTML = filtered.map(a => {
        const isPos = a.change24h >= 0;
        const badge = a.entra_ora
          ? `<span class="px-2 py-0.5 rounded bg-neonGreen/15 text-neonGreen font-bold border border-neonGreen/30">💎 ENTRA ORA (${a.diamond}%)</span>`
          : `<span class="text-slate-400">💎 ${a.diamond}%</span>`;

        return `
          <tr class="hover:bg-[#121216]">
            <td class="py-3 pl-4 font-bold text-white">${a.symbol}<span class="block text-[9px] text-slate-400 font-normal">Vol: $${a.volume24h}M</span></td>
            <td class="py-3 px-2.5 text-right font-bold text-white">${fmt(a.price, a.decimals)}</td>
            <td class="py-3 px-2.5 text-right ${isPos ? 'text-neonGreen' : 'text-neonRed'}">${isPos?'+':''}${a.change24h.toFixed(2)}%</td>
            <td class="py-3 px-2.5 text-center">${badge}</td>
            <td class="py-3 px-2.5 text-center font-extrabold text-white">💲 ${a.dollar}%</td>
            <td class="py-3 px-2.5 text-right text-white font-bold">${fmt(a.entry, a.decimals)}</td>
            <td class="py-3 px-2.5 text-right text-neonRed font-bold">${fmt(a.sl, a.decimals)}</td>
            <td class="py-3 px-2.5 text-right text-neonGreen font-bold">${fmt(a.tp1, a.decimals)}</td>
            <td class="py-3 px-2.5 text-right text-neonGreen font-bold">${fmt(a.tp2, a.decimals)}</td>
            <td class="py-3 px-3 text-right pr-4">
              <button onclick="manualTrade('${a.symbol}')" class="px-2.5 py-1 rounded bg-white text-black hover:bg-neonGreen font-extrabold text-[10px]">⚡ Esegui</button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function renderLogs(logs) {
      const box = document.getElementById('logsContainer');
      box.innerHTML = logs.map(l => {
        const cls = l.level === 'SUCCESS' ? 'text-neonGreen' : l.level === 'ERROR' ? 'text-neonRed' : l.level === 'WARN' ? 'text-white font-bold' : 'text-slate-400';
        return `<div class="${cls}">[${l.time}] ${l.msg}</div>`;
      }).join('');
    }

    async function toggleAutoBot() {
      const nextState = !currentState.config.auto_bot;
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ auto_bot: nextState })
      });
      syncFromServer();
    }

    async function saveConfig() {
      await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          env: document.getElementById('cfgEnv').value,
          api_key: document.getElementById('cfgKey').value,
          secret_key: document.getElementById('cfgSecret').value,
          margin_usdt: document.getElementById('cfgMargin').value,
          leverage: document.getElementById('cfgLev').value
        })
      });
      closeModal();
      syncFromServer();
    }

    async function manualTrade(symbol) {
      await fetch('/api/manual-trade', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol })
      });
      syncFromServer();
    }

    function openModal() { document.getElementById('configModal').classList.remove('hidden'); document.getElementById('configModal').classList.add('flex'); }
    function closeModal() { document.getElementById('configModal').classList.add('hidden'); document.getElementById('configModal').classList.remove('flex'); }

    document.getElementById('searchInput').addEventListener('input', renderTable);
    syncFromServer();
    setInterval(syncFromServer, 3000);
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
