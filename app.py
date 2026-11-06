from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import datetime
import requests

app = FastAPI(title="ApexScan Pro Live API")

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"<h3>Errore caricamento index.html: {e}</h3>"

def get_fear_and_greed():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=3)
        data = res.json()
        return {"value": int(data['data'][0]['value']), "classification": data['data'][0]['value_classification']}
    except:
        return {"value": 71, "classification": "Greed"}

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
                "fear_greed_text": fng["classification"]
            }
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        print("Client disconnesso dal WebSocket")
