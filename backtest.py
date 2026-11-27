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
B_ = {"thr": 2.0, "mr": 1, "rej": 1, "rej_k": 1.0, "t1_max": 0, "obst": 0, "fb": 0}
VARIANTS = {"+ rejet 1": {**B_, "ev": 0, "ev_sl": 0, "cpi_tilt": 0},
            "+ calendrier (pause avant/après CPI et Fed)": {**B_, "ev": 1, "ev_pre": 18, "ev_post": 2, "ev_sl": 0, "cpi_tilt": 0},
            "+ calendrier + après-CPI baissier": {**B_, "ev": 1, "ev_pre": 18, "ev_post": 2, "ev_sl": 0, "cpi_tilt": 0.4},
            "+ après-CPI baissier": {**B_, "ev": 0, "ev_sl": 0, "cpi_tilt": 0.4}}
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


def ev_study():
    """Comportement réel des marchés autour des CPI et des décisions de la Fed sur la période testée."""
    def cls_of(c):
        return "crypto" if ":" not in c else ("indice/matière" if c.split(":")[-1] in trader.NOT_STOCK else "action")
    mean = lambda L: round(sum(L) / len(L) * 100, 2) if L else None
    out = {}
    for kind in ("CPI", "FOMC"):
        evs = [t for t, k in trader.EVENTS if k == kind and span[0] + 4 * D < t < span[1] - 4 * D]
        R = {"n": len(evs)}
        for cl_ in ("crypto", "action", "indice/matière"):
            a24, b24, r72, b72, p24, bp24 = [], [], [], [], [], []
            for c, (h1, d1) in DATA.items():
                if cls_of(c) != cl_:
                    continue
                ts = [k["t"] for k in h1]; cl = [k["c"] for k in h1]
                for i in range(24, len(h1) - 72, 24):
                    b24.append(abs(cl[i + 24] / cl[i] - 1)); b72.append(cl[i + 72] / cl[i] - 1); bp24.append(cl[i] / cl[i - 24] - 1)
                for t in evs:
                    i = bisect.bisect_left(ts, t) - 1
                    if i < 24 or i + 72 >= len(h1):
                        continue
                    a24.append(abs(cl[i + 24] / cl[i] - 1)); r72.append(cl[i + 72] / cl[i] - 1); p24.append(cl[i] / cl[i - 24] - 1)
            R[cl_] = {"mouv24": mean(a24), "mouv24_normal": mean(b24), "apres72": mean(r72), "apres72_normal": mean(b72),
                      "avant24": mean(p24), "avant24_normal": mean(bp24), "cas": len(a24)}
        out[kind] = R
    return out


EVS = ev_study() if DATA else {}
print("évènements", EVS)


def lvl_study():
    """Supports et résistances testés au moins 3 fois : quand le prix vient les toucher, cèdent-ils ou tiennent-ils ?
    Contact : le prix arrive à 0,1 ATR 4 h du niveau pendant l'horizon d'une projection (3 jours crypto, 4 jours actions),
    sans s'être d'abord éloigné de 1,5 ATR. Ensuite, dans les 24 h : « cède » = clôture 1 h à plus d'un demi-ATR de
    l'autre côté ; « tient » = rebond d'un ATR depuis le niveau ; sinon indécis (non compté dans les pourcentages).
    « avec » = niveau visité dans le sens de la tendance de fond (support en tendance baissière, résistance en haussière)."""
    def cls_of(c):
        return "crypto" if ":" not in c else ("indice/matière" if c.split(":")[-1] in trader.NOT_STOCK else "action")
    out = {}
    for c, (h1, d1) in DATA.items():
        cl_, seen, hz = cls_of(c), set(), (72 if ":" not in c else 96)
        for i in range(24 * 25, len(h1) - hz - 26, 8):
            c1h = h1[max(0, i - 503):i + 1]
            c4 = trader.agg(c1h, 4)
            d = daily_upto(d1, h1, i)
            if len(c4) < 60 or len(d) < 60:
                continue
            px = h1[i]["c"]
            a4 = trader.atr(c4)[-1]
            if not a4:
                continue
            sup, res = trader.levels(c4, d, px, a4)
            cl = [k["c"] for k in d]
            e50 = trader.ema(cl, 50)[-1]
            e200 = trader.ema(cl, 200)[-1] if len(cl) >= 200 else None
            tdj = 1 if px > e50 and (e200 is None or e50 > e200) else -1 if px < e50 and (e200 is None or e50 < e200) else 0
            for side, L, sg in (("sup", sup, 1), ("res", res, -1)):
                z = next((z for z in L if z["n"] >= 3 and 0.3 * a4 <= (px - z["p"]) * sg <= 3 * a4), None)
                if not z:
                    continue
                S, j = z["p"], None
                for k in range(i + 1, min(len(h1), i + 1 + hz)):
                    kk = h1[k]
                    if (kk["l"] <= S + 0.1 * a4) if sg > 0 else (kk["h"] >= S - 0.1 * a4):
                        j = k
                        break
                    if (kk["h"] >= px + 1.5 * a4) if sg > 0 else (kk["l"] <= px - 1.5 * a4):
                        break
                if j is None or (side, j) in seen:
                    continue
                seen.add((side, j))
                o = "und"
                for k in range(j, min(len(h1), j + 25)):
                    kk = h1[k]
                    if (kk["c"] < S - 0.5 * a4) if sg > 0 else (kk["c"] > S + 0.5 * a4):
                        o = "brk"
                        break
                    if k > j and ((kk["h"] >= S + a4) if sg > 0 else (kk["l"] <= S - a4)):
                        o = "hold"
                        break
                cx = "avec" if tdj == -sg else "contre" if tdj == sg else "neutre"
                for key in (cl_, "tous"):
                    for cx_ in (cx, "tous"):
                        e_ = out.setdefault(key, {}).setdefault(side, {}).setdefault(cx_, {"brk": 0, "hold": 0, "und": 0})
                        e_[o] += 1
    for sides in out.values():
        for ctxs in sides.values():
            for e_ in ctxs.values():
                n = e_["brk"] + e_["hold"]
                e_.update(n=n, p_brk=round(e_["brk"] / n, 3) if n else None, p_hold=round(e_["hold"] / n, 3) if n else None)
    return out


try:
    LVS = lvl_study() if DATA else {}
except Exception as e:
    LVS = {"err": str(e)[:100]}
print("niveaux", json.dumps((LVS.get("tous") or {}), ensure_ascii=False)[:600], f"{time.time() - T0:.0f}s", flush=True)
ok_ = [k for k in RES if (RES[k]["stats"]["all"].get("trades") or 0) >= 300] or list(RES)  # assez de trades pour ne pas choisir sur du bruit
# la pause autour des publications protège des sauts de prix que ce test ne sait pas mesurer (il suppose un stop toujours
# exécuté au bon prix) : à résultat presque égal (écart de moins de 0,015 R), on la préfère par prudence
best = max(ok_, key=lambda k: ((RES[k]["stats"]["all"].get("avgR") or -9) + (0.015 if RES[k]["par"].get("ev") else 0), RES[k]["stats"]["all"].get("trades") or 0)) if RES else None
json.dump({"at": int(time.time() * 1000), "from": span[0], "to": span[1], "coins": list(DATA), "step_h": STEP_H, "variants": RES, "best": best, "chosen": best, "events": EVS, "levels": LVS},
          open("data/backtest.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("ok", f"{time.time() - T0:.0f}s")
