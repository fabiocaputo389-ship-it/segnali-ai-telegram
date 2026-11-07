import os
import time
import hmac
import hashlib
import urllib.parse
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# ==========================================
# LETTURA VARIABILI DIRETTAMENTE DA RENDER
# ==========================================
API_KEY = os.environ.get("BINGX_API_KEY")
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY")
BASE_URL = "https://open-api.bingx.com"

# Stato globale della modalità di esecuzione ("MANUAL" o "AUTO")
TRADING_MODE = "MANUAL"

def _genera_firma(payload: dict, secret_key: str) -> str:
    """Genera la firma HMAC SHA256 richiesta dalle API Futures di BingX."""
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature

def get_live_market_data():
    """Scarica i prezzi reali live dalle API pubbliche di BingX Futures."""
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get("code") == 0 and "data" in data:
            tickers = data["data"]
            market_map = {}
            for t in tickers:
                symbol = t.get("symbol")
                market_map[symbol] = {
                    "price": float(t.get("lastPrice", 0)),
                    "change24h": float(t.get("priceChangePercent", 0)) * 100,
                    "high24h": float(t.get("highPrice", 0)),
                    "low24h": float(t.get("lowPrice", 0))
                }
            return market_map
    except Exception as e:
        print(f"[MARKET DATA ERROR] Impossibile connettersi a BingX Market Data: {e}")
    return {}

def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_tp: float, prezzo_sl: float):
    """
    CERVELLO OPERATIVO: Apre la posizione a mercato reale su BingX 
    e imposta automaticamente Stop Loss e Take Profit protettivi (con reduceOnly).
    """
    if not API_KEY or not SECRET_KEY:
        print("[BINGX ERROR] BINGX_API_KEY o BINGX_SECRET_KEY non trovate nelle variabili d'ambiente di Render!")
        return {"code": -1, "msg": "API keys missing in Render Environment Variables"}

    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    # --- STEP 1: APERTURA POSIZIONE PRINCIPALE A MERCATO ---
    payload_ordine = {
        "symbol": simbolo,       # Es: "BTC-USDT"
        "side": lato,            # "BUY" o "SELL"
        "positionSide": "LONG" if lato == "BUY" else "SHORT",
        "type": "MARKET",
        "quantity": str(quantita),
        "timestamp": timestamp
    }
    
    query_string = _genera_firma(payload_ordine, SECRET_KEY)
    headers = {"X-BX-APIKEY": API_KEY}
    
    try:
        response = requests.post(f"{url}?{query_string}", headers=headers)
        risultato = response.json()
        
        if risultato.get("code") == 0:
            print(f"[BINGX SUCCESS] Posizione reale {lato} aperta su {simbolo}!")
            
            # --- STEP 2: IMPOSTAZIONE TP E SL PROTETTIVI ---
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET")
            _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET")
        else:
            print(f"[BINGX ERROR API] Risposta: {risultato}")
        
        return risultato
    except Exception as e:
        print(f"[EXCEPT] Errore di connessione a BingX: {e}")
        return {"code": -1, "msg": str(e)}

def _imposta_ordine_protettivo(simbolo: str, lato: str, prezzo_trigger: float, tipo_ordine: str):
    """Funzione di servizio per inviare SL o TP protettivi reali con reduceOnly=True"""
    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    payload = {
        "symbol": simbolo,
        "side": lato,
        "positionSide": "LONG" if lato == "SELL" else "SHORT",
        "type": tipo_ordine,
        "stopPrice": str(prezzo_trigger),
        "workingType": "MARK_PRICE",
        "reduceOnly": "true",
        "timestamp": timestamp
    }
    
    query_string = _genera_firma(payload, SECRET_KEY)
    headers = {"X-BX-APIKEY": API_KEY}
    
    try:
        resp = requests.post(f"{url}?{query_string}", headers=headers)
        res_data = resp.json()
        if res_data.get("code") == 0:
            print(f"[PROTECT SUCCESS] {tipo_ordine} reale impostato a {prezzo_trigger} per {simbolo}")
        else:
            print(f"[PROTECT ERROR] Impossibile impostare {tipo_ordine}: {res_data.get('msg')}")
    except Exception as e:
        print(f"[PROTECT EXCEPT] Errore invio ordine protettivo: {e}")


# ==========================================
# ENDPOINT API FLASK 
# ==========================================
@app.route('/api/market-data', methods=['GET'])
def api_market_data():
    data = get_live_market_data()
    return jsonify({"code": 0, "data": data})

@app.route('/api/esegui-segnale', methods=['POST'])
def webhook_esegui_segnale():
    dati = request.json or {}
    simbolo = dati.get("symbol")
    lato = dati.get("action")
    quantita = float(dati.get("size", 0.001))
    tp = float(dati.get("tp", 0))
    sl = float(dati.get("sl", 0))
    is_auto = dati.get("is_auto", False)

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Bot in modalità Manuale: esecuzione automatica bloccata."})
    
    risultato = esegui_ordine_bingx(simbolo, lato, quantita, tp, sl)
    return jsonify(risultato)

@app.route('/api/set-mode', methods=['POST'])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    mode = dati.get("mode")
    if mode in ["MANUAL", "AUTO"]:
        TRADING_MODE = mode
        print(f"[MODALITY CHANGE] Modalità impostata su: {TRADING_MODE}")
        return jsonify({"code": 0, "mode": TRADING_MODE})
    return jsonify({"code": -1, "msg": "Modalità non valida"})

@app.route('/api/get-mode', methods=['GET'])
def get_trading_mode():
    return jsonify({"code": 0, "mode": TRADING_MODE})


# ==========================================
# INTERFACCIA FRONTEND (DASHBOARD HTML)
# ==========================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro - BingX Live Data &amp; Auto Engine</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] },
          colors: {
            obsidian: { 950: '#000000', 900: '#08080A', 850: '#0E0E11', 800: '#141418', 700: '#1E1E24' },
            emeraldNeon: '#00FF66',
            rubyNeon: '#FF334B'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #000000; }
    ::-webkit-scrollbar-thumb { background: rgba(0, 255, 102, 0.35); border-radius: 9999px; }
    @keyframes flashGreen { 0% { background-color: rgba(0, 255, 102, 0.3); color: #00FF66; } 100% { background-color: transparent; } }
    @keyframes flashRed { 0% { background-color: rgba(255, 51, 75, 0.3); color: #FF334B; } 100% { background-color: transparent; } }
    @keyframes greenPulseGlow { 0%, 100% { box-shadow: 0 0 12px rgba(0, 255, 102, 0.35), inset 0 0 8px rgba(0, 255, 102, 0.2); } 50% { box-shadow: 0 0 22px rgba(0, 255, 102, 0.8), inset 0 0 12px rgba(0, 255, 102, 0.4); } }
    .price-flash-up { animation: flashGreen 0.75s ease-out; }
    .price-flash-down { animation: flashRed 0.75s ease-out; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .master-enter-section { background: radial-gradient(circle at top right, rgba(0, 255, 102, 0.14), rgba(8, 8, 10, 0.99) 65%); border: 1.5px solid rgba(0, 255, 102, 0.55); box-shadow: 0 10px 38px -8px rgba(0, 255, 102, 0.2); }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .green-gradient-btn:hover { background: linear-gradient(135deg, #33FF85 0%, #00E65C 100%); }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
    .bingx-btn:hover { background: linear-gradient(135deg, #3385FF 0%, #003399 100%); }
    .enter-now-badge { background: linear-gradient(135deg, #00FF66 0%, #FFFFFF 140%); color: #000000; animation: greenPulseGlow 1.8s infinite ease-in-out; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#00FF66]/30 selection:text-white">

  <!-- HEADER -->
  <header class="sticky top-0 z-30 bg-[#000000]/95 backdrop-blur-md border-b border-white/15 px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-xl bg-gradient-to-br from-[#00FF66] to-[#00993D] text-black flex items-center justify-center shadow-lg border border-white/40 font-bold">&#9889;</div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">ApexScan <span class="text-[#00FF66]">Render Env Sync</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span> <span id="modeText">MANUALE (RENDER SECURE)</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]">Connesso a BingX API v2</span>
          </div>
        </div>
      </div>

      <!-- INTERRUTTORE MANUALE / AUTOMATICO -->
      <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
        <span class="text-xs font-mono font-bold text-white/70 px-1">Esecuzione:</span>
        <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
        <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">automatico ⚡</button>
      </div>

      <!-- CONTROLLI AUDIO -->
      <div class="flex items-center gap-2">
        <button id="audioToggleBtn" class="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-white/10 text-white border border-white/25 hover:border-[#00FF66]/50">
          <span id="audioIcon">&#128266;</span><span id="audioToggleText">Audio ON</span>
        </button>
      </div>
    </div>
  </header>

  <!-- MAIN -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- KPI DINAMICI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Coppie Monitorate Live</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span id="kpiEnterNowCount" class="text-2xl font-extrabold font-mono text-white">--</span>
          <span class="text-xs font-mono text-[#00FF66] font-semibold">attive</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Chiavi API Render</span>
        <div class="mt-2">
          <span class="text-xs font-mono text-[#00FF66] font-bold">✔ CONFIGURATE IN SICUREZZA</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Protezione Ordini</span>
        <div class="mt-2">
          <span class="text-xs font-mono text-white">TP / SL con flag ReduceOnly</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Ultimo Sync Dati</span>
        <div class="mt-2">
          <span id="lastUpdateTimestamp" class="text-xs font-mono text-[#00FF66] font-bold">In attesa...</span>
        </div>
      </div>
    </section>

    <!-- TABELLA SCREENER LIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col">
        <div class="px-4 py-3 border-b border-white/15 flex items-center justify-between bg-[#08080A]">
          <h2 class="text-sm font-bold text-white">Screener Live Reale (Mercato BingX Futures)</h2>
          <span class="text-xs font-mono text-[#00FF66]">Aggiornamento ogni 3s</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-[#000000]">
                <th class="py-3 pl-3">Simbolo</th>
                <th class="py-3 px-2.5 text-right">Prezzo Live</th>
                <th class="py-3 px-2.5 text-right">Var. 24h</th>
                <th class="py-3 px-2.5">Stop Loss (ATR)</th>
                <th class="py-3 px-2.5">Take Profit 1</th>
                <th class="py-3 px-2.5 text-right">Azione / Esegui</th>
              </tr>
            </thead>
            <tbody id="scannerTableBody" class="divide-y divide-white/10 text-xs"></tbody>
          </table>
        </div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Log Attività Bot
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[760px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">Sistema avviato. Pronto a dialogare con BingX...</div>
        </div>
      </aside>
    </div>
  </main>

  <!-- MODALE DI CONFERMA ESECUZIONE -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[600px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 id="modalSymbol" class="text-base font-extrabold font-mono text-white">BTC-USDT</h2>
          <p class="text-xs text-white/70">Invio Ordine Reale con credenziali Render</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-2.5">
          <div class="flex justify-between"><span class="text-white/75">Prezzo Corrente Live:</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Segnale Operativo:</span><strong id="modalSide" class="text-[#00FF66]">BUY (LONG)</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Stop Loss Dinamico:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Take Profit Dinamico:</span><strong id="modalTP" class="text-[#00FF66]">--</strong></div>
        </div>
        <div>
          <label class="block text-[11px] text-white/70 mb-1 font-semibold">Taglia Contratto (es. 0.001):</label>
          <input type="number" id="tradeQuantityInput" value="0.001" step="0.001" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none" />
        </div>
        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>&#128640; Esegui Ordine Reale su BingX</span>
        </button>
        <div id="executionFeedback" class="mt-2 text-xs text-center hidden"></div>
      </div>
    </div>
  </div>

  <!-- JAVASCRIPT ENGINE -->
  <script>
    let state = {
      marketData: {},
      tradingMode: 'MANUAL',
      currentSelectedAsset: null,
      autoExecutedSymbols: new Set()
    };

    let sharedAudioCtx = null;
    function getAudioContext() {
      if (!sharedAudioCtx) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) sharedAudioCtx = new AudioCtx();
      }
      if (sharedAudioCtx && sharedAudioCtx.state === 'suspended') sharedAudioCtx.resume().catch(() => {});
      return sharedAudioCtx;
    }

    function playChime() {
      const ctx = getAudioContext();
      if (!ctx) return;
      const now = ctx.currentTime;
      [880, 1108.73, 1318.51].forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.frequency.setValueAtTime(freq, now + i * 0.08);
        gain.gain.setValueAtTime(0.001, now + i * 0.08);
        gain.gain.exponentialRampToValueAtTime(0.08, now + i * 0.08 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.08 + 0.4);
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start(now + i * 0.08);
        osc.stop(now + i * 0.08 + 0.45);
      });
    }

    async function fetchServerMode() {
      try {
        const res = await fetch('/api/get-mode');
        const data = await res.json();
        if (data.code === 0) updateModeUI(data.mode);
      } catch (e) {}
    }

    async function switchMode(mode) {
      try {
        const res = await fetch('/api/set-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode })
        });
        const data = await res.json();
        if (data.code === 0) updateModeUI(data.mode);
      } catch (e) {
        alert("Errore cambio modalità");
      }
    }

    function updateModeUI(mode) {
      state.tradingMode = mode;
      const btnMan = document.getElementById('btnModeManual');
      const btnAuto = document.getElementById('btnModeAuto');
      const badge = document.getElementById('modeStatusBadge');
      const modeDot = document.getElementById('modeDot');
      const modeText = document.getElementById('modeText');

      if (mode === 'AUTO') {
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-black shadow-lg";
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping";
        modeText.textContent = "ATTIVO: BOT AUTOMATICO ⚡";
      } else {
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white";
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-yellow-400";
        modeText.textContent = "MANUALE (RENDER SECURE)";
      }
    }

    async function pollMarketData() {
      try {
        const res = await fetch('/api/market-data');
        const json = await res.json();
        if (json.code === 0) {
          state.marketData = json.data;
          renderDashboard();
          document.getElementById('lastUpdateTimestamp').textContent = new Date().toLocaleTimeString();
        }
      } catch (e) {
        console.error("Errore fetch dati live:", e);
      }
    }

    function renderDashboard() {
      const tableBody = document.getElementById('scannerTableBody');
      const symbols = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT"];
      
      let rows = [];
      let activeCount = 0;

      symbols.forEach(sym => {
        const info = state.marketData[sym];
        if (!info || info.price <= 0) return;
        activeCount++;

        const price = info.price;
        const change = info.change24h;
        const isBull = change >= 0;
        
        const atr = price * 0.01;
        const sl = isBull ? price - (atr * 1.5) : price + (atr * 1.5);
        const tp = isBull ? price + (atr * 2.5) : price - (atr * 2.5);

        // Controllo Bot Automatico se attivato dall'interruttore
        if (state.tradingMode === 'AUTO' && Math.abs(change) > 1.8 && !state.autoExecutedSymbols.has(sym)) {
          state.autoExecutedSymbols.add(sym);
          eseguiOperazioneServer({
            symbol: sym,
            action: isBull ? 'BUY' : 'SELL',
            size: 0.001,
            tp: tp,
            sl: sl
          }, true);
        }

        rows.push(`
          <tr class="hover:bg-white/5 transition-colors cursor-pointer" onclick='openModal("${sym}", ${price}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}")'>
            <td class="py-3 pl-3 font-mono font-bold text-white">${sym}</td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums text-white font-bold">$${price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums ${change >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}">${change >= 0 ? '+' : ''}${change.toFixed(2)}%</td>
            <td class="py-3 px-2.5 font-mono text-[#FF334B]">$${sl.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td class="py-3 px-2.5 font-mono text-[#00FF66]">$${tp.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td class="py-3 px-2.5 text-right">
              <button class="px-3 py-1 rounded-lg bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50 font-bold text-xs hover:bg-[#00FF66] hover:text-black transition-all">
                Esegui ⚡
              </button>
            </td>
          </tr>
        `);
      });

      document.getElementById('kpiEnterNowCount').textContent = activeCount;
      if (rows.length > 0) {
        tableBody.innerHTML = rows.join('');
      } else {
        tableBody.innerHTML = `<tr><td colspan="6" class="text-center py-6 text-white/50">Caricamento prezzi live da BingX...</td></tr>`;
      }
    }

    function openModal(symbol, price, tp, sl, side) {
      state.currentSelectedAsset = { symbol, price, tp, sl, side };
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalLivePrice').textContent = `$${price.toLocaleString()}`;
      document.getElementById('modalSide').textContent = `${side} (${side === 'BUY' ? 'LONG' : 'SHORT'})`;
      document.getElementById('modalSide').className = side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]';
      document.getElementById('modalSL').textContent = `$${sl.toLocaleString()}`;
      document.getElementById('modalTP').textContent = `$${tp.toLocaleString()}`;
      
      const feedback = document.getElementById('executionFeedback');
      feedback.classList.add('hidden');
      feedback.textContent = '';

      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');
    }

    function closeModal() {
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
      state.currentSelectedAsset = null;
    }

    async function eseguiOrdineSuBingX() {
      if (!state.currentSelectedAsset) return;
      const asset = state.currentSelectedAsset;
      const quantity = parseFloat(document.getElementById('tradeQuantityInput').value) || 0.001;

      const btn = document.getElementById('executeBingXBtn');
      const feedback = document.getElementById('executionFeedback');

      btn.disabled = true;
      btn.textContent = "Invio ordine a BingX...";
      feedback.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedback.classList.add('text-white/70');
      feedback.textContent = "Firma HMAC in corso...";

      const res = await eseguiOperazioneServer({
        symbol: asset.symbol,
        action: asset.side,
        size: quantity,
        tp: asset.tp,
        sl: asset.sl
      }, false);

      if (res.code === 0) {
        feedback.classList.remove('text-white/70', 'text-[#FF334B]');
        feedback.classList.add('text-[#00FF66]');
        feedback.textContent = `✔ Successo! Posizione ${asset.side} aperta e protetta con TP/SL.`;
        playChime();
        logFeed(`[MANUALE OK] Posizione aperta su ${asset.symbol} (${asset.side})`);
      } else {
        feedback.classList.remove('text-white/70', 'text-[#00FF66]');
        feedback.classList.add('text-[#FF334B]');
        feedback.textContent = `✖ Errore: ${res.msg || 'Controlla i log su Render'}`;
        logFeed(`[ERRORE API] ${asset.symbol}: ${res.msg}`);
      }

      btn.disabled = false;
      btn.textContent = "Esegui Ordine Reale su BingX";
    }

    async function eseguiOperazioneServer(payload, isAuto) {
      try {
        const response = await fetch('/api/esegui-segnale', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...payload, is_auto: isAuto })
        });
        const data = await response.json();
        if (isAuto) {
          if (data.code === 0) {
            logFeed(`[AUTO ⚡ SUCCESS] Eseguito su ${payload.symbol} (${payload.action})`);
            playChime();
          } else {
            logFeed(`[AUTO ⚡ SKIP] ${payload.symbol}: ${data.msg}`);
          }
        }
        return data;
      } catch (err) {
        return { code: -1, msg: err.message };
      }
    }

    function logFeed(text) {
      const container = document.getElementById('alertsFeedContainer');
      const div = document.createElement('div');
      div.className = "p-2 rounded bg-white/5 border border-white/10 text-white/90 mt-1";
      div.textContent = `[${new Date().toLocaleTimeString()}] ${text}`;
      container.prepend(div);
    }

    window.addEventListener('DOMContentLoaded', () => {
      fetchServerMode();
      pollMarketData();
      setInterval(pollMarketData, 3000); // Aggiorna i prezzi live ogni 3 secondi
      setInterval(() => {
        const now = new Date();
        document.getElementById('liveCetClock').textContent = now.toTimeString().slice(0, 8) + ' CET';
      }, 1000);
    });
  </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
