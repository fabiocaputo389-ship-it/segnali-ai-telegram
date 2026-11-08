def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "15m / 1h", market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti. Inserisci le tue chiavi personali."}

    # Distinguiamo nettamente tra Perpetual, Standard Futures e Spot
    if market_type == "STANDARD_FUTURES":
        # Endpoint ufficiale BingX per Futures Standard (Standard Futures Trade)
        endpoint = "/openApi/futures/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita),
            "leverage": str(leva),
            "timestamp": str(int(time.time() * 1000))
        }
    elif market_type == "FUTURES":
        imposta_leva_bingx(simbolo, leva, lato, active_api, active_secret)
        endpoint = "/openApi/swap/v2/trade/order"
        position_side = "LONG" if lato == "BUY" else "SHORT"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }
    else:  # SPOT
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }

    url = BASE_URL + endpoint
    query_string = _genera_firma(payload_ordine, active_secret)
    headers = {"X-BX-APIKEY": active_api}

    try:
        response = requests.post(f"{url}?{query_string}", headers=headers)
        risultato = response.json()

        # Gestione ordini protettivi TP/SL solo per Perpetual Futures
        if risultato.get("code") == 0 and market_type == "FUTURES":
            time.sleep(0.8)
            lato_chiusura = "SELL" if lato == "BUY" else "BUY"
            position_side = "LONG" if lato == "BUY" else "SHORT"
            if prezzo_sl > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_sl, "STOP_MARKET", position_side, active_api, active_secret)
            if prezzo_tp > 0:
                _imposta_ordine_protettivo(simbolo, lato_chiusura, prezzo_tp, "TAKE_PROFIT_MARKET", position_side, active_api, active_secret)

        return risultato
    except Exception as e:
        return {"code": -1, "msg": str(e)}
