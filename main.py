
import os
import time
import requests

# Leggiamo i nomi esatti impostati nel tuo pannello Railway
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("CHANNEL_ID")
def send_telegram_message(text):
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

def get_binance_tickers():
    """Scarica i dati di mercato da Binance per tutte le coppie USDT"""
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url)
        data = response.json()
        # Filtriamo solo le crypto scambiate contro USDT con buon volume
        crypto_list = [
            item for item in data 
            if item['symbol'].endswith('USDT') and float(item['quoteVolume']) > 10000000
        ]
        return crypto_list
    except Exception as e:
        print(f"Errore nel recupero dati Binance: {e}")
        return []

def analyze_and_signal():
    print("Scansione del mercato crypto in corso...")
    tickers = get_binance_tickers()
    
    for coin in tickers:
        symbol = coin['symbol']
        price = float(coin['lastPrice'])
        price_change = float(coin['priceChangePercent'])
        
        # Esempio di logica di segnale: Cerchiamo movimenti di pump rapidi (> +6% nelle 24h con forti volumi)
        if price_change > 6.0:
            entry = price
            tp1 = entry * 1.035  # +3.5%
            tp2 = entry * 1.07   # +7%
            sl = entry * 0.975   # -2.5% Stop Loss
            
            # Calcolo dinamico della leva consigliata in base allo stop loss stretto
            leverage = "10x - 20x" if price_change < 10 else "5x - 10x"
            
            message = (
                f"🚀 **CRYPTO SIGNAL DETECTED** 🚀\n\n"
                f"🪙 **Asset:** `{symbol}`\n"
                f"📈 **Direzione:** `LONG`\n"
                f"⚡ **Leva Consigliata:** `{leverage}`\n\n"
                f"📍 **Entry Zone:** `{entry}`\n"
                f"🎯 **TP1:** `{tp1:.4f}`\n"
                f"🎯 **TP2:** `{tp2:.4f}`\n"
                f"🛑 **Stop Loss:** `{sl:.4f}`\n\n"
                f"📊 *Variazione 24h:* `+{price_change}%`"
            )
            
            send_telegram_message(message)
            print(f"Segnale inviato per {symbol}!")
            
            # Pausa per evitare spam sullo stesso asset
            time.sleep(300)

if __name__ == "__main__":
    print("Bot avviato e in ascolto su tutte le crypto...")
    while True:
        analyze_and_signal()
        # Controlla il mercato ogni 15 minuti
        time.sleep(900)
