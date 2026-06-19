# REGOLE — Agent giornaliero di riconciliazione GHL → Foglio "raccolta dati / foglio madre"

> Versione 1 — 2026-06-01. Questo documento è la **fonte di verità** delle regole.
> L'agent gira **in cloud ogni sera alle 23:00**, scrive **da solo** tutte le correzioni (auto-fix)
> e lascia un **report** in un tab dedicato dentro lo Sheet "Raccolta dati".

## Principio cardine
**GHL è la verità. Il foglio è lo specchio** che alimenta le dashboard di analisi.
In ogni conflitto, GHL comanda e il foglio si adegua. L'agent non scrive mai su GHL
(eccetto casi già concordati fuori da questa routine).

## Identificazione del lead (aggancio riga foglio ↔ contatto GHL)
Per ogni contatto GHL rilevante, trovo la riga sul foglio con priorità:
1. **Contact ID** (colonna BP) — chiave univoca, sempre prima
2. Email (D) — come check
3. Numero di telefono (E, ultime 9 cifre) — come check, **ma solo se NON è un numero-spazzatura**
   (cifre ripetute tipo `3333333333`, troppo corto, sequenze): i telefoni finti sono condivisi da
   molti lead diversi e creerebbero falsi accoppiamenti, quindi vengono ignorati nel match e nel backfill.

**L'agent CREA la riga sul foglio** per i lead **lavorati e recenti** non ancora presenti (con tutti i
dati GHL + Contact ID). Criterio:
- **Lavorato** = stato avanzato: in pipeline UPSELL (cliente), oppure stage Front End da "Appuntamento
  fissato" in poi (show/no show/in attesa/perso/sospeso/chiuso), oppure ha dati vendita/telegram/tag setter.
  NON i lead "freddi" (candidatura/optin/non risponde/fake/no fissato/in contatto).
- **Recente** = attività nel **2026+** (data creazione GHL o data chiusura FE/upsell). I **vecchi clienti
  storici** (2024-2025, es. lista clienti UPSELL importata) **NON vengono creati**.
- I lead recenti ma "freddi" (non creabili) → solo segnalati nel report.
Se la riga esiste ma ha Contact ID vuoto e il match per email/tel (pulito) è univoco → **backfill del Contact ID**.

---

## SEZIONE 1 — Pipeline Front End: flag SI (colonne O,P,Q,S,T,U,V,W,X,AK,AM)

L'agent legge lo **stato attuale** del lead su GHL e accende/spegne i flag SI sul foglio.

### Stato attuale = come lo determino
- Se il lead ha un'opportunity nella pipeline **UPSELL** (qualsiasi stage) → è un **cliente chiuso**
  (l'automazione GHL, allo spostamento su "Chiuso", imposta l'opportunity FE su WON, la chiude e
  fa comparire il lead in UPSELL/Lista Clienti). Quindi **non lo troverò più in Front End**.
- Altrimenti leggo lo **stage corrente** dell'opportunity nella pipeline **Front End candidatura**.
- Regola **setter**: se il lead ha tag `lead-setter-telegram` (o equivalente) **e** i campi Telegram
  compilati, è stato creato da un setter che prende appuntamento diretto → conta come **Appuntamento fissato**.

### Mappa stato → flag attesi (con implicazioni cumulative)
| Stato GHL | Flag attesi a SI |
|---|---|
| In UPSELL (qualsiasi stage) = cliente chiuso | **T + U + X** |
| Non risponde | **O** |
| Fake | **P** |
| No Fissato | **Q** |
| In contatto | **S** |
| Appuntamento fissato (o lead da setter) | **T** |
| Appuntamento show | **U + T** |
| Appuntamento no show | **V + T** |
| In attesa di pagamento | **W + U + T** |
| Perso | **AK + U + T** |
| Sospeso | **AM + U + T** |

### Categorie dei flag (regola di scrittura/rimozione)
- **🔒 STORICI — una volta SI non si tolgono mai:** `T` (Fissato), `U` (Show), `X` (Chiuso)
  Sono tappe raggiunte: restano per sempre.
- **🔄 STATO CORRENTE — SI se e solo se è lo stage attuale, altrimenti RIMOSSO:**
  `O` (Non risponde), `P` (Fake), `Q` (No Fissato), `S` (In contatto), `V` (No show),
  `W` (In attesa pagamento), `AM` (Sospeso), `AK` (Perso)

### Conseguenze pratiche delle categorie
- O/P/Q sono **esclusivi tra loro**: se il lead è in uno, gli altri due vanno tolti.
- Quando un lead **chiude** (compare in UPSELL) o va **Perso**: tolgo `W` e `AM` se presenti.
- `V` (no show): appena il lead si muove (sospeso/attesa/perso/chiuso) l'appuntamento di fatto
  è avvenuto → metto `U` (Show) e **tolgo `V`**.
- `S` (in contatto): stage transitorio (lead da richiamare); se si sposta → tolto.
- `AK` (Perso): se da perso il lead viene chiuso o rispostato → **tolgo `AK`**.

### Correzione errori di compilazione
Se sul foglio c'è un SI che **non** corrisponde allo stato GHL attuale (per i flag di stato corrente),
l'agent lo corregge. Esempio: GHL = Sospeso, foglio = SI su "In attesa di pagamento" → tolgo W,
metto AM+U+T. (Tipico caso: il sales aveva sbagliato stage e poi corretto, ma il SI era rimasto.)
Se il SI già presente è **coerente** con lo stato GHL → lasciato invariato.

### Colonne R e AL (NON sono flag SI)
- `R` (campo custom NO FISSATO APPUNTAMENTO) e `AL` (campo custom PER PERSO): se su GHL ci sono
  **note testuali**, vengono **copiate pari pari** sul foglio. Qui non va mai il valore "SI".

---

## SEZIONE 2 — Dati Telegram (colonne BS → BY)

Sorgente: campi custom GHL sotto "DATI TELEGRAM".
| Foglio | GHL |
|---|---|
| BS Data iscrizione community telegram | Data iscrizione community telegram |
| BT Username telegram | Username telegram |
| BU Nome Telegram | Nome Telegram |
| BV Link iscrizione Community | Link iscrizione Community |
| BW ID Chat Lead | ID Chat Lead |
| BX Data apertura chat | data apertura chat |
| BY NOME SETTER | Nome setter |

Regola: riempio le celle vuote + sovrascrivo quando il valore foglio è semanticamente diverso da GHL
(date confrontate per giorno; placeholder GHL tipo `-`,`/`,`no data` ignorati).
- Alcuni campi possono mancare legittimamente (es. Username telegram spesso assente).
- Può capitare che sia compilato **solo NOME SETTER** (lead entrati da altro funnel).
- **Link iscrizione Community (auto-fix)**: gestito **solo se il lead proviene da Telegram** (ha almeno un altro campo Telegram compilato). Se **tutti** i campi Telegram sono vuoti → il lead NON viene da Telegram → link giustamente vuoto, niente da fare.
  Se proviene da Telegram e il link manca, l'agent lo compila (su GHL **e** foglio):
  - **ID Chat Lead vuoto** → `NON PRESENTE`
  - **ID Chat Lead presente** → cerca l'ID nella colonna H (USER ID) del foglio **"lead nuovi"** (spreadsheet `1VgGQMUp2G2Z6ZZly8WVjK6uS89LaVQeAMssBpAYAxkQ`, letto via azione Apps Script `?action=leadnuovi`):
    - **trovato** → scrive il link dalla colonna D (LINK)
    - **non trovato** → `NON TROVATO`

### Regola NOME SETTER (BY) — uniformità su GHL **e** foglio
Il nome del setter deve essere scritto in **un'unica forma: prima lettera maiuscola** (es. `serena`→`Serena`,
`MARCO`→`Marco`, `giuseppe`→`Giuseppe`, `daniela`→`Daniela`). L'agent normalizza il valore **sia sul foglio
sia su GHL** (eccezione consapevole al principio "scrivo solo sul foglio": serve per non sballare le dashboard,
dove `serena` e `Serena` verrebbero contati come due setter diversi). La forma canonica è: trim + Capitalize
della/e parola/e del nome.

### Regola Data iscrizione community (BS) — disambiguazione formato, MAI inversione cieca
Spesso i setter copiano la data da un altro foglio in formato **mese/giorno/anno** (US), altre volte la
scrivono a mano in **giorno/mese/anno** (IT). L'agent **non deve invertire la data di default**.
Logica di disambiguazione:
- Uso come **àncora di plausibilità la Data Creazione Lead di GHL**: l'iscrizione alla community è
  tipicamente **vicina** alla creazione del lead.
- Se il valore già presente, letto in formato IT (gg/mm/aaaa), è **plausibile** (cade vicino / nello stesso
  periodo della data creazione GHL) → lo **lascio così**, non lo inverto.
- Inverto giorno/mese **solo** se l'interpretazione IT risulta palesemente implausibile (es. molti mesi
  prima della creazione del lead) **e** l'interpretazione invertita cade coerente con la data creazione.
- Se il giorno è > 12 l'ordine è univoco (non è ambiguo) → uso quello reale senza forzature.
- In caso di ambiguità non risolvibile con certezza → **non tocco** e segnalo nel report.
> Esempio: lead creato a giugno 2026, setter scrive `03/06/2026` → è il **3 giugno**, plausibile → lascio
> com'è (NON diventa 6 marzo).

---

## SEZIONE 3 — Dati di vendita (Front End: Y→AJ; Upsell 1-7)

Sorgente: campi custom GHL sotto "FRONTEND" e sotto ogni menu Upsell 1-7.
Mappatura colonna↔field ID già definita (vedi `common.py` / memory ghl-sheet-sales-field-mapping).
Regola: GHL comanda → riempio vuoti + sovrascrivo differenze semantiche (date per giorno,
range/picklist normalizzati, importi numerici). Formati normalizzati (date `gg/mm/aaaa`).
Colonne senza campo GHL (LOTTI, SBLOCCATO) non gestite.

---

## SEZIONE 4 — Info lead (colonne A→E)

| Col | Campo | Regola |
|---|---|---|
| A | VENDITORE (owner) | Allineo al valore GHL (assignedTo). Normalizzo alias: Fabio→Fabio Picca, Valerio→Valerio Alteri, Riccardo/Setter/Sales→Riccardo Romboli, Rebecca→Rebecca Russo, Nicholas→Nicholas Martino, Alessandro Moia→Nicholas Martino. **Marco Scarpellini = solo setter, mai owner**. Owner ignoti (Pierangelo/Gaetano/Samuele) → non toccare. |
| B | DATA CREAZIONE LEAD | Specchio del campo custom GHL "Data Creazione Lead". Se il campo custom GHL è vuoto → lo riempio con la data del **primo appuntamento** del contatto, poi rifletto sul foglio. Formato `gg/mm/aaaa`. |
| C | NOME E COGNOME | Da GHL se diverso/vuoto |
| D | EMAIL | Flag in report se diverge (no overwrite automatico) |
| E | NUMERO DI TELEFONO | Flag in report se diverge (no overwrite automatico) |
| BY | NOME SETTER | Capitalizzazione uniforme (prima lettera maiuscola) **su GHL e foglio** — vedi Sezione 2 |

---

## SEZIONE 5 — Pipeline UPSELL (colonne AN→AT)
**Per ora NON gestita.** (Riservata a una versione futura dell'agent.)

---

## OUTPUT — Report giornaliero
Tab dedicato dentro lo Sheet "Raccolta dati" (es. **"Log Agent"**), aggiornato ad ogni run con:
- data/ora run, n. righe analizzate
- riepilogo azioni per categoria (flag aggiunti/rimossi, dati vendita/telegram scritti, owner/setter
  allineati, data creazione, nuove righe aggiunte, backfill CID)
- **anomalie da rivedere a mano** (mai auto-fix): warning Link community mancante, email/tel divergenti,
  cronologia FE/Upsell incoerente, duplicati, lead non agganciabili.

## Esecuzione
- **Cloud, ogni sera alle 23:00.**
- **Auto-fix**: scrive tutte le correzioni a regola; le anomalie le mette solo in report.
- Quirk tecnici: GHL v1 richiede `User-Agent: curl/7.88.1`; POST allo Apps Script via Python urllib;
  scritture Sheet via `updateRows` in batch da 100; idempotente (rilanciabile senza danni).

---

## PUNTI APERTI / DA DEFINIRE PIÙ AVANTI
1. Notifica automatica al setter per Link community mancante (per ora solo report).
2. Gestione pipeline UPSELL (flag AN-AT).
3. Politica su email/telefono divergenti (per ora solo segnalazione).
