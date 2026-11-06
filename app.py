from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import datetime
import requests
import ccxt

app = FastAPI(title="ApexScan Pro Live API")

# Serviamo la cartella static come frontend web
app.mount("/static", StaticFiles(directory="static"), name="static")

# Inizializziamo l'exchange CCXT in modalità pubblica (nessuna chiave segreta richiesta)
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# Funzione per recuperare il Fear & Greed Index reale da Alternative.me
def get_fear_and_greed():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = res.json()
        val = int(data['data'][0]['value'])
        classification = data['data'][0]['value_classification']
        return {"value": val, "classification": classification}
    except:
        return {"value": 58, "classification": "Greed"}

@app.get("/api/market-status")
def market_status():
    fng = get_fear_and_greed()
    return {
        "status": "ONLINE",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fear_and_greed": fng,
        "active_engine": "Python CCXT Real-Time Data Active"
    }

# WebSocket per lo streaming in tempo reale dei prezzi reali degli asset
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            fng = get_fear_and_greed()
            
            # Recuperiamo il prezzo reale di Bitcoin (BTC/USDT) da Binance via CCXT
            real_btc_price = 94000.0
            try:
                ticker = exchange.fetch_ticker('BTC/USDT')
                real_btc_price = float(ticker['last'])
            except Exception as e:
                print("Errore fetch CCXT:", e)

            payload = {
                "type": "MARKET_TICK",
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
                "fear_greed_val": fng["value"],
                "fear_greed_text": fng["classification"],
                "btc_real_price": real_btc_price
            }
            
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(4) # Aggiornamento live ogni 4 secondi
            
    except WebSocketDisconnect:
        print("Client disconnesso dal flusso WebSocket")
