import asyncio
import json
import datetime
import threading
import websockets
from flask import Flask, request, jsonify

app = Flask(__name__)

# Struttura in-memory per memorizzare l'ultimo stato dei mercati monitorati
market_data = {}
latest_signals = []

# --- 1. MOTORE WEBSOCKET BINGX ---
async def bingx_ws_client():
    """Si connette al WebSocket pubblico di BingX per ricevere i ticker in tempo reale."""
    url = "wss://open-api-swap.bingx.com/swap-market"
    
    while True:
        try:
            print(f"[{datetime.datetime.now()}] Connessione al WebSocket di BingX in corso...")
            async with websockets.connect(url) as websocket:
                
                # Sottoscrizione ai ticker perpetui principali di BingX
                sub_message = {
                    "id": "crypto_scanner_id",
                    "reqType": "sub",
                    "dataType": "ticker"
                }
                await websocket.send(json.dumps(sub_message))
                print("Sottoscrizione WebSocket BingX completata con successo!")

                async for message in websocket:
                    # Gestione del flusso dati in arrivo
                    try:
                        data = json.loads(message)
                        
                        # BingX invia a volte ping di controllo, verifichiamo la presenza dei dati di mercato
                        if 'data' in data:
                            ticker = data['data']
                            symbol = ticker.get('s', 'UNKNOWN')
                            price = float(ticker.get('c', 0))
                            volume_24h = float(ticker.get('v', 0))
                            
                            # Salviamo lo stato live
                            market_data[symbol] = {
                                "price": price,
                                "volume_24h": volume_24h,
                                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            }
                            
                            # Esempio di logica di scanner: se il prezzo supera una soglia o rileva volatilità
                            # Qui puoi inserire la tua strategia matematica o incrocio indicatori
                            
                    except json.JSONDecodeError:
                        continue
                        
        except (websockets.ConnectionClosed, Exception as e:
            print(f"Connessione WebSocket interrotta ({e}). Tentativo di riconnessione tra 5 secondi...")
            await asyncio.sleep(5)

def start_background_loop(loop):
    """Esegue il client WebSocket in un thread separato rispetto a Flask."""
    asyncio.set_event_loop(loop)
    loop.run_until_complete(bingx_ws_client())

# --- 2. SERVER FLASK PER RENDER E WEBHOOK ---

@app.route('/')
def home():
    """Dashboard di stato base dell'applicazione su Render."""
    return jsonify({
        "status": "online",
        "active_pairs_monitored": len(market_data),
        "latest_market_snapshot": market_data
    })

@app.route('/webhook', methods=['POST'])
def tradingview_webhook():
    """Riceve i segnali da TradingView, calcola orario, TP e SL e registra l'evento."""
    data = request.json
    if not data:
        return "Payload non valido", 400

    symbol = data.get('symbol', 'UNKNOWN')
    action = data.get('action', 'LONG')
    entry_price = float(data.get('price', 0))
    
    # Calcolo dinamico di TP e SL basato su percentuali di rischio (es. SL 1.5%, TP 3%)
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
    if len(latest_signals) > 50:  # Mantieni solo gli ultimi 50 segnali in memoria
        latest_signals.pop()

    print(f"🚨 SEGNALE RILEVATO: {signal_payload}")
    
    # Qui puoi integrare l'invio della notifica su Telegram usando la libreria requests o python-telegram-bot
    
    return jsonify({"status": "success", "signal": signal_payload}), 200

@app.route('/signals', methods=['GET'])
def get_signals():
    """Restituisce gli ultimi segnali generati con i relativi livelli di TP e SL."""
    return jsonify(latest_signals)

if __name__ == '__main__':
    # Avvia il WebSocket di BingX in un thread in background
    ws_loop = asyncio.new_event_loop()
    t = threading.Thread(target=start_background_loop, args=(ws_loop,), daemon=True)
    t.start()

    # Avvia il server Flask richiesto da Render
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
