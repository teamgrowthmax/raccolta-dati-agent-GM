"""Engine dell'agent giornaliero di riconciliazione GHL -> Foglio.
Implementa le 4 sezioni di REGOLE.md. Calcola le scritture (no I/O qui: pura logica).
Le funzioni di fetch/scrittura sono in run_daily.py."""
import datetime, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

PLACE = {"", "-", "--", "/", "//", ".", "..", "n/a", "na", "n.a.", "no data"}

def nz(v): return v is not None and str(v).strip() != ""
def ne(s): return str(s if s is not None else "").strip().lower()
def dg(s): return re.sub(r"\D", "", str(s or ""))
def gnz(v):
    if v is None: return False
    vals = v if isinstance(v, list) else [v]
    return any(str(x).strip().lower() not in PLACE for x in vals)


# ---------------------------------------------------------------- date helpers
def parse_iso_or_it(s):
    """Ritorna date o None. Gestisce ISO e gg/mm/aaaa (italiano)."""
    s = str(s).strip().replace("T", " ").split(" ")[0]
    if not s: return None
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        try: return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except: return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = 2000 + y if y < 100 else y
        try:
            if b > 12 and a <= 12:
                return datetime.date(y, a, b)   # formato US mm/dd (a=mese)
            return datetime.date(y, b, a)       # gg/mm italiano (default)
        except: return None
    return None

def fmt_it(d):
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


# ================================================================
# SEZIONE 1 — flag pipeline Front End
# ================================================================
def compute_pipeline_flags(row, stato):
    """Ritorna lista di change {col,value} per i flag pipeline secondo REGOLE.md.
    stato = 'UPSELL' | nome stage FE | None (non in nessuna pipeline)."""
    changes = []
    if stato is None:
        return changes
    expected = C.expected_flags(stato)
    for key, col in C.FLAG_COLS.items():
        cur = ne(row.get(col))
        is_si = cur == "si"
        want = key in expected
        if key in C.FLAG_STORICI:
            if want and not is_si:
                changes.append({"col": col, "value": "SI", "op": "add"})
            # storici: mai rimossi
        else:  # FLAG_STATO
            if want and not is_si:
                changes.append({"col": col, "value": "SI", "op": "add"})
            elif (not want) and is_si:
                changes.append({"col": col, "value": "", "op": "remove"})
    return changes


def lead_stato(contact, fe_stage_id, in_upsell):
    """Determina lo stato logico del lead.
    Priorita': UPSELL (cliente chiuso) > stage FE > setter-only."""
    if in_upsell:
        return "UPSELL"
    if fe_stage_id and fe_stage_id in C.FE_STAGES:
        nome = C.FE_STAGES[fe_stage_id]
        # stage iniziali senza colonna: se setter -> fissato
        if nome in ("Lead candidatura", "lead optin", "lead setter", "Da Recuperare"):
            if is_setter(contact):
                return "Appuntamento fissato"
            return None
        return nome
    # nessuna opportunity: se setter -> fissato
    if is_setter(contact):
        return "Appuntamento fissato"
    return None


def is_setter(contact):
    tags = [ne(t) for t in (contact.get("tags") or [])]
    if C.SETTER_TAG in tags:
        return True
    # fallback: ha campi telegram compilati
    cf = cfmap(contact)
    tg_filled = sum(1 for _, fid, _ in C.TG_FIELDS if gnz(cf.get(fid)))
    return tg_filled >= 2


# ================================================================
# SEZIONE 2 — Telegram + NOME SETTER + data iscrizione
# ================================================================
LINK_FID = "80MrmQlYv54VU1yw71lu"
ID_CHAT_FID = "E1rokzfIvCi7ltONWWNE"

def compute_telegram(row, contact, picklists, leadnuovi=None):
    """Ritorna (changes_sheet, ghl_writes, warnings).
    ghl_writes = lista di (field_id, value) da scrivere su GHL (setter, link)."""
    changes = []; warnings = []; ghl_writes = []
    leadnuovi = leadnuovi or {}
    cf = cfmap(contact)
    for col, fid, kind in C.TG_FIELDS:
        gv = cf.get(fid)
        if col == "NOME SETTER":
            continue  # gestito sotto (uniformita' GHL+foglio)
        if col == "Link iscrizione Community":
            continue  # gestito sotto (logica NON PRESENTE / lead nuovi / NON TROVATO)
        if col == "Data iscrizione community telegram":
            ch = _data_iscrizione(row, col, gv, cf)
            if ch: changes.append(ch)
            continue
        if not gnz(gv):
            continue
        new = _render_tg(kind, gv)
        if not nz(row.get(col)):
            changes.append({"col": col, "value": new, "op": "fill"})
        elif not _same(kind, row.get(col), gv):
            changes.append({"col": col, "value": new, "op": "overwrite"})
    # --- LINK iscrizione Community (Sez.2) ---
    # Solo se il lead PROVIENE da Telegram (almeno un altro campo Telegram compilato).
    # Se tutti vuoti -> non e' Telegram -> link giustamente vuoto, niente da fare.
    tg_altri = [fid for _c, fid, _k in C.TG_FIELDS if fid != LINK_FID]
    proviene_da_telegram = any(gnz(cf.get(fid)) for fid in tg_altri)
    link_presente = gnz(cf.get(LINK_FID)) or nz(row.get("Link iscrizione Community"))
    if proviene_da_telegram and not link_presente:
        idchat = cf.get(ID_CHAT_FID)
        if not gnz(idchat):
            idchat = row.get("ID Chat Lead")  # fallback dal foglio
        idkey = re.sub(r"\D", "", str(idchat)) if gnz(idchat) else ""
        if not idkey:
            newlink = "NON PRESENTE"          # manca anche l'ID chat
        else:
            newlink = leadnuovi.get(idkey) or "NON TROVATO"  # cerca in "lead nuovi"
        changes.append({"col": "Link iscrizione Community", "value": newlink, "op": "linkfix"})
        ghl_writes.append((LINK_FID, newlink))
    # NOME SETTER: uniforma GHL + foglio
    g_setter = cf.get(C.NOME_SETTER_FID)
    if gnz(g_setter):
        canon = C.capitalize_name(g_setter)
        if str(g_setter).strip() != canon:
            ghl_writes.append((C.NOME_SETTER_FID, canon))  # da scrivere su GHL
        if ne(row.get("NOME SETTER")) != ne(canon):
            changes.append({"col": "NOME SETTER", "value": canon, "op": "setter"})
    elif nz(row.get("NOME SETTER")):
        canon = C.capitalize_name(row.get("NOME SETTER"))
        if row.get("NOME SETTER") != canon:
            changes.append({"col": "NOME SETTER", "value": canon, "op": "setter"})
    return changes, ghl_writes, warnings


def _data_iscrizione(row, col, gv, cf):
    """Disambiguazione data iscrizione community (REGOLE.md Sez.2). Non inverte alla cieca."""
    if not gnz(gv):
        return None
    sv = row.get(col)
    # se foglio gia' valorizzato e plausibile -> lascio
    anchor = parse_iso_or_it(cf.get(C.DATA_CREAZIONE_FID)) or None
    g_d = parse_iso_or_it(gv)
    if nz(sv):
        s_d = parse_iso_or_it(sv)
        if s_d and g_d and s_d == g_d:
            return None  # gia' uguale
        # se valore foglio plausibile rispetto all'anchor -> non toccare
        if s_d and anchor and abs((s_d - anchor).days) <= 45:
            return None
        if s_d and not anchor:
            return None  # senza anchor non rischio inversione
    if not g_d:
        return None
    return {"col": col, "value": fmt_it(g_d), "op": "fill" if not nz(sv) else "overwrite"}


def _render_tg(kind, gv):
    if isinstance(gv, list):
        return ", ".join(str(x) for x in gv if str(x).strip())
    if kind == "date":
        d = parse_iso_or_it(gv)
        return fmt_it(d) if d else str(gv)
    if kind in ("num", "money"):
        n = C.to_number(gv, allow_extract=True)
        return n if n is not None else str(gv)
    return str(gv)


# ================================================================
# SEZIONE 3 — dati vendita FE + Upsell 1-7
# ================================================================
def compute_vendita(row, contact, picklists):
    changes = []
    cf = cfmap(contact)
    fields = list(C.FE_FIELDS)
    for n in range(1, 8):
        fields += C.UP_FIELDS[n]
    for col, fid, kind in fields:
        gv = cf.get(fid)
        if not gnz(gv):
            continue
        val, warn = C.coerce(kind, gv, fid, picklists)
        if warn or val is None:
            continue
        disp = _render_tg(kind, gv) if kind not in ("multi",) else ", ".join(val) if isinstance(val, list) else val
        if not nz(row.get(col)):
            changes.append({"col": col, "value": disp, "op": "fill", "kind": kind})
        elif not _same(kind, row.get(col), gv, fid, picklists):
            changes.append({"col": col, "value": disp, "op": "overwrite", "kind": kind})
    # note R / AL copiate pari-pari (liste rese senza parentesi)
    for col, fid in C.NOTE_FIELDS:
        gv = cf.get(fid)
        if gnz(gv) and not nz(row.get(col)):
            disp = ", ".join(str(x) for x in gv if str(x).strip()) if isinstance(gv, list) else str(gv)
            changes.append({"col": col, "value": disp, "op": "note"})
    return changes


# ================================================================
# SEZIONE 4 — info lead (owner, data creazione)
# ================================================================
SALES_NAMES = set(C.USER_ID_TO_NAME.values())
MARCO_UID = "2alUFKiTUvdBXEVTpUlh"

def compute_owner(row, contact):
    """VENDITORE: allinea a GHL assignedTo con alias; Marco mai owner."""
    ga = contact.get("assignedTo")
    cur = str(row.get("VENDITORE") or "").strip()
    if not ga:
        # GHL vuoto: normalizza alias del valore foglio se mappabile
        uid, st = C.resolve_owner(cur)
        if st == "mapped":
            nm = C.USER_ID_TO_NAME.get(uid)
            if nm and nm != cur:
                return {"col": "VENDITORE", "value": nm, "op": "owner_norm"}
        return None
    if ga == MARCO_UID:
        # Marco e' solo setter: non deve essere owner. Tengo il foglio se valido sales.
        return None
    nm = C.USER_ID_TO_NAME.get(ga)
    if not nm:
        return None  # owner GHL sconosciuto: non tocco
    if nm != cur:
        return {"col": "VENDITORE", "value": nm, "op": "owner_align"}
    return None


def compute_data_creazione(row, contact, first_appt_date):
    """Foglio specchia campo custom GHL Data Creazione Lead.
    Se custom GHL vuoto -> usa primo appuntamento (gia' risolto a monte)."""
    cf = cfmap(contact)
    gv = cf.get(C.DATA_CREAZIONE_FID)
    ghl_write = None
    if not gnz(gv) and first_appt_date:
        gv = fmt_it(first_appt_date)
        ghl_write = gv  # scrivo anche su GHL il custom
    if not gnz(gv):
        return None, None
    d = parse_iso_or_it(gv)
    target = fmt_it(d) if d else str(gv)
    sv = row.get("DATA CREAZIONE LEAD")
    if nz(sv):
        sd = parse_iso_or_it(sv)
        if sd and d and sd == d:
            return None, ghl_write
    return {"col": "DATA CREAZIONE LEAD", "value": target, "op": "data_crea"}, ghl_write


# ---------------------------------------------------------------- shared
def cfmap(contact):
    return {f["id"]: f.get("value") for f in contact.get("customField", []) or []}

def _same(kind, sv, gv, fid=None, picklists=None):
    if kind == "date":
        a, b = parse_iso_or_it(sv), parse_iso_or_it(gv)
        return (a == b) if (a and b) else (str(sv).strip() == str(gv).strip())
    if kind in ("num", "money"):
        return C.to_number(sv, allow_extract=True) == C.to_number(gv, allow_extract=True)
    if kind == "multi" and picklists is not None:
        s = str(sv).strip()
        parts = [p.strip() for p in s.split(",")] if "," in s else [s]
        sf = set()
        for p in parts:
            if p:
                tok, _ = C.canon_token(fid, p, picklists); sf.add(C.fold(tok))
        gl = gv if isinstance(gv, list) else [gv]
        gf = set(C.fold(x) for x in gl if str(x).strip())
        return sf == gf
    return C.fold(sv) == C.fold(gv)
