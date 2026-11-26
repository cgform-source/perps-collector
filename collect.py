"""Collecteur gratuit pour le dashboard Perps actions HL.
Tourne sur GitHub Actions (gratuit) 3 fois par jour et écrit data/feed.json.
Sources : X/Twitter (via Scweet + cookie d'un compte X secondaire), Truth Social de Trump (archive trumpstruth.org),
Reddit, actualités mondiales à risque (GDELT), enlèvements liés à la crypto en France (GDELT), calendrier de la Fed.
Chaque source est indépendante : si l'une échoue, les autres sont quand même écrites."""
import json, os, urllib.error, re, time, datetime as dt, traceback, urllib.request, urllib.parse, xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (compatible; perps-dashboard-collector/1.0)"}
OUT = {"generated": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "errors": {}}
CFG = json.load(open("config.json", encoding="utf-8"))

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
                        "url": f"https://stocktwits.com/{m['user']['username']}/message/{m['id']}", "fr": m["body"][:400],
                        "tag": {"Bullish": "bull", "Bearish": "bear"}.get(((m.get("entities") or {}).get("sentiment") or {}).get("basic"), "mix")} for m in j.get("messages", [])[:15]]
            time.sleep(1)
        except Exception as e:
            OUT["errors"][f"stocktwits:{tk}"] = str(e)[:120]
    return out

IBULL = re.compile(r"\b(long|longed|longing|bull(ish)?|moon|pump(ing)?|send(ing)?|breakout|higher|ath|buy(ing)?|bid|accumulat\w*|undervalued|reversal|bottom(ed)?|rip|green)\b", re.I)
IBEAR = re.compile(r"\b(short(s|ed|ing)?|bear(ish)?|dump(s|ed|ing)?|rug(ged)?|sell(s|ing)?|sold|lower|top(ped)? is in|breakdown|overvalued|rekt|crash(es|ed|ing)?|exit|slam(s|med)?|down(side)?|drop(s|ped|ping)?|fall(s|ing)?|nuke[ds]?|fade|fading|puts?|tank(s|ed|ing)?|bleed(ing)?|capitulat\w*|collapse[ds]?)\b", re.I)
TALIAS = {"MATIC": "POL", "WBTC": "BTC", "WETH": "ETH", "XBT": "BTC"}
def norm_tk(L):
    """$ETHUSD -> ETH, $HYPEBTC -> HYPE + BTC, $MATIC -> POL (nom actuel)."""
    known = {r[0] for r in (OUT.get("top") or [])} | {"BTC", "ETH", "SOL", "HYPE"}
    out = []
    for t in L:
        t = TALIAS.get(t, t)
        m = re.match(r"^([A-Z0-9]{2,10}?)(USDT|USDC|USDE|USD|PERP)$", t)
        if m and (m.group(1) in known or len(m.group(1)) >= 3):
            out.append(TALIAS.get(m.group(1), m.group(1)))
            continue
        if t not in known and len(t) >= 5:
            for i in range(2, len(t) - 1):
                if t[:i] in known and t[i:] in known:
                    out += [t[:i], t[i:]]
                    break
            else:
                out.append(t)
            continue
        out.append(t)
    return sorted(set(out))

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
    for t in g.get("influ", []):
        pr = prof.get((t.get("h") or "").lower()) or {}
        t["fol"] = pr.get("fol", 0)
        txt = t.get("fr", "")
        tk = norm_tk(set(x.upper() for x in re.findall(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b", txt)) - {"BTC.D", "USDT", "USDC", "USD"})
        b, r = bool(IBULL.search(txt)), bool(IBEAR.search(txt))
        t["tickers"] = tk
        t["tag"] = "bull" if b and not r else "bear" if r and not b else "mix"
        out.append(t)
    return out

SKIPT = {"BTC.D", "USD", "USDT", "USDC", "SPX", "DXY", "ETF", "CPI", "FOMC", "AI", "CT", "ATH", "TA", "NFA", "DYOR", "GM"}
@section("top")
def top():
    """Classement CoinGecko (top 250) pour masquer les grosses capitalisations dans l'onglet Influenceurs."""
    j = json.loads(get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1", headers={"Accept": "application/json"}))
    return [[(x.get("symbol") or "").upper(), x.get("market_cap_rank"), x.get("market_cap")] for x in j if x.get("symbol")]

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
                res[t["url"]] = {"px": px, "mc": supply * px if supply else None}
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
        txt = re.sub(r"\s+", " ", desc or title).strip()
        if not txt or txt.lower().startswith("[no title]") and len(txt) < 20:
            continue
        items.append({"t": it.findtext("pubDate"), "fr": txt[:600], "url": it.findtext("link"), "tag": tag(txt),
                      "market": bool(re.search(r"tariff|china|fed|rate|stock|market|crypto|bitcoin|oil|deal|trade|dollar|powell|inflation|tax", txt, re.I))})
        if len(items) >= 25:
            break
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
    out.sort(key=lambda x: x["when"])
    now = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return [x for x in out if x["when"] >= now[:10]][:40]

for fn in (x_feed, top, eu, metals, influ, tokens, callpx, stocktwits, trump, reddit, risks, kidnap, fed):
    fn()
os.makedirs("data", exist_ok=True)
json.dump(OUT, open("data/feed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in OUT.items() if k != "generated"})
