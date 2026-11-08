import os
import time
import hmac
import hashlib
import urllib.parse
import requests
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# ==========================================
# CREDENZIALI DI DEFAULT (Fallback / Sistema)
# ==========================================
DEFAULT_API_KEY = os.environ.get("BINGX_API_KEY", "")
DEFAULT_SECRET_KEY = os.environ.get("BINGX_SECRET_KEY", "")
BASE_URL = "https://open-api.bingx.com"

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
    """Scarica tutte le coppie di mercato live da BingX Futures in tempo reale."""
    url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"
    try:
        response = requests.get(url, timeout=4)
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
                    if any(mat in sym_upper for mat in ["GOLD", "SILVER", "OIL", "XAU", "XAG", "WTI", "BRENT", "COPPER"]):
                        category = "Materie Prime"
                    elif any(fx in sym_upper for fx in ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]):
                        category = "Forex / Valute"
                    elif any(st in sym_upper for st in ["NASDAQ", "SP500", "AAPL", "TSLA", "NVDA", "AMZN", "MSFT", "GOLDEN", "US30"]):
                        category = "Azioni & Indici (TradFi)"

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

def imposta_leva_bingx(simbolo: str, leva: int, lato: str, api_key: str, secret_key: str):
    """Imposta la leva finanziaria desiderata sull'account BingX dell'utente."""
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
    
    query_string = _genera_firma(payload, secret_key)
    headers = {"X-BX-APIKEY": api_key}
    
    try:
        resp = requests.post(f"{url}?{query_string}", headers=headers)
        res_data = resp.json()
        if res_data.get("code") == 0:
            print(f"[LEVERAGE SUCCESS] Leva {leva}x impostata per {simbolo} ({position_side})")
    except Exception as e:
        print(f"[LEVERAGE EXCEPT] {e}")

def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "1h / 4h", api_key: str = "", secret_key: str = ""):
    """Esegue l'ordine a mercato in tempo reale e imposta TP e SL di precisione."""
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti. Inserisci le tue chiavi personali nel pannello in alto a destra."}

    imposta_leva_bingx(simbolo, leva, lato, active_api, active_secret)

    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    position_side = "LONG" if lato == "BUY" else "SHORT"
    
    payload_ordine = {
        "symbol": simbolo,
        "side": lato,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": str(quantita),
        "timestamp": timestamp
    }
    
    query_string = _genera_firma(payload_ordine, active_secret)
    headers = {"X-BX-APIKEY": active_api}
    
    try:
        response = requests.post(f"{url}?{query_string}", headers=headers)
        risultato = response.json()
        
        if risultato.get("code") == 0:
            print(f"[SUCCESS] Ordine di precisione eseguito su {simbolo} [{timeframe}]")
            time.sleep(0.8)
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            
            if prezzo_sl > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET", position_side, active_api, active_secret)
            if prezzo_tp > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET", position_side, active_api, active_secret)
        
        return risultato
    except Exception as e:
        return {"code": -1, "msg": str(e)}

def _imposta_ordine_protettivo(simbolo: str, lato_chiusura: str, prezzo_trigger: float, tipo_ordine: str, position_side: str, api_key: str, secret_key: str):
    endpoint = "/openApi/swap/v2/trade/order"
    url = BASE_URL + endpoint
    timestamp = str(int(time.time() * 1000))
    
    payload = {
        "symbol": simbolo,
        "side": lato_chiusura,
        "positionSide": position_side,
        "type": tipo_ordine,
        "stopPrice": str(prezzo_trigger),
        "workingType": "MARK_PRICE",
        "reduceOnly": "true",
        "timestamp": timestamp
    }
    
    query_string = _genera_firma(payload, secret_key)
    headers = {"X-BX-APIKEY": api_key}
    
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
    prezzo_ingresso = float(dati.get("entry", dati.get("price", 0)))
    tp = float(dati.get("tp", 0))
    sl = float(dati.get("sl", 0))
    leva = int(dati.get("leverage", 3))
    timeframe = dati.get("timeframe", "1h / 4h")
    is_auto = dati.get("is_auto", False)
    
    user_api_key = dati.get("apiKey", "")
    user_secret_key = dati.get("secretKey", "")

    if is_auto and TRADING_MODE != "AUTO":
        return jsonify({"code": -99, "msg": "Modalità Automatica disattivata dal sistema."})
    
    risultato = esegui_ordine_bingx(simbolo, lato, quantita, prezzo_ingresso, tp, sl, leva, timeframe, user_api_key, user_secret_key)
    return jsonify(risultato)

@app.route('/api/set-mode', methods=['POST'])
def set_trading_mode():
    global TRADING_MODE
    dati = request.json or {}
    mode = dati.get("mode")
    if mode in ["MANUAL", "AUTO"]:
        TRADING_MODE = mode
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
  <title>ApexScan Pro - Precision Signals &amp; Real-Time Engine</title>
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
          🎯
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">APEXSCAN <span class="text-[#00FF66]">Precision Signals Engine</span></h1>
            <span id="modeStatusBadge" class="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20">
              <span id="modeDot" class="w-1.5 h-1.5 rounded-full bg-yellow-400"></span> <span id="modeText">PHANTOM: MANUALE</span>
            </span>
          </div>
          <div class="flex items-center gap-2 text-[11px] font-mono text-white/70 mt-0.5">
            <span id="liveCetClock" class="text-white font-semibold tabular-nums">--:--:-- CET</span>
            <span>|</span>
            <span class="text-[#00FF66]">Motore Chirurgico ATR Attivo 🟢</span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-3">
        <!-- BOTTONE PANNELLO API UTENTE -->
        <button onclick="toggleApiModal()" class="px-3.5 py-1.5 rounded-xl text-xs font-mono font-bold bg-cyan-500/20 text-cyan-300 border border-cyan-500/50 hover:bg-cyan-500/30 transition-all flex items-center gap-1.5">
          <span>🔑</span> <span id="apiStatusText">Collega API Personali</span>
        </button>

        <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
          <span class="text-xs font-mono font-bold text-white/70 px-1">Modalità:</span>
          <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
          <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">AUTOMATICO ⚡</button>
        </div>
      </div>
    </div>
  </header>

  <!-- MAIN -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- KPI DINAMICI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Stato Connessione Utente</span>
        <div class="mt-2">
          <strong id="kpiUserApiStatus" class="text-xs font-mono text-yellow-400 block">⚠️ API Personali non inserite</strong>
          <span class="text-[11px] text-white/70">Inserisci chiavi per auto-trading</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Asset Monitorati Live</span>
        <div class="mt-2">
          <span id="kpiTotalAssets" class="text-base font-mono text-cyan-400 font-extrabold">-- Asset Live</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Top Mover in Tempo Reale 🔥</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-sm font-extrabold font-mono text-white">--</span>
          <span id="kpiTopMoverChange" class="text-sm font-extrabold font-mono text-[#00FF66]">--%</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Precisione Segnali</span>
        <div class="mt-2">
          <span class="text-xs font-mono text-[#00FF66] font-bold">ATR Volatilità + Durata TP 🟢</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER: SEGNALE DI PRECISIONE CHIRURGICA -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#FFCC00]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFCC00] text-black flex items-center justify-center font-extrabold animate-bounce">🎯</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">🎯 SEGNALE DI PRECISIONE CHIRURGICA (MASTER TOP COIN)</h2>
            <p class="text-[11px] text-[#FFCC00]">Ingresso ottimizzato, gestione rischio ATR e durata stimata al TP</p>
          </div>
        </div>
        <span class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#FFCC00] text-black animate-pulse">PRECISIONE 94%</span>
      </div>
      <div id="prioritySignalCardContent"></div>
    </section>

    <!-- FILTRO CATEGORIE E RICERCA LIVE -->
    <section class="terminal-card rounded-2xl p-4 flex flex-col gap-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <span class="text-xs font-bold text-white/80">Filtra per Categoria di Mercato:</span>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button onclick="setCategoryTab('ALL')" id="catTabALL" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all">⭐ Tutti i Mercati</button>
          <button onclick="setCategoryTab('Criptovalute')" id="catTabCriptovalute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🪙 Criptovalute</button>
          <button onclick="setCategoryTab('Materie Prime')" id="catTabMaterie Prime" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🛢️ Materie Prime</button>
          <button onclick="setCategoryTab('Forex / Valute')" id="catTabForex / Valute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">💱 Forex &amp; Valute</button>
          <button onclick="setCategoryTab('Azioni & Indici (TradFi)')" id="catTabTradFi" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">📈 Azioni &amp; Indici</button>
        </div>
      </div>
      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Asset (es. BTC, SOL, GOLD, EUR, AAPL)..." class="w-full bg-[#000000] border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
      </div>
    </section>

    <!-- TABELLE LIVE CON TEMPI E LIVELLI ATR -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-6">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span>📊</span> Tabella Flussi di Mercato in Tempo Reale
          </h2>
          <span class="text-xs font-mono text-cyan-400 flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Live Feed (3s)
          </span>
        </div>

        <div id="tablesContainer" class="space-y-6"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed Segnali (3s)
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Precision Engine] Sincronizzazione indicatori ATR completata...</div>
        </div>
      </aside>
    </div>
  </main>

  <!-- MODALE CONFIGURAZIONE API PERSONALI UTENTE -->
  <div id="apiModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-cyan-500/40 rounded-2xl w-full max-w-[500px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 class="text-base font-extrabold font-mono text-cyan-300">🔑 Configurazione API Key Personale</h2>
          <p class="text-xs text-white/70">Collega il tuo account BingX per l'operatività automatica</p>
        </div>
        <button onclick="toggleApiModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX API Key:</label>
          <input type="text" id="userApiKeyInput" placeholder="Inserisci la tua API Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-cyan-400 outline-none" />
        </div>
        <div class="space-y-1.5">
          <label class="block text-white/80 font-bold">BingX Secret Key:</label>
          <input type="password" id="userSecretKeyInput" placeholder="Inserisci la tua Secret Key..." class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-cyan-400 outline-none" />
        </div>
        <span class="text-[10px] text-yellow-400 block">🔒 Salvate in sicurezza nel tuo browser (localStorage) per firmare crittograficamente i tuoi ordini personali.</span>
        
        <button onclick="saveUserApiKeys()" class="w-full py-2.5 rounded-xl text-xs font-bold bg-cyan-500 text-black shadow-lg hover:bg-cyan-400 transition-all">
          Salva Credenziali API
        </button>
        <button onclick="clearUserApiKeys()" class="w-full py-1.5 rounded-xl text-xs font-bold bg-red-500/20 text-[#FF334B] border border-red-500/40 hover:bg-red-500/30 transition-all">
          Disconnetti API
        </button>
      </div>
    </div>
  </div>

  <!-- MODALE DI CONFERMA ESECUZIONE CON DURATA E LIVELLI ATR -->
  <div id="assetModal" class="fixed inset-0 z-50 hidden items-center justify-center bg-black/90 backdrop-blur-sm p-3 overflow-y-auto">
    <div class="bg-[#08080A] border border-white/30 rounded-2xl w-full max-w-[600px] shadow-2xl overflow-hidden my-auto">
      <div class="px-5 py-3.5 border-b border-white/15 flex items-center justify-between bg-[#000000]">
        <div>
          <h2 id="modalSymbol" class="text-base font-extrabold font-mono text-white">BTC-USDT</h2>
          <p class="text-xs text-white/70">Esecuzione di Precisione con ATR &amp; Durata Stimata</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-2.5">
          <div class="flex justify-between"><span class="text-white/75">Asset / Categoria:</span><strong id="modalCategory" class="text-[#00FF66]">Criptovalute</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Timeframe Consigliato:</span><strong id="modalTF" class="text-cyan-400 font-extrabold">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">⏱️ Durata Stimata al TP:</span><strong id="modalDurata" class="text-yellow-300 font-bold">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Prezzo Live Corrente:</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Segnale di Ingresso:</span><strong id="modalSide" class="text-[#00FF66]">LONG (ACQUISTO)</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Stop Loss (ATR):</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Take Profit (1:2.2):</span><strong id="modalTrendingTP" class="text-[#00FF66]">--</strong></div>
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
          <span class="text-[10px] text-yellow-400 block mt-1">💡 Gestione Rischio: Raggiunto TP1, sposta lo Stop Loss a Break-Even (Pareggio).</span>
        </div>

        <div>
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-white/70 font-semibold">Taglia Contratto Consigliata (Min Sicuro):</label>
            <span id="suggestedMinSizeLabel" class="text-[10px] font-mono text-[#00FF66]">Min: --</span>
          </div>
          <input type="number" id="tradeQuantityInput" value="0.01" step="0.001" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
        </div>

        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>🎯 Esegui Ordine di Precisione su BingX</span>
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
      currentCategoryTab: 'ALL',
      searchQuery: '',
      selectedLeverage: 3,
      currentSelectedAsset: null,
      autoExecutedSymbols: new Set()
    };

    function getStoredApiKeys() {
      return {
        apiKey: localStorage.getItem('bingx_user_api_key') || '',
        secretKey: localStorage.getItem('bingx_user_secret_key') || ''
      };
    }

    function updateApiStatusUI() {
      const keys = getStoredApiKeys();
      const statusEl = document.getElementById('kpiUserApiStatus');
      const textBtn = document.getElementById('apiStatusText');
      if (keys.apiKey && keys.secretKey) {
        statusEl.textContent = "🟢 API Personali Attive";
        statusEl.className = "text-xs font-mono text-[#00FF66] block font-bold";
        textBtn.textContent = "API Personali Connesse 🟢";
      } else {
        statusEl.textContent = "⚠️ API Personali non inserite";
        statusEl.className = "text-xs font-mono text-yellow-400 block";
        textBtn.textContent = "Collega API Personali";
      }
    }

    function toggleApiModal() {
      const modal = document.getElementById('apiModal');
      const keys = getStoredApiKeys();
      document.getElementById('userApiKeyInput').value = keys.apiKey;
      document.getElementById('userSecretKeyInput').value = keys.secretKey;
      modal.classList.toggle('hidden');
      modal.classList.toggle('flex');
    }

    function saveUserApiKeys() {
      const apiKey = document.getElementById('userApiKeyInput').value.trim();
      const secretKey = document.getElementById('userSecretKeyInput').value.trim();
      if (!apiKey || !secretKey) {
        alert("Inserisci sia l'API Key che la Secret Key.");
        return;
      }
      localStorage.setItem('bingx_user_api_key', apiKey);
      localStorage.setItem('bingx_user_secret_key', secretKey);
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali API personali salvate con successo!");
    }

    function clearUserApiKeys() {
      localStorage.removeItem('bingx_user_api_key');
      localStorage.removeItem('bingx_user_secret_key');
      document.getElementById('userApiKeyInput').value = '';
      document.getElementById('userSecretKeyInput').value = '';
      updateApiStatusUI();
      toggleApiModal();
      alert("Credenziali API rimosse.");
    }

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

    // MOTORE DI PRECISIONE ATR E DURATA
    function calcolaParametriPrecisione(c) {
      const isBull = c.change24h >= 0;
      const prezzo = c.price;
      const range24h = c.high24h - c.low24h;
      
      // ATR stimato basato sull'escursione High-Low giornaliera
      const atr = range24h > 0 ? range24h * 0.04 : prezzo * 0.012;
      
      const sl = Number((isBull ? prezzo - (atr * 1.2) : prezzo + (atr * 1.2)).toFixed(2));
      const tp = Number((isBull ? prezzo + (atr * 2.5) : prezzo - (atr * 2.5)).toFixed(2));
      
      let timeframe = "1h / 4h (Swing Trend)";
      let durata = "1 - 3 ore (Intraday)";
      
      const absChange = Math.abs(c.change24h);
      if (c.category === "Materie Prime" || c.category === "Azioni & Indici (TradFi)") {
        timeframe = absChange > 2.5 ? "1h / 4h" : "4h / 1D";
        durata = "2 - 6 ore (Trend di Sessione)";
      } else if (absChange > 7.0) {
        timeframe = "5m / 15m (Scalping Rapido)";
        durata = "15 - 45 minuti (Alta Volatilità)";
      } else if (absChange > 3.5) {
        timeframe = "15m / 1h (Intraday Momentum)";
        durata = "1 - 2 ore";
      }

      return { sl, tp, timeframe, durata };
    }

    function setCategoryTab(cat) {
      state.currentCategoryTab = cat;
      ['ALL', 'Criptovalute', 'Materie Prime', 'Forex / Valute', 'Azioni & Indici (TradFi)'].forEach(c => {
        const btnId = c === 'ALL' ? 'catTabALL' : c === 'Azioni & Indici (TradFi)' ? 'catTabTradFi' : `catTab${c}`;
        const btn = document.getElementById(btnId);
        if (btn) {
          btn.className = state.currentCategoryTab === c ? "px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all" : "px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all";
        }
      });
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

      if (mode === 'AUTO') {
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-black shadow-lg";
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping";
        modeText.textContent = "PHANTOM: ATTIVO ⚡ (AUTO PRECISION)";
      } else {
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white";
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-yellow-400";
        modeText.textContent = "PHANTOM: MANUALE";
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

      document.getElementById('kpiTotalAssets').textContent = keys.length + " Asset Live";

      const topPrioritySymbols = ["BTC-USDT", "SOL-USDT", "ETH-USDT", "XRP-USDT", "DOGE-USDT", "GOLD-USDT", "NASDAQ-USDT", "AVAX-USDT"];

      let sortedCoins = keys.map(sym => ({ symbol: sym, ...state.marketData[sym] })).sort((a, b) => {
        let aTop = topPrioritySymbols.includes(a.symbol) ? 0 : 1;
        let bTop = topPrioritySymbols.includes(b.symbol) ? 0 : 1;
        if (aTop !== bTop) return aTop - bTop;
        return Math.abs(b.change24h) - Math.abs(a.change24h);
      });

      let topMover = sortedCoins[0];
      if (topMover) {
        document.getElementById('kpiTopMoverSymbol').textContent = topMover.symbol;
        const topChangeEl = document.getElementById('kpiTopMoverChange');
        topChangeEl.textContent = (topMover.change24h >= 0 ? '+' : '') + topMover.change24h.toFixed(2) + '%';
        topChangeEl.className = topMover.change24h >= 0 ? 'text-sm font-extrabold font-mono text-[#00FF66]' : 'text-sm font-extrabold font-mono text-[#FF334B]';
      }

      const masterAsset = sortedCoins.find(c => c.symbol.includes("BTC") || c.symbol.includes("SOL")) || sortedCoins[0];
      if (masterAsset) {
        const isBull = masterAsset.change24h >= 0;
        const entry = masterAsset.price;
        const param = calcolaParametriPrecisione(masterAsset);

        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <div class="flex items-center justify-between"><strong class="text-white text-sm">${masterAsset.symbol} - MASTER CHIRURGICO</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">Prezzo Live:</span><strong class="text-[#00FF66] text-sm">$${entry.toLocaleString()}</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏱️ Timeframe &amp; Durata:</span><strong class="text-cyan-300 font-extrabold">${param.timeframe}</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏳ Tempo Stimato al TP:</span><strong class="text-yellow-300 font-bold">${param.durata}</strong></div>
            </div>
            <div class="space-y-2 flex flex-col justify-between">
              <div class="bg-black/40 border border-[#FFCC00]/30 rounded-xl p-3 space-y-1.5">
                <div class="flex justify-between"><span class="text-[#FF334B]">Stop Loss (ATR):</span><strong class="text-[#FF334B]">$${param.sl.toLocaleString()}</strong></div>
                <div class="flex justify-between"><span class="text-[#00FF66]">Take Profit (1:2.5):</span><strong class="text-[#00FF66]">$${param.tp.toLocaleString()}</strong></div>
                <div class="flex justify-between"><span class="text-white/70">⚡ Leva Consigliata:</span><strong class="text-yellow-400">3x (Isolato)</strong></div>
              </div>
              <button onclick='openModal("${masterAsset.symbol}", ${entry}, ${param.tp}, ${param.sl}, "${isBull ? 'BUY' : 'SELL'}", "${param.timeframe}", "${param.durata}", "${masterAsset.category}")' class="green-gradient-btn w-full py-2 rounded-xl text-xs shadow-lg animate-pulse">
                🎯 ESEGUI SEGNALE CHIRURGICO ORA &#8599;
              </button>
            </div>
          </div>
        `;
      }

      let filteredCoins = sortedCoins;
      if (state.currentCategoryTab !== 'ALL') {
        filteredCoins = filteredCoins.filter(c => c.category === state.currentCategoryTab);
      }
      if (state.searchQuery) {
        filteredCoins = filteredCoins.filter(c => c.symbol.toLowerCase().includes(state.searchQuery) || c.category.toLowerCase().includes(state.searchQuery));
      }

      const categoriesMap = {};
      filteredCoins.forEach(c => {
        if (!categoriesMap[c.category]) categoriesMap[c.category] = [];
        categoriesMap[c.category].push(c);
      });

      const container = document.getElementById('tablesContainer');
      if (Object.keys(categoriesMap).length === 0) {
        container.innerHTML = `<div class="p-6 text-center text-white/50 font-mono text-sm">Nessun asset trovato.</div>`;
        return;
      }

      container.innerHTML = Object.keys(categoriesMap).map(cat => {
        const assets = categoriesMap[cat];
        let catIcon = "🪙";
        if (cat === "Materie Prime") catIcon = "🛢️";
        else if (cat === "Forex / Valute") catIcon = "💱";
        else if (cat === "Azioni & Indici (TradFi)") catIcon = "📈";

        return `
          <div class="space-y-3">
            <div class="flex items-center justify-between bg-[#08080A] px-3 py-2 rounded-xl border border-white/10">
              <h3 class="text-xs font-bold uppercase tracking-wider text-[#00FF66] flex items-center gap-2">
                <span>${catIcon}</span> ${cat} <span class="text-white/50 text-[10px]">(${assets.length} asset)</span>
              </h3>
            </div>
            <div class="overflow-x-auto">
              <table class="w-full text-left border-collapse">
                <thead>
                  <tr class="border-b border-white/15 text-[10px] font-bold uppercase tracking-wider text-white/75 bg-[#000000]">
                    <th class="py-2.5 pl-3">Asset Symbol</th>
                    <th class="py-2.5 px-2.5">Segnale</th>
                    <th class="py-2.5 px-2.5">⏱️ TF &amp; Durata</th>
                    <th class="py-2.5 px-2.5 text-right">Prezzo Live</th>
                    <th class="py-2.5 px-2.5 text-right">Var. 24h 🔥</th>
                    <th class="py-2.5 px-2.5">SL &amp; TP (ATR)</th>
                    <th class="py-2.5 px-2.5 text-right">Azione / Esegui</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/10 text-xs font-mono">
                  ${assets.map(c => {
                    const isBull = c.change24h >= 0;
                    const param = calcolaParametriPrecisione(c);
                    const statusText = Math.abs(c.change24h) > 2.0 ? (isBull ? 'LONG' : 'SHORT') : 'ATTESA';
                    const statusBadgeColor = statusText === 'LONG' ? 'bg-green-500/20 text-[#00FF66] border-green-500/40' : statusText === 'SHORT' ? 'bg-red-500/20 text-[#FF334B] border-red-500/40' : 'bg-white/10 text-white/70 border-white/20';

                    if (state.tradingMode === 'AUTO' && Math.abs(c.change24h) > 4.5 && !state.autoExecutedSymbols.has(c.symbol)) {
                      state.autoExecutedSymbols.add(c.symbol);
                      const keys = getStoredApiKeys();
                      eseguiOperazioneServer({
                        symbol: c.symbol,
                        action: isBull ? 'BUY' : 'SELL',
                        size: Math.max(0.001, Number((10.0 / c.price).toFixed(4))),
                        entry: c.price,
                        tp: param.tp,
                        sl: param.sl,
                        leverage: 3,
                        timeframe: param.timeframe,
                        apiKey: keys.apiKey,
                        secretKey: keys.secretKey
                      }, true);
                    }

                    return `
                      <tr class="hover:bg-white/5 transition-colors cursor-pointer" onclick='openModal("${c.symbol}", ${c.price}, ${param.tp}, ${param.sl}, "${isBull ? 'BUY' : 'SELL'}", "${param.timeframe}", "${param.durata}", "${c.category}")'>
                        <td class="py-2.5 pl-3 font-bold text-white">${c.symbol.replace('-USDT', '')}</td>
                        <td class="py-2.5 px-2.5"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${statusBadgeColor}">${statusText}</span></td>
                        <td class="py-2.5 px-2.5 text-cyan-300 font-bold">${param.timeframe.split(' ')[0]} <span class="text-white/50 text-[9px]">(${param.durata.split(' ')[0]}h)</span></td>
                        <td class="py-2.5 px-2.5 text-right tabular-nums text-white font-bold">$${c.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                        <td class="py-2.5 px-2.5 text-right tabular-nums ${isBull ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-extrabold">${isBull ? '+' : ''}${c.change24h.toFixed(2)}%</td>
                        <td class="py-2.5 px-2.5 text-white/80">$${param.sl.toLocaleString()} / $${param.tp.toLocaleString()}</td>
                        <td class="py-2.5 px-2.5 text-right">
                          <button class="px-3 py-1 rounded-lg bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50 font-bold text-xs hover:bg-[#00FF66] hover:text-black transition-all">
                            Esegui ⚡
                          </button>
                        </td>
                      </tr>
                    `;
                  }).join('')}
                </tbody>
              </table>
            </div>
          </div>
        `;
      }).join('');
    }

    function openModal(symbol, livePrice, tp, sl, side, tf, durata, category) {
      let minSize = 0.001;
      if (livePrice > 0) {
        minSize = Math.max(0.001, Number((10.0 / livePrice).toFixed(4)));
        if (minSize < 0.001) minSize = 0.001;
      }

      state.currentSelectedAsset = { symbol, livePrice, tp, sl, side, tf, durata, category, minSize };
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalCategory').textContent = category;
      document.getElementById('modalTF').textContent = tf;
      document.getElementById('modalDurata').textContent = durata;
      document.getElementById('modalLivePrice').textContent = `$${livePrice.toLocaleString()}`;
      document.getElementById('modalSide').textContent = `${side === 'BUY' ? 'LONG (ACQUISTO)' : 'SHORT (VENDITA)'}`;
      document.getElementById('modalSide').className = side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]';
      document.getElementById('modalSL').textContent = `$${sl.toLocaleString()}`;
      document.getElementById('modalTrendingTP').textContent = `$${tp.toLocaleString()}`;
      
      document.getElementById('tradeQuantityInput').value = minSize;
      document.getElementById('suggestedMinSizeLabel').textContent = `Min Sicuro: ${minSize}`;

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
      const quantity = parseFloat(document.getElementById('tradeQuantityInput').value) || asset.minSize;
      const leverage = state.selectedLeverage;
      const keys = getStoredApiKeys();

      const btn = document.getElementById('executeBingXBtn');
      const feedback = document.getElementById('executionFeedback');

      btn.disabled = true;
      btn.textContent = `Invio ordine di precisione su BingX...`;
      feedback.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedback.classList.add('text-white/70');
      feedback.textContent = `Configurazione Leva ${leverage}x, ATR Stop Loss e TP (${asset.durata})...`;

      const res = await eseguiOperazioneServer({
        symbol: asset.symbol,
        action: asset.side === 'BUY' ? 'BUY' : 'SELL',
        size: quantity,
        entry: asset.livePrice,
        tp: asset.tp,
        sl: asset.sl,
        leverage: leverage,
        timeframe: asset.tf,
        apiKey: keys.apiKey,
        secretKey: keys.secretKey
      }, false);

      if (res.code === 0) {
        feedback.classList.remove('text-white/70', 'text-[#FF334B]');
        feedback.classList.add('text-[#00FF66]');
        feedback.textContent = `✔ Successo! Posizione aperta con livelli ATR e TP protettivi.`;
        playChime();
        logFeed(`[PRECISION TRADE] ${asset.symbol} (${asset.side}) - Durata stimata: ${asset.durata}`);
      } else {
        feedback.classList.remove('text-white/70', 'text-[#00FF66]');
        feedback.classList.add('text-[#FF334B]');
        feedback.textContent = `✖ Errore: ${res.msg || 'Controlla le tue API Key personali'}`;
        logFeed(`[ERRORE] ${asset.symbol}: ${res.msg}`);
      }

      btn.disabled = false;
      btn.textContent = "🎯 Esegui Ordine di Precisione su BingX";
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
            logFeed(`[AUTO ⚡] Segnale di precisione eseguito su ${payload.symbol} (${payload.action})`);
            playChime();
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
      updateApiStatusUI();
      fetchServerMode();
      pollMarketData();
      setInterval(pollMarketData, 3000); // Aggiornamento mercato ogni 3 secondi
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
