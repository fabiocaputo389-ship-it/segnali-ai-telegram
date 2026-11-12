def esegui_ordine_bingx(simbolo: str, lato: str, quantita: float, prezzo_ingresso: float, prezzo_tp: float, prezzo_sl: float, leva: int = 3, timeframe: str = "15m", market_type: str = "FUTURES", api_key: str = "", secret_key: str = ""):
    active_api = api_key if api_key else DEFAULT_API_KEY
    active_secret = secret_key if secret_key else DEFAULT_SECRET_KEY

    if not active_api or not active_secret:
        return {"code": -1, "msg": "API Key o Secret Key BingX mancanti."}

    # Se la quantità è troppo bassa (es. con 2$), forziamo un controllo di sicurezza sul lotto minimo
    if quantita <= 0:
        quantita = 0.001

    if market_type == "STANDARD_FUTURES":
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
    else:
        endpoint = "/openApi/spot/v1/trade/order"
        payload_ordine = {
            "symbol": simbolo,
            "side": lato,
            "type": "MARKET",
            "quantity": str(quantita),
            "timestamp": str(int(time.time() * 1000))
        }

    try:
        res = _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
        msg = str(res.get("msg", ""))
        
        # Se BingX rifiuta per importo minimo o margine insufficiente, tentiamo l'adattamento automatico del lotto
        if res.get("code") != 0:
            if "minimum order amount is" in msg.lower() or "is" in msg.lower():
                match = re.search(r"(?:minimum order amount is|is)\s+([0-9.]+)", msg, re.IGNORECASE)
                if match:
                    min_qty = float(match.group(1))
                    adjusted_qty = round(min_qty * 1.05, 6 if min_qty < 1 else 2)
                    payload_ordine["quantity"] = str(adjusted_qty)
                    payload_ordine["timestamp"] = str(int(time.time() * 1000))
                    res = _invia_richiesta_ordine(endpoint, payload_ordine, active_api, active_secret)
            elif "insufficient margin" in msg.lower():
                return {
                    "code": -1, 
                    "msg": "Margine insufficiente su BingX. Riduci la leva (es. a 1x o 3x) oppure ricarica il conto Futures USDT."
                }
        return res
    except Exception as e:
        return {"code": -1, "msg": str(e)}
