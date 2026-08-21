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

def get_binance_klines(symbol, interval="1h", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        closes = [float(candle[4]) for candle in data]
        highs = [float(candle[2]) for candle in data]
        lows = [float(candle[3]) for candle in data]
        return closes, highs, lows
    except Exception as e:
        return [], [], []

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
    return 100 - (100 / (1 + rs))

def calculate_ema(closes, period=50):
    if len(closes) < period:
        return closes[-1]
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return (highs[-1] - lows[-1])
    tr_list = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr_list.append(max(hl, hc, lc))
    return sum(tr_list[-period:]) / period

# Memoria per evitare di inviare troppi avvisi ripetuti sulla stessa coin
alerted_coins = {}

def scan_market(is_report_cycle=False):
    print("💎 [ELITE SCAN] Analisi di precisione quantitativa in corso...")
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url)
        tickers = response.json()
        
        active_coins = [
            item for item in tickers 
            if item['symbol'].endswith('USDT') and float(item['quoteVolume']) > 25000000
        ]
        
        watchlist_summary = []
        current_time = time.time()

        for coin in active_coins:
            symbol = coin['symbol']
            price = float(coin['lastPrice'])
            
            closes, highs, lows = get_binance_klines(symbol, interval="1h", limit=50)
            if not closes:
                continue
            
            rsi = calculate_rsi(closes)
            ema50 = calculate_ema(closes, 50)
            atr = calculate_atr(highs, lows, closes, 14)
            
            # 1. PRE-ALLERTA: Se l'RSI scende sotto 35 (ma è sopra 32), la moneta sta per scattare!
            if 32 <= rsi < 35 and price > ema50:
                # Controlliamo se non abbiamo già mandato un avviso per questa coin nelle ultime 3 ore
                if symbol not in alerted_coins or (current_time - alerted_coins[symbol]) > 10800:
                    alert_msg = (
                        f"⚡ **PRE-ALERT WATCHLIST** ⚡\n\n"
                        f"🪙 **Asset:** `{symbol}`\n"
                        f"👀 *Stato:* L'asset si sta avvicinando a un'area di forte ipervenduto.\n"
                        f"📊 **RSI(14):** `{rsi:.1f}` (In discesa verso il target di acquisto)\n"
                        f"💡 *Preparate i fondi, il bot sta monitorando il trigger di ingresso!*"
                    )
                    send_telegram_message(alert_msg)
                    alerted_coins[symbol] = current_time
                    print(f"⚠️ Pre-allerta inviata per {symbol} (RSI: {rsi:.1f})")

            if rsi < 40:
                watchlist_summary.append(f"• `{symbol}`: RSI `{rsi:.1f}`")

            # 2. SEGNALE OPERATIVO DI ECCELLENZA: RSI < 32 + Prezzo > EMA 50
            if rsi < 32 and price > ema50:
                entry = price
                tp1_val = entry + (atr * 1.5)
                tp2_val = entry + (atr * 3.0)
                tp3_val = entry + (atr * 4.5)
                sl_val  = entry - (atr * 1.2)
                
                p_tp1 = ((tp1_val - entry) / entry) * 100
                p_tp2 = ((tp2_val - entry) / entry) * 100
                p_tp3 = ((tp3_val - entry) / entry) * 100
                p_sl  = ((entry - sl_val) / entry) * 100
                
                message = (
                    f"🏆 **ELITE VIP CRYPTO SIGNAL** 🏆\n\n"
                    f"🪙 **Asset:** `{symbol}`\n"
                    f"📈 **Direzione:** `LONG (Buy Setup)`\n"
                    f"⚡ **Leva Consigliata:** `10x - 20x`\n\n"
                    f"📍 **Entry Zone:** `{entry:.4f}`\n\n"
                    f"🎯 **TP 1:** `{tp1_val:.4f}` `(+{p_tp1:.2f}%)`\n"
                    f"🎯 **TP 2:** `{tp2_val:.4f}` `(+{p_tp2:.2f}%)`\n"
                    f"🎯 **TP 3:** `{tp3_val:.4f}` `(+{p_tp3:.2f}%)`\n"
                    f"🛑 **Stop Loss:** `{sl_val:.4f}` `(-{p_sl:.2f}%)`\n\n"
                    f"📊 *Metriche Quant:* RSI(14): `{rsi:.1f}` | Trend EMA50: `Confermato`"
                )
                
                send_telegram_message(message)
                print(f"🚀 SEGNALE D'ÉLITE INVIATO PER {symbol}!")
                time.sleep(1200)

        # Report periodico orario
        if is_report_cycle and watchlist_summary:
            top_watchlist = "\n".join(watchlist_summary[:6])
            report_msg = (
                f"📊 **MARKET UPDATE & WATCHLIST** 📊\n\n"
                f"Il bot sta monitorando oltre {len(active_coins)} asset H24.\n"
                f"Asset vicini alle zone di accumulo:\n\n"
                f"{top_watchlist}\n\n"
                f"⏳ *In attesa dei prossimi trigger operativi.*"
            )
            send_telegram_message(report_msg)
            print("📢 Report di mercato inviato su Telegram.")

    except Exception as e:
        print(f"Errore durante l'analisi: {e}")

if __name__ == "__main__":
    print("🤖 Bot Sala Segnali con Pre-Allerte e Monitoraggio avviato H24...")
    counter = 0
    while True:
        # Incrementiamo il contatore ad ogni ciclo
        counter += 1
        
        # Se il contatore arriva a 3, forziamo il report e resettiamo
        is_report = (counter >= 3)
        
        scan_market(is_report_cycle=is_report)
        
        if is_report:
            counter = 0
            
        time.sleep(600) # Pausa di 10 minuti
