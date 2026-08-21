import os
import time
import requests

TELEGRAM_TOKEN = os.getenv("BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("CHANNEL_ID")

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

def get_data(symbol, interval="15m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            closes = [float(entry[4]) for entry in data]
            highs = [float(entry[2]) for entry in data]
            lows = [float(entry[3]) for entry in data]
            return closes, highs, lows
    except Exception as e:
        print(f"Errore recupero klines per {symbol}: {e}")
    return [], [], []

def get_indicators(closes, highs, lows):
    if len(closes) < 50:
        return 50, 0, 0, 0, 0, 0
        
    delta = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in delta]
    losses = [-d if d < 0 else 0 for d in delta]
    
    avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else 0.001
    avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else 0.001
    
    if avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
    
    ema20 = sum(closes[-20:]) / 20
    ema50 = sum(closes[-50:]) / 50
    
    sma20 = sum(closes[-20:]) / 20
    variance = sum([(x - sma20) ** 2 for x in closes[-20:]]) / 20
    std = variance ** 0.5
    
    bb_upper = sma20 + (std * 2)
    bb_lower = sma20 - (std * 2)
    
    tr_list = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(-14, 0)]
    atr = sum(tr_list) / len(tr_list) if tr_list else 0.0
    
    return rsi, ema20, ema50, bb_upper, bb_lower, atr

def scan_market(is_report_cycle=False):
    print("💎 [PRO SCAN] Scansione di mercato avviata...")
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        response = requests.get(url)
        tickers = response.json()
        if not isinstance(tickers, list):
            return
            
        active_coins = [c for c in tickers if c['symbol'].endswith('USDT') and float(c['quoteVolume']) > 25000000]
        signals_found = 0
        results = []

        for coin in active_coins[:30]:
            symbol = coin['symbol']
            c, h, l = get_data(symbol)
            if len(c) < 50: 
                continue
                
            rsi, ema20, ema50, bb_u, bb_l, atr = get_indicators(c, h, l)
            price = c[-1]
            
            results.append({'symbol': symbol, 'rsi': rsi})
            
            # --- LOGICA LONG ---
            if price <= bb_l * 1.01 and rsi < 45 and ema20 > ema50:
                signals_found += 1
                entry = price
                tp1 = entry + (atr * 1.5)
                tp2 = entry + (atr * 2.5)
                tp3 = entry + (atr * 3.5)
                sl  = entry - (atr * 1.2)
                
                p_tp1 = ((tp1 - entry) / entry) * 100
                p_tp2 = ((tp2 - entry) / entry) * 100
                p_tp3 = ((tp3 - entry) / entry) * 100
                p_sl  = ((entry - sl) / entry) * 100
                
                msg = (
                    f"🚀 **PREMIUM VIP CRYPTO SIGNAL** 🚀\n\n"
                    f"🪙 **Asset:** `{symbol}`\n"
                    f"📈 **Direzione:** `LONG (Buy)`\n"
                    f"⚡ **Leva Consigliata:** `10x - 15x`\n\n"
                    f"📍 **Entry Zone:** `{entry:.4f}`\n\n"
                    f"🎯 **TP 1:** `{tp1:.4f}` `(+{p_tp1:.1f}%)`\n"
                    f"🎯 **TP 2:** `{tp2:.4f}` `(+{p_tp2:.1f}%)`\n"
                    f"🎯 **TP 3:** `{tp3:.4f}` `(+{p_tp3:.1f}%)`\n"
                    f"🛑 **Stop Loss:** `{sl:.4f}` `(-{p_sl:.1f}%)`\n\n"
                    f"📊 **Analisi Pro:** `RSI a {rsi:.1f} | Bollinger Bounce + Trend EMA`"
                )
                send_telegram_message(msg)

            # --- LOGICA SHORT ---
            elif price >= bb_u * 0.99 and rsi > 55 and ema20 < ema50:
                signals_found += 1
                entry = price
                tp1 = entry - (atr * 1.5)
                tp2 = entry - (atr * 2.5)
                tp3 = entry - (atr * 3.5)
                sl  = entry + (atr * 1.2)
                
                p_tp1 = ((entry - tp1) / entry) * 100
                p_tp2 = ((entry - tp2) / entry) * 100
                p_tp3 = ((entry - tp3) / entry) * 100
                p_sl  = ((sl - entry) / entry) * 100
                
                msg = (
                    f"🚀 **PREMIUM VIP CRYPTO SIGNAL** 🚀\n\n"
                    f"🪙 **Asset:** `{symbol}`\n"
                    f"📉 **Direzione:** `SHORT (Sell)`\n"
                    f"⚡ **Leva Consigliata:** `10x - 15x`\n\n"
                    f"📍 **Entry Zone:** `{entry:.4f}`\n\n"
                    f"🎯 **TP 1:** `{tp1:.4f}` `(+{p_tp1:.1f}%)`\n"
                    f"🎯 **TP 2:** `{tp2:.4f}` `(+{p_tp2:.1f}%)`\n"
                    f"🎯 **TP 3:** `{tp3:.4f}` `(+{p_tp3:.1f}%)`\n"
                    f"🛑 **Stop Loss:** `{sl:.4f}` `(-{p_sl:.1f}%)`\n\n"
                    f"📊 **Analisi Pro:** `RSI a {rsi:.1f} | Bollinger Resistance + Trend EMA`"
                )
                send_telegram_message(msg)

        if is_report_cycle and results:
            avg_rsi = sum([r['rsi'] for r in results]) / len(results)
            status = "Laterale / Bilanciato"
            if avg_rsi < 45:
                status = "Inclinato verso l'Ipervenduto"
            elif avg_rsi > 55:
                status = "Inclinato verso l'Ipercomprato"
                
            report_msg = (
                f"📊 **DIAGNOSI PRO MERCATO** 📊\n\n"
                f"🔍 **Asset scansionati:** {len(results)}\n"
                f"🌡️ **Stato mercato:** `{status}` (RSI medio: `{avg_rsi:.1f}`)\n"
                f"🎯 **Segnali attivi ora:** `{signals_found}`\n\n"
                f"💡 *Il bot sta monitorando costantemente i timeframe a 15m con le Bande di Bollinger.*"
            )
            send_telegram_message(report_msg)

    except Exception as e:
        print(f"Errore nel ciclo di scansione: {e}")

if __name__ == "__main__":
    print("🤖 Bot Pro-Adaptive avviato H24...")
    while True:
        scan_market(is_report_cycle=True)
        time.sleep(600)  # Pausa di 10 minuti
