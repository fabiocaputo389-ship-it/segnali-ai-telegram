import os
import time
import requests

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
        volumes = [float(candle[5]) for candle in data]
        return closes, volumes
    except Exception as e:
        return [], []

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
    print("🔎 [MONITORAGGIO H24] Scansione di tutte le crypto in corso...")
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url)
        tickers = response.json()
        
        # Selezioniamo solo le crypto con volumi importanti per evitare manipolazioni (scam coins)
        active_coins = [
            item for item in tickers 
            if item['symbol'].endswith('USDT') and float(item['quoteVolume']) > 20000000
        ]
        
        print(f"📊 Monitoraggio attivo su {len(active_coins)} asset principali.")

        for coin in active_coins:
            symbol = coin['symbol']
            price = float(coin['lastPrice'])
            price_change_24h = float(coin['priceChangePercent'])
            
            # Scarichiamo le candele per RSI e volumi
            closes, volumes = get_binance_klines(symbol, interval="1h", limit=30)
            if not closes or not volumes:
                continue
            
            rsi = calculate_rsi(closes)
            
            # Mostra nei log lo stato di monitoraggio per capire chi sta per scattare
            if rsi < 35 or rsi > 65:
                print(f"👀 [In Watchlist] {symbol} -> RSI: {rsi:.1f} | 24h: {price_change_24h}%")

            # CONDIZIONE LONG PERFETTA: RSI in forte ipervenduto (< 30) + Trend o spinta in corso
            if rsi < 30:
                entry = price
                
                # Calcolo percentuali millimetriche
                tp1_val = entry * 1.020  # +2.0%
                tp2_val = entry * 1.040  # +4.0%
                tp3_val = entry * 1.070  # +7.0%
                sl_val  = entry * 0.985  # -1.5% (Stop loss stretto per proteggere il capitale)
                
                leverage = "10x - 15x"
                
                message = (
                    f"🚀 **PREMIUM VIP CRYPTO SIGNAL** 🚀\n\n"
                    f"🪙 **Asset:** `{symbol}`\n"
                    f"📈 **Direzione:** `LONG (Buy)`\n"
                    f"⚡ **Leva Consigliata:** `{leverage}`\n\n"
                    f"📍 **Entry Zone:** `{entry}`\n\n"
                    f"🎯 **TP 1:** `{tp1_val:.4f}` `(+2.0%)`\n"
                    f"🎯 **TP 2:** `{tp2_val:.4f}` `(+4.0%)`\n"
                    f"🎯 **TP 3:** `{tp3_val:.4f}` `(+7.0%)`\n"
                    f"🛑 **Stop Loss:** `{sl_val:.4f}` `(-1.5%)`\n\n"
                    f"📊 *Dati di Analisi:* RSI(14) a `{rsi:.1f}` | Variazione 24h: `{price_change_24h}%`\n"
                    f"💡 *Setup ad alta probabilità di rimbalzo tecnico.*"
                )
                
                send_telegram_message(message)
                print(f"✅ SEGNALE PERFETTO INVIATO PER {symbol} (RSI: {rsi:.1f})!")
                
                # Pausa strategica per non saturare il canale
                time.sleep(1200)
                
    except Exception as e:
        print(f"Errore durante la scansione di mercato: {e}")

if __name__ == "__main__":
    print("🤖 Bot Sala Segnali di Precisione avviato H24...")
    while True:
        scan_market()
        # Ciclo di controllo ogni 10 minuti
        time.sleep(600)
        
