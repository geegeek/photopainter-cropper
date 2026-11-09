# Storybook Prompt Builder — Specifiche di progetto

Strumento web locale per costruire, tavola per tavola, i prompt (testo + immagini di riferimento)
con cui generare le illustrazioni di un libro per bambini mantenendo **coerenza dei personaggi**
e **personalizzazione degli ambienti** (foto reali della propria casa, giardino, paese).

Versione documento: 1.0 — 2026-11-09
Stato: specifica da implementare

---

## 0. Il problema e il principio guida

Oggi il flusso è manuale: si scrive lo storyboard con ChatGPT, si copia a mano in ogni prompt la
parte fissa (identità grafica + modello dei protagonisti), si allega ogni volta la scheda
personaggi, e si spera che il risultato resti coerente su 30 tavole.

Il tool deve rendere questo flusso **componibile e ripetibile**. Il principio è uno solo:

> **Il prompt di una tavola non si scrive: si compila.**
> Il prompt finale è il risultato deterministico di blocchi riutilizzabili (identità grafica,
> personaggi, asset, regole di continuità) più la descrizione specifica della scena.
> L'utente decide *quali* blocchi entrano in una tavola; il motore decide *come* assemblarli.

Conseguenza pratica rispetto all'idea iniziale "trascino tutto in ogni tavola": i blocchi
**globali** (identità grafica, formato, divieti) entrano automaticamente in tutte le tavole e non
vanno trascinati. Il drag & drop serve per le cose che cambiano da tavola a tavola:

- quali **personaggi** compaiono;
- quali **asset fotografici** (la mia casa, il mio giardino) fanno da riferimento;
- quali **render già approvati** di altre tavole vanno riusati come riferimento di continuità;
- l'**ordine** delle tavole e il loro **stato** di lavorazione (colonne tipo Trello).

---

## 1. Glossario

| Termine | Significato |
|---|---|
| **Progetto (Libro)** | Un libro illustrato. Contiene tutto quanto segue. Una cartella su disco. |
| **Identità grafica** | Blocco testuale fisso: stile pittorico, forme, palette, formato pagina, divieti (no 3D, no testo in immagine…). Entra in ogni prompt. |
| **Character sheet** | L'immagine "I nostri protagonisti" generata dalle foto reali e approvata. È l'immagine di riferimento n. 1 di ogni prompt in cui compare almeno un protagonista. |
| **Personaggio** | Un protagonista umano o un personaggio secondario (Tobia, Nino…). Ha nome, descrizione canonica, elementi identificativi, abiti per scena, eventuale ritaglio immagine. |
| **Asset di riferimento** | Un'immagine esterna (foto della casa, del giardino, del passeggino, del paese) con un **alias** (es. `casa`) e una descrizione. Quando una tavola lo collega, il prompt dice "quando si parla di *casa* si intende l'immagine di riferimento N". |
| **Oggetto fisso** | Oggetto ricorrente descritto solo a testo (Stella delle Radici, Fiore della Stella). Può avere un'immagine di riferimento una volta che un render lo mostra bene. |
| **Ambiente ricorrente** | Luogo che deve restare uguale in più tavole (giardino, tunnel, piazza, cameretta). Può puntare a un asset fotografico e/o a un render approvato. |
| **Tavola** | Una pagina del libro: numero, titolo, parte, descrizione IMMAGINE, TESTO, collegamenti a personaggi/asset/ambienti, note di continuità, stato. |
| **Prompt compilato** | Il testo finale + la lista ordinata di immagini allegate, prodotto dal motore di composizione per una tavola. È ciò che viene inviato all'API. |
| **Render** | Un'immagine generata per una tavola. Una tavola ha N render (versioni); al massimo uno è **approvato**. |
| **Pagina finale** | Il render approvato impaginato in 4:5 con il TESTO nella fascia inferiore. |

---

## 2. Flusso di lavoro end-to-end

```
FASE 0  Setup progetto          → nome libro, identità grafica (da template modificabile)
FASE 1  Protagonisti            → carico foto reali → genero character sheet → approvo
                                 → estraggo descrizioni canoniche (LLM vision) → correggo → blocco
FASE 2  Asset & secondari       → carico foto (casa, giardino…), assegno alias + descrizione
                                 → definisco personaggi secondari e oggetti fissi (testo)
FASE 3  Storyboard              → A) genero da brief con LLM (output JSON)   oppure
                                   B) incollo storyboard esistente → LLM lo converte in JSON
                                 → nascono le card delle tavole, già con personaggi suggeriti
FASE 4  Board (Trello)          → per ogni tavola: verifico personaggi, collego asset,
                                   aggiungo note, vedo l'anteprima del prompt compilato
FASE 5  Generazione             → una chiamata API per tavola → render → confronto versioni
                                 → approvo / rigenero / modifica guidata
FASE 6  Finalizzazione          → testo nella fascia (locale, Pillow) oppure via AI
                                 → export PDF/ZIP di tutte le pagine
```

Ogni fase è indipendente: si può saltare la generazione dello storyboard e importarne uno,
si può rigenerare il character sheet in corso d'opera (con avviso che le tavole già generate
potrebbero perdere coerenza).

---

## 3. Architettura

### 3.1 Scelte

| Componente | Scelta | Motivazione |
|---|---|---|
| Backend | **Python 3.11+ / FastAPI** | Coerente con il repo esistente (Python, SDK `openai`, `.env`). Serve API REST + file statici. |
| Frontend | **Vue 3 via CDN (ESM, senza build) + SortableJS** | Trello-like con drag & drop senza toolchain Node. Alternativa se si preferisce: React + Vite. |
| Storage | **Filesystem: una cartella per progetto con `project.json` + immagini** | Trasparente, versionabile, backup = copia cartella. Nessun DB da gestire. |
| Immagini AI | **OpenAI Images API** (`images.edit` con immagini di riferimento multiple) | Richiesta esplicita: generazione con ChatGPT/OpenAI, chiamate singole per tavola. |
| Testo AI | Provider astratto: **OpenAI** o **Anthropic** | Storyboard e descrizioni possono essere fatte con Claude; immagini sempre OpenAI. |
| Impaginazione | **Pillow** | Già in `requirements.txt`. Crop 4:5, fascia testo, export. |
| PDF | `img2pdf` o Pillow (`save(..., save_all=True)`) | Export del libro. |

### 3.2 Topologia

```
Browser (127.0.0.1:8765)  ──HTTP──▶  FastAPI (uvicorn, bind solo su loopback)
                                        ├── /api/...            REST JSON
                                        ├── /files/...          immagini dei progetti (static)
                                        └── /                   SPA (index.html, app.js, css)
                                              │
                                              ├── openai SDK  ──▶ api.openai.com   (immagini + testo)
                                              └── anthropic SDK ─▶ api.anthropic.com (solo testo, opz.)
```

- La chiave API vive **solo nel backend** (`.env`, come nel repo attuale). Il browser non la vede mai.
- Bind su `127.0.0.1`. Nessuna autenticazione (uso personale locale); documentare che esporre la
  porta su LAN richiede almeno un token statico.
- Chiamate lunghe (generazione immagine 20–90 s) gestite come **job asincroni**: `POST` ritorna
  `job_id`, il frontend fa polling su `/api/jobs/{id}` (o SSE). Evita timeout e permette di
  mostrare lo stato "In generazione…" sulla card.

### 3.3 Struttura su disco

```
storybook-prompt-builder/
├── backend/
│   ├── main.py               # FastAPI app, mount static, router
│   ├── models.py             # Pydantic: Project, Character, Asset, Plate, Render…
│   ├── storage.py            # load/save project.json, gestione file
│   ├── composer.py           # MOTORE DI COMPOSIZIONE DEL PROMPT
│   ├── providers/
│   │   ├── base.py           # interfacce TextProvider, ImageProvider
│   │   ├── openai_images.py  # images.edit / images.generate
│   │   ├── openai_text.py    # responses/chat + structured output
│   │   └── anthropic_text.py # messages + JSON output
│   ├── jobs.py               # coda job in-process (asyncio) + stato
│   ├── layout.py             # Pillow: pagina 4:5, fascia testo, export
│   └── templates/            # prompt template (.md/.txt) editabili dall'utente
│       ├── identita_grafica_default.md
│       ├── character_sheet.md
│       ├── estrai_descrizioni.md
│       ├── genera_storyboard.md
│       ├── importa_storyboard.md
│       └── tavola.md
├── frontend/
│   ├── index.html
│   ├── app.js                # Vue 3 (ESM da CDN)
│   ├── components/…
│   └── style.css
├── projects/                 # DATI (in .gitignore)
│   └── <slug-progetto>/
│       ├── project.json
│       ├── sources/          # foto reali caricate (volti, casa, giardino…)
│       ├── character_sheet/  # versioni generate del character sheet
│       ├── renders/<NN>/     # render per tavola: v1.png, v2.png, …
│       ├── pages/            # pagine finali impaginate 4:5
│       └── prompts/<NN>/     # prompt compilati inviati (log, riproducibilità)
├── .env.example
├── requirements.txt
└── README.md
```

---

## 4. Modello dati (`project.json`)

Notazione: campi Pydantic; `id` sono stringhe corte univoche (es. `nanoid`).

### 4.1 Project
```
id, name, slug, created_at, updated_at
style:            StyleIdentity
format:           PageFormat
characters:       [Character]          # protagonisti e secondari
assets:           [Asset]              # immagini di riferimento con alias
fixed_objects:    [FixedObject]        # oggetti descritti a testo
environments:     [Environment]        # ambienti ricorrenti
character_sheet:  CharacterSheet | null
continuity_rules: [str]                # "Controlli di continuità" globali, numerati
plates:           [Plate]              # le tavole
parts:            [Part]               # PARTE I, II… (id, titolo, ordine)
settings:         ProjectSettings      # modelli, qualità, size, provider testo…
```

### 4.2 StyleIdentity (blocco fisso, entra in ogni prompt)
```
technique:        str   # "gouache e acquerello, texture carta…"
character_style:  str   # "fumettosi, forme morbide, contorni delicati…"
mood:             str   # "caldo, accogliente, magico"
negatives:        [str] # "no fotorealismo", "no 3D", "no testo in immagine", …
extra:            str   # note libere
```

### 4.3 PageFormat
```
aspect:           "4:5"
illustration_pct: 80            # % di altezza occupata dall'illustrazione
text_band:        "chiara, uniforme"
render_strategy:  "square_plus_band" | "full_page"   # vedi §9
```

### 4.4 Character
```
id, name, role: "protagonist" | "secondary"
age_label:        str | null          # "3 anni"
canonical:        str                 # descrizione completa (paragrafo)
key_features:     [str]               # 3–5 elementi identificativi ("ciuffo castano mosso", …)
outfits:          { default: str, <tag>: str }   # es. { default: "felpa turchese…", notte: "pigiama azzurro" }
props:            [str]               # "piccola torcia a mano"
never:            [str]               # "non deve sembrare di 3 anni"
source_photos:    [file]              # foto reali (solo protagonisti)
sheet_crop:       {x,y,w,h} | null    # ritaglio sul character sheet per anteprima card
reference_image:  file | null         # per secondari: render approvato che li mostra bene
```

### 4.5 Asset (immagine di riferimento esterna)
```
id, alias: str                 # "casa", "giardino", "passeggino"  (univoco, usato nel prompt)
title, description: str        # cosa è e cosa deve essere preservato
file: path
adaptation_note: str           # "tradurre nello stile del libro, mantenere forma tetto e colore"
```

### 4.6 FixedObject / Environment
```
FixedObject: id, name, description, never: [str], reference_image: file | null
Environment: id, name, description, asset_id: id | null, reference_plate_id: id | null
             # reference_plate_id → il render approvato di quella tavola fa da riferimento
```

### 4.7 Plate (tavola)
```
id, number: int, title: str, part_id: id | null
status:            "draft" | "ready" | "generated" | "approved" | "final"
scene:             str            # blocco IMMAGINE dello storyboard
text:              str            # blocco TESTO (dialoghi), va in impaginazione
character_ids:     [id]           # chi compare
outfit_tags:       { character_id: tag }   # es. { luigi: "notte" }
asset_ids:         [id]
fixed_object_ids:  [id]
environment_ids:   [id]
continuity_plate_ids: [id]        # tavole il cui render approvato va allegato come riferimento
continuity_notes:  str            # "stesso giardino della tavola 1; luce al tramonto"
plate_negatives:   [str]          # divieti specifici ("nessun passeggino in cameretta")
prompt_override:   str | null     # se valorizzato sostituisce il prompt compilato (escape hatch)
renders:           [Render]
approved_render_id: id | null
final_page:        file | null
```

### 4.8 Render
```
id, version: int, file: path, created_at
prompt_text: str                  # prompt esattamente inviato
reference_files: [path]           # immagini allegate, in ordine
model, size, quality, input_fidelity
mode: "generate" | "edit_from_render" | "add_text"
parent_render_id: id | null       # per modifiche guidate
cost_estimate: float | null
notes: str
```

### 4.9 ProjectSettings
```
image_model:      "gpt-image-1.5"     # default; alternative: gpt-image-1, gpt-image-2
image_quality:    "high"
image_size:       "1024x1024"         # con render_strategy square_plus_band
input_fidelity:   "high"              # ignorato dai modelli che non lo supportano
text_provider:    "openai" | "anthropic"
text_model:       str
max_reference_images: 16
font_path, font_size_min, font_size_max   # impaginazione testo
```

---

## 5. Interfaccia utente

### 5.1 Schermate

1. **Progetti** — elenco cartelle in `projects/`, crea/duplica/apri.
2. **Setup libro** (tab):
   - *Identità grafica*: form sui campi di `StyleIdentity` + `PageFormat`, precompilato dal
     template; anteprima del blocco testuale risultante.
   - *Protagonisti*: card per personaggio con foto reali caricabili, campi canonici,
     key_features, outfits. Bottone **"Genera character sheet"** → mostra versioni, approvo una.
     Bottone **"Estrai descrizioni dalla scheda"** → LLM vision compila `canonical` e
     `key_features` (modificabili). Lucchetto **"Blocca protagonisti"**.
   - *Secondari, oggetti, ambienti*: liste editabili.
   - *Asset*: upload immagini, alias, descrizione, nota di adattamento.
3. **Storyboard** — due modalità:
   - *Genera*: textarea con brief (trama, morale, n. tavole, luoghi, personaggi coinvolti) →
     LLM → JSON → anteprima tabellare → "Crea tavole".
   - *Importa*: incolla testo esistente → LLM lo struttura → stessa anteprima.
   - L'anteprima mostra per ogni tavola i personaggi/asset **suggeriti automaticamente**
     (match per nome e per alias nel testo) con possibilità di correggere prima di creare.
4. **Board** (schermata principale, stile Trello) — vedi §5.2.
5. **Scheda tavola** (drawer/modal a destra) — vedi §5.3.
6. **Export** — anteprima pagine finali, download ZIP / PDF, download dei prompt in `.md`.
7. **Impostazioni** — modelli, qualità, provider testo, stima costi, font.

### 5.2 Board

- **Colonne = stato**: `Bozza` → `Prompt pronto` → `Generata` → `Approvata` → `Finale`.
  Trascinare una card tra colonne cambia `status` (con vincoli: non si può portare in
  `Approvata` senza un render approvato; il sistema sposta da solo in `Generata` al primo
  render e in `Approvata` all'approvazione).
- **Vista alternativa "Sequenza"**: griglia ordinata per `number`, raggruppata per Parte.
  Qui il drag riordina le tavole e rinumera.
- **Card**: numero, titolo, thumbnail (render approvato o ultimo render o placeholder),
  avatar dei personaggi collegati (ritagli dal character sheet), chip degli asset,
  badge "⚠ manca riferimento" se la scena cita un alias non collegato, badge versioni (v3).
- **Sidebar Libreria** (sinistra, collassabile): sezioni *Personaggi*, *Asset*, *Oggetti*,
  *Ambienti*, *Render approvati*. Ogni voce è un chip **trascinabile su una card** → crea il
  collegamento. Drop di un render approvato → aggiunge a `continuity_plate_ids`.
- **Azioni di massa**: seleziona N card → "Genera tutte" (coda sequenziale, una chiamata per
  tavola), "Segna pronte", "Esporta prompt".

### 5.3 Scheda tavola

Layout a due colonne.

Sinistra (input):
- Titolo, parte, numero.
- `scene` (IMMAGINE) e `text` (TESTO), textarea.
- Personaggi presenti (chip rimovibili + selettore outfit per ciascuno).
- Asset collegati, oggetti, ambienti, tavole di continuità.
- Note di continuità, divieti specifici.
- **Anteprima prompt compilato** (read-only, aggiornata live) con contatore caratteri e
  lista ordinata delle immagini che verranno allegate ("[1] character sheet, [2] casa.jpg,
  [3] render approvato tavola 01"). Pulsante "Copia prompt" per uso manuale su ChatGPT.
- Toggle "Usa prompt manuale" → sblocca `prompt_override`.

Destra (output):
- Galleria render (v1…vN) con confronto affiancato, zoom, data, modello.
- Pulsanti per render: **Approva**, **Rigenera** (stesso prompt), **Modifica guidata**
  (campo istruzione: "rendi il cielo più scuro" → `edit_from_render`), **Elimina**.
- Pannello "Pagina finale": anteprima 4:5 con testo; pulsanti **Impagina testo (locale)** e
  **Aggiungi testo via AI** (vedi §9); **Esporta pagina**.

---

## 6. Motore di composizione del prompt (`composer.py`)

Funzione pura: `compose(project, plate) -> CompiledPrompt { text: str, images: [path], warnings: [str] }`.

### 6.1 Ordine delle immagini allegate
```
[1]   character_sheet (se almeno un protagonista è presente)
[2..] asset collegati, nell'ordine della card
[..]  reference_image di secondari/oggetti presenti (se esistono)
[..]  render approvati delle continuity_plate_ids
```
Se il totale supera `max_reference_images` → warning e troncamento dal fondo.
Il prompt cita sempre le immagini **per posizione** ("immagine di riferimento 2").

### 6.2 Struttura del testo (template `tavola.md`, sezioni in quest'ordine)

```
A. IDENTITÀ GRAFICA         ← StyleIdentity (globale, invariato)
B. RIFERIMENTI VISIVI       ← una riga per immagine allegata: cosa è, come usarla
C. PERSONAGGI IN SCENA      ← per ciascun personaggio presente: nome, canonical, key_features,
                               outfit per il tag della tavola, never. Esplicita chi NON c'è se
                               la scena potrebbe suggerirlo ("Claudia non è presente").
D. OGGETTI E AMBIENTI       ← fixed_objects + environments collegati; se l'ambiente ha un
                               asset: "quando la scena dice 'casa' intende l'immagine N,
                               tradotta nello stile del libro: mantenere <adaptation_note>"
E. CONTINUITÀ               ← continuity_notes + regole globali pertinenti (filtrate per
                               keyword o selezionate manualmente)
F. SCENA                    ← plate.scene (testo dello storyboard, intatto)
G. FORMATO E DIVIETI        ← PageFormat + negatives globali + plate_negatives
```

Regole del motore:
- Tutto in italiano, frasi dichiarative, niente markdown nel prompt inviato (le API immagini
  lo leggono come testo piatto).
- Le descrizioni dei personaggi assenti **non** vengono incluse: riduce rumore e il rischio
  che il modello li aggiunga.
- Se `plate.text` contiene i dialoghi, **non** entra nel prompt immagine (sezione G ribadisce
  "nessuna scritta"). Entra solo nella fase di impaginazione.
- `warnings`: alias citato nella scena ma asset non collegato; personaggio nominato nella scena
  ma non collegato; nessun personaggio collegato; > 16 immagini; prompt > soglia caratteri.

### 6.3 Esempio di output (Tavola 01 del libro di esempio, abbreviato)

```
IDENTITÀ GRAFICA. Illustrazione 2D per libro di fiabe, personaggi fumettosi, forme morbide e
arrotondate, contorni delicati, colorazione acquerello e gouache, texture leggera di carta,
colori luminosi ma naturali, atmosfera calda, accogliente e magica. Stesso illustratore per
tutte le tavole.

RIFERIMENTI VISIVI. Immagine 1: scheda ufficiale dei quattro protagonisti; è il riferimento
principale per volti, capelli, proporzioni e abiti; non riprodurre volti fotografici.
Immagine 2: fotografia della casa reale della famiglia; quando la scena dice "casetta" si
intende questo edificio, reinterpretato nello stile del libro mantenendo forma del tetto,
colore delle pareti e posizione della porta.

PERSONAGGI IN SCENA.
Vittorio, 3 anni: bambino fumettoso, viso tondo, grandi occhi marroni, capelli castano scuro
folti e mossi con ciuffo ricurvo sulla fronte, guance rosate. Felpa turchese con zip arancione,
maglietta chiara, pantaloni blu scuro. Elementi chiave: ciuffo castano mosso, felpa turchese.
Lorenzo, 1 anno: […] Non deve mai sembrare un bambino di tre anni.
Papà Luigi: […] Fronte alta e barba corta sono gli elementi identificativi.
Mamma Claudia non è presente in questa scena.

OGGETTI E AMBIENTI. Passeggino di Lorenzo: verde scuro, struttura nera, cappottina verde
scuro, ruote nere. Giardino: questo è il giardino che tornerà nelle tavole 26–28; grande
albero, erba, zona di terra morbida.

CONTINUITÀ. Prima tavola: stabilisce l'aspetto del giardino e dell'albero. Abiti
dell'avventura.

SCENA. Ampia inquadratura del giardino della casetta a Montiglio Monferrato in un pomeriggio
soleggiato. […testo IMMAGINE dello storyboard…]

FORMATO E DIVIETI. Illustrazione quadrata a piena immagine, senza bordi, senza fascia per il
testo (verrà aggiunta in impaginazione). Nessuna scritta, numero, fumetto o dialogo nell'immagine.
Nessun fotorealismo, rendering 3D, plastica lucida, volti realistici. Non introdurre altri
personaggi umani.
```

---

## 7. Integrazioni LLM

Tutte le chiamate passano da `providers/` con interfacce:

```python
class ImageProvider:
    def generate(prompt, size, quality) -> bytes
    def edit(prompt, images: list[Path], size, quality, input_fidelity) -> bytes

class TextProvider:
    def structured(system, user, json_schema, images: list[Path] = []) -> dict
```

### 7.1 Character sheet (FASE 1)
- Input: foto reali dei protagonisti (1–3 per persona), nomi, età, abiti desiderati, stile.
- Chiamata: `images.edit(model, image=[foto…], prompt=template character_sheet.md, size="1536x1024", quality="high", input_fidelity="high")`.
- Il template chiede esplicitamente il layout dell'immagine di esempio: gruppo in alto, un
  riquadro per personaggio con tratti elencati, palette, riga di espressioni (gioia,
  meraviglia, curiosità, tenerezza). Chiede di **non** mettere testo (o, se si vuole il
  testo come nell'esempio, accettare possibili refusi: è solo un riferimento interno).
- Iterazione: ogni versione salvata; "Rigenera con nota" riusa la versione precedente come
  riferimento aggiuntivo + istruzione di correzione.
- Approvazione → `project.character_sheet`. Facoltativo: l'utente disegna i ritagli per gli
  avatar delle card (`sheet_crop`).

### 7.2 Estrazione descrizioni canoniche
- `TextProvider.structured(images=[character_sheet], schema=CharacterDescriptions)` →
  per ogni personaggio: `canonical`, `key_features`, `outfits.default`, `never`.
- L'utente rivede e corregge. Queste descrizioni vanno nel prompt di ogni tavola (sezione C).

### 7.3 Generazione storyboard da brief
- System prompt (`genera_storyboard.md`): ruolo di autore di libri illustrati per bambini,
  struttura in parti, numero tavole richiesto, per ogni tavola `scene` (descrizione visiva
  dettagliata, personaggi riconoscibili, luce, inquadratura) e `text` (dialoghi brevi,
  onomatopee), più `characters_present`, `environment`, `new_objects`, `continuity_notes`.
- Output: **JSON conforme a `storyboard.schema.json`** (structured output / JSON mode).
- Il brief include automaticamente la lista dei personaggi e asset del progetto, così il
  modello usa i nomi e gli alias giusti e l'auto-collegamento è affidabile.

### 7.4 Import di uno storyboard esistente
- Stesso schema, system prompt `importa_storyboard.md`: "converti fedelmente, non riscrivere,
  non inventare tavole". Se il testo contiene un blocco di identità grafica/personaggi,
  viene estratto in campi separati e proposto per aggiornare il progetto.

### 7.5 Generazione tavola
- `images.edit(model, image=compiled.images, prompt=compiled.text, size, quality, input_fidelity)`.
- Se `compiled.images` è vuota (tavola senza riferimenti) → `images.generate`.
- Salvataggio render + copia del prompt in `prompts/<NN>/vN.txt`.

### 7.6 Modifica guidata di un render
- `images.edit(image=[render_corrente] + riferimenti originali, prompt="Mantieni identica
  questa illustrazione tranne: <istruzione>. <blocco personaggi>")` → nuova versione con
  `parent_render_id`.

### 7.7 Controllo di coerenza (opzionale, v2)
- `TextProvider.structured(images=[character_sheet, render], schema=ConsistencyReport)` →
  punteggio per personaggio e lista difetti ("Luigi ha capelli troppo folti"). Mostrato sulla
  card come semaforo; utile per decidere cosa rigenerare prima di guardare 30 immagini.

### 7.8 Modelli
- Immagini: default `gpt-image-1.5`; selezionabili `gpt-image-1` e `gpt-image-2`.
  Verificato: l'endpoint di edit accetta fino a 16 immagini di riferimento; formati
  standard `1024x1024`, `1024x1536`, `1536x1024`; `input_fidelity` high/low sui modelli 1 e 1.5
  (il 2 lavora sempre ad alta fedeltà). Da verificare sulla documentazione ufficiale al
  momento dell'implementazione: formati flessibili di gpt-image-2 (un 4:5 nativo
  renderebbe opzionale la strategia §9), prezzi correnti.
- Testo: configurabile; qualsiasi modello recente con supporto a immagini in input e output
  JSON (OpenAI) oppure Anthropic (`claude-sonnet-5-5` consigliato per costo/qualità).

---

## 8. API REST del backend

Prefisso `/api`. Tutte le risposte JSON. Errori con `{error, detail}`.

| Metodo | Percorso | Scopo |
|---|---|---|
| GET/POST | `/projects` | lista / crea |
| GET/PUT/DELETE | `/projects/{slug}` | progetto completo / salva / elimina |
| POST | `/projects/{slug}/upload` | upload immagine (`kind`: source, asset, reference) |
| POST | `/projects/{slug}/character-sheet/generate` | job: genera sheet |
| POST | `/projects/{slug}/character-sheet/{id}/approve` | approva versione |
| POST | `/projects/{slug}/characters/extract` | job: descrizioni canoniche dallo sheet |
| POST | `/projects/{slug}/storyboard/generate` | job: da brief → JSON (non crea tavole) |
| POST | `/projects/{slug}/storyboard/import` | job: da testo → JSON |
| POST | `/projects/{slug}/plates/bulk` | crea tavole da JSON approvato |
| PUT | `/projects/{slug}/plates/{id}` | aggiorna tavola |
| POST | `/projects/{slug}/plates/reorder` | nuovo ordine (lista id) |
| GET | `/projects/{slug}/plates/{id}/prompt` | prompt compilato + immagini + warnings |
| POST | `/projects/{slug}/plates/{id}/generate` | job: genera render |
| POST | `/projects/{slug}/plates/{id}/renders/{rid}/edit` | job: modifica guidata |
| POST | `/projects/{slug}/plates/{id}/renders/{rid}/approve` | approva |
| POST | `/projects/{slug}/plates/{id}/final` | impagina testo (`mode`: local \| ai) |
| GET | `/projects/{slug}/export` | ZIP pagine + prompt (query `pdf=1`) |
| GET | `/jobs/{id}` | stato job: queued/running/done/error, result |
| GET/PUT | `/settings` | impostazioni globali (senza chiavi) |

Static: `/files/{slug}/...` serve la cartella progetto in sola lettura.

---

## 9. Impaginazione e post-processing (`layout.py`)

### 9.1 Strategia consigliata: "quadrato + fascia" (`square_plus_band`)
Osservazione: in una pagina 4:5, l'80% superiore dell'altezza è **esattamente un quadrato**
(larghezza W, altezza 1,25·W × 0,8 = W). Quindi:

1. si genera l'illustrazione **quadrata** (`1024x1024`, o più grande se il modello lo consente)
   a piena immagine, senza fascia;
2. il tool costruisce localmente la pagina 4:5 (es. 2048×2560): illustrazione in alto,
   fascia chiara in basso (20%) con il TESTO renderizzato da Pillow.

Vantaggi: layout deterministico, testo senza refusi, font coerente in tutto il libro,
rigenerare l'illustrazione non tocca il testo, e il modello usa tutto il frame per la scena.

### 9.2 Alternativa: pagina intera (`full_page`)
Si genera `1024x1536` (2:3) chiedendo al modello l'illustrazione nel 78% superiore e fascia
chiara sotto; il tool ritaglia a 4:5 (1024×1280) dall'alto. Meno controllabile; mantenuta per
chi vuole la fascia "dipinta" dal modello.

### 9.3 Testo nella fascia
- Locale: wrap automatico, dimensione font adattiva tra `font_size_min` e `font_size_max`,
  colore scuro su fascia chiara (colore fascia campionato dal bordo inferiore
  dell'illustrazione o fisso), margini configurabili, supporto a battute su righe separate.
- Via AI (su richiesta esplicita): `images.edit(image=[pagina_senza_testo], prompt="Aggiungi
  nella fascia inferiore chiara esattamente questo testo, in italiano, carattere leggibile
  per bambini, senza modificare l'illustrazione: «…»")`. Avviso in UI: possibili refusi,
  controllare sempre.

### 9.4 Export
- ZIP: `pages/01.png … 30.png`, `renders/`, `prompts/*.txt`, `project.json`.
- PDF: pagine in ordine, 300 dpi equivalenti.

---

## 10. Costi, errori, robustezza

- **Stima costi** prima di ogni generazione (tabella prezzi in `settings`, aggiornabile a mano)
  e totale speso per progetto (somma `cost_estimate` dei render).
- **Retry**: errori di rete/429 → 3 tentativi con backoff esponenziale (2, 4, 8 s). Errori di
  policy contenuti → nessun retry, messaggio chiaro sulla card.
- **Coda**: una generazione alla volta per default (configurabile 2–3 in parallelo).
- **Log**: ogni chiamata registrata in `prompts/<NN>/` con prompt, lista file, modello,
  parametri, durata, esito. Permette di riprodurre o incollare a mano su ChatGPT.
- **Salvataggio**: `project.json` scritto in modo atomico (file temporaneo + rename) e con
  backup `.bak` dell'ultima versione.

---

## 11. Requisiti non funzionali e sicurezza

- Avvio con un comando: `uvicorn backend.main:app --host 127.0.0.1 --port 8765` (o `python -m storybook`).
- Nessuna chiave nel frontend, nei log o in `project.json`.
- Upload: solo PNG/JPG/WebP, max 20 MB, nomi normalizzati, EXIF rimosso (le foto di famiglia
  contengono spesso GPS).
- `projects/` in `.gitignore`.
- Funziona offline per tutto tranne le chiamate AI (board, composizione, impaginazione, export).

---

## 12. Piano di implementazione

**M1 — Scheletro e composizione (senza AI)**
- FastAPI + storage + modello dati; SPA con Board e Scheda tavola; drag & drop (stati,
  ordine, chip → card); upload asset; motore di composizione con anteprima e "Copia prompt".
- Risultato utile già da solo: si incollano prompt e immagini a mano su ChatGPT.

**M2 — Generazione immagini**
- `openai_images.py`, job asincroni, galleria versioni, approvazione, modifica guidata,
  impaginazione locale del testo, export ZIP.

**M3 — Storyboard e personaggi assistiti**
- Import storyboard testuale → JSON → tavole con auto-collegamento; generazione da brief;
  character sheet da foto; estrazione descrizioni canoniche.

**M4 — Qualità**
- Controllo di coerenza vision, stima costi, PDF, testo via AI, template modificabili da UI.

---

## 13. Decisioni prese e punti aperti

Decisioni:
- I blocchi globali non si trascinano: entrano sempre. Il drag & drop è per personaggi, asset,
  continuità, ordine e stato.
- Il TESTO della tavola non va mai nel prompt immagine; si impagina dopo (locale di default).
- Illustrazione quadrata + fascia costruita localmente come strategia predefinita.
- Una chiamata API per tavola; nessuna generazione "a lotto" in una sola richiesta.
- Dati su filesystem, un progetto = una cartella.

Da decidere durante l'implementazione:
- Se gpt-image-2 espone un 4:5 nativo, offrirlo come terza strategia.
- Soglia caratteri del prompt oltre la quale avvisare (dipende dal modello; partire da 4000).
- Se usare SSE invece del polling per lo stato dei job.
- Font predefinito per la fascia testo (serve un font con licenza libera incluso nel repo).

---

## Appendice A — Template dei prompt (bozze, file in `backend/templates/`)

### A.1 `character_sheet.md`
```
Crea una tavola di riferimento ("character sheet") per un libro illustrato per bambini,
nello stile: {style.technique}; {style.character_style}; {style.mood}.
Le fotografie allegate mostrano le persone reali: {per ogni protagonista: "immagine N = NOME, età"}.
Traduci ogni persona in un personaggio fumettoso semplice e riconoscibile, mantenendo i tratti
distintivi (forma del viso, capelli, colore occhi, barba, corporatura naturale) senza
fotorealismo. Abiti: {per ogni protagonista: outfit default}.
Composizione: in alto il gruppo dei {N} personaggi sorridenti insieme; sotto, un riquadro per
ciascuno con mezzo busto e la palette dei suoi colori; in basso una riga con le stesse quattro
persone in quattro espressioni: gioia, meraviglia, curiosità, tenerezza.
Sfondo chiaro tipo carta, {ambientazione opzionale}. Nessun testo nell'immagine.
```

### A.2 `estrai_descrizioni.md` (system)
```
Sei l'art director di un libro illustrato. Dall'immagine allegata (scheda dei protagonisti)
scrivi, per ciascun personaggio elencato, una descrizione canonica in italiano utilizzabile
come prompt: viso, occhi, capelli, corporatura, abiti con colori precisi, 3–5 elementi
identificativi, cosa NON deve mai cambiare. Rispondi solo con JSON conforme allo schema.
```

### A.3 `genera_storyboard.md` (system)
```
Sei un autore di libri illustrati per bambini di 2–5 anni. Scrivi uno storyboard di {n}
tavole diviso in parti. Personaggi disponibili (usa esattamente questi nomi): {lista}.
Luoghi/asset disponibili (usa esattamente questi alias quando appropriato): {alias+descrizione}.
Per ogni tavola: titolo breve; "scene": descrizione visiva dettagliata in 4–7 frasi (luogo,
luce, inquadratura, posizione e azione di ogni personaggio, oggetti visibili, cosa NON deve
apparire); "text": dialoghi e narrazione brevi, con onomatopee, max 5 righe;
"characters_present"; "environment"; "new_objects" (oggetti introdotti per la prima volta,
con descrizione fissa); "continuity_notes" (riferimenti a tavole precedenti).
Mantieni gli stessi oggetti e luoghi coerenti tra le tavole. Rispondi solo con JSON conforme
allo schema fornito.
```

### A.4 `tavola.md`
Vedi §6.2 e §6.3: il template è la concatenazione delle sezioni A–G con i segnaposto
`{style.*}`, `{references[]}`, `{characters[]}`, `{objects[]}`, `{environments[]}`,
`{continuity}`, `{scene}`, `{format}`, `{negatives[]}`.

## Appendice B — Schema JSON dello storyboard
File separato: `storyboard.schema.json` (stessa cartella di questo documento).

## Appendice C — Riferimenti verificati durante la stesura
- Rilascio gpt-image-1.5 (16 dic 2025) e gpt-image-2 (21 apr 2026) con editing su più immagini
  di riferimento (fino a 16) e fedeltà elevata agli input.
- Endpoint images edit: parametro `image[]` ripetibile; formati 1024x1024, 1536x1024, 1024x1536;
  `input_fidelity` high/low.
Da ricontrollare sulla documentazione ufficiale OpenAI prima dell'implementazione.
