# Third-party notices — TExporT

TExporT uses these components, unmodified and loaded as separate libraries or programs. Each one stays under its own license.

| Component | License | Source |
|---|---|---|
| Qt for Python (PySide6) and Qt 6 libraries | GNU LGPL v3 | https://code.qt.io/ · https://download.qt.io/official_releases/QtForPython/ |
| Tesseract OCR and its English language data | Apache License 2.0 | https://github.com/tesseract-ocr/tesseract |
| Leptonica (used by Tesseract) | BSD 2-Clause | https://github.com/DanBloomberg/leptonica |
| GStreamer core and base plugins | GNU LGPL v2.1+ | https://gstreamer.freedesktop.org/ |
| PipeWire GStreamer plugin | MIT | https://pipewire.org/ |
| PyGObject | GNU LGPL v2.1+ | https://gitlab.gnome.org/GNOME/pygobject |
| Python | PSF License | https://www.python.org/ |

**LGPL components.** PySide6/Qt, GStreamer and PyGObject are dynamically loaded and are not modified. You can replace them with your own builds; the Python modules and shared libraries sit unchanged inside the package. Full license texts ship with each component and are available at the URLs above. The corresponding source for the exact versions bundled is available from those upstream projects, or on request from American Milestone Inc.
