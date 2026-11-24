def calcola_parametri_quantistici_unificati(simbolo, market_type, style="Scalping", margin_usdt=2.0, leverage=10):
    fetch_type = "FUTURES" if market_type == "STANDARD_FUTURES" else market_type
    try:
        tfs = {iv: analizza_tf(simbolo, fetch_type, iv) for iv in ("5m", "15m", "1h", "4h")}
    except Exception:
        tfs = None

    if not tfs or any(v is None for v in tfs.values()):
        return None

    style_base_tf = {
        "Scalping": "5m",
        "Day Trading": "15m",
        "Swing Trading": "1h",
        "Long Term": "4h",
    }.get(style, "15m")

    trend_5m = tfs["5m"]["trend"]
    trend_15m = tfs["15m"]["trend"]
    trend_1h = tfs["1h"]["trend"]
    trend_4h = tfs["4h"]["trend"]

    # Richiede allineamento reale tra 1h e 4h (no forzatura RSI quando 1h e 4h divergono o sono neutri)
    if trend_1h == trend_4h and trend_1h in ("BUY", "SELL"):
        side = trend_1h
        if trend_5m == trend_15m == side:
            score_precisione = 100.0
            favorabilita = 88.0
        elif trend_15m == side or trend_5m == side:
            score_precisione = 84.0
            favorabilita = 80.0
        else:
            score_precisione = 76.0
            favorabilita = 75.0
    else:
        side = "NEUTRO"
        score_precisione = 50.0
        favorabilita = 50.0

    certainty = _calcola_certezza_reale(tfs, side if side in ("BUY", "SELL") else "BUY", style_base_tf) if side in ("BUY", "SELL") else 50.0
    base = tfs[style_base_tf]
    price = tfs["5m"]["last"]["close"]
    atr_reale = base["atr14"] if base["atr14"] > 0 else (price * 0.01)
    dec = 2 if price >= 1000 else 3 if price >= 10 else 4 if price >= 1 else 5 if price >= 0.01 else 7

    if side == "BUY":
        min_recente = min(base["lows"][-5:])
        sl = round(min(min_recente, price - (atr_reale * 1.8)), dec)
        rischio_unit = max(price - sl, price * 0.003)
        tp1 = round(price + (rischio_unit * 1.5), dec)
        tp2 = round(price + (rischio_unit * 2.1), dec)
        tp3 = round(price + (rischio_unit * 3.2), dec)
        reward_unit = max(tp2 - price, rischio_unit * 1.5)

        counter_side = "SELL"
        counter_sl = round(price + (atr_reale * 1.8), dec)
        counter_tp = round(price - (rischio_unit * 1.5), dec)
        counter_desc = (
            f"La vendita sarebbe contro il trend 1h/4h rialzista ({price}) "
            f"e quindi un setup a bassa probabilità e più rischioso."
        )
    elif side == "SELL":
        max_recente = max(base["highs"][-5:])
        sl = round(max(max_recente, price + (atr_reale * 1.8)), dec)
        rischio_unit = max(sl - price, price * 0.003)
        tp1 = round(price - (rischio_unit * 1.5), dec)
        tp2 = round(price - (rischio_unit * 2.1), dec)
        tp3 = round(price - (rischio_unit * 3.2), dec)
        reward_unit = max(price - tp2, rischio_unit * 1.5)

        counter_side = "BUY"
        counter_sl = round(price - (atr_reale * 1.8), dec)
        counter_tp = round(price + (rischio_unit * 1.5), dec)
        counter_desc = (
            f"L'acquisto sarebbe contro il trend 1h/4h ribassista ({price}) "
            f"e quindi un setup a bassa probabilità e più rischioso."
        )
    else:
        sl = round(price - (atr_reale * 1.8), dec)
        tp1 = round(price + (atr_reale * 1.5), dec)
        tp2 = round(price + (atr_reale * 2.1), dec)
        tp3 = round(price + (atr_reale * 3.2), dec)
        rischio_unit = max(price - sl, price * 0.003)
        reward_unit = max(tp2 - price, rischio_unit * 1.5)

        counter_side = "NEUTRO"
        counter_sl = sl
        counter_tp = tp1
        counter_desc = (
            "Timeframe 1h e 4h non allineati o in fase neutra: attendere conferma strutturale "
            "prima di aprire posizioni direzionali."
        )

    eff_margin = _normalizza_importo_minimo(margin_usdt)
    eff_lev = _calcola_leva_dinamica(eff_margin, leverage, market_type)
    notional = eff_margin * eff_lev
    raw_qty = (notional / price) if price > 0 else 0.0
    size_qty = float(_formatta_quantita_contratto(raw_qty, price))

    rr_ratio = round(reward_unit / rischio_unit, 2) if rischio_unit > 0 else 1.5
    p_win = min(0.95, max(0.51, certainty / 100.0)) if side != "NEUTRO" else 0.50
    p_loss = 1.0 - p_win
    payoff_win = notional * (reward_unit / price)
    payoff_loss = notional * (rischio_unit / price)
    ev_usd = round((p_win * payoff_win) - (p_loss * payoff_loss), 4) if side != "NEUTRO" else 0.0
    kelly_pct = round(max(1.0, min(25.0, ((p_win * rr_ratio - p_loss) / max(rr_ratio, 0.1)) * 100.0)), 1) if side != "NEUTRO" else 0.0

    candele_eta = max(2, min(8, int(round((rr_ratio * 1.6)))))
    minuti_eta = candele_eta * 15
    target_eta_ts = int(time.time()) + (minuti_eta * 60)
    target_eta_utc = datetime.fromtimestamp(target_eta_ts, tz=timezone.utc).strftime("%H:%M UTC")

    entry_zone_min = round(price - (atr_reale * 0.2), dec)
    entry_zone_max = round(price + (atr_reale * 0.2), dec)

    candle_sec = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}.get(style_base_tf, 900)
    ultima_candela = base["last"]
    velocita_reale = max(atr_reale * 0.25 / candle_sec, price * 0.000015)
    dist_tp_val = abs(tp2 - price)
    tempo_esatto_sec = int(max(60, min(14400, round(dist_tp_val / velocita_reale))))
    timestamp_ultima_candela = int(ultima_candela["time"])

    category, display_label = _classifica_categoria_e_label(simbolo, market_type)
    candle_window = _calcola_finestra_candela(style_base_tf)
    is_safe, time_msg, borsa_rif, start_t, end_t = verifica_finestra_temporale_sicura()
    momento_giusto = is_safe and (side != "NEUTRO")

    return {
        "symbol": simbolo,
        "displayLabel": display_label,
        "category": category,
        "marketType": market_type,
        "style": style,
        "price": round(price, dec),
        "side": side,
        "htfBias": "BULLISH" if side == "BUY" else ("BEARISH" if side == "SELL" else "NEUTRAL"),
        "size": size_qty,
        "leverage": eff_lev,
        "marginUsdt": round(eff_margin, 2),
        "rr": rr_ratio,
        "evUsd": ev_usd,
        "kellyPct": kelly_pct,
        "certaintyPct": certainty,
        "etaMinutes": minuti_eta,
        "etaCandles": candele_eta,
        "etaUtc": target_eta_utc,
        "candle15m": candle_window,
        "entryZoneMin": entry_zone_min,
        "entryZoneMax": entry_zone_max,
        "sentiment": f"Filtro 1h/4h Attivo ({side})" if side != "NEUTRO" else "In attesa di allineamento 1h/4h (NEUTRO)",
        "favorability": favorabilita,
        "confluenza": f"{time_msg}",
        "borsaRif": borsa_rif,
        "momentoGiusto": momento_giusto,
        "exactTf": style_base_tf,
        "precisionScore": score_precisione,
        "ema9": round(base["ema9"], dec),
        "ema21": round(base["ema21"], dec),
        "rsi14": base["rsi14"],
        "atr14": round(atr_reale, dec),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "tp": tp2,
        "counterTrade": {
            "side": counter_side,
            "entry": round(price, dec),
            "sl": counter_sl,
            "tp": counter_tp,
            "description": counter_desc,
        },
        "decimals": dec,
        "tempoSec": tempo_esatto_sec,
        "candleTime": timestamp_ultima_candela,
        "tfs": tfs,
        "isSafeTime": is_safe,
        "sessionStart": start_t,
        "sessionEnd": end_t,
    }
