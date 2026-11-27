"""Projections « trader » + suivi de leurs résultats. Lancé après collect.py ; ajoute F.proj au feed.

Fichiers : data/proj_open.json (projections en cours), data/proj_cache.json (bougies jour + historique OI),
data/proj/AAAA-MM.json (projections terminées, gardées pour les statistiques).
"""
import json, os, time, signal, urllib.request, datetime as dt
import trader


class Budget(Exception):
    """Temps alloué aux projections écoulé (distinct d'un simple délai réseau)."""


def _alarm(*a):
    raise Budget("temps dépassé")


signal.signal(signal.SIGALRM, _alarm)
signal.alarm(300)
UTC = dt.timezone.utc
_last = [0.0]


def hl(fn, *a, gap=1.5):
    """Appel Hyperliquid espacé (limite 1200 de poids par minute : ~1,5 s pour des bougies, 0,2 s pour un carnet) ; 2e essai si 429."""
    for k in range(2):
        w = gap - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()
        try:
            return fn(*a)
        except Budget:
            raise
        except Exception as e:
            if "429" in str(e) and k == 0:
                time.sleep(10)
                continue
            raise
NOW = int(time.time() * 1000)
F = json.load(open("data/feed.json", encoding="utf-8"))
ERR = {}


def jload(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def jsave(p, v):
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    json.dump(v, open(p, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


OPEN = jload("data/proj_open.json", {})
CACHE = jload("data/proj_cache.json", {"d1": {}, "oi": {}})

# --- marchés suivis : majeures crypto, actions et matières premières Hyperliquid, + la grille du dashboard
try:
    m0, c0 = hl(trader.post, {"type": "metaAndAssetCtxs"})
    mx, cx = hl(trader.post, {"type": "metaAndAssetCtxs", "dex": "xyz"})
except Exception as e:
    print("proj: Hyperliquid indisponible", e)
    raise SystemExit(0)
CTX = {}
for meta, ctxs in ((m0, c0), (mx, cx)):
    for u, c in zip(meta.get("universe", []), ctxs):
        name = u["name"]
        try:
            CTX[name] = {"mark": float(c.get("markPx") or 0) or None, "orc": float(c.get("oraclePx") or 0) or None,
                         "fund": float(c.get("funding") or 0), "oi": float(c.get("openInterest") or 0)}
        except Exception:
            pass
grid = []
try:
    rq = urllib.request.Request("https://lqefewiwrecdjommktgm.supabase.co/rest/v1/grid_prefs?id=eq.analyserxxx&select=coins",
                                headers={"apikey": "sb_publishable_IMMxd7lqThb0elf0Z2JwNQ_3Da4tdWq", "User-Agent": "perps-collector"})
    with urllib.request.urlopen(rq, timeout=10) as r:
        j = json.loads(r.read() or b"[]")
    grid = (j[0].get("coins") or []) if j else []
except Exception:
    pass
UNI = []
for c in trader.CRYPTO_MAJ + ["xyz:" + s for s in trader.STOCKS + trader.OTHERS] + [g for g in grid if isinstance(g, str)]:
    if c in CTX and c not in UNI:
        UNI.append(c)
UNI = UNI[:55]


# --- contexte de marché
def bench_of(coin):
    d1 = CACHE["d1"].get(coin)
    if not d1:
        return None
    cl = [k["c"] for k in d1]
    e50, e200 = trader.ema(cl, 50)[-1], (trader.ema(cl, 200)[-1] if len(cl) >= 200 else None)
    px = cl[-1]
    t = 1 if px > e50 and (e200 is None or e50 > e200) else -1 if px < e50 and (e200 is None or e50 < e200) else 0
    r20 = (cl[-1] / cl[-21] - 1) * 100 if len(cl) > 21 else None
    return t, r20


def daily(coin):
    """Bougies jour en cache (relues toutes les 3 h) ; la dernière est mise à jour avec les bougies 1 h."""
    d = CACHE["d1"].get(coin)
    if d and NOW - CACHE.get("d1_at", {}).get(coin, 0) < 3 * 3600000:
        return d
    d = hl(trader.candles, coin, "1d", 230)
    CACHE["d1"][coin] = d
    CACHE.setdefault("d1_at", {})[coin] = NOW
    return d


def us_closed():
    from zoneinfo import ZoneInfo
    n = dt.datetime.now(ZoneInfo("America/New_York"))
    early = {"2026-11-27", "2026-12-24", "2027-11-26"}  # séances écourtées : fermeture à 13 h
    hol = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
           "2027-09-06", "2027-11-25", "2027-12-24"}
    m = n.hour * 60 + n.minute
    d = n.strftime("%Y-%m-%d")
    return n.weekday() >= 5 or d in hol or not (570 <= m < (780 if d in early else 960))


for b in ("BTC", "xyz:SP500"):
    try:
        daily(b)
    except Exception as e:
        ERR["bench:" + b] = str(e)[:80]
FG = None
try:
    with urllib.request.urlopen(urllib.request.Request("https://api.alternative.me/fng/?limit=1", headers={"User-Agent": "perps-collector"}), timeout=10) as r:
        FG = int(json.loads(r.read())["data"][0]["value"])
except Exception:
    pass
CLOSED = us_closed()
AN = (F.get("analyst") or {}).get("by") or {}
TW = [t for t in (F.get("influ") or []) if t.get("t", "") >= dt.datetime.fromtimestamp(time.time() - 86400, UTC).strftime("%Y-%m-%dT%H:%M:%S")]

# --- calibration : taux de réussite récent par type de setup (60 jours)
MONTHS = sorted({dt.datetime.fromtimestamp(NOW / 1000 - d * 86400, UTC).strftime("%Y-%m") for d in (0, 31, 62)})
BYM = {m: jload(f"data/proj/{m}.json", []) for m in MONTHS}
DIRTY = set()
LIM = NOW - 60 * 86400000
RECS = [r for m in MONTHS for r in BYM[m] if r.get("t0", 0) >= LIM]
ST = trader.stats(RECS)

BT = jload("data/backtest.json", {})
BV = (((BT.get("variants") or {}).get(BT.get("chosen") or "") or {}).get("stats")) or {}
_par = ((BT.get("variants") or {}).get(BT.get("chosen") or "") or {}).get("par")
if _par:  # le moteur en direct utilise les réglages retenus par le test historique : statistiques et projections restent cohérentes
    trader.PARAM.update({k: v for k, v in _par.items() if k in trader.PARAM})


def edge(setup, cls):
    """(chances en %, gain moyen en R, nombre de cas) pour un type de setup : test historique (poids de 60 trades au plus),
    complété par les projections terminées en direct, qui prennent peu à peu le dessus."""
    live = (ST.get("setup") or {}).get(setup) or {}
    if setup == "Attendre":
        h = (BV.get("sc") or {}).get(f"Attendre|{cls}") or (BV.get("setup") or {}).get("Attendre") or {}
        hn = h.get("nrange") or 0
        p0 = h.get("range") if h.get("range") is not None else 0.5
        k = min(60, hn)
        ln = live.get("nrange") or 0
        lp = live.get("range") or 0
        p = (p0 * k + lp * ln) / (k + ln) if k + ln else p0
        return round(p * 100), None, hn + ln
    h = None
    for x in ((BV.get("sc") or {}).get(f"{setup}|{cls}"), (BV.get("setup") or {}).get(setup), BV.get("all")):
        if x and (x.get("win", 0) + x.get("loss", 0)) >= 30:
            h = x
            break
    h = h or {}
    hw, hl = h.get("win", 0), h.get("loss", 0)
    p0 = hw / (hw + hl) if hw + hl else 0.35
    e0 = h.get("avgR") if h.get("avgR") is not None else 0.0
    k = min(60, hw + hl)
    lw, ll, lt = live.get("win", 0), live.get("loss", 0), live.get("trades", 0)
    p = (p0 * k + lw) / (k + lw + ll) if k + lw + ll else p0
    e = (e0 * k + (live.get("avgR") or 0) * lt) / (k + lt) if k + lt else e0
    return round(p * 100), round(e, 2), hw + hl + lw + ll


BY, NEWREC = {}, []
for coin in UNI:
    try:
        c1h = hl(trader.candles, coin, "1h", 21)
        d1 = daily(coin)
        if not c1h or not d1:
            continue
        # la bougie du jour suit les bougies 1 h
        day0 = (c1h[-1]["t"] // 86400000) * 86400000
        hs = [k for k in c1h if k["t"] >= day0]
        if hs:
            last = {"t": day0, "o": hs[0]["o"], "h": max(k["h"] for k in hs), "l": min(k["l"] for k in hs), "c": hs[-1]["c"], "v": sum(k["v"] for k in hs)}
            d1 = [k for k in d1 if k["t"] < day0] + [last]
        sym = coin.split(":")[-1]
        crypto = ":" not in coin
        ctx = CTX.get(coin, {})
        # open interest sur ~24 h
        hist = CACHE["oi"].setdefault(coin, [])
        if ctx.get("oi") and (not hist or NOW - hist[-1][0] > 50 * 60000):
            hist.append([NOW, ctx["oi"], ctx.get("mark") or c1h[-1]["c"]])
        CACHE["oi"][coin] = [h for h in hist if NOW - h[0] < 30 * 3600000]
        oi_chg = None
        ref = next((h for h in CACHE["oi"][coin] if NOW - h[0] <= 26 * 3600000 and NOW - h[0] >= 18 * 3600000), None)
        if ref and ref[1]:
            oi_chg = ((ctx.get("mark") or c1h[-1]["c"]) / ref[2] * 100 - 100, ctx["oi"] / ref[1] * 100 - 100)
        book = None
        try:
            lb = hl(trader.post, {"type": "l2Book", "coin": coin}, gap=0.2)
            mid = ctx.get("mark") or c1h[-1]["c"]
            bids = sum(float(x["sz"]) for x in (lb.get("levels") or [[], []])[0] if float(x["px"]) >= mid * 0.995)
            asks = sum(float(x["sz"]) for x in (lb.get("levels") or [[], []])[1] if float(x["px"]) <= mid * 1.005)
            book = (bids, asks)
        except Budget:
            raise
        except Exception:
            pass
        b = bench_of("BTC") if crypto and coin != "BTC" else (bench_of("xyz:SP500") if not crypto and coin != "xyz:SP500" else None)
        bench = (b[0], "le bitcoin" if crypto else "le S&P 500", b[1]) if b else None
        an = AN.get(sym) if not crypto else None
        earn = None
        if an and an.get("earn"):
            dd = (an["earn"] * 1000 - NOW) / 86400000
            earn = None if dd < -1 else max(0, int(dd + 0.999))
        bull = sum(1 for t in TW if sym in (t.get("tickers") or []) and t.get("tag") == "bull")
        bear = sum(1 for t in TW if sym in (t.get("tickers") or []) and t.get("tag") == "bear")
        extra = {"crypto": crypto, "fg": FG, "analyst": an, "senti": (bull, bear), "oi_chg": oi_chg, "book": book,
                 "closed": (not crypto) and sym not in trader.NOT_STOCK and CLOSED, "earn_days": earn}
        a = trader.analyze(coin, c1h, d1, ctx, bench, extra)
        if not a:
            continue
        cls = "crypto" if crypto else ("indice/matière" if sym in trader.NOT_STOCK else "action")
        # chances de toucher la 1re cible avant le stop et gain moyen par trade : test historique puis résultats en direct
        if a["bias"] != "neutre":
            ch, ed, nref = edge(a["setup"], cls)
            if ed is not None and ed < -0.05 and nref >= 50:
                # pas de blocage automatique (il figerait le moteur sur la période testée) : un avertissement clair
                a["avoid"] = f"Ce type d'entrée ({a['setup'].lower()}) a perdu en moyenne {abs(ed):.2f} R par trade sur les cas comparables : prudence".replace(".", ",", 1)
        if a["bias"] == "neutre":
            ch, ed, nref = edge("Attendre", cls)
        a["sconf"] = a["conf"]  # force du signal (règles de suivi, identiques au test historique)
        a["conf"], a["edge"], a["nref"] = ch, ed, nref
        # direction à l'échéance des projections terminées avant leur horizon (stop ou cible touchés tôt)
        for m in MONTHS:
            for r in BYM[m]:
                if r.get("coin") == coin and r.get("dir_ok") is None and not r.get("dir_done") and trader.dir_fill(r, c1h):
                    DIRTY.add(m)
        # suivi de la projection en cours (mêmes règles que le test historique)
        op, fin = trader.track(OPEN.get(coin), a, c1h, NOW, coin, cls)
        NEWREC += fin
        OPEN[coin] = op
        a["open"] = {k: op.get(k) for k in ("t0", "px0", "bias", "setup", "conf", "edge", "nref", "e", "sl", "tp", "fill", "fill_t", "band", "hz", "mfe")}
        a["track"] = ST["coin"].get(coin)
        BY[coin] = a
    except Budget:
        ERR["temps"] = f"arrêt à {coin}"
        break
    except Exception as e:
        ERR[coin] = f"{type(e).__name__}: {e}"[:120]
signal.alarm(0)

# --- enregistrement des projections terminées (par mois de départ)
KEYS = ("id", "coin", "cls", "t0", "px0", "bias", "setup", "conf", "edge", "nref", "hz", "e", "sl", "tp", "rr", "band", "atr",
        "fill", "fill_t", "status", "r", "end", "px_end", "px_hz", "dir_ok", "dir_done", "mfe")
for r in NEWREC:
    m = dt.datetime.fromtimestamp(r["t0"] / 1000, UTC).strftime("%Y-%m")
    L = BYM.setdefault(m, jload(f"data/proj/{m}.json", []))
    if not any(x.get("id") == r["id"] for x in L):
        L.append({k: r.get(k) for k in KEYS if r.get(k) is not None})
        DIRTY.add(m)
        RECS.append(L[-1])
for m in DIRTY:
    jsave(f"data/proj/{m}.json", BYM[m])
ST = trader.stats([r for r in RECS if r.get("t0", 0) >= LIM])
OPEN = {k: v for k, v in OPEN.items() if k in BY or NOW - v.get("t0", 0) < 7 * 86400000}
jsave("data/proj_open.json", OPEN)
jsave("data/proj_cache.json", CACHE)
recent = sorted([r for r in RECS if r.get("status") not in (None, "open", "remplacée")], key=lambda r: -(r.get("end") or 0))[:40]
BT = jload("data/backtest.json", {})
bt = None
if BT.get("variants"):
    vv = BT["variants"].get(BT.get("chosen") or "standard") or {}
    bt = {"at": BT.get("at"), "from": BT.get("from"), "to": BT.get("to"), "n_coins": len(BT.get("coins") or []), "all": (vv.get("stats") or {}).get("all"),
          "setup": (vv.get("stats") or {}).get("setup"), "class": (vv.get("stats") or {}).get("class"), "conf": (vv.get("stats") or {}).get("conf")}
F["proj"] = {"at": NOW, "by": BY, "stats": ST, "bt": bt,
             "recent": [{k: r.get(k) for k in ("coin", "t0", "bias", "setup", "conf", "status", "r", "dir_ok", "end", "px0", "px_end", "px_hz", "e", "sl", "tp", "band", "fill")} for r in recent],
             "fg": FG, "closed": CLOSED, "errors": ERR}
json.dump(F, open("data/feed.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f"proj: {len(BY)} marchés analysés, {len(NEWREC)} projections terminées, {len(OPEN)} en cours, erreurs {len(ERR)}")
