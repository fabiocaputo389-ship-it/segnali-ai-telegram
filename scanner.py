import asyncio
import json
import websockets
import datetime

# URL WebSocket pubblico di BingX (Futures/Perpetual o Spot)
BINGX_WS_URL = "wss://open-api-swap.bingx.com/swap-market"

async def bingx_scanner():
    async with websockets.connect(BINGX_WS_URL) as websocket:
        # Sottoscrizione al ticker di alcune coppie principali (es. BTC-USDT, ETH-USDT, SOL-USDT)
        subscribe_msg = {
            "id": "scanner_1",
            "reqType": "sub",
            "dataType": "ticker" # oppure candele / depth
        }
        await websocket.send(json.dumps(subscribe_msg))
        print("Connesso al WebSocket di BingX in tempo reale...")

        while True:
            try:
                response = await websocket.recv()
                data = json.loads(response)
                
                # Elaborazione dati in tempo reale del mercato
                if 'data' in data:
                    ticker = data['data']
                    symbol = ticker.get('s', 'BTC-USDT')
                    price = float(ticker.get('c', 0))
                    
                    # Esempio di logica di segnale / calcolo dinamico TP e SL (basato su volatilità stimata)
                    # In produzione qui applichi la tua strategia (es. incrocio medie o RSI)
                    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    
                    # Output di log per Render
                    print(f"[{timestamp}] {symbol} - Prezzo Live: {price}")
                    
            except websockets.ConnectionClosed:
                print("Connessione WebSocket persa. Riconnessione in corso...")
                break
            except Exception as e:
                print(f"Errore: {e}")

if __name__ == "__main__":
    asyncio.run(bingx_scanner())
