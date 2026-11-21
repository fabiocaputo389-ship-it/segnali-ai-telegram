def compute_real_profit_metrics(
    score_pct: float,
    entry: float,
    sl: float,
    tp1: float,
    risk_usd: float,
    false_breakout: bool,
    fee_rate: float = 0.0006,
    notional_usd: float = 0.0,
) -> dict:
    """Calcola probabilità di profitto netto, EV reale, R:R e frazione di Kelly."""
    reward_dist = abs(tp1 - entry)
    risk_dist = abs(entry - sl)
    if risk_dist <= 0 or reward_dist <= 0 or entry <= 0:
        return {
            "rrr": 0.0,
            "p_win_pct": 0.0,
            "profit_certainty_pct": 0.0,
            "ev_net_usd": 0.0,
            "kelly_pct": 0.0,
        }

    rrr = reward_dist / risk_dist
    # Probabilità implicita calibrata da score tecnico e penalizzata da eventuale fakeout
    raw_p = 0.35 + (max(0.0, min(100.0, score_pct)) / 100.0) * 0.38
    if false_breakout:
        raw_p *= 0.72
    p_win = max(0.05, min(0.88, raw_p))

    fee_cost_usd = notional_usd * fee_rate * 2.0
    gross_win_usd = risk_usd * rrr
    ev_gross = (p_win * gross_win_usd) - ((1.0 - p_win) * risk_usd)
    ev_net = ev_gross - fee_cost_usd

    # Certezza di profitto netta: combina p_win, rapporto R:R e segno/entità dell'EV netto
    edge_factor = max(0.0, min(1.0, 0.5 + (ev_net / max(risk_usd, 1e-6)) * 0.35))
    rr_factor = max(0.0, min(1.0, rrr / 2.5))
    certainty_pct = round(
        max(5.0, min(95.0, (p_win * 0.55 + edge_factor * 0.25 + rr_factor * 0.20) * 100.0)),
        1,
    )

    # Kelly frazionario (quarter-Kelly per prudenza operativa)
    b = max(0.1, rrr)
    full_kelly = max(0.0, p_win - ((1.0 - p_win) / b))
    kelly_pct = round(full_kelly * 0.25 * 100.0, 2)

    return {
        "rrr": round(rrr, 2),
        "p_win_pct": round(p_win * 100.0, 1),
        "profit_certainty_pct": certainty_pct,
        "ev_net_usd": round(ev_net, 4),
        "kelly_pct": kelly_pct,
    }
