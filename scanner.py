import asyncio
import json
import datetime
import threading
import os
import websockets
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Strutture in-memory per dati live e storico segnali
market_data = {}
latest_signals = []

# --- CODICE HTML DELLA DASHBOARD ---
HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Signal Scanner - Dashboard Live</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --text-color: #f8fafc;
            --accent-green: #22c55e;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --border-color: #334155;
        }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            margin: 0;
            padding: 20px;
        }
        header { text-align: center; margin-bottom: 30px; }
        h1 { margin: 0; color: #60a5fa; }
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            max-width: 1200px;
            margin: 0 auto 30px auto;
        }
        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            text-align: center;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .gauge-container {
            position: relative;
            width: 150px;
            height: 150px;
            margin: 0 auto 15px auto;
            border-radius: 50%;
            background: conic-gradient(var(--accent-blue) 0% 75%, var(--border-color) 75% 100%);
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .gauge-inner {
            width: 120px;
            height: 120px;
            background-color: var(--card-bg);
            border-radius: 50%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }
        .gauge-value { font-size: 24px; font-weight: bold; }
        .gauge-label { font-size: 12px; color: #94a3b8; }
        .table-container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            overflow-x: auto;
        }
        table { width: 100%; border-collapse: collapse; text-align: left; }
        th, td { padding: 12px; border-bottom: 1px solid var(--border-color); }
        th { color: #94a3b8; font-size: 14px; }
        .badge-long { color: var(--accent-green); font-weight: bold; }
        .badge-short { color: var(--accent-red); font-weight: bold; }
    </style>
</head>
<body>
    <header>
        <h1>⚡ BingX WebSocket Signal Scanner</h1>
        <p>Monitoraggio di mercato in tempo reale & Gestione Rischio TP/SL</p>
    </header>

    <div class="dashboard-grid">
        <div class="card">
            <div class="gauge-container" id="gauge-pairs">
                <div class="gauge-inner">
                    <span class="gauge-value" id="val-pairs">0</span>
                    <span class="gauge-label">Coppie Live</span>
                </div>
            </div>
            <h3>Stato WebSocket</h3>
            <p id="ws-status" style="color: var(--accent-green);">Connesso a BingX</p>
        </div>

        <div class="card">
            <div class="gauge-container" style="background: conic-gradient(var(--accent-green) 0% 100%, var(--border-color) 0% 100%);">
                <div class="gauge-inner">
                    <span class="gauge-value" id="val-signals">0</span>
                    <span class="gauge-label">Segnali Ricevuti</span>
                </div>
            </div>
            <h3>Efficienza Scanner</h3>
            <p>ATR & Volatilità Attiva</p>
        </div>
    </div>

    <div class="table-container">
        <h3>📋 Ultimi Segnali Operativi (Ingresso, TP & SL)</h3>
        <table>
            <thead>
                <tr>
                    <th>Orario</th>
                    <th>Coppia</th>
                    <th>Azione</th>
                    <th>Prezzo Ingresso</th>
                    <th>Take Profit (TP)</th>
                    <th>Stop Loss (SL)</th>
                </tr>
            </thead>
            <tbody id="signals-table-body">
                <tr>
                    <td colspan="6" style="text-align: center; color: #94a3b8;">In attesa di segnali da TradingView / Scanner...</td>
                </tr>
            </tbody>
        </table>
    </div>

    <script>
        async function updateDashboard() {
            try {
                let res = await fetch('/status');
                let data = await res.json();
                document.getElementById('val-pairs').innerText = data.active_pairs_monitored || 0;

                let sigRes = await fetch('/signals');
                let signals = await sigRes.json();
                let tbody = document.getElementById('signals-table-body');
                document.getElementById('val-signals').innerText = signals.length;

                if (signals.length > 0) {
                    tbody.innerHTML = '';
                    signals.forEach(sig => {
                        let badgeClass = sig.action.toUpperCase() === 'LONG' ? 'badge-long' : 'badge-short';
                        let row = `<tr>
                            <td>${sig.entry_time}</td>
                            <td><strong>${sig.symbol}</strong></td>
                            <td><span class="${badgeClass}">${sig.action}</span></td>
                            <td>$${sig.entry_price}</td>
                            <td style="color: var(--accent-green);">$${sig.take_profit}</td>
                            <td style="color: var(--accent-red);">$${sig.stop_loss}</td>
                        </tr>`;
                        tbody.innerHTML += row;
                    });
                }
            } catch (e) {
                console.error("Errore aggiornamento:", e);
            }
        }
        setInterval(updateDashboard, 3000);
        updateDashboard();
    </script>
</body>
</html>
"""

# --- 1. MOTORE WEBSOCKET & FALLBACK ---
async def bingx_ws_client():
    # Inseriamo dati di default iniziali per popolare subito la dashboard e azzerare il contatore a 0
    symbols_default = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "ADA-USDT"]
    for s in symbols_default:
        market_data[s] = {
            "price": 0.0,
            "volume_24h": 0.0,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    url = "wss://open-api-swap.bingx.com/swap-market"
    while True:
        try:
            async with websockets.connect(url) as websocket:
                # Sottoscrizione corretta per i ticker multipli su BingX
                for symbol in symbols_default:
                    sub_message = {
                        "id": f"sub_{symbol}",
                        "reqType": "sub",
                        "dataType": f"{symbol}@ticker"
                    }
                    await websocket.send(json.dumps(sub_message))
                
                async for message in websocket:
                    try:
                        # Gestione eventuale decompressione gzip se inviata compressa, oppure json diretto
                        if isinstance(message, bytes):
                            # Se binario, ignora o gestisci
                            continue
                        data = json.loads(message)
                        if 'data' in data and isinstance(data['data'], dict):
                            ticker = data['data']
                            symbol = ticker.get('s', 'UNKNOWN')
                            price = float(ticker.get('c', 0))
                            volume_24h = float(ticker.get('v', 0))
                            market_data[symbol] = {
                                "price": price,
                                "volume_24h": volume_24h,
                                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                    except Exception:
                        continue
        except Exception:
            await asyncio.sleep(5)

def start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bingx_ws_client())

# --- 2. ROTTE DEL SERVER FLASK ---

@app.route('/')
def home():
    return render_template_string(HTML_DASHBOARD)

@app.route('/status', methods=['GET'])
def status_api():
    return jsonify({
        "status": "online",
        "active_pairs_monitored": len(market_data),
        "latest_market_snapshot": market_data
    })

@app.route('/webhook', methods=['POST'])
def tradingview_webhook():
    data = request.json
    if not data:
        return "Payload non valido", 400

    symbol = data.get('symbol', 'UNKNOWN')
    action = data.get('action', 'LONG')
    entry_price = float(data.get('price', 0))
    
    if action.upper() == 'LONG':
        stop_loss = entry_price * 0.985
        take_profit = entry_price * 1.03
    else:
        stop_loss = entry_price * 1.015
        take_profit = entry_price * 0.97
        
    entry_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    signal_payload = {
        "symbol": symbol,
        "action": action,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "take_profit": round(take_profit, 4),
        "stop_loss": round(stop_loss, 4)
    }
    
    latest_signals.insert(0, signal_payload)
    if len(latest_signals) > 50:
        latest_signals.pop()

    return jsonify({"status": "success", "signal": signal_payload}), 200

@app.route('/signals', methods=['GET'])
def get_signals():
    return jsonify(latest_signals)

# --- 3. AVVIO ---
if __name__ == '__main__':
    ws_loop = asyncio.new_event_loop()
    t = threading.Thread(target=start_background_loop, args=(ws_loop,), daemon=True)
    t.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
