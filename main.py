import os
import time
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_message(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Credenziali Telegram non configurate.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code != 200:
            print(f"Errore Telegram: {response.text}")
    except Exception as e:
        print(f"Errore di connessione a Telegram: {e}")

def get_binance_klines(symbol, interval="1h", limit=50):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        if isinstance(data, list):
            closes = [float(entry[4]) for entry in data]
            highs = [float(entry[2]) for entry in data]
            lows = [float(entry[3]) for entry in data]
            return closes, highs, lows
    except Exception as e:
        print(f"Errore nel recupero klines per {symbol}: {e}")
    return [], [], []

def calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_ema(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema

def calculate_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i-1])
        lc = abs(lows[i] - closes[i-1])
        tr = max(hl, hc, lc)
        tr_list.append(tr)
    if len(tr_list) < period:
        return sum(tr_list) / len(tr_list) if tr_list else 0.0
    atr = sum(tr_list[:period]) / period
    return atr

def scan_market(is_report_cycle=False):
    print("💎 [ELITE SCAN] Analisi Dual-Mode (Long/Short) in corso...")
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url)
        tickers = response.json()
        active_coins = [item for item in tickers if item['symbol'].endswith('USDT') and float(item['quoteVolume']) > 25000000]
        
        watchlist_summary = []

        for coin in active_coins[:30]:
            symbol = coin['symbol']
            price = float(coin['lastPrice'])
            closes, highs, lows = get_binance_klines(symbol, interval="1h", limit=50)
            if not closes: continue
            
            rsi = calculate_rsi(closes)
            ema50 = calculate_ema(closes, 50)
            atr = calculate_atr(highs, lows, closes, 14)
            
            watchlist_summary.append({'symbol': symbol, 'rsi': rsi})

            # --- LOGICA LONG ---
            if rsi < 35 and price > ema50:
                entry = price
                tp1_val = entry + (atr * 1.5)
                sl_val  = entry - (atr * 1.2)
                message = f"🏆 **ELITE VIP LONG** 🏆\n\n🪙 {symbol}\n📈 Direzione: LONG\n📍 Entry: `{entry:.4f}`\n🎯 TP1: `{tp1_val:.4f}`\n🛑 SL: `{sl_val:.4f}`"
                send_telegram_message(message)

            # --- LOGICA SHORT ---
            elif rsi > 70 and price < ema50:
                entry = price
                tp1_val = entry - (atr * 1.5)
                sl_val  = entry + (atr * 1.2)
                message = f"🔻 **ELITE VIP SHORT** 🔻\n\n🪙 {symbol}\n📉 Direzione: SHORT\n📍 Entry: `{entry:.4f}`\n🎯 TP1: `{tp1_val:.4f}`\n🛑 SL: `{sl_val:.4f}`"
                send_telegram_message(message)

        if is_report_cycle:
            if watchlist_summary:
                watchlist_summary = sorted(watchlist_summary, key=lambda k: k['rsi'])
                top_watchlist = "\n".join([f"• `{item['symbol']}` - RSI: {item['rsi']:.1f}" for item in watchlist_summary[:6]])
                report_msg = (
                    f"📊 **MARKET UPDATE & WATCHLIST** 📊\n\n"
                    f"Il bot sta monitorando gli asset H24.\n"
                    f"Asset più vicini alle zone di accumulo/vendita:\n\n"
                    f"{top_watchlist}\n\n"
                    f"⏳ *In attesa di setup ideali.*"
                )
            else:
                report_msg = (
                    f"📊 **MARKET UPDATE** 📊\n\n"
                    f"Il bot ha scansionato il mercato.\n"
                    f"Status: *Mercato in fase di transizione.* \n"
                    f"Monitoraggio H24 attivo. 🔍"
                )
            send_telegram_message(report_msg)
            print("📢 Report di mercato inviato su Telegram.")

    except Exception as e:
        print(f"Errore durante l'analisi: {e}")

if __name__ == "__main__":
    print("🤖 Bot Sala Segnali con Pre-Allerte e Monitoraggio avviato H24...")
    counter = 0
    while True:
        counter += 1
        is_report = (counter >= 3)
        scan_market(is_report_cycle=is_report)
        if is_report:
            counter = 0
        time.sleep(600)
        
