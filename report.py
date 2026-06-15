"""Scrive il report giornaliero in un tab 'Log Agent' dentro lo Sheet Raccolta dati.
Usa l'API Apps Script (createSheet se manca, poi append righe di log)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C

LOG_SHEET = "Log Agent"
HEADERS = ["Data run", "Flag +", "Flag -", "Telegram", "Vendita", "Owner",
           "Data creaz.", "Setter GHL", "Backfill CID", "Lead non su foglio (recenti)", "Warning", "Note"]


def _resolve_name():
    """Trova il nome reale del tab di log (case-insensitive), altrimenti lo crea."""
    info = C.sheet_get({"action": "sheets"})
    names = [s["name"] for s in info.get("sheets", [])]
    for n in names:
        if n.strip().lower() == LOG_SHEET.lower():
            return n
    try:
        C.sheet_post({"action": "createSheet", "sheet": LOG_SHEET})
    except Exception as e:
        print("createSheet:", e)
    return LOG_SHEET


def ensure_sheet():
    """Risolve il tab e mette gli header se vuoto. Ritorna il nome reale."""
    name = _resolve_name()
    info = C.sheet_get({"action": "headers", "sheet": name})
    if not info.get("headers"):
        C.sheet_post({"action": "append", "sheet": name, "rows": [dict(zip(HEADERS, HEADERS))]})
    return name


def write_log(plan, ts, warnings_detail=True):
    """Aggiunge una riga di riepilogo + (opzionale) le righe di warning."""
    name = ensure_sheet()
    s = plan["stats"]
    # header reali del foglio (possono avere spazi extra) -> mappo i valori per posizione
    real = C.sheet_get({"action": "headers", "sheet": name}).get("headers", HEADERS)
    values = [ts, s["flag_add"], s["flag_remove"], s["tg"], s["vendita"], s["owner"],
              s["data_crea"], s["setter_ghl"], s["backfill"],
              s.get("not_on_sheet_recenti", 0), len(plan["warnings"]), ""]
    row = {real[i]: values[i] for i in range(min(len(real), len(values)))}
    C.sheet_post({"action": "append", "sheet": name, "rows": [row]})
    # blocco warning dettagliato (righe sotto, col 1 = descrizione)
    if warnings_detail and plan["warnings"]:
        h0, h1, hlast = real[0], real[1], real[-1]
        wr = [{h0: "  ⚠ " + w.get("warn", ""), h1: w.get("row"), hlast: w.get("nome", "")}
              for w in plan["warnings"][:200]]
        C.sheet_post({"action": "append", "sheet": name, "rows": wr})
    return row


if __name__ == "__main__":
    import json
    plan = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.json")))
    write_log(plan, "TEST")
    print("log scritto")
