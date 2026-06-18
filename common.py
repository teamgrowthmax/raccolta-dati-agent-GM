"""Config, mapping e helper HTTP per il sync vendite Sheet 'foglio madre' -> GHL."""
import json, os, re, time, unicodedata, urllib.request, urllib.parse, urllib.error

# ---------------------------------------------------------------- config
# Credenziali: SOLO da variabili d'ambiente (cloud) o da local_secrets.py (uso locale, NON su git).
# Nessun segreto hardcoded qui: il repo puo' stare anche pubblico in sicurezza.
try:
    import local_secrets as _LS  # file gitignored, presente solo in locale
except Exception:
    _LS = None
GHL_KEY = os.environ.get("GHL_KEY") or getattr(_LS, "GHL_KEY", "")
GHL_BASE = "https://rest.gohighlevel.com/v1"
SHEET_API = os.environ.get("SHEET_API") or getattr(_LS, "SHEET_API", "")
SHEET_NAME = "foglio madre"

PIPE_FRONTEND = "t1jvQKOuvSvNqhO2qftl"
PIPE_UPSELL = "zWruWF05ae6fdzfiADZI"
STAGE_FE_CHIUSO = "3fa92324-12a7-4258-80fe-f00400b1ada6"
STAGE_UP_LISTA = "de5f1d74-13ef-4991-8f46-b80d57cc14e8"

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
os.makedirs(os.path.join(CACHE, "contacts"), exist_ok=True)

# ---------------------------------------------------------------- owner map
# VENDITORE (sheet) -> GHL user id. Chiavi normalizzate lower/trim.
OWNER_MAP = {
    "rebecca russo": "3BvqTzqqsHQ7ZMK8kHeu", "rebecca": "3BvqTzqqsHQ7ZMK8kHeu",
    "nicholas martino": "xFEQx1Xuu4XqM53cVzi1", "nicholas": "xFEQx1Xuu4XqM53cVzi1",
    "valerio alteri": "tt5VSwqbupS4TI8Xpv6B", "valerio": "tt5VSwqbupS4TI8Xpv6B",
    "fabio picca": "ZUILtbpyYW42WQjtKDNJ", "fabio": "ZUILtbpyYW42WQjtKDNJ",
    "riccardo romboli": "nuK7C4KrXcO88yTCWpyn", "riccardo": "nuK7C4KrXcO88yTCWpyn",
    "riccardo setter": "nuK7C4KrXcO88yTCWpyn", "riccardo sales": "nuK7C4KrXcO88yTCWpyn",
    "francesco lucchetta": "xkwDIx7eq4oZgRPGWw3h",
    "marco scarpellini": "2alUFKiTUvdBXEVTpUlh", "marco": "2alUFKiTUvdBXEVTpUlh",
    "giacomo secci": "Z3x9GLndbYkgL0taPt7m",
    "giuseppe d'amore": "tVfhUxp0Qe44SczuuvWo", "giuseppe damore": "tVfhUxp0Qe44SczuuvWo",
    "alessio vescovo": "LsYev9Fo8vyqH2SBPZpX",
    "alessandro moia": "xFEQx1Xuu4XqM53cVzi1",  # riassegnato a Nicholas Martino
}
# id GHL -> nome canonico (per scrivere nello Sheet quando GHL vince)
USER_ID_TO_NAME = {
    "3BvqTzqqsHQ7ZMK8kHeu": "Rebecca Russo", "xFEQx1Xuu4XqM53cVzi1": "Nicholas Martino",
    "tt5VSwqbupS4TI8Xpv6B": "Valerio Alteri", "ZUILtbpyYW42WQjtKDNJ": "Fabio Picca",
    "nuK7C4KrXcO88yTCWpyn": "Riccardo Romboli", "xkwDIx7eq4oZgRPGWw3h": "Francesco Lucchetta",
    "2alUFKiTUvdBXEVTpUlh": "Marco Scarpellini", "Z3x9GLndbYkgL0taPt7m": "Giacomo Secci",
    "tVfhUxp0Qe44SczuuvWo": "Giuseppe D'amore", "LsYev9Fo8vyqH2SBPZpX": "Alessio Vescovo",
}
# owner sheet che NON sono utenti GHL -> non toccare nulla, solo report
OWNER_UNKNOWN = {"pierangelo", "gaetano", "samuele"}

def resolve_owner(venditore):
    """Ritorna (ghl_user_id|None, status). status: 'mapped'|'unknown'|'empty'."""
    v = (venditore or "").strip().lower()
    if not v:
        return None, "empty"
    if v in OWNER_MAP:
        return OWNER_MAP[v], "mapped"
    if v in OWNER_UNKNOWN:
        return None, "unknown"
    return None, "unknown"

# ---------------------------------------------------------------- field map
# (sheet_col, ghl_field_id, kind) ; kind: date|money|num|multi
FE_FIELDS = [
    ("DATA CHIUSURA", "3QS3YTvHAJ9hDaIjvyDO", "date"),
    ("BROKER", "Mn5wLjVDnoVhKnpypoSV", "multi"),
    ("STRATEGIA ADOTTATA 1", "MNO5xBD0JeT4XcxV4JCv", "multi"),
    ("STRATEGIA ADOTTATA 2", "2X34ZsfGiAePj1ALiUq7", "multi"),
    ("STRATEGIA ADOTTATA 3", "TA68oZDWMt7MFlMrX7Dt", "multi"),
    ("VALORE DEPOSITO", "LsaSA67RTZnZ3IGu0PYX", "money"),
    ("RANGE DEPOSITO", "klXN7zaHsM3Cr2tYMp7y", "multi"),
    ("USER ID PUPRIME", "ZucPu5B9fLL6ewEzF7X1", "num"),
    ("DEPOSITO CONFERMATO", "kaY5av0kkGxwPagbu4f3", "multi"),
    ("PROVENIENZA", "RthnLKdRfChMD8ZuyJJt", "multi"),
]
# upsell 1 e 2 = 10 campi (no LOTTI/SBLOCCATO che non hanno field GHL)
UP_FIELDS = {
    1: [("DATA CHIUSURA UPSELL 1","SIR94wRn27sVcRNDoTqW","date"),("BROKER UPSELL 1","3RrdcnZXzPnFn5gYHluF","multi"),
        ("STRATEGIA ADOTTATA UPSELL 1","DKHa8ta8EejASxYJp4yp","multi"),("VALORE DEPOSITO UPSELL 1","RJHDfhgFJdamSBLbt2Ty","money"),
        ("RANGE DEPOSITO UPSELL 1","tX0D0ym30RLegyhrQpT6","multi"),("USER ID PUPRIME UPSELL 1","fMx5TV3V5dWrLvNunw33","num"),
        ("DEPOSITO UPSELL 1 CONFERMATO","GS1qsOuR4OM37VOJHQ7X","multi"),("PROVENIENZA UPSELL 1","J1du305YZQTK66UmGDVR","multi")],
    2: [("DATA CHIUSURA UPSELL 2","jHEGAAHgilkC6Jr34DrL","date"),("BROKER UPSELL 2","HXmomxiCZliv4M4VTgmI","multi"),
        ("STRATEGIA ADOTTATA UPSELL 2","vpN7j4HfOkRoK2ZvHwgU","multi"),("VALORE DEPOSITO UPSELL 2","4I80aP14xwAaVO8vZtbL","money"),
        ("RANGE DEPOSITO UPSELL 2","3WW6RPkiEofNqts1zPAu","multi"),("USER ID PUPRIME UPSELL 2","AChNotOoTNcwOLktq9SG","num"),
        ("DEPOSITO UPSELL 2 CONFERMATO","bsBwTNfnwedJ3xyAexkT","multi"),("PROVENIENZA UPSELL 2","2URV9CSjrrZYtZJufDEv","multi")],
    3: [("DATA CHIUSURA UPSELL 3","D3dzy7964zEOWyJWE0HX","date"),("BROKER UPSELL 3","VOfKCy2mzXJ7TDK7denn","multi"),
        ("STRATEGIA ADOTTATA UPSELL 3","GKoC1kGpeC3FDW75khh4","multi"),("VALORE DEPOSITO UPSELL 3","WSHOE9XJVLHuoJtt3UT7","money"),
        ("RANGE DEPOSITO UPSELL 3","RtUEw2m1les2vRsGHR5R","multi"),("PROVENIENZA UPSELL 3","ZKT8g3HkStmrbhnPSlXO","multi")],
    4: [("DATA CHIUSURA UPSELL 4","Zn64OPsupzFIrZRm5PXf","date"),("BROKER UPSELL 4","XXE1s1gZeRUCXAqPd0Ez","multi"),
        ("STRATEGIA ADOTTATA UPSELL 4","mf65dTCM8R1k3iGTk7Z0","multi"),("VALORE DEPOSITO UPSELL 4","3rv3rfLhohBNq6RUQ5hi","money"),
        ("RANGE DEPOSITO UPSELL 4","Nu8WInhlY9dZJeNo7kes","multi"),("PROVENIENZA UPSELL 4","pAhCZwUmaeXKnFerL6kh","multi")],
    5: [("DATA CHIUSURA UPSELL 5","ZtFZ4Nla5fwULHedKF1Z","date"),("BROKER UPSELL 5","8RxGyAx1Twiw1po8FYHB","multi"),
        ("STRATEGIA ADOTTATA UPSELL 5","FW2HgFLyOkGE4RBCubo5","multi"),("VALORE DEPOSITO UPSELL 5","t2P4MddHOHNUqbKckm3y","money"),
        ("RANGE DEPOSITO UPSELL 5","sw3giSK90QVFSXc52teW","multi"),("PROVENIENZA UPSELL 5","gyTkpacWJ9WdB5P35eSH","multi")],
    6: [("DATA CHIUSURA UPSELL 6","6Rq4CsYByRwsZWLbrUUE","date"),("BROKER UPSELL 6","4sCs0aMSRZFkKjx50urh","multi"),
        ("STRATEGIA ADOTTATA UPSELL 6","AZiy2tVJzJH64NRdoAoi","multi"),("VALORE DEPOSITO UPSELL 6","LJjv8wIs7tc6jS0xIalq","money"),
        ("RANGE DEPOSITO UPSELL 6","Rb2yPNlknnSTUUPaYZwj","multi"),("PROVENIENZA UPSELL 6","jGiqmZXvn4uuLyXYe5zH","multi")],
    7: [("DATA CHIUSURA UPSELL 7","D5LWf0J7rfaxqUyx6C2i","date"),("BROKER UPSELL 7","2rn0Q4GcLjXImZlTeUM6","multi"),
        ("STRATEGIA ADOTTATA UPSELL 7","nhYNJWP4BnTEd9UjyvG3","multi"),("VALORE DEPOSITO UPSELL 7","slXfFRC8Hbx0GlGuyW1L","money"),
        ("RANGE DEPOSITO UPSELL 7","sBMqyAf366diIzh1SH42","multi"),("PROVENIENZA UPSELL 7","SETo4T2AVlwnJSEFTGuZ","multi")],
}
# DATI TELEGRAM (GHL TEXT/NUM -> colonna sheet) per il sync inverso GHL->Sheet
TG_FIELDS = [
    ("Data iscrizione community telegram", "wIGC5XPPFTR7QD7iaw2h", "date"),
    ("Username telegram", "PKrq10Jk3pvDNduziyMT", "text"),
    ("Nome Telegram", "UlP7SEqbzvECNcaLvuys", "text"),
    ("Link iscrizione Community", "80MrmQlYv54VU1yw71lu", "text"),
    ("ID Chat Lead", "E1rokzfIvCi7ltONWWNE", "num"),
    ("Data apertura chat", "3NNj4DDWIE306enhzDr4", "date"),
    ("NOME SETTER", "UNHi4V4tvuAu8RR4zdJs", "text"),
]

def reverse_fields():
    """Tutti i campi per il sync GHL->Sheet: FE + Upsell 1-7 + Telegram."""
    out = list(FE_FIELDS)
    for n in range(1, 8):
        out += UP_FIELDS[n]
    out += TG_FIELDS
    return out

# colonne usate per capire se un blocco vendita e' "presente"
FE_PRESENCE = ["DATA CHIUSURA", "VALORE DEPOSITO"]
def up_presence(n): return [f"DATA CHIUSURA UPSELL {n}", f"VALORE DEPOSITO UPSELL {n}"]

# ---------------------------------------------------------------- helpers
def nz(v):
    return v is not None and str(v).strip() != ""

def http_json(url, headers=None, data=None, method=None, tries=6):
    """GET/POST con retry+backoff su 429/403/5xx."""
    headers = headers or {}
    body = json.dumps(data).encode() if data is not None else None
    if body is not None:
        headers.setdefault("Content-Type", "application/json")
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 403, 500, 502, 503, 504):
                time.sleep(min(2 ** i, 30))
                continue
            raise
        except Exception as e:
            last = e
            time.sleep(min(2 ** i, 30))
    raise RuntimeError(f"HTTP fail {url}: {last}")

def ghl_get(path):
    return http_json(GHL_BASE + path, headers={"Authorization": "Bearer " + GHL_KEY, "User-Agent": "curl/7.88.1"})

def ghl_put(path, payload):
    return http_json(GHL_BASE + path, headers={"Authorization": "Bearer " + GHL_KEY, "User-Agent": "curl/7.88.1"},
                     data=payload, method="PUT")

def ghl_post(path, payload):
    return http_json(GHL_BASE + path, headers={"Authorization": "Bearer " + GHL_KEY, "User-Agent": "curl/7.88.1"},
                     data=payload, method="POST")

def sheet_get(params):
    return http_json(SHEET_API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "curl/7.88.1"})

def sheet_post(payload):
    return http_json(SHEET_API, headers={"User-Agent": "curl/7.88.1"}, data=payload, method="POST")

# ---------------------------------------------------------------- value coercion
def to_date(v):
    s = str(v).strip()
    # formati visti: '2026-03-01 00:00:00', '2026-03-05', '14/2/2026 14:31'
    for sep_fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            import datetime
            return datetime.datetime.strptime(s.split("T")[0] if "T" in s else s, sep_fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    if " " in s:
        s = s.split(" ")[0]
    # dd/mm/yyyy
    if "/" in s:
        try:
            import datetime
            d, m, y = s.split("/")
            return datetime.date(int(y), int(m), int(d)).strftime("%Y-%m-%d")
        except Exception:
            return None
    return s if len(s) == 10 else None

def to_number(v, allow_extract=False):
    s = str(v).strip().replace("€", "").replace("$", "").replace(".", "").replace(",", ".").replace(" ", "")
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except Exception:
        pass
    if allow_extract:  # es. USER ID '1271402 - 17297109' -> 1271402 ; salta note pure
        m = re.search(r"\d{3,}", str(v))
        if m:
            return int(m.group())
    return None

# ---------------------------------------------------------------- picklist normalization
def fold(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()

RANGE_MAP = {
    "0-299": "0$ - 299$", "300-499": "300$ - 499$", "500-999": "500$ - 999$",
    "1000-2999": "1.000$ - 2.999$", "3000": "3.000$ - 1.000.000$", "3000+": "3.000$ - 1.000.000$",
}
SYNONYMS = {  # fold(raw) -> opzione canonica
    "kudotrade": "KUDO", "pu prime": "Puprime", "puprime": "Puprime",
}

def canon_token(field_id, raw, picklists):
    """Normalizza un singolo token. Ritorna (valore, matched_bool)."""
    s = str(raw).strip()
    if s in RANGE_MAP:
        s = RANGE_MAP[s]
    f = fold(s)
    if f in SYNONYMS:
        s = SYNONYMS[f]
        f = fold(s)
    for o in picklists.get(field_id, []):
        if fold(o) == f:
            return o, True   # usa opzione esistente
    return s, False          # token nuovo (da aggiungere)

def coerce_multi(raw, field_id, picklists):
    """Split su virgola (NON su '+'), normalizza ogni token, dedup.
    Ritorna (lista_valori, lista_token_mancanti).
    raw puo' essere una LISTA (valori GHL multi-opzione) o una stringa (valori dal foglio)."""
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw]
    else:
        s = str(raw).strip()
        parts = [p.strip() for p in s.split(",")] if "," in s else [s]
    vals, missing, seen = [], [], set()
    for p in parts:
        if not p:
            continue
        tok, ok = canon_token(field_id, p, picklists)
        if tok not in seen:
            seen.add(tok)
            vals.append(tok)
        if not ok:
            missing.append(tok)
    return vals, missing

def load_picklists(cf_path):
    d = json.load(open(cf_path))
    return {f["id"]: (f.get("picklistOptions") or []) for f in d["customFields"]}

def coerce(kind, raw, field_id, picklists):
    """Ritorna (value_for_ghl, warning|None). value None = scarta."""
    if kind == "date":
        d = to_date(raw)
        return (d, None) if d else (None, f"data non parsabile: {raw!r}")
    if kind == "money":
        n = to_number(raw)
        return (n, None) if n is not None else (None, f"numero non parsabile: {raw!r}")
    if kind == "num":
        n = to_number(raw, allow_extract=True)
        return (n, None) if n is not None else (None, f"numero non parsabile: {raw!r}")
    if kind == "multi":
        vals, missing = coerce_multi(raw, field_id, picklists)
        if missing:
            return (None, f"valore/i non in picklist: {missing}")
        return (vals, None) if vals else (None, f"valore vuoto: {raw!r}")
    return (raw, None)


# ============================================================
# AGENT GIORNALIERO — costanti pipeline flag (Sezione 1 REGOLE.md)
# ============================================================

SETTER_TAG = "lead-setter-telegram"

# Stage pipeline Front End candidatura: id -> nome
FE_STAGES = {
    "2dd91662-51d7-4ce7-81e6-8d2ed455c848": "Lead candidatura",
    "7e7ea4d4-37a7-4d0c-9106-2c83490973be": "lead optin",
    "08d64512-dd47-48ca-a773-dea9144bbcd6": "lead setter",
    "4156179c-8366-485e-9eb6-b6f8fbb0cee0": "Non risponde",
    "c27b9775-02d8-4311-a90d-2201280aedec": "Fake",
    "7a69e38e-94c7-4f11-95aa-2034fcd397ba": "No Fissato",
    "d8ff8f00-68dd-41a1-a25c-4061a3a1b41e": "In contatto",
    "ada4a983-7a2d-46b2-95d4-61a437e7f4cd": "Appuntamento fissato",
    "67199a18-690e-4825-b96f-6ad52b091f24": "Appuntamento show",
    "49cef79b-ab24-45d0-8990-10d97f0d0bbf": "Appuntamento no show",
    "4b7da10a-77d3-40ae-9063-67b082309588": "In attesa pagamento",
    "3fa92324-12a7-4258-80fe-f00400b1ada6": "Chiuso",
    "047f56a1-3b32-41af-9872-3b5a59de25bb": "Perso",
    "17ee3f44-4725-495d-b471-336b465bbcbc": "Sospeso",
    "ea848635-ca72-4a68-922d-e2ddf073c0f5": "Da Recuperare",
}

# Colonne flag pipeline Front End sul foglio
FLAG_COLS = {
    "non_risponde": "NON RISPONDE",        # O
    "fake": "FAKE",                          # P
    "no_fissato": "NO FISSATO",              # Q
    "in_contatto": "IN CONTATTO",            # S
    "fissato": "APPUNTAMENTO FISSATO",       # T
    "show": "APPUNTAMENTO SHOW",             # U
    "no_show": "APPUNTAMENTO NO SHOW",       # V
    "attesa_pag": "IN ATTESA DI PAGAMENTO",  # W
    "chiuso": "CHIUSO",                       # X
    "perso": "PERSO",                         # AK
    "sospeso": "SOSPESO",                     # AM
}
# storici: una volta SI mai rimossi
FLAG_STORICI = {"fissato", "show", "chiuso"}
# stato corrente: SI se atteso, rimosso se non atteso
FLAG_STATO = {"non_risponde", "fake", "no_fissato", "in_contatto", "no_show",
              "attesa_pag", "perso", "sospeso"}

# campi custom note (copiate pari-pari, NON flag)
NOTE_FIELDS = [
    ("CAMPO CUSTOM NO FISSATO APPUNTAMENTO", "3WyUBws2g5Sb4Q5YvW02"),  # R
    ("CAMPO CUSTOM PER PERSO", "cfxKYPRGT9wI2HM3XaLX"),                # AL
]
DATA_CREAZIONE_FID = "7XySD7qkqkC364UNeYKj"
NOME_SETTER_FID = "UNHi4V4tvuAu8RR4zdJs"
DATA_ISCRIZIONE_TG_FID = "wIGC5XPPFTR7QD7iaw2h"


def expected_flags(stato):
    """Dato lo stato logico del lead, ritorna il set di flag-key attesi a SI.
    stato: 'UPSELL' | nome stage FE | 'SETTER_ONLY'."""
    if stato == "UPSELL":
        return {"fissato", "show", "chiuso"}
    M = {
        "Non risponde": {"non_risponde"},
        "Fake": {"fake"},
        "No Fissato": {"no_fissato"},
        "In contatto": {"in_contatto"},
        "Appuntamento fissato": {"fissato"},
        "Appuntamento show": {"show", "fissato"},
        "Appuntamento no show": {"no_show", "fissato"},
        "In attesa pagamento": {"attesa_pag", "show", "fissato"},
        "Chiuso": {"chiuso", "show", "fissato"},
        "Perso": {"perso", "show", "fissato"},
        "Sospeso": {"sospeso", "show", "fissato"},
    }
    return set(M.get(stato, set()))


def capitalize_name(s):
    """Forma canonica nome setter: trim + Capitalize di ogni parola."""
    s = re.sub(r"\s+", " ", str(s or "").strip())
    if not s:
        return ""
    return " ".join(w[:1].upper() + w[1:].lower() for w in s.split(" "))


def fetch_picklists_live():
    """Scarica i custom field da GHL e ritorna {fid: [opzioni]}. Per uso in cloud (no cache file)."""
    d = ghl_get("/custom-fields/")
    return {f["id"]: (f.get("picklistOptions") or []) for f in d.get("customFields", [])}
