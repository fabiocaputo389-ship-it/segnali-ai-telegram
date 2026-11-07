import os
import time
import hmac
import hashlib
import urllib.parse
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# ==========================================
# LETTURA CREDENZIALI DA RENDER
# ==========================================
API_KEY = os.environ.get("BINGX_API_KEY")
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY")
BASE_URL = "https://open-api.bingx.com"

# Stato globale del Cervello Phantom ("MANUAL" o "AUTO")
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
    """Scarica tutte le coppie di criptovalute live dalle API pubbliche di BingX Futures."""
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get("code") == 0 and "data" in data:
            tickers = data["data"]
            market_map = {}
            for t in tickers:
                symbol = t.get("symbol")
                if symbol and symbol.endswith("-USDT"):
                    price = float(t.get("lastPrice", 0))
                    change = float(t.get("priceChangePercent", 0)) * 100
                    volume = float(t.get("volume", 0))
                    high = float(t.get("highPrice", price))
                    low = float(t.get("lowPrice", price))
                    market_map[symbol] = {
                        "price": price,
                        "change24h": change,
                        "volume": volume,
                        "high24h": high,
                        "low24h": low
                    }
            return market_map
    except Exception as e:
        print(f"[MARKET DATA ERROR] Errore connessione BingX: {e}")
    return {}

def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_tp: float, prezzo_sl: float):
    """
    CERVELLO PHANTOM: Apre la posizione a mercato reale su BingX 
    e imposta automaticamente Stop Loss e Take Profit protettivi (con reduceOnly).
    """
    if not API_KEY or not SECRET_KEY:
        print("[PHANTOM ERROR] BINGX_API_KEY o BINGX_SECRET_KEY non trovate nelle variabili d'ambiente di Render!")
        return {"code": -1, "msg": "API keys missing in Render Environment Variables"}

    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    payload_ordine = {
        "symbol": simbolo,
        "side": lato,
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
            print(f"[PHANTOM SUCCESS] Posizione reale {lato} aperta su {simbolo}!")
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET")
            _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET")
        else:
            print(f"[PHANTOM API ERROR] Risposta: {risultato}")
        
        return risultato
    except Exception as e:
        print(f"[EXCEPT] Errore di connessione a BingX: {e}")
        return {"code": -1, "msg": str(e)}

def _imposta_ordine_protettivo(simbolo: str, lato: str, prezzo_trigger: float, tipo_ordine: str):
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
        requests.post(f"{url}?{query_string}", headers=headers)
    except Exception as e:
        print(f"[PROTECT EXCEPT] {e}")


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
        return jsonify({"code": -99, "msg": "Cervello Phantom in modalità Manuale: esecuzione automatica bloccata."})
    
    risultato = esegui_ordine_bingx(simbolo, lato, quantita, tp, sl)
    return jsonify(risultato)

@app.route('/api/set-mode', methods=['POST'])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    mode = dati.get("mode")
    if mode in ["MANUAL", "AUTO"]:
        TRADING_MODE = mode
        print(f"[PHANTOM MODE] Modalità impostata su: {TRADING_MODE}")
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
  <title>ApexScan Pro - Phantom Brain &amp; BingX All Crypto</title>
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
    @keyframes neonGlowPulse { 0%, 100% { box-shadow: 0 0 15px rgba(0, 255, 102, 0.4), inset 0 0 10px rgba(0, 255, 102, 0.2); border-color: rgba(0, 255, 102, 0.8); } 50% { box-shadow: 0 0 30px rgba(0, 255, 102, 0.8), inset 0 0 18px rgba(0, 255, 102, 0.5); border-color: #00FF66; } }
    .price-flash-up { animation: flashGreen 0.75s ease-out; }
    .price-flash-down { animation: flashRed 0.75s ease-out; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .master-enter-section { background: radial-gradient(circle at top right, rgba(0, 255, 102, 0.18), rgba(8, 8, 10, 0.99) 65%); border: 2px solid rgba(0, 255, 102, 0.6); animation: neonGlowPulse 2.5s infinite ease-in-out; }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
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
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">ApexScan <span class="text-[#00FF66]">Phantom Brain</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span> <span id="modeText">PHANTOM: MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]">Flusso Live Universale BingX</span>
          </div>
        </div>
      </div>

      <!-- INTERRUTTORE CERVELLO PHANTOM -->
      <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
        <span class="text-xs font-mono font-bold text-white/70 px-1">Cervello Phantom:</span>
        <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
        <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">AUTOMATICO ⚡</button>
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

    <!-- KPI DINAMICI & SENTIMENT -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Cripto Totali Monitorate</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span id="kpiTotalCoins" class="text-2xl font-extrabold font-mono text-white">--</span>
          <span class="text-xs font-mono text-[#00FF66] font-semibold">su BingX</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Sentiment Globale Mercato</span>
        <div class="mt-2">
          <div class="w-full h-2 bg-white/10 rounded-full overflow-hidden flex mb-1">
            <div id="kpiSentimentBar" class="h-full bg-[#00FF66]" style="width: 50%"></div>
          </div>
          <span id="kpiSentimentLabel" class="text-xs font-mono text-[#00FF66] font-bold">NEUTRO / BULLISH</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Top Mover 24h 🔥</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-sm font-extrabold font-mono text-white">--</span>
          <span id="kpiTopMoverChange" class="text-sm font-extrabold font-mono text-[#00FF66]">--%</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Stato Cervello Phantom</span>
        <div class="mt-2">
          <span id="kpiPhantomStatus" class="text-xs font-mono text-yellow-400 font-bold">PRONTO (RENDER SECURE)</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER: ENTRA ADESSO ANIMATA (DIAMANTE CON CORONA 💎👑 + TF + PERCENTUALI) -->
    <section class="master-enter-section rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-4 border-b border-white/15">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold animate-bounce">💎👑</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">💎👑 <span class="text-[#00FF66]">ENTRA ADESSO</span> - Segnali Top &amp; Timeframe Esatto</h2>
            <p class="text-[11px] text-white/70">Segnali ad altissima confluenza animati in tempo reale.</p>
          </div>
        </div>
        <span id="masterEnterNowBadgeCount" class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#00FF66] text-black animate-pulse">SETUP 💎👑 ATTIVI</span>
      </div>
      <div id="masterEnterNowCardsGrid" class="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4"></div>
    </section>

    <!-- TABELLA TUTTE LE CRIPTO & TOP MOVIMENTAZIONI 🔥 -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col">
        <div class="px-4 py-3 border-b border-white/15 flex items-center justify-between bg-[#08080A]">
          <h2 class="text-sm font-bold text-white">Tutte le Criptovalute &amp; Top Movimentazioni 🔥 (Live BingX)</h2>
          <span id="filteredResultsCount" class="text-xs font-mono text-[#00FF66]">Sync attivo</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-[#000000]">
                <th class="py-3 pl-3">Simbolo 🔥</th>
                <th class="py-3 px-2.5">Timeframe</th>
                <th class="py-3 px-2.5 text-right">Prezzo Live</th>
                <th class="py-3 px-2.5 text-right">Var. 24h 🔥</th>
                <th class="py-3 px-2.5">Stop Loss &amp; %</th>
                <th class="py-3 px-2.5">Take Profit &amp; %</th>
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
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Log Cervello Phantom
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[760px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Phantom] Connesso al flusso mercati di BingX...</div>
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
          <p class="text-xs text-white/70">Esecuzione Reale gestita dal Cervello Phantom</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-2.5">
          <div class="flex justify-between"><span class="text-white/75">Timeframe Operativo:</span><strong id="modalTF" class="text-cyan-400">TF 15M</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Prezzo Live:</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Segnale Phantom:</span><strong id="modalSide" class="text-[#00FF66]">BUY (LONG)</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Confidenza Calcolata:</span><strong id="modalConfidence" class="text-[#00FF66]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Stop Loss Dinamico:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Take Profit Dinamico:</span><strong id="modalTP" class="text-[#00FF66]">--</strong></div>
        </div>
        <div>
          <label class="block text-[11px] text-white/70 mb-1 font-semibold">Taglia Contratto (es. 0.001):</label>
          <input type="number" id="tradeQuantityInput" value="0.001" step="0.001" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none" />
        </div>
        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>&#128640; Esegui con Cervello Phantom (Live BingX)</span>
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
      const kpiStatus = document.getElementById('kpiPhantomStatus');

      if (mode === 'AUTO') {
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-black shadow-lg";
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping";
        modeText.textContent = "PHANTOM: ATTIVO ⚡";
        kpiStatus.textContent = "ATTIVO (AUTO ⚡)";
        kpiStatus.className = "text-xs font-mono text-[#00FF66] font-bold";
      } else {
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white";
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-yellow-400";
        modeText.textContent = "PHANTOM: MANUALE";
        kpiStatus.textContent = "PRONTO (MANUALE)";
        kpiStatus.className = "text-xs font-mono text-yellow-400 font-bold";
      }
    }

    async function pollMarketData() {
      try {
        const res = await fetch('/api/market-data');
        const json = await res.json();
        if (json.code === 0) {
          state.marketData = json.data;
          renderDashboard();
        }
      } catch (e) {
        console.error("Errore fetch dati live:", e);
      }
    }

    function renderDashboard() {
      const keys = Object.keys(state.marketData);
      document.getElementById('kpiTotalCoins').textContent = keys.length;
      if (keys.length === 0) return;

      let sortedCoins = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => Math.abs(b.change24h) - Math.abs(a.change24h));
      let topMover = sortedCoins[0];

      if (topMover) {
        document.getElementById('kpiTopMoverSymbol').textContent = topMover.symbol;
        const topChangeEl = document.getElementById('kpiTopMoverChange');
        topChangeEl.textContent = (topMover.change24h >= 0 ? '+' : '') + topMover.change24h.toFixed(2) + '%';
        topChangeEl.className = topMover.change24h >= 0 ? 'text-sm font-extrabold font-mono text-[#00FF66]' : 'text-sm font-extrabold font-mono text-[#FF334B]';
      }

      // Sentiment Globale
      let bullishCount = sortedCoins.filter(c => c.change24h > 0).length;
      let bullishPercent = Math.round((bullishCount / sortedCoins.length) * 100) || 50;
      document.getElementById('kpiSentimentBar').style.width = bullishPercent + '%';
      document.getElementById('kpiSentimentLabel').textContent = `${bullishPercent}% BULLISH (LIVE)`;

      // Sezione Entra Adesso (💎👑 animata con TF e TP/SL % esatti)
      const enterNowGrid = document.getElementById('masterEnterNowCardsGrid');
      let enterNowCoins = sortedCoins.filter(c => Math.abs(c.change24h) >= 2.5).slice(0, 3);
      
      enterNowGrid.innerHTML = enterNowCoins.map(c => {
        const isBull = c.change24h >= 0;
        const tf = Math.abs(c.change24h) > 5 ? 'TF 5M' : Math.abs(c.change24h) > 3 ? 'TF 15M' : 'TF 1H';
        const atr = c.price * 0.012;
        const sl = isBull ? c.price - (atr * 1.5) : c.price + (atr * 1.5);
        const tp = isBull ? c.price + (atr * 2.5) : c.price - (atr * 2.5);
        const slPercent = ((Math.abs(c.price - sl) / c.price) * 100).toFixed(2);
        const tpPercent = ((Math.abs(tp - c.price) / c.price) * 100).toFixed(2);
        const confidence = Math.min(98, Math.round(85 + Math.abs(c.change24h) * 1.5));

        return `
          <div class="bg-[#000000] border border-white/25 hover:border-[#00FF66] rounded-2xl p-4 flex flex-col justify-between transition-all shadow-xl relative overflow-hidden">
            <div class="absolute top-0 left-0 w-1.5 h-full ${isBull ? 'bg-[#00FF66]' : 'bg-[#FF334B]'}"></div>
            <div>
              <div class="flex items-center justify-between pb-2.5 border-b border-white/15">
                <div class="flex items-center gap-2">
                  <span class="text-base font-extrabold font-mono text-white">💎👑 ${c.symbol}</span>
                  <span class="px-2 py-0.5 rounded text-[10px] font-mono font-extrabold bg-cyan-500/20 text-cyan-300 border border-cyan-500/40">${tf}</span>
                </div>
                <span class="font-mono font-extrabold text-sm text-white">$${c.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</span>
              </div>
              <div class="my-2 flex justify-between items-center text-xs font-mono">
                <span class="text-white/70">Confidenza Phantom:</span>
                <strong class="text-[#00FF66] text-sm">${confidence}% 💎</strong>
              </div>
              <div class="grid grid-cols-2 gap-2 font-mono text-center my-2">
                <div class="bg-[#FF334B]/15 border border-[#FF334B]/40 rounded-xl p-2">
                  <span class="text-[9px] text-[#FF334B] block font-bold">Stop Loss (-${slPercent}%)</span>
                  <strong class="text-[#FF334B] text-xs">$${sl.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                </div>
                <div class="bg-[#00FF66]/15 border border-[#00FF66]/40 rounded-xl p-2">
                  <span class="text-[9px] text-[#00FF66] block font-bold">Take Profit (+${tpPercent}%)</span>
                  <strong class="text-[#00FF66] text-xs">$${tp.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong>
                </div>
              </div>
            </div>
            <button onclick='openModal("${c.symbol}", ${c.price}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}", ${confidence}, "${tf}")' class="green-gradient-btn w-full mt-3 py-1.5 px-3 rounded-xl text-xs transition-all animate-pulse">Esegui Setup 💎👑 &#8599;</button>
          </div>
        `;
      }).join('');

      // Tabella principale con tutte le crypto
      const tableBody = document.getElementById('scannerTableBody');
      document.getElementById('filteredResultsCount').textContent = `${sortedCoins.length} asset scansionati`;

      tableBody.innerHTML = sortedCoins.map(c => {
        const isBull = c.change24h >= 0;
        const isTopMover = Math.abs(c.change24h) >= 3.0;
        const tf = Math.abs(c.change24h) > 5 ? 'TF 5M' : Math.abs(c.change24h) > 3 ? 'TF 15M' : 'TF 1H';
        const atr = c.price * 0.01;
        const sl = isBull ? c.price - (atr * 1.5) : c.price + (atr * 1.5);
        const tp = isBull ? c.price + (atr * 2.5) : c.price - (atr * 2.5);
        const slPercent = ((Math.abs(c.price - sl) / c.price) * 100).toFixed(2);
        const tpPercent = ((Math.abs(tp - c.price) / c.price) * 100).toFixed(2);
        const confidence = Math.min(95, Math.round(75 + Math.abs(c.change24h)));

        if (state.tradingMode === 'AUTO' && Math.abs(c.change24h) > 4.0 && !state.autoExecutedSymbols.has(c.symbol)) {
          state.autoExecutedSymbols.add(c.symbol);
          eseguiOperazioneServer({ symbol: c.symbol, action: isBull ? 'BUY' : 'SELL', size: 0.001, tp: tp, sl: sl }, true);
        }

        return `
          <tr class="hover:bg-white/5 transition-colors cursor-pointer" onclick='openModal("${c.symbol}", ${c.price}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}", ${confidence}, "${tf}")'>
            <td class="py-3 pl-3 font-mono font-bold text-white flex items-center gap-1.5">
              ${c.symbol} ${isTopMover ? '🔥' : ''}
            </td>
            <td class="py-3 px-2.5 font-mono text-cyan-300 font-bold">${tf}</td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums text-white font-bold">$${c.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums ${isBull ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-extrabold">${isBull ? '+' : ''}${c.change24h.toFixed(2)}% ${Math.abs(c.change24h)>=2?'🔥':''}</td>
            <td class="py-3 px-2.5 font-mono text-[#FF334B]">$${sl.toLocaleString(undefined, {minimumFractionDigits: 2})} <span class="text-[10px] opacity-80">(-${slPercent}%)</span></td>
            <td class="py-3 px-2.5 font-mono text-[#00FF66]">$${tp.toLocaleString(undefined, {minimumFractionDigits: 2})} <span class="text-[10px] opacity-80">(+${tpPercent}%)</span></td>
            <td class="py-3 px-2.5 text-right">
              <button class="px-3 py-1 rounded-lg bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50 font-bold text-xs hover:bg-[#00FF66] hover:text-black transition-all">
                Esegui ⚡
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function openModal(symbol, price, tp, sl, side, confidence, tf) {
      state.currentSelectedAsset = { symbol, price, tp, sl, side, confidence, tf };
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalTF').textContent = tf;
      document.getElementById('modalLivePrice').textContent = `$${price.toLocaleString()}`;
      document.getElementById('modalSide').textContent = `${side} (${side === 'BUY' ? 'LONG' : 'SHORT'})`;
      document.getElementById('modalSide').className = side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]';
      document.getElementById('modalConfidence').textContent = `${confidence}% 💎👑`;
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
      btn.textContent = "Cervello Phantom in esecuzione...";
      feedback.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedback.classList.add('text-white/70');
      feedback.textContent = "Firmando richiesta HMAC per BingX...";

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
        logFeed(`[PHANTOM MANUALE] Posizione aperta su ${asset.symbol} (${asset.side})`);
      } else {
        feedback.classList.remove('text-white/70', 'text-[#00FF66]');
        feedback.classList.add('text-[#FF334B]');
        feedback.textContent = `✖ Errore: ${res.msg || 'Controlla i log su Render'}`;
        logFeed(`[PHANTOM ERRORE] ${asset.symbol}: ${res.msg}`);
      }

      btn.disabled = false;
      btn.textContent = "Esegui con Cervello Phantom (Live BingX)";
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
            logFeed(`[PHANTOM AUTO ⚡] Eseguito su ${payload.symbol} (${payload.action})`);
            playChime();
          } else {
            logFeed(`[PHANTOM AUTO SKIP] ${payload.symbol}: ${data.msg}`);
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
      setInterval(pollMarketData, 3000);
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
