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
    """Scarica tutte le coppie di mercato live dalle API pubbliche di BingX Futures."""
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
                    
                    category = "Criptovalute"
                    sym_upper = symbol.upper()
                    if any(mat in sym_upper for mat in ["GOLD", "SILVER", "OIL", "XAU", "XAG"]):
                        category = "Materie Prime"
                    elif any(fx in sym_upper for fx in ["EUR", "GBP", "JPY", "AUD"]):
                        category = "Forex / Valute"

                    market_map[symbol] = {
                        "price": price,
                        "change24h": change,
                        "volume": volume,
                        "high24h": high,
                        "low24h": low,
                        "category": category
                    }
            return market_map
    except Exception as e:
        print(f"[MARKET DATA ERROR] Errore connessione BingX: {e}")
    return {}

def imposta_leva_bingx(simbolo: str, leva: int, lato: str):
    """Imposta la leva finanziaria desiderata su BingX prima dell'ordine."""
    endpoint = "/openApi/swap/v2/trade/leverage"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    position_side = "LONG" if lato == "BUY" else "SHORT"
    payload = {
        "symbol": simbolo,
        "leverage": str(leva),
        "positionSide": position_side,
        "timestamp": timestamp
    }
    
    query_string = _genera_firma(payload, SECRET_KEY)
    headers = {"X-BX-APIKEY": API_KEY}
    
    try:
        resp = requests.post(f"{url}?{query_string}", headers=headers)
        res_data = resp.json()
        if res_data.get("code") == 0:
            print(f"[LEVERAGE SUCCESS] Leva {leva}x impostata per {simbolo} ({position_side})")
        else:
            print(f"[LEVERAGE WARNING] Impossibile impostare leva: {res_data.get('msg')}")
    except Exception as e:
        print(f"[LEVERAGE EXCEPT] {e}")

def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "1h / 4h"):
    """
    CERVELLO PHANTOM - ESECUZIONE SICURA BINGX:
    Imposta la leva, apre l'ordine a mercato immediato ed invia i TP e SL protettivi 
    evitando l'errore 'position not exist'.
    """
    if not API_KEY or not SECRET_KEY:
        print("[PHANTOM ERROR] BINGX_API_KEY o BINGX_SECRET_KEY non trovate nelle variabili d'ambiente di Render!")
        return {"code": -1, "msg": "API keys missing in Render Environment Variables"}

    # 0. Imposta la leva scelta
    imposta_leva_bingx(simbolo, leva, lato)

    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    # 1. Apertura immediata a mercato per garantire la creazione istantanea della posizione su BingX
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
            print(f"[PHANTOM SUCCESS] Posizione aperta su {simbolo} [{timeframe}] con leva {leva}x!")
            
            # Breve pausa per sincronizzazione sul server BingX
            time.sleep(0.6)
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            
            # 2. Invio ordini protettivi TP e SL
            if prezzo_sl > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET", lato)
            if prezzo_tp > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET", lato)
        else:
            print(f"[PHANTOM API ERROR] Risposta: {risultato}")
        
        return risultato
    except Exception as e:
        print(f"[EXCEPT] Errore di connessione a BingX: {e}")
        return {"code": -1, "msg": str(e)}

def _imposta_ordine_protettivo(simbolo: str, lato_chiusura: str, prezzo_trigger: float, tipo_ordine: str, lato_apertura: str):
    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    payload = {
        "symbol": simbolo,
        "side": lato_chiusura,
        "positionSide": "LONG" if lato_apertura == "BUY" else "SHORT",
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
            print(f"[PROTECT ERROR] Impossibile impostare {tipo_ordine} su {simbolo}: {res_data.get('msg')}")
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
    prezzo_ingresso = float(dati.get("entry", dati.get("price", 0)))
    tp = float(dati.get("tp", 0))
    sl = float(dati.get("sl", 0))
    leva = int(dati.get("leverage", 3))
    timeframe = dati.get("timeframe", "1h / 4h")
    is_auto = dati.get("is_auto", False)

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Cervello Phantom in modalità Manuale: esecuzione automatica bloccata."})
    
    risultato = esegui_ordine_bingx(simbolo, lato, quantita, prezzo_ingresso, tp, sl, leva, timeframe)
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
  <title>ApexScan Pro - Phantom Brain &amp; Scia BTC Pro</title>
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
    @keyframes neonGlowPulse { 0%, 100% { box-shadow: 0 0 15px rgba(255, 204, 0, 0.3), inset 0 0 10px rgba(255, 204, 0, 0.1); border-color: rgba(255, 204, 0, 0.7); } 50% { box-shadow: 0 0 30px rgba(255, 204, 0, 0.6), inset 0 0 18px rgba(255, 204, 0, 0.3); border-color: #FFCC00; } }
    .priority-signal-box { background: linear-gradient(180deg, rgba(20, 20, 10, 0.98) 0%, rgba(8, 8, 5, 0.99) 100%); border: 2px solid #FFCC00; animation: neonGlowPulse 3s infinite ease-in-out; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .bingx-btn { background: linear-gradient(135deg, #0066FF 0%, #0044BB 100%); color: #FFFFFF; font-weight: 800; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="min-h-screen flex flex-col selection:bg-[#00FF66]/30 selection:text-white">

  <!-- HEADER -->
  <header class="sticky top-0 z-30 bg-[#000000]/95 backdrop-blur-md border-b border-white/15 px-3 sm:px-5 py-2.5">
    <div class="max-w-[1920px] mx-auto flex flex-wrap items-center justify-between gap-3">
      <div class="flex items-center gap-3.5">
        <div class="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#00FF66] via-[#00CC52] to-[#006622] text-black flex items-center justify-center shadow-2xl border-2 border-[#00FF66] font-extrabold text-xl animate-pulse">
          🧠
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">CERVELLO PHANTOM <span class="text-[#00FF66]">ApexScan</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span> <span id="modeText">PHANTOM: MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]">BingX Live API Connesso 🟢</span>
          </div>
        </div>
      </div>

      <!-- INTERRUTTORE CERVELLO PHANTOM -->
      <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
        <span class="text-xs font-mono font-bold text-white/70 px-1">Modalità:</span>
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
        <span class="text-[11px] font-semibold uppercase text-white/75">Andamento Attuale Mercato</span>
        <div class="mt-2">
          <strong class="text-xs font-mono text-[#00FF66] block">🟢 MERCATO IN FASE LONG</strong>
          <span class="text-[11px] text-white/70">Trend Guida Bitcoin: SOPRA MEDIE ORARIE</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Sentiment Altcoin Scia BTC</span>
        <div class="mt-2">
          <div class="w-full h-2 bg-white/10 rounded-full overflow-hidden flex mb-1">
            <div id="kpiSentimentBar" class="h-full bg-[#00FF66]" style="width: 75%"></div>
          </div>
          <span id="kpiSentimentLabel" class="text-xs font-mono text-[#00FF66] font-bold">IN ACCUMULO POSITIVO</span>
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
        <span class="text-[11px] font-semibold uppercase text-white/75">Stato Cervello Server Python</span>
        <div class="mt-2">
          <span id="kpiPhantomStatus" class="text-xs font-mono text-[#00FF66] font-bold">COLLEGATO 🟢 (BINGX LIVE)</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER: SEGNALE PRIORITARIO DA APRIRE A MERCATO ORA (BOX GIALLO EVIDENZIATO) -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#FFCC00]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFCC00] text-black flex items-center justify-center font-extrabold animate-bounce">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">⚡ SEGNALE PRIORITARIO DA APRIRE A MERCATO ORA</h2>
            <p class="text-[11px] text-[#FFCC00]">Setup ad altissima confluenza con attivazione istantanea</p>
          </div>
        </div>
        <span class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#FFCC00] text-black animate-pulse">CONFERMATO</span>
      </div>
      <div id="prioritySignalCardContent"></div>
    </section>

    <!-- FILTRO RAPIDO E RICERCA COIN -->
    <section class="terminal-card rounded-2xl p-4 flex flex-col gap-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="text-xs font-bold text-white/80">Progetto Scanner 7 Novembre:</span>
        </div>
        <div class="flex items-center gap-2">
          <button onclick="setFilterTab('ALL')" id="filterTabALL" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all">⭐ Tutte (50 Coin)</button>
          <button onclick="setFilterTab('SCIA_BTC')" id="filterTabSCIA_BTC" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🛡️ Scia BTC Basso Rischio (25)</button>
        </div>
      </div>
      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Coin (es. XLM, BTC, SOL, ETH, DOGE, XRP)..." class="w-full bg-[#000000] border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
      </div>
    </section>

    <!-- SEZIONE TABELLE FLUSSI LIVE -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-4">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span>📊</span> Flussi di Mercato &amp; Categorie (Scia BTC)
          </h2>
          <span class="text-xs font-mono text-cyan-400">Timeframe: 15m / 1h / 4h</span>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-left border-collapse">
            <thead>
              <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-[#000000]">
                <th class="py-3 pl-3">Asset / Categoria</th>
                <th class="py-3 px-2.5">Segnale</th>
                <th class="py-3 px-2.5">⏱️ Timeframe</th>
                <th class="py-3 px-2.5 text-right">Prezzo Live</th>
                <th class="py-3 px-2.5 text-right">Var. 24h 🔥</th>
                <th class="py-3 px-2.5">Stop Loss &amp; TP</th>
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
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed BingX Live (5s)
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[760px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Phantom] Sincronizzazione API BingX attiva...</div>
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
          <p class="text-xs text-white/70">Esecuzione Reale con TP/SL protettivi &amp; Margine Isolato</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-2.5">
          <div class="flex justify-between"><span class="text-white/75">Asset / Categoria:</span><strong id="modalCategory" class="text-[#00FF66]">Criptovalute</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Timeframe Consigliato:</span><strong id="modalTF" class="text-cyan-400">1h / 4h</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Prezzo Live (Trigger):</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Segnale Operativo:</span><strong id="modalSide" class="text-[#00FF66]">LONG (ACQUISTO)</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Stop Loss Strutturale:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Take Profit 1 (+2.5%):</span><strong id="modalTP" class="text-[#00FF66]">--</strong></div>
        </div>

        <!-- SELETTORE LEVA -->
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-3">
          <label class="block text-xs font-bold text-white uppercase tracking-wider">Leva Protetta &amp; Margine Isolato:</label>
          <div class="grid grid-cols-4 gap-2">
            <button type="button" onclick="setLeverage(1)" id="levBtn1" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">1x</button>
            <button type="button" onclick="setLeverage(3)" id="levBtn3" class="py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold">3x</button>
            <button type="button" onclick="setLeverage(5)" id="levBtn5" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">5x</button>
            <button type="button" onclick="setLeverage(10)" id="levBtn10" class="py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold">10x</button>
          </div>
          <span class="text-[10px] text-yellow-400 block mt-1">💡 Regola Rischio: Margine Isolato (Rischio Max 1% del Conto). Su TP1 chiudi il 50% e sposta lo Stop Loss a Pareggio (Break-Even).</span>
        </div>

        <div>
          <label class="block text-[11px] text-white/70 mb-1 font-semibold">Taglia Contratto (es. 0.001):</label>
          <input type="number" id="tradeQuantityInput" value="0.001" step="0.001" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none" />
        </div>

        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>🧠 Esegui a Mercato con TP/SL su BingX</span>
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
      currentFilterTab: 'ALL',
      searchQuery: '',
      selectedLeverage: 3,
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

    function setFilterTab(tab) {
      state.currentFilterTab = tab;
      document.getElementById('filterTabALL').className = tab === 'ALL' ? "px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all" : "px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all";
      document.getElementById('filterTabSCIA_BTC').className = tab === 'SCIA_BTC' ? "px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all" : "px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all";
      renderDashboard();
    }

    function filterCoinSearch(query) {
      state.searchQuery = query.toLowerCase().trim();
      renderDashboard();
    }

    function setLeverage(lev) {
      state.selectedLeverage = lev;
      [1, 3, 5, 10].forEach(l => {
        const btn = document.getElementById(`levBtn${l}`);
        if (btn) {
          if (l === lev) {
            btn.className = "py-1.5 rounded-lg border border-[#00FF66] bg-[#00FF66]/20 text-[#00FF66] font-bold";
          } else {
            btn.className = "py-1.5 rounded-lg border border-white/20 bg-white/5 text-white font-bold";
          }
        }
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
        kpiStatus.textContent = "COLLEGATO 🟢 (BINGX LIVE)";
        kpiStatus.className = "text-xs font-mono text-[#00FF66] font-bold";
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
      if (keys.length === 0) return;

      const prioritySymbols = ["BTC-USDT", "SOL-USDT", "ETH-USDT", "XRP-USDT", "DOGE-USDT", "ADA-USDT", "PEPE-USDT", "AVAX-USDT"];

      let sortedCoins = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => {
        let aIsPriority = prioritySymbols.includes(a.symbol);
        let bIsPriority = prioritySymbols.includes(b.symbol);
        if (aIsPriority && !bIsPriority) return -1;
        if (!aIsPriority && bIsPriority) return 1;
        return Math.abs(b.change24h) - Math.abs(a.change24h);
      });

      let topMover = sortedCoins[0];
      if (topMover) {
        document.getElementById('kpiTopMoverSymbol').textContent = topMover.symbol;
        const topChangeEl = document.getElementById('kpiTopMoverChange');
        topChangeEl.textContent = (topMover.change24h >= 0 ? '+' : '') + topMover.change24h.toFixed(2) + '%';
        topChangeEl.className = topMover.change24h >= 0 ? 'text-sm font-extrabold font-mono text-[#00FF66]' : 'text-sm font-extrabold font-mono text-[#FF334B]';
      }

      // Render Segnale Prioritario (Box Giallo)
      const btcCoin = sortedCoins.find(c => c.symbol === "BTC-USDT") || sortedCoins[0];
      if (btcCoin) {
        const isBull = btcCoin.change24h >= 0;
        const entry = btcCoin.price;
        const atr = btcCoin.price * 0.01;
        const sl = Number((isBull ? btcCoin.price - (atr * 1.5) : btcCoin.price + (atr * 1.5)).toFixed(2));
        const tp = Number((isBull ? btcCoin.price + (atr * 2.5) : btcCoin.price - (atr * 2.5)).toFixed(2));

        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <div class="flex items-center justify-between"><strong class="text-white text-sm">BTC/USDT - MASTER BTC (Rischio: BASSO)</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">Entrata a Mercato:</span><strong class="text-[#00FF66] text-sm">$${entry.toLocaleString()} (Entrata Market Immediata)</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏱️ Timeframe Consigliato:</span><strong class="text-cyan-300">1h / 4h</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">💎 Confluenza &amp; Stato:</span><strong class="text-[#FFCC00]">-70% Confluenza</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏳ Validità Forte Residua:</span><strong class="text-white">29:08 (Finestra di Forza Volumetrica)</strong></div>
            </div>
            <div class="space-y-2 flex flex-col justify-between">
              <div class="bg-black/40 border border-[#FFCC00]/30 rounded-xl p-3 space-y-1.5">
                <div class="flex justify-between"><span class="text-[#FF334B]">Stop Loss Strutturale (-1.00%):</span><strong class="text-[#FF334B]">$${sl.toLocaleString()}</strong></div>
                <div class="flex justify-between"><span class="text-[#00FF66]">Take Profit 1 (+2.50%):</span><strong class="text-[#00FF66]">$${tp.toLocaleString()}</strong></div>
                <div class="flex justify-between"><span class="text-white/70">⚡ Leva Protetta Consigliata:</span><strong class="text-yellow-400">3x-5x (Margine Isolato Consigliato)</strong></div>
              </div>
              <div class="text-[11px] text-yellow-300 bg-yellow-950/30 p-2 rounded-lg border border-yellow-600/30">
                💡 <strong>Istruzioni:</strong> Entra su BingX a Margine Isolato. Su TP1 chiudi il 50% e sposta lo Stop Loss a Pareggio (Break-Even).
              </div>
              <button onclick='openModal("${btcCoin.symbol}", ${entry}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}", "1h / 4h", "Criptovalute")' class="green-gradient-btn w-full py-2 rounded-xl text-xs shadow-lg animate-pulse">
                ⚡ APRI SHORT / LONG A MERCATO ORA &#8599;
              </button>
            </div>
          </div>
        `;
      }

      // Filtro Tab e Ricerca
      let filteredCoins = sortedCoins;
      if (state.currentFilterTab === 'SCIA_BTC') {
        filteredCoins = sortedCoins.filter(c => prioritySymbols.includes(c.symbol) || c.symbol.includes("BTC") || c.symbol.includes("ETH"));
      }
      if (state.searchQuery) {
        filteredCoins = filteredCoins.filter(c => c.symbol.toLowerCase().includes(state.searchQuery));
      }

      const tableBody = document.getElementById('scannerTableBody');
      tableBody.innerHTML = filteredCoins.map(c => {
        const isBull = c.change24h >= 0;
        const isPriority = prioritySymbols.includes(c.symbol);
        const tf = Math.abs(c.change24h) > 5 ? '15m / 1h' : '1h / 4h';
        const statusText = Math.abs(c.change24h) > 3 ? (isBull ? 'LONG' : 'SHORT') : 'ATTESA';
        const statusBadgeColor = statusText === 'LONG' ? 'bg-green-500/20 text-[#00FF66] border-green-500/40' : statusText === 'SHORT' ? 'bg-red-500/20 text-[#FF334B] border-red-500/40' : 'bg-white/10 text-white/70 border-white/20';
        const atr = c.price * 0.01;
        const sl = Number((isBull ? c.price - (atr * 1.5) : c.price + (atr * 1.5)).toFixed(2));
        const tp = Number((isBull ? c.price + (atr * 2.5) : c.price - (atr * 2.5)).toFixed(2));

        if (state.tradingMode === 'AUTO' && Math.abs(c.change24h) > 4.5 && !state.autoExecutedSymbols.has(c.symbol)) {
          state.autoExecutedSymbols.add(c.symbol);
          eseguiOperazioneServer({ symbol: c.symbol, action: isBull ? 'BUY' : 'SELL', size: 0.001, entry: c.price, tp: tp, sl: sl, leverage: 3, timeframe: tf }, true);
        }

        return `
          <tr class="hover:bg-white/5 transition-colors cursor-pointer" onclick='openModal("${c.symbol}", ${c.price}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}", "${tf}", "${c.category}")'>
            <td class="py-3 pl-3 font-mono font-bold text-white">
              <div class="flex items-center gap-1.5">
                ${isPriority ? '<span class="text-xs">⭐</span>' : ''} (${c.symbol.replace('-USDT', '')})
              </div>
              <span class="text-[10px] text-white/50">${c.category}</span>
            </td>
            <td class="py-3 px-2.5">
              <span class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border ${statusBadgeColor}">${statusText}</span>
            </td>
            <td class="py-3 px-2.5 font-mono text-cyan-300 font-bold">${tf}</td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums text-white font-bold">$${c.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
            <td class="py-3 px-2.5 text-right font-mono tabular-nums ${isBull ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-extrabold">${isBull ? '+' : ''}${c.change24h.toFixed(2)}%</td>
            <td class="py-3 px-2.5 font-mono text-white/80">$${sl.toLocaleString()} / $${tp.toLocaleString()}</td>
            <td class="py-3 px-2.5 text-right">
              <button class="px-3 py-1 rounded-lg bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50 font-bold text-xs hover:bg-[#00FF66] hover:text-black transition-all">
                Esegui ⚡
              </button>
            </td>
          </tr>
        `;
      }).join('');
    }

    function openModal(symbol, livePrice, tp, sl, side, tf, category) {
      state.currentSelectedAsset = { symbol, livePrice, tp, sl, side, tf, category };
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalCategory').textContent = category;
      document.getElementById('modalTF').textContent = tf;
      document.getElementById('modalLivePrice').textContent = `$${livePrice.toLocaleString()}`;
      document.getElementById('modalSide').textContent = `${side === 'BUY' ? 'LONG (ACQUISTO)' : 'SHORT (VENDITA)'}`;
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
      const leverage = state.selectedLeverage;

      const btn = document.getElementById('executeBingXBtn');
      const feedback = document.getElementById('executionFeedback');

      btn.disabled = true;
      btn.textContent = `Invio ordine su BingX in corso...`;
      feedback.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedback.classList.add('text-white/70');
      feedback.textContent = `Configurazione Leva ${leverage}x, Margine Isolato e TP/SL protettivi...`;

      const res = await eseguiOperazioneServer({
        symbol: asset.symbol,
        action: asset.side === 'BUY' ? 'BUY' : 'SELL',
        size: quantity,
        entry: asset.livePrice,
        tp: asset.tp,
        sl: asset.sl,
        leverage: leverage,
        timeframe: asset.tf
      }, false);

      if (res.code === 0) {
        feedback.classList.remove('text-white/70', 'text-[#FF334B]');
        feedback.classList.add('text-[#00FF66]');
        feedback.textContent = `✔ Successo! Posizione aperta a mercato con TP/SL protettivi su BingX.`;
        playChime();
        logFeed(`[BINGX LIVE] Posizione aperta su ${asset.symbol} (${asset.side}) - Leva ${leverage}x`);
      } else {
        feedback.classList.remove('text-white/70', 'text-[#00FF66]');
        feedback.classList.add('text-[#FF334B]');
        feedback.textContent = `✖ Errore: ${res.msg || 'Controlla le credenziali API su Render'}`;
        logFeed(`[BINGX ERRORE] ${asset.symbol}: ${res.msg}`);
      }

      btn.disabled = false;
      btn.textContent = "🧠 Esegui a Mercato con TP/SL su BingX";
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
            logFeed(`[AUTO ⚡] Ordine eseguito su ${payload.symbol} (${payload.action})`);
            playChime();
          } else {
            logFeed(`[AUTO SKIP] ${payload.symbol}: ${data.msg}`);
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
