import os
from flask import Flask, jsonify
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

app = Flask(__name__)

# Configurazione Watchlist e Parametri
WATCHLIST = ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA", "AMZN", "META", "AMD"]

def esegui_scansione_reale():
    segnali_attivi = []
    for ticker in WATCHLIST:
        try:
            stock = yf.Ticker(ticker)
            df_daily = stock.history(period="6mo", interval="1d")
            df_intraday = stock.history(period="1mo", interval="60m")
            
            if df_daily.empty or df_intraday.empty:
                continue
                
            # Calcolo RSI base
            delta = df_daily['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df_daily['RSI'] = 100 - (100 / (1 + rs))
            
            current_price = df_daily['Close'].iloc[-1]
            rsi_oggi = df_daily['RSI'].iloc[-1]
            volume_oggi = df_daily['Volume'].iloc[-1]
            volume_medio = df_daily['Volume'].rolling(window=20).mean().iloc[-1]
            
            # Condizione di Timing (es. Volume alto e RSI in zona di rimbalzo)
            if volume_oggi > (volume_medio * 1.2) and rsi_oggi < 45:
                segnali_attivi.append({
                    "ticker": ticker,
                    "prezzo": round(current_price, 2),
                    "rsi": round(rsi_oggi, 2),
                    "volume_relativo": round(volume_oggi / volume_medio, 2),
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                })
        except Exception as e:
            print(f"Errore su {ticker}: {e}")
            
    return segnali_attivi

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "message": "Scanner di Trading Reale attivo su Render!",
        "endpoint_scansione": "/scan"
    })

@app.route("/scan")
def scan():
    risultati = esegui_scansione_reale()
    return jsonify({
        "totale_segnali": len(risultati),
        "timestamp_scansione": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "segnali": risultati
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
