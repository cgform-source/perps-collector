"""Alertes graves : repère les infos vraiment dangereuses et les sépare des simples mauvaises nouvelles.

Tourne après proj.py et avant notify.py (GitHub Actions, gratuit). Lit data/feed.json et :
  - classe chaque info récente (comptes d'actualité X, StockTwits, influenceurs, Trump, presse GDELT) :
      niveau 3 « ALERTE GRAVE » : évènement grave (puces exportées illégalement, fraude, poursuites pénales,
        suspension de cotation, faillite, piratage, sanctions, guerre, rachat surprise…) confirmé par une source
        fiable (Bloomberg, Reuters, comptes d'agence comme DeItaone…) ou par plusieurs sources ; ou mauvaise
        nouvelle sérieuse suivie d'une réaction violente du prix ;
      niveau 2 « à surveiller » : mauvaise nouvelle sérieuse (enquête, procès, objectifs abaissés…) de source fiable ;
  - regroupe les infos d'une même histoire (même actif, même type d'évènement) et compte les sources ;
  - vérifie la réaction du prix (bougies 1 h Hyperliquid) ;
  - écrit F["danger"] et met les alertes graves en avant dans ce que le dashboard affiche déjà :
    liste des tweets de l'actif, « Risques mondiaux », verdict et graphique ;
  - garde une mémoire dans data/danger_state.json (une info sortie du flux reste une alerte pendant 12 h).
Aucun texte d'alerte ne contient de prix ni de pourcentage (anti-spoil).
"""
import json, os, re, sys, time, hashlib, datetime as dt, urllib.request, urllib.parse

FEED = os.environ.get("DANGER_FEED", "data/feed.json")
STATE = os.environ.get("DANGER_STATE", "data/danger_state.json")
NOW = int(float(os.environ.get("DANGER_NOW") or time.time()) * 1000)
OFFLINE = os.environ.get("DANGER_OFFLINE") == "1"   # tests : ni traduction ni bougies en ligne
CANDLES_DIR = os.environ.get("DANGER_CANDLES") or ""  # tests : bougies lues dans des fichiers
H = 3600000
WIN = 18 * H      # infos lues : 18 dernières heures
ACTIVE = 12 * H   # une alerte reste « en cours » 12 h après sa dernière info
KEEP = 72 * H     # mémoire : 3 jours
MARK = "⚠︎"
I = re.I

# ---------------------------------------------------------------- actifs suivis et leurs autres noms
AL_I = {
    "NVDA": r"nvidia|blackwell|jensen\s+huang", "AAPL": r"\biphones?\b|tim\s+cook", "MSFT": r"microsoft|satya\s+nadella|\bazure\b",
    "AMZN": r"\bamazon\b|\baws\b|andy\s+jassy", "GOOGL": r"\bgoogle\b|\balphabet\b|\bwaymo\b|sundar\s+pichai",
    "META": r"\bfacebook\b|\binstagram\b|\bwhatsapp\b|zuckerberg", "TSLA": r"\btesla\b|cybertruck|robotaxi",
    "AMD": r"advanced\s+micro\s+devices|\blisa\s+su\b", "PLTR": r"palantir", "NFLX": r"netflix", "COIN": r"coinbase",
    "MSTR": r"microstrategy|\bsaylor\b", "HOOD": r"robinhood", "ORCL": r"larry\s+ellison", "AVGO": r"broadcom",
    "COST": r"\bcostco\b", "MU": r"\bmicron\b", "INTC": r"\bintel\s+(?:corp|shares?|stock|chips?|foundry)", "UBER": r"\buber\b",
    "SHOP": r"shopify", "BABA": r"alibaba", "CRWV": r"coreweave", "NBIS": r"nebius", "SNOW": r"snowflake\s+(?:inc|shares?|stock|earnings)",
    "SNDK": r"sandisk", "BTC": r"\bbitcoin\b|\bbtc\b", "ETH": r"\bethereum\b|\bether\b", "SOL": r"\bsolana\b",
    "HYPE": r"hyperliquid", "XRP": r"\bxrp\b|\bripple\b", "DOGE": r"dogecoin", "BNB": r"\bbnb\b|\bbinance\b",
    "SUI": r"\bsui\s+(?:network|blockchain|token)", "LINK": r"chainlink", "AVAX": r"avalanche\s+(?:network|blockchain|avax)",
    "GOLD": r"\bgold\s+(?:price|prices|futures)\b|\bxau\b", "SILVER": r"\bsilver\s+(?:price|prices|futures)\b|\bxag\b",
    "CL": r"\bwti\b|crude\s+oil|oil\s+prices?", "BRENTOIL": r"\bbrent\b", "NATGAS": r"natural\s+gas", "COPPER": r"\bcopper\b",
    "SP500": r"s&p\s*500|\bspx\b", "XYZ100": r"\bnasdaq\b|\bndx\b",
}
AL_C = {"AAPL": r"\bApple\b", "META": r"\bMeta\b", "AMD": r"\bAMD\b", "ORCL": r"\bOracle\b", "INTC": r"\bIntel\b",
        "SNOW": r"\bSnowflake\b", "ETH": r"\bETH\b", "SOL": r"\bSOL\b"}
RX_I = {k: re.compile(v, I) for k, v in AL_I.items()}
RX_C = {k: re.compile(v) for k, v in AL_C.items()}
CRYPTO = {"BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "BNB", "SUI", "LINK", "AVAX"}
INDEX = {"SP500", "XYZ100"}

# ---------------------------------------------------------------- sources
NEWS = {"deitaone", "walterbloomberg", "firstsquawk", "livesquawk", "newsquawk", "financialjuice", "business", "bloomberg",
        "reuters", "wsj", "ft", "financialtimes", "cnbc", "cnbcnow", "apnews", "ap", "axios", "markets", "breakingnews"}
OFFICIAL = {"realdonaldtrump", "whitehouse", "potus", "federalreserve", "sec_news"}
RELAY = {"kobeissiletter", "zerohedge", "unusual_whales", "watcherguru", "spectatorindex", "mr_derivatives", "elonmusk",
         "theroaringkitty", "tier10k", "db"}
CRED_DOM = {"reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "apnews.com", "nytimes.com", "washingtonpost.com",
            "bbc.co.uk", "bbc.com", "theguardian.com", "aljazeera.com", "france24.com", "lemonde.fr", "lefigaro.fr", "lesechos.fr",
            "scmp.com", "asia.nikkei.com", "nikkei.com", "politico.com", "axios.com", "theinformation.com", "marketwatch.com",
            "barrons.com", "economist.com", "dw.com", "cnn.com", "nbcnews.com", "cbsnews.com", "npr.org", "francetvinfo.fr",
            "bfmtv.com", "latribune.fr", "coindesk.com", "theblock.co", "afp.com"}
OUTLETS = r"bloomberg|reuters|wsj|wall\s+street\s+journal|financial\s+times|\bft\b|cnbc|the\s+information|nikkei|axios|associated\s+press|new\s+york\s+times|politico|scmp|caixin|washington\s+post"
CITE = re.compile(r"(?:according\s+to|\bper\b|\bvia\b|citing|reported\s+by|reports?\s+from|told|selon)\s+(?:the\s+|le\s+|la\s+)?(" + OUTLETS +
                  r")|\b(" + OUTLETS + r")\s+(?:reports?|reported|says|said|claims?|news|citing|first\s+reported|rapporte|affirme)|\((" + OUTLETS + r")\)"
                  r"|[:\-–]\s*(" + OUTLETS + r")\s*$", I | re.M)
OUT_NAME = [("bloomberg", "Bloomberg"), ("reuters", "Reuters"), ("wsj", "WSJ"), ("wall street journal", "WSJ"), ("financial times", "FT"), ("ft", "FT"),
            ("cnbc", "CNBC"), ("the information", "The Information"), ("nikkei", "Nikkei"), ("axios", "Axios"), ("associated press", "AP"),
            ("new york times", "NYT"), ("politico", "Politico"), ("scmp", "SCMP"), ("caixin", "Caixin"), ("washington post", "Washington Post")]

# ---------------------------------------------------------------- types d'évènements
WS = r"(?:[\w'’.-]+\s+)"
CTX_EXP = re.compile(r"china|chinese|shanghai|beijing|shenzhen|hong\s+kong|russia|iran|north\s+korea|restricted|banned|illegal|blacklist|chine|chinois|russie", I)
CTX_HACK = re.compile(r"exchange|protocol|bridge|wallet|funds|million|billion|\$\s?\d|tokens?|users?|smart\s+contract|cold\s+storage|hot\s+wallet|plateforme|portefeuille|fonds", I)
CTX_BIGJ = re.compile(r"\b(?:us|u\.s\.|united\s+states|america|china|chinese|eu|european|sec|uk|japan|korea|india)\b|[ée]tats-unis|europ", I)
# (clé, famille, gravité, sens pour l'actif, [(motif, contexte obligatoire)])
CATS = [
    ("export", "export", 3, -1, [
        (r"smuggl\w*|contrebande", None),
        (r"(?:chips?|gpus?|hardware|servers?|processors?|accelerators?|puces?)\W+(?:[\w'’-]+\W+){0,4}?(?:found|surfac\w*|turn(?:s|ed)?\s+up|discovered|installed|diverted|smuggled|retrouv\w*|d[ée]couv\w*)", CTX_EXP),
        (r"(?:installed|smuggled|diverted|acquired|obtained|deployed)\s+" + WS + r"{0,4}?(?:restricted|banned|high-end|advanced|blackwell|h100|h200|h20)\s+" + WS + r"{0,2}?(?:chips?|gpus?|hardware|servers?|processors?)", CTX_EXP),
        (r"in\s+violation\s+of\s+(?:u\.?s\.?\s+)?(?:export|sanctions?)|evad(?:e|ed|es|ing)\s+(?:u\.?s\.?\s+)?(?:export|sanctions?)|export[- ]control\s+(?:violations?|breach\w*|evasion)", None),
    ]),
    ("export_pol", "export", 2, -1, [
        (r"export\s+(?:controls?|restrictions?|ban|curbs?|licen[cs]es?|rules?)|restricted\s+" + WS + r"{0,2}(?:chips?|gpus?)|entity\s+list|contr[ôo]les?\s+(?:à|aux|des)\s+(?:l'|les\s+)?exportations?"
         r"|halt(?:s|ed|ing)?\s+" + WS + r"{0,4}?shipments?|stop(?:s|ped)?\s+(?:delivering|shipping)\s+" + WS + r"{0,3}?(?:servers?|chips?|gpus?)", None),
    ]),
    ("fraud", "legal", 3, -1, [
        (r"\bindict(?:ed|ment|s)?\b|criminal\s+(?:charges?|probe|investigation|case|complaint|referral|inquiry)|charged\s+with|securities\s+fraud"
         r"|accounting\s+(?:fraud|irregularit\w*|scandal|probe)|restat(?:e|es|ed|ement|ing)\s+(?:of\s+)?(?:its\s+)?(?:earnings|results|financials|financial\s+statements|revenue)"
         r"|money\s+laundering\s+(?:charges?|probe|case|investigation|concern)|(?:charged|accused|sued|alleg\w*)\s+" + WS + r"{0,5}?ponzi|ponzi\s+scheme\s+(?:charges?|case|probe)|embezzl\w*|insider\s+trading\s+(?:charges?|probe|case)|whistleblower"
         r"|(?:accus\w*|alleg\w*|charg\w*|sued?\s+for|probe|investigat\w*)\s+" + WS + r"{0,4}?fraud|fraud\s+(?:allegations?|charges?|probe|investigation|case|scheme)"
         r"|short[- ]seller\s+(?:report|alleg\w*|target\w*)"
         r"|(?:\bdoj\b|department\s+of\s+justice|justice\s+department|\bsec\b|\bftc\b|\bfbi\b|\bcftc\b)\s+" + WS + r"{0,2}?(?:sues|sued|charges?|charged|raid\w*|indict\w*|opens?\s+(?:a\s+)?(?:criminal\s+)?(?:probe|investigation))"
         r"|\braid(?:ed|s)?\s+" + WS + r"{0,3}?(?:offices?|headquarters|hq)|(?:ceo|cfo|founder|executives?)\s+" + WS + r"{0,2}?arrested"
         r"|mise\s+en\s+examen|inculp\w*|perquisition|garde\s+à\s+vue|enqu[êe]te\s+p[ée]nale", None),
    ]),
    ("halt", "halt", 3, -1, [
        (r"trading\s+(?:halt(?:ed)?|suspended|paused)|halt(?:s|ed)?\s+(?:all\s+)?trading|\bdelist(?:ed|ing|s)?\b|chapter\s+(?:11|7)\b|going[- ]concern"
         r"|(?:files?|filed|filing)\s+for\s+(?:chapter\s+(?:11|7)\s+)?(?:bankruptcy|insolvency|creditor\s+protection)|bankruptcy\s+(?:filing|protection|proceedings?)|declares?\s+(?:itself\s+)?bankrupt(?:cy)?|insolvency\s+proceedings?"
         r"|(?:withdrawals?|deposits?|redemptions?)\s+" + WS + r"{0,2}?(?:paused|halted|suspended|frozen|disabled)|bank\s+run"
         r"|(?:halts?|halted|pauses?|paused|suspends?|suspended|freezes?|froze|frozen|disables?|disabled)\s+(?:all\s+)?(?:withdrawals?|deposits?|redemptions?)"
         r"|defaults?\s+on\s+(?:its\s+)?(?:debt|bonds?|loans?|payments?)|missed\s+(?:a\s+)?(?:debt|bond|interest|coupon)\s+payment"
         r"|(?:d[ée]pose|placée?|plac[ée]s?)\s+(?:le\s+bilan|en\s+faillite|en\s+redressement)|suspension\s+de\s+(?:la\s+)?cotation|cotation\s+suspendue|retraits?\s+(?:[\w'’-]+\s+)?(?:bloqu|suspendu|gel)\w*|d[ée]faut\s+de\s+paiement", None),
    ]),
    ("hack", "hack", 3, -1, [
        (r"\b(?:hack(?:ed|s)?|hackers?|exploit(?:ed|s)?|drain(?:ed|s)?|compromised|stolen|theft)\b", CTX_HACK),
        (r"pirat(?:age|é|ée|és)\b|faille\s+(?:de\s+s[ée]curit[ée]|exploit\w*)|vol\s+de\s+(?:fonds|cryptos?)", None),
    ]),
    ("rug", "hack", 3, -1, [
        (r"rug\s*-?\s*pull(?:ed)?\b(?!\s*\?)|\bde-?pegg?(?:ed|s|ing)\b|lost\s+(?:its\s+)?peg|perd\s+(?:sa\s+)?parit[ée]", None),
    ]),
    ("ban", "ban", 3, -1, [
        (r"\bsanction(?:s|ed|ing)?\s+" + WS + r"{0,3}?(?:on|against)\b|\bban(?:s|ned|ning)?\s+" + WS + r"{0,3}?(?:sales?|exports?|imports?|trading|purchases?|chips?|crypto|operations?)\b"
         r"|\bnationali[sz]\w*|seiz(?:e|ed|es|ure|ing)\s+" + WS + r"{0,3}?(?:assets?|funds?|bitcoin|crypto|servers?|domains?)\b"
         r"|interdi(?:t|ction)\s+" + WS + r"{0,3}?(?:ventes?|exportations?|importations?|crypto)|sanctions?\s+(?:contre|visant)", None),
    ]),
    ("takeover", "takeover", 3, 1, [
        (r"\bto\s+be\s+(?:acquired|bought|taken\s+private)\b|(?:takeover|buyout|acquisition)\s+(?:bid|offer|proposal)\s+(?:for|to\s+buy)"
         r"|tender\s+offer\s+for|offre\s+publique\s+d'achat|\bOPA\s+sur", None),
    ]),
    ("guidance", "earnings", 2, -1, [
        (r"(?:cuts?|lowers?|lowered|slash(?:es|ed)?|reduc(?:es|ed)|withdraws?|withdrew|pulls?|pulled|suspends?)\s+(?:its\s+|the\s+|full[- ]year\s+|annual\s+|quarterly\s+|q[1-4]\s+)?(?:guidance|forecast|outlook|dividend)"
         r"|profit\s+warning|misses?\s+" + WS + r"{0,2}?(?:estimates|expectations|consensus)|(?:revenue|sales|earnings|profit)\s+(?:miss|plunge|collapse|warning)"
         r"|\bq[1-4]\s+(?:earnings\s+|results\s+)?miss\b|(?:earnings|eps|results)\s+miss(?:es|ed)?\b"
         r"|downgrade[sd]?\s+" + WS + r"{0,3}?to\s+(?:sell|underperform|underweight|reduce)|abaisse\s+(?:ses\s+)?(?:pr[ée]visions|objectifs)"
         r"|avertissement\s+sur\s+(?:les\s+)?r[ée]sultats|d[ée]grad[ée]\s+[àa]\s+(?:vendre|sous-performance)", None),
    ]),
    ("legal", "legal", 2, -1, [
        (r"\b(?:probe|investigation|investigating|antitrust|lawsuit|sued|sues|class[- ]action|fined|penalt(?:y|ies)|scrutiny|recall(?:s|ed|ing)?)\b|enqu[êe]te|poursuites?\s+judiciaires?|amende\b|proc[èe]s\b", None),
    ]),
    ("mgmt", "mgmt", 2, -1, [
        (r"(?:ceo|cfo|chief\s+executive|founder|co-founder|chairman)\s+" + WS + r"{0,3}?(?:resigns?|resigned|steps?\s+down|stepping\s+down|ousted|fired|departs?|departure|leaving|dies|died|passed\s+away|hospitali[sz]ed)", None),
    ]),
    ("macro", "macro", 3, -1, [
        (r"declar(?:es|ed|ing)?\s+war\b|declaration\s+of\s+war|(?:missile|drone|air)\s*strikes?\s+(?:on|against|hit\w*)\b|(?:launch(?:es|ed)?|fires?|fired)\s+" + WS + r"{0,2}?(?:ballistic\s+)?missiles?\s+(?:at|on|toward|towards|into)"
         r"|\binvad(?:es|ed|ing)\b|launch(?:es|ed)?\s+(?:an?\s+)?invasion|nuclear\s+(?:strike|attack|war)|martial\s+law|\bcoup\s+(?:attempt|d'?[ée]tat)|assassinat(?:ed|ion|e)\b|terror(?:ist)?\s+attack"
         r"|(?:attack(?:s|ed)?|strikes?|struck|hit(?:s)?|torch(?:es|ed)?|bomb(?:s|ed)?|set\s+(?:ablaze|on\s+fire))\s+(?:on\s+)?" + WS + r"{0,3}?(?:refiner(?:y|ies)|pipelines?|tankers?|oil\s+(?:facilit\w*|field\w*|terminal\w*)|military\s+base|embassy)"
         r"|tankers?\s+(?:hits?|struck\s+by|strikes?)\s+(?:a\s+)?(?:sea\s+)?mine"
         r"|(?:strait\s+of\s+hormuz|hormuz\s+strait)\s+" + WS + r"{0,3}?(?:closed|closure|blocked|shut|mined)|(?:close[sd]?|closing|block(?:s|ed|ade)|shut(?:s|ting)?)\s+" + WS + r"{0,3}?(?:strait\s+of\s+hormuz|hormuz\s+strait)"
         r"|emergency\s+(?:rate\s+(?:cut|hike)|fed\s+meeting|meeting\s+of\s+the\s+fed)|circuit\s+breakers?\s+(?:triggered|tripped|hit|halt\w*)|market[- ]wide\s+(?:trading\s+)?halt"
         r"|flash\s+crash|\bbank\s+(?:collapse|failure|failed)|sovereign\s+default"
         r"|d[ée]clare\s+la\s+guerre|frappes?\s+(?:a[ée]riennes?|de\s+missiles?)\s+(?:sur|contre)|loi\s+martiale|coup\s+d'[ée]tat|attentat|d[ée]troit\s+d'ormuz\s+" + WS + r"{0,3}?(?:ferm|bloqu)\w*", None),
    ]),
]
CATS = [(k, fam, g, d, [(re.compile(p, I), c) for p, c in pats]) for k, fam, g, d, pats in CATS]
CAT_FR = {"export": "exportations illégales / puces restreintes", "export_pol": "contrôles à l'exportation", "fraud": "fraude ou poursuites pénales",
          "halt": "suspension de cotation, faillite ou retraits bloqués", "hack": "piratage", "rug": "effondrement ou perte d'ancrage", "ban": "interdiction ou sanctions",
          "takeover": "rachat annoncé (hausse brutale possible)", "guidance": "objectifs abaissés / résultats décevants", "legal": "enquête ou procès",
          "mgmt": "départ d'un dirigeant", "macro": "choc géopolitique ou de marché", "move": "chute brutale signalée"}
FAM_FR = {"export": "exportations", "legal": "justice", "halt": "cotation", "hack": "piratage", "ban": "sanctions", "takeover": "rachat",
          "earnings": "résultats", "mgmt": "direction", "macro": "marché", "move": "prix"}
REACT = re.compile(r"erases?\s+(?:all\s+)?(?:its\s+|earlier\s+|early\s+|session\s+|intraday\s+|pre-?market\s+)?gains|turns?\s+(?:negative|red|lower)|wipes?\s+out\s+(?:its\s+)?gains"
                   r"|extends?\s+(?:its\s+)?(?:losses|declines?|slide|drop|fall)|\b(?:plunge|plummet|tumble|crash|sink|slump|nosedive|crater|tank)(?:s|ed|ing)?\b|sell-?off"
                   r"|worst\s+(?:day|week|session|drop|decline)|bloodbath|capitulat\w*|efface\s+ses\s+gains|passe\s+(?:dans\s+le\s+rouge|en\s+n[ée]gatif)"
                   r"|\bchute\b|\bplonge\b|s'effondre|d[ée]gringole|d[ée]visse|\bkrach\b", I)
DROP = re.compile(r"(?:down|fell|falls?|drops?|dropped|plunges?|plunged|sinks?|sank|tumbles?|tumbled|slides?|slid|lower|loses?|lost|crash(?:es|ed)?|plummets?|plummeted|chute|recule|perd|baisse\s+de)"
                  r"\s+(?:by\s+|as\s+much\s+as\s+|more\s+than\s+|over\s+|nearly\s+|about\s+|almost\s+|de\s+|plus\s+de\s+|~)?(\d+(?:[.,]\d+)?)\s?%|[−-](\d+(?:[.,]\d+)?)\s?%", I)
DENY = re.compile(r"\b(?:denies|denied|deny|denying|refutes?|refuted|dismiss(?:es|ed)\s+(?:the\s+)?reports?|no\s+evidence|zero\s+(?:proof|evidence)|no\s+proof"
                  r"|not\s+true|false\s+report|fake\s+news|debunk\w*|hoax|no\s+criminal\s+charges|not\s+(?:been\s+)?charged|cleared\s+of)\b|d[ée]ment\w*", I)
RUMOR = re.compile(r"\b(?:rumou?rs?|unconfirmed|speculat\w*|unverified|doubtful)\b|not\s+(?:yet\s+)?confirmed|\bif\s+true\b|non\s+confirm[ée]|rumeur", I)
HEDGE = re.compile(r"\b(?:warns?|warned|warning|threat\w*|could|may|might|would|plans?\s+to|prepar\w*|imminent|considering|weighs?)\b|menace|pourrait|envisage", I)


def _d(x):
    return x if isinstance(x, dict) else {}


def _by(F):
    return _d(_d(F.get("proj")).get("by"))


def tms(v):
    """Heure d'une info en millisecondes (ISO, format GDELT 20261127T120000Z, secondes ou millisecondes)."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v if v > 1e12 else v * 1000)
    s = str(v).strip()
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})?Z?$", s)
    try:
        if m:
            return int(dt.datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]), int(m[6] or 0), tzinfo=dt.timezone.utc).timestamp() * 1000)
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return int(d.timestamp() * 1000)
    except Exception:
        return None


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def hm(ms):
    try:
        from zoneinfo import ZoneInfo
        d = dt.datetime.fromtimestamp(ms / 1000, ZoneInfo("Europe/Paris"))
    except Exception:
        d = dt.datetime.fromtimestamp(ms / 1000 + 3600, dt.timezone.utc)
    return d.strftime("%H h %M").replace(" h 00", " h")


def nonum(s):
    """Retire prix, pourcentages et montants d'un texte (anti-spoil)."""
    if not s:
        return ""
    s = re.sub(r"\b(?:shares?|stock|stocks|futures|price|prices|actions?|l'action|le\s+titre|le\s+cours)\s+(?:are\s+|is\s+|were\s+|was\s+)?"
               r"(?:up|down|higher|lower|rises?|rose|fell|falls?|drop(?:s|ped)?|gain(?:s|ed)?|jump(?:s|ed)?|slip(?:s|ped)?|slides?|plunge[sd]?|sinks?|"
               r"en\s+hausse|en\s+baisse|monte|baisse|recule|grimpe|chute)\s*(?:de\s+|by\s+|about\s+|around\s+|nearly\s+|over\s+|more\s+than\s+|plus\s+de\s+|environ\s+)?"
               r"[+\-−]?\d+(?:[.,]\d+)?\s?%\s*[;,:.]?", " ", s, flags=I)
    s = re.sub(r"[+\-−]?\d+(?:[.,]\d+)?\s?(?:%|pct\b|percent\b|pour\s?cent\b|bps\b|points?\b|pts\b)", " ", s, flags=I)
    num = r"\d{1,3}(?:[\s\u202f\u00a0.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
    s = re.sub(r"[$€£]\s?(?:" + num + r")(?:\s?(?:k|m|bn|b|billion|million|trillion|milliards?|millions?)\b)?|\b(?:" + num + r")\s?(?:\$|€|£|usd\b|usdc\b|usdt\b|eur\b|dollars?\b|euros?\b)", " ", s, flags=I)
    s = re.sub(r"\b(?:target|objectif|price|prix|level|niveau|support|r[ée]sistance|resistance|cible|stop|entry|entr[ée]e)s?\s*(?:de|of|at|à|:|=)?\s*(?:" + num + r")\b", lambda m: re.sub(r"[\d\s\u202f\u00a0.,:=]+$", "", m.group(0)), s, flags=I)
    s = re.sub(r"\b\d+(?:[.,]\d+)?\s?[kK]\b", " ", s)
    s = re.sub(r"\b\d+[.,]\d+\b", " ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", " ", s)
    s = re.sub(r"\s+([,.])", r"\1", s)  # (les espaces avant « : ; ! ? » restent : typographie française)
    s = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip(" ,;:-–—")


PROPER = ["Nvidia", "Blackwell", "Apple", "Microsoft", "Amazon", "Google", "Alphabet", "Meta", "Tesla", "Intel", "Micron", "Broadcom", "Oracle",
          "Coinbase", "Robinhood", "Palantir", "Netflix", "Alibaba", "CoreWeave", "Nebius", "Snowflake", "SanDisk", "Costco", "Uber", "Shopify",
          "Bitcoin", "Ethereum", "Solana", "Hyperliquid", "Ripple", "Binance", "Chainlink", "China", "Chinese", "Shanghai", "Beijing", "Shenzhen",
          "Hong Kong", "Taiwan", "Japan", "Korea", "Russia", "Ukraine", "Iran", "Israel", "Saudi", "Europe", "European", "America", "American",
          "Washington", "Trump", "Powell", "Fed", "Treasury", "Congress", "Senate", "White House", "Pentagon", "Bloomberg", "Reuters", "Hormuz"]
ACR = ["US", "U.S.", "AI", "GPU", "GPUs", "CEO", "CFO", "SEC", "DOJ", "FTC", "FBI", "EU", "UK", "ETF", "IPO", "ECB", "OPEC", "NATO", "UN", "IMF",
       "AMD", "IBM", "TSMC", "ASML", "HBM", "BTC", "ETH", "XRP", "SOL", "CPI", "FOMC", "WSJ", "FT", "CNBC", "Q1", "Q2", "Q3", "Q4"]


def decaps(s):
    letters = [c for c in s if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.6:
        return s
    out = s.lower()
    out = re.sub(r"(^|[.!?]\s+)([a-zà-ÿ])", lambda m: m.group(1) + m.group(2).upper(), out)
    for w in PROPER:
        out = re.sub(r"\b" + re.escape(w.lower()) + r"\b", w, out)
    for a in ACR:
        out = re.sub(r"\b" + re.escape(a.lower()) + r"(?![\w.])", a, out, flags=I)
    out = re.sub(r"\$([a-z]{1,6})\b", lambda m: "$" + m.group(1).upper(), out)
    return out


def mixed_span(s):
    """Plus longue suite de mots qui n'est pas en capitales (le résumé qui suit souvent un titre d'agence en capitales)."""
    toks = list(re.finditer(r"\S+", s))
    best, cur = None, []
    def caps(w):
        L = [c for c in w if c.isalpha()]
        return len(L) > 4 and all(c.isupper() for c in L)
    for m in toks + [None]:
        if m is not None and not caps(m.group(0)):
            cur.append(m)
            continue
        if len(cur) >= 8 and (best is None or len(cur) > len(best)):
            best = cur
        cur = []
    if not best:
        return None
    return s[best[0].start():best[-1].end()]


def ncaps(s):
    return sum(1 for w in re.findall(r"\S+", s or "") if len([c for c in w if c.isalpha()]) > 4 and all(c.isupper() for c in w if c.isalpha()))


def readable(it):
    """Info déjà en français, ou texte qui n'est pas un titre d'agence tout en capitales (ou qui contient un résumé normal)."""
    return bool(it["fr"]) or ncaps(it["en"]) < 3 or bool(mixed_span(it["en"]))


def headline(text):
    """Titre lisible : sans $TICKER en tête, puces d'agence, liens ni fin coupée."""
    s = re.sub(r"https?://\S+", " ", text or "")
    s = re.sub(r"^\s*(?:breaking|just\s+in|alert|urgent|flash)\s*[:\-–]\s*", "", s, flags=I)
    s = re.sub(r"^\s*(?:\$[A-Za-z]{1,6}\s*(?:,\s*)?)+\s*[-–:]\s*", "", s)
    if ncaps(s) >= 3:  # titre d'agence en capitales suivi d'un résumé : on garde le résumé
        sp = mixed_span(s)
        if sp:
            s = sp
    s = s.replace("*", " ").replace("\n", " ")
    s = re.sub(r"\s{2,}", " ", s).strip()
    if s.endswith("…") or s.endswith("..."):
        s = s.rstrip(".…").rstrip()
        s = re.sub(r",?\s*(?:according\s+to|per|via|citing|selon)\s+\S*$", "", s, flags=I)
        s = re.sub(r"\s+\S{1,3}$", "", s)
    s = decaps(s)
    s = s.strip(" -–—:;,")
    if len(s) > 170:  # titre long : la première phrase suffit si elle dit l'essentiel
        first = re.split(r"(?<=[.!?])\s+(?=[A-Z])", s)[0]
        if len(first) >= 50:
            s = first
    if len(s) > 230:
        s = s[:230].rsplit(" ", 1)[0] + "…"
    return s


def to_fr(text, cache):
    """Traduction gratuite (même point d'accès que collect.py), mémorisée ; None si indisponible."""
    if not text:
        return None
    k = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    if k in cache:
        return cache[k]
    if OFFLINE:
        return None
    q = urllib.parse.quote(text[:600])
    ua = {"User-Agent": "Mozilla/5.0 (compatible; perps-dashboard-collector/1.0)"}
    try:
        with urllib.request.urlopen(urllib.request.Request("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=fr&dt=t&q=" + q, headers=ua), timeout=8) as r:
            j = json.loads(r.read())
        out = "".join(seg[0] for seg in j[0] if seg and seg[0]).strip()
        src = j[2] if len(j) > 2 else ""
    except Exception:
        try:  # second point d'accès Google (celui que collect.py utilise quand le premier refuse)
            with urllib.request.urlopen(urllib.request.Request("https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl=fr&q=" + q, headers=ua), timeout=8) as r:
                j = json.loads(r.read())
            first = j[0] if isinstance(j, list) and j else j
            out, src = (first[0], first[1] if len(first) > 1 else "") if isinstance(first, list) else (str(first), "")
            out = str(out).strip()
        except Exception:
            return None
    if not out:
        return None
    cache[k] = text if src == "fr" else out
    return cache[k]


# ---------------------------------------------------------------- lecture des infos
def items_of(F):
    out, seen = [], {}
    def add(src, h, t, en, fr, url, kind, syms=(), tickers=()):
        ms = tms(t)
        if not ms or ms < NOW - WIN or ms > NOW + 10 * 60000:
            return
        en = str(en or "")
        if not en.strip():
            return
        key = url or (kind + ":" + hashlib.sha1(en.encode("utf-8")).hexdigest()[:12])
        if key in seen:
            seen[key]["syms"] |= set(syms)
            return
        it = {"src": src, "h": str(h or ""), "t": ms, "en": en, "fr": fr, "url": url or "", "kind": kind, "syms": set(syms),
              "tickers": {str(x).upper().lstrip("$") for x in (tickers or [])}}
        seen[key] = it
        out.append(it)
    def lst(x):
        return [t for t in x if isinstance(t, dict)] if isinstance(x, list) else []
    def dct(x):
        return x if isinstance(x, dict) else {}
    X = dct(F.get("x"))
    for acc, L in dct(X.get("accounts")).items():
        for t in lst(L):
            add("x", t.get("h") or acc, t.get("t"), t.get("o_fr") or t.get("fr"), t.get("fr") if t.get("o_fr") else None, t.get("url"), "x")
    for sym, L in dct(X.get("tickers")).items():
        for t in lst(L):
            if t.get("alert"):
                continue
            add("x", t.get("h"), t.get("t"), t.get("o_fr") or t.get("fr"), t.get("fr") if t.get("o_fr") else None, t.get("url"), "x", [str(sym).upper()])
    for t in lst(X.get("latest")):
        if t.get("alert"):
            continue
        add("x", t.get("h"), t.get("t"), t.get("o_fr") or t.get("fr"), t.get("fr") if t.get("o_fr") else None, t.get("url"), "x")
    for sym, L in dct(F.get("stocktwits")).items():
        for t in lst(L):
            add("st", t.get("h"), t.get("t"), t.get("o_fr") or t.get("fr"), t.get("fr") if t.get("o_fr") else None, t.get("url"), "st", [str(sym).upper()])
    for t in lst(F.get("influ")):
        if t.get("rt"):
            continue
        add("influ", t.get("h"), t.get("t"), t.get("o_fr") or t.get("fr"), t.get("fr") if t.get("o_fr") else None, t.get("url"), "influ",
            tickers=t.get("tickers") if isinstance(t.get("tickers"), list) else [])
    for t in lst(F.get("trump")):
        add("trump", "realDonaldTrump", t.get("t"), t.get("o_fr") or t.get("o") or t.get("fr"), t.get("fr") if (t.get("o_fr") or t.get("o")) else None, t.get("url"), "trump")
    for a in lst(F.get("risks")):
        if a.get("danger"):
            continue
        add("risks", a.get("src"), a.get("t"), a.get("title"), a.get("fr") if a.get("tr") and a.get("fr") != a.get("title") else None, a.get("url"), "risks")
    return out


def cred_of(it):
    """2 = agence de presse ou grand média (ou info qui cite Bloomberg, Reuters…), 1 = compte officiel ou relais d'actualité, 0 = autre."""
    h = it["h"].lower().lstrip("@")
    txt = re.sub(r"https?://\S+", " ", it["en"] + " \n " + (it["fr"] or "")).strip()
    cited = set()
    for m in CITE.finditer(txt):
        g = next((x for x in m.groups() if x), "").lower()
        for k, nm in OUT_NAME:
            if k in re.sub(r"\s+", " ", g):
                cited.add(nm)
    if it["kind"] == "risks":
        dom = h.replace("www.", "")
        return (2 if dom in CRED_DOM else 0), cited
    if h in NEWS or (cited and (h in RELAY or h in OFFICIAL)):
        return 2, cited
    if h in OFFICIAL or h in RELAY or cited:
        return 1, cited
    return 0, cited


def assets_of(it, uni):
    txt = re.sub(r"https?://\S+", " ", it["en"] + " \n " + (it["fr"] or ""))
    out = set(s for s in it["syms"] if s in uni)
    for m in re.finditer(r"\$([A-Za-z]{1,6})\b", txt):
        s = m.group(1).upper()
        if s in uni:
            out.add(s)
    out |= {s for s in it["tickers"] if s in uni}
    for s, rx in RX_I.items():
        if s in uni and rx.search(txt):
            out.add(s)
    dc = decaps(it["en"]) if ncaps(it["en"]) >= 3 else ""  # titres en capitales : « APPLE » → « Apple »
    for s, rx in RX_C.items():
        if s in uni and (rx.search(txt) or (dc and rx.search(dc))):
            out.add(s)
    return out


def classify(it):
    txt = it["en"] + " \n " + (it["fr"] or "")
    cats = []
    for k, fam, g, d, pats in CATS:
        for rx, ctx in pats:
            if rx.search(txt) and (ctx is None or ctx.search(txt)):
                cats.append((k, fam, g, d))
                break
    drop = 0.0
    for m in DROP.finditer(txt):
        try:
            drop = max(drop, float((m.group(1) or m.group(2)).replace(",", ".")))
        except Exception:
            pass
    q = it["en"].strip().endswith("?")
    return {"cats": cats, "react": bool(REACT.search(txt)), "drop": drop, "deny": bool(DENY.search(txt)), "rumor": bool(RUMOR.search(txt)) or q}


TK_SUBJ = re.compile(r"\bto\s+be\s+(?:acquired|bought|taken\s+private)\b", I)
TK_OBJ = re.compile(r"(?:takeover|buyout|acquisition)\s+(?:bid|offer|proposal)\s+(?:for|to\s+buy)|tender\s+offer\s+for|offre\s+publique\s+d'achat\s+sur|\bOPA\s+sur", I)


def takeover_targets(txt, syms):
    """Rachat : seul l'actif racheté est concerné (celui juste avant « to be acquired », ou juste après « offer for »)."""
    out = set()
    for s in syms:
        pats = [re.compile(r"\$" + re.escape(s) + r"\b", I)] + ([RX_I[s]] if s in RX_I else []) + ([RX_C[s]] if s in RX_C else [])
        pos = [m.start() for p in pats for m in p.finditer(txt)]
        for m in TK_SUBJ.finditer(txt):
            if any(0 <= m.start() - q <= 60 for q in pos):
                out.add(s)
        for m in TK_OBJ.finditer(txt):
            if any(0 <= q - m.end() <= 25 for q in pos):
                out.add(s)
    return out


def big_drop(sym, pct):
    return pct >= (3 if sym in INDEX else 10 if sym in CRYPTO else 7)


# ---------------------------------------------------------------- réaction du prix
def candles(coin):
    if CANDLES_DIR:
        p = os.path.join(CANDLES_DIR, coin.replace(":", "_") + ".json")
        if os.path.exists(p):
            cs = json.load(open(p))
            return [k for k in cs if k["t"] + H <= NOW]  # bougies terminées seulement (pas de futur dans les tests)
        return None
    if OFFLINE:
        return None
    try:
        import trader
        w = 1.5 - (time.time() - _LAST[0])  # appels espacés : proj.py vient d'utiliser une bonne partie du quota Hyperliquid
        if w > 0:
            time.sleep(w)
        _LAST[0] = time.time()
        return trader.candles(coin, "1h", 4)
    except Exception as e:
        print("danger: bougies", coin, type(e).__name__)
        return None


_LAST = [0.0]


def shock(coin, t0, d, atr1d):
    """Réaction du prix depuis la 1re info : « forte », « notable » ou « aucune » (jamais de prix en sortie)."""
    cs = candles(coin)
    if not cs:
        return None
    cs = [k for k in cs if k["t"] <= NOW]
    pre = [k for k in cs if k["t"] + H <= t0]
    post = [k for k in cs if k["t"] + H > t0]
    if len(pre) < 24 or not post:
        return None
    ref = pre[-1]["c"]
    if not atr1d or atr1d <= 0:
        trs = [max(b["h"] - b["l"], abs(b["h"] - a["c"]), abs(b["l"] - a["c"])) for a, b in zip(pre[-73:-1], pre[-72:])]
        atr1d = 3 * sum(trs) / max(1, len(trs))
    mv = (ref - min(k["l"] for k in post)) if d <= 0 else (max(k["h"] for k in post) - ref)
    r = mv / atr1d
    vols = sorted(k["v"] for k in pre[-72:])
    vmed = vols[len(vols) // 2] if vols else 0
    vx = max(k["v"] for k in post) / vmed if vmed > 0 else 0
    lvl = "forte" if r >= 0.5 or (r >= 0.35 and vx >= 4) else "notable" if r >= 0.3 or (r >= 0.2 and vx >= 4) else "aucune"
    return lvl


# ---------------------------------------------------------------- détection
def detect(F, state):
    by = _by(F)
    uni = set(AL_I) | set(AL_C)
    coin_of = {}
    for c in by:
        s = c.split(":")[-1].upper()
        uni.add(s)
        coin_of[s] = c
    for s in list(uni):
        coin_of.setdefault(s, None)
    clusters = {}
    for it in items_of(F):
        cl = classify(it)
        if not cl["cats"] and not (cl["react"] or cl["drop"]):
            continue
        it.update(cl)
        it["cred"], it["cited"] = cred_of(it)
        syms = assets_of(it, uni)
        txt = it["en"] + " " + (it["fr"] or "")
        if len(syms) >= 3:  # message qui aligne les $tickers : on ne garde que les actifs cités par leur nom
            nc = re.sub(r"\$[A-Za-z]{1,6}(?:\.[A-Za-z]{1,2})?\b", " ", txt)
            syms = {x for x in syms if (x in RX_I and RX_I[x].search(nc)) or (x in RX_C and RX_C[x].search(nc))}
        fams = {}
        for k, fam, g, d in cl["cats"]:
            if fam == "macro":
                continue
            if k == "ban" and syms and syms <= CRYPTO and not CTX_BIGJ.search(txt):
                g = 2  # une interdiction locale ne menace pas bitcoin ou ether comme une décision américaine ou chinoise
            if fam not in fams or g > fams[fam][1]:
                fams[fam] = (k, g, d)
        if fams:  # une même info ne crée pas en plus une alerte moins grave sur le même actif
            gmax = max(v[1] for v in fams.values())
            fams = {f: v for f, v in fams.items() if v[1] == gmax}
        def scope(k, syms):
            if k == "takeover":
                return takeover_targets(txt, syms)
            if k == "rug":
                return syms & CRYPTO
            if k == "hack":
                return syms & (CRYPTO | {"COIN", "HOOD", "MSTR"})
            return syms
        targets = [(s, fam, k, g, d) for fam, (k, g, d) in fams.items() for s in scope(k, syms)]
        if any(c[1] == "macro" for c in cl["cats"]):
            targets.append(("MARCHE", "macro", "macro", 2 if HEDGE.search(txt) else 3, -1))
        if not targets and (cl["react"] or cl["drop"]):
            targets = [(s, "move", "move", 1, -1) for s in syms]
        for s, fam, k, g, d in targets:
            C = clusters.setdefault((s, fam), {"asset": s, "fam": fam, "cat": k, "g": 0, "dir": d, "ev": [], "react": [], "deny": []})
            if it["deny"]:  # un démenti n'est pas une preuve : il est seulement signalé
                C["deny"].append(it)
                continue
            if it["rumor"] and it["cred"] < 2:
                g = min(g, 2)
            if g > C["g"]:
                C["g"], C["cat"], C["dir"] = g, k, d
            C["ev"].append((it, g))
    # les infos « le prix décroche » sans cause rejoignent l'histoire la plus grave du même actif
    for key in [k for k in clusters if k[1] == "move"]:
        others = [C for k, C in clusters.items() if k[0] == key[0] and k[1] != "move"]
        if others:
            max(others, key=lambda C: C["g"])["react"].extend(it for it, g in clusters[key]["ev"])
            del clusters[key]
    res = []
    for (s, fam), C in clusters.items():
        if not C["ev"]:
            continue
        g = C["g"]
        ev = [it for it, gi in C["ev"]]
        top = [it for it, gi in C["ev"] if gi == g]  # les infos les plus graves datent l'histoire
        srcs = {}
        for it in ev:
            hk = it["h"].lower().lstrip("@")
            srcs[hk] = max(srcs.get(hk, 0), it["cred"])
        n_src = len(srcs)
        n_news = sum(1 for v in srcs.values() if v >= 2)
        n_rel = sum(1 for v in srcs.values() if v >= 1)
        top_news = any(it["cred"] >= 2 for it in top)
        top_rel = any(it["cred"] >= 1 for it in top)
        if fam == "move":
            lvl = 2 if any(it["cred"] >= 2 and big_drop(s, it["drop"]) for it in ev) else 1
        elif fam == "macro":
            lvl = 3 if (top_news and n_src >= 2) else 2 if top_rel else 1
        elif g >= 3:
            lvl = 3 if (top_news or (top_rel and n_src >= 3)) else 2 if (top_rel or n_src >= 4) else 1
        else:
            lvl = 2 if (n_news >= 1 or (n_rel >= 1 and n_src >= 3) or n_src >= 5) else 1
        if lvl < 2:
            continue
        allr = ev + C["react"]
        first = min(it["t"] for it in top)
        last = max(it["t"] for it in allr)
        grave_t = min([it["t"] for it in top if it["cred"] >= 1] or [first])
        res.append({"asset": s, "coin": coin_of.get(s), "fam": fam, "cat": C["cat"], "g": g, "dir": C["dir"], "lvl": lvl,
                    "first": first, "last": last, "n_src": n_src, "n_news": n_news,
                    "react": any(it.get("react") for it in allr if it["cred"] >= 1),
                    "drop": max([it.get("drop", 0) for it in allr if it["cred"] >= 1] or [0]),
                    "denied": any(it["cred"] >= 2 and it["t"] > grave_t for it in C["deny"]),
                    "items": sorted(ev, key=lambda it: it["t"]), "top": sorted(top, key=lambda it: it["t"]), "ritems": C["react"]})
    return res


def src_label(it):
    if it["kind"] == "risks":
        return it["h"].replace("www.", "")
    if it["kind"] == "st":
        return "@" + it["h"] + " (StockTwits)"
    return "@" + it["h"]


def best_item(items):
    def score(it):
        return (it["cred"], max([c[2] for c in it.get("cats", [])] or [0]), 1 if readable(it) else 0, -it["t"])
    return max(items, key=score)


STOP = set("""that this with from have been were they their them after says said report reports reported according sources source
shares stock stocks breaking just news what will would could about over more than into amid following linked tied when where which
while also some other there here your onto under against since until today week year company companies""".split())


STEM = {"chinese": "china", "russian": "russia", "iranian": "iran", "israeli": "israel", "american": "america", "european": "europe"}


def wset(text, asset):
    """Mots qui caractérisent une info (sans les mots courants ni le nom de l'actif), au singulier."""
    w = set()
    for x in re.findall(r"[a-zà-ÿ]{4,}", (text or "").lower()):
        x = STEM.get(x, x)
        if len(x) > 4 and x.endswith("s") and not x.endswith("ss"):
            x = x[:-1]
        if x not in STOP and x != asset.lower():
            w.add(x)
    al = "|".join(x for x in (AL_I.get(asset), AL_C.get(asset)) if x)
    if al:
        rx = re.compile(r"(?:" + al + r")", I)
        w = {x for x in w if not rx.fullmatch(x)}
    return w


def jacc(a, b):
    return len(a & b) / max(1, len(a | b))


def run(F, S):
    S.setdefault("alerts", [])
    S.setdefault("tr", {})
    S["alerts"] = [a for a in S["alerts"] if NOW - a.get("last", 0) < KEEP]
    found = detect(F, S)
    found.sort(key=lambda c: (-c["lvl"], -c["last"]))
    nshock = [0]
    by = _by(F)
    for c in found:
        prev = None
        urls = {it["url"] for it in c["items"] if it["url"]}
        for a in S["alerts"]:
            same = set(a.get("urls", [])) & urls or (a.get("fam") == c["fam"] and (a.get("g") or 0) == c["g"])
            if a["asset"] == c["asset"] and same and c["first"] - a.get("last", 0) < 24 * H:
                prev = a
                break
        new = prev is None
        if new:
            prev = {"id": f"{c['asset']}-{c['fam']}-{dt.datetime.fromtimestamp(c['first'] / 1000, dt.timezone.utc).strftime('%m%d%H%M')}",
                    "asset": c["asset"], "fam": c["fam"], "det": NOW, "first": c["first"], "urls": [], "srcs": [], "cred": [], "sev": 0,
                    "keys": [], "dev_n": 0, "dev_first": None, "lct": None}
            S["alerts"].append(prev)
        a = prev
        # nouvel élément : une source fiable relance l'histoire après au moins 4 h sans info fiable (les agences publient par salves),
        # sur une info au moins aussi grave, et ce n'est pas la simple republication d'un ancien titre
        keys = [set(k) for k in a.get("keys", [])]
        fresh = sorted((it for it in c["top"] if it["cred"] >= 1 and it["url"] not in a.get("urls", [])), key=lambda it: it["t"])
        dev = None
        if not new and a.get("lct") and c["lvl"] >= 3 and c["g"] >= (a.get("g") or 0):
            for it in fresh:
                w = wset(it["en"], c["asset"])
                if it["t"] - a["lct"] >= 4 * H and len(w) >= 4 and max([jacc(w, k) for k in keys] or [0]) < 0.5:
                    dev = it
                    break
        for it in fresh:
            w = wset(it["en"], c["asset"])
            if len(w) >= 4:
                keys.append(w)
        a["keys"] = [sorted(k)[:25] for k in keys][-20:]
        tc = [it["t"] for it in c["top"] if it["cred"] >= 1]
        if dev:
            a["dev_n"] = a.get("dev_n", 0) + 1
            a["dev_first"] = dev["t"]
            a["dev_det"] = NOW
            a["shock"] = None
            a.pop("shock_t", None)
            a["head_en"] = None
            a["srcs"], a["cred"], a["n_src"] = [], [], 0
        elif tc and not a.get("dev_first"):
            a["dev_first"] = min(tc)
        if tc:
            a["lct"] = max(a.get("lct") or 0, max(tc))
        a["coin"] = c["coin"]
        a["cat"] = c["cat"] if c["g"] >= (a.get("g") or 0) else a.get("cat", c["cat"])
        a["g"] = max(a.get("g") or 0, c["g"])
        a["dir"] = c["dir"]
        a["first"] = min(a.get("first", c["first"]), c["first"])
        a["last"] = max(a.get("last", 0), c["last"])
        a["seen"] = NOW
        a["urls"] = (a.get("urls", []) + [u for u in urls if u not in a.get("urls", [])])[-60:]
        cur = [it for it in c["items"] if it["t"] >= (a.get("dev_first") or 0) - 30 * 60000] or c["items"]  # sources du dernier élément
        lab = list(dict.fromkeys(src_label(it) for it in sorted(cur, key=lambda it: (-it["cred"], it["t"]))))
        a["srcs"] = (a.get("srcs", []) + [x for x in lab if x not in a.get("srcs", [])])[:20]
        cr = [nm for it in cur for nm in sorted(it.get("cited") or [])] + [src_label(it) for it in cur if it["cred"] >= 2 and it["kind"] != "st"]
        cr = list(dict.fromkeys(cr))
        a["cred"] = (a.get("cred", []) + [x for x in cr if x not in a.get("cred", [])])[-6:]
        a["n_src"] = max(a.get("n_src", 0), len({it["h"].lower() for it in cur}))
        a["denied"] = c["denied"]
        a["react"] = a.get("react") or c["react"]
        lvl = c["lvl"]
        if lvl == 2 and c["fam"] not in ("macro", "move") and c["g"] >= 2 and big_drop(c["asset"], c["drop"]):
            lvl = 3
        # réaction du prix depuis le dernier élément (uniquement pour une alerte sérieuse sur un actif cotable)
        if lvl >= 2 and c["coin"] and c["fam"] != "macro" and nshock[0] < 8:
            nshock[0] += 1
            P = by.get(c["coin"]) or {}
            try:
                sh = shock(c["coin"], a.get("dev_first") or a["first"], a["dir"], P.get("atr1d"))
            except Exception as e:
                print("danger: réaction du prix", c["coin"], type(e).__name__)
                sh = None
            if sh:
                if sh == "forte" and a.get("shock") != "forte":
                    a["shock_t"] = NOW
                a["shock"] = "forte" if a.get("shock") == "forte" else sh
            if lvl == 2 and a.get("shock") == "forte" and c["g"] >= 2 and c["fam"] != "move":
                lvl = 3
        if lvl >= 3 and not a.get("det3"):
            a["det3"] = NOW
        # une alerte ne redescend pas parce que ses tweets sont sortis du flux (un démenti est seulement signalé)
        a["sev"] = max(a.get("sev", 0), lvl)
        # titre : celui d'une source fiable sur le dernier élément ; il ne change plus ensuite, sauf info plus grave ou plus fiable
        cand = [it for it in c["top"] if it["t"] >= (a.get("dev_first") or 0) - 30 * 60000] or c["top"]
        b = best_item(cand)
        bg = c["g"]
        if b["cred"] <= 0:  # que des comptes anonymes : pas de titre repris tel quel
            if (a.get("head_cred") or 0) <= 0:
                a["head_en"] = None
                a["head_fr"] = f"Plusieurs comptes ({a['n_src']}) évoquent : {CAT_FR.get(a['cat'], 'une info grave')}, sans source fiable pour l'instant"
                a["head_cred"], a["head_g"] = 0, bg
                a["url"] = b["url"]
        elif not a.get("head_en") or (a.get("head_cred") or 0) <= 0 or bg > a.get("head_g", 0) or (bg == a.get("head_g", 0) and b["cred"] > a.get("head_cred", -1)):
            a["head_en"] = nonum(headline(b["en"]))
            a["head_fr"] = nonum(headline(b["fr"])) if b["fr"] else None
            a["head_cred"], a["head_g"] = b["cred"], bg
            a["url"] = b["url"]
        if not a.get("head_fr") and a.get("head_en"):
            tr = to_fr(a["head_en"], S["tr"])
            if tr:
                a["head_fr"] = nonum(tr)
    if len(S["tr"]) > 400:
        S["tr"] = dict(list(S["tr"].items())[-300:])
    return found


def src_part(a):
    n = a.get("n_src") or len(a.get("srcs", [])) or 1
    cr = [x for x in a.get("cred", []) if not x.endswith("(StockTwits)")]
    return f"{n} source{'s' if n > 1 else ''}" + (f" dont {', '.join(cr[:2])}" if cr else "")


def public(a):
    """Alerte telle qu'écrite dans feed.json (sans prix), avec les notifications qu'elle déclenche."""
    asset = "MARCHÉ" if a["asset"] == "MARCHE" else a["asset"]
    head = a.get("head_fr") or (CAT_FR.get(a.get("cat"), "info grave") + " : " + (a.get("head_en") or ""))
    head = nonum(head)
    rx = " · réaction du marché forte" if a.get("shock") == "forte" else ""
    deny = " · démenti publié" if a.get("denied") else ""
    short = head[:160].rsplit(" ", 1)[0] + "…" if len(head) > 160 else head
    up = a.get("dir", -1) > 0
    sev = a.get("sev", 0)
    det3 = a.get("det3") or 0
    t0 = a.get("dev_first") or a.get("first")
    seen = (a.get("dev_det") if a.get("dev_n") else None) or det3 or a.get("det")
    base = f"ALERTE GRAVE · {asset}" + (" (hausse brutale possible)" if up else "")
    push = []
    recent = (t0 or 0) >= NOW - 6 * H  # pas de notification pour une histoire de plus de 6 h (premier passage, panne)
    if sev >= 3 and det3 and recent:
        push.append({"k": "danger:" + a["id"], "t": det3, "title": base, "body": nonum(f"{short} — {src_part(a)}{rx}{deny}")})
        if a.get("dev_n") and (a.get("dev_det") or 0) > det3 + 60000:
            push.append({"k": f"danger:{a['id']}:d{a['dev_n']}", "t": a["dev_det"], "title": base + " · nouvel élément",
                         "body": nonum(f"{short} — {src_part(a)}{rx}")})
    if sev >= 3 and det3 and (t0 or 0) >= NOW - 72 * H:
        last_push = max(det3, a.get("dev_det") or 0)
        if a.get("shock") == "forte" and (a.get("shock_t") or 0) > last_push + 60000:
            push.append({"k": f"danger:{a['id']}:c{a.get('dev_n', 0)}", "t": a["shock_t"], "title": f"ALERTE GRAVE · {asset} : le marché réagit fortement",
                         "body": nonum(f"Info de {hm(t0)} : {short}")})
    return {"id": a["id"], "sev": sev, "lvl": "grave" if sev >= 3 else "important", "asset": asset, "coin": a.get("coin"),
            "cat": a.get("cat"), "cat_fr": CAT_FR.get(a.get("cat"), ""), "fam": a.get("fam"), "dir": a.get("dir", -1),
            "first": t0, "story_first": a.get("first"), "last": a.get("last"), "det": seen, "dev_n": a.get("dev_n", 0),
            "active": NOW - a.get("last", 0) < ACTIVE, "head": head, "head_en": a.get("head_en"), "src": src_part(a),
            "srcs": list(dict.fromkeys([x for x in a.get("cred", []) if x.startswith("@")] + a.get("srcs", [])))[:8], "cred": a.get("cred", [])[:4], "url": a.get("url"), "shock": a.get("shock"),
            "denied": bool(a.get("denied")), "push": push, "line": nonum(f"{head} ({src_part(a)}{rx}{deny})")}


# ---------------------------------------------------------------- mise en avant dans le dashboard actuel
def clean(F):
    """Retire ce qu'un passage précédent a ajouté (si collect.py n'a pas réécrit le flux)."""
    X = _d(F.get("x"))
    for L in list(_d(X.get("tickers")).values()) + [X.get("latest")] + list(_d(X.get("accounts")).values()) + list(_d(F.get("stocktwits")).values()) + [F.get("influ")]:
        if not isinstance(L, list):
            continue
        L[:] = [t for t in L if not (isinstance(t, dict) and t.get("alert"))]
        for t in L:
            if isinstance(t, dict) and t.get("dz"):
                if str(t.get("fr", "")).startswith(MARK + " "):
                    t["fr"] = t["fr"][len(MARK) + 1:]
                if "tag0" in t:
                    t["tag"] = t.pop("tag0")
                t.pop("dz", None)
    if isinstance(F.get("risks"), list):
        F["risks"] = [r for r in F["risks"] if not (isinstance(r, dict) and r.get("danger"))]
    for P in _by(F).values():
        if not isinstance(P, dict):
            continue
        if isinstance(P.get("risk"), list):
            P["risk"] = [r for r in P["risk"] if not (isinstance(r, str) and (r.startswith("ALERTE GRAVE") or r.startswith("À SURVEILLER")))]
        if isinstance(P.get("fx"), list):
            P["fx"] = [f for f in P["fx"] if not (isinstance(f, list) and f and f[0] in ("Alerte grave", "À surveiller"))]
        if isinstance(P.get("ann"), list):
            P["ann"] = [x for x in P["ann"] if not (isinstance(x, dict) and x.get("dz"))]


def inject(F, S, pub):
    if not isinstance(F.get("x"), dict):
        F["x"] = {}
    X = F["x"]
    if not isinstance(X.get("tickers"), dict):
        X["tickers"] = {}
    tick = X["tickers"]
    by = _by(F)
    act3 = [p for p in pub if p["sev"] >= 3 and p["active"]]
    act2 = [p for p in pub if p["sev"] == 2 and p["active"]]
    urls3 = {}
    for a in S["alerts"]:
        for p in act3:
            if a["id"] == p["id"]:
                for u in a.get("urls", []):
                    urls3[u] = p
    # 1) les tweets de l'histoire : marqués ⚠︎ et classés baissiers
    for L in list(_d(X.get("tickers")).values()) + [X.get("latest")] + list(_d(X.get("accounts")).values()) + list(_d(F.get("stocktwits")).values()) + [F.get("influ")]:
        for t in L if isinstance(L, list) else []:
            if not isinstance(t, dict):
                continue
            p = urls3.get(t.get("url"))
            if not p or t.get("dz"):
                continue
            t["dz"] = 1
            t["tag0"] = t.get("tag")
            t["tag"] = "bull" if p["dir"] > 0 else "bear"
            t["fr"] = MARK + " " + str(t.get("fr") or "")
    for p in act3:
        txt = f"{MARK} ALERTE GRAVE · {p['asset']} (info de {hm(p['first'])}) — {p['line']}"
        # daté du passage du collecteur : l'alerte reste en tête de la liste tant qu'elle est en cours
        item = {"h": "ALERTE GRAVE", "fol": 0, "t": iso(NOW), "eng": 0, "url": (p.get("url") or "https://x.com") + "#alerte", "fr": txt,
                "o_fr": nonum(f"Détection automatique du dashboard · info publiée à {hm(p['first'])}, repérée à {hm(p['det'])} · {p['cat_fr']} · sources : {', '.join(p['srcs'][:6])}"),
                "tag": "bull" if p["dir"] > 0 else "bear", "alert": 3}
        # 2) en tête des tweets de l'actif (ou de tous les actifs pour une alerte de marché)
        syms = [p["asset"]] if p["asset"] != "MARCHÉ" else sorted({c.split(":")[-1].upper() for c in by} | set(tick.keys()))
        for s in syms:
            if not isinstance(tick.get(s), list):
                tick[s] = []
            tick[s].insert(0, dict(item))
        if not isinstance(X.get("latest"), list):
            X["latest"] = []
        X["latest"].insert(0, dict(item))
        # 3) colonne « Risques mondiaux » (visible même en anti-spoil). Si la presse (GDELT) n'a pas répondu à ce passage,
        # on reprend sa dernière liste de moins de 6 h : sinon le dashboard n'afficherait que l'alerte
        if not isinstance(F.get("risks"), list) or not F["risks"]:
            rc = S.get("risks_cache") or {}
            F["risks"] = [dict(r) for r in rc.get("items", [])] if NOW - (rc.get("at") or 0) < 6 * H else []
        F["risks"].insert(0, {"t": iso(p["last"]), "title": p.get("head_en") or p["head"], "fr": txt, "tr": 1, "url": p.get("url") or "",
                              "src": "Alerte du dashboard", "lang": "French", "danger": 3})
        # 4) verdict et graphique de l'actif
        coins = [p["coin"]] if p.get("coin") else ([c for c in by] if p["asset"] == "MARCHÉ" else [])
        for c in coins:
            P = by.get(c)
            if not isinstance(P, dict):
                continue
            expo = "une position courte est exposée" if p["dir"] > 0 else "une position longue est exposée"
            if not isinstance(P.get("risk"), list):
                P["risk"] = []
            P["risk"].insert(0, nonum(f"ALERTE GRAVE (info de {hm(p['first'])}) : {p['line']}. Info de nature à faire bouger fortement le prix : {expo}."))
            rows = [["Alerte grave", "Info", p["head"][:120] + ("…" if len(p["head"]) > 120 else ""), "up" if p["dir"] > 0 else "dn"],
                    ["Alerte grave", "Type", p["cat_fr"], ""],
                    ["Alerte grave", "Publiée / repérée", f"{hm(p['first'])} / {hm(p['det'])}", ""],
                    ["Alerte grave", "Sources", p["src"], ""],
                    ["Alerte grave", "Réaction du marché", {"forte": "forte", "notable": "notable", "aucune": "pas encore visible"}.get(p.get("shock"), "non mesurée"),
                     ("dn" if p["dir"] < 0 else "up") if p.get("shock") in ("forte", "notable") else "amb"]]
            if p.get("denied"):
                rows.append(["Alerte grave", "Démenti", "un démenti a été publié depuis", "amb"])
            P["fx"] = rows + [f for f in (P.get("fx") or []) if isinstance(f, list)]
            if not isinstance(P.get("ann"), list):
                P["ann"] = []
            if isinstance(P["ann"], list):
                P["ann"].insert(0, {"k": "vl", "t": p["first"], "c": "up" if p["dir"] > 0 else "dn", "l": "Alerte grave", "pr": 3, "dz": 1})
    for p in act2:
        P = by.get(p.get("coin")) if p.get("coin") else None
        if isinstance(P, dict):
            if not isinstance(P.get("risk"), list):
                P["risk"] = []
            P["risk"].append(nonum(f"À SURVEILLER ({hm(p['first'])}) : {p['line']}"))


def main():
    try:
        F = json.load(open(FEED, encoding="utf-8"))
    except Exception as e:
        print("danger: flux illisible", type(e).__name__)
        return
    try:
        S = json.load(open(STATE, encoding="utf-8"))
        if not isinstance(S, dict):
            S = {}
    except Exception:
        S = {}
    clean(F)
    rk = F.get("risks")
    if isinstance(rk, list) and rk:
        S["risks_cache"] = {"at": NOW, "items": [r for r in rk if isinstance(r, dict)][:20]}
    run(F, S)
    pub = [public(a) for a in S["alerts"] if a.get("sev", 0) >= 2 and NOW - a.get("last", 0) < 24 * H]
    pub.sort(key=lambda p: (-p["sev"], -(p["last"] or 0)))
    pub = pub[:30]
    inject(F, S, pub)
    F["danger"] = pub
    F["danger_at"] = iso(NOW)
    tmp = FEED + ".tmp"
    json.dump(F, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.load(open(tmp, encoding="utf-8"))  # relu avant de remplacer : jamais de flux abîmé
    os.replace(tmp, FEED)
    os.makedirs(os.path.dirname(STATE) or ".", exist_ok=True)
    json.dump(S, open(STATE + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(STATE + ".tmp", STATE)
    g = [p for p in pub if p["sev"] >= 3 and p["active"]]
    print(f"danger: {len(g)} alerte(s) grave(s) en cours, {sum(1 for p in pub if p['sev'] == 2 and p['active'])} à surveiller"
          + (" · " + " | ".join(f"{p['asset']} {p['cat']}" for p in g) if g else ""))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("danger: erreur", type(e).__name__, e)
