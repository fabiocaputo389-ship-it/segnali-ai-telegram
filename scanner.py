import ccxt
import pandas as pd
import pandas_ta as ta

def run_market_scanner(symbols=['BTC/USDT', 'ETH/USDT', 'UNI/USDT'], timeframe='1h'):
    """
    Scansiona le criptovalute e restituisce i segnali di mercato reali.
    """
    exchange = ccxt.binance({
        'enableRateLimit': True
    })
    
    signals = []

    for symbol in symbols:
        try:
            # Scarica i dati storici recenti dall'exchange
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
            df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            
            # Calcola indicatori tecnici (RSI e EMA)
            df['RSI'] = ta.rsi(df['close'], length=14)
            df['EMA_50'] = ta.ema(df['close'], length=50)
            
            latest = df.iloc[-2]
            current_price = latest['close']
            rsi_value = latest['RSI']
            ema_50 = latest['EMA_50']
            
            # Logica dei segnali in base ai valori reali del mercato
            if rsi_value < 35 and current_price > ema_50:
                recommendation = "STRONG BUY"
                trend = "Bullish"
                score = 85
            elif rsi_value > 65:
                recommendation = "STRONG SELL"
                trend = "Bearish"
                score = 40
            else:
                recommendation = "NEUTRAL"
                trend = "Sideways"
                score = 50

            if recommendation in ["STRONG BUY", "STRONG SELL"]:
                signals.append({
                    'symbol': symbol,
                    'price': current_price,
                    'rsi': round(rsi_value, 2),
                    'recommendation': recommendation,
                    'score': score,
                    'trend': trend
                })
        except Exception as e:
            print(f"Errore su {symbol}: {e}")
            
    return signals
  
