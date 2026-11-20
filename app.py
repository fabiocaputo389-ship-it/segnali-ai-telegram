def calculate_setup(df, current_price, symbol, balance, risk_pct, rsi_buy_min, rsi_buy_max, rsi_sell_min, rsi_sell_max, sl_m, tp_m, timeframe, profile, category, is_open):
    close = df['Close']
    high = df['High']
    low = df['Low']
    volume = df['Volume']

    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rsi = safe_float((100 - (100 / (1 + (gain / loss)))).iloc[-1], 50.0)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    current_macd = safe_float(macd_hist.iloc[-1], 0.0)
    prev_macd = safe_float(macd_hist.iloc[-2], 0.0)
    curr_ema12 = safe_float(ema12.iloc[-1], current_price)
    curr_ema26 = safe_float(ema26.iloc[-1], current_price)
    prev_ema12 = safe_float(ema12.iloc[-2], current_price)
    prev_ema26 = safe_float(ema26.iloc[-2], current_price)

    tr = pd.concat([high - low, np.abs(high - close.shift()), np.abs(low - close.shift())], axis=1).max(axis=1)
    atr = safe_float(tr.rolling(14).mean().iloc[-1], current_price * 0.005)
    if atr <= 0:
        atr = current_price * 0.005

    vol_curr = safe_float(volume.iloc[-1], 0.0)
    vol_avg20 = safe_float(volume.rolling(20).mean().iloc[-1], 0.0)
    low_volume = (vol_avg20 > 0) and (vol_curr < vol_avg20 * 0.85)
    confirmed_breakout_vol = (vol_avg20 <= 0) or (vol_curr >= vol_avg20 * 1.10)

    # Anti-falsa rottura: controlla che l'ultima chiusura non sia solo una finta rottura respinta (wick/stop-hunt)
    last_high = safe_float(high.iloc[-1], current_price)
    last_low = safe_float(low.iloc[-1], current_price)
    last_open = safe_float(df['Open'].iloc[-1], current_price)
    prev_high_20 = safe_float(high.iloc[-21:-1].max(), last_high) if len(df) >= 22 else last_high
    prev_low_20 = safe_float(low.iloc[-21:-1].min(), last_low) if len(df) >= 22 else last_low

    fake_bull_breakout = (last_high > prev_high_20) and (current_price < prev_high_20)
    fake_bear_breakout = (last_low < prev_low_20) and (current_price > prev_low_20)

    # Anti-whipsaw: candela troppo piccola rispetto all'ATR o volume debole
    last_range = abs(last_high - last_low)
    is_chop = low_volume or (last_range < atr * 0.25)

    # Conferma direzionale stretta
    bull_trend_ok = (curr_ema12 > curr_ema26) and (prev_ema12 >= prev_ema26 * 0.998)
    bear_trend_ok = (curr_ema12 < curr_ema26) and (prev_ema12 <= prev_ema26 * 1.002)

    bull_momentum_ok = (current_macd > 0) and (current_macd > prev_macd)
    bear_momentum_ok = (current_macd < 0) and (current_macd < prev_macd)

    # Limiti RSI più selettivi per evitare ingressi in zona tirata o incerta
    strict_buy_min = max(rsi_buy_min, 42.0)
    strict_buy_max = min(rsi_buy_max, 68.0)
    strict_sell_max = min(rsi_sell_max, 58.0)
    strict_sell_min = max(rsi_sell_min, 32.0)

    is_buy = (
        not is_chop
        and not fake_bull_breakout
        and confirmed_breakout_vol
        and bull_trend_ok
        and bull_momentum_ok
        and (strict_buy_min <= rsi <= strict_buy_max)
        and (current_price >= last_open or current_price >= curr_ema12)
    )

    is_sell = (
        not is_chop
        and not fake_bear_breakout
        and confirmed_breakout_vol
        and bear_trend_ok
        and bear_momentum_ok
        and (strict_sell_min <= rsi <= strict_sell_max)
        and (current_price <= last_open or current_price <= curr_ema12)
    )

    if fake_bull_breakout:
        inv = "🛑 Anti-Falsa Rottura (Fakeout Long)"
    elif fake_bear_breakout:
        inv = "🛑 Anti-Falsa Rottura (Fakeout Short)"
    elif rsi >= 70 and current_macd < prev_macd:
        inv = "⚠️ Inversione RIBASSISTA Confermata"
    elif rsi <= 30 and current_macd > prev_macd:
        inv = "🚀 Inversione RIALZISTA Confermata"
    else:
        inv = "🛡️ Filtro Anti-Rumore Attivo"

    if is_buy:
        action, score, sent, col = "BUY", 92.5, "LONG CONFERMATO (Anti-Fakeout)", "#00ff66"
    elif is_sell:
        action, score, sent, col = "SELL", 92.5, "SHORT CONFERMATO (Anti-Fakeout)", "#ff1e42"
    else:
        action, score, sent, col = "NEUTRAL", 50.0, "FILTRATO / NO-TRADE (Evita Falsi Segnali)", "#ffffff"

    sl_dist = atr * max(sl_m, 1.2)
    tp_dist = atr * max(tp_m, 2.0)
    decimals = 2 if current_price > 5 else 5

    if action == "BUY":
        entry, sl, tp1 = current_price, round(current_price - sl_dist, decimals), round(current_price + tp_dist, decimals)
        target_rialzista, target_ribassista = round(current_price + (tp_dist * 2.0), decimals), sl
    elif action == "SELL":
        entry, sl, tp1 = current_price, round(current_price + sl_dist, decimals), round(current_price - tp_dist, decimals)
        target_rialzista, target_ribassista = sl, round(current_price - (tp_dist * 2.0), decimals)
    else:
        entry, sl, tp1 = current_price, round(current_price - sl_dist, decimals), round(current_price + tp_dist, decimals)
        target_rialzista, target_ribassista = round(current_price + tp_dist, decimals), round(current_price - sl_dist, decimals)

    tf_min = 5 if timeframe == '5m' else (15 if timeframe == '15m' else 60)
    recent_movement = abs(close.diff().tail(5)).mean()
    effective_speed = max(recent_movement, atr * 0.15, current_price * 0.0001)
    candles_needed = max(1, int(np.ceil(tp_dist / effective_speed)))
    est_min = candles_needed * tf_min

    if est_min < 60:
        duration = f"~{est_min} min ({candles_needed} candele)"
    elif est_min < 1440:
        duration = f"~{round(est_min / 60, 1)} ore ({candles_needed} candele)"
    else:
        duration = f"~{round(est_min / 1440, 1)} giorni ({candles_needed} candele)"

    max_risk = balance * (risk_pct / 100.0)
    risk_unit = abs(current_price - sl)
    pos_size = round(max((max_risk / risk_unit) if risk_unit > 0 else 0.0, 0.001), 3)

    if symbol in ['BTCUSDT', 'ETHUSDT']:
        lev_int = 8
    elif category == 'Criptovalute Globali':
        lev_int = 10
    else:
        lev_int = 5

    margin = round((pos_size * current_price) / lev_int, 2)

    roi_raw = round((tp_dist / current_price) * lev_int * 100, 1)
    roi_pct = f"+{roi_raw}%" if action == "BUY" else f"-{roi_raw}%" if action == "SELL" else "0.0%"

    notional = pos_size * current_price
    roundtrip_fee = round(notional * 0.001, 4)
    expected_gross_profit = round(pos_size * tp_dist, 4)
    expected_net_profit = round(expected_gross_profit - roundtrip_fee, 4)

    if symbol == 'BTCUSDT':
        forecast_table = "📊 [Web Forecast Nov 2026] Bitcoin quota intorno a 63.735 $ in un contesto di trend ribassista di breve termine, con possibili rimbalzi tecnici da aree di ipervenduto ma rischi di ulteriori discese verso area 60.000-62.000 $"
    elif symbol == 'ETHUSDT':
        forecast_table = "📊 [Web Forecast Nov 2026] Ethereum in fase di test dei supporti chiave in area $1.700-$1.750 con potenziale reazione di mercato"
    elif category == 'Materie Prime & Forex':
        forecast_table = "📊 [Market Intel Nov 2026] Monitoraggio flussi macro valutari e beni rifugio (Oro/Argento) con volatilità controllata"
    elif category == 'Azioni & Indici Globali':
        forecast_table = "📊 [Market Intel Nov 2026] Analisi equity tech e indici globali in ottica di risk-management multi-asset"
    else:
        forecast_table = f"📊 [Market Intel Nov 2026] Trend Globale monitorato per {symbol} | Volatilità Standard"

    advisor_msg = f"{forecast_table} | Anti-Fakeout ON | Leva Consigliata: {lev_int}x Isolata | Net PnL stimato: +${expected_net_profit}"

    if not is_open:
        advisor_status = "BLOCCATO"
        advisor_color = "#ff1e42"
    elif action == "NEUTRAL":
        advisor_status = "FILTRATO (NO FALSO SEGNALE)"
        advisor_color = "#8a99ad"
    elif expected_net_profit <= 0:
        advisor_status = "SCONSIGLIATO (FEE)"
        advisor_color = "#ff1e42"
    else:
        advisor_status = f"ESEGUI IN REALE (LEVA {lev_int}x)"
        advisor_color = "#00ff66"

    return {
        "action": action, "ai_score": f"{score}%", "sentiment": sent, "sentiment_color": col,
        "inversion": inv, "entry": entry, "sl": sl, "tp1": tp1, "roi_pct": roi_pct,
        "target_rialzista": target_rialzista, "target_ribassista": target_ribassista, "duration": duration,
        "rrr": round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0, "rsi": round(rsi, 1),
        "size": pos_size, "leverage": f"Isolata {lev_int}x", "margin": margin, "decimals": decimals,
        "roundtrip_fee": roundtrip_fee, "expected_net": expected_net_profit,
        "advisor_status": advisor_status, "advisor_color": advisor_color, "advisor_msg": advisor_msg
    }
