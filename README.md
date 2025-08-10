# PhotoPainter Cropper (macOS)

Interactive cropper for the **Waveshare PhotoPainter** (7.3" ACeP, 800×480).  
It helps me frame the most important area of each photo with a fixed **800:480** ratio.  
The crop rectangle can also go **outside** the image. The empty area can be filled with **white** or with an auto-generated **blurred background**.

I built this for my personal use on **macOS**. The PhotoPainter has a fixed resolution and needs **24-bit BMP** files at **480×800 or 800×480**. I often have mixed vertical and horizontal photos, so I needed a quick way to center each image and keep the best part inside the frame.  
Because the rectangle selection is the core of this workflow, every save also writes a small **text state file** next to the original photo. When I run the tool again on the same folder, it restores the exact crop automatically. This helps a lot with **large batches**.

- Output: **JPG 800×480** (landscape).  
- For the final BMP, I use Waveshare’s official converter, which gives better results on the 7-color panel than my own BMP export attempts.

---

## Features

- Fixed **800:480** crop ratio (landscape).
- Crop can extend **outside** the image bounds.
- **Fill options**: plain **White** or **Blur** (auto background from the image).
- **Per-image state** (`*_ppcrop.txt`): saves and restores the exact rectangle.
- **Keyboard**:
  - Arrows = move (hold **Shift** for faster)
  - `+` / `-` = resize (hold **Shift** for faster)
  - **Enter**, **Tab**, or **A** = save current and go to the next image
  - **F** = toggle fill (White ↔ Blur)
  - **Esc** = skip current
- **Mouse**: drag to move, scroll to resize.
- Crisp grid lines aligned to device pixels (looks straight on Retina).

---

## Why JPG first, then BMP?

I tested direct BMP export that follows the device format, but the results looked a bit **flat**.  
Waveshare’s official converter applies **dithering** and other adjustments, and in practice it produces **better images** on the PhotoPainter.  
My workflow:

1. Use this tool to export **JPG 800×480**.
2. Convert JPG → **24-bit BMP** using Waveshare’s official tool.
3. Copy the BMPs to the SD card.

---

## Requirements

- **macOS** with the **official Python** (includes Tkinter).
- Python packages: see `requirements.txt` (Pillow).

The PhotoPainter expects **24-bit BMP** images at **480×800 or 800×480**.  
The stock firmware limits the number of image files (in `pic/`).  
I personally use a **custom firmware** (not mine) that allows **up to 2000 photos**; see link below.

---

## Install

```bash
# Create a virtual environment (official Python on macOS)
# Adjust the 3.xx path if needed (3.12+ recommended)
python3 -m venv ~/ppainter-venv
source ~/ppainter-venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
