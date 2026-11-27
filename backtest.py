"""Test historique des projections : rejoue le moteur trader.py heure par heure sur ~7 mois de bougies Hyperliquid,
avec exactement les mêmes règles de suivi qu'en direct (entrée, stop, cibles, échéance, remplacement).

Sans regard vers le futur : à chaque point, seules les bougies déjà terminées sont utilisées.
Les données sans historique gratuit (funding, open interest, carnet, sentiment, analystes) sont absentes du test :
il mesure la partie graphique du moteur. Résultat : data/backtest.json.
"""
import json, time, sys, bisect, datetime as dt
import trader

COINS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "BNB", "SUI", "LINK", "AVAX",
         "xyz:NVDA", "xyz:AAPL", "xyz:MSFT", "xyz:AMZN", "xyz:GOOGL", "xyz:META", "xyz:TSLA", "xyz:AMD", "xyz:PLTR",
         "xyz:COIN", "xyz:MSTR", "xyz:GOLD", "xyz:SILVER", "xyz:CL", "xyz:SP500"]
STEP_H = 4
VARIANTS = {"tendance + repli 1,5": {"thr": 1.5, "mr": 1}, "tendance + repli 2": {"thr": 2.0, "mr": 1},
            "niveaux 0,6": {"thr": 2.0, "pb_min": 0.6, "mr": 0}}
H, D = 3600000, 86400000
T0 = time.time()


def fetch(coin, interval, n):
    end = int(time.time() * 1000)
    step = H if interval == "1h" else D
    for k in range(3):
        try:
            d = trader.post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": end - n * step, "endTime": end}}, timeout=40)
            cs = [{"t": int(x["t"]), "o": float(x["o"]), "h": float(x["h"]), "l": float(x["l"]), "c": float(x["c"]), "v": float(x.get("v") or 0)} for x in d]
            for ts, r in trader.SPLITS.get(coin, []):
                for x in cs:
                    if x["t"] < ts:
                        for f in ("o", "h", "l", "c"):
                            x[f] /= r
                        x["v"] *= r
            return cs
        except Exception as e:
            print("  retry", coin, interval, e)
            time.sleep(15)
    return []


DATA = {}
for c in COINS:
    h1 = fetch(c, "1h", 5000); time.sleep(6)
    d1 = fetch(c, "1d", 700); time.sleep(3)
    if len(h1) > 800 and len(d1) > 80:
        DATA[c] = (h1, d1)
    print(c, len(h1), len(d1), flush=True)


def daily_upto(d1, h1, i):
    """Bougies jour connues à l'heure i : jours terminés + jour en cours reconstruit avec les bougies 1 h."""
    now = h1[i]["t"] + H
    day0 = (now - 1) // D * D
    ts = [k["t"] for k in d1]
    j = bisect.bisect_left(ts, day0)
    hs = [k for k in h1[max(0, i - 30):i + 1] if k["t"] >= day0]
    out = d1[max(0, j - 240):j]
    if hs:
        out = out + [{"t": day0, "o": hs[0]["o"], "h": max(k["h"] for k in hs), "l": min(k["l"] for k in hs), "c": hs[-1]["c"], "v": sum(k["v"] for k in hs)}]
    return out


def bench_at(coin, t):
    if coin not in DATA:
        return None
    h1, d1 = DATA[coin]
    ts = [k["t"] for k in h1]
    i = bisect.bisect_right(ts, t - H) - 1
    if i < 0:
        return None
    d = daily_upto(d1, h1, i)
    cl = [k["c"] for k in d]
    if len(cl) < 55:
        return None
    e50 = trader.ema(cl, 50)[-1]
    e200 = trader.ema(cl, 200)[-1] if len(cl) >= 200 else None
    px = cl[-1]
    tr_ = 1 if px > e50 and (e200 is None or e50 > e200) else -1 if px < e50 and (e200 is None or e50 < e200) else 0
    return tr_, ((cl[-1] / cl[-21] - 1) * 100 if len(cl) > 21 else None)


RES = {}
for vname, par in VARIANTS.items():
    trader.PARAM.update(par)
    recs = []
    for coin, (h1, d1) in DATA.items():
        crypto = ":" not in coin
        cls = "crypto" if crypto else ("indice/matière" if coin.split(":")[-1] in trader.NOT_STOCK else "action")
        op = None
        start = 24 * 25
        for i in range(start, len(h1) - 1):
            if (h1[i]["t"] // H) % STEP_H:
                continue
            now = h1[i]["t"] + H + 5 * 60000  # juste après la clôture de la bougie i
            c1h = h1[max(0, i - 503):i + 1]
            d = daily_upto(d1, h1, i)
            b = bench_at("BTC" if crypto else "xyz:SP500", now) if coin not in ("BTC", "xyz:SP500") else None
            bench = (b[0], "le bitcoin" if crypto else "le S&P 500", b[1]) if b else None
            a = trader.analyze(coin, c1h, d, {}, bench, {"crypto": crypto})
            if not a:
                continue
            op, fin = trader.track(op, a, c1h, now, coin, cls)
            recs += fin
        # direction à l'échéance pour les projections closes avant leur horizon
        for r in recs:
            if r.get("coin") == coin and r.get("dir_ok") is None and not r.get("dir_done"):
                ts = [k["t"] for k in h1]
                j = bisect.bisect_right(ts, r["t0"] + r["hz"] * H) + 1
                trader.dir_fill(r, h1[max(0, j - 200):j])
    st = trader.stats(recs)
    RES[vname] = {"par": par, "stats": st, "n_rec": len(recs),
                  "recent": [{k: r.get(k) for k in ("coin", "t0", "bias", "setup", "conf", "status", "r", "dir_ok")} for r in sorted(recs, key=lambda r: -r["t0"])[:12]]}
    a_ = st["all"]
    print(vname, a_, f"{time.time() - T0:.0f}s", flush=True)

span = [min(v[0][0]["t"] for v in DATA.values()), max(v[0][-1]["t"] for v in DATA.values())] if DATA else [0, 0]
best = max(RES, key=lambda k: ((RES[k]["stats"]["all"].get("avgR") or -9), RES[k]["stats"]["all"].get("trades") or 0)) if RES else None
json.dump({"at": int(time.time() * 1000), "from": span[0], "to": span[1], "coins": list(DATA), "step_h": STEP_H, "variants": RES, "best": best, "chosen": best},
          open("data/backtest.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("ok", f"{time.time() - T0:.0f}s")
