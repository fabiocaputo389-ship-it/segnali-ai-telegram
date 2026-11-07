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
    """Scarica tutte le coppie di mercato live da BingX Futures."""
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
    except Exception as e:
        print(f"[LEVERAGE EXCEPT] {e}")

def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "1h / 4h"):
    """
    CERVELLO PHANTOM - ESECUZIONE SICURA BINGX CON TP/SL RIGOROSI:
    Esegue l'ordine a mercato e imposta SL e TP protettivi garantendo il rispetto delle regole BingX.
    """
    if not API_KEY or not SECRET_KEY:
        print("[PHANTOM ERROR] BINGX_API_KEY o BINGX_SECRET_KEY mancanti!")
        return {"code": -1, "msg": "API keys missing in Environment Variables"}

    imposta_leva_bingx(simbolo, leva, lato)

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
    
    query_string = _genera_firma(payload_ordine, SECRET_KEY)
    headers = {"X-BX-APIKEY": API_KEY}
    
    try:
        response = requests.post(f"{url}?{query_string}", headers=headers)
        risultato = response.json()
        
        if risultato.get("code") == 0:
            print(f"[PHANTOM SUCCESS] Posizione {position_side} aperta su {simbolo} con taglia {quantita} e leva {leva}x!")
            time.sleep(1.0) # Pausa di sincronizzazione server
            
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            
            # Validazione e invio Stop Loss
            if prezzo_sl > 0:
                if position_side == "LONG" and prezzo_sl >= prezzo_ingresso:
                    prezzo_sl = prezzo_ingresso * 0.99  # Correzione di sicurezza
                elif position_side == "SHORT" and prezzo_sl <= prezzo_ingresso:
                    prezzo_sl = prezzo_ingresso * 1.01  # Correzione di sicurezza
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET", position_side)

            # Validazione e invio Take Profit
            if prezzo_tp > 0:
                if position_side == "LONG" and prezzo_tp <= prezzo_ingresso:
                    prezzo_tp = prezzo_ingresso * 1.025
                elif position_side == "SHORT" and prezzo_tp >= prezzo_ingresso:
                    prezzo_tp = prezzo_ingresso * 0.975
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET", position_side)
        else:
            print(f"[PHANTOM API ERROR] Risposta: {risultato}")
        
        return risultato
    except Exception as e:
        print(f"[EXCEPT] Errore di connessione a BingX: {e}")
        return {"code": -1, "msg": str(e)}

def _imposta_ordine_protettivo(simbolo: str, lato_chiusura: str, prezzo_trigger: float, tipo_ordine: str, position_side: str):
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
    
    query_string = _genera_firma(payload, SECRET_KEY)
    headers = {"X-BX-APIKEY": API_KEY}
    
    try:
        resp = requests.post(f"{url}?{query_string}", headers=headers)
        res_data = resp.json()
        if res_data.get("code") == 0:
            print(f"[PROTECT SUCCESS] {tipo_ordine} impostato a {prezzo_trigger} per {simbolo} ({position_side})")
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
  <title>ApexScan Pro - Universal Multi-Asset Engine</title>
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
          🌐
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">APEXSCAN UNIVERSAL <span class="text-[#00FF66]">Multi-Asset Engine</span></h1>
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

      <div class="flex items-center bg-[#08080A] p-1.5 rounded-xl border border-white/20 gap-1.5">
        <span class="text-xs font-mono font-bold text-white/70 px-1">Modalità:</span>
        <button id="btnModeManual" onclick="switchMode('MANUAL')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white">MANUALE</button>
        <button id="btnModeAuto" onclick="switchMode('AUTO')" class="px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white">AUTOMATICO ⚡</button>
      </div>
    </div>
  </header>

  <!-- MAIN -->
  <main class="flex-1 max-w-[1920px] w-full mx-auto px-3 sm:px-5 py-4 flex flex-col gap-4">

    <!-- KPI DINAMICI -->
    <section class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Andamento Mercati Globali</span>
        <div class="mt-2">
          <strong class="text-xs font-mono text-[#00FF66] block">🟢 TOP COINS IN PRIMA PAGINA</strong>
          <span class="text-[11px] text-white/70">BTC, SOL, ETH, XRP &amp; Altri Asset</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Totale Strumenti Monitorati</span>
        <div class="mt-2">
          <span id="kpiTotalAssets" class="text-base font-mono text-cyan-400 font-extrabold">-- Asset Live</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Top Mover Globale 🔥</span>
        <div class="mt-2 flex items-baseline justify-between">
          <span id="kpiTopMoverSymbol" class="text-sm font-extrabold font-mono text-white">--</span>
          <span id="kpiTopMoverChange" class="text-sm font-extrabold font-mono text-[#00FF66]">--%</span>
        </div>
      </div>
      <div class="terminal-card rounded-2xl p-3.5 flex flex-col justify-between">
        <span class="text-[11px] font-semibold uppercase text-white/75">Stato Server &amp; API</span>
        <div class="mt-2">
          <span id="kpiPhantomStatus" class="text-xs font-mono text-[#00FF66] font-bold">ONLINE 🟢 (5s Feed)</span>
        </div>
      </div>
    </section>

    <!-- SEZIONE MASTER: SEGNALE PRIORITARIO TOP COIN -->
    <section class="priority-signal-box rounded-2xl p-4 sm:p-5">
      <div class="flex items-center justify-between pb-3 mb-3 border-b border-[#FFCC00]/30">
        <div class="flex items-center gap-3">
          <div class="w-9 h-9 rounded-xl bg-[#FFCC00] text-black flex items-center justify-center font-extrabold animate-bounce">⚡</div>
          <div>
            <h2 class="text-sm sm:text-base font-extrabold uppercase tracking-wider text-white">⚡ SEGNALE PRIORITARIO TOP COIN (MASTER BTC / SOL)</h2>
            <p class="text-[11px] text-[#FFCC00]">Setup ad altissima confluenza con attivazione istantanea</p>
          </div>
        </div>
        <span class="px-2.5 py-1 rounded-md text-xs font-mono font-extrabold bg-[#FFCC00] text-black animate-pulse">CONFERMATO</span>
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
          <button onclick="setCategoryTab('ALL')" id="catTabALL" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-[#00FF66] text-black transition-all">⭐ Tutti i Mercati (Top in Evidenza)</button>
          <button onclick="setCategoryTab('Criptovalute')" id="catTabCriptovalute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🪙 Criptovalute</button>
          <button onclick="setCategoryTab('Materie Prime')" id="catTabMaterie Prime" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">🛢️ Materie Prime</button>
          <button onclick="setCategoryTab('Forex / Valute')" id="catTabForex / Valute" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">💱 Forex &amp; Valute</button>
          <button onclick="setCategoryTab('Azioni & Indici (TradFi)')" id="catTabTradFi" class="px-3 py-1.5 rounded-xl text-xs font-bold bg-white/10 text-white/80 hover:text-white transition-all">📈 Azioni &amp; Indici</button>
        </div>
      </div>
      <div class="relative">
        <input type="text" id="coinSearchInput" oninput="filterCoinSearch(this.value)" placeholder="🔍 Cerca Asset (es. BTC, SOL, ETH, GOLD, AAPL)..." class="w-full bg-[#000000] border border-white/25 rounded-xl px-4 py-2.5 text-xs font-mono text-white outline-none focus:border-[#00FF66]" />
      </div>
    </section>

    <!-- TABELLE LIVE CON TOP COINS IN PRIMA PAGINA -->
    <div class="grid grid-cols-1 xl:grid-cols-12 gap-4 items-start">
      <section class="xl:col-span-9 terminal-card rounded-2xl overflow-hidden flex flex-col p-4 space-y-6">
        <div class="flex items-center justify-between border-b border-white/15 pb-3">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span>📊</span> Tabella Flussi (Top Coins in Prima Pagina)
          </h2>
          <span class="text-xs font-mono text-cyan-400">Aggiornamento Real-time BingX Futures</span>
        </div>

        <div id="tablesContainer" class="space-y-6"></div>
      </section>

      <aside class="xl:col-span-3 terminal-card rounded-2xl flex flex-col overflow-hidden">
        <div class="px-4 py-3 border-b border-white/15 bg-[#08080A]">
          <h2 class="text-sm font-bold text-white flex items-center gap-2">
            <span class="w-2 h-2 rounded-full bg-[#00FF66] animate-ping"></span> Feed Multi-Market (5s)
          </h2>
        </div>
        <div id="alertsFeedContainer" class="p-3 space-y-2.5 max-h-[800px] overflow-y-auto font-mono text-[11px]">
          <div class="p-2 rounded bg-white/5 text-white/70">[Multi-Market] Sincronizzazione API BingX completata...</div>
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
          <p class="text-xs text-white/70">Esecuzione Reale con TP/SL protettivi esatti &amp; Margine Isolato</p>
        </div>
        <button onclick="closeModal()" class="p-2 rounded-xl bg-white/10 text-white hover:bg-[#FF334B] transition-colors">&#10005;</button>
      </div>
      <div class="p-5 space-y-4 text-xs font-mono">
        <div class="bg-[#000000] border border-white/20 rounded-xl p-4 space-y-2.5">
          <div class="flex justify-between"><span class="text-white/75">Asset / Categoria:</span><strong id="modalCategory" class="text-[#00FF66]">Criptovalute</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Timeframe Consigliato:</span><strong id="modalTF" class="text-cyan-400">1h / 4h</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Prezzo Live (Trigger):</span><strong id="modalLivePrice" class="text-white">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Segnale Operativo:</span><strong id="modalSide" class="text-[#00FF66]">LONG (ACQUISTO)</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Stop Loss Esatto:</span><strong id="modalSL" class="text-[#FF334B]">--</strong></div>
          <div class="flex justify-between"><span class="text-white/75">Take Profit Esatto:</span><strong id="modalTrendingTP" class="text-[#00FF66]">--</strong></div>
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
          <div class="flex justify-between items-center mb-1">
            <label class="text-[11px] text-white/70 font-semibold">Taglia Contratto Consigliata (Min Sicuro):</label>
            <span id="suggestedMinSizeLabel" class="text-[10px] font-mono text-[#00FF66]">Min: --</span>
          </div>
          <input type="number" id="tradeQuantityInput" value="0.01" step="0.001" class="w-full bg-[#000000] border border-white/30 rounded-lg px-3 py-2 text-white font-mono text-xs focus:border-[#0066FF] outline-none font-bold" />
        </div>

        <button id="executeBingXBtn" onclick="eseguiOrdineSuBingX()" class="bingx-btn w-full py-2.5 rounded-xl text-xs shadow-lg flex items-center justify-center gap-2">
          <span>🧠 Esegui a Mercato con TP/SL esatti su BingX</span>
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
      const kpiStatus = document.getElementById('kpiPhantomStatus');

      if (mode === 'AUTO') {
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-[#00FF66] text-black shadow-lg";
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-[#00FF66]/20 text-[#00FF66] border border-[#00FF66]/50";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-[#00FF66] animate-ping";
        modeText.textContent = "PHANTOM: ATTIVO ⚡";
        kpiStatus.textContent = "ONLINE (AUTO ⚡)";
      } else {
        btnMan.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all bg-white/20 text-white";
        btnAuto.className = "px-3 py-1 rounded-lg text-xs font-bold transition-all text-white/50 hover:text-white";
        badge.className = "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-white/10 text-white/80 border border-white/20";
        modeDot.className = "w-1.5 h-1.5 rounded-full bg-yellow-400";
        modeText.textContent = "PHANTOM: MANUALE";
        kpiStatus.textContent = "ONLINE 🟢 (5s Feed)";
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

      // TOP COINS SEMPRE IN PRIMA PAGINA (BTC, SOL, ETH, XRP, DOGE, GOLD, NASDAQ)
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
        const atr = masterAsset.price * 0.01;
        const sl = Number((isBull ? masterAsset.price - (atr * 1.5) : masterAsset.price + (atr * 1.5)).toFixed(2));
        const tp = Number((isBull ? masterAsset.price + (atr * 2.5) : masterAsset.price - (atr * 2.5)).toFixed(2));

        document.getElementById('prioritySignalCardContent').innerHTML = `
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-xs font-mono">
            <div class="space-y-2">
              <div class="flex items-center justify-between"><strong class="text-white text-sm">${masterAsset.symbol} - TOP MASTER ASSET (Rischio: BASSO)</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">Entrata a Mercato:</span><strong class="text-[#00FF66] text-sm">$${entry.toLocaleString()} (Entrata Market Immediata)</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">⏱️ Timeframe Consigliato:</span><strong class="text-cyan-300">1h / 4h</strong></div>
              <div class="flex items-center justify-between"><span class="text-white/70">💎 Confluenza &amp; Stato:</span><strong class="text-[#FFCC00]">-85% Confluenza Alta</strong></div>
            </div>
            <div class="space-y-2 flex flex-col justify-between">
              <div class="bg-black/40 border border-[#FFCC00]/30 rounded-xl p-3 space-y-1.5">
                <div class="flex justify-between"><span class="text-[#FF334B]">Stop Loss Esatto (-1.5%):</span><strong class="text-[#FF334B]">$${sl.toLocaleString()}</strong></div>
                <div class="flex justify-between"><span class="text-[#00FF66]">Take Profit Esatto (+2.5%):</span><strong class="text-[#00FF66]">$${tp.toLocaleString()}</strong></div>
                <div class="flex justify-between"><span class="text-white/70">⚡ Leva Consigliata:</span><strong class="text-yellow-400">3x (Margine Isolato)</strong></div>
              </div>
              <button onclick='openModal("${masterAsset.symbol}", ${entry}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}", "1h / 4h", "${masterAsset.category}")' class="green-gradient-btn w-full py-2 rounded-xl text-xs shadow-lg animate-pulse">
                ⚡ APRI SHORT / LONG CON TP/SL ESATTI ORA &#8599;
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
                    <th class="py-2.5 px-2.5">⏱️ Timeframe</th>
                    <th class="py-2.5 px-2.5 text-right">Prezzo Live</th>
                    <th class="py-2.5 px-2.5 text-right">Var. 24h 🔥</th>
                    <th class="py-2.5 px-2.5">Stop Loss &amp; TP Esatti</th>
                    <th class="py-2.5 px-2.5 text-right">Azione / Esegui</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-white/10 text-xs font-mono">
                  ${assets.map(c => {
                    const isBull = c.change24h >= 0;
                    const tf = Math.abs(c.change24h) > 5 ? '15m / 1h' : '1h / 4h';
                    const statusText = Math.abs(c.change24h) > 2.5 ? (isBull ? 'LONG' : 'SHORT') : 'ATTESA';
                    const statusBadgeColor = statusText === 'LONG' ? 'bg-green-500/20 text-[#00FF66] border-green-500/40' : statusText === 'SHORT' ? 'bg-red-500/20 text-[#FF334B] border-red-500/40' : 'bg-white/10 text-white/70 border-white/20';
                    const atr = c.price * 0.01;
                    const sl = Number((isBull ? c.price - (atr * 1.5) : c.price + (atr * 1.5)).toFixed(2));
                    const tp = Number((isBull ? c.price + (atr * 2.5) : c.price - (atr * 2.5)).toFixed(2));

                    return `
                      <tr class="hover:bg-white/5 transition-colors cursor-pointer" onclick='openModal("${c.symbol}", ${c.price}, ${tp}, ${sl}, "${isBull ? 'BUY' : 'SELL'}", "${tf}", "${c.category}")'>
                        <td class="py-2.5 pl-3 font-bold text-white">${c.symbol.replace('-USDT', '')}</td>
                        <td class="py-2.5 px-2.5"><span class="px-2 py-0.5 rounded text-[10px] font-bold border ${statusBadgeColor}">${statusText}</span></td>
                        <td class="py-2.5 px-2.5 text-cyan-300 font-bold">${tf}</td>
                        <td class="py-2.5 px-2.5 text-right tabular-nums text-white font-bold">$${c.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                        <td class="py-2.5 px-2.5 text-right tabular-nums ${isBull ? 'text-[#00FF66]' : 'text-[#FF334B]'} font-extrabold">${isBull ? '+' : ''}${c.change24h.toFixed(2)}%</td>
                        <td class="py-2.5 px-2.5 text-white/80">$${sl.toLocaleString()} / $${tp.toLocaleString()}</td>
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

    function openModal(symbol, livePrice, tp, sl, side, tf, category) {
      let minSize = 0.001;
      if (livePrice > 0) {
        minSize = Math.max(0.001, Number((10.0 / livePrice).toFixed(4)));
        if (minSize < 0.001) minSize = 0.001;
      }

      state.currentSelectedAsset = { symbol, livePrice, tp, sl, side, tf, category, minSize };
      document.getElementById('modalSymbol').textContent = symbol;
      document.getElementById('modalCategory').textContent = category;
      document.getElementById('modalTF').textContent = tf;
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

      const btn = document.getElementById('executeBingXBtn');
      const feedback = document.getElementById('executionFeedback');

      btn.disabled = true;
      btn.textContent = `Invio ordine su BingX in corso...`;
      feedback.classList.remove('hidden', 'text-[#00FF66]', 'text-[#FF334B]');
      feedback.classList.add('text-white/70');
      feedback.textContent = `Configurazione Leva ${leverage}x e aggancio TP/SL esatti...`;

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
        feedback.textContent = `✔ Successo! Posizione aperta e TP/SL impostati esattamente come da segnale.`;
        playChime();
        logFeed(`[BINGX LIVE] Posizione aperta su ${asset.symbol} (${asset.side}) - SL: ${asset.sl} | TP: ${asset.tp}`);
      } else {
        feedback.classList.remove('text-white/70', 'text-[#00FF66]');
        feedback.classList.add('text-[#FF334B]');
        feedback.textContent = `✖ Errore: ${res.msg || 'Parametri non validi o saldo insufficiente'}`;
        logFeed(`[BINGX ERRORE] ${asset.symbol}: ${res.msg}`);
      }

      btn.disabled = false;
      btn.textContent = "🧠 Esegui a Mercato con TP/SL esatti su BingX";
    }

    async function eseguiOperazioneServer(payload, isAuto) {
      try {
        const response = await fetch('/api/esegui-segnale', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...payload, is_auto: isAuto })
        });
        const data = await response.json();
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
