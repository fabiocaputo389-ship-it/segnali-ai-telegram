import asyncio
import json
import datetime
import threading
import os
import websockets
from flask import Flask, render_template, request, jsonify

# Configurazione dell'app Flask puntando alla cartella 'templates' per la Dashboard HTML
app = Flask(__name__, template_folder='templates')

# Strutture in-memory per dati live e storico segnali
market_data = {}
latest_signals = []

# --- 1. MOTORE WEBSOCKET BINGX (Background Thread) ---
async def bingx_ws_client():
    """Connessione persistente al WebSocket pubblico di BingX per i ticker."""
    url = "wss://open-api-swap.bingx.com/swap-market"
    
    while True:
        try:
            print(f"[{datetime.datetime.now()}] Connessione al WebSocket di BingX in corso...")
            async with websockets.connect(url) as websocket:
                
                # Sottoscrizione al flusso dati ticker perpetuo
                sub_message = {
                    "id": "crypto_scanner_id",
                    "reqType": "sub",
                    "dataType": "ticker"
                }
                await websocket.send(json.dumps(sub_message))
                print("Sottoscrizione WebSocket BingX completata con successo!")

                async for message in websocket:
                    try:
                        data = json.loads(message)
                        if 'data' in data:
                            ticker = data['data']
                            symbol = ticker.get('s', 'UNKNOWN')
                            price = float(ticker.get('c', 0))
                            volume_24h = float(ticker.get('v', 0))
                            
                            # Aggiornamento stato di mercato in-memory
                            market_data[symbol] = {
                                "price": price,
                                "volume_24h": volume_24h,
                                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                    except json.JSONDecodeError:
                        continue
                        
        except Exception as e:
            print(f"Connessione WebSocket interrotta ({e}). Riconnessione tra 5 secondi...")
            await asyncio.sleep(5)

def start_background_loop(loop):
    """Esegue il client WebSocket in un thread separato senza bloccare Flask."""
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bingx_ws_client())


# --- 2. ROTTE DEL SERVER FLASK (Dashboard & API) ---

@app.route('/')
def home():
    """Serve la Dashboard HTML con i tachometri e la tabella live."""
    return render_template('index.html')

@app.route('/status', methods=['GET'])
def status_api():
    """API JSON per monitorare lo stato generale e le coppie attive."""
    return jsonify({
        "status": "online",
        "active_pairs_monitored": len(market_data),
        "latest_market_snapshot": market_data
    })

@app.route('/webhook', methods=['POST'])
def tradingview_webhook():
    """Riceve i segnali da TradingView, calcola TP/SL e registra l'orario."""
    data = request.json
    if not data:
        return "Payload non valido", 400

    symbol = data.get('symbol', 'UNKNOWN')
    action = data.get('action', 'LONG')
    entry_price = float(data.get('price', 0))
    
    # Gestione dinamica di Take Profit e Stop Loss basata sul prezzo d'ingresso
    if action.upper() == 'LONG':
        stop_loss = entry_price * 0.985   # -1.5%
        take_profit = entry_price * 1.03  # +3.0%
    else:
        stop_loss = entry_price * 1.015   # +1.5%
        take_profit = entry_price * 0.97  # -3.0%
        
    entry_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    signal_payload = {
        "symbol": symbol,
        "action": action,
        "entry_time": entry_time,
        "entry_price": entry_price,
        "take_profit": round(take_profit, 4),
        "stop_loss": round(stop_loss, 4)
    }
    
    # Inserisce il segnale in cima alla lista (massimo 50 elementi salvati)
    latest_signals.insert(0, signal_payload)
    if len(latest_signals) > 50:
        latest_signals.pop()

    print(f"🚨 SEGNALE RILEVATO & ELABORATO: {signal_payload}")
    
    return jsonify({"status": "success", "signal": signal_payload}), 200

@app.route('/signals', methods=['GET'])
def get_signals():
    """Restituisce lo storico degli ultimi segnali operativi alla Dashboard."""
    return jsonify(latest_signals)


# --- 3. AVVIO DELL'APPLICAZIONE ---
if __name__ == '__main__':
    # 1. Avvia il client WebSocket in background
    ws_loop = asyncio.new_event_loop()
    t = threading.Thread(target=start_background_loop, args=(ws_loop,), daemon=True)
    t.start()

    # 2. Avvia il server Flask richiesto da Render
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
