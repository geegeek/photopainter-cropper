#!/usr/bin/env python3
"""
photo_painter_cropperChatGPTProfessional.py
───────────────────────────────────────────
Script opzionale per il progetto photopainter-cropper.

Apre una finestra grafica (Tkinter, stessa logica del programma principale)
che mostra uno per uno i ritagli già esportati da FotoPainter.
Per ogni foto puoi decidere se inviarla alle API di OpenAI (dall-e-2) per
una rielaborazione fotografica professionale.

NON modifica né tocca photo_painter_cropper.py.
NON sovrascrive mai file esistenti.

Uso:
    python photo_painter_cropperChatGPTProfessional.py [cartella_export]

    cartella_export  percorso di _export_photopainter_jpg
                     (default: ./_export_photopainter_jpg)

Configurazione API key (una delle due strade):
    1. Variabile d'ambiente:   export OPENAI_API_KEY="sk-..."
    2. File .env accanto allo script:  OPENAI_API_KEY=sk-...

Dipendenze aggiuntive:
    pip install openai
"""

import os
import sys
import datetime
import base64
import io
import tkinter as tk
from tkinter import messagebox
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageTk


# ════════════════════════════════════════════════════════════════════════════
#
#   PROMPT — modifica liberamente questo blocco
#   ───────────────────────────────────────────
#   È l'unica parte da toccare se vuoi cambiare le istruzioni a ChatGPT.
#   Usa triple-quote per scrivere su più righe senza preoccuparti di escape.
#
# ════════════════════════════════════════════════════════════════════════════

PROFESSIONAL_PROMPT = """
Ricrea questa foto come se fosse scattata da una macchina fotografica \
professionale da un fotografo professionista, senza alterare minimamente \
i volti e le posizioni delle persone in foto.

Migliora solo: nitidezza, gamma dinamica, color grading, riduzione del \
rumore e qualità della luce. Non aggiungere, rimuovere o reinterpretare \
alcun elemento della scena. Il risultato deve sembrare lo stesso momento \
con attrezzatura migliore, non una reinterpretazione.
""".strip()

# ════════════════════════════════════════════════════════════════════════════
#   Fine sezione PROMPT
# ════════════════════════════════════════════════════════════════════════════


# ────────────────────────────────────────────────────────────────────────────
#  CONFIGURAZIONE (parametri tecnici — modifica se necessario)
# ────────────────────────────────────────────────────────────────────────────

# Sottocartella di export creata da FotoPainter
EXPORT_SUBDIR = "_export_photopainter_jpg"

# Suffissi del programma principale
CROP_SUFFIX  = "_pp.jpg"      # ritaglio esportato
STATE_SUFFIX = "_ppcrop.txt"  # file stato accanto all'originale

# Suffisso per i file prodotti da questo script
CHATGPT_SUFFIX = "_chatgpt"

# Nome del log (nella cartella export)
LOG_FILENAME = "chatgpt_pro_session.log"

# Modello usato per la Responses API (stesso pipeline di ChatGPT).
# GPT-4o vede l'immagine originale (vision) e poi usa image_generation
# per produrre una versione migliorata fedele all'originale.
OPENAI_IMAGE_MODEL = "gpt-4o"

# Finestra minima
WINDOW_MIN = (960, 640)


# ────────────────────────────────────────────────────────────────────────────
#  STRUTTURA DATI
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class PhotoEntry:
    """Tutte le info associate a un ritaglio esportato da FotoPainter."""
    basename:   str            # es. "20-3670x2462"
    crop_path:  Path           # es. …/_export_photopainter_jpg/20-3670x2462_pp.jpg
    state_path: Optional[Path] # es. …/samples/20-3670x2462_ppcrop.txt (può mancare)
    state_data: dict = field(default_factory=dict)


# ────────────────────────────────────────────────────────────────────────────
#  API KEY
# ────────────────────────────────────────────────────────────────────────────

def load_api_key() -> str:
    """Cerca OPENAI_API_KEY nell'ambiente o in un file .env accanto allo script."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        return key
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
    """Inizializza il client OpenAI o esce con messaggio chiaro."""
    try:
        from openai import OpenAI
    except ImportError:
        messagebox.showerror(
            "Libreria mancante",
            "La libreria openai non è installata.\n\nEsegui:\n    pip install openai"
        )
        sys.exit(1)

    api_key = load_api_key()
    if not api_key:
        messagebox.showerror(
            "API key mancante",
            "OPENAI_API_KEY non trovata.\n\n"
            "Imposta la variabile d'ambiente oppure crea un file .env con:\n"
            "    OPENAI_API_KEY=sk-..."
        )
        sys.exit(1)

    return OpenAI(api_key=api_key)


# ────────────────────────────────────────────────────────────────────────────
#  SCANSIONE FOTO
# ────────────────────────────────────────────────────────────────────────────

def read_state_file(txt_path: Optional[Path]) -> dict:
    """Legge un *_ppcrop.txt (key=value) e restituisce un dict. Silenzioso se assente."""
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


def scan_photos(export_folder: Path, source_folder: Path) -> list:
    """
    Trova tutti i *_pp.jpg nella cartella export.
    Per ciascuno cerca il corrispondente _ppcrop.txt nella cartella sorgente.
    """
    entries = []
    for crop_file in sorted(export_folder.glob(f"*{CROP_SUFFIX}")):
        basename = crop_file.name[: -len(CROP_SUFFIX)]
        state_path = source_folder / (basename + STATE_SUFFIX)
        if not state_path.is_file():
            state_path = None
        entries.append(PhotoEntry(
            basename=basename,
            crop_path=crop_file,
            state_path=state_path,
            state_data=read_state_file(state_path),
        ))
    return entries


# ────────────────────────────────────────────────────────────────────────────
#  NAMING PROGRESSIVO (mai sovrascrivere)
# ────────────────────────────────────────────────────────────────────────────

def get_chatgpt_output_path(export_folder: Path, basename: str) -> Path:
    """
    Restituisce il prossimo percorso disponibile:
        {basename}_chatgpt_1.jpg  →  _chatgpt_2.jpg  →  _chatgpt_3.jpg  …
    Non sovrascrive mai file esistenti.
    """
    n = 1
    while True:
        candidate = export_folder / f"{basename}{CHATGPT_SUFFIX}_{n}.jpg"
        if not candidate.exists():
            return candidate
        n += 1


# ────────────────────────────────────────────────────────────────────────────
#  API OPENAI (flusso sincrono) — Responses API con GPT-4o + image_generation
# ────────────────────────────────────────────────────────────────────────────
#
#  Questo è lo stesso pipeline usato da ChatGPT:
#    1. GPT-4o riceve l'immagine originale e la "vede" davvero (vision)
#    2. Il tool image_generation produce una versione migliorata
#  A differenza di images.edit(), il modello comprende il contenuto prima
#  di generare — volti, posizioni e composizione vengono preservati.

def _image_to_base64(crop_path: Path) -> str:
    """Legge il file e restituisce la stringa base64."""
    with open(crop_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def send_to_openai(client, crop_path: Path) -> bytes:
    """
    Invia il ritaglio alle API OpenAI (GPT-4o Responses API) con PROFESSIONAL_PROMPT.
    GPT-4o vede l'immagine originale, poi usa il tool image_generation per
    produrre una versione migliorata fedele all'originale.
    Lancia eccezione in caso di errore.
    """
    b64 = _image_to_base64(crop_path)
    # Determina il mime type dal suffisso del file
    suffix = crop_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"

    response = client.responses.create(
        model="gpt-4o",
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_image",
                        "image_url": f"data:{mime};base64,{b64}",
                    },
                    {
                        "type": "input_text",
                        "text": PROFESSIONAL_PROMPT,
                    },
                ],
            }
        ],
        tools=[{"type": "image_generation"}],
    )

    # Cerca il risultato nell'output della risposta
    for item in response.output:
        if getattr(item, "type", None) == "image_generation_call":
            result = getattr(item, "result", None)
            if result:
                return base64.b64decode(result)

    raise ValueError("La risposta API non contiene immagini generate.")


def save_result(image_bytes: bytes, out_path: Path) -> None:
    """Salva i byte ricevuti dall'API come JPEG qualità 95."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img.save(out_path, format="JPEG", quality=95, progressive=True)


# ────────────────────────────────────────────────────────────────────────────
#  LOGGING
# ────────────────────────────────────────────────────────────────────────────

def append_log(log_path: Path, entry: dict) -> None:
    """Appende una voce leggibile al file di log (non sovrascrive mai)."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n" + "─" * 60 + "\n")
            for k, v in entry.items():
                f.write(f"{k}: {v}\n")
    except Exception as e:
        print(f"[WARN] Impossibile scrivere nel log {log_path}: {e}")


# ────────────────────────────────────────────────────────────────────────────
#  APPLICAZIONE TKINTER
# ────────────────────────────────────────────────────────────────────────────

class ReviewerApp:
    """
    Finestra Tkinter che mostra uno per uno i ritagli esportati da FotoPainter.

    Controlli:
        INVIO   → passa alla foto successiva (skip)
        Y       → invia a ChatGPT, aspetta risposta, salva, avanza
        ESC     → chiude la sessione in modo pulito
    """

    # colori dell'interfaccia (stessa palette del programma principale)
    BG_DARK   = "#111111"
    BG_BAR    = "#1a1a1a"
    FG_INFO   = "#cccccc"
    FG_OK     = "#00ff88"
    FG_WARN   = "#ffaa00"
    FG_ERR    = "#ff4444"

    def __init__(self, root: tk.Tk, photos: list, client,
                 export_folder: Path, log_path: Path):
        self.root          = root
        self.photos        = photos
        self.client        = client
        self.export_folder = export_folder
        self.log_path      = log_path

        self.idx         = 0
        self.sent_count  = 0
        self.skip_count  = 0
        self.tk_img      = None   # riferimento mantenuto per evitare GC
        self.current_pil = None   # PIL Image della foto corrente

        self._build_ui()
        self._bind_keys()
        self.show_current()

    # ── costruzione UI ───────────────────────────────────────────────────────

    def _build_ui(self):
        self.root.title("FotoPainter Pro — Revisione ChatGPT")
        self.root.minsize(*WINDOW_MIN)
        self.root.configure(bg=self.BG_DARK)

        # barra superiore: info foto corrente
        top = tk.Frame(self.root, bg=self.BG_BAR)
        top.pack(fill=tk.X, side=tk.TOP)
        self.info_lbl = tk.Label(
            top, text="", bg=self.BG_BAR, fg=self.FG_INFO,
            font=("monospace", 11), anchor="w"
        )
        self.info_lbl.pack(padx=12, pady=7, fill=tk.X)

        # canvas centrale: mostra il ritaglio
        self.canvas = tk.Canvas(self.root, bg=self.BG_DARK, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # barra inferiore: istruzioni / stato operazione
        bottom = tk.Frame(self.root, bg=self.BG_BAR)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_lbl = tk.Label(
            bottom,
            text="  [INVIO] Prossima foto   [Y] Invia a ChatGPT   [ESC] Esci",
            bg=self.BG_BAR, fg=self.FG_OK,
            font=("monospace", 11), anchor="w"
        )
        self.status_lbl.pack(padx=12, pady=7, fill=tk.X)

    def _bind_keys(self):
        self.root.bind("<Return>",    self.on_next)
        self.root.bind("<Escape>",    self.on_quit)
        self.root.bind("<y>",         self.on_send)
        self.root.bind("<Y>",         self.on_send)
        self.root.bind("<Configure>", self.on_resize)

    # ── navigazione foto ─────────────────────────────────────────────────────

    def show_current(self):
        """Carica e mostra la foto corrente; gestisce fine lista e immagini corrotte."""
        if self.idx >= len(self.photos):
            self._on_all_done()
            return

        entry = self.photos[self.idx]

        # carica il ritaglio _pp.jpg
        try:
            self.current_pil = Image.open(entry.crop_path).convert("RGB")
        except Exception as e:
            self._set_status(f"[WARN] Immagine non leggibile: {e}", self.FG_WARN)
            self.root.after(1500, self._advance)
            return

        self._update_info_label(entry)
        self._set_status(
            "  [INVIO] Prossima foto   [Y] Invia a ChatGPT   [ESC] Esci",
            self.FG_OK
        )
        self._redraw()

    def _advance(self):
        self.idx += 1
        self.show_current()

    # ── rendering ────────────────────────────────────────────────────────────

    def _redraw(self):
        """Scala e disegna la foto corrente centrata sul canvas (dark background)."""
        if not self.current_pil:
            return

        cw = max(self.canvas.winfo_width(),  WINDOW_MIN[0])
        ch = max(self.canvas.winfo_height(), WINDOW_MIN[1] - 80)

        iw, ih = self.current_pil.size
        scale  = min(cw / iw, ch / ih)
        dw     = max(1, int(iw * scale))
        dh     = max(1, int(ih * scale))

        resized      = self.current_pil.resize((dw, dh), Image.LANCZOS)
        self.tk_img  = ImageTk.PhotoImage(resized)

        ox = (cw - dw) // 2
        oy = (ch - dh) // 2

        self.canvas.delete("all")
        self.canvas.create_image(ox, oy, anchor="nw", image=self.tk_img)

    def on_resize(self, event=None):
        self._redraw()

    # ── label helpers ─────────────────────────────────────────────────────────

    def _update_info_label(self, entry: PhotoEntry):
        """Costruisce la riga informativa nella barra superiore."""
        sd = entry.state_data
        parts = [f"Foto {self.idx + 1}/{len(self.photos)}  —  {entry.basename}"]

        if sd.get("image_name"):
            parts.append(f"orig: {sd['image_name']}")
        if sd.get("image_w") and sd.get("image_h"):
            parts.append(f"{sd['image_w']}×{sd['image_h']} px")
        if sd.get("fill_mode"):
            parts.append(f"fill={sd['fill_mode']}")
        if sd.get("timestamp"):
            try:
                ts = datetime.datetime.fromtimestamp(int(sd["timestamp"]))
                parts.append(ts.strftime("%Y-%m-%d %H:%M"))
            except (ValueError, OSError):
                pass

        # versioni ChatGPT già esistenti per questa foto
        existing = sorted(
            self.export_folder.glob(f"{entry.basename}{CHATGPT_SUFFIX}_*.jpg")
        )
        if existing:
            parts.append(f"versioni ChatGPT già presenti: {len(existing)}")

        self.info_lbl.config(text="  " + "   │   ".join(parts))

    def _set_status(self, text: str, color: str):
        self.status_lbl.config(text=f"  {text}", fg=color)

    # ── azioni utente ─────────────────────────────────────────────────────────

    def on_next(self, event=None):
        """INVIO → salta alla foto successiva."""
        self.skip_count += 1
        self._advance()

    def on_quit(self, event=None):
        """ESC → chiude la sessione in modo pulito."""
        self._show_summary()
        self.root.after(50, self.root.quit)

    def on_send(self, event=None):
        """
        Y → invia il ritaglio a ChatGPT (flusso sincrono):
            1. mostra stato "invio in corso"
            2. chiama l'API (bloccante — la finestra è ferma ma non crasha)
            3. salva il risultato
            4. logga
            5. avanza alla prossima foto
        """
        entry   = self.photos[self.idx]
        out_path = get_chatgpt_output_path(self.export_folder, entry.basename)
        ts       = datetime.datetime.now().isoformat(timespec="seconds")

        # feedback immediato prima di bloccare il thread UI
        self._set_status(
            f"Invio a OpenAI ({OPENAI_IMAGE_MODEL})… può richiedere 20-40 secondi.",
            self.FG_WARN
        )
        self.info_lbl.config(text=f"  Elaborazione: {entry.basename}  —  non chiudere la finestra")
        self.root.update()   # forza il ridisegno prima della chiamata bloccante

        api_error = None
        try:
            image_bytes = send_to_openai(self.client, entry.crop_path)
            save_result(image_bytes, out_path)
            self.sent_count += 1
            self._set_status(f"✓ Salvato: {out_path.name}", self.FG_OK)
            self.root.update()
        except Exception as e:
            api_error = str(e)
            self._set_status(f"[ERRORE API] {e}", self.FG_ERR)
            self.root.update()
            # lascia il messaggio di errore visibile 3 secondi, poi avanza
            self.root.after(3000, self._advance)
            self._write_log(ts, entry, out_path, api_error)
            return

        self._write_log(ts, entry, out_path, api_error)
        # breve pausa per leggere il messaggio di successo, poi avanza
        self.root.after(1200, self._advance)

    # ── log ──────────────────────────────────────────────────────────────────

    def _write_log(self, ts: str, entry: PhotoEntry,
                   out_path: Path, api_error: Optional[str]):
        append_log(self.log_path, {
            "timestamp":    ts,
            "originale":    entry.state_data.get("image_name", entry.basename),
            "crop_inviato": str(entry.crop_path),
            "prompt_usato": PROFESSIONAL_PROMPT[:80] + "…",
            "file_salvato": str(out_path) if not api_error else "(non salvato)",
            "errore":       api_error if api_error else "nessuno",
        })

    # ── fine sessione ─────────────────────────────────────────────────────────

    def _on_all_done(self):
        self._show_summary()
        self.root.after(50, self.root.quit)

    def _show_summary(self):
        msg = (
            f"Sessione completata.\n\n"
            f"Inviate a ChatGPT : {self.sent_count}\n"
            f"Saltate           : {self.skip_count}\n"
        )
        if self.log_path.exists():
            msg += f"\nLog: {self.log_path}"
        messagebox.showinfo("FotoPainter Pro — Riepilogo", msg)


# ────────────────────────────────────────────────────────────────────────────
#  ENTRY POINT
# ────────────────────────────────────────────────────────────────────────────

def main():
    """
    Uso:
        python photo_painter_cropperChatGPTProfessional.py
        python photo_painter_cropperChatGPTProfessional.py /percorso/a/_export_photopainter_jpg
    """
    # determina la cartella export
    if len(sys.argv) > 1:
        export_folder = Path(sys.argv[1]).resolve()
    else:
        export_folder = Path(__file__).parent / EXPORT_SUBDIR

    if not export_folder.is_dir():
        # usa una finestra Tkinter minima per l'errore, poi esce
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "Cartella non trovata",
            f"Cartella export non trovata:\n{export_folder}\n\n"
            f"Specifica il percorso come argomento:\n"
            f"    python {Path(__file__).name} /percorso/a/{EXPORT_SUBDIR}"
        )
        sys.exit(1)

    # la cartella sorgente (dove stanno i _ppcrop.txt) è il genitore dell'export
    source_folder = export_folder.parent

    # scansione foto
    photos = scan_photos(export_folder, source_folder)
    if not photos:
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo(
            "Nessuna foto",
            f"Nessun file *{CROP_SUFFIX} trovato in:\n{export_folder}\n\n"
            "Esegui prima photo_painter_cropper.py per generare i ritagli."
        )
        sys.exit(0)

    # inizializza client (esce con messagebox se manca la chiave)
    client   = setup_openai_client()
    log_path = export_folder / LOG_FILENAME

    # avvia la GUI
    root = tk.Tk()
    ReviewerApp(root, photos, client, export_folder, log_path)
    root.mainloop()


if __name__ == "__main__":
    main()
