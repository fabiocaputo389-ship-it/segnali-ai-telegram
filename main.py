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
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Errore Telegram: {e}")

def get_data(symbol, interval="15m", limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        data = requests.get(url).json()
        closes = [float(entry[4]) for entry in data]
        highs = [float(entry[2]) for entry in data]
        lows = [float(entry[3]) for entry in data]
        return closes, highs, lows
    except: return [], [], []

def get_indicators(closes, highs, lows):
    # RSI Semplificato
    delta = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in delta]
    losses = [-d if d < 0 else 0 for d in delta]
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14
    rsi = 100 - (100 / (1 + (avg_gain/avg_loss if avg_loss != 0 else 1)))
    
    # Trend (EMA 20/50)
    ema20 = sum(closes[-20:]) / 20
    ema50 = sum(closes[-50:]) / 50
    
    # Volatilità (Bollinger & ATR)
    std = (sum([(x - (sum(closes[-20:])/20))**2 for x in closes[-20:]]) / 20)**0.5
    bb_upper = (sum(closes[-20:])/20) + (std * 2)
    bb_lower = (sum(closes[-20:])/20) - (std * 2)
    atr = sum([max(highs[i]-lows[i], abs(highs[i]-closes[i-1])) for i in range(-14, 0)]) / 14
    
    return rsi, ema20, ema50, bb_upper, bb_lower, atr

def scan_market(is_report_cycle=False):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    active_coins = [c for c in requests.get(url).json() if c['symbol'].endswith('USDT')][:30]
    signals_found = 0
    results = []

    for coin in active_coins:
        c, h, l = get_data(coin['symbol'])
        if len(c) < 50: continue
        rsi, ema20, ema50, bb_u, bb_l, atr = get_indicators(c, h, l)
        price = c[-1]
        
        # LOGICA PRO-ADAPTIVE
        # Long: Prezzo vicino a BB Lower + RSI scarico + Trend 20>50
        if price <= bb_lower * 1.01 and rsi < 45 and ema20 > ema50:
            signals_found += 1
            msg = f"🚀 *PREMIUM LONG* | {coin['symbol']}\nEntry: {price:.4f}\nTP: {price + (atr*2):.4f} | SL: {price - (atr*1.5):.4f}"
            send_telegram_message(msg)
        
        # Short: Prezzo vicino a BB Upper + RSI carico + Trend 20<50
        elif price >= bb_upper * 0.99 and rsi > 55 and ema20 < ema50:
            signals_found += 1
            msg = f"🚀 *PREMIUM SHORT* | {coin['symbol']}\nEntry: {price:.4f}\nTP: {price - (atr*2):.4f} | SL: {price + (atr*1.5):.4f}"
            send_telegram_message(msg)
            
        results.append({'symbol': coin['symbol'], 'rsi': rsi})

    if is_report_cycle:
        avg_rsi = sum([r['rsi'] for r in results]) / len(results)
        status = "Laterale" if 45 < avg_rsi < 55 else ("Ipervenduto" if avg_rsi <= 45 else "Ipercomprato")
        send_telegram_message(f"📊 *DIAGNOSI PRO* | Stato: {status} (RSI: {avg_rsi:.1f}) | Segnali Attivi: {signals_found}")

# Loop principale più serrato (ogni 10 minuti)
while True:
    scan_market(is_report_cycle=True)
    time.sleep(600)
    
