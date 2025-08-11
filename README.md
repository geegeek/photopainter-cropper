PhotoPainter Cropper (macOS)
============================

Interactive cropper for the **Waveshare PhotoPainter** (7.3" ACeP, 800×480).
It helps you frame the most important area of each photo with a fixed
**800:480** ratio. The crop rectangle may also go **outside** the image; the
empty area can be filled with **white** or with an auto-generated **blurred
background**.

I wrote this for my personal use on **macOS**. PhotoPainter has a fixed
resolution and expects **24-bit BMP** files at **480×800 or 800×480**. I often
work with mixed vertical and horizontal pictures, so I needed a quick way to
center each image and keep the best part inside the frame. Because the
rectangle selection is the core of this workflow, every save also writes a
small **text state file** next to the original photo. When I run the tool again
on the same folder, it restores the exact crop automatically. This helps a lot
with **large batches**.

Workflow
--------

* Export **JPG 800×480** (landscape) with this tool.
* Convert JPG → **24-bit BMP** with Waveshare’s official converter for better
  results on the 7-color panel (dithering, auto orientation/crop).
* Copy BMPs to the SD card for PhotoPainter.

Features
--------

* Fixed **800:480** crop ratio (landscape).
* Crop can extend **outside** the image bounds.
* **Fill options**: **White** or **Blur** (auto background from the image).
* **Per-image state** (``*_ppcrop.txt``): saves and restores the exact rectangle.
* **Keyboard**:
  - Arrows = move (hold **Shift** for faster)
  - ``+`` / ``-`` = resize (hold **Shift** for faster)
  - **Enter**, **Tab**, or **A** = save current and go next
  - **F** = toggle fill (White ↔ Blur)
  - **Esc** = skip image
* **Mouse**: drag to move, wheel to resize.
* Crisp grid lines aligned to device pixels (looks straight on Retina).

Why JPG first, then BMP?
------------------------

I tested direct BMP export that follows the device format, but the results looked
a bit **flat**. The official converter applies **dithering** and other adjustments,
and in practice produces **better images** on the 7-color display. Therefore I
export **JPG** first, then convert to **24-bit BMP** with the official tool.

Requirements
------------

* **macOS** and the **official Python** for macOS (includes Tkinter).
* Python packages: see ``requirements.txt`` (Pillow).

Notes on the device and firmware
--------------------------------

* PhotoPainter accepts **24-bit BMP** images at **480×800 or 800×480**.
* With the stock firmware, Waveshare recommends fewer than **100** files in
  the ``pic/`` directory.
* I personally use a **custom firmware** (not mine) by **@tcellerier**, which
  adds *mode 3* with **up to 2000** photos and other features.

Install
-------

.. code-block:: bash

   # Create a virtual environment (official Python on macOS)
   python3 -m venv ~/ppainter-venv
   source ~/ppainter-venv/bin/activate
   python -m pip install --upgrade pip
   pip install -r requirements.txt

Run
---

.. code-block:: bash

   source ~/ppainter-venv/bin/activate
   python photo_painter_cropper.py

* Choose a folder with images (``.jpg/.jpeg/.png/.bmp/.tif/.webp``).
* Position and size the rectangle (mouse or keyboard).
* Press **Enter / Tab / A** to save and go to the next.
* Output JPGs are written to ``_export_photopainter_jpg/`` (next to your originals).
* The tool writes a ``*_ppcrop.txt`` next to each original to **remember** the crop.

Portrait support
----------------

This version exports **landscape** (800×480). If you need **portrait** (480×800),
feel free to open an issue or send a PR.

References
----------

* Waveshare PhotoPainter wiki (specs, BMP format, Mac/Win conversion tools):
  `Waveshare PhotoPainter wiki`_

* Official image converter (JPEG → BMP) by Waveshare:
  `PhotoPainter_B`_

* Custom firmware (not mine) that adds *mode 3* with up to **2000** photos:
  `Pico_ePaper_73`_

* Article in this blog:
  `geegeek.github.io`_

License & Credits
-----------------

* Not affiliated with Waveshare. Trademarks belong to their owners.
* Firmware credit: **@tcellerier** (see `Pico_ePaper_73`_).

.. _Waveshare PhotoPainter wiki: https://www.waveshare.com/wiki/PhotoPainter
.. _PhotoPainter_B: https://github.com/waveshareteam/PhotoPainter_B
.. _Pico_ePaper_73: https://github.com/tcellerier/Pico_ePaper_73
.. _geegeek.github.io: https://geegeek.github.io/
