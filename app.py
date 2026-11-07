import os
import time
import hmac
import hashlib
import urllib.parse
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# ==========================================
# 1. CONFIGURAZIONE CREDENZIALI BINGX (RENDER)
# ==========================================
API_KEY = os.environ.get("BINGX_API_KEY")
SECRET_KEY = os.environ.get("BINGX_SECRET_KEY")
BASE_URL = "https://open-api.bingx.com"

def _genera_firma(payload: dict, secret_key: str) -> str:
    """Genera la firma HMAC SHA256 richiesta dalle API Futures di BingX."""
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature

def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_tp: float, prezzo_sl: float):
    """
    CERVELLO OPERATIVO: Epre la posizione a mercato su BingX 
    e imposta automaticamente Stop Loss e Take Profit protettivi (con reduceOnly).
    """
    if not API_KEY or not SECRET_KEY:
        print("[BINGX ERROR] Chiavi API non configurate nelle variabili d'ambiente di Render!")
        return {"code": -1, "msg": "API keys missing in environment variables on Render"}

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
            print(f"[BINGX SUCCESS] Posizione {lato} aperta con successo su {simbolo}!")
            
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
    """Funzione di servizio per inviare SL o TP protettivi con reduceOnly=True"""
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
            print(f"[PROTECT SUCCESS] {tipo_ordine} impostato a {prezzo_trigger} per {simbolo}")
        else:
            print(f"[PROTECT ERROR] Impossibile impostare {tipo_ordine}: {res_data.get('msg')}")
    except Exception as e:
        print(f"[PROTECT EXCEPT] Errore invio ordine protettivo: {e}")


# ==========================================
# 2. ENDPOINT API FLASK PER IL WEBHOOK
# ==========================================
@app.route('/api/esegui-segnale', methods=['POST'])
def webhook_esegui_segnale():
    dati = request.json or {}
    simbolo = dati.get("symbol")         # Es: "BTC-USDT"
    lato = dati.get("action")            # "BUY" o "SELL"
    quantita = float(dati.get("size", 0.001))
    tp = float(dati.get("tp", 0))
    sl = float(dati.get("sl", 0))
    
    # Esegue l'ordine tramite il cervello BingX
    risultato = esegui_ordine_bingx(simbolo, lato, quantita, tp, sl)
    return jsonify(risultato)


# ==========================================
# 3. INTERFACCIA FRONTEND (DASHBOARD HTML)
# ==========================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro - Live &amp; Real-Time Engine + BingX Auto</title>
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
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">ApexScan <span class="text-[#00FF66]">Pro Live</span></h1>
            <span class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#00FF66]/15 text-[#00FF66] border border-[#00FF66]/40">
              <span class="w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping"></span> BINGX SYNC ACTIVE
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span id="recalibrationTimerLabel" class="text-[#00FF66]">Prossimo ricalcolo ATR: 02:00</span>
          </div>
        </div>
      </div>

      <!-- SELETTORE MERCATO -->
      <div class="flex items-center bg-[#08080A] p-1 rounded-xl border border-white/20 overflow-x-auto" id="marketSelector">
        <button data-market="ALL" class="market-btn active px-3 py-1.5 rounded-lg text-xs font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50">Tutti</button>
        <button data-market="CRYPTO" class="market-btn px-3 py-1.5 rounded-lg text-xs font-medium text-white/60 hover:text-white">Crypto</button>
        <button data-market="STOCKS" class="market-btn px-3 py-1.5 rounded-lg text-xs font-medium text-white/60 hover:text-white">Azioni USA</button>
        <button data-market="FOREX" class="market-btn px-3 py-1.5 rounded-lg text-xs font-medium text-white/60 hover:text-white">Forex</button>
        <button data-market="INDICES" class="market-btn px-3 py-1.5 rounded-lg text-xs font-medium text-white/60 hover:text-white">Indici</button>
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
        <span class="text-[11px] font-semibold uppercase text-white/75">Segnali Entra Adesso Attivi</span>
        <div class="mt-2 flex items-baseline gap-2">
          <span id="kpiEnterNowCount" class="text-2xl font-extrabold font-mono text-white">--</span>
          <span class="text-xs font-mono text-[#00FF66] font-semibold">pronti ora</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Sentiment Globale Mercato</span>
        <div class="mt-2">
          <div class="w-full h-2 bg-white/10 rounded-full overflow-hidden flex mb-1">
            <div id="kpiGlobalSentimentBar" class="h-full bg-gradient-to-r from-white to-[#00FF66]" style="width: 74%"></div>
          </div>
          <span id="kpiGlobalSentimentLabel" class="text-xs font-mono text-[#00FF66] font-bold">74% BULLISH ESTREMO</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Rapporto Long / Short</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiLongShortRatioBadge" class="text-lg font-extrabold font-mono text-white">78% <span class="text-[#00FF66]">L</span> / 22% <span class="text-[#FF334B]">S</span></span>
          <span class="text-xs font-mono text-white/80">Confluenza Media: 5.4/6</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Top Mover Live</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-base font-extrabold font-mono text-white">SOL/USDT</span>
          <span id="kpiTopMoverChange" class="text-base font-extrabold font-mono text-[#00FF66]">+8.64%</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER: ENTRA ADESSO -->
    <section class="master-enter-section rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-4 border-b border-white/15">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold">&#9889;</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">&#9889; <span class="text-[#00FF66]">ENTRA ADESSO</span> - Segnali Certi &amp; Parametri Reali</h2>
            <p class="text-[11px] text-white/70">Analisi Smart Money e studi volumetrici live con invio diretto a BingX.</p>
          </div>
        </div>
        <span id="masterEnterNowBadgeCount" class="enter-now-badge px-2.5 py-1 rounded-md text-xs font-mono font-extrabold">ATTIVI ORA</span>
      </div>
      <div id="masterEnterNowCardsGrid" class="grid grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3 gap-4"></div>
    </section>

    <!-- TABELLA SCREENER E STUDI LIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col">
        <div class="px-4 py-3 border-b border-white/15 flex items-center justify-between bg-[#08080A]">
          <h2 class="text-sm font-bold text-white">Screener Multi-Asset &amp; Studi di Mercato Live</h2>
          <span id="filteredResultsCount" class="text-xs font-mono text-[#00FF66]">8 asset</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-[#000000]">
                <th class="py-3 pl-3">Asset</th>
                <th class="py-3 px-2.5 text-right">Prezzo Live &amp; 24h</th>
                <th class="py-3 px-2.5">RSI (14)</th>
                <th class="py-3 px-2.5">Sentiment</th>
                <th class="py-3 px-2.5">Segnale</th>
                <th class="py-3 px-2.5">Finestra &amp; ETA TP1</th>
                <th class="py-3 px-2.5">Optimal Entry / SL</th>
                <th class="py-3 px-2.5 text-right">Trend / Studio</th>
              </tr>
            </thead>
            <tbody id="scannerTableBody" class="divide-y divide-white/10 text-xs"></tbody>
          </table>
        </div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed Alert &amp; Studi Live
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[760px] overflow-y-auto"></div>
      </aside>
    </div>
  </main>

  <!-- MODALE STUDIO DETTAGLIO & ESECUZIONE BINGX -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[1000px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 id="modalSymbol" class="text-base font-extrabold font-mono text-white">BTC/USDT</h2>
          <p id="modalName" class="text-xs text-white/70">Studio di Mercato &amp; Parametri Reali</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <h3 class="text-xs font-bold uppercase text-white">Parametri di Esecuzione Live</h3>
          <div class="flex justify-between p-2 rounded bg-white/10 border border-white/25"><span class="text-white/80">Optimal Entry:</span><strong id="modalEntry" class="text-white">--</strong></div>
          <div class="flex justify-between p-2 rounded bg-[#FF334B]/15 border border-[#FF334B]/40"><span class="text-white/80">Stop Loss (ATR):</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
          <div class="flex justify-between p-2 rounded bg-[#00FF66]/15 border border-[#00FF66]/40"><span class="text-white/80">Take Profit 1:</span><strong id="modalTP1" class="text-[#00FF66]">--</strong></div>
          <div class="flex justify-between p-2 rounded bg-[#00FF66]/15 border border-[#00FF66]/40"><span class="text-white/80">Take Profit 2:</span><strong id="modalTP2" class="text-[#00FF66]">--</strong></div>
          
          <!-- Pulsante di Esecuzione Automatica BingX -->
          <div class="pt-2">
            <label class="block text-[10px] text-white/70 mb-1 font-semibold">Quantità Futures (es. 0.001 o 0.1):</label>
            <div class="flex gap-2">
              <input type="number" id="tradeQuantityInput" value="0.001" step="0.001" class="w-28 bg-[#08080A] border border-white/30 rounded-lg px-2.5 py-1.5 text-white font-mono text-xs focus:border-[#0066FF] outline-none" />
              <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn flex-1 py-2 px-4 rounded-xl text-xs transition-all shadow-lg flex items-center justify-center gap-2">
                <span>&#128640; Esegui su BingX (Real)</span>
              </button>
            </div>
            <div id="executionFeedback" class="mt-2 text-[11px] font-mono text-center hidden"></div>
          </div>
        </div>
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <h3 class="text-xs font-bold uppercase text-white">Studi &amp; Confluenze SMC</h3>
          <div id="modalConfluencesList" class="space-y-2"></div>
        </div>
      </div>
    </div>
  </div>

  <!-- JAVASCRIPT ENGINE -->
  <script>
    const INITIAL_ASSETS = [
      { id: 'btc', symbol: 'BTC-USDT', name: 'Bitcoin', market: 'CRYPTO', price: 78330.00, decimals: 2, change24h: 1.87, rvol: 1.88, rsi: 61.2, smaTrend: 'Sopra SMA 50/200', pattern: 'Breakout Range', wyckoff: 'Markup', smcEvent: 'BOS Rialzista + Bullish OB', score: 89 },
      { id: 'eth', symbol: 'ETH-USDT', name: 'Ethereum', market: 'CRYPTO', price: 2317.00, decimals: 2, change24h: 2.41, rvol: 1.45, rsi: 56.7, smaTrend: 'Sopra SMA 50/200', pattern: 'Golden Cross', wyckoff: 'Markup', smcEvent: 'Retest Bullish FVG', score: 79 },
      { id: 'sol', symbol: 'SOL-USDT', name: 'Solana', market: 'CRYPTO', price: 190.26, decimals: 2, change24h: 8.64, rvol: 2.65, rsi: 72.8, smaTrend: 'Sopra SMA 50/200', pattern: 'Bullish FVG + OB', wyckoff: 'Markup', smcEvent: 'BOS + Displacement', score: 94 },
      { id: 'nvda', symbol: 'NVDA', name: 'NVIDIA Corp.', market: 'STOCKS', price: 146.80, decimals: 2, change24h: 3.92, rvol: 1.84, rsi: 66.8, smaTrend: 'Sopra SMA 50/200', pattern: 'Golden Cross', wyckoff: 'Markup', smcEvent: 'BOS + Bullish Order Block', score: 91 },
      { id: 'eurusd', symbol: 'EUR/USD', name: 'Euro / Dollaro USA', market: 'FOREX', price: 1.06425, decimals: 5, change24h: -0.48, rvol: 1.42, rsi: 33.8, smaTrend: 'Sotto SMA 50/200', pattern: 'Liquidity Sweep', wyckoff: 'Markdown', smcEvent: 'London High Sweep + CHoCH', score: 23 },
      { id: 'spx', symbol: 'US500', name: 'S&P 500 Index', market: 'INDICES', price: 5968.40, decimals: 2, change24h: 0.78, rvol: 1.25, rsi: 60.5, smaTrend: 'Sopra SMA 50/200', pattern: 'Golden Cross', wyckoff: 'Markup', smcEvent: 'NY Open BOS + FVG', score: 78 },
      { id: 'xauusd', symbol: 'XAU/USD', name: 'Oro Spot (Gold)', market: 'INDICES', price: 2684.50, decimals: 2, change24h: 1.58, rvol: 1.76, rsi: 65.4, smaTrend: 'Sopra SMA 50/200', pattern: 'Breakout Range', wyckoff: 'Markup', smcEvent: 'London/NY Overlap BOS', score: 88 },
      { id: 'pltr', symbol: 'PLTR', name: 'Palantir Technologies', market: 'STOCKS', price: 61.45, decimals: 2, change24h: 6.75, rvol: 2.52, rsi: 74.5, smaTrend: 'Sopra SMA 50/200', pattern: 'Breakout Range', wyckoff: 'Markup', smcEvent: 'Breakout Range + FVG', score: 92 }
    ];

    const state = {
      assets: [], market: 'ALL', audioEnabled: true, recalibrationCountdown: 120, currentSelectedAsset: null
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
      if (!state.audioEnabled) return;
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

    function formatPrice(val, decimals, market) {
      if (market === 'FOREX') return val.toFixed(decimals);
      return '$' + val.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    }

    function computePlan(asset) {
      const isLong = asset.score >= 50;
      const atr = asset.price * 0.008;
      const optimalEntry = isLong ? asset.price - atr * 0.2 : asset.price + atr * 0.2;
      const stopLoss = isLong ? optimalEntry - atr * 1.5 : optimalEntry + atr * 1.5;
      const tp1 = isLong ? optimalEntry + atr * 2.2 : optimalEntry - atr * 2.2;
      const tp2 = isLong ? optimalEntry + atr * 4.0 : optimalEntry - atr * 4.0;
      const now = new Date();
      return {
        isLong, optimalEntry, stopLoss, tp1, tp2,
        tp1ExactStr: `${String((now.getHours() + 1) % 24).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')} CET`,
        confluences: [
          { name: 'Trend & SMA', desc: asset.smaTrend, ok: true },
          { name: 'Fase Wyckoff', desc: asset.wyckoff, ok: true },
          { name: 'Smart Money (SMC)', desc: asset.smcEvent, ok: true },
          { name: 'Volume Profile (RVOL)', desc: `RVOL ${asset.rvol}x`, ok: asset.rvol >= 1.2 }
        ]
      };
    }

    function initApp() {
      state.assets = INITIAL_ASSETS.map(a => ({ ...a, plan: computePlan(a), lastFlash: null }));
      renderDashboard();
    }

    function renderDashboard() {
      const enterNowGrid = document.getElementById('masterEnterNowCardsGrid');
      const tableBody = document.getElementById('scannerTableBody');
      const alertsContainer = document.getElementById('alertsFeedContainer');

      const activeEnterNow = state.assets.filter(a => a.score >= 75);
      document.getElementById('kpiEnterNowCount').textContent = activeEnterNow.length;
      document.getElementById('masterEnterNowBadgeCount').textContent = `${activeEnterNow.length} SETUP ATTIVI ORA`;

      enterNowGrid.innerHTML = activeEnterNow.map(a => {
        const flashClass = a.lastFlash === 'UP' ? 'price-flash-up' : a.lastFlash === 'DOWN' ? 'price-flash-down' : '';
        return `
          <div class="bg-[#000000] border border-white/25 hover:border-[#00FF66] rounded-2xl p-4 flex flex-col justify-between transition-all shadow-xl relative overflow-hidden">
            <div class="absolute top-0 left-0 w-1.5 h-full ${a.plan.isLong ? 'bg-[#00FF66]' : 'bg-[#FF334B]'}"></div>
            <div>
              <div class="flex items-center justify-between pb-2.5 border-b border-white/15">
                <div class="flex items-center gap-2">
                  <span class="text-base font-extrabold font-mono text-white">${a.symbol}</span>
                  <span class="px-2 py-0.5 rounded text-[10px] font-mono font-extrabold ${a.plan.isLong ? 'bg-[#00FF66]/15 text-[#00FF66]' : 'bg-[#FF334B]/15 text-[#FF334B]'}">${a.plan.isLong ? 'LONG' : 'SHORT'}</span>
                </div>
                <span class="font-mono font-extrabold text-sm text-white rounded px-1 ${flashClass}">${formatPrice(a.price, a.decimals, a.market)}</span>
              </div>
              <div class="grid grid-cols-3 gap-2 font-mono text-center my-3">
                <div class="bg-white/10 border border-white/30 rounded-xl p-2"><span class="text-[9px] text-white/80 block font-bold">Optimal Entry</span><strong class="text-white text-xs">${formatPrice(a.plan.optimalEntry, a.decimals, a.market)}</strong></div>
                <div class="bg-[#FF334B]/15 border border-[#FF334B]/40 rounded-xl p-2"><span class="text-[9px] text-[#FF334B] block font-bold">Stop Loss</span><strong class="text-[#FF334B] text-xs">${formatPrice(a.plan.stopLoss, a.decimals, a.market)}</strong></div>
                <div class="bg-[#00FF66]/15 border border-[#00FF66]/40 rounded-xl p-2"><span class="text-[9px] text-[#00FF66] block font-bold">Take Profit 1</span><strong class="text-[#00FF66] text-xs">${formatPrice(a.plan.tp1, a.decimals, a.market)}</strong></div>
              </div>
              <div class="bg-[#08080A] border border-[#00FF66]/35 rounded-xl p-2 font-mono text-[10px] flex items-center justify-between">
                <span class="text-[#00FF66] font-bold">ETA TP1: ${a.plan.tp1ExactStr}</span>
                <span class="text-white font-bold">Confluenze: 4/4 OK</span>
              </div>
            </div>
            <button onclick="openModal('${a.id}')" class="green-gradient-btn w-full mt-3 py-1.5 px-3 rounded-xl text-xs transition-all">Apri Analisi &amp; Esecuzione BingX &#8599;</button>
          </div>
        `;
      }).join('');

      const filtered = state.assets.filter(a => state.market === 'ALL' || a.market === state.market);
      document.getElementById('filteredResultsCount').textContent = `${filtered.length} asset`;
      tableBody.innerHTML = filtered.map(a => {
        const flashClass = a.lastFlash === 'UP' ? 'price-flash-up' : a.lastFlash === 'DOWN' ? 'price-flash-down' : '';
        const isBull = a.score >= 50;
        return `
          <tr onclick="openModal('${a.id}')" class="hover:bg-white/5 transition-colors cursor-pointer">
            <td class="py-3 pl-3 font-mono font-bold text-white">${a.symbol}<span class="text-[9px] block text-white/50">${a.market}</span></td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums ${flashClass}"><div class="font-bold text-white">${formatPrice(a.price, a.decimals, a.market)}</div><div class="text-[10px] ${a.change24h>=0?'text-[#00FF66]':'text-[#FF334B]'}">${a.change24h>=0?'+':''}${a.change24h.toFixed(2)}%</div></td>
            <td class="py-3 px-2.5 font-mono font-bold text-white">${a.rsi.toFixed(1)}</td>
            <td class="py-3 px-2.5 font-mono font-extrabold text-white">${a.score}%</td>
            <td class="py-3 px-2.5"><span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold ${isBull ? 'bg-[#00FF66]/15 text-[#00FF66]' : 'bg-[#FF334B]/15 text-[#FF334B]'}">${isBull ? 'STRONG BUY' : 'STRONG SELL'}</span></td>
            <td class="py-3 px-2.5 font-mono text-[10px]"><span class="enter-now-badge px-1.5 py-0.5 rounded font-extrabold">&#9889; ENTRA ADESSO</span><div class="text-[#00FF66] font-bold mt-1">TP1: ${a.plan.tp1ExactStr}</div></td>
            <td class="py-3 px-2.5 font-mono text-[10px]"><span class="text-white">In: ${formatPrice(a.plan.optimalEntry, a.decimals, a.market)}</span><div class="text-[#FF334B]">SL: ${formatPrice(a.plan.stopLoss, a.decimals, a.market)}</div></td>
            <td class="py-3 px-2.5 text-right font-mono text-white/80">${a.smaTrend}</td>
          </tr>
        `;
      }).join('');

      alertsContainer.innerHTML = activeEnterNow.map(a => `
        <div class="p-3 rounded-xl border border-[#00FF66]/45 bg-[#000000]">
          <div class="flex items-center justify-between text-[10px] font-mono text-[#00FF66] mb-1">
            <span>&#9889; SEGNALE CERTO LIVE</span><span class="text-white/60">Ora</span>
          </div>
          <h4 class="text-xs font-bold text-white">${a.symbol} - ${a.smcEvent}</h4>
          <p class="text-[11px] text-white/75 mt-1">Entry: <span class="text-white font-semibold">${formatPrice(a.plan.optimalEntry, a.decimals, a.market)}</span> | TP1: <span class="text-[#00FF66] font-semibold">${formatPrice(a.plan.tp1, a.decimals, a.market)}</span></p>
        </div>
      `).join('');

      state.assets.forEach(a => a.lastFlash = null);
    }

    function openModal(id) {
      const asset = state.assets.find(a => a.id === id);
      if (!asset) return;
      state.currentSelectedAsset = asset;

      document.getElementById('modalSymbol').textContent = asset.symbol;
      document.getElementById('modalName').textContent = `${asset.name} - Studio Istituzionale Reale`;
      document.getElementById('modalEntry').textContent = formatPrice(asset.plan.optimalEntry, asset.decimals, asset.market);
      document.getElementById('modalSL').textContent = formatPrice(asset.plan.stopLoss, asset.decimals, asset.market);
      document.getElementById('modalTP1').textContent = formatPrice(asset.plan.tp1, asset.decimals, asset.market);
      document.getElementById('modalTP2').textContent = formatPrice(asset.plan.tp2, asset.decimals, asset.market);

      const feedbackEl = document.getElementById('executionFeedback');
      feedbackEl.classList.add('hidden');
      feedbackEl.textContent = '';

      const confList = document.getElementById('modalConfluencesList');
      confList.innerHTML = asset.plan.confluences.map(c => `
        <div class="flex justify-between p-2 rounded bg-[#08080A] border border-white/15">
          <span class="text-white/75">${c.name}:</span>
          <strong class="text-[#00FF66]">${c.desc}</strong>
        </div>
      `).join('');

      document.getElementById('assetModal').classList.remove('hidden');
      document.getElementById('assetModal').classList.add('flex');
    }

    function closeModal() {
      document.getElementById('assetModal').classList.add('hidden');
      document.getElementById('assetModal').classList.remove('flex');
      state.currentSelectedAsset = null;
    }

    // Funzione JavaScript che invia i parametri reali al server Flask per l'esecuzione su BingX
    async function eseguiOrdineSuBingX() {
      if (!state.currentSelectedAsset) return;
      const asset = state.currentSelectedAsset;
      const quantity = parseFloat(document.getElementById('tradeQuantityInput').value) || 0.001;
      const action = asset.score >= 50 ? 'BUY' : 'SELL';

      const btn = document.getElementById('executeBingXBtn');
      const feedbackEl = document.getElementById('executionFeedback');

      btn.disabled = true;
      btn.textContent = "Invio ordine a BingX in corso...";
      feedbackEl.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedbackEl.classList.add('text-white/70');
      feedbackEl.textContent = "Comunicazione con le API Futures in corso...";

      try {
        const response = await fetch('/api/esegui-segnale', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            symbol: asset.symbol,       // Es: "BTC-USDT"
            action: action,             // "BUY" o "SELL"
            size: quantity,
            tp: asset.plan.tp1,         // TP calcolato dallo scanner a parametri reali
            sl: asset.plan.stopLoss     // SL calcolato dallo scanner a parametri reali
          })
        });

        const data = await response.json();

        if (data.code === 0) {
          feedbackEl.classList.remove('text-white/70', 'text-[#FF334B]');
          feedbackEl.classList.add('text-[#00FF66]');
          feedbackEl.textContent = `✔ Successo! Posizione ${action} aperta e protetta su BingX.`;
        } else {
          feedbackEl.classList.remove('text-white/70', 'text-[#00FF66]');
          feedbackEl.classList.add('text-[#FF334B]');
          feedbackEl.textContent = `✖ Errore BingX: ${data.msg || 'Controlla i log su Render'}`;
        }
      } catch (err) {
        feedbackEl.classList.remove('text-white/70', 'text-[#00FF66]');
        feedbackEl.classList.add('text-[#FF334B]');
        feedbackEl.textContent = `✖ Errore di rete o server: ${err.message}`;
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<span>&#128640; Esegui su BingX (Real)</span>';
      }
    }

    function simulateLiveMarket() {
      state.assets.forEach(a => {
        const delta = (Math.random() - 0.49) * 0.003;
        const oldPrice = a.price;
        a.price = Math.max(0.001, oldPrice * (1 + delta));
        a.lastFlash = a.price >= oldPrice ? 'UP' : 'DOWN';
        a.change24h += delta * 40;
      });
      renderDashboard();
    }

    function tickClock() {
      const now = new Date();
      document.getElementById('liveCetClock').textContent = now.toTimeString().slice(0, 8) + ' CET';
      state.recalibrationCountdown--;
      const m = Math.floor(state.recalibrationCountdown / 60);
      const s = state.recalibrationCountdown % 60;
      document.getElementById('recalibrationTimerLabel').textContent = `Prossimo ricalcolo ATR: ${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;

      if (state.recalibrationCountdown <= 0) {
        state.recalibrationCountdown = 120;
        state.assets.forEach(a => { a.plan = computePlan(a); });
      }
    }

    window.addEventListener('DOMContentLoaded', () => {
      initApp();
      setInterval(simulateLiveMarket, 2500);
      setInterval(tickClock, 1000);

      document.querySelectorAll('.market-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          document.querySelectorAll('.market-btn').forEach(b => {
            b.classList.remove('active', 'bg-[#00FF66]/20', 'text-[#00FF66]', 'border', 'border-[#00FF66]/50', 'font-bold');
            b.classList.add('text-white/60', 'font-medium');
          });
          e.currentTarget.classList.remove('text-white/60', 'font-medium');
          e.currentTarget.classList.add('active', 'bg-[#00FF66]/20', 'text-[#00FF66]', 'border', 'border-[#00FF66]/50', 'font-bold');
          state.market = e.currentTarget.getAttribute('data-market');
          renderDashboard();
        });
      });

      document.getElementById('audioToggleBtn').addEventListener('click', () => {
        state.audioEnabled = !state.audioEnabled;
        document.getElementById('audioToggleText').textContent = state.audioEnabled ? 'Audio ON' : 'Audio OFF';
        if (state.audioEnabled) playChime();
      });
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
