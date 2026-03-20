#!/usr/bin/env python3
"""
FotoPainterProProfessionalChatGPT.py
─────────────────────────────────────
Script opzionale per il progetto photopainter-cropper.

Scansiona le foto ritagliate già esportate da FotoPainter e permette
di scegliere manualmente, foto per foto, se inviarle alle API di OpenAI
(modello gpt-image-1) per una rielaborazione fotografica professionale.

NON modifica né tocca il programma principale photo_painter_cropper.py.
NON sovrascrive mai file esistenti.

Uso:
    python FotoPainterProProfessionalChatGPT.py [cartella_export]

    cartella_export  percorso della cartella _export_photopainter_jpg
                     (default: ./_export_photopainter_jpg)

Configurazione API key (una delle due strade):
    1. Variabile d'ambiente:   export OPENAI_API_KEY="sk-..."
    2. File .env nella stessa cartella dello script: OPENAI_API_KEY=sk-...

Dipendenze aggiuntive (oltre a quelle già presenti nel progetto):
    pip install openai
    pip install python-dotenv   # opzionale, per il supporto .env
"""

import os
import sys
import time
import datetime
import json
import base64
import termios
import tty
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ────────────────────────────────────────────────────────────────────────────
#  CONFIGURAZIONE
# ────────────────────────────────────────────────────────────────────────────

# Nome della sottocartella di export creata da FotoPainter
EXPORT_SUBDIR = "_export_photopainter_jpg"

# Suffissi usati dal programma principale
CROP_SUFFIX  = "_pp.jpg"        # file ritagliato esportato
STATE_SUFFIX = "_ppcrop.txt"    # file stato accanto all'originale

# Suffisso per i file prodotti da questo script
CHATGPT_SUFFIX = "_chatgpt"

# Nome del log per questa funzionalità (nella cartella export)
LOG_FILENAME = "chatgpt_pro_session.log"

# Modello OpenAI da usare per il editing delle immagini.
# "gpt-image-1" è il modello più recente e raccomandato.
# In caso di errore di accesso, provare "dall-e-2" (vincoli più stretti).
OPENAI_IMAGE_MODEL = "gpt-image-1"

# Dimensione output richiesta all'API
# Valori supportati da gpt-image-1: "1024x1024", "1536x1024", "1024x1536"
OPENAI_OUTPUT_SIZE = "1024x1024"

# Timeout API in secondi (le elaborazioni foto possono richiedere tempo)
API_TIMEOUT_SECONDS = 120

# Prompt professionale da inviare ad ogni elaborazione
PROFESSIONAL_PROMPT = (
    "Reprocess this photo as if it were taken by a professional photographer "
    "using a full-frame DSLR or mirrorless camera (such as a Canon EOS R5 or "
    "Sony A7 IV) with a high-quality prime lens. Apply professional three-point "
    "studio lighting or natural golden-hour lighting, whichever is more coherent "
    "with the original scene.\n\n"
    "Strict constraints — do not violate under any circumstances:\n"
    "- Do NOT alter, reconstruct, or \"improve\" any human face. Preserve every "
    "facial feature, expression, and characteristic exactly as they appear in "
    "the original.\n"
    "- Do NOT change the position, posture, or arrangement of any person in "
    "the photo.\n"
    "- Do NOT add, remove, or reinterpret any element of the scene.\n"
    "- If any area of the image is blurry or out of focus in the original, do "
    "NOT invent or hallucinate detail — keep that area consistent with the "
    "original level of blur and information.\n"
    "- Improve only: sharpness where it was technically limited by the phone "
    "sensor, dynamic range, color grading, noise reduction, and lighting quality.\n"
    "- The result must feel like the same moment captured with better equipment "
    "— not a reinterpretation or enhancement of the subjects."
)

# ────────────────────────────────────────────────────────────────────────────
#  STRUTTURA DATI per una singola foto
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class PhotoEntry:
    """Tutte le informazioni associate a un ritaglio esportato da FotoPainter."""
    basename: str           # es. "20-3670x2462"
    crop_path: Path         # es. …/_export_photopainter_jpg/20-3670x2462_pp.jpg
    state_path: Optional[Path]  # es. …/samples/20-3670x2462_ppcrop.txt (può mancare)
    state_data: dict = field(default_factory=dict)  # contenuto del _ppcrop.txt


# ────────────────────────────────────────────────────────────────────────────
#  CARICAMENTO CONFIGURAZIONE API KEY
# ────────────────────────────────────────────────────────────────────────────

def load_api_key() -> str:
    """
    Cerca OPENAI_API_KEY nell'ambiente o in un file .env nella cartella
    dello script. Restituisce la chiave trovata o '' se assente.
    """
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key

    # Prova a leggere un file .env semplice (key=value, una per riga)
    env_path = Path(__file__).parent / ".env"
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if key:
                        return key

    return ""


def setup_openai_client():
    """
    Inizializza e restituisce il client OpenAI.
    Termina con un messaggio chiaro se la libreria non è installata
    o la chiave API è assente.
    """
    try:
        from openai import OpenAI
    except ImportError:
        print(
            "\n[ERRORE] La libreria openai non è installata.\n"
            "Esegui:  pip install openai\n"
        )
        sys.exit(1)

    api_key = load_api_key()
    if not api_key:
        print(
            "\n[ERRORE] OPENAI_API_KEY non trovata.\n"
            "Imposta la variabile d'ambiente oppure crea un file .env con:\n"
            "    OPENAI_API_KEY=sk-...\n"
        )
        sys.exit(1)

    return OpenAI(api_key=api_key)


# ────────────────────────────────────────────────────────────────────────────
#  SCANSIONE FOTO
# ────────────────────────────────────────────────────────────────────────────

def read_state_file(txt_path: Path) -> dict:
    """
    Legge un file *_ppcrop.txt (formato key=value) e restituisce un dict.
    Ritorna {} se il file non esiste o non è leggibile.
    """
    if not txt_path or not txt_path.is_file():
        return {}
    data = {}
    try:
        with open(txt_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    except Exception as e:
        print(f"[WARN] Impossibile leggere {txt_path}: {e}")
    return data


def scan_photos(export_folder: Path, source_folder: Path) -> list[PhotoEntry]:
    """
    Scansiona la cartella export cercando tutti i file *_pp.jpg.
    Per ciascuno cerca il corrispondente _ppcrop.txt nella cartella sorgente.
    Restituisce la lista ordinata di PhotoEntry.
    """
    if not export_folder.is_dir():
        print(f"[ERRORE] Cartella export non trovata: {export_folder}")
        return []

    entries = []
    for crop_file in sorted(export_folder.glob(f"*{CROP_SUFFIX}")):
        # Basename: "20-3670x2462_pp.jpg" → "20-3670x2462"
        basename = crop_file.name[: -len(CROP_SUFFIX)]

        # Cerca il file stato nella cartella sorgente
        state_path = source_folder / (basename + STATE_SUFFIX)
        if not state_path.is_file():
            state_path = None

        state_data = read_state_file(state_path)

        entries.append(PhotoEntry(
            basename=basename,
            crop_path=crop_file,
            state_path=state_path,
            state_data=state_data,
        ))

    return entries


# ────────────────────────────────────────────────────────────────────────────
#  NAMING PROGRESSIVO (evita sempre di sovrascrivere)
# ────────────────────────────────────────────────────────────────────────────

def get_chatgpt_output_path(export_folder: Path, basename: str) -> Path:
    """
    Restituisce il percorso per salvare il risultato ChatGPT senza
    sovrascrivere file esistenti.

    Pattern:  {basename}_chatgpt_1.jpg, _chatgpt_2.jpg, …
    """
    n = 1
    while True:
        candidate = export_folder / f"{basename}{CHATGPT_SUFFIX}_{n}.jpg"
        if not candidate.exists():
            return candidate
        n += 1


# ────────────────────────────────────────────────────────────────────────────
#  INVIO A CHATGPT (flusso sincrono, lineare)
# ────────────────────────────────────────────────────────────────────────────

def send_to_openai(client, crop_path: Path) -> bytes:
    """
    Invia il file ritagliato alle API OpenAI e restituisce i bytes dell'immagine
    risultante. Aspetta la risposta prima di ritornare (flusso sincrono).

    Lancia eccezione in caso di errore API.
    """
    print(f"  → Invio a OpenAI ({OPENAI_IMAGE_MODEL})… attendere.")

    with open(crop_path, "rb") as img_file:
        response = client.images.edit(
            model=OPENAI_IMAGE_MODEL,
            image=img_file,
            prompt=PROFESSIONAL_PROMPT,
            size=OPENAI_OUTPUT_SIZE,
            response_format="b64_json",
        )

    # La risposta contiene una lista di immagini; prendiamo la prima
    b64_data = response.data[0].b64_json
    if not b64_data:
        raise ValueError("La risposta API non contiene dati immagine.")

    return base64.b64decode(b64_data)


def save_result(image_bytes: bytes, out_path: Path) -> None:
    """
    Salva i byte PNG/JPG ricevuti dall'API come JPG nel percorso indicato.
    Usa Pillow per garantire il formato JPEG corretto e qualità 95.
    """
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.save(out_path, format="JPEG", quality=95, progressive=True)


# ────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ────────────────────────────────────────────────────────────────────────────

def append_log(log_path: Path, entry: dict) -> None:
    """
    Aggiunge una voce al file di log in formato leggibile.
    Crea il file se non esiste. Non sovrascrive mai le voci precedenti.
    """
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n" + "─" * 60 + "\n")
            for k, v in entry.items():
                f.write(f"{k}: {v}\n")
    except Exception as e:
        print(f"[WARN] Impossibile scrivere nel log {log_path}: {e}")


# ────────────────────────────────────────────────────────────────────────────
#  INPUT TASTIERA (singolo tasto, senza bisogno di premere Invio)
# ────────────────────────────────────────────────────────────────────────────

def get_single_keypress() -> str:
    """
    Attende un singolo tasto dall'utente senza richiedere Invio.
    Ritorna uno tra: 'enter', 'y', 'esc', o il carattere premuto.

    Funziona su terminale Unix/Linux/macOS con tty reale.
    Fallback su input() se non è possibile usare la modalità raw.
    """
    # Fallback se non siamo su un terminale reale (es. pipe, IDE)
    if not sys.stdin.isatty():
        try:
            raw = input().strip().lower()
            if raw == "":
                return "enter"
            if raw in ("y", "yes"):
                return "y"
            if raw in ("esc", "q", "quit"):
                return "esc"
            return raw
        except (EOFError, KeyboardInterrupt):
            return "esc"

    # Modalità raw: legge un singolo byte senza bufferizzazione
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    if ch == "\r" or ch == "\n":
        return "enter"
    if ch == "\x1b":      # ESC
        return "esc"
    if ch == "\x03":      # Ctrl+C
        return "esc"
    return ch.lower()


# ────────────────────────────────────────────────────────────────────────────
#  PRESENTAZIONE FOTO A TERMINALE
# ────────────────────────────────────────────────────────────────────────────

def show_photo_info(entry: PhotoEntry, index: int, total: int) -> None:
    """Stampa le informazioni della foto corrente in modo leggibile."""
    sep = "═" * 62
    print(f"\n{sep}")
    print(f"  Foto {index}/{total}")
    print(sep)
    print(f"  Basename    : {entry.basename}")
    print(f"  Ritaglio    : {entry.crop_path}")

    if entry.state_path:
        print(f"  Stato TXT   : {entry.state_path}")
        # Mostra alcuni campi utili dal file stato
        sd = entry.state_data
        if sd.get("image_name"):
            print(f"  Originale   : {sd['image_name']}")
        if sd.get("image_w") and sd.get("image_h"):
            print(f"  Dim.orig.   : {sd['image_w']} × {sd['image_h']} px")
        if sd.get("fill_mode"):
            print(f"  Fill mode   : {sd['fill_mode']}")
        if sd.get("timestamp"):
            try:
                ts = datetime.datetime.fromtimestamp(int(sd["timestamp"]))
                print(f"  Processato  : {ts.strftime('%Y-%m-%d %H:%M:%S')}")
            except (ValueError, OSError):
                pass
    else:
        print("  Stato TXT   : (non trovato)")

    # Mostra versioni ChatGPT già esistenti, se presenti
    folder = entry.crop_path.parent
    existing = sorted(folder.glob(f"{entry.basename}{CHATGPT_SUFFIX}_*.jpg"))
    if existing:
        print(f"  Versioni GI : {len(existing)} ({', '.join(f.name for f in existing)})")

    print(sep)
    print("  [INVIO] Passa alla successiva  |  [y] Invia a ChatGPT  |  [ESC] Esci")
    print("  Scelta: ", end="", flush=True)


# ────────────────────────────────────────────────────────────────────────────
#  SESSIONE INTERATTIVA PRINCIPALE
# ────────────────────────────────────────────────────────────────────────────

def interactive_session(export_folder: Path, source_folder: Path) -> None:
    """
    Ciclo principale: mostra foto per foto, aspetta input utente,
    invia a ChatGPT se richiesto (flusso sincrono).
    """
    print("\n" + "═" * 62)
    print("  FotoPainter Pro — Rielaborazione Professionale ChatGPT")
    print("═" * 62)
    print(f"  Export folder : {export_folder}")
    print(f"  Source folder : {source_folder}")

    # Scansione
    photos = scan_photos(export_folder, source_folder)
    if not photos:
        print("\n[INFO] Nessuna foto trovata nella cartella export.")
        print(f"       Assicurati che contenga file *{CROP_SUFFIX}")
        return

    print(f"\n  Trovate {len(photos)} foto esportate da FotoPainter.\n")

    # Inizializzazione client (qui: prima di entrare nel loop,
    # così eventuali errori di configurazione emergono subito)
    client = setup_openai_client()
    log_path = export_folder / LOG_FILENAME

    sent_count = 0
    skip_count = 0

    for i, entry in enumerate(photos, start=1):

        # ── Verifica accessibilità del file ritagliato ──────────────────────
        if not entry.crop_path.is_file():
            print(f"\n[WARN] File non trovato, salto: {entry.crop_path}")
            continue

        try:
            # Verifica che l'immagine sia leggibile prima di mostrare la UI
            from PIL import Image as _Image
            with _Image.open(entry.crop_path) as _img:
                _ = _img.size  # forza decodifica header
        except Exception as e:
            print(f"\n[WARN] Immagine non leggibile ({entry.crop_path.name}): {e}")
            continue

        # ── Mostra informazioni ──────────────────────────────────────────────
        show_photo_info(entry, i, len(photos))

        # ── Attendi input utente ─────────────────────────────────────────────
        key = get_single_keypress()
        print(key if key not in ("enter", "esc") else "")  # echo visivo

        if key == "esc":
            print("\n  Interruzione richiesta. Uscita pulita.")
            break

        if key == "enter":
            skip_count += 1
            continue  # passa alla prossima foto

        if key != "y":
            # Tasto non riconosciuto → tratta come skip
            print(f"  (tasto '{key}' non riconosciuto — salto)")
            skip_count += 1
            continue

        # ── L'utente ha scelto y: invia a ChatGPT ───────────────────────────
        out_path = get_chatgpt_output_path(export_folder, entry.basename)
        timestamp = datetime.datetime.now().isoformat(timespec="seconds")

        print(f"\n  Elaborazione in corso… (questo può richiedere fino a "
              f"{API_TIMEOUT_SECONDS}s)")

        api_error = None
        try:
            image_bytes = send_to_openai(client, entry.crop_path)
            save_result(image_bytes, out_path)
            print(f"  ✓ Salvato: {out_path.name}")
            sent_count += 1

        except Exception as e:
            api_error = str(e)
            print(f"\n  [ERRORE API] {e}")
            print("  Il file NON è stato salvato. Puoi continuare con la prossima foto.")

        # ── Log ─────────────────────────────────────────────────────────────
        log_entry = {
            "timestamp":    timestamp,
            "originale":    entry.state_data.get("image_name", entry.basename),
            "crop_inviato": str(entry.crop_path),
            "prompt_usato": PROFESSIONAL_PROMPT[:80] + "…",
            "file_salvato": str(out_path) if not api_error else "(non salvato)",
            "errore":       api_error if api_error else "nessuno",
        }
        append_log(log_path, log_entry)

    # ── Riepilogo finale ─────────────────────────────────────────────────────
    print(f"\n{'═' * 62}")
    print(f"  Sessione terminata.")
    print(f"  Inviate a ChatGPT : {sent_count}")
    print(f"  Saltate           : {skip_count}")
    if log_path.exists():
        print(f"  Log sessione      : {log_path}")
    print("═" * 62 + "\n")


# ────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Punto di ingresso dello script.

    Uso:
        python FotoPainterProProfessionalChatGPT.py
        python FotoPainterProProfessionalChatGPT.py /percorso/a/_export_photopainter_jpg
    """
    # Determina la cartella export
    if len(sys.argv) > 1:
        export_folder = Path(sys.argv[1]).resolve()
    else:
        # Default: _export_photopainter_jpg nella stessa directory dello script
        export_folder = Path(__file__).parent / EXPORT_SUBDIR

    # La cartella sorgente (dove si trovano i _ppcrop.txt) è il genitore dell'export
    # a meno che l'export sia la root stessa (caso raro, ma gestito).
    source_folder = export_folder.parent

    if not export_folder.exists():
        print(
            f"\n[ERRORE] Cartella export non trovata: {export_folder}\n"
            f"Specifica il percorso corretto come argomento:\n"
            f"    python {Path(__file__).name} /percorso/a/{EXPORT_SUBDIR}\n"
        )
        sys.exit(1)

    interactive_session(export_folder, source_folder)


if __name__ == "__main__":
    main()
