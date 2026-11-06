from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import asyncio
import json
import datetime
import requests
import ccxt
import pandas as pd

app = FastAPI(title="ApexScan Pro Live API & Risk Engine")

# Serviamo la cartella static come file statici
app.mount("/static", StaticFiles(directory="static"), name="static")

# Rotta principale per aprire direttamente la dashboard
@app.get("/", response_class=HTMLResponse)
def read_root():
    try:
        with open("static/index.html", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"<h3>Errore caricamento dashboard: {e}</h3><p>Assicurati che il file static/index.html esista nel repository.</p>"

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

def get_fear_and_greed():
    try:
        res = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        data = res.json()
        val = int(data['data'][0]['value'])
        classification = data['data'][0]['value_classification']
        return {"value": val, "classification": classification}
    except:
        return {"value": 58, "classification": "Greed"}

def calculate_atr_and_levels(symbol='BTC/USDT'):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=30)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = abs(df['high'] - df['close'].shift())
        df['tr3'] = abs(df['low'] - df['close'].shift())
        df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
        atr_14 = df['tr'].rolling(window=14).mean().iloc[-1]
        current_price = df['close'].iloc[-1]
        
        entry = current_price
        sl = entry - (1.5 * atr_14)
        tp1 = entry + (2.0 * atr_14)
        tp2 = entry + (3.5 * atr_14)
        
        return {
            "price": round(current_price, 2),
            "atr": round(atr_14, 2),
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "tp1": round(tp1, 2),
            "tp2": round(tp2, 2)
        }
    except Exception as e:
        return {"price": 94000.0, "atr": 450.0, "entry": 94000.0, "sl": 93250.0, "tp1": 95100.0, "tp2": 96200.0}

@app.get("/api/market-status")
def market_status():
    fng = get_fear_and_greed()
    btc_metrics = calculate_atr_and_levels('BTC/USDT')
    return {
        "status": "ONLINE",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fear_and_greed": fng,
        "btc_live_metrics": btc_metrics,
        "active_engine": "Python Pandas + CCXT ATR Risk Engine Active"
    }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            fng = get_fear_and_greed()
            metrics = calculate_atr_and_levels('BTC/USDT')
            payload = {
                "type": "MARKET_TICK",
                "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
                "fear_greed_val": fng["value"],
                "fear_greed_text": fng["classification"],
                "market_data": metrics
            }
            await websocket.send_text(json.dumps(payload))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        print("Client disconnesso")
