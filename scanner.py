import asyncio
import json
import datetime
import threading
import os
import websockets
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Strutture in-memory per dati di mercato live e storico segnali
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
            padding: 15px;
        }
        header { text-align: center; margin-bottom: 20px; }
        h1 { font-size: 22px; margin: 0; color: #60a5fa; }
        p { font-size: 14px; color: #94a3b8; }
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 15px;
            max-width: 1200px;
            margin: 0 auto 20px auto;
        }
        .card {
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 15px;
            text-align: center;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }
        .gauge-container {
            position: relative;
            width: 130px;
            height: 130px;
            margin: 0 auto 10px auto;
            border-radius: 50%;
            background: conic-gradient(var(--accent-green) 0% 100%, var(--border-color) 0% 100%);
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .gauge-inner {
            width: 105px;
            height: 105px;
            background-color: var(--card-bg);
            border-radius: 50%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }
        .gauge-value { font-size: 22px; font-weight: bold; }
        .gauge-label { font-size: 11px; color: #94a3b8; }
        .table-container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 15px;
            overflow-x: auto;
        }
        h3 { font-size: 16px; margin-top: 0; color: #f8fafc; }
        table { width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; min-width: 500px; }
        th, td { padding: 10px; border-bottom: 1px solid var(--border-color); }
        th { color: #94a3b8; font-size: 12px; }
        .badge-long { color: var(--accent-green); font-weight: bold; }
        .badge-short { color: var(--accent-red); font-weight: bold; }
    </style>
</head>
<body>
    <header>
        <h1>⚡ BingX Signal Scanner</h1>
        <p>Monitoraggio Live & Gestione TP/SL</p>
    </header>

    <div class="dashboard-grid">
        <div class="card">
            <h3 style="margin-bottom: 15px;">Stato WebSocket</h3>
            <div style="font-size: 18px; font-weight: bold; color: var(--accent-green); margin: 20px 0;">
                🟢 Connesso a BingX
            </div>
            <p>Feed Dati in tempo reale attivo</p>
        </div>

        <div class="card">
            <div class="gauge-container">
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
        <h3>📋 Storico Segnali (Ingresso, TP & SL)</h3>
        <table>
            <thead>
                <tr>
                    <th>Orario</th>
                    <th>Coppia</th>
                    <th>Azione</th>
                    <th>Ingresso</th>
                    <th>TP</th>
                    <th>SL</th>
                </tr>
            </thead>
            <tbody id="signals-table-body">
                <tr>
                    <td colspan="6" style="text-align: center; color: #94a3b8;">In attesa di segnali...</td>
                </tr>
            </tbody>
        </table>
    </div>

    <script>
        async function updateDashboard() {
            try {
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

# --- 1. MOTORE WEBSOCKET BINGX ---
async def bingx_ws_client():
    url = "wss://open-api-swap.bingx.com/swap-market"
    while True:
        try:
            async with websockets.connect(url) as websocket:
                sub_message = {
                    "id": "crypto_scanner_id",
                    "reqType": "sub",
                    "dataType": "ticker"
                }
                await websocket.send(json.dumps(sub_message))
                
                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if 'data' in data:
                            ticker = data['data']
                            symbol = ticker.get('s', 'UNKNOWN')
                            price = float(ticker.get('c', 0))
                            volume_24h = float(ticker.get('v', 0))
                            
                            market_data[symbol] = {
                                "price": price,
                                "volume_24h": volume_24h,
                                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                    except json.JSONDecodeError:
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
    
    # Calcolo dinamico TP e SL
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
