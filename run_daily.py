"""Agent giornaliero GHL -> Foglio. Orchestratore.
  python3 run_daily.py dry     -> calcola tutto, NON scrive (salva piano + stampa riepilogo)
  python3 run_daily.py run     -> esegue le scritture (sheet + GHL setter/data) + report su tab
Idempotente: rilanciabile senza danni.
"""
import sys, os, json, re, time, datetime, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C
import engine as E

HERE = os.path.dirname(os.path.abspath(__file__))
def hp(n): return os.path.join(HERE, n)
# picklist: prima prova file cache locale, altrimenti scarica da GHL (cloud)
_cf = os.path.join(C.CACHE, "custom_fields.json")
PICK = C.load_picklists(_cf) if os.path.exists(_cf) else C.fetch_picklists_live()
LEADNUOVI = {}  # mappa ID Chat -> Link community (dal foglio "lead nuovi"), caricata in build()

def dg(s): return re.sub(r"\D", "", str(s or ""))
def ne(s): return str(s if s is not None else "").strip().lower()
def nz(v): return v is not None and str(v).strip() != ""


# ---------------------------------------------------------------- FETCH
def scan_contacts():
    """Tutti i contatti GHL con customField+tags+assignedTo. -> dict id->contact"""
    out = {}; url = f"{C.GHL_BASE}/contacts/?limit=100"; seen = set(); last = None; page = 0
    while url:
        page += 1
        d = C.http_json(url, headers={"Authorization": "Bearer " + C.GHL_KEY, "User-Agent": "curl/7.88.1"})
        batch = d.get("contacts", []); new = 0
        for c in batch:
            if c["id"] in seen: continue
            seen.add(c["id"]); new += 1
            out[c["id"]] = c
        meta = d.get("meta", {}); sid = meta.get("startAfterId")
        if not batch or not meta.get("nextPageUrl") or new == 0 or sid == last: url = None
        else:
            last = sid; url = f"{C.GHL_BASE}/contacts/?limit=100&startAfter={meta.get('startAfter')}&startAfterId={sid}"
        if page % 50 == 0: print(f"  contacts {len(out)}", flush=True)
    return out

def scan_pipeline(pid):
    """contactId -> lista stage. Per FE: stage corrente; per UPSELL: presenza."""
    by = {}; url = f"{C.GHL_BASE}/pipelines/{pid}/opportunities?limit=100"; seen = set(); last = None
    while url:
        d = C.http_json(url, headers={"Authorization": "Bearer " + C.GHL_KEY, "User-Agent": "curl/7.88.1"})
        batch = d.get("opportunities", []); new = 0
        for o in batch:
            if o["id"] in seen: continue
            seen.add(o["id"]); new += 1
            ct = (o.get("contact") or {}).get("id")
            if ct: by.setdefault(ct, []).append({"stage": o.get("pipelineStageId"), "status": o.get("status"),
                                                  "updated": o.get("updatedAt")})
        meta = d.get("meta", {}); sid = meta.get("startAfterId")
        if not batch or not meta.get("nextPageUrl") or new == 0 or sid == last: url = None
        else: last = sid; url = f"{C.GHL_BASE}/pipelines/{pid}/opportunities?limit=100&startAfter={meta.get('startAfter')}&startAfterId={sid}"
    return by

def fetch_sheet():
    rows = []; off = 0
    while True:
        d = C.sheet_get({"action": "data", "sheet": C.SHEET_NAME, "limit": 2000, "offset": off})
        ch = d.get("data", [])
        if not ch: break
        rows += ch; off += len(ch)
        if len(ch) < 2000: break
    return rows

def first_appt_date(cid):
    try:
        ev = C.ghl_get(f"/contacts/{cid}/appointments").get("events", [])
        times = [e.get("startTime") for e in ev if e.get("startTime")]
        if not times: return None
        return E.parse_iso_or_it(min(times))
    except Exception:
        return None


# ---------------------------------------------------------------- BUILD PLAN
def build(dry=True):
    global LEADNUOVI
    print("FETCH mappa 'lead nuovi' (ID->Link)...", flush=True)
    try:
        LEADNUOVI = C.sheet_get({"action": "leadnuovi"}).get("map", {}) or {}
        print(f"  lead nuovi: {len(LEADNUOVI)} ID->Link", flush=True)
    except Exception as e:
        LEADNUOVI = {}
        print("  WARN: lead nuovi non caricato:", e, flush=True)
    print("FETCH contatti GHL...", flush=True)
    contacts = scan_contacts()
    print(f"  {len(contacts)} contatti", flush=True)
    print("FETCH pipeline FE + UPSELL...", flush=True)
    fe = scan_pipeline(C.PIPE_FRONTEND)
    up = scan_pipeline(C.PIPE_UPSELL)
    print(f"  FE opp contatti {len(fe)} | UPSELL contatti {len(up)}", flush=True)
    print("FETCH foglio...", flush=True)
    rows = fetch_sheet()
    print(f"  {len(rows)} righe", flush=True)

    # indici foglio
    by_cid = {}; by_email = {}; by_phone = {}
    for r in rows:
        cid = str(r.get("Contact ID") or "").strip()
        if cid: by_cid.setdefault(cid, r)
        em = ne(r.get("EMAIL"))
        if em: by_email.setdefault(em, r)
        ph = dg(r.get("NUMERO DI TELEFONO"))
        if len(ph) >= 9: by_phone.setdefault(ph[-9:], r)

    def find_row(c):
        r = by_cid.get(c["id"])
        if r: return r, "cid"
        em = ne(c.get("email"))
        if em and em in by_email: return by_email[em], "email"
        ph = dg(c.get("phone"))
        if len(ph) >= 9 and ph[-9:] in by_phone: return by_phone[ph[-9:]], "phone"
        return None, None

    # lead rilevanti: con opportunity FE o UPSELL (per flag) UNION righe foglio con CID (per sync)
    relevant_ids = set(fe) | set(up)
    cell_changes = []         # {row,col,value} per updateRows
    cell_removes = []         # idem value=""
    ghl_writes = []           # {cid, field, value} per PUT GHL (setter/data creazione)
    not_on_sheet = []         # lead GHL non sul foglio (SOLO segnalati, mai creati)
    backfill_cid = []         # {row, cid}
    stats = dict(flag_add=0, flag_remove=0, tg=0, vendita=0, owner=0, data_crea=0,
                 setter_ghl=0, datacrea_ghl=0, link_fix=0, link_ghl=0,
                 not_on_sheet=0, not_on_sheet_recenti=0, backfill=0)
    warnings = []

    # finestra "recente" per segnalare lead non sul foglio (possibile automazione fallita)
    _td = os.environ.get("AGENT_TODAY") or datetime.date.today().isoformat()
    today = datetime.date(*[int(x) for x in _td.split("-")])
    recent_cut = today - datetime.timedelta(days=7)

    # ---- FASE 1: ogni contatto rilevante reclama una riga (o nessuna) ----
    # row_claims: _row -> lista di (cid, contact, stato, via)
    row_claims = collections.defaultdict(list)
    for cid in relevant_ids:
        c = contacts.get(cid)
        if not c: continue
        r, via = find_row(c)
        in_upsell = cid in up
        fe_stage = fe.get(cid, [{}])[0].get("stage") if cid in fe else None
        stato = E.lead_stato(c, fe_stage, in_upsell)
        if r is None:
            # REGOLA: NON creo righe. Segnalo solo i lead RECENTI con stato rilevante.
            if stato and stato != "UPSELL":
                da = (c.get("dateAdded") or "")[:10]
                try: dadd = datetime.date(*[int(x) for x in da.split("-")]) if da else None
                except: dadd = None
                if dadd and dadd >= recent_cut:
                    not_on_sheet.append({"cid": cid, "nome": ((c.get("firstName") or "")+" "+(c.get("lastName") or "")).strip(),
                                         "email": c.get("email"), "tel": c.get("phone"), "stato": stato, "dateAdded": da})
                    stats["not_on_sheet_recenti"] += 1
            stats["not_on_sheet"] += 1
            continue
        row_claims[r["_row"]].append((cid, c, stato, via))

    # righe foglio con CID che non hanno opportunity: aggiungo claim (stato solo da setter)
    claimed_rows = set(row_claims.keys())
    row_by_num = {r["_row"]: r for r in rows}
    for r in rows:
        if r["_row"] in claimed_rows: continue
        cid = str(r.get("Contact ID") or "").strip()
        if not cid or cid not in contacts: continue
        c = contacts[cid]
        row_claims[r["_row"]].append((cid, c, E.lead_stato(c, None, False), "cid"))

    # ---- FASE 2: una riga = un solo contatto (dedup), poi accumulo ----
    for rownum, claims in row_claims.items():
        r = row_by_num.get(rownum)
        if not r: continue
        # scelta del contatto "proprietario": preferisci match per Contact ID
        cid_claims = [x for x in claims if x[3] == "cid"]
        chosen = cid_claims[0] if cid_claims else claims[0]
        if len(claims) > 1:
            # piu' contatti GHL mappano la stessa riga -> ambiguo: segnalo, processo il scelto
            warnings.append({"row": rownum, "nome": r.get("NOME E COGNOME"),
                             "warn": "riga agganciata da %d contatti GHL diversi (possibili duplicati)" % len(claims)})
        cid, c, stato, via = chosen
        if via != "cid" and not nz(r.get("Contact ID")):
            backfill_cid.append({"row": rownum, "cid": cid}); stats["backfill"] += 1
        _accumulate(r, c, stato, cell_changes, cell_removes, ghl_writes, warnings, stats)

    plan = {"cell_changes": cell_changes, "cell_removes": cell_removes, "ghl_writes": ghl_writes,
            "not_on_sheet": not_on_sheet, "backfill_cid": backfill_cid, "warnings": warnings, "stats": stats}
    json.dump(plan, open(hp("plan.json"), "w"), ensure_ascii=False)
    _print_summary(plan)
    return plan


def _accumulate(r, c, stato, cell_changes, cell_removes, ghl_writes, warnings, stats):
    rownum = r["_row"]
    # Sez 1: flag pipeline
    for ch in E.compute_pipeline_flags(r, stato):
        if ch["op"] == "remove":
            cell_removes.append({"row": rownum, "col": ch["col"], "value": ""}); stats["flag_remove"] += 1
        else:
            cell_changes.append({"row": rownum, "col": ch["col"], "value": ch["value"], "asText": True}); stats["flag_add"] += 1
    # Sez 2: telegram + setter + link
    tg_ch, tg_ghl, warns = E.compute_telegram(r, c, PICK, LEADNUOVI)
    for ch in tg_ch:
        cell_changes.append({"row": rownum, "col": ch["col"], "value": ch["value"], "asText": True})
        if ch.get("op") == "linkfix": stats["link_fix"] += 1
        elif ch.get("op") == "setter": stats["tg"] += 1
        else: stats["tg"] += 1
    for fid, val in tg_ghl:
        ghl_writes.append({"cid": c["id"], "field": fid, "value": val})
        if fid == C.NOME_SETTER_FID: stats["setter_ghl"] += 1
        elif fid == E.LINK_FID: stats["link_ghl"] += 1
    for w in warns:
        warnings.append({"row": rownum, "nome": r.get("NOME E COGNOME"), "warn": w})
    # Sez 3: vendita
    for ch in E.compute_vendita(r, c, PICK):
        cell_changes.append({"row": rownum, "col": ch["col"], "value": ch["value"], "asText": True}); stats["vendita"] += 1
    # Sez 4: owner
    ow = E.compute_owner(r, c)
    if ow:
        cell_changes.append({"row": rownum, "col": ow["col"], "value": ow["value"], "asText": True}); stats["owner"] += 1
    # Sez 4: data creazione (con eventuale primo appuntamento)
    cf = E.cfmap(c)
    fad = None
    if not E.gnz(cf.get(C.DATA_CREAZIONE_FID)):
        fad = first_appt_date(c["id"])
    dc, ghl_dc = E.compute_data_creazione(r, c, fad)
    if dc:
        cell_changes.append({"row": rownum, "col": dc["col"], "value": dc["value"], "asText": True}); stats["data_crea"] += 1
    if ghl_dc:
        ghl_writes.append({"cid": c["id"], "field": C.DATA_CREAZIONE_FID, "value": ghl_dc}); stats["datacrea_ghl"] += 1


def _build_new_row(c, stato, contacts):
    cf = E.cfmap(c)
    row = {"Contact ID": c["id"],
           "NOME E COGNOME": ((c.get("firstName") or "") + " " + (c.get("lastName") or "")).strip(),
           "EMAIL": c.get("email") or "", "NUMERO DI TELEFONO": c.get("phone") or ""}
    ga = c.get("assignedTo")
    if ga and ga != E.MARCO_UID:
        row["VENDITORE"] = C.USER_ID_TO_NAME.get(ga, "")
    # flag pipeline
    for key in C.expected_flags(stato or ""):
        row[C.FLAG_COLS[key]] = "SI"
    # telegram + vendita
    for col, fid, kind in C.TG_FIELDS:
        if E.gnz(cf.get(fid)) and col != "NOME SETTER":
            row[col] = E._render_tg(kind, cf.get(fid))
    g_setter = cf.get(C.NOME_SETTER_FID)
    if E.gnz(g_setter): row["NOME SETTER"] = C.capitalize_name(g_setter)
    fields = list(C.FE_FIELDS)
    for n in range(1, 8): fields += C.UP_FIELDS[n]
    for col, fid, kind in fields:
        gv = cf.get(fid)
        if E.gnz(gv):
            val, warn = C.coerce(kind, gv, fid, PICK)
            if not warn and val is not None:
                row[col] = ", ".join(val) if isinstance(val, list) else E._render_tg(kind, gv)
    dc = cf.get(C.DATA_CREAZIONE_FID)
    if E.gnz(dc):
        d = E.parse_iso_or_it(dc); row["DATA CREAZIONE LEAD"] = E.fmt_it(d) if d else str(dc)
    return row


def _print_summary(plan):
    s = plan["stats"]
    print("\n===== PIANO AGENT =====")
    print(f"Flag pipeline:    +{s['flag_add']} aggiunti / -{s['flag_remove']} rimossi")
    print(f"Telegram:         {s['tg']} celle")
    print(f"Vendita FE/Upsell:{s['vendita']} celle")
    print(f"Owner allineati:  {s['owner']}")
    print(f"Data creazione:   {s['data_crea']} (su GHL custom: {s['datacrea_ghl']})")
    print(f"NOME SETTER su GHL:{s['setter_ghl']}")
    print(f"Link community fix:{s['link_fix']} celle foglio (+{s['link_ghl']} su GHL)")
    print(f"Backfill CID:     {s['backfill']}")
    print(f"Lead non sul foglio: {s['not_on_sheet']} (di cui RECENTI da segnalare: {s['not_on_sheet_recenti']})")
    print(f"Warning:          {len(plan['warnings'])}")
    tot = s['flag_add']+s['flag_remove']+s['tg']+s['vendita']+s['owner']+s['data_crea']
    print(f"TOTALE celle foglio da scrivere: ~{tot}")


# ---------------------------------------------------------------- EXECUTE
def execute(plan):
    import report
    started = None  # timestamp passato da fuori (cloud)
    # 1) GHL writes (setter + data creazione custom)
    for w in plan["ghl_writes"]:
        try:
            C.ghl_put("/contacts/" + w["cid"], {"customField": {w["field"]: w["value"]}})
        except Exception as e:
            print("ghl write err", w["cid"], e)
        time.sleep(0.15)
    # 2) backfill CID + nuove righe + celle: tutto via updateRows/append
    allcells = plan["cell_changes"] + [{**x, "asText": True} for x in plan["cell_removes"]]
    for x in plan["backfill_cid"]:
        allcells.append({"row": x["row"], "col": "Contact ID", "value": x["cid"], "asText": True})
    ok = 0
    for i in range(0, len(allcells), 100):
        chunk = allcells[i:i+100]
        r = C.sheet_post({"action": "updateRows", "sheet": C.SHEET_NAME,
                          "changes": [{"row": c["row"], "col": c["col"], "value": c["value"], "asText": c.get("asText", True)} for c in chunk]})
        ok += r.get("applied", 0)
        time.sleep(0.3)
    print(f"celle scritte: {ok}/{len(allcells)}")
    # NB: l'agent NON crea righe nuove (regola). I lead non sul foglio sono solo nel report.
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "dry"
    plan = build(dry=(cmd == "dry"))
    if cmd == "run":
        execute(plan)
        try:
            import report
            ts = os.environ.get("AGENT_TODAY") or datetime.date.today().isoformat()
            report.write_log(plan, ts)
            print("Report scritto su tab 'log agent'")
        except Exception as e:
            print("report err:", e)
        print("RUN completato")
