import os
import time
import requests

# Leggiamo le variabili d'ambiente configurate su Railway
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("CHANNEL_ID")

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHANNEL_ID:
        print("Errore: Variabili d'ambiente Telegram non configurate.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Errore nell'invio a Telegram: {e}")

def get_binance_klines(symbol, interval="1h", limit=30):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        closes = [float(candle[4]) for candle in data]
        return closes
    except Exception as e:
        return []

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    gains = 0
    losses = 0
    for i in range(1, period + 1):
        change = closes[-i] - closes[-i-1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def scan_market():
    print("🔎 Scansione avanzata di tutte le crypto in corso...")
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url)
        tickers = response.json()
        
        # Filtriamo le coppie USDT con volumi consistenti
        active_coins = [
            item for item in tickers 
            if item['symbol'].endswith('USDT') and float(item['quoteVolume']) > 15000000
        ]
        
        for coin in active_coins:
            symbol = coin['symbol']
            price = float(coin['lastPrice'])
            
            # Controlliamo l'RSI su timeframe 1H
            closes = get_binance_klines(symbol, interval="1h", limit=30)
            if not closes:
                continue
            rsi = calculate_rsi(closes)
            
            # Condizione per segnale LONG (RSI in ipervenduto < 32)
            if rsi < 32:
                entry = price
                
                # Calcoli percentuali precisi basati sul prezzo di entrata
                tp1_val = entry * 1.025  # +2.5%
                tp2_val = entry * 1.050  # +5.0%
                tp3_val = entry * 1.080  # +8.0%
                sl_val  = entry * 0.980  # -2.0%
                
                leverage = "10x - 20x"
                
                message = (
                    f"🚀 **PREMIUM CRYPTO SIGNAL** 🚀\n\n"
                    f"🪙 **Asset:** `{symbol}`\n"
                    f"📈 **Direzione:** `LONG (Buy)`\n"
                    f"⚡ **Leva Consigliata:** `{leverage}`\n\n"
                    f"📍 **Entry Zone:** `{entry}`\n\n"
                    f"🎯 **TP 1:** `{tp1_val:.4f}` `(+2.5%)`\n"
                    f"🎯 **TP 2:** `{tp2_val:.4f}` `(+5.0%)`\n"
                    f"🎯 **TP 3:** `{tp3_val:.4f}` `(+8.0%)`\n"
                    f"🛑 **Stop Loss:** `{sl_val:.4f}` `(-2.0%)`\n\n"
                    f"📊 *Analisi Tecnica:* RSI(14) a `{rsi:.1f}` (Rimbalzo da ipervenduto).\n"
                    f"⚠️ *Gestisci il rischio con attenzione.*"
                )
                
                send_telegram_message(message)
                print(f"✅ Segnale perfetto inviato per {symbol}!")
                
                # Pausa per evitare spam sullo stesso asset
                time.sleep(900)
                
    except Exception as e:
        print(f"Errore durante la scansione: {e}")

if __name__ == "__main__":
    print("🤖 Bot Sala Segnali Perfetto avviato H24...")
    while True:
        scan_market()
        # Scansiona il mercato ogni 10 minuti
        time.sleep(600)
        
