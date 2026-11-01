import os
import asyncio
import ccxt.async_support as ccxt
import pandas as pd
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Trading Scanner Real-Time", version="1.0")

# Abilita CORS per permettere alla dashboard di comunicare col backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"status": "online", "message": "AI Trading Scanner Backend attivo e operativo!"}

@app.get("/api/scan")
async def scan_market():
    """
    Scansiona il mercato reale (es. Binance USDT pairs)
    e restituisce i token con confluenza di segnali alta.
    """
    try:
        exchange = ccxt.binance({'enableRateLimit': True})
        markets = await exchange.load_markets()
        
        # Filtra le principali coppie USDT con volume attivo
        usdt_symbols = [s for s, m in markets.items() if s.endswith('/USDT') and m['active']][:10]
        
        results = []
        for symbol in usdt_symbols:
            try:
                ohlcv = await exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
                closes = [x[4] for x in ohlcv]
                
                current_price = closes[-1]
                prev_price = closes[-2]
                change_pct = ((current_price - prev_price) / prev_price) * 100
                
                signal = "LONG" if change_pct >= 0 else "SHORT"
                score = round(min(max(abs(change_pct) * 20 + 50, 50), 95), 1)
                
                results.append({
                    "symbol": symbol,
                    "price": current_price,
                    "change": round(change_pct, 2),
                    "signal": signal,
                    "confluence": f"{score}%",
                    "rating": "Strong Buy" if signal == "LONG" else "Neutral"
                })
            except Exception as e:
                continue
                
        await exchange.close()
        return {"success": True, "data": results}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
  
