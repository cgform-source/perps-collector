"""Analyse « trader » des marchés Hyperliquid et suivi de ses projections.

Pour chaque marché suivi : tendance de fond (jours), structure et momentum (4 h), niveaux clés,
volatilité, volume, dérivés (funding, open interest, prime), contexte de marché (BTC / S&P 500),
force relative, analystes et sentiment des influenceurs. Le tout donne un biais, un type de setup,
un plan (entrée, stop, cibles) et un indice de confiance.

Chaque projection est ensuite SUIVIE : entrée déclenchée ou non, cible ou stop touché en premier,
résultat en multiples du risque (R), direction juste ou fausse à l'échéance. Les statistiques
servent à calibrer la confiance des projections suivantes.
"""
import json, math, os, time, urllib.request, datetime as dt

API = "https://api.hyperliquid.xyz/info"
UA = {"User-Agent": "Mozilla/5.0 (compatible; perps-dashboard-collector/1.0)", "Content-Type": "application/json"}
SPLITS = {"xyz:SNDK": [(int(dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc).timestamp() * 1000), 8)]}
CRYPTO_MAJ = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "BNB", "SUI", "LINK", "AVAX"]
STOCKS = ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "AMD", "PLTR", "NFLX", "COIN", "MSTR", "HOOD", "ORCL",
          "AVGO", "COST", "MU", "INTC", "UBER", "SHOP", "BABA", "CRWV", "NBIS", "SNOW", "ANT", "SNDK"]
OTHERS = ["GOLD", "SILVER", "CL", "BRENTOIL", "COPPER", "NATGAS", "SP500", "XYZ100"]
NOT_STOCK = {"XYZ100", "SP500", "SILVER", "GOLD", "CL", "BRENTOIL", "COPPER", "NATGAS", "PLATINUM", "EUR", "JPY", "JP225", "KR200",
             "DXY", "URANIUM", "PALLADIUM"}


def post(body, timeout=20):
    req = urllib.request.Request(API, data=json.dumps(body).encode(), headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def candles(coin, interval, days):
    end = int(time.time() * 1000)
    d = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": end - days * 86400000, "endTime": end}})
    cs = [{"t": int(k["t"]), "o": float(k["o"]), "h": float(k["h"]), "l": float(k["l"]), "c": float(k["c"]), "v": float(k.get("v") or 0)} for k in d]
    for ts, r in SPLITS.get(coin, []):
        for k in cs:
            if k["t"] < ts:
                for f in ("o", "h", "l", "c"):
                    k[f] /= r
                k["v"] *= r
    return cs


def agg(cs, hours):
    """Regroupe des bougies 1 h en bougies de N heures (alignées sur l'heure UTC)."""
    out, cur, key = [], None, None
    span = hours * 3600000
    for k in cs:
        b = k["t"] // span
        if b != key:
            if cur:
                out.append(cur)
            key, cur = b, dict(k)
            cur["t"] = b * span
        else:
            cur["h"] = max(cur["h"], k["h"]); cur["l"] = min(cur["l"], k["l"]); cur["c"] = k["c"]; cur["v"] += k["v"]
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------- indicateurs
def ema(xs, n):
    if not xs:
        return []
    a, out, e = 2 / (n + 1), [], xs[0]
    for x in xs:
        e = x * a + e * (1 - a)
        out.append(e)
    return out


def rsi(cl, n=14):
    if len(cl) <= n:
        return [50.0] * len(cl)
    g = l = 0.0
    for i in range(1, n + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    out = [50.0] * (n + 1)
    for i in range(n + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
        out.append(100.0 if l == 0 else 100 - 100 / (1 + g / l))
    return out


def tr(cs, i):
    return max(cs[i]["h"] - cs[i]["l"], abs(cs[i]["h"] - cs[i - 1]["c"]), abs(cs[i]["l"] - cs[i - 1]["c"]))


def atr(cs, n=14):
    if len(cs) <= n:
        return [cs[-1]["h"] - cs[-1]["l"] if cs else 0]
    a = sum(tr(cs, i) for i in range(1, n + 1)) / n
    out = [a]
    for i in range(n + 1, len(cs)):
        a = (a * (n - 1) + tr(cs, i)) / n
        out.append(a)
    return out


def adx(cs, n=14):
    """ADX de Wilder : force de la tendance (>25 tendance nette, <18 marché sans direction)."""
    if len(cs) < 2 * n + 2:
        return 20.0, 0.0, 0.0
    pdm = mdm = trs = 0.0
    dxs = []
    pdi = mdi = 0.0
    for i in range(1, len(cs)):
        up = cs[i]["h"] - cs[i - 1]["h"]; dn = cs[i - 1]["l"] - cs[i]["l"]
        p = up if up > dn and up > 0 else 0.0
        m = dn if dn > up and dn > 0 else 0.0
        t = tr(cs, i)
        if i <= n:
            pdm += p; mdm += m; trs += t
            continue
        pdm = pdm - pdm / n + p; mdm = mdm - mdm / n + m; trs = trs - trs / n + t
        if trs <= 0:
            continue
        pdi, mdi = 100 * pdm / trs, 100 * mdm / trs
        dxs.append(100 * abs(pdi - mdi) / max(pdi + mdi, 1e-9))
    if len(dxs) < n:
        return 20.0, pdi, mdi
    a = sum(dxs[:n]) / n
    for x in dxs[n:]:
        a = (a * (n - 1) + x) / n
    return a, pdi, mdi


def macd(cl):
    e12, e26 = ema(cl, 12), ema(cl, 26)
    line = [a - b for a, b in zip(e12, e26)]
    sig = ema(line, 9)
    return [a - b for a, b in zip(line, sig)]


def boll(cl, n=20, k=2.0):
    out = []
    for i in range(len(cl)):
        w = cl[max(0, i - n + 1):i + 1]
        m = sum(w) / len(w)
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / len(w))
        out.append((m, m + k * sd, m - k * sd, (2 * k * sd) / m if m else 0))
    return out


def swings(cs, w=2):
    s = []
    for i in range(w, len(cs) - w):
        if all(cs[i]["h"] >= cs[j]["h"] for j in range(i - w, i + w + 1)):
            s.append((i, "H", cs[i]["h"]))
        if all(cs[i]["l"] <= cs[j]["l"] for j in range(i - w, i + w + 1)):
            s.append((i, "L", cs[i]["l"]))
    return s


def levels(c4, c1d, px, a4):
    """Supports / résistances : creux et sommets 4 h et journaliers regroupés ; plus un niveau a été testé, plus il compte."""
    pts = [(p, 1.0) for _, _, p in swings(c4[-180:], 2)] + [(p, 1.6) for _, _, p in swings(c1d[-150:], 2)]
    pts.sort()
    cl = []
    tol = max(a4 * 0.45, px * 0.002)
    for p, w in pts:
        if cl and p - cl[-1]["v"] / cl[-1]["w"] <= tol:
            cl[-1]["v"] += p * w; cl[-1]["w"] += w; cl[-1]["n"] += 1
        else:
            cl.append({"v": p * w, "w": w, "n": 1})
    lv = [{"p": z["v"] / z["w"], "s": z["w"], "n": z["n"]} for z in cl if z["w"] >= 1.6]
    sup = sorted([z for z in lv if z["p"] < px * 0.999], key=lambda z: -z["p"])[:3]
    res = sorted([z for z in lv if z["p"] > px * 1.001], key=lambda z: z["p"])[:3]
    return sup, res


def pctile(xs, x):
    if not xs:
        return 50
    return 100 * sum(1 for v in xs if v <= x) / len(xs)


def fmt(v):
    if v is None:
        return "—"
    a = abs(v)
    s = f"{v:,.1f}" if a >= 1000 else f"{v:.2f}" if a >= 10 else f"{v:.3f}" if a >= 1 else f"{v:.4g}"
    return s.replace(",", " ").replace(".", ",")


# ---------------------------------------------------------------- analyse
def analyze(coin, c1h, c1d, ctx, bench, extra):
    """Retourne la projection d'un marché, ou None si l'historique est trop court."""
    if len(c1h) < 200 or len(c1d) < 60:
        return None
    c4 = agg(c1h, 4)
    px = ctx.get("mark") or c1h[-1]["c"]
    cl4, cld = [k["c"] for k in c4], [k["c"] for k in c1d]
    a4, ad = atr(c4)[-1], atr(c1d)[-1]
    e20d, e50d, e200d = ema(cld, 20)[-1], ema(cld, 50)[-1], (ema(cld, 200)[-1] if len(cld) >= 200 else None)
    e20h4, e50h4 = ema(cl4, 20)[-1], ema(cl4, 50)[-1]
    adxd, pdid, mdid = adx(c1d)
    adx4, pdi4, mdi4 = adx(c4)
    r4 = rsi(cl4); rd = rsi(cld)
    mh = macd(cl4)
    bb = boll(cl4)
    bw = [b[3] for b in bb[-120:]]
    sw = swings(c4[-120:], 2)
    H = [s for s in sw if s[1] == "H"]; L = [s for s in sw if s[1] == "L"]
    sup, res = levels(c4, c1d, px, a4)
    sig = []
    cat = {}

    def add(c, v, txt):
        cat[c] = cat.get(c, 0) + v
        sig.append(["bull" if v > 0 else "bear" if v < 0 else "mix", round(v, 2), txt, c])

    # 1. tendance de fond (journalier)
    if e200d:
        if px > e50d > e200d:
            add("tendance", 1.5, f"Tendance de fond haussière : prix au-dessus des moyennes 50 et 200 jours ({fmt(e50d)} / {fmt(e200d)})")
        elif px < e50d < e200d:
            add("tendance", -1.5, f"Tendance de fond baissière : prix sous les moyennes 50 et 200 jours ({fmt(e50d)} / {fmt(e200d)})")
        elif px > e200d:
            add("tendance", 0.5, f"Au-dessus de la moyenne 200 jours ({fmt(e200d)}) mais tendance moyenne terme hésitante")
        else:
            add("tendance", -0.5, f"Sous la moyenne 200 jours ({fmt(e200d)}) malgré des rebonds")
    else:
        add("tendance", 1.0 if px > e50d else -1.0, f"Prix {'au-dessus' if px > e50d else 'en dessous'} de sa moyenne 50 jours ({fmt(e50d)})")
    if adxd >= 25:
        d = 1 if pdid > mdid else -1
        add("tendance", 0.6 * d, f"ADX journalier {adxd:.0f} : tendance {'haussière' if d > 0 else 'baissière'} solide")
    elif adxd < 18:
        sig.append(["mix", 0, f"ADX journalier {adxd:.0f} : pas de tendance claire, le marché oscille", "tendance"])
    # 2. structure 4 h
    struct = "range"
    if len(H) >= 2 and len(L) >= 2:
        hh, hl = H[-1][2] > H[-2][2], L[-1][2] > L[-2][2]
        lh, ll = H[-1][2] < H[-2][2], L[-1][2] < L[-2][2]
        if hh and hl:
            struct = "up"; add("structure", 1.0, "Structure 4 h : sommets et creux de plus en plus hauts")
        elif lh and ll:
            struct = "down"; add("structure", -1.0, "Structure 4 h : sommets et creux de plus en plus bas")
        else:
            sig.append(["mix", 0, "Structure 4 h sans direction (sommets et creux mélangés)", "structure"])
    bos = 0
    if H and cl4[-1] > H[-1][2] and H[-1][0] < len(c4[-120:]) - 2:
        bos = 1; add("structure", 0.6, f"Cassure du dernier sommet 4 h ({fmt(H[-1][2])}) : les acheteurs prennent la main")
    if L and cl4[-1] < L[-1][2] and L[-1][0] < len(c4[-120:]) - 2:
        bos = -1; add("structure", -0.6, f"Cassure du dernier creux 4 h ({fmt(L[-1][2])}) : les vendeurs prennent la main")
    # 3. momentum
    if len(mh) > 3:
        if mh[-1] > 0 and mh[-1] > mh[-2]:
            add("momentum", 0.6, "MACD 4 h positif et en accélération")
        elif mh[-1] < 0 and mh[-1] < mh[-2]:
            add("momentum", -0.6, "MACD 4 h négatif et en accélération")
        elif mh[-1] > 0 > mh[-3]:
            add("momentum", 0.4, "MACD 4 h vient de repasser positif")
        elif mh[-1] < 0 < mh[-3]:
            add("momentum", -0.4, "MACD 4 h vient de repasser négatif")
    r = r4[-1]
    strong_up, strong_dn = struct == "up" and adx4 >= 25, struct == "down" and adx4 >= 25
    if r >= 75 and not strong_up:
        add("momentum", -0.6, f"RSI 4 h {r:.0f} : suracheté, l'élan risque de s'essouffler")
    elif r <= 25 and not strong_dn:
        add("momentum", 0.6, f"RSI 4 h {r:.0f} : survendu, un rebond technique est fréquent")
    elif 50 <= r < 70:
        add("momentum", 0.3, f"RSI 4 h {r:.0f} : élan acheteur sain")
    elif 30 < r < 50:
        add("momentum", -0.3, f"RSI 4 h {r:.0f} : élan vendeur")
    # divergences RSI sur les deux derniers sommets / creux 4 h
    off = len(c4) - len(c4[-120:])
    if len(H) >= 2 and H[-1][2] > H[-2][2] and r4[off + H[-1][0]] < r4[off + H[-2][0]] - 3:
        add("momentum", -0.7, "Divergence baissière : nouveau sommet de prix mais RSI plus faible")
    if len(L) >= 2 and L[-1][2] < L[-2][2] and r4[off + L[-1][0]] > r4[off + L[-2][0]] + 3:
        add("momentum", 0.7, "Divergence haussière : nouveau creux de prix mais RSI plus fort")
    # 4. emplacement (où est le prix par rapport aux niveaux)
    ds = (px - sup[0]["p"]) / a4 if sup else 9
    dr = (res[0]["p"] - px) / a4 if res else 9
    if ds < 0.8 and sup:
        add("emplacement", 0.7, f"Collé à un support testé {sup[0]['n']} fois ({fmt(sup[0]['p'])})")
    if dr < 0.8 and res:
        add("emplacement", -0.7, f"Collé à une résistance testée {res[0]['n']} fois ({fmt(res[0]['p'])})")
    # 5. volatilité et volume
    sq = pctile(bw, bw[-1]) if bw else 50
    if sq <= 15:
        sig.append(["mix", 0, "Bandes de Bollinger 4 h très resserrées : un mouvement fort se prépare", "volatilité"])
    vols = [k["v"] for k in c1d[-21:-1]]
    rv = (c1d[-1]["v"] / (sum(vols) / len(vols))) if vols and sum(vols) > 0 else 1
    day = c1d[-1]["c"] - c1d[-1]["o"]
    if rv >= 1.6:
        add("volume", 0.5 if day > 0 else -0.5, f"Volume du jour {rv:.1f}× la moyenne, sur une séance {'haussière' if day > 0 else 'baissière'}")
    obv, ob = 0.0, []
    for i in range(1, len(c4)):
        obv += c4[i]["v"] if c4[i]["c"] > c4[i - 1]["c"] else -c4[i]["v"] if c4[i]["c"] < c4[i - 1]["c"] else 0
        ob.append(obv)
    if len(ob) > 30:
        o20 = ob[-1] - ob[-21]
        p20 = cl4[-1] - cl4[-21]
        if o20 > 0 and p20 < 0:
            add("volume", 0.4, "Le volume accompagne les hausses mieux que les baisses (accumulation discrète)")
        elif o20 < 0 and p20 > 0:
            add("volume", -0.4, "La hausse se fait sans volume acheteur (distribution possible)")
    # 6. dérivés Hyperliquid
    f = ctx.get("fund")
    if f is not None:
        an = f * 24 * 365 * 100
        if an > 40:
            add("dérivés", -0.5, f"Funding {an:.0f} %/an : trop de longs, risque de purge")
        elif an < -15:
            add("dérivés", 0.5, f"Funding {an:.0f} %/an : beaucoup de shorts, risque de short squeeze")
    if ctx.get("mark") and ctx.get("orc"):
        pr = (ctx["mark"] / ctx["orc"] - 1) * 100
        if abs(pr) >= 0.3:
            add("dérivés", -0.3 if pr > 0 else 0.3, f"Le perp cote {pr:+.2f} % par rapport au prix de référence")
    oi = extra.get("oi_chg")
    if oi:
        dp, doi = oi
        if dp > 1 and doi > 3:
            add("dérivés", 0.4, f"Hausse avec open interest +{doi:.0f} % : de l'argent frais entre à l'achat")
        elif dp < -1 and doi > 3:
            add("dérivés", -0.4, f"Baisse avec open interest +{doi:.0f} % : de nouveaux vendeurs s'installent")
        elif dp > 1 and doi < -3:
            sig.append(["mix", 0, "Hausse portée par des rachats de shorts : rallye plus fragile", "dérivés"])
    # 7. contexte de marché et force relative
    if bench:
        bt, bname, b20 = bench
        if bt != 0:
            add("contexte", 0.5 * bt, f"{bname} en tendance {'haussière' if bt > 0 else 'baissière'} : vent {'porteur' if bt > 0 else 'contraire'}")
        if len(cld) > 21 and b20 is not None:
            r20 = (cld[-1] / cld[-21] - 1) * 100
            rs = r20 - b20
            if rs > 5:
                add("contexte", 0.5, f"Plus fort que {bname} sur 20 jours ({rs:+.0f} points)")
            elif rs < -5:
                add("contexte", -0.5, f"Plus faible que {bname} sur 20 jours ({rs:+.0f} points)")
    fg = extra.get("fg")
    if fg is not None and extra.get("crypto"):
        if fg <= 20:
            add("contexte", 0.3, f"Peur extrême sur la crypto ({fg}/100) : souvent proche d'un creux")
        elif fg >= 80:
            add("contexte", -0.3, f"Avidité extrême sur la crypto ({fg}/100) : souvent proche d'un sommet")
    # 8. analystes et sentiment
    an_ = extra.get("analyst")
    if an_ and an_.get("avg"):
        up = (an_["avg"] / px - 1) * 100
        if up > 20:
            add("fondamental", 0.3, f"Objectif moyen des analystes {fmt(an_['avg'])} ({up:+.0f} %)")
        elif up < 0:
            add("fondamental", -0.3, f"Objectif moyen des analystes {fmt(an_['avg'])} sous le prix ({up:+.0f} %)")
    st_ = extra.get("senti")
    if st_ and st_[0] + st_[1] >= 3:
        b, s_ = st_
        if b >= 2 * s_:
            add("sentiment", 0.3, f"Influenceurs plutôt acheteurs ({b} haussiers / {s_} baissiers sur 24 h)")
        elif s_ >= 2 * b:
            add("sentiment", -0.3, f"Influenceurs plutôt vendeurs ({s_} baissiers / {b} haussiers sur 24 h)")
    caps = {"tendance": 2.5, "structure": 1.6, "momentum": 1.8, "emplacement": 1.0, "volume": 0.9, "dérivés": 1.0, "contexte": 1.3, "fondamental": 0.3, "sentiment": 0.3}
    score = sum(max(-caps.get(k, 1), min(caps.get(k, 1), v)) for k, v in cat.items())
    risk_flags = []
    if extra.get("closed"):
        risk_flags.append("Bourse fermée : liquidité faible, le prix peut sauter à l'ouverture")
    if extra.get("earn_days") is not None and extra["earn_days"] <= 3:
        risk_flags.append(f"Résultats dans {extra['earn_days']} jour(s) : risque de gap, pas de nouvelle position")

    # ------------------------------------------------ setup et plan
    bias = "long" if score >= 2.0 else "short" if score <= -2.0 else "neutre"
    if any("Résultats" in x for x in risk_flags):
        bias = "neutre"
    plan, setup = None, "Attendre"
    if bias != "neutre":
        lg = bias == "long"
        lv = sup if lg else res
        opp = res if lg else sup
        near = lv[0]["p"] if lv and abs(px - lv[0]["p"]) / a4 <= 2.5 else None
        brk = H[-1][2] if (lg and bos == 1 and H) else (L[-1][2] if (not lg and bos == -1 and L) else None)
        if brk and (sq <= 35 or rv >= 1.3):
            setup = "Cassure" if lg else "Cassure baissière"
            e = [brk, brk + 0.3 * a4] if lg else [brk - 0.3 * a4, brk]
        elif near is not None:
            setup = "Achat sur repli" if lg else "Vente sur rebond"
            e = [near, near + 0.3 * a4] if lg else [near - 0.3 * a4, near]
        else:
            m = e20h4 if (lg and e20h4 < px) or (not lg and e20h4 > px) else px - (0.6 * a4 if lg else -0.6 * a4)
            setup = "Achat sur repli" if lg else "Vente sur rebond"
            e = [m - 0.15 * a4, m + 0.15 * a4]
        e = sorted(e)
        if lg:
            sl = e[0] - 0.7 * a4
            if L and e[0] - L[-1][2] < 2.5 * a4 and L[-1][2] < e[0]:
                sl = min(sl, L[-1][2] - 0.25 * a4)
            risk = e[1] - sl
            if risk > 3 * a4:
                sl = e[1] - 3 * a4; risk = 3 * a4
            cands = [z["p"] for z in opp if z["p"] - e[1] >= 1.2 * risk]
            t1 = cands[0] if cands else e[1] + 1.6 * risk
            t2c = [z["p"] for z in opp if z["p"] > t1 + 0.5 * risk]
            t2 = t2c[0] if t2c else max(t1 + 0.9 * risk, e[1] + 2.6 * risk)
        else:
            sl = e[1] + 0.7 * a4
            if H and H[-1][2] - e[1] < 2.5 * a4 and H[-1][2] > e[1]:
                sl = max(sl, H[-1][2] + 0.25 * a4)
            risk = sl - e[0]
            if risk > 3 * a4:
                sl = e[0] + 3 * a4; risk = 3 * a4
            cands = [z["p"] for z in opp if e[0] - z["p"] >= 1.2 * risk]
            t1 = cands[0] if cands else e[0] - 1.6 * risk
            t2c = [z["p"] for z in opp if z["p"] < t1 - 0.5 * risk]
            t2 = t2c[0] if t2c else min(t1 - 0.9 * risk, e[0] - 2.6 * risk)
        mid = (e[0] + e[1]) / 2
        rr = abs(t1 - mid) / max(abs(mid - sl), 1e-12)
        plan = {"e": [round(e[0], 8), round(e[1], 8)], "sl": round(sl, 8), "tp": [round(t1, 8), round(t2, 8)], "rr": round(rr, 2)}
    # confiance : force et accord des signaux, puis calibrée par l'historique (ailleurs)
    dirs = [s for s in sig if s[0] != "mix"]
    agree = (sum(1 for s in dirs if (s[0] == "bull") == (score > 0)) / len(dirs)) if dirs else 0.5
    conf = 50 + 38 * math.tanh(max(0, abs(score) - 1.0) / 3) * (0.6 + 0.4 * agree)
    if risk_flags:
        conf -= 6
    conf = round(max(40, min(88, conf)))
    # texte façon trader
    good = [s[2] for s in sorted(dirs, key=lambda s: -abs(s[1])) if (s[0] == "bull") == (bias == "long")][:3] if bias != "neutre" else []
    bad = [s[2] for s in sorted(dirs, key=lambda s: -abs(s[1])) if (s[0] == "bull") != (bias == "long")][:2] if bias != "neutre" else []
    if bias == "neutre":
        band = [sup[0]["p"] if sup else px - 1.5 * a4, res[0]["p"] if res else px + 1.5 * a4]
        view = (f"ATTENDRE : les signaux se contredisent (score {score:+.1f}). Zone de range attendue entre {fmt(band[0])} et {fmt(band[1])}. "
                f"Une clôture 4 h au-dessus de {fmt(band[1])} ou sous {fmt(band[0])} donnerait la direction.")
        invalid = f"Sortie franche du range {fmt(band[0])} – {fmt(band[1])}"
    else:
        p = plan
        view = (f"{'LONG' if bias == 'long' else 'SHORT'} · {setup.lower()} : " + " ; ".join(good) + ". "
                + (f"À surveiller : {' ; '.join(bad)}. " if bad else "")
                + f"Plan : entrée {fmt(p['e'][0])} – {fmt(p['e'][1])}, stop {fmt(p['sl'])}, cibles {fmt(p['tp'][0])} puis {fmt(p['tp'][1])} (R/R {str(p['rr']).replace('.', ',')}).")
        invalid = f"Clôture 4 h {'sous' if bias == 'long' else 'au-dessus de'} {fmt(p['sl'])}"
    zone = lambda z: [round(z["p"] - 0.15 * a4, 8), round(z["p"] + 0.15 * a4, 8)]
    return {"at": int(time.time() * 1000), "px": px, "bias": bias, "setup": setup, "score": round(score, 2), "conf": conf, "plan": plan,
            "sup": [zone(z) for z in sup[:2]], "res": [zone(z) for z in res[:2]], "atr4h": a4, "atr1d": ad, "adx4": round(adx4), "adxd": round(adxd),
            "rsi4": round(r), "rsid": round(rd[-1]), "sig": [s[:3] for s in sig], "risk": risk_flags, "view": view, "invalid": invalid,
            "hz": 72 if extra.get("crypto") else 96}


# ---------------------------------------------------------------- suivi des projections
def evaluate(p, c1h, now_ms):
    """Avance l'état d'une projection ouverte avec les bougies 1 h. Retourne la projection (status mis à jour)."""
    hz_end = p["t0"] + p["hz"] * 3600000
    lg = p["bias"] == "long"
    first = (p["t0"] // 3600000 + 1) * 3600000  # la bougie en cours au moment de la projection ne compte pas (pas de regard en arrière)
    for k in c1h:
        if k["t"] < first or k["t"] + 3600000 <= p.get("last", 0):
            continue
        if k["t"] >= hz_end or k["t"] + 3600000 > now_ms:
            break  # seulement des bougies terminées
        p["last"] = k["t"] + 3600000
        if p["bias"] == "neutre":
            continue
        e0, e1 = p["e"]
        if not p.get("fill"):
            if k["l"] <= e1 and k["h"] >= e0:
                p["fill"] = min(max(k["o"], e0), e1)
                p["fill_t"] = k["t"]
            else:
                continue
        risk = abs(p["fill"] - p["sl"]) or 1e-12
        # première chose touchée : le stop (perte) ou la cible 1 (gain). Bougie qui touche les deux : on compte le stop (prudence).
        if lg:
            p["mfe"] = round(max(p.get("mfe", 0), (k["h"] - p["fill"]) / risk), 2)
            if k["l"] <= p["sl"]:
                p.update(status="stop", r=-1.0, end=k["t"], dir_ok=False); return p
            if k["h"] >= p["tp"][0]:
                big = k["h"] >= p["tp"][1]
                p.update(status="cible2" if big else "cible1", r=round(((p["tp"][1] if big else p["tp"][0]) - p["fill"]) / risk, 2), end=k["t"], dir_ok=True); return p
        else:
            p["mfe"] = round(max(p.get("mfe", 0), (p["fill"] - k["l"]) / risk), 2)
            if k["h"] >= p["sl"]:
                p.update(status="stop", r=-1.0, end=k["t"], dir_ok=False); return p
            if k["l"] <= p["tp"][0]:
                big = k["l"] <= p["tp"][1]
                p.update(status="cible2" if big else "cible1", r=round((p["fill"] - (p["tp"][1] if big else p["tp"][0])) / risk, 2), end=k["t"], dir_ok=True); return p
    if now_ms >= hz_end:
        last = None
        for k in c1h:
            if k["t"] <= hz_end:
                last = k["c"]
        last = last if last is not None else c1h[-1]["c"]
        p["px_end"] = last
        if p["bias"] == "neutre":
            lo, hi = p.get("band", [None, None])
            p.update(status="range tenu" if lo is not None and lo <= last <= hi else "range cassé", end=hz_end)
            return p
        p["dir_ok"] = None if abs(last / p["px0"] - 1) < 0.002 else (last > p["px0"]) == (p["bias"] == "long")
        if p.get("fill"):
            risk = abs(p["fill"] - p["sl"]) or 1e-12
            p.update(status="expirée", r=round(((last - p["fill"]) if lg else (p["fill"] - last)) / risk, 2), end=hz_end)
        else:
            p.update(status="non déclenchée", end=hz_end)
    return p


def stats(recs):
    def agg_(rs):
        tr_ = [r for r in rs if r.get("status") in ("stop", "cible1", "cible2", "expirée")]
        w = sum(1 for r in tr_ if r["status"] in ("cible1", "cible2"))
        l_ = sum(1 for r in tr_ if r["status"] == "stop")
        dirs = [r for r in rs if r.get("dir_ok") is not None]
        rng = [r for r in rs if r.get("status") in ("range tenu", "range cassé")]
        return {"n": len(rs), "trades": len(tr_), "win": w, "loss": l_, "wr": round(w / (w + l_), 3) if w + l_ else None,
                "avgR": round(sum(r.get("r", 0) for r in tr_) / len(tr_), 2) if tr_ else None,
                "dir": round(sum(1 for r in dirs if r["dir_ok"]) / len(dirs), 3) if dirs else None,
                "range": round(sum(1 for r in rng if r["status"] == "range tenu") / len(rng), 3) if rng else None}
    out = {"all": agg_(recs), "setup": {}, "class": {}, "conf": {}, "coin": {}}
    for key, fn in (("setup", lambda r: r.get("setup")), ("class", lambda r: r.get("cls")),
                    ("conf", lambda r: "< 55" if r.get("conf", 0) < 55 else "55–69" if r.get("conf", 0) < 70 else "70 +"),
                    ("coin", lambda r: r.get("coin"))):
        groups = {}
        for r in recs:
            groups.setdefault(fn(r), []).append(r)
        out[key] = {k: agg_(v) for k, v in groups.items() if k}
    return out
