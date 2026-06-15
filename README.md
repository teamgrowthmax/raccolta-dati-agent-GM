# GHL Daily Agent

Agent giornaliero di riconciliazione **GoHighLevel → Foglio "raccolta dati / foglio madre"**.
GHL è la fonte di verità; il foglio è lo specchio per le dashboard.

## Esecuzione
```bash
GHL_KEY=... SHEET_API=... AGENT_TODAY=$(date +%F) python3 run_daily.py run
```
- `dry` invece di `run` = calcola senza scrivere.
- Le credenziali si passano via variabili d'ambiente (`GHL_KEY`, `SHEET_API`).

## File
- `REGOLE.md` — specifica completa delle regole di business
- `common.py` — config, mapping campi/owner/pipeline, helper HTTP
- `engine.py` — logica delle 4 sezioni (pura)
- `run_daily.py` — orchestratore (fetch GHL → calcolo → scrittura → report)
- `report.py` — scrive il report nel tab "log agent" dello Sheet

Le picklist e i custom field vengono scaricati live da GHL ad ogni run.
