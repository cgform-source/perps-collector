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
    if not tok:
        return guest("Mode invité : tweets des comptes suivis seulement. Ajoute X_AUTH_TOKEN et X_CT0 pour la recherche par action.")
    since = (dt.datetime.utcnow() - dt.timedelta(days=2)).strftime("%Y-%m-%d")
    accs, tickers = CFG.get("x_accounts", []), CFG.get("tickers", [])
    tried = []
    for name, fn, needs_ct0 in (("Scweet", _scweet, False), ("twscrape", _twscrape, True), ("twifork", _twifork, True)):
        if needs_ct0 and not ct0:
            tried.append(f"{name}: secret X_CT0 manquant")
            continue
        try:
            acc, tick = fn(tok, accs, tickers, since) if not needs_ct0 else fn(tok, ct0, accs, tickers, since)
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
IBEAR = re.compile(r"\b(short(ed|ing)?|bear(ish)?|dump(ing)?|rug|sell(ing)?|lower|top(ped)?|breakdown|overvalued|rekt|red|crash|exit)\b", re.I)
@section("influ")
def influ():
    try:
        g = json.load(open("data/x_guest.json", encoding="utf-8"))
    except Exception:
        return []
    out = []
    for t in g.get("influ", []):
        txt = t.get("fr", "")
        tk = sorted(set(x.upper() for x in re.findall(r"[$#]([A-Za-z][A-Za-z0-9]{1,9})\b", txt)) - {"BTC.D", "USDT", "USDC", "USD"})
        b, r = bool(IBULL.search(txt)), bool(IBEAR.search(txt))
        t["tickers"] = tk
        t["tag"] = "bull" if b and not r else "bear" if r and not b else "mix"
        out.append(t)
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

for fn in (x_feed, influ, stocktwits, trump, reddit, risks, kidnap, fed):
    fn()
os.makedirs("data", exist_ok=True)
json.dump(OUT, open("data/feed.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("ok", {k: (len(v) if hasattr(v, "__len__") else v) for k, v in OUT.items() if k != "generated"})
