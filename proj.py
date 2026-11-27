"""Projections « trader » + suivi de leurs résultats. Lancé après collect.py ; ajoute F.proj au feed.

Fichiers : data/proj_open.json (projections en cours), data/proj_cache.json (bougies jour + historique OI),
data/proj/AAAA-MM.json (projections terminées, gardées pour les statistiques).
"""
import json, os, time, signal, urllib.request, datetime as dt, traceback
import trader

signal.signal(signal.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError("proj: temps dépassé")))
signal.alarm(330)
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
    m0, c0 = trader.post({"type": "metaAndAssetCtxs"})
    mx, cx = trader.post({"type": "metaAndAssetCtxs", "dex": "xyz"})
except Exception as e:
    print("proj: Hyperliquid indisponible", e)
    raise SystemExit(0)
CTX = {}
for meta, ctxs, pre in ((m0, c0, ""), (mx, cx, "")):
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
    d = trader.candles(coin, "1d", 230)
    CACHE["d1"][coin] = d
    CACHE.setdefault("d1_at", {})[coin] = NOW
    time.sleep(0.6)
    return d


def us_closed():
    from zoneinfo import ZoneInfo
    n = dt.datetime.now(ZoneInfo("America/New_York"))
    hol = {"2026-11-26", "2026-12-25", "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05",
           "2027-09-06", "2027-11-25", "2027-12-24"}
    m = n.hour * 60 + n.minute
    return n.weekday() >= 5 or n.strftime("%Y-%m-%d") in hol or not (570 <= m < 960)


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
TW = [t for t in (F.get("influ") or []) if t.get("t", "") >= dt.datetime.utcfromtimestamp(time.time() - 86400).strftime("%Y-%m-%dT%H:%M:%S")]

# --- calibration : taux de réussite récent par type de setup (60 jours)
MONTHS = sorted({(dt.datetime.utcnow() - dt.timedelta(days=d)).strftime("%Y-%m") for d in (0, 31, 62)})
RECS = []
for m in MONTHS:
    RECS += jload(f"data/proj/{m}.json", [])
LIM = NOW - 60 * 86400000
RECS = [r for r in RECS if r.get("t0", 0) >= LIM]
ST = trader.stats(RECS)

BY, NEWREC = {}, []
for coin in UNI:
    try:
        c1h = trader.candles(coin, "1h", 21)
        time.sleep(0.9)
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
        b = bench_of("BTC") if crypto and coin != "BTC" else (bench_of("xyz:SP500") if not crypto and coin != "xyz:SP500" else None)
        bench = (b[0], "le bitcoin" if crypto else "le S&P 500", b[1]) if b else None
        an = AN.get(sym) if not crypto else None
        earn = None
        if an and an.get("earn"):
            earn = max(0, int((an["earn"] * 1000 - NOW) / 86400000))
        bull = sum(1 for t in TW if sym in (t.get("tickers") or []) and t.get("tag") == "bull")
        bear = sum(1 for t in TW if sym in (t.get("tickers") or []) and t.get("tag") == "bear")
        extra = {"crypto": crypto, "fg": FG, "analyst": an, "senti": (bull, bear), "oi_chg": oi_chg,
                 "closed": (not crypto) and sym not in trader.NOT_STOCK and CLOSED, "earn_days": earn}
        a = trader.analyze(coin, c1h, d1, ctx, bench, extra)
        if not a:
            continue
        cls = "crypto" if crypto else ("indice/matière" if sym in trader.NOT_STOCK else "action")
        # calibration de la confiance par l'historique du même type de setup
        s_ = ST["setup"].get(a["setup"]) or {}
        if s_.get("wr") is not None and (s_.get("win", 0) + s_.get("loss", 0)) >= 8:
            a["conf"] = round(0.65 * a["conf"] + 0.35 * s_["wr"] * 100)
            a["calib"] = {"wr": s_["wr"], "n": s_["win"] + s_["loss"]}
        # suivi de la projection en cours
        op = OPEN.get(coin)
        if op:
            op = trader.evaluate(op, c1h, NOW)
            if op.get("status") and op["status"] != "open":
                NEWREC.append(op); op = None
            elif not op.get("fill") and op["bias"] != a["bias"] and a["bias"] != "neutre" and a["conf"] >= 60:
                op.update(status="remplacée", end=NOW, px_end=a["px"]); NEWREC.append(op); op = None
        if not op:
            op = {"id": f"{coin}:{NOW}", "coin": coin, "cls": cls, "t0": NOW, "px0": a["px"], "bias": a["bias"], "setup": a["setup"],
                  "conf": a["conf"], "hz": a["hz"], "status": "open", "last": 0}
            if a["plan"]:
                op.update(e=a["plan"]["e"], sl=a["plan"]["sl"], tp=a["plan"]["tp"], rr=a["plan"]["rr"])
            else:
                lo = a["sup"][0][0] if a["sup"] else a["px"] - 1.5 * a["atr4h"]
                hi = a["res"][0][1] if a["res"] else a["px"] + 1.5 * a["atr4h"]
                op["band"] = [lo, hi]
        OPEN[coin] = op
        a["open"] = {k: op.get(k) for k in ("t0", "px0", "bias", "setup", "conf", "e", "sl", "tp", "fill", "fill_t", "band", "hz", "mfe")}
        a["track"] = ST["coin"].get(coin)
        BY[coin] = a
    except TimeoutError:
        ERR["temps"] = f"arrêt à {coin}"
        break
    except Exception as e:
        ERR[coin] = f"{type(e).__name__}: {e}"[:120]
        if "429" in str(e):
            time.sleep(8)

# --- enregistrement des projections terminées (par mois de départ)
for r in NEWREC:
    m = dt.datetime.utcfromtimestamp(r["t0"] / 1000).strftime("%Y-%m")
    L = jload(f"data/proj/{m}.json", [])
    if not any(x.get("id") == r["id"] for x in L):
        L.append({k: r.get(k) for k in ("id", "coin", "cls", "t0", "px0", "bias", "setup", "conf", "hz", "e", "sl", "tp", "rr", "band",
                                          "fill", "fill_t", "status", "r", "end", "px_end", "dir_ok", "mfe")})
        jsave(f"data/proj/{m}.json", L)
    RECS.append(r)
ST = trader.stats([r for r in RECS if r.get("t0", 0) >= LIM])
signal.alarm(0)
OPEN = {k: v for k, v in OPEN.items() if k in BY or NOW - v.get("t0", 0) < 7 * 86400000}
jsave("data/proj_open.json", OPEN)
jsave("data/proj_cache.json", CACHE)
recent = sorted([r for r in RECS if r.get("status") not in (None, "open", "remplacée")], key=lambda r: -(r.get("end") or 0))[:40]
F["proj"] = {"at": NOW, "by": BY, "stats": ST, "recent": [{k: r.get(k) for k in ("coin", "t0", "bias", "setup", "conf", "status", "r", "dir_ok", "end", "px0", "px_end", "e", "sl", "tp")} for r in recent],
             "fg": FG, "closed": CLOSED, "errors": ERR}
json.dump(F, open("data/feed.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print(f"proj: {len(BY)} marchés analysés, {len(NEWREC)} projections terminées, {len(OPEN)} en cours, erreurs {len(ERR)}")
