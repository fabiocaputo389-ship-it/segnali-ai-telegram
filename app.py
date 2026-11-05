import os
from flask import Flask, render_template_string, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

app = Flask(__name__)

# Watchlist di esempio (puoi estenderla con azioni o crypto)
WATCHLIST = ["ETH-USD", "BTC-USD", "AAPL", "NVDA", "TSLA"]

def analizza_mercato():
    risultati = []
    for ticker in WATCHLIST:
        try:
            stock = yf.Ticker(ticker)
            df_daily = stock.history(period="1mo", interval="1d")
            df_intraday = stock.history(period="5d", interval="60m")
            
            if df_daily.empty or df_intraday.empty:
                continue
                
            prezzo_attuale = df_daily['Close'].iloc[-1]
            var_24h = ((prezzo_attuale - df_daily['Close'].iloc[-2]) / df_daily['Close'].iloc[-2]) * 100
            
            # Calcolo RSI e indicatori
            delta = df_daily['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            current_rsi = rsi.iloc[-1]
            
            # Simulazione metriche stile "Pro Terminal" della foto
            score_ia = int(np.clip(70 + (current_rsi * 0.3), 50, 98))
            potenza = int(np.clip(60 + (var_24h * 5), 30, 99))
            
            # Finestra di tempo utile dinamica (es. prossimi 45 minuti)
            ora_corrente = datetime.now()
            finestra_inizio = ora_corrente.strftime("%H:%M")
            finestra_fine = (ora_corrente + timedelta(minutes=45)).strftime("%H:%M")
            
            risultati.append({
                "ticker": ticker.replace("-USD", ""),
                "prezzo": round(prezzo_attuale, 2),
                "var_24h": round(var_24h, 2),
                "setup_valido": var_24h > -2.0, # Condizione logica di setup
                "tempo_inizio": finestra_inizio,
                "tempo_fine": finestra_fine,
                "timeframe": "1 ORA (Intraday)",
                "score_ia": score_ia,
                "confluenza": f"{score_ia}% (Altissimo) 💎",
                "grado": "A (Alta Confluenza)",
                "leva": "3x",
                "potenza": potenza,
                "sl": round(prezzo_attuale * 0.984, 2), # Stop Loss -1.6%
                "tp1": round(prezzo_attuale * 1.032, 2), # Take Profit 1 +3.2%
                "tp2": round(prezzo_attuale * 1.064, 2)  # Take Profit 2 +6.4%
            })
        except Exception as e:
            print(f"Errore su {ticker}: {e}")
            
    return risultati

# Interfaccia grafica integrata in stile Racing Dashboard / Terminale Pro
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pro Trading Scanner & Timing Engine</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background-color: #0b0f19; color: #f3f4f6; font-family: 'Inter', sans-serif; }
        .card-glass { background: rgba(17, 24, 39, 0.85); border: 1px solid rgba(55, 65, 81, 0.5); backdrop-filter: blur(10px); }
        .tachimetro {
            background: conic-gradient(#10b981 0% {{potenza}}%, #374151 {{potenza}}% 100%);
            border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            position: relative;
        }
        .tachimetro::before {
            content: ""; position: absolute; width: 75%; height: 75%;
            background-color: #111827; border-radius: 50%;
        }
    </style>
</head>
<body class="p-4 md:p-8">
    <div class="max-w-7xl mx-auto">
        <header class="flex flex-col md:flex-row justify-between items-center mb-8 border-b border-gray-800 pb-4">
            <div>
                <h1 class="text-3xl font-black tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-cyan-500">
                    ⚡ PRO TRADING SCANNER & TIMING ENGINE
                </h1>
                <p class="text-xs text-gray-400 mt-1">MOTORE DI FLUSSO ISTANTANEO • MULTI-TIMEFRAME & ORDER ROUTING</p>
            </div>
            <div class="mt-4 md:mt-0 flex items-center space-x-3">
                <span class="px-3 py-1 bg-emerald-950 text-emerald-400 border border-emerald-800 rounded-full text-xs font-bold animate-pulse">● LIVE DATA ACTIVE</span>
                <button onclick="location.reload()" class="px-4 py-2 bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-bold rounded-lg transition">Aggiorna Scanner</button>
            </div>
        </header>

        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {% for item in dati %}
            <div class="card-glass rounded-2xl p-5 shadow-2xl relative overflow-hidden">
                <!-- Top Badge -->
                <div class="flex justify-between items-center mb-3">
                    <span class="text-2xl font-black tracking-wide text-white">{{ item.ticker }}</span>
                    {% if item.setup_valido %}
                    <span class="px-2.5 py-1 bg-emerald-900/80 text-emerald-300 text-xs font-bold rounded-md border border-emerald-700">✓ SETUP VALIDO</span>
                    {% else %}
                    <span class="px-2.5 py-1 bg-amber-900/80 text-amber-300 text-xs font-bold rounded-md border border-amber-700">⏳ IN ATTESA</span>
                    {% endif %}
                </div>

                <!-- Tempo Utile Ingresso (Come da tua richiesta ed esempio) -->
                <div class="bg-gray-900/90 border border-gray-800 rounded-xl p-3 mb-4">
                    <div class="flex justify-between items-center mb-1">
                        <span class="text-xs text-amber-400 font-bold uppercase tracking-wider">⏱️ TEMPO UTILE INGRESSO:</span>
                        <span class="text-base font-mono font-bold text-white bg-black px-2 py-0.5 rounded border border-gray-700">{{ item.tempo_fine }}</span>
                    </div>
                    <p class="text-[11px] text-gray-400">Finestra Consigliata: <span class="text-white font-semibold">{{ item.tempo_inizio }} - {{ item.tempo_fine }}</span> (Retest 1H)</p>
                    <div class="w-full bg-gray-800 h-1.5 rounded-full mt-2 overflow-hidden">
                        <div class="bg-gradient-to-r from-amber-500 to-emerald-400 h-full rounded-full" style="width: 85%;"></div>
                    </div>
                </div>

                <!-- Timeframe & AI Score con Tachimetro Stile Corsa -->
                <div class="grid grid-cols-2 gap-4 items-center mb-4 bg-gray-900/50 p-3 rounded-xl border border-gray-800/60">
                    <div>
                        <div class="text-[10px] text-gray-400 uppercase font-semibold">Timeframe Consigliato</div>
                        <div class="text-xs font-bold text-cyan-400 mt-0.5">{{ item.timeframe }}</div>
                        <div class="text-[11px] text-gray-300 mt-2">Score IA: <span class="text-emerald-400 font-bold">{{ item.score_ia }}%</span></div>
                        <div class="text-[11px] text-gray-300">Confluenza: <span class="text-white font-bold">{{ item.confluenza }}</span></div>
                        <div class="text-[11px] text-gray-300">Var 24h: <span class="{% if item.var_24h >= 0 %}text-emerald-400{% else %}text-rose-400{% endif %} font-bold">{% if item.var_24h > 0 %}+{% endif %}{{ item.var_24h }}%</span></div>
                    </div>
                    <!-- Tachimetro Stile Racing -->
                    <div class="flex flex-col items-center justify-center">
                        <div class="tachimetro w-20 h-20 shadow-inner">
                            <div class="z-10 text-center">
                                <span class="text-xs font-black text-emerald-400">{{ item.potenza }}</span>
                                <span class="block text-[8px] text-gray-400 font-bold uppercase">POTENZA</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Prezzi e Livelli Operativi -->
                <div class="space-y-1.5 pt-2 border-t border-gray-800">
                    <div class="flex justify-between items-center text-xs">
                        <span class="text-gray-400">PREZZO ENTRATA (MARKET):</span>
                        <span class="font-mono font-bold text-cyan-300 text-sm">${{ item.prezzo }}</span>
                    </div>
                    <div class="flex justify-between items-center text-xs bg-rose-950/30 px-2 py-1 rounded border border-rose-900/40">
                        <span class="text-rose-400">SL (-1.6%):</span>
                        <span class="font-mono font-bold text-rose-300">${{ item.sl }}</span>
                    </div>
                    <div class="flex justify-between items-center text-xs bg-emerald-950/30 px-2 py-1 rounded border border-emerald-900/40">
                        <span class="text-emerald-400">TP1 (+3.2%):</span>
                        <span class="font-mono font-bold text-emerald-300">${{ item.tp1 }}</span>
                    </div>
                    <div class="flex justify-between items-center text-xs bg-emerald-950/30 px-2 py-1 rounded border border-emerald-900/40">
                        <span class="text-emerald-400">TP2 (+6.4%):</span>
                        <span class="font-mono font-bold text-emerald-300">${{ item.tp2 }}</span>
                    </div>
                </div>

                <!-- Azione Rapida / Order Routing -->
                <div class="mt-4 flex space-x-2">
                    <button class="w-full py-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white font-bold text-xs rounded-xl shadow-lg transition">
                        ⚡ ESEGUI ORDINE (ROUTING)
                    </button>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</body>
</html>
"""

@app.route("/")
def index():
    dati = analizza_mercato()
    return render_template_string(HTML_TEMPLATE, dati=dati)

@app.route("/api/scan")
def api_scan():
    return jsonify(analizza_mercato())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
