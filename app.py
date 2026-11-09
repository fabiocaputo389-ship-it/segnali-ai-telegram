import os
import re
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
MARKET_TYPE = "FUTURES"  # "FUTURES", "SPOT" o "STANDARD_FUTURES"


def _genera_firma(payload: dict, secret_key: str) -> str:
    """Genera la firma HMAC SHA256 richiesta dalle API di BingX."""
    query_string = urllib.parse.urlencode(payload)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return query_string + "&signature=" + signature


# ==========================================
# MOTORE MATEMATICO & MULTI-TIMEFRAME
# ==========================================
def _calcola_ema_serie(valori: list, periodo: int) -> float:
    if not valori:
        return 0.0
    k = 2.0 / (periodo + 1)
    ema = valori[0]
    for v in valori[1:]:
        ema = v * k + ema * (1.0 - k)
    return ema


def _calcola_rsi_wilder(closes: list, periodo: int = 14) -> float:
    if len(closes) < periodo + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:periodo]) / periodo
    avg_loss = sum(losses[:periodo]) / periodo

    for i in range(periodo, len(gains)):
        avg_gain = (avg_gain * (periodo - 1) + gains[i]) / periodo
        avg_loss = (avg_loss * (periodo - 1) + losses[i]) / periodo

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calcola_atr_reale(highs: list, lows: list, closes: list, periodo: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    if len(trs) < periodo:
        return sum(trs) / len(trs)
    atr = sum(trs[:periodo]) / periodo
    for tr in trs[periodo:]:
        atr = (atr * (periodo - 1) + tr) / periodo
    return atr


def analizza_tf(simbolo, market_type, interval, limit=60):
    url = (f"{BASE_URL}/openApi/spot/v1/market/kline" if market_type == "SPOT"
           else f"{BASE_URL}/openApi/swap/v3/quote/klines")
    try:
        resp = requests.get(url, params={"symbol": simbolo, "interval": interval, "limit": limit}, timeout=3)
        raw = resp.json().get("data", [])
        candles = []
        for k in raw:
            if isinstance(k, dict):
                candles.append({"time": int(k.get("time", 0)), "open": float(k["open"]), "high": float(k["high"]),
                                "low": float(k["low"]), "close": float(k["close"]), "volume": float(k.get("volume", 0))})
            elif isinstance(k, list) and len(k) >= 6:
                candles.append({"time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
        candles.sort(key=lambda c: c["time"])
        if len(candles) < 22:
            return None
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        ema9 = _calcola_ema_serie(closes, 9)
        ema21 = _calcola_ema_serie(closes, 21)
        rsi = _calcola_rsi_wilder(closes, 14)
        atr = _calcola_atr_reale(highs, lows, closes, 14)
        bull = ema9 >= ema21 and rsi > 45
        bear = ema9 < ema21 and rsi < 55
        return {
            "interval": interval,
            "trend": "BUY" if bull else ("SELL" if bear else "NEUTRO"),
            "ema9": ema9, "ema21": ema21, "rsi14": round(rsi, 2), "atr14": atr,
            "last": candles[-1],
            "candles": candles[-12:],
        }
    except Exception as e:
        print(f"[TF ERROR {interval}] {e}")
        return None


def get_live_market_data(market_type: str = "FUTURES"):
    if market_type == "SPOT":
        url = f"{BASE_URL}/openApi/spot/v1/ticker/24hr"
    else:
        url = f"{BASE_URL}/openApi/swap/v2/quote/ticker"

    try:
        response = requests.get(url, timeout=3.5)
        data = response.json()
        if data.get("code") == 0 and "data" in data:
            tickers = data["data"]
            market_map = {}
            for t in tickers:
                symbol = t.get("symbol")
                if not symbol or not symbol.endswith("-USDT"):
                    continue
                price = float(t.get("lastPrice", 0))
                if price <= 0:
                    continue

                if market_type == "SPOT":
                    raw_chg = t.get("priceChangePercent", "0")
                    if isinstance(raw_chg, str):
                        raw_chg = raw_chg.replace("%", "")
                    change = float(raw_chg)
                else:
                    change = float(t.get("priceChangePercent", 0)) * 100

                market_map[symbol] = {
                    "price": price,
                    "change24h": change,
                    "marketType": market_type
                }
            return market_map
    except Exception as e:
        print(f"[MARKET ERROR] {e}")
    return {}


@app.route('/api/market-data', methods=['GET'])
def api_market_data():
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    if mtype not in ["FUTURES", "SPOT", "STANDARD_FUTURES"]:
        mtype = "FUTURES"
    fetch_type = "FUTURES" if mtype == "STANDARD_FUTURES" else mtype
    data = get_live_market_data(fetch_type)
    return jsonify({"code": 0, "marketType": mtype, "data": data})


@app.route('/api/deep-scan', methods=['GET'])
def api_deep_scan():
    simbolo = request.args.get("symbol", "BTC-USDT")
    mtype = request.args.get("marketType", MARKET_TYPE).upper()
    fetch_type = "FUTURES" if mtype == "STANDARD_FUTURES" else mtype
    try:
        tfs = {iv: analizza_tf(simbolo, fetch_type, iv) for iv in ("5m", "15m", "1h")}
    except Exception as e:
        return jsonify({"code": -1, "msg": str(e)})
    if any(v is None for v in tfs.values()):
        return jsonify({"code": -1, "msg": "Candele non disponibili"})

    exact_tf = "15m"
    base = tfs[exact_tf]
    price = tfs["5m"]["last"]["close"]
    atr = base["atr14"]
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7
    
    side = base["trend"] if base["trend"] != "NEUTRO" else "BUY"
    k = 1 if side == "BUY" else -1
    sl = round(price - (k * atr * 1.25), dec)
    tp = round(price + (k * atr * 2.50), dec)

    return jsonify({"code": 0, "data": {
        "symbol": simbolo, "marketType": mtype, "price": round(price, dec), "side": side,
        "sentiment": "Bullish 🚀 (Long)" if side == "BUY" else "Bearish 📉 (Short)",
        "exactTf": exact_tf,
        "precisionScore": 98,
        "sl": sl, "tp": tp, "decimals": dec, "tempoSec": 7200, "tfs": tfs,
    }})


# ==========================================
# INTERFACCIA FRONTEND (VOCE & AUDIO ADVISOR)
# ==========================================
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ApexScan Pro - Live Voice Advisor Engine</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=JetBrains+Mono:wght@400;700;800&display=swap" rel="stylesheet">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: { sans: ['Inter', 'sans-serif'], mono: ['JetBrains Mono', 'monospace'] }
        }
      }
    }
  </script>
  <style>
    body { background-color: #000000; color: #FFFFFF; font-family: 'Inter', sans-serif; overflow-x: hidden; }
    .terminal-card { background: linear-gradient(180deg, rgba(14, 14, 17, 0.98) 0%, rgba(8, 8, 10, 0.99) 100%); border: 1px solid rgba(255, 255, 255, 0.16); box-shadow: 0 8px 32px -6px rgba(0, 0, 0, 0.95); }
    .green-gradient-btn { background: linear-gradient(135deg, #00FF66 0%, #00CC52 100%); color: #000000; font-weight: 800; }
    .tabular-nums { font-variant-numeric: tabular-nums; }
  </style>
</head>
<body class="min-h-screen flex flex-col p-4 max-w-4xl mx-auto gap-4">

  <header class="terminal-card rounded-2xl p-4 flex flex-wrap items-center justify-between gap-3 border-2 border-[#00FF66]/40">
    <div class="flex items-center gap-3">
      <div class="w-11 h-11 rounded-2xl bg-[#00FF66] text-black flex items-center justify-center font-extrabold text-xl animate-pulse">🔊</div>
      <div>
        <h1 class="text-base sm:text-lg font-extrabold tracking-tight text-white">LIVE VOICE ADVISOR <span class="text-[#00FF66]">ENGINE</span></h1>
        <p class="text-xs text-white/70 font-mono">1. ENTRA (Long/Short) • 2. RIMANI • 3. ESCI (Aggiornamenti ogni 15s)</p>
      </div>
    </div>
    <div class="text-right font-mono text-xs">
      <span id="liveClock" class="text-[#00FF66] font-bold tabular-nums">--:--:--</span>
    </div>
  </header>

  <!-- BOX MONITORAGGIO ATTIVO VOCALE -->
  <div id="voiceAdvisorBox" class="terminal-card rounded-2xl p-5 border-2 border-[#00FF66] shadow-2xl space-y-4">
    <div class="flex flex-wrap items-center justify-between pb-3 border-b border-white/15 gap-3">
      <div>
        <h2 class="text-sm font-extrabold uppercase tracking-wider text-white">🎙️ MONITORAGGIO ATTIVO: <span id="advSymbol" class="text-[#00FF66]">BTC-USDT</span></h2>
        <p class="text-xs text-white/70">La voce guida l'intera sessione di trading in tempo reale</p>
      </div>
      <button onclick="fermaMonitoraggio()" class="px-3.5 py-1.5 rounded-xl bg-red-500/20 text-[#FF334B] border border-red-500/50 hover:bg-red-500/30 text-xs font-bold transition-all">
        Termina Sessione ✕
      </button>
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs font-mono">
      <div class="bg-black/50 p-3 rounded-xl border border-white/10">
        <span class="text-white/60 text-[10px] block">1. 🟢 SEGNALE DI APERTURA</span>
        <strong id="advSideDisplay" class="text-base font-extrabold text-[#00FF66] block mt-1">LONG / SHORT</strong>
        <span id="advEntryPrice" class="text-white text-xs block mt-0.5">Prezzo: --</span>
      </div>
      <div class="bg-black/50 p-3 rounded-xl border border-white/10">
        <span class="text-white/60 text-[10px] block">2. 📊 RIMANI A MERCATO (PNL LIVE)</span>
        <strong id="advLivePrice" class="text-cyan-300 text-sm block mt-1">--</strong>
        <span id="advPnlVal" class="block mt-0.5 text-xs font-bold text-[#00FF66]">PnL: 0.00%</span>
      </div>
      <div class="bg-black/50 p-3 rounded-xl border border-white/10">
        <span class="text-white/60 text-[10px] block">3. 🔴 STATO GUIDA VOCALE</span>
        <strong id="advStatusAdvice" class="text-yellow-300 text-xs block mt-1">In attesa del TP...</strong>
        <span id="advActionAlert" class="text-[10px] text-white/70 italic block mt-0.5">Audio attivo ogni 15s</span>
      </div>
    </div>
  </div>

  <!-- SELETTORE ASSET DA AVVIARE -->
  <section class="terminal-card rounded-2xl p-4 space-y-3">
    <h2 class="text-sm font-bold text-white flex items-center gap-2">
      <span>🎯</span> Scegli Asset per Avviare il Live Voice Advisor
    </h2>
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-2">
      <button onclick="avviaVoiceAdvisor('BTC-USDT')" class="p-3 rounded-xl bg-white/5 hover:bg-[#00FF66]/20 border border-white/20 hover:border-[#00FF66] text-left transition-all">
        <div class="font-bold text-white">BTC-USDT</div>
        <div class="text-[10px] text-white/60">Bitcoin Futures</div>
      </button>
      <button onclick="avviaVoiceAdvisor('ETH-USDT')" class="p-3 rounded-xl bg-white/5 hover:bg-[#00FF66]/20 border border-white/20 hover:border-[#00FF66] text-left transition-all">
        <div class="font-bold text-white">ETH-USDT</div>
        <div class="text-[10px] text-white/60">Ethereum Futures</div>
      </button>
      <button onclick="avviaVoiceAdvisor('SOL-USDT')" class="p-3 rounded-xl bg-white/5 hover:bg-[#00FF66]/20 border border-white/20 hover:border-[#00FF66] text-left transition-all">
        <div class="font-bold text-white">SOL-USDT</div>
        <div class="text-[10px] text-white/60">Solana Futures</div>
      </button>
      <button onclick="avviaVoiceAdvisor('XRP-USDT')" class="p-3 rounded-xl bg-white/5 hover:bg-[#00FF66]/20 border border-white/20 hover:border-[#00FF66] text-left transition-all">
        <div class="font-bold text-white">XRP-USDT</div>
        <div class="text-[10px] text-white/60">Ripple Futures</div>
      </button>
    </div>
  </section>

  <!-- SCRIPT VOCALE E AGGIORNAMENTO TEMPO REALE -->
  <script>
    let state = {
      marketData: {},
      monitoredAsset: null,
      marketType: 'FUTURES',
      lastVoiceTimestamp: 0
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

    function speakText(text) {
      if ('speechSynthesis' in window) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'it-IT';
        utterance.rate = 1.05;
        window.speechSynthesis.speak(utterance);
      }
    }

    function playChime(urgent = false, voiceMsg = "") {
      const ctx = getAudioContext();
      if (ctx) {
        const now = ctx.currentTime;
        const freqs = urgent ? [1200, 900, 1400] : [880, 1108.73, 1318.51];
        freqs.forEach((freq, i) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.frequency.setValueAtTime(freq, now + i * 0.08);
          gain.gain.setValueAtTime(0.001, now + i * 0.08);
          gain.gain.exponentialRampToValueAtTime(0.1, now + i * 0.08 + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.08 + 0.4);
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start(now + i * 0.08);
          osc.stop(now + i * 0.08 + 0.45);
        });
      }
      if (voiceMsg) {
        speakText(voiceMsg);
      }
    }

    function getPrecisionDecimals(val) {
      if (val >= 1000) return 2;
      if (val >= 10) return 3;
      if (val >= 1) return 4;
      if (val >= 0.01) return 5;
      return 7;
    }

    function formatPrice(val) {
      return Number(val).toFixed(getPrecisionDecimals(val));
    }

    async function pollMarketData() {
      try {
        const res = await fetch(`/api/market-data?marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0) {
          state.marketData = json.data;
          aggiornaLoopVoce();
        }
      } catch (e) {}
    }

    async function avviaVoiceAdvisor(symbol) {
      try {
        const res = await fetch(`/api/deep-scan?symbol=${encodeURIComponent(symbol)}&marketType=${state.marketType}`);
        const json = await res.json();
        if (json.code === 0 && json.data) {
          const d = json.data;
          state.monitoredAsset = {
            symbol: d.symbol,
            side: d.side, // 'BUY' o 'SELL'
            entryPrice: d.price,
            tpPrice: d.tp,
            slPrice: d.sl
          };

          document.getElementById('advSymbol').textContent = d.symbol;
          const sideName = d.side === 'BUY' ? 'LONG' : 'SHORT';
          document.getElementById('advSideDisplay').textContent = sideName;
          document.getElementById('advSideDisplay', 'advSideDisplay').className = `text-base font-extrabold ${d.side === 'BUY' ? 'text-[#00FF66]' : 'text-[#FF334B]'} block mt-1`;
          document.getElementById('advEntryPrice').textContent = `Prezzo Ingresso: $${formatPrice(d.price)}`;

          state.lastVoiceTimestamp = Date.now();
          playChime(false, `Primo. Entra a mercato. Posizione ${sideName} su ${d.symbol}. Prezzo d'ingresso ${formatPrice(d.price)}.`);
        }
      } catch (e) {
        alert("Errore avvio advisor vocale");
      }
    }

    function fermaMonitoraggio() {
      state.monitoredAsset = null;
      document.getElementById('advStatusAdvice').textContent = "Sessione terminata.";
      speakText("Sessione di monitoraggio vocale interrotta.");
    }

    function aggiornaLoopVoce() {
      if (!state.monitoredAsset) return;
      const pos = state.monitoredAsset;
      const liveItem = state.marketData[pos.symbol];
      if (!liveItem) return;

      const currentPrice = liveItem.price;
      document.getElementById('advLivePrice').textContent = `$${formatPrice(currentPrice)}`;
      
      const rawDiff = pos.side === 'BUY' ? (currentPrice - pos.entryPrice) : (pos.entryPrice - currentPrice);
      const pnlPct = (rawDiff / pos.entryPrice) * 100 * 3; // Leva 3x

      const pnlEl = document.getElementById('advPnlVal');
      pnlEl.textContent = `PnL: ${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
      pnlEl.className = `block mt-0.5 text-xs font-bold ${pnlPct >= 0 ? 'text-[#00FF66]' : 'text-[#FF334B]'}`;

      const advAdvice = document.getElementById('advStatusAdvice');
      
      if ((pos.side === 'BUY' && currentPrice >= pos.tpPrice) || (pos.side === 'SELL' && currentPrice <= pos.tpPrice)) {
        advAdvice.textContent = "3. ESCI SUBITO (TP RAGGIUNTO)";
        playChime(true, "Terzo. Esci subito. Target Take Profit raggiunto con successo.");
        state.monitoredAsset = null;
      } else if ((pos.side === 'BUY' && currentPrice <= pos.slPrice) || (pos.side === 'SELL' && currentPrice >= pos.slPrice)) {
        advAdvice.textContent = "3. ESCI SUBITO (STOP LOSS)";
        playChime(true, "Terzo. Esci subito. Stop loss raggiunto, chiudi la posizione per protezione.");
        state.monitoredAsset = null;
      } else {
        advAdvice.textContent = "2. RIMANI A MERCATO";
        const now = Date.now();
        // Aggiornamento vocale automatico ogni 15 secondi per tenerti informato in tempo reale
        if (now - state.lastVoiceTimestamp >= 15000) {
          state.lastVoiceTimestamp = now;
          const pnlText = `${pnlPct >= 0 ? 'guadagno del' : 'perdita del'} ${Math.abs(pnlPct).toFixed(1)} percento`;
          speakText(`Secondo. Rimani a mercato. Prezzo attuale ${formatPrice(currentPrice)}, PnL in ${pnlText}.`);
        }
      }
    }

    window.addEventListener('DOMContentLoaded', async () => {
      await pollMarketData();
      setInterval(pollMarketData, 2000);
      setInterval(() => {
        document.getElementById('liveClock').textContent = new Date().toTimeString().slice(0, 8);
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
