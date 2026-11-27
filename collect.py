"""Collecteur gratuit pour le dashboard Perps actions HL.
Tourne sur GitHub Actions (gratuit) 3 fois par jour et écrit data/feed.json.
Sources : X/Twitter (via Scweet + cookie d'un compte X secondaire), Truth Social de Trump (archive trumpstruth.org),
Reddit, actualités mondiales à risque (GDELT), enlèvements liés à la crypto en France (GDELT), calendrier de la Fed.
Chaque source est indépendante : si l'une échoue, les autres sont quand même écrites."""
import json, os, html, urllib.error, re, time, datetime as dt, traceback, urllib.request, urllib.parse, xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (compatible; perps-dashboard-collector/1.0)"}
OUT = {"generated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": {}}
CFG = json.load(open("config.json", encoding="utf-8"))
try:  # tokens masqués depuis le dashboard (bouton « Masquer »), ajoutés à ceux de config.json
    _rq = urllib.request.Request("https://lqefewiwrecdjommktgm.supabase.co/rest/v1/influ_prefs?id=eq.analyserxxx&select=tok_hidden",
                                 headers={"apikey": "sb_publishable_IMMxd7lqThb0elf0Z2JwNQ_3Da4tdWq", "User-Agent": "perps-collector"})
    with urllib.request.urlopen(_rq, timeout=10) as _r:
        _j = json.loads(_r.read() or b"[]")
    CFG["tok_hidden"] = sorted({str(x).upper().lstrip("$") for x in (CFG.get("tok_hidden") or []) + ((_j[0].get("tok_hidden") or []) if _j else [])})
except Exception:
    pass

def get(url, timeout=25, headers=None):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig", "replace")

def section(name):
    def deco(fn):
        def run():
            try:
                OUT[name] = fn()
            except Exception as e:  # une source en panne ne bloque pas les autres
                OUT["errors"][name] = f"{type(e).__name__}: {e}"[:300]
                traceback.print_exc()
        return run
    return deco

BULL = re.compile(r"\b(beat|surge|soar|jump|rall(y|ies)|upgrade|record|bullish|buy|long|breakout|deal|cut rates|rate cut|stimulus)\b", re.I)
BEAR = re.compile(r"\b(miss|fall|drop|plunge|crash|downgrade|bearish|sell|short|tariff|sanction|war|ban|probe|lawsuit|hack|default|recession)\b", re.I)
def tag(t):
    b, r = bool(BULL.search(t or "")), bool(BEAR.search(t or ""))
    return "bull" if b and not r else "bear" if r and not b else "mix"

def pick(d, *keys, default=None):
    for k in keys:
        cur = d
        for part in k.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        if cur not in (None, ""):
            return cur
    return default

def norm_tweet(t):
    if not isinstance(t, dict):
        t = getattr(t, "__dict__", {}) or {}
    user = pick(t, "username", "user.username", "user.screen_name", "author.username", "screen_name", "handle", default="?")
    tid = pick(t, "id", "tweet_id", "id_str", "rest_id", default="")
    text = pick(t, "text", "full_text", "content", "rawContent", "legacy.full_text", default="")
    return {
        "h": str(user).lstrip("@"),
        "fol": int(pick(t, "user.followers_count", "followers", "user.followers", "author.followers", default=0) or 0),
        "t": str(pick(t, "created_at", "date", "timestamp", "datetime", default="")),
        "eng": int(pick(t, "likes", "favorite_count", "like_count", "legacy.favorite_count", default=0) or 0)
               + 2 * int(pick(t, "retweets", "retweet_count", "legacy.retweet_count", default=0) or 0),
        "url": pick(t, "url", "tweet_url", default=f"https://x.com/{user}/status/{tid}" if tid else f"https://x.com/{user}"),
        "fr": text[:400],
        "tag": tag(text),
    }

def _scweet(tok, accs, tickers, since):
    from Scweet import Scweet
    s = Scweet(auth_token=tok)
    acc_rows, tick = [], {}
    if accs:
        try:
            acc_rows = list(s.get_profile_tweets(accs, limit=15 * len(accs)) or [])
        except Exception:
            for a in accs:
                try:
                    acc_rows += list(s.search("", since=since, from_users=[a], limit=15) or [])
                except Exception:
                    pass
    for tk in tickers:
        try:
            tick[tk] = list(s.search(f"${tk}", since=since, min_likes=CFG.get("min_likes", 100), limit=25) or [])
            time.sleep(2)
        except Exception:
            tick[tk] = []
    return [norm_tweet(r) for r in acc_rows], {k: [norm_tweet(r) for r in v] for k, v in tick.items()}

def _twscrape(tok, ct0, accs, tickers, since):
    import asyncio
    from twscrape import API
    def nt(t):
        u = t.user
        return {"h": u.username, "fol": int(getattr(u, "followersCount", 0) or 0), "t": t.date.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "eng": int(t.likeCount or 0) + 2 * int(t.retweetCount or 0), "url": t.url, "fr": (t.rawContent or "")[:400], "tag": tag(t.rawContent)}
    async def run():
        api = API("/tmp/twscrape.db")
        await api.pool.add_account("collecteur", "x", "x@x.x", "x", cookies=f"auth_token={tok}; ct0={ct0}")
        acc, tick = [], {}
        for a in accs:
            try:
                u = await api.user_by_login(a)
                async for t in api.user_tweets(u.id, limit=15):
                    acc.append(nt(t))
            except Exception:
                pass
        for tk in tickers:
            try:
                tick[tk] = [nt(t) async for t in api.search(f"${tk} min_faves:{CFG.get('min_likes', 100)} since:{since}", limit=25)]
            except Exception:
                tick[tk] = []
        return acc, tick
    return asyncio.run(run())

def _twifork(tok, ct0, accs, tickers, since):
    import asyncio
    try:
        from twifork import Client
    except ImportError:
        from twikit import Client
    def nt(t):
        u = t.user
        return {"h": u.screen_name, "fol": int(getattr(u, "followers_count", 0) or 0), "t": str(t.created_at),
                "eng": int(t.favorite_count or 0) + 2 * int(t.retweet_count or 0), "url": f"https://x.com/{u.screen_name}/status/{t.id}",
                "fr": (t.text or "")[:400], "tag": tag(t.text)}
    async def run():
        c = Client("en-US")
        c.set_cookies({"auth_token": tok, "ct0": ct0})
        acc, tick = [], {}
        for a in accs:
            try:
                u = await c.get_user_by_screen_name(a)
                acc += [nt(t) for t in await c.get_user_tweets(u.id, "Tweets", count=15)]
            except Exception:
                pass
        for tk in tickers:
            try:
                tick[tk] = [nt(t) for t in await c.search_tweet(f"${tk} min_faves:{CFG.get('min_likes', 100)} since:{since}", "Top", count=25)]
            except Exception:
                tick[tk] = []
        return acc, tick
    return asyncio.run(run())

@section("x")
def x_feed():
    tok = os.environ.get("X_AUTH_TOKEN", "").strip()
    ct0 = os.environ.get("X_CT0", "").strip()
    def guest(note):
        try:
            g = json.load(open("data/x_guest.json", encoding="utf-8"))
        except Exception:
            g = {"tweets": []}
        tick = {}
        for t in g.get("tweets", []):
            for tk in set(re.findall(r"\$([A-Z]{2,6})\b", t["fr"])):
                tick.setdefault(tk, []).append(t)
        acc = {}
        for t in g.get("tweets", []):
            acc.setdefault(t["h"].lower(), []).append(t)
        if g.get("errors"):
            OUT["errors"]["x_guest"] = str(g["errors"])[:300]
        return {"source": "invité (sans compte)", "note": note, "accounts": acc, "tickers": tick, "latest": sorted(g.get("tweets", []), key=lambda x: x["t"], reverse=True)[:40]}
    # Les outils Python (Scweet, twscrape) se bloquent avec les cookies : la lecture X passe désormais
    # uniquement par x_guest.mjs (connecté au compte secondaire quand les secrets existent).
    try:
        lg = json.load(open("data/x_guest.json", encoding="utf-8")).get("influ_mode") == "compte"
    except Exception:
        lg = False
    if lg or not tok or os.environ.get("X_PY_TOOLS") != "1":
        r = guest("Compte X secondaire connecté : tweets récents des comptes suivis." if lg else "Mode invité : tweets des comptes suivis seulement. Ajoute X_AUTH_TOKEN et X_CT0 pour les tweets récents.")
        if lg:
            r["source"] = "compte secondaire"
        return r
    since = (dt.datetime.utcnow() - dt.timedelta(days=2)).strftime("%Y-%m-%d")
    accs, tickers = CFG.get("x_accounts", []), CFG.get("tickers", [])
    tried = []
    for name, fn, needs_ct0 in (("Scweet", _scweet, False), ("twscrape", _twscrape, True), ("twifork", _twifork, True)):
        if needs_ct0 and not ct0:
            tried.append(f"{name}: secret X_CT0 manquant")
            continue
        import signal
        def _to(*_):
            raise TimeoutError("trop long (120 s)")
        signal.signal(signal.SIGALRM, _to)
        signal.alarm(120)
        try:
            acc, tick = fn(tok, accs, tickers, since) if not needs_ct0 else fn(tok, ct0, accs, tickers, since)
            signal.alarm(0)
            n = len(acc) + sum(len(v) for v in tick.values())
            if n == 0:
                tried.append(f"{name}: 0 tweet")
                continue
            out = {"source": name, "accounts": {}, "tickers": {k: sorted(v, key=lambda x: -x["eng"])[:8] for k, v in tick.items()}}
            for t in acc:
                out["accounts"].setdefault(t["h"].lower(), []).append(t)
            if tried:
                OUT["errors"]["x_fallback"] = " | ".join(tried)
            return out
        except Exception as e:
            tried.append(f"{name}: {type(e).__name__}: {str(e)[:120]}")
        finally:
            signal.alarm(0)
    OUT["errors"]["x"] = " | ".join(tried)
    return guest("Les 3 outils avec compte ont échoué, repli en mode invité.")

@section("stocktwits")
def stocktwits():
    out = {}
    for tk in CFG.get("tickers", []):
        try:
            j = json.loads(get(f"https://api.stocktwits.com/api/2/streams/symbol/{tk}.json", headers={"Accept": "application/json"}))
            out[tk] = [{"h": m["user"]["username"], "fol": m["user"].get("followers", 0), "t": m["created_at"], "eng": (m.get("likes") or {}).get("total", 0),
                        "url": f"https://stocktwits.com/{m['user']['username']}/message/{m['id']}", "fr": html.unescape(m["body"])[:400],
                        "tag": {"Bullish": "bull", "Bearish": "bear"}.get(((m.get("entities") or {}).get("sentiment") or {}).get("basic"), "mix")} for m in j.get("messages", [])[:15]]
            time.sleep(1)
        except Exception as e:
            OUT["errors"][f"stocktwits:{tk}"] = str(e)[:120]
    return out

IBULL = re.compile(r"\b(long|longed|longing|bull(ish)?|moon|pump(ing)?|send(ing)?|breakout|higher|ath|buy(ing)?|bid|accumulat\w*|undervalued|reversal|bottom(ed)?|rip|green)\b", re.I)
IBEAR = re.compile(r"\b(short(s|ed|ing)?|bear(ish)?|dump(s|ed|ing)?|rug(ged)?|sell(s|ing)?|sold|lower|top(ped)? is in|breakdown|overvalued|rekt|crash(es|ed|ing)?|exit|slam(s|med)?|down(side)?|drop(s|ped|ping)?|fall(s|ing)?|nuke[ds]?|fade|fading|puts?|tank(s|ed|ing)?|bleed(ing)?|capitulat\w*|collapse[ds]?)\b", re.I)
TALIAS = {"MATIC": "POL", "WBTC": "BTC", "WETH": "ETH", "XBT": "BTC"}
QUOTES = {"BTC", "ETH", "SOL", "BNB", "HYPE"}
MACROW = {"NATGAS", "USOIL", "UKOIL", "BRENT", "WTI", "GOLD", "SILVER", "COPPER", "XAUUSD", "XAGUSD", "PLATINUM", "URANIUM", "SPX", "SPY", "QQQ",
          "NDX", "DXY", "VIX", "TNX", "US10Y", "EURUSD", "USDJPY", "CORN", "WHEAT", "MAGS", "SOXL"}
def norm_tk(L):
    """$ETHUSD -> ETH, $HYPEBTC -> HYPE + BTC, $MATIC -> POL (nom actuel)."""
    known = {r[0] for r in (OUT.get("top") or [])} | {"BTC", "ETH", "SOL", "HYPE"}
    major = {r[0] for r in (OUT.get("top") or []) if (r[1] or 9999) <= 100} | {"BTC", "ETH", "SOL", "HYPE"}
    out = []
    for t in L:
        t = TALIAS.get(t, t)
        m = re.match(r"^([A-Z0-9]{2,10}?)(USDT|USDC|USDE|USD|PERP)$", t)
        if m and (m.group(1) in known or len(m.group(1)) >= 3):
            out.append(TALIAS.get(m.group(1), m.group(1)))
            continue
        if t in MACROW:
            out.append(t)
            continue
        if t not in known and len(t) >= 5:
            for i in range(2, len(t) - 1):
                if t[:i] in known and t[i:] in known and (t[i:] in QUOTES or (t[:i] in major and t[i:] in major)):
                    out += [t[:i], t[i:]]
                    break
            else:
                out.append(t)
            continue
        out.append(t)
    return sorted(set(out))

# ---- traduction en français avec le jargon crypto protégé (« shorts » ne doit pas devenir « courts métrages »)
JARGON = ["shorts", "short", "shorted", "shorting", "shorters", "longs", "long", "longed", "longing", "longers", "bottom longers", "pump", "pumps", "pumping", "dump", "dumps",
          "dumping", "bullish", "bearish", "bull", "bear", "ATH", "gm", "gn", "aped", "ape", "bags", "bag", "moon", "mooning", "send", "sends",
          "rug", "rugged", "degen", "alpha", "CT", "dip", "bid", "TP", "TPs", "SL", "stop loss", "breakout", "retest", "leverage", "perps", "perp",
          "spot", "airdrop", "memecoin", "memecoins", "entry", "entries", "invalidation", "invalidations", "chart", "charts", "rekt", "fade", "ser", "fren", "wagmi", "ngmi", "fomo", "fud"]
JRX = re.compile(r"(?<![\w$])(" + "|".join(sorted((re.escape(w) for w in JARGON), key=len, reverse=True)) + r")(?![\w])", re.I)

FIXED = {"breaking": "URGENT", "just in": "DERNIÈRE MINUTE", "última hora": "DERNIÈRE MINUTE", "ultima hora": "DERNIÈRE MINUTE"}
FRX = re.compile(r"(?<![\w$])(" + "|".join(re.escape(k) for k in FIXED) + r")(?![\w])", re.I)

def tr_fr(text):
    keep = []
    # un texte écrit tout en majuscules se traduit mal : on le passe en minuscules (les $tickers restent protégés plus bas)
    letters = [ch for ch in text if ch.isalpha()]
    if letters and sum(ch.isupper() for ch in letters) / len(letters) > 0.6:
        text = re.sub(r"(\$[A-Za-z][A-Za-z0-9]{1,9})|([^$]+)", lambda m: m.group(1) or m.group(2).lower(), text)
    text = FRX.sub(lambda m: (keep.append(FIXED[m.group(0).lower()]), f"ZQ{len(keep) - 1}Q")[1], text)
    def sub(m):
        keep.append(m.group(0))
        return f"ZQ{len(keep) - 1}Q"
    prot = JRX.sub(sub, text)
    # les $tickers, @comptes et liens ne sont pas traduits non plus
    prot = re.sub(r"(\$[A-Za-z][A-Za-z0-9]{1,9}|@\w+|https?://\S+)", lambda m: (keep.append(m.group(0)), f"ZQ{len(keep) - 1}Q")[1], prot)
    q = urllib.parse.quote(prot)
    try:
        j = json.loads(get("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fr&dt=t&q=" + q, timeout=10))
        src = j[2] if len(j) > 2 else ""
        out = "".join(seg[0] for seg in j[0] if seg and seg[0])
    except urllib.error.HTTPError:
        # second point d'accès Google, souvent accepté quand le premier refuse les serveurs GitHub
        j = json.loads(get("https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl=fr&q=" + q, timeout=10))
        first = j[0] if isinstance(j, list) and j else j
        if isinstance(first, list):
            out, src = first[0], (first[1] if len(first) > 1 else "")
        else:
            out, src = str(first), ""
    if src == "fr":
        return None
    out = re.sub(r"Z\s?Q\s?(\d+)\s?Q", lambda m: keep[int(m.group(1))] if int(m.group(1)) < len(keep) else m.group(0), out, flags=re.I)
    if re.search(r"Z\s?Q\s?\d", out, re.I):
        return None  # repère abîmé par la traduction : on laisse le dashboard traduire lui-même
    return out

def translate_rows(rows, key):
    """Traduit le champ fr (l'original passe dans o_fr), avec cache par lien."""
    path = "data/tr_cache.json"
    try:
        cache = json.load(open(path, encoding="utf-8"))
    except Exception:
        cache = {}
    n = 0
    for t in rows:
        u, txt = t.get("url"), t.get("fr") or ""
        if not u or not txt.strip():
            continue
        if u not in cache and n < 25:
            n += 1
            try:
                v = tr_fr(txt)
                cache[u] = v if v and v != txt else ""
                time.sleep(0.4)
            except Exception as e:
                OUT["errors"]["traduction_" + key] = (f"{type(e).__name__} {getattr(e, 'code', '')}").strip()[:80]
                break
        if cache.get(u):
            t["o_fr"], t["fr"] = txt, cache[u]
    json.dump(cache, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

def translate_influ(rows):
    path = "data/tr_cache.json"
    try:
        cache = json.load(open(path, encoding="utf-8"))
    except Exception:
        cache = {}
    t0, n = time.time(), 0
    for t in rows:
        u, txt = t.get("url"), t.get("fr") or ""
        if not u or not txt.strip():
            continue
        if u in cache:
            if cache[u]:
                t["o_fr"], t["fr"] = txt, cache[u]
            continue
        if n >= 80 or time.time() - t0 > 90:
            continue
        n += 1
        try:
            v = tr_fr(txt)
            cache[u] = v if v and v != txt else ""
            if cache[u]:
                t["o_fr"], t["fr"] = txt, cache[u]
            time.sleep(0.4)
        except Exception as e:
            OUT["errors"]["traduction"] = (f"{type(e).__name__} {getattr(e, 'code', '')}").strip()[:80]
            break
    keep = {t.get("url") for t in rows}
    cache = {k: v for k, v in cache.items() if k in keep or "truth" in str(k)}
    json.dump(cache, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

@section("influ")
def influ():
    try:
        g = json.load(open("data/x_guest.json", encoding="utf-8"))
    except Exception:
        return []
    out = []
    prof = g.get("influ_prof", {})
    OUT["influ_prof"] = prof
    OUT["influ_mode"] = g.get("influ_mode", "invite")
    try:
        OUT["influ_list"] = json.load(open("config.json", encoding="utf-8")).get("influencers", [])
        OUT["influ_n"] = len(OUT["influ_list"])
    except Exception:
        pass
    TKRX = r"\$([A-Za-z][A-Za-z0-9]{1,9})\b"
    def tks(txt):
        return norm_tk(set(x.upper() for x in re.findall(TKRX, txt or "")) - {"BTC.D", "USDT", "USDC", "USD"})
    def tagof(txt):
        b, r = bool(IBULL.search(txt or "")), bool(IBEAR.search(txt or ""))
        return "bull" if b and not r else "bear" if r and not b else "mix"
    rows = g.get("influ", [])
    # tokens masqués (pas de vrais calls) : retirés partout, dans config.json → tok_hidden
    TH = {str(x).upper().lstrip("$") for x in (CFG.get("tok_hidden") or [])}
    byid = {}
    for t in rows:
        m = re.search(r"/status/(\d+)", t.get("url") or "")
        if m:
            byid[m.group(1)] = t
    for t in rows:
        pr = prof.get((t.get("h") or "").lower()) or {}
        t["fol"] = pr.get("fol", 0)
        txt = t.get("fr", "")
        t["tickers"] = tks(txt)
        t["tag"] = tagof(txt)
        # réponse ou citation d'un autre influenceur de la liste : elle valide son call
        link = t.get("re") or t.get("q")
        wasre = bool(t.get("re"))
        if link:
            par = byid.get(str(link.get("id") or "")) or {}
            ptxt = par.get("fr") or link.get("tx") or ""
            ptk = tks(ptxt)
            if ptk:
                t["via"] = "reply" if t.get("re") else "quote"
                t["of"] = link.get("h")
                if not t["tickers"]:
                    t["tickers"] = ptk
                    own = t["tag"]
                    ptag = par.get("tag") or tagof(ptxt)
                    # « agreed », « this », « 🚀 » : même sens que le call ; un désaccord explicite garde son propre sens
                    t["tag"] = own if own != "mix" else (ptag if ptag != "mix" else "bull")
            elif not t["tickers"]:
                t.pop("re", None); t.pop("q", None)
        if TH:
            t["tickers"] = [x for x in t["tickers"] if x not in TH]
        if wasre and not t["tickers"]:
            continue  # simple réponse sans token : bruit
        out.append(t)
    translate_influ(out)
    # le dashboard recoupe aussi les tickers composés (HYPEBTC → HYPE + BTC) : on lui signale ceux qui ne doivent pas l'être (NATGAS…)
    try:
        topset = {r[0] for r in (OUT.get("top") or [])}
        K2 = topset | {"BTC", "ETH", "SOL", "USD"}
        extra = set()
        for t in out:
            for T in t.get("tickers") or []:
                if T in topset or len(T) < 5:
                    continue
                if any(T[:i] in K2 and T[i:] in K2 for i in range(2, len(T) - 1)):
                    extra.add(T)
        if extra and OUT.get("top"):
            OUT["top"] = OUT["top"] + [[T, 99999, None] for T in sorted(extra)]
    except Exception as e:
        OUT["errors"]["tickers_composes"] = str(e)[:100]
    return out

SKIPT = {"BTC.D", "USD", "USDT", "USDC", "SPX", "DXY", "ETF", "CPI", "FOMC", "AI", "CT", "ATH", "TA", "NFA", "DYOR", "GM",
         "ES", "NQ", "CL", "NG", "GC", "SI", "HG", "YM", "RTY", "WTI", "BRENT", "NATGAS", "USOIL", "UKOIL", "OIL", "GOLD", "SILVER", "XAU", "XAG",
         "COPPER", "VIX", "NDX", "QQQ", "SPY", "DJI", "TNX", "US10Y", "EURUSD", "USDJPY", "CORN", "WHEAT", "URANIUM", "PLATINUM", "MAGS"}
@section("top")
def top():
    """Classement CoinGecko (top 1000) pour masquer les grosses capitalisations dans l'onglet Influenceurs.
    La page 1 (top 250) est relue à chaque passage ; les pages 2 à 4 (rangs 251 à 1000) toutes les 6 h, pour rester dans la limite gratuite."""
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page="
    row = lambda x: [(x.get("symbol") or "").upper(), x.get("market_cap_rank"), x.get("market_cap")]
    first = [row(x) for x in json.loads(get(url + "1", headers={"Accept": "application/json"})) if x.get("symbol")]
    try:
        prev = json.load(open("data/feed.json", encoding="utf-8"))
    except Exception:
        prev = {}
    old = [r for r in (prev.get("top") or []) if 250 < (r[1] or 0) <= 1000]
    at = prev.get("top_deep_at") or ""
    fresh = at and (dt.datetime.utcnow() - dt.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ")).total_seconds() < 6 * 3600
    if fresh and len(old) > 500:
        OUT["top_deep_at"] = at
        return first + old
    deep = []
    for pg in (2, 3, 4):
        try:
            time.sleep(2.5)
            deep += [row(x) for x in json.loads(get(url + str(pg), headers={"Accept": "application/json"})) if x.get("symbol")]
        except Exception as e:
            OUT["errors"]["top_deep"] = f"page {pg}: {type(e).__name__}"[:120]
            break
    if len(deep) > 500:
        OUT["top_deep_at"] = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return first + deep
    if old:
        OUT["top_deep_at"] = at
    return first + (old or deep)

EU_LIST = ["^FCHI", "AC.PA", "AI.PA", "AIR.PA", "MT.AS", "CS.PA", "BNP.PA", "EN.PA", "CAP.PA", "CA.PA", "ACA.PA", "BN.PA", "DSY.PA", "EDEN.PA", "ENGI.PA", "EL.PA", "ERF.PA", "RMS.PA", "KER.PA", "OR.PA", "LR.PA", "MC.PA", "ML.PA", "ORA.PA", "RI.PA", "PUB.PA", "RNO.PA", "SAF.PA", "SGO.PA", "SAN.PA", "SU.PA", "GLE.PA", "STLAP.PA", "STMPA.PA", "HO.PA", "TTE.PA", "URW.PA", "VIE.PA", "DG.PA", "BVI.PA", "AM.PA"]
@section("eu")
def eu():
    """CAC 40 et ses actions : cours DIFFÉRÉS (~15 min), source Yahoo Finance, relevés à chaque passage du collecteur."""
    out = {}
    for s in EU_LIST:
        try:
            j = json.loads(get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(s) + "?range=5d&interval=15m", headers={"Accept": "application/json"}))
            r = j["chart"]["result"][0]
            m = r["meta"]
            closes = [x for x in (r["indicators"]["quote"][0].get("close") or []) if x is not None]
            px = m.get("regularMarketPrice") or (closes[-1] if closes else None)
            prev = m.get("chartPreviousClose") or m.get("previousClose")
            # variation du jour : comparée à la clôture de la veille
            ts = r.get("timestamp") or []
            day_prev = None
            if ts:
                last_day = dt.datetime.utcfromtimestamp(ts[-1]).date()
                for t_, v_ in zip(ts, r["indicators"]["quote"][0].get("close") or []):
                    if v_ is not None and dt.datetime.utcfromtimestamp(t_).date() < last_day:
                        day_prev = v_
            ref = day_prev or prev
            out[s] = {"name": m.get("shortName") or m.get("longName") or s, "px": px, "chg": ((px / ref - 1) * 100) if px and ref else None,
                      "t": m.get("regularMarketTime"), "cur": m.get("currency"), "closes": [round(x, 4) for x in closes[-130:]]}
            time.sleep(0.3)
        except Exception as e:
            OUT["errors"]["eu:" + s] = str(e)[:80]
    return out

@section("metals")
def metals():
    """Cours de référence de l'or et de l'argent (contrats à terme COMEX, différés ~15 min) et euro/dollar."""
    out = {}
    for key, s in (("au", "GC=F"), ("ag", "SI=F"), ("eurusd", "EURUSD=X")):
        try:
            j = json.loads(get("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(s) + "?range=5d&interval=1h", headers={"Accept": "application/json"}))
            m = j["chart"]["result"][0]["meta"]
            out[key] = m.get("regularMarketPrice")
            out[key + "_prev"] = m.get("chartPreviousClose") or m.get("previousClose")
            out[key + "_t"] = m.get("regularMarketTime")
        except Exception as e:
            OUT["errors"]["metals:" + key] = str(e)[:80]
    return out

@section("tokens")
def tokens():
    L = OUT.get("influ") or []
    cnt = {}
    for t in L:
        for k in t.get("tickers", []):
            if k not in SKIPT:
                cnt[k] = cnt.get(k, 0) + 1
    out = {}
    # adresses de contrat (CA) postées dans les calls : EVM 0x… et Solana (base58)
    cas = {}
    for t in L:
        for a in set(re.findall(r"\b0x[a-fA-F0-9]{40}\b", t.get("fr", "")) + re.findall(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", t.get("fr", ""))):
            cas.setdefault(a, []).append(t)
    for a, tw in list(cas.items())[:40]:
        try:
            j = json.loads(get("https://api.dexscreener.com/latest/dex/tokens/" + a))
            ps = [p for p in (j.get("pairs") or []) if (p.get("baseToken") or {}).get("address", "").lower() == a.lower()]
            if not ps:
                continue
            p = max(ps, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            sym_ = (p["baseToken"].get("symbol") or "").upper()
            info = p.get("info") or {}
            out[sym_] = {"name": p["baseToken"].get("name"), "price": float(p.get("priceUsd") or 0) or None, "mcap": p.get("marketCap") or p.get("fdv"),
                         "ch24": (p.get("priceChange") or {}).get("h24"), "liq": (p.get("liquidity") or {}).get("usd"), "vol24": (p.get("volume") or {}).get("h24"),
                         "logo": info.get("imageUrl"), "banner": info.get("header"), "url": p.get("url"), "chain": p.get("chainId"), "pair": p.get("pairAddress"), "ca": a, "byca": 1,
                         "x": next((s.get("url") for s in info.get("socials", []) if s.get("type") == "twitter"), None), "age": p.get("pairCreatedAt")}
            for t in tw:
                if sym_ and sym_ not in t["tickers"]:
                    t["tickers"].append(sym_)
            time.sleep(0.4)
        except Exception as e:
            OUT["errors"]["ca:" + a[:10]] = str(e)[:100]
    topset = {r[0] for r in (OUT.get("top") or []) if r[1] and r[1] <= 100}
    for k, _ in sorted(cnt.items(), key=lambda x: (x[0] in topset, -x[1]))[:45]:
        if k in out:
            continue
        try:
            j = json.loads(get("https://api.dexscreener.com/latest/dex/search?q=" + urllib.parse.quote(k)))
            ps = [p for p in j.get("pairs", []) if (p.get("baseToken") or {}).get("symbol", "").upper() == k]
            if not ps:
                continue
            p = max(ps, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
            info = p.get("info") or {}
            out[k] = {"name": p["baseToken"].get("name"), "price": float(p.get("priceUsd") or 0) or None, "mcap": p.get("marketCap") or p.get("fdv"),
                      "ch24": (p.get("priceChange") or {}).get("h24"), "liq": (p.get("liquidity") or {}).get("usd"), "vol24": (p.get("volume") or {}).get("h24"),
                      "logo": info.get("imageUrl"), "banner": info.get("header"), "url": p.get("url"), "chain": p.get("chainId"),
                      "x": next((s.get("url") for s in info.get("socials", []) if s.get("type") == "twitter"), None),
                      "pair": p.get("pairAddress")}
            time.sleep(0.4)
        except Exception as e:
            OUT["errors"]["token:" + k] = str(e)[:100]
    return out

GT = {"ethereum": "eth", "bsc": "bsc", "solana": "solana", "base": "base", "arbitrum": "arbitrum", "polygon": "polygon_pos", "avalanche": "avax", "sui": "sui-network", "ton": "ton", "hyperevm": "hyperevm", "optimism": "optimism", "blast": "blast", "abstract": "abstract", "sonic": "sonic", "pulsechain": "pulsechain", "cronos": "cro"}
def hl_hist(coin):
    body = json.dumps({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h", "startTime": int((time.time() - 40 * 86400) * 1000), "endTime": int(time.time() * 1000)}}).encode()
    req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=body, headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read())
    return [(int(k["t"]) // 1000, float(k["c"])) for k in d]
def gt_hist(chain, pair):
    net = GT.get(chain)
    if not net or not pair:
        return []
    j = json.loads(get(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{pair}/ohlcv/hour?limit=1000&currency=usd", headers={"Accept": "application/json"}))
    rows = ((j.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
    return sorted((int(r[0]), float(r[4])) for r in rows)
@section("callpx")
def callpx():
    """Prix (et market cap) au moment de chaque call, pour mesurer la performance depuis le call."""
    toks = OUT.get("tokens") or {}
    L = OUT.get("influ") or []
    need = {}
    for t in L:
        for k in t.get("tickers", []):
            need.setdefault(k, []).append(t)
    out = {}
    gtn = [0]
    for k, tw in need.items():
        hist = []
        try:
            hist = hl_hist(k)
        except Exception:
            hist = []
        src = "hl"
        if not hist and k in toks and gtn[0] < 25:
            gtn[0] += 1
            try:
                time.sleep(2.6)
                hist = gt_hist(toks[k].get("chain"), toks[k].get("pair"))
                src = "dex"
            except Exception as e:
                OUT["errors"]["callpx:" + k] = str(e)[:100]
        if not hist:
            continue
        supply = None
        tk = toks.get(k) or {}
        if src == "dex" and tk.get("mcap") and tk.get("price"):
            supply = tk["mcap"] / tk["price"]
        res = {}
        for t in tw:
            ts = int(dt.datetime.fromisoformat(t["t"].replace("Z", "+00:00")).timestamp())
            px = None
            for (h, c) in hist:
                if h <= ts:
                    px = c
                else:
                    break
            if px is None and hist and hist[0][0] - ts < 7200:
                px = hist[0][1]
            if px:
                r = {"px": px, "mc": supply * px if supply else None}
                # performance après le call, lue dans le même historique horaire (pas de requête en plus)
                try:
                    def at(sec):
                        for (h, cl) in hist:
                            if h >= ts + sec:
                                return cl
                        return None
                    for lab, sec in (("r1h", 3600), ("r24h", 86400), ("r3d", 3 * 86400), ("r7d", 7 * 86400)):
                        v = at(sec)
                        if v is not None and hist[-1][0] >= ts + sec:
                            r[lab] = round(max(-1.0, min(50.0, v / px - 1)), 4)
                    w24 = [cl for (h, cl) in hist if ts < h <= ts + 86400]
                    w7 = [cl for (h, cl) in hist if ts < h <= ts + 7 * 86400]
                    if w24:
                        r["mx24"] = round(max(-1.0, min(50.0, max(w24) / px - 1)), 4)
                        r["mn24"] = round(max(-1.0, min(50.0, min(w24) / px - 1)), 4)
                    if w7:
                        r["mx7d"] = round(max(-1.0, min(50.0, max(w7) / px - 1)), 4)
                except Exception:
                    pass
                res[t["url"]] = r
        out[k] = {"src": src, "now": hist[-1][1], "calls": res}
    return out

@section("trump")
def trump():
    xml = get("https://www.trumpstruth.org/feed")
    root = ET.fromstring(xml)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
        txt = html.unescape(re.sub(r"\s+", " ", desc or title).strip())
        txt = re.sub(r"^RT:?\s*https?://\S+\s*", "", txt).strip()
        if not txt or txt.lower().startswith("[no title]") and len(txt) < 20 or re.fullmatch(r"(view image|image|video)\W*", txt, re.I):
            continue
        items.append({"t": it.findtext("pubDate"), "fr": txt[:600], "url": it.findtext("link"), "tag": tag(txt),
                      "market": bool(re.search(r"tariff|china|fed|rate|stock|market|crypto|bitcoin|oil|deal|trade|dollar|powell|inflation|tax", txt, re.I))})
        if len(items) >= 25:
            break
    translate_rows(items, "trump")
    return items

def _reddit_json(sub):
    for host in ("www.reddit.com", "old.reddit.com", "api.reddit.com"):
        try:
            j = json.loads(get(f"https://{host}/r/{sub}/hot.json?limit=30&raw_json=1", headers={"Accept": "application/json", "User-Agent": "linux:perps-collector:1.0 (by /u/perps_dashboard)"}))
            return [c["data"] for c in j["data"]["children"] if not c["data"].get("stickied")]
        except Exception:
            time.sleep(2)
    return None

def _reddit_rss(sub):
    x = get(f"https://www.reddit.com/r/{sub}/hot/.rss?limit=30", headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/128.0"})
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(x).findall("a:entry", ns):
        content = re.sub(r"<[^>]+>", " ", e.findtext("a:content", "", ns))
        out.append({"title": e.findtext("a:title", "", ns), "selftext": content, "created": e.findtext("a:updated", "", ns),
                    "permalink": (e.find("a:link", ns).get("href") if e.find("a:link", ns) is not None else ""), "score": 0, "num_comments": 0})
    return out

@section("reddit")
def reddit():
    out = {}
    for sub in CFG.get("subreddits", ["wallstreetbets", "stocks", "CryptoCurrency"]):
        try:
            rows = _reddit_json(sub)
            if rows is None:
                rows = _reddit_rss(sub)
            posts = []
            for d in rows:
                t = dt.datetime.utcfromtimestamp(d["created_utc"]).strftime("%Y-%m-%dT%H:%M:%SZ") if "created_utc" in d else d.get("created", "")
                link = d.get("permalink", "")
                posts.append({"t": t, "title": d["title"][:300], "score": d.get("score", 0), "com": d.get("num_comments", 0),
                              "url": link if link.startswith("http") else "https://www.reddit.com" + link,
                              "tickers": sorted(set(re.findall(r"\$([A-Z]{2,6})\b", d["title"] + " " + (d.get("selftext") or "")[:2000]))),
                              "tag": tag(d["title"])})
            out[sub] = posts[:20]
            time.sleep(2)
        except Exception as e:
            OUT["errors"][f"reddit:{sub}"] = str(e)[:200]
    return out

def gdelt(query, n=15, days=3):
    time.sleep(7)  # GDELT : 1 requête toutes les 5 s maximum
    q = urllib.parse.quote(query)
    try:
        txt = get(f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords={n}&format=json&timespan={days}d&sort=datedesc")
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        time.sleep(20)
        txt = get(f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist&maxrecords={n}&format=json&timespan={days}d&sort=datedesc")
    j = json.loads(txt)
    return [{"t": a.get("seendate"), "title": a.get("title"), "url": a.get("url"), "src": a.get("domain"), "lang": a.get("language")} for a in j.get("articles", [])]

@section("risks")
def risks():
    return gdelt('("financial crisis" OR "bank run" OR "debt default" OR "new sanctions" OR invasion OR "missile strike" OR "market crash" OR "emergency rate cut" OR cyberattack OR "trade war") sourcelang:english', 20, 2)

def gnews(q, n=12):
    x = get("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=fr&gl=FR&ceid=FR:fr")
    out = []
    for it in ET.fromstring(x).iter("item"):
        src = it.find("source")
        out.append({"t": it.findtext("pubDate"), "title": it.findtext("title"), "url": it.findtext("link"), "src": src.text if src is not None else "Google Actualités"})
        if len(out) >= n:
            break
    return out

@section("kidnap_fr")
def kidnap():
    return gnews('(enlèvement OR séquestration OR rapt OR kidnapping) (crypto OR bitcoin OR cryptomonnaie) when:30d')

@section("fed")
def fed():
    from zoneinfo import ZoneInfo
    j = json.loads(get("https://www.federalreserve.gov/json/calendar.json"))
    ev = j.get("events", j if isinstance(j, list) else [])
    out = []
    for e in ev:
        title = e.get("title") or ""
        if not re.search(r"FOMC|Chair|Powell|Speech|Testimony|Minutes|Beige", title, re.I):
            continue
        days = [int(x) for x in re.findall(r"\d+", str(e.get("days", "")))]
        if not days or not e.get("month"):
            continue
        day = days[-1] if re.search(r"FOMC", title) else days[0]
        h, m = 14, 0
        tm = re.match(r"(\d{1,2}):(\d{2})\s*([ap])", str(e.get("time", "")).lower())
        if tm:
            h, m = int(tm.group(1)) % 12 + (12 if tm.group(3) == "p" else 0), int(tm.group(2))
        try:
            y, mo = map(int, e["month"].split("-"))
            ts = dt.datetime(y, mo, day, h, m, tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)
        except Exception:
            continue
        out.append({"title": title, "when": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": e.get("type"), "desc": (e.get("description") or "")[:160]})
    # inflation US (CPI) : calendrier officiel du BLS, 8 h 30 heure de New York
    CPI = ["2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13", "2027-05-12", "2027-06-10",
           "2027-07-14", "2027-08-11", "2027-09-14", "2027-10-14", "2027-11-10", "2027-12-10"]
    for d in CPI:
        y, mo, da = map(int, d.split("-"))
        ts = dt.datetime(y, mo, da, 8, 30, tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)
        out.append({"title": "CPI – inflation US", "when": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "CPI", "desc": "Chiffre mensuel de l'inflation (BLS)"})
    out.sort(key=lambda x: x["when"])
    now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return [x for x in out if x["when"] >= now[:10]][:40]

@section("firsts")
def firsts():
    """Mémoire longue des premiers calls : pour chaque token, qui en a parlé le premier et quand, gardé 1 an
    (les tweets eux-mêmes ne sont gardés que 30 jours). Permet de repérer celui qui était early, même des semaines avant la foule."""
    path = "data/first_calls.json"
    try:
        M = json.load(open(path, encoding="utf-8"))
    except Exception:
        M = {}
    cp = OUT.get("callpx") or {}
    tk = OUT.get("tokens") or {}
    for t in OUT.get("influ") or []:
        if t.get("rt") or not t.get("t"):
            continue
        tks = t.get("tickers") or []
        if len(tks) > 4:
            continue
        for T in tks:
            e = M.get(T)
            if e is None:
                e = M[T] = {"h": t["h"], "t": t["t"], "url": t.get("url"), "acc": {}}
            if t["t"] < e["t"]:
                e.update({"h": t["h"], "t": t["t"], "url": t.get("url")})
                e.pop("px", None); e.pop("mc", None)
            a = e.setdefault("acc", {})
            if t["h"] not in a or t["t"] < a[t["h"]]:
                if len(a) < 40 or t["h"] in a:
                    a[t["h"]] = t["t"]
            if "px" not in e and e.get("url"):
                p = ((cp.get(T) or {}).get("calls") or {}).get(e["url"])
                if p and p.get("px"):
                    e["px"] = p["px"]
                    if p.get("mc"):
                        e["mc"] = p["mc"]
            if not e.get("chain") and (tk.get(T) or {}).get("chain"):
                e["chain"] = tk[T]["chain"]
    lim = (dt.datetime.utcnow() - dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    M = {k: v for k, v in M.items() if v["t"] >= lim or len(v.get("acc", {})) >= 3}
    json.dump(M, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # classement d'influence sur toute la mémoire (1 an), pas seulement les 30 jours de tweets :
    # sur les tokens où il est parmi les 3 premiers, combien de comptes en parlent dans les 48 h qui suivent
    try:
        big = {r[0] for r in (OUT.get("top") or []) if (r[1] or 9999) <= 100}
        big |= {str(x).upper().lstrip("$") for x in (CFG.get("tok_hidden") or [])}  # tokens masqués : hors classement
        # comme le dashboard : on écarte aussi les actions/matières cotées sur Hyperliquid et les tokens plus gros que le 100e
        try:
            body = json.dumps({"type": "meta", "dex": "xyz"}).encode()
            req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=body, headers={**UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                big |= {u["name"].split(":")[-1].upper() for u in json.loads(r.read()).get("universe", [])}
        except Exception:
            pass
        thr = next((r[2] for r in (OUT.get("top") or []) if r[1] == 100), None)
        if thr:
            big |= {k for k, v in (OUT.get("tokens") or {}).items() if (v.get("mcap") or 0) >= thr and not v.get("byca")}
        P = lambda s: dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
        I, calls = {}, {}
        for T, e in M.items():
            if T in big:
                continue
            a = sorted(((h, P(t)) for h, t in (e.get("acc") or {}).items()), key=lambda x: x[1])
            for h, _ in a:
                calls[h] = calls.get(h, 0) + 1
            if len(a) < 2:
                continue
            for i, (h, t0) in enumerate(a[:3]):
                fl = [x for x in a[i + 1:] if 120 <= x[1] - t0 <= 48 * 3600]
                q = I.setdefault(h, {"acc": set(), "tok": 0, "rep": 0, "d1": [], "toks": []})
                q["tok"] += 1
                q["acc"].update(x[0] for x in fl)
                if len(fl) >= 2:
                    q["rep"] += 1
                if fl:
                    q["d1"].append(fl[0][1] - t0)
                    q["toks"].append(T)
        R = []
        for h, q in I.items():
            n = len(q["acc"])
            cl = max(calls.get(h, 0), q["tok"])
            if n < 2 or cl < 2:
                continue
            d = sorted(q["d1"])
            R.append({"h": h, "n": n, "rep": q["rep"], "calls": cl, "md": d[len(d) // 2] * 1000 if d else None,
                      "toks": q["toks"][-6:], "score": round((n + 4 * q["rep"]) * (0.5 + q["rep"] / cl), 2)})
        R.sort(key=lambda r: -r["score"])
        OUT["influence"] = R[:40]
    except Exception as ex:
        OUT["errors"]["influence"] = f"{type(ex).__name__}: {ex}"[:200]
    # dans le feed : seulement les tokens cités ces 30 derniers jours (le fichier complet reste dans data/)
    cur = {T for t in (OUT.get("influ") or []) for T in (t.get("tickers") or [])}
    return {k: {"h": v["h"], "t": v["t"], "url": v.get("url"), "px": v.get("px"), "mc": v.get("mc"), "n": len(v.get("acc", {})),
                "acc": sorted(v.get("acc", {}).items(), key=lambda x: x[1])[:12]} for k, v in M.items() if k in cur}

@section("callstats")
def callstats():
    """Qui fait les meilleurs call : résultat de chaque call (24 h, 3 j, 7 j, plus haut atteint), gardé au-delà des 30 jours de tweets
    dans data/perf/AAAA-MM.json (un fichier par mois), puis classement par influenceur."""
    cp = OUT.get("callpx") or {}
    byurl = {t.get("url"): t for t in (OUT.get("influ") or [])}
    big = {r[0] for r in (OUT.get("top") or []) if (r[1] or 9999) <= 100}
    os.makedirs("data/perf", exist_ok=True)
    months = {}
    def load(m):
        if m not in months:
            try:
                months[m] = json.load(open(f"data/perf/{m}.json", encoding="utf-8"))
            except Exception:
                months[m] = {}
        return months[m]
    changed = set()
    for T, v in cp.items():
        for url, r in (v.get("calls") or {}).items():
            t = byurl.get(url)
            if not t or "r24h" not in r:
                continue
            side = -1 if t.get("tag") == "bear" else 1
            rec = {"h": t.get("h"), "T": T, "t": t.get("t"), "s": side, "big": 1 if T in big else 0,
                   **{k: r[k] for k in ("r1h", "r24h", "r3d", "r7d", "mx24", "mn24", "mx7d") if k in r}}
            m = (t.get("t") or "")[:7]
            if not m:
                continue
            M = load(m)
            if M.get(url) != rec:
                M[url] = rec
                changed.add(m)
    for m in changed:
        json.dump(months[m], open(f"data/perf/{m}.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    # tableau des 3 derniers mois
    allrec = []
    today = dt.datetime.utcnow()
    for k in range(3):
        y, mo = today.year, today.month - k
        while mo <= 0:
            mo += 12; y -= 1
        allrec += list(load(f"{y:04d}-{mo:02d}").values())
    S = {}
    TH = {str(x).upper().lstrip("$") for x in (CFG.get("tok_hidden") or [])}
    for r in allrec:
        if r.get("big") or r.get("T") in TH:
            continue
        q = S.setdefault(r["h"], {"h": r["h"], "n": 0, "win": 0, "sum24": 0.0, "summx": 0.0, "best": None, "toks": []})
        g = r["r24h"] * r.get("s", 1)
        mx = r.get("mx24") if r.get("s", 1) > 0 else (-(r.get("mn24") or 0))
        q["n"] += 1
        q["win"] += 1 if g > 0 else 0
        q["sum24"] += g
        q["summx"] += mx or 0
        if q["best"] is None or (r.get("mx7d") or -9) > q["best"][1]:
            q["best"] = [r["T"], r.get("mx7d")]
        q["toks"].append(r["T"])
    R = []
    for q in S.values():
        if q["n"] < 3:
            continue
        R.append({"h": q["h"], "n": q["n"], "win": round(q["win"] / q["n"], 3), "avg24": round(q["sum24"] / q["n"], 4),
                  "avgmx24": round(q["summx"] / q["n"], 4), "best": q["best"], "toks": list(dict.fromkeys(q["toks"]))[-6:]})
    R.sort(key=lambda x: -(x["avgmx24"] * 0.6 + x["avg24"] * 0.4) * min(1, x["n"] / 10))
    return {"by": R[:40], "n": len(allrec)}

@section("trends")
def trends():
    """Tendances du moment (remplace Reddit, qui bloque les serveurs GitHub) : StockTwits (actions) et CoinGecko (crypto)."""
    res = {"stocks": [], "crypto": []}
    try:
        j = json.loads(get("https://api.stocktwits.com/api/2/trending/symbols.json", headers={"Accept": "application/json"}))
        for s_ in (j.get("symbols") or [])[:20]:
            res["stocks"].append({"sym": s_.get("symbol"), "name": s_.get("title"), "watch": s_.get("watchlist_count"),
                                  "why": ((s_.get("trends") or {}).get("summary") or "")[:200]})
    except Exception as e:
        OUT["errors"]["trends_stocktwits"] = str(e)[:100]
    try:
        j = json.loads(get("https://api.coingecko.com/api/v3/search/trending", headers={"Accept": "application/json"}))
        for it in (j.get("coins") or [])[:15]:
            x = it.get("item") or {}
            d = x.get("data") or {}
            ch = (d.get("price_change_percentage_24h") or {}).get("usd")
            res["crypto"].append({"sym": (x.get("symbol") or "").upper(), "name": x.get("name"), "rank": x.get("market_cap_rank"),
                                  "img": x.get("small") or x.get("thumb"), "ch24": round(ch, 2) if isinstance(ch, (int, float)) else None})
    except Exception as e:
        OUT["errors"]["trends_coingecko"] = str(e)[:100]
    return res

@section("analyst")
def analyst():
    """Objectifs des analystes (moyenne, médiane, fourchette, nombre d'avis) pour les actions Hyperliquid, relus une fois par jour (Yahoo Finance)."""
    path = "data/analyst.json"
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = {}
    if old.get("at") and (dt.datetime.utcnow() - dt.datetime.strptime(old["at"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() < 20 * 3600:
        return old

    def later(why):
        """Yahoo limite parfois les serveurs GitHub : on garde l'ancienne version et on réessaie dans ~2 h (pas à chaque passage)."""
        back = {"at": (dt.datetime.utcnow() - dt.timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ"), "by": old.get("by") or {}, "fail": why[:80]}
        json.dump(back, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        return back
    import http.cookiejar
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    hd = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
    try:
        op.open(urllib.request.Request("https://fc.yahoo.com", headers=hd), timeout=15)
    except Exception:
        pass  # répond 404 mais pose le cookie
    crumb, host = None, None
    for h_ in ("query2", "query1"):
        try:
            crumb = op.open(urllib.request.Request(f"https://{h_}.finance.yahoo.com/v1/test/getcrumb", headers=hd), timeout=15).read().decode().strip()
            host = h_
            break
        except Exception as e:
            why = f"crumb {h_}: {type(e).__name__} {getattr(e, 'code', '')}"
            time.sleep(2)
    if not crumb:
        return later(why)
    names = []
    try:
        body = json.dumps({"type": "meta", "dex": "xyz"}).encode()
        req = urllib.request.Request("https://api.hyperliquid.xyz/info", data=body, headers={**UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            names = [u["name"].split(":")[-1].upper() for u in json.loads(r.read()).get("universe", [])]
    except Exception:
        pass
    skip = MACROW | {"SP500", "XYZ100", "EUR", "JPY", "GBP", "CL", "BRENTOIL", "NATGAS", "GOLD", "SILVER", "COPPER", "PLATINUM", "URANIUM", "CORN", "WHEAT", "DXY", "VIX", "NDX"}
    names = [n for n in dict.fromkeys(names + (CFG.get("tickers") or [])) if re.fullmatch(r"[A-Z]{1,5}", n) and n not in skip][:70]
    res = {"at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "by": {}}
    for n in names:
        try:
            u = f"https://{host}.finance.yahoo.com/v10/finance/quoteSummary/{n}?modules=financialData,calendarEvents&crumb={urllib.parse.quote(crumb)}"
            j = json.loads(op.open(urllib.request.Request(u, headers=hd), timeout=15).read())
            res0 = (((j.get("quoteSummary") or {}).get("result") or [{}])[0] or {})
            fd = res0.get("financialData") or {}
            ed = (((res0.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or [])
            val = lambda k: (fd.get(k) or {}).get("raw") if isinstance(fd.get(k), dict) else None
            if val("targetMeanPrice"):
                res["by"][n] = {"avg": val("targetMeanPrice"), "med": val("targetMedianPrice"), "lo": val("targetLowPrice"), "hi": val("targetHighPrice"),
                                "n": val("numberOfAnalystOpinions"), "rec": fd.get("recommendationKey"), "px": val("currentPrice"),
                                "earn": (ed[0] or {}).get("raw") if ed and isinstance(ed[0], dict) else None}
            time.sleep(0.6)
        except Exception as e:
            OUT["errors"]["analyst"] = f"{n}: {type(e).__name__} {getattr(e, 'code', '')}"[:100]
    if len(res["by"]) < 5:
        return later(OUT["errors"].get("analyst") or "trop peu de réponses")
    json.dump(res, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    return res

@section("follow")
def follow():
    """Score « À suivre » : déclenche des reprises (influence) ET fait de bons call (résultat à 24 h, plus haut atteint, réussite)."""
    inf = {r["h"].lower(): r for r in (OUT.get("influence") or [])}
    perf = {r["h"].lower(): r for r in ((OUT.get("callstats") or {}).get("by") or [])}
    mi = max([r.get("score") or 0 for r in inf.values()] + [1e-9])
    def pscore(r):
        q = (r.get("avgmx24") or 0) * 0.6 + (r.get("avg24") or 0) * 0.4
        return max(0.0, q) * min(1.0, (r.get("n") or 0) / 8) * (0.5 + (r.get("win") or 0))
    mp = max([pscore(r) for r in perf.values()] + [1e-9])
    R = []
    for k in set(inf) | set(perf):
        a, b = inf.get(k), perf.get(k)
        si = (a.get("score") or 0) / mi if a else 0.0
        sp = pscore(b) / mp if b else 0.0
        R.append({"h": (a or b)["h"], "score": round(100 * (0.55 * si + 0.45 * sp), 1), "si": round(si, 3), "sp": round(sp, 3),
                  "infl": {x: a.get(x) for x in ("n", "rep", "calls", "md", "toks")} if a else None,
                  "perf": {x: b.get(x) for x in ("n", "win", "avg24", "avgmx24", "best", "toks")} if b else None})
    R.sort(key=lambda r: -r["score"])
    return R[:40]

for fn in (x_feed, top, eu, metals, influ, tokens, callpx, firsts, callstats, follow, stocktwits, trump, trends, analyst, risks, kidnap, fed):
    fn()
try:  # projections : gardées du passage précédent tant que proj.py ne les a pas remplacées (le dashboard vérifie leur fraîcheur)
    _pp = json.load(open("data/feed.json", encoding="utf-8")).get("proj")
    if _pp:
        OUT["proj"] = _pp
except Exception:
    pass
os.makedirs("data", exist_ok=True)
json.dump(OUT, open("data/feed.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("ok", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in OUT.items() if k != "generated"})
