# Storybook Prompt Builder — Specifiche di progetto

Applicazione **desktop per macOS** (Python, avviata da terminale) per costruire, tavola per
tavola, i prompt (testo + immagini di riferimento) con cui generare le illustrazioni di un libro
per bambini mantenendo **coerenza dei personaggi** e **personalizzazione degli ambienti**
(foto reali della propria casa, giardino, paese), con **generazione parallela** e
**ramificazioni di modifica per singola tavola**.

Versione documento: 1.1 — 2026-11-09 (sostituisce la 1.0 "pagina web")
Stato: specifica da implementare

---

## 0. Il problema e il principio guida

Oggi il flusso è manuale: si scrive lo storyboard con ChatGPT, si copia a mano in ogni prompt la
parte fissa (identità grafica + modello dei protagonisti), si allega ogni volta la scheda
personaggi, e si spera che il risultato resti coerente su 30 tavole.

Il tool deve rendere questo flusso **componibile e ripetibile**:

> **Il prompt di una tavola non si scrive: si compila.**
> Il prompt finale è il risultato deterministico di blocchi riutilizzabili (identità grafica,
> personaggi, asset, snippet, regole di continuità) più la descrizione specifica della scena.
> L'utente decide *quali* blocchi entrano in una tavola e può **sovrascrivere qualsiasi
> dettaglio** in una ramificazione locale; il motore decide *come* assemblarli.

Tre regole operative:

1. I blocchi **globali** (identità grafica, formato, divieti) entrano in tutte le tavole senza
   trascinarli. Il drag & drop serve per ciò che cambia: personaggi presenti, asset fotografici,
   note/snippet, render di continuità, ordine e stato delle tavole.
2. **Ogni informazione inserita una volta è riutilizzabile**: personaggi, asset, oggetti,
   ambienti, snippet di testo (luce, inquadratura, regole), render approvati. Tutto vive nella
   Libreria ed è trascinabile su qualunque card.
3. **Ogni tavola può ramificare**: si apre in una finestra propria, si crea una variante che
   eredita tutto dalla base e sovrascrive solo ciò che si tocca (anche la descrizione di un
   personaggio o una riga dello stile), si generano più render in parallelo, si sceglie il
   migliore. Le altre tavole non vengono toccate.

---

## 1. Glossario

| Termine | Significato |
|---|---|
| **Progetto (Libro)** | Un libro illustrato. Una cartella su disco con `project.json` e le immagini. |
| **Identità grafica** | Blocco testuale fisso: tecnica pittorica, forme, palette, formato, divieti. Entra in ogni prompt. |
| **Character sheet** | L'immagine "I nostri protagonisti" generata dalle foto reali e approvata. Immagine di riferimento n. 1 di ogni prompt in cui compare un protagonista. |
| **Personaggio** | Protagonista umano o personaggio secondario. Nome, descrizione canonica, elementi identificativi, abiti per scena, eventuale immagine di riferimento. |
| **Asset** | Immagine esterna (foto della casa, del giardino, del passeggino) con un **alias** (es. `casa`) e una nota di adattamento. Collegato a una tavola, il prompt dice "quando la scena dice *casa* intende l'immagine di riferimento N". |
| **Oggetto fisso** | Oggetto ricorrente descritto a testo (Stella delle Radici, Fiore). Può puntare a un render che lo mostra bene. |
| **Ambiente ricorrente** | Luogo che deve restare uguale in più tavole. Può puntare a un asset e/o al render approvato di una tavola. |
| **Snippet** | Frammento di testo riutilizzabile ("luce calda al tramonto", "stesso giardino della tavola 1", "inquadratura dal basso"). Trascinabile su una card; entra nella sezione CONTINUITÀ/SCENA del prompt. |
| **Nota** | Post-it sul board: testo libero, colore, posizione. Può essere libera (promemoria) o agganciata a una card; se marcata "nel prompt" si comporta come uno snippet. |
| **Tavola** | Una pagina del libro: numero, titolo, parte, IMMAGINE, TESTO, collegamenti, stato, albero di varianti. |
| **Variante** | Nodo dell'albero di modifiche di una tavola. Eredita dal genitore e sovrascrive solo i campi toccati. La radice è la **base** (ciò che si vede sulla card). |
| **Prompt compilato** | Testo finale + lista ordinata di immagini allegate, prodotto dal motore per una variante. È ciò che viene inviato all'API. |
| **Render** | Un'immagine generata per una variante. Più render per variante (es. 4 in parallelo). Al massimo uno per tavola è **approvato**. |
| **Pagina finale** | Il render approvato impaginato in 4:5 con il TESTO nella fascia inferiore. |
| **Job** | Una chiamata API in coda o in esecuzione. Visibile nel pannello Coda, annullabile. |

---

## 2. Flusso di lavoro end-to-end

```
FASE 0  Setup progetto      → nome libro, identità grafica (da template modificabile)
FASE 1  Protagonisti        → foto reali → character sheet (N varianti in parallelo) → approvo
                             → estraggo descrizioni canoniche (LLM vision) → correggo → blocco
FASE 2  Libreria            → asset con alias, secondari, oggetti fissi, ambienti, snippet
FASE 3  Storyboard          → A) genero da brief (output JSON)  oppure
                               B) incollo storyboard esistente → LLM lo converte in JSON
                             → nascono le card, già collegate a personaggi/asset per nome e alias
FASE 4  Board               → trascino card, note, snippet, personaggi, asset; ispeziono il
                               prompt compilato; segno "pronta"
FASE 5  Generazione         → in parallelo: più tavole insieme e/o più render della stessa
                               tavola; coda con progresso e costo stimato
FASE 6  Ramificazione       → apro la tavola in una finestra, creo varianti, sovrascrivo
                               dettagli, genero, confronto, approvo il render migliore
FASE 7  Finalizzazione      → testo nella fascia (locale, Pillow) oppure via AI
                             → export PDF/ZIP di tutte le pagine + log dei prompt
```

---

## 3. Architettura

### 3.1 Scelte

| Componente | Scelta | Motivazione |
|---|---|---|
| Linguaggio | **Python 3.11+** | Coerente con il repo (cropper Tkinter, SDK `openai`, `.env`). |
| GUI | **PySide6 (Qt 6)** | Drag & drop nativo, `QGraphicsView` per un board con card e note trascinabili, finestre multiple, dock, thread pool integrato, aspetto nativo macOS. Tkinter (usato nel cropper) non regge un board Trello-like né più finestre con job in background. |
| Core | Pacchetto `core/` **senza dipendenze da Qt** | Modello dati, composizione prompt, provider, impaginazione. Usato sia dalla GUI sia dalla CLI. |
| CLI | `python -m storybook …` | Per chi preferisce il terminale: compilare prompt, generare a lotti in parallelo, esportare. Stessa logica della GUI. |
| Storage | **Filesystem: una cartella per progetto, `project.json` + immagini** | Trasparente, backup = copia cartella. Nessun DB. |
| Immagini AI | **OpenAI Images API** (`images.edit` con riferimenti multipli) | Richiesta esplicita. Una chiamata per render. |
| Testo AI | Provider astratto: **OpenAI** o **Anthropic** | Storyboard e descrizioni anche con Claude; immagini sempre OpenAI. |
| Concorrenza | `concurrent.futures.ThreadPoolExecutor` nel core; in GUI incapsulato in `QThreadPool`/`QRunnable` con segnali | Le chiamate SDK sono sincrone e I/O-bound: i thread bastano. Concorrenza configurabile (default 3). |
| Impaginazione | **Pillow** | Già nel repo. Pagina 4:5, fascia testo, export. |

### 3.2 Struttura del codice

```
storybook/
├── __main__.py              # entry: `python -m storybook gui` | compose | generate | import | export
├── cli.py                   # argparse, usa core/
├── core/
│   ├── models.py            # Pydantic: Project, Character, Asset, Snippet, Note, Plate, Variant, Render…
│   ├── storage.py           # load/save atomico di project.json, gestione file, backup
│   ├── composer.py          # MOTORE DI COMPOSIZIONE (variante → prompt + immagini + warning)
│   ├── resolver.py          # risoluzione eredità delle varianti (base → … → foglia)
│   ├── providers/
│   │   ├── base.py          # ImageProvider, TextProvider
│   │   ├── openai_images.py
│   │   ├── openai_text.py
│   │   └── anthropic_text.py
│   ├── jobs.py              # JobManager: coda, pool thread, stati, annullamento, retry, costi
│   ├── layout.py            # Pillow: pagina 4:5, fascia testo, export ZIP/PDF
│   └── templates/           # prompt template .md modificabili dall'utente
├── gui/
│   ├── app.py               # QApplication, tema, apertura progetto
│   ├── main_window.py       # finestra principale: Libreria | Board | Ispettore | Coda
│   ├── board/               # QGraphicsScene/View, CardItem, NoteItem, ColumnItem, drag/drop
│   ├── library/             # QTreeView con MIME type custom per il drag
│   ├── inspector/           # pannello proprietà card + anteprima prompt
│   ├── plate_window.py      # FINESTRA TAVOLA: albero varianti, editor override, render, confronto
│   ├── setup/               # dialoghi: identità grafica, protagonisti, asset, storyboard
│   ├── queue_panel.py       # tabella job con progresso, annulla, riprova
│   └── workers.py           # QRunnable che avvolgono core.jobs e emettono segnali
├── projects/                # DATI (in .gitignore)
│   └── <slug>/
│       ├── project.json
│       ├── sources/         # foto reali
│       ├── character_sheet/ # versioni generate
│       ├── renders/<NN>/<variant_id>/vK.png
│       ├── prompts/<NN>/<variant_id>/vK.txt   # prompt inviato + lista file + parametri
│       ├── pages/           # pagine finali 4:5
│       └── project.json.bak
├── .env.example
├── requirements.txt         # PySide6, openai, anthropic (opz.), Pillow, pydantic, img2pdf
└── README.md
```

Avvio da terminale:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m storybook gui                 # apre la finestra
python -m storybook generate --project vittorio --plates 1-5 --parallel 3   # headless
```

---

## 4. Modello dati (`project.json`)

### 4.1 Project
```
id, name, slug, created_at, updated_at
style:             StyleIdentity
format:            PageFormat
characters:        [Character]
assets:            [Asset]
fixed_objects:     [FixedObject]
environments:      [Environment]
snippets:          [Snippet]
notes:             [Note]            # post-it del board
character_sheet:   CharacterSheet | null
continuity_rules:  [str]
parts:             [Part]
plates:            [Plate]
board_layout:      { mode: "columns" | "sequence" | "free", card_positions: {plate_id: {x,y}} }
settings:          ProjectSettings
```

### 4.2 StyleIdentity / PageFormat
```
StyleIdentity: technique, character_style, mood, negatives: [str], extra
PageFormat:    aspect "4:5", illustration_pct 80, text_band, render_strategy "square_plus_band" | "full_page"
```

### 4.3 Character
```
id, name, role: "protagonist" | "secondary", age_label
canonical: str, key_features: [str], outfits: {default: str, <tag>: str}, props: [str], never: [str]
source_photos: [file], sheet_crop: {x,y,w,h} | null, reference_image: file | null
```

### 4.4 Asset / FixedObject / Environment / Snippet / Note
```
Asset:       id, alias (univoco), title, description, file, adaptation_note
FixedObject: id, name, description, never: [str], reference_image: file | null
Environment: id, name, description, asset_id | null, reference_plate_id | null
Snippet:     id, label, text, section: "scene" | "continuity" | "negatives" | "style", tags: [str]
Note:        id, text, color, pos: {x,y}, attached_plate_id | null, in_prompt: bool, section
```

### 4.5 Plate (tavola)
```
id, number, title, part_id
status:            "draft" | "ready" | "generated" | "approved" | "final"
text:              str                 # TESTO (dialoghi), solo impaginazione
base:              VariantFields       # i campi "di base" della card (vedi 4.6)
variants:          [Variant]           # albero; la radice ha parent_id = null ed è la base
active_variant_id: id                  # variante mostrata/usata di default
approved_render_id: id | null          # può stare in qualunque variante
final_page:        file | null
```

### 4.6 VariantFields (tutti opzionali in una variante: `null` = eredita)
```
scene:                 str
character_ids:         [id]
outfit_tags:           {character_id: tag}
asset_ids:             [id]
fixed_object_ids:      [id]
environment_ids:       [id]
snippet_ids:           [id]
continuity_plate_ids:  [id]
continuity_notes:      str
plate_negatives:       [str]
character_overrides:   {character_id: str}   # testo che SOSTITUISCE la descrizione canonica solo qui
style_override:        str | null            # sostituisce/integra l'identità grafica solo qui
extra_images:          [file]                # riferimenti aggiuntivi solo per questa variante
prompt_override:       str | null            # prompt manuale completo (escape hatch)
model, size, quality, input_fidelity         # parametri API per questa variante
```

### 4.7 Variant
```
id, parent_id | null, label, created_at, note
fields:   VariantFields          # solo i campi sovrascritti
renders:  [Render]
status:   "open" | "chosen" | "discarded"
```
Risoluzione (`resolver.py`): si percorre la catena foglia → … → radice; per ogni campo vince
il primo valore non nullo. Per le liste è possibile indicare operazioni relative
(`{"add": [...], "remove": [...]}`) oltre alla sostituzione completa.

### 4.8 Render
```
id, version, file, created_at, variant_id
prompt_text, reference_files: [path], model, size, quality, input_fidelity
mode: "generate" | "edit_from_render" | "add_text"
parent_render_id | null, job_id, duration_s, cost_estimate, notes, rating: 0–5
```

### 4.9 ProjectSettings
```
image_model "gpt-image-1.5" | "gpt-image-1" | "gpt-image-2"
image_quality "high", image_size "1024x1024", input_fidelity "high"
text_provider "openai" | "anthropic", text_model
max_reference_images 16
max_parallel_jobs 3
default_renders_per_run 1          # quanti render per variante a ogni "Genera"
price_table {model: {quality: €}}  # per la stima costi, modificabile
font_path, font_size_min, font_size_max
```

---

## 5. Interfaccia (PySide6)

### 5.1 Finestra principale

```
┌──────────────┬─────────────────────────────────────────────┬──────────────────┐
│ LIBRERIA     │ BOARD (QGraphicsView, zoom/pan)             │ ISPETTORE        │
│ ▸ Personaggi │  ┌Bozza──┐ ┌Pronta──┐ ┌Generata┐ ┌Approv.┐  │ card selezionata │
│ ▸ Asset      │  │[01]   │ │[03]    │ │[02] v4 │ │       │  │ – personaggi     │
│ ▸ Oggetti    │  │[05]   │ │ 📝nota │ │        │ │       │  │ – asset          │
│ ▸ Ambienti   │  │       │ │        │ │        │ │       │  │ – snippet        │
│ ▸ Snippet    │  └───────┘ └────────┘ └────────┘ └───────┘  │ – continuità     │
│ ▸ Note       │     📝 nota libera                           │ – anteprima      │
│ ▸ Render ok  │                                              │   PROMPT (live)  │
├──────────────┴─────────────────────────────────────────────┴──────────────────┤
│ CODA  [job] tavola 02 var. "cielo scuro" ×3  ▓▓▓▓░░ 2/3  0,24 €  [annulla]     │
└───────────────────────────────────────────────────────────────────────────────┘
```

- **Libreria** (dock sinistro): albero con elementi trascinabili (MIME `application/x-storybook-ref`).
  Doppio clic → modifica. Pulsanti "+" per nuovo elemento. Ricerca.
- **Board** (centro): tre modalità, commutabili dalla toolbar.
  - *Colonne*: colonne = stato. Trascinare tra colonne cambia stato (con vincoli: `approved`
    solo con render approvato; passaggio automatico a `generated` al primo render).
  - *Sequenza*: griglia ordinata per numero, raggruppata per Parte; il drag riordina e rinumera.
  - *Libera*: posizioni salvate in `board_layout`; utile per raggruppare visivamente.
  - **Card**: numero, titolo, thumbnail (render approvato / attivo / placeholder), avatar dei
    personaggi (ritagli dal character sheet), chip asset, badge varianti (es. "3 rami"),
    badge job in corso, semaforo warning (alias citato ma non collegato, nessun personaggio…).
  - **Note** (post-it): create con doppio clic sul vuoto o dalla toolbar; trascinabili ovunque;
    rilasciate su una card si agganciano (seguono la card); checkbox "nel prompt" + sezione.
  - **Drop su card** di: personaggio → `character_ids`; asset → `asset_ids`; snippet →
    `snippet_ids`; render approvato di un'altra tavola → `continuity_plate_ids`; nota → aggancio.
  - Menu contestuale card: Apri finestra tavola, Genera (×N), Genera varianti attive, Duplica,
    Segna pronta, Copia prompt, Apri cartella render.
  - Selezione multipla (rubber band / Cmd+clic) → "Genera selezionate in parallelo".
- **Ispettore** (dock destro): modifica rapida della variante attiva della card selezionata e
  **anteprima live del prompt compilato** con contatore caratteri e lista numerata delle
  immagini allegate. "Copia prompt" per uso manuale in ChatGPT.
- **Coda** (dock inferiore): tabella job con tavola, variante, modello, progresso, costo
  stimato, durata, stato, pulsanti Annulla/Riprova; riga totale (job attivi / in coda /
  spesa stimata della sessione). Un job fallito mostra il messaggio API sulla riga e sulla card.

### 5.2 Finestra Tavola (ramificazione)

Si apre dal board (doppio clic o menu). Se ne possono aprire **più di una** contemporaneamente.

```
┌──────────────────┬──────────────────────────────────┬─────────────────────────┐
│ ALBERO VARIANTI  │ EDITOR VARIANTE                  │ RENDER                  │
│ ● base           │ Scene        [ereditato ▾][reset]│ ┌────┐┌────┐┌────┐┌────┐│
│  ├─ cielo scuro  │ Personaggi   [override]          │ │ v1 ││ v2 ││ v3 ││ v4 ││
│  │   └─ +torcia  │  Luigi: "… senza torcia" [reset] │ └────┘└────┘└────┘└────┘│
│  └─ inquadr. alta│ Asset        [ereditato]         │ confronto A|B  zoom     │
│                  │ Snippet      [override]          │ ★ voto   [Approva]      │
│ [Nuovo ramo]     │ Continuità   [ereditato]         │ [Modifica guidata…]     │
│ [Duplica ramo]   │ Divieti      [override]          │ [Usa come riferimento]  │
│ [Scarta]         │ Stile locale [vuoto]             │                         │
│ [Promuovi a base]│ Immagini extra [+]               │ [Genera ×  3 ▾]         │
│                  │ Parametri API  modello/size/q    │                         │
│                  │ ── PROMPT COMPILATO (live) ──    │                         │
│                  │ … 3.812 caratteri · 3 immagini   │                         │
└──────────────────┴──────────────────────────────────┴─────────────────────────┘
```

- **Albero varianti**: radice = base (ciò che sta sulla card). "Nuovo ramo" crea un figlio
  della variante selezionata con tutti i campi a `null` (eredita tutto). Il nome del ramo
  è editabile. Stato: aperto / scelto / scartato (i rami scartati si collassano).
- **Editor**: ogni campo mostra se è *ereditato* (grigio, con tooltip "da: cielo scuro") o
  *override* (evidenziato) e ha un pulsante *reset* che torna a ereditare. Si può
  sovrascrivere anche la descrizione di un singolo personaggio o aggiungere uno stile
  locale: le modifiche restano confinate a questo ramo e ai suoi discendenti.
- **Prompt compilato**: anteprima live della variante selezionata; "Copia"; "Apri come
  prompt manuale" (compila in `prompt_override` per ritocchi a mano).
- **Render**: galleria della variante; "Genera ×N" lancia N job in parallelo sullo stesso
  prompt (N render indipendenti); confronto affiancato tra due render anche di rami diversi;
  voto a stelle; **Approva** → `plate.approved_render_id`, variante marcata *scelta*,
  card aggiornata; **Modifica guidata** → nuovo ramo figlio con `mode: edit_from_render` e
  istruzione testuale; **Usa come riferimento** → aggiunge il render a `extra_images` di un
  altro ramo o lo espone in Libreria → Render ok.
- **Promuovi a base**: copia gli override del ramo scelto nella base della tavola
  (così le ramificazioni future partono da lì). Non tocca altre tavole.
- **Propaga a…** (opzionale): copia uno o più override (es. `character_overrides.luigi`) in
  altre tavole selezionate. Unico modo in cui un ramo influenza altre tavole, ed è esplicito.

### 5.3 Dialoghi di setup
- *Identità grafica*: form + anteprima del blocco testuale.
- *Protagonisti*: foto reali, campi canonici, "Genera character sheet ×N" (N varianti in
  parallelo, scelgo la migliore), "Estrai descrizioni dalla scheda", lucchetto.
- *Asset / Oggetti / Ambienti / Snippet*: liste editabili (stesse voci della Libreria).
- *Storyboard*: Genera da brief | Importa da testo → anteprima tabellare con i collegamenti
  suggeriti (per nome e alias) → "Crea tavole".
- *Impostazioni*: modelli, qualità, concorrenza, tabella prezzi, font, provider testo.

---

## 6. Motore di composizione (`core/composer.py`)

`compose(project, plate, variant_id) -> CompiledPrompt {text, images: [path], warnings}`.
Lavora sui campi **risolti** dal `resolver` (eredità applicata).

### 6.1 Ordine delle immagini allegate
```
[1]   character_sheet (se almeno un protagonista è presente)
[2..] asset collegati, nell'ordine della card
[..]  reference_image di secondari/oggetti presenti
[..]  render approvati delle continuity_plate_ids
[..]  extra_images della variante
```
Oltre `max_reference_images` → warning e troncamento dal fondo. Il prompt cita sempre le
immagini per posizione ("immagine di riferimento 2").

### 6.2 Sezioni del testo (template `tavola.md`, in quest'ordine)
```
A. IDENTITÀ GRAFICA     StyleIdentity (+ style_override della variante se presente)
B. RIFERIMENTI VISIVI   una riga per immagine allegata: cosa è, come usarla
C. PERSONAGGI IN SCENA  per ciascun presente: canonical (o character_override), key_features,
                        outfit del tag, never. Esplicita chi NON c'è se la scena lo suggerisce.
D. OGGETTI E AMBIENTI   fixed_objects + environments; se l'ambiente ha un asset: "quando la
                        scena dice 'casa' intende l'immagine N, tradotta nello stile del libro:
                        mantenere <adaptation_note>"
E. CONTINUITÀ           continuity_notes + snippet/note con section=continuity + regole globali
F. SCENA                scene + snippet/note con section=scene
G. FORMATO E DIVIETI    PageFormat + negatives globali + plate_negatives + snippet negatives
```
Regole: italiano, frasi dichiarative, nessun markdown nel testo inviato; i personaggi assenti
non vengono descritti; il TESTO della tavola non entra mai nel prompt immagine.
Warnings: alias citato nella scena ma non collegato; personaggio nominato ma non collegato;
nessun personaggio; > 16 immagini; prompt oltre soglia caratteri (default 4000).

### 6.3 Esempio di output (Tavola 01, abbreviato)
```
IDENTITÀ GRAFICA. Illustrazione 2D per libro di fiabe, personaggi fumettosi, forme morbide e
arrotondate, contorni delicati, colorazione acquerello e gouache, texture leggera di carta,
colori luminosi ma naturali, atmosfera calda, accogliente e magica. Stesso illustratore per
tutte le tavole.

RIFERIMENTI VISIVI. Immagine 1: scheda ufficiale dei quattro protagonisti; riferimento
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
scuro, ruote nere. Giardino: tornerà nelle tavole 26–28; grande albero, erba, terra morbida.

CONTINUITÀ. Prima tavola: stabilisce l'aspetto del giardino e dell'albero. Abiti dell'avventura.

SCENA. Ampia inquadratura del giardino della casetta a Montiglio Monferrato in un pomeriggio
soleggiato. […testo IMMAGINE dello storyboard…]

FORMATO E DIVIETI. Illustrazione quadrata a piena immagine, senza bordi né fascia per il testo
(aggiunta in impaginazione). Nessuna scritta, numero, fumetto o dialogo nell'immagine. Nessun
fotorealismo, rendering 3D, plastica lucida, volti realistici. Non introdurre altri umani.
```

---

## 7. Generazione parallela e coda (`core/jobs.py`)

- `JobManager` con `ThreadPoolExecutor(max_workers = settings.max_parallel_jobs)`.
- Un **job = una chiamata API = un render**. "Genera ×3" su una variante crea 3 job con lo
  stesso prompt compilato (snapshot al momento dell'invio: modifiche successive all'editor non
  influenzano i job in volo). "Genera selezionate" crea un job per tavola (variante attiva).
  "Genera tutti i rami aperti" crea un job per ramo.
- Stati: `queued → running → done | error | cancelled`. L'annullamento di un job in coda è
  immediato; di uno in esecuzione viene segnalato e il risultato scartato (la chiamata API non è
  interrompibile).
- **Stima costi** prima dell'invio: dialogo "3 render × gpt-image-1.5 high ≈ 0,xx €. Procedere?"
  (soglia di conferma configurabile). Totale per sessione e per progetto.
- **Rate limit (429)** e errori di rete: 3 tentativi con backoff 2/4/8 s, poi `error`. Errori di
  policy contenuti: nessun retry. Alla ricezione di 429 la concorrenza effettiva si riduce
  temporaneamente di 1.
- Ogni job scrive `prompts/<NN>/<variant>/vK.txt` (prompt, file, parametri, durata, esito) e
  il render in `renders/…`. `project.json` viene salvato dopo ogni job completato (atomico,
  `.bak`).
- In GUI i worker sono `QRunnable` che chiamano il core ed emettono segnali Qt (`started`,
  `progress`, `finished`, `failed`) verso il pannello Coda e le card; nessun accesso alla UI
  dai thread.
- In CLI lo stesso `JobManager` stampa una riga per job (tavola, variante, stato, durata, costo).

---

## 8. Integrazioni LLM (`core/providers/`)

```python
class ImageProvider:
    def generate(prompt, size, quality) -> bytes
    def edit(prompt, images: list[Path], size, quality, input_fidelity) -> bytes

class TextProvider:
    def structured(system, user, json_schema, images: list[Path] = []) -> dict
```

| Uso | Chiamata | Note |
|---|---|---|
| Character sheet | `images.edit(image=[foto…], prompt=character_sheet.md, size="1536x1024", quality="high", input_fidelity="high")` | ×N in parallelo; iterazione "Rigenera con nota" riusa la versione precedente come riferimento. |
| Descrizioni canoniche | `TextProvider.structured(images=[sheet], schema=CharacterDescriptions)` | L'utente corregge; i testi finiscono nella sezione C. |
| Storyboard da brief | `TextProvider.structured(system=genera_storyboard.md, schema=storyboard.schema.json)` | Il brief include nomi personaggi e alias asset del progetto, così l'auto-collegamento è affidabile. |
| Import storyboard | come sopra con `importa_storyboard.md` ("converti fedelmente, non inventare") | Estrae anche identità grafica e personaggi se presenti nel testo. |
| Render tavola | `images.edit(image=compiled.images, prompt=compiled.text, …)`; `images.generate` se senza riferimenti | Un job per render. |
| Modifica guidata | `images.edit(image=[render] + riferimenti, prompt="Mantieni identica questa illustrazione tranne: <istruzione>. <blocco personaggi>")` | Crea un ramo figlio. |
| Controllo coerenza (v2) | `TextProvider.structured(images=[sheet, render], schema=ConsistencyReport)` | Semaforo per personaggio sulla card. |

Modelli immagine: default `gpt-image-1.5`; selezionabili `gpt-image-1` e `gpt-image-2`.
Verificato: edit accetta fino a 16 immagini di riferimento; formati `1024x1024`, `1024x1536`,
`1536x1024`; `input_fidelity` high/low su 1 e 1.5 (il 2 lavora sempre ad alta fedeltà).
Da verificare sulla documentazione ufficiale al momento dell'implementazione: formati
flessibili di gpt-image-2 (un 4:5 nativo renderebbe opzionale la strategia §10), prezzi.
Modelli testo: configurabili; OpenAI con immagini in input e JSON, oppure Anthropic
(`claude-sonnet-5-5` consigliato per costo/qualità).

---

## 9. CLI (`python -m storybook`)

| Comando | Effetto |
|---|---|
| `gui [--project SLUG]` | apre la finestra principale |
| `new NAME` | crea progetto da template |
| `import-storyboard SLUG file.txt` | testo → JSON → tavole (stampa anteprima, chiede conferma) |
| `compose SLUG --plate N [--variant ID]` | stampa prompt compilato + lista immagini + warning |
| `generate SLUG --plates 1-5,8 [--variant ID] [--n 3] [--parallel 3] [--yes]` | genera in parallelo, stampa costo stimato e chiede conferma |
| `approve SLUG --plate N --render ID` | approva un render |
| `finalize SLUG [--plates …] [--mode local\|ai]` | impagina testo |
| `export SLUG [--pdf] [--out DIR]` | ZIP/PDF |
| `status SLUG` | tabella tavole: stato, rami, render, spesa |

---

## 10. Impaginazione e post-processing (`core/layout.py`)

### 10.1 Strategia consigliata: "quadrato + fascia" (`square_plus_band`)
In una pagina 4:5 l'80% superiore dell'altezza è **esattamente un quadrato**
(W × 1,25W × 0,8 = W × W). Quindi: si genera l'illustrazione quadrata a piena immagine; il tool
costruisce localmente la pagina 4:5 (es. 2048×2560) con l'illustrazione in alto e la fascia
chiara (20%) in basso con il TESTO renderizzato da Pillow. Layout deterministico, testo senza
refusi, font coerente, rigenerare l'illustrazione non tocca il testo.

### 10.2 Alternativa: pagina intera (`full_page`)
Si genera `1024x1536` chiedendo l'illustrazione nel 78% superiore e fascia chiara sotto; il tool
ritaglia a 4:5 dall'alto. Meno controllabile.

### 10.3 Testo nella fascia
- Locale: wrap automatico, font adattivo tra min e max, colore scuro su fascia chiara (colore
  campionato dal bordo inferiore dell'illustrazione o fisso), margini configurabili, battute su
  righe separate.
- Via AI (su richiesta): `images.edit(image=[pagina], prompt="Aggiungi nella fascia inferiore
  chiara esattamente questo testo, in italiano, carattere leggibile per bambini, senza modificare
  l'illustrazione: «…»")`. Avviso: possibili refusi.

### 10.4 Export
ZIP con `pages/`, `renders/`, `prompts/`, `project.json`; PDF con le pagine in ordine.

---

## 11. Requisiti non funzionali e sicurezza

- Chiave API solo in `.env` (come nel cropper); mai in `project.json`, nei log o nei prompt
  esportati.
- Upload immagini: PNG/JPG/WebP, EXIF rimosso alla copia in `sources/` (le foto di famiglia
  contengono spesso GPS).
- `projects/` in `.gitignore`. Salvataggio atomico + `.bak`. Autosave a ogni modifica.
- Tutto funziona offline tranne le chiamate AI.
- macOS: testato su Apple Silicon; PySide6 da pip; nessuna dipendenza di sistema. Packaging
  `.app` con PyInstaller opzionale, non richiesto (si lancia da terminale).

---

## 12. Piano di implementazione

**M1 — Core e board (senza AI)**
Modelli, storage, resolver delle varianti, composer con test unitari; finestra principale con
Libreria, Board (colonne/sequenza/libera), Note, Ispettore con anteprima prompt, "Copia prompt";
CLI `compose`. Già utile: prompt e immagini si incollano a mano in ChatGPT.

**M2 — Generazione parallela**
`JobManager`, provider OpenAI immagini, pannello Coda, stima costi, render sulle card,
approvazione, impaginazione locale, export ZIP; CLI `generate`, `approve`, `export`.

**M3 — Finestra Tavola e ramificazioni**
Albero varianti, editor con ereditato/override/reset, Genera ×N, confronto, Modifica guidata,
Promuovi a base, Propaga a.

**M4 — Assistenti**
Import storyboard → JSON → tavole; generazione da brief; character sheet ×N da foto;
estrazione descrizioni canoniche.

**M5 — Qualità**
Controllo di coerenza vision, testo via AI, PDF, template modificabili dall'app.

---

## 13. Decisioni prese e punti aperti

Decisioni:
- App desktop PySide6 avviata da terminale, con CLI equivalente sullo stesso core.
- I blocchi globali entrano sempre; drag & drop per personaggi, asset, snippet, note, continuità,
  ordine e stato.
- Un job = una chiamata = un render; concorrenza configurabile; stima costi con conferma.
- Le ramificazioni sono per tavola, con eredità per campo e override anche di descrizioni di
  personaggi e stile; influenzano altre tavole solo con "Propaga a" esplicito.
- TESTO mai nel prompt immagine; impaginazione locale di default; illustrazione quadrata + fascia.
- Dati su filesystem, un progetto = una cartella.

Da decidere durante l'implementazione:
- 4:5 nativo se gpt-image-2 lo espone.
- Soglia caratteri del prompt (partire da 4000).
- Font con licenza libera da includere per la fascia testo.
- Se le liste nelle varianti usano solo sostituzione completa o anche add/remove (consigliato
  add/remove per `snippet_ids` e `plate_negatives`).

---

## Appendice A — Template dei prompt (bozze, in `core/templates/`)

### A.1 `character_sheet.md`
```
Crea una tavola di riferimento ("character sheet") per un libro illustrato per bambini,
nello stile: {style.technique}; {style.character_style}; {style.mood}.
Le fotografie allegate mostrano le persone reali: {per ogni protagonista: "immagine N = NOME, età"}.
Traduci ogni persona in un personaggio fumettoso semplice e riconoscibile, mantenendo i tratti
distintivi (forma del viso, capelli, colore occhi, barba, corporatura naturale) senza
fotorealismo. Abiti: {per ogni protagonista: outfit default}.
Composizione: in alto il gruppo dei {N} personaggi sorridenti insieme; sotto, un riquadro per
ciascuno con mezzo busto e la palette dei suoi colori; in basso una riga con le stesse persone
in quattro espressioni: gioia, meraviglia, curiosità, tenerezza.
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
Concatenazione delle sezioni A–G di §6.2 con i segnaposto `{style}`, `{references[]}`,
`{characters[]}`, `{objects[]}`, `{environments[]}`, `{continuity}`, `{scene}`, `{format}`,
`{negatives[]}`.

## Appendice B — Schema JSON dello storyboard
File `storyboard.schema.json` nella stessa cartella.

## Appendice C — Riferimenti verificati durante la stesura
- gpt-image-1.5 (16 dic 2025) e gpt-image-2 (21 apr 2026): editing con più immagini di
  riferimento (fino a 16) e alta fedeltà agli input.
- Endpoint images edit: `image[]` ripetibile; formati 1024x1024, 1536x1024, 1024x1536;
  `input_fidelity` high/low.
Da ricontrollare sulla documentazione ufficiale OpenAI prima dell'implementazione.
