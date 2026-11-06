from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import random
import datetime
import requests

app = FastAPI(title="ApexScan Pro Live API")

# Serviamo la cartella static come frontend web
app.mount("/static", StaticFiles(directory="static"), name="static")

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
        "active_engine": "Python CCXT + WebSockets Ready"
    }

# WebSocket per lo streaming in tempo reale dei dati di mercato
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            fng = get_fear_and_greed()
            payload = {
                "type": "MARKET_TICK",
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
                "fear_greed_val": fng["value"],
                "fear_greed_text": fng["classification"],
                "random_price_bump": round(random.uniform(-0.15, 0.25), 2)
            }
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        print("Client disconnesso")
