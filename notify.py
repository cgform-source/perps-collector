"""Notifications web push vers l'iPad (et tout appareil abonné dans le dashboard).

Lit data/feed.json (que collect.py vient d'écrire), repère :
  - un call publié par un compte favori (☆ dans le dashboard) ;
  - un token qui chauffe : N influenceurs différents en 2 h (hors grosses cryptos).
Les abonnements et préférences viennent de Supabase (table push_subs).
La clé privée VAPID est un secret GitHub : VAPID_PRIVATE. Sans elle, rien n'est envoyé.
Le texte d'une notification ne contient jamais de prix (anti-spoil).
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse

PRIV = os.environ.get("VAPID_PRIVATE", "").strip()
SBU = "https://lqefewiwrecdjommktgm.supabase.co/rest/v1/"
SBK = "sb_publishable_IMMxd7lqThb0elf0Z2JwNQ_3Da4tdWq"
STATE = "data/notify_state.json"
MAXN = 5  # au plus 5 notifications par passage, pour ne jamais inonder l'iPad


def sb(path, method="GET"):
    req = urllib.request.Request(SBU + path, method=method, headers={"apikey": SBK, "Authorization": "Bearer " + SBK})
    with urllib.request.urlopen(req, timeout=20) as r:
        b = r.read()
        return json.loads(b) if b else None


def paris_hour():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Europe/Paris")).hour
    except Exception:
        return (datetime.datetime.utcnow().hour + 1) % 24


def quiet(p, h):
    if not p.get("quiet", True):
        return False
    a, b = int(p.get("qFrom", 22)), int(p.get("qTo", 8))
    return (a <= h or h < b) if a > b else (a <= h < b)


def main():
    if not PRIV:
        print("notify: pas de secret VAPID_PRIVATE, rien à envoyer")
        return
    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        print("notify: pywebpush absent")
        return
    F = json.load(open("data/feed.json"))
    try:
        S = json.load(open(STATE))
    except Exception:
        S = {}
    first_run = not S.get("init")
    sent = S.get("sent", {})
    now = time.time()
    sent = {k: v for k, v in sent.items() if now - v < 3 * 86400}

    try:
        subs = sb("push_subs?select=endpoint,sub,prefs") or []
    except Exception as e:
        print("notify: lecture des abonnements impossible", e)
        return
    subs = [s for s in subs if (s.get("prefs") or {}).get("on")]
    try:
        vip = {h.lower() for h in ((sb("influ_prefs?id=eq.analyserxxx&select=vip") or [{}])[0].get("vip") or [])}
    except Exception:
        vip = set()

    top = {r[0]: r[1] for r in (F.get("top") or [])}
    def big(T):
        return T in top and top[T] <= 100

    ts = lambda t: datetime.datetime.fromisoformat(t["t"].replace("Z", "+00:00")).timestamp()
    tw = [t for t in (F.get("influ") or []) if not t.get("rt") and t.get("t")]
    events = []
    # 1) call d'un favori (2 dernières heures)
    for t in tw:
        if t["h"].lower() in vip and t.get("tickers") and now - ts(t) < 2 * 3600:
            k = "vip:" + t["url"]
            tk = " ".join("$" + x for x in t["tickers"][:3])
            body = (t.get("fr") or "").replace("&amp;", "&").replace("\n", " ")
            events.append({"k": k, "type": "vip", "title": f"@{t['h']} a call {tk}", "body": body[:140], "tag": k, "url": "/"})
    # 2) token qui chauffe : comptes distincts sur 2 h
    acc = {}
    for t in tw:
        if now - ts(t) > 2 * 3600:
            continue
        tks = t.get("tickers") or []
        if len(tks) > 4:
            continue
        for T in tks:
            if big(T):
                continue
            acc.setdefault(T, {})
            acc[T].setdefault(t["h"], ts(t))
    for T, m in acc.items():
        n = len(m)
        if n >= 2:
            hs = sorted(m, key=lambda h: m[h])
            step = max(x for x in (2, 3, 4, 5, 7, 10, 15, 20) if x <= n)  # paliers : pas une notification à chaque compte de plus
            events.append({"k": f"heat:{T}:{step}", "type": "heat", "n": n, "T": T,
                           "title": f"${T} chauffe : {n} influenceurs en 2 h",
                           "body": "Dans l'ordre : " + ", ".join("@" + h for h in hs[:5]) + ("…" if n > 5 else ""),
                           "tag": "heat:" + T, "url": "/"})

    # 3) déclencheur : un des 5 comptes « À suivre » call un token dont personne n'avait parlé ces 7 derniers jours
    top5 = [r["h"].lower() for r in (F.get("follow") or [])[:5]]
    hidden = set()
    try:
        hidden = {str(x).upper() for x in (json.load(open("config.json")).get("tok_hidden") or [])}
    except Exception:
        pass
    for t in tw:
        if t["h"].lower() not in top5 or now - ts(t) > 2 * 3600:
            continue
        for T in (t.get("tickers") or [])[:3]:
            if big(T) or T in hidden:
                continue
            t0 = ts(t)
            before = [u for u in tw if T in (u.get("tickers") or []) and t0 - 7 * 86400 <= ts(u) < t0 - 60]
            if before:
                continue
            rk = [r["h"].lower() for r in (F.get("follow") or [])].index(t["h"].lower()) + 1
            events.append({"k": "trig:" + t["url"] + ":" + T, "type": "trig", "title": f"@{t['h']} (n°{rk} à suivre) call ${T}",
                           "body": "Nouveau token, personne n'en parlait avant lui. " + (t.get("fr") or "").replace("\n", " ")[:110], "tag": "trig:" + T, "url": "/"})
    new = [e for e in events if e["k"] not in sent]
    for e in new:
        sent[e["k"]] = now
    S = {"init": True, "sent": sent, "at": datetime.datetime.utcnow().isoformat() + "Z"}
    if first_run:
        print(f"notify: premier passage, {len(new)} évènements mémorisés sans envoi")
        json.dump(S, open(STATE, "w"))
        return
    h = paris_hour()
    count = 0
    for s in subs:
        p = s.get("prefs") or {}
        if quiet(p, h):
            continue
        mine = [e for e in new if (e["type"] == "trig" and p.get("trig", True)) or (e["type"] == "vip" and p.get("vip", True)) or
                (e["type"] == "heat" and p.get("heat", True) and e["n"] >= int(p.get("heatMin", 3)))]
        # pour un même token qui chauffe, une seule notification (le palier le plus haut)
        best = {}
        for e in mine:
            key = e.get("T") or e["k"]
            if key not in best or e.get("n", 0) > best[key].get("n", 0):
                best[key] = e
        for e in list(best.values())[:MAXN]:
            try:
                webpush(subscription_info=s["sub"], data=json.dumps({k: e[k] for k in ("title", "body", "tag", "url")}),
                        vapid_private_key=PRIV, vapid_claims={"sub": "mailto:collecteur@users.noreply.github.com"}, ttl=3600)
                count += 1
            except WebPushException as ex:
                code = getattr(ex.response, "status_code", None)
                print("notify: échec", code)
                if code in (404, 410):
                    try:
                        sb("push_subs?endpoint=eq." + urllib.parse.quote(s["endpoint"], safe=""), "DELETE")
                    except Exception:
                        pass
                    break
            except Exception as ex:
                print("notify: erreur", type(ex).__name__)
    json.dump(S, open(STATE, "w"))
    print(f"notify: {len(new)} nouveaux évènements, {count} notifications envoyées à {len(subs)} appareil(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("notify: erreur générale", type(e).__name__, e)
