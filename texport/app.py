"""TExporT – floating, translucent capture panel."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from .qt import (QAbstractItemView, QApplication, QBrush, QCursor, QButtonGroup, QBuffer, QCheckBox, QGroupBox, QRadioButton, QColor, QDesktopServices,
                 QDialog, QDialogButtonBox, QFileDialog, QGuiApplication, QHBoxLayout, QImage, QInputDialog,
                 QIODevice, QKeySequence, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMarginsF,
                 QMessageBox, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen, QPixmap, QPlainTextEdit,
                 QPoint, QPushButton, QRect, QShortcut, QSizeGrip, QSlider, Qt, QTextDocument, QTimer, QUrl,
                 QVBoxLayout, QWidget, Signal, image_bytes, qbytes)

from . import __version__, export, mg8
from . import pipeline as nych_pipeline
from .capture import is_hyprland, make_source, session_is_wayland
from .ocr import OcrError, clean_frame_lines, ocr_png, tesseract_available
from .stitch import Stitcher
from .autoscroll import AutoScroller, same_view
from .log import LOG, log

CONFIG_DIR = Path.home() / ".config" / "texport"
DATA_DIR = Path.home() / ".local" / "share" / "texport" / "sessions"
LEGACY_DATA_DIR = Path.home() / ".local" / "share" / "convo-capture" / "sessions"
MG8_URL = "https://github.com/nhartman000/mg8"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def list_sessions() -> list[dict]:
    out = []
    if DATA_DIR.exists():
        for p in DATA_DIR.glob("*.json"):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:  # noqa: BLE001
                pass
    out.sort(key=lambda s: s.get("captured_at", ""), reverse=True)
    return out


def migrate_legacy() -> None:
    """Bring over captures made with the earlier Convo Capture build."""
    if LEGACY_DATA_DIR.exists() and not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for p in LEGACY_DATA_DIR.glob("*.json"):
            (DATA_DIR / p.name).write_bytes(p.read_bytes())


# --------------------------------------------------------------------------- region picker
class _Canvas(QWidget):
    def __init__(self, pix: QPixmap, rect: QRect | None):
        super().__init__()
        self.pix = pix
        self.setFixedSize(pix.size())
        self.rect_ = rect
        self._origin: QPoint | None = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, e):
        self._origin = e.position().toPoint()
        self.rect_ = QRect(self._origin, self._origin)
        self.update()

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self.rect_ = QRect(self._origin, e.position().toPoint()).normalized()
            self.update()

    def mouseReleaseEvent(self, e):
        self._origin = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.drawPixmap(0, 0, self.pix)
        if self.rect_ and self.rect_.width() > 4:
            shade = QColor(0, 0, 0, 120)
            full = self.rect()
            r = self.rect_
            p.fillRect(QRect(0, 0, full.width(), r.top()), shade)
            p.fillRect(QRect(0, r.bottom(), full.width(), full.height() - r.bottom()), shade)
            p.fillRect(QRect(0, r.top(), r.left(), r.height()), shade)
            p.fillRect(QRect(r.right(), r.top(), full.width() - r.right(), r.height()), shade)
            p.setPen(QPen(QColor("#f59e0b"), 2))
            p.drawRect(r)


class RegionDialog(QDialog):
    def __init__(self, img: QImage, current: QRect | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Drag a box around the conversation text")
        avail = (QGuiApplication.primaryScreen().availableGeometry())
        self.scale = min(avail.width() * 0.85 / img.width(), avail.height() * 0.78 / img.height(), 1.0)
        pix = QPixmap.fromImage(img).scaled(int(img.width() * self.scale), int(img.height() * self.scale),
                                            Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
        cur = None
        if current:
            s = self.scale
            cur = QRect(int(current.x() * s), int(current.y() * s), int(current.width() * s), int(current.height() * s))
        self.canvas = _Canvas(pix, cur)
        self.img_size = img.size()
        tip = QLabel("Include only the message column (leave out the sidebar and the input box). "
                     "Then scroll the conversation yourself while capturing.")
        tip.setWordWrap(True)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(self.canvas)
        lay.addWidget(bb)

    def region(self) -> QRect | None:
        r = self.canvas.rect_
        if not r or r.width() < 40 or r.height() < 40:
            return None
        s = self.scale
        out = QRect(int(r.x() / s), int(r.y() / s), int(r.width() / s), int(r.height() / s))
        return out.intersected(QRect(QPoint(0, 0), self.img_size))


# --------------------------------------------------------------------------- export dialog
class ExportDialog(QDialog):
    def __init__(self, current_id: str, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Export marked document")
        self.sessions = list_sessions()
        self.list = QListWidget()
        for s in self.sessions:
            n_m = len(s.get("markers", []))
            it = QListWidgetItem(f"{s.get('title') or 'Untitled'} — {len(s.get('lines', []))} lines, "
                                 f"{n_m} marker(s) · {s.get('captured_at', '')[:16].replace('T', ' ')}")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if s.get("id") == current_id else Qt.CheckState.Unchecked)
            self.list.addItem(it)
        self.title = QLineEdit("Marked conversations")
        self.mg8_box = QCheckBox("Also write an MG8 unit — lets you Verify the AI's answer afterwards")
        self.mg8_box.setChecked(bool(cfg.get("export_mg8", True)))
        self.intent = QLineEdit(cfg.get("last_intent", ""))
        self.intent.setPlaceholderText("Optional: what should the receiving AI do with this? (stored as the intent)")

        self.pipe_box = QGroupBox("NYCH encoder → mg8-engine handoff (files for the LLM step)")
        self.pipe_box.setCheckable(True)
        self.pipe_box.setChecked(bool(cfg.get("export_pipeline", False)))
        self.fmt_nych = QRadioButton("NYCH format — symbolic pretext (nych.bonit step 2: Gestalt mapping)")
        self.fmt_text = QRadioButton("Text format — same schemas, plain language (mg8.bonit step 3)")
        (self.fmt_text if cfg.get("pipeline_format") == "text" else self.fmt_nych).setChecked(True)
        self.g_marker = QRadioButton("One encoder unit per marker (recommended)")
        self.g_combined = QRadioButton("All marked text as one unit")
        (self.g_combined if cfg.get("pipeline_units") == "combined" else self.g_marker).setChecked(True)
        fg, gg = QButtonGroup(self), QButtonGroup(self)
        for b in (self.fmt_nych, self.fmt_text):
            fg.addButton(b)
        for b in (self.g_marker, self.g_combined):
            gg.addButton(b)
        self.run_box = QCheckBox("Run it through mg8-engine now (uses the LLM configured in mg8-engine)")
        self.run_box.setChecked(bool(cfg.get("pipeline_run", False)))
        pl = QVBoxLayout(self.pipe_box)
        for w in (self.fmt_nych, self.fmt_text, self.g_marker, self.g_combined, self.run_box):
            pl.addWidget(w)
        ws_lbl = QLabel(f"Workspace: {nych_pipeline.workspace(cfg)}  (change with ⚙ on the panel)")
        ws_lbl.setStyleSheet("color: #888; font-size: 10px;")
        pl.addWidget(ws_lbl)
        bb = QDialogButtonBox()
        bb.addButton("Save…", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Captures to include (in this order — tick several to combine them into one document):"))
        lay.addWidget(self.list)
        lay.addWidget(QLabel("Document title:"))
        lay.addWidget(self.title)
        lay.addWidget(self.mg8_box)
        lay.addWidget(self.intent)
        lay.addWidget(self.pipe_box)
        lay.addWidget(bb)
        self.resize(600, 640)

    def _save(self):
        chosen = [self.sessions[i] for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.CheckState.Checked]
        if not chosen:
            QMessageBox.warning(self, "Export", "Tick at least one capture.")
            return
        chosen.reverse()  # oldest first
        title = self.title.text().strip() or "Marked conversations"
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip().replace(" ", "_")
        default = str((Path.home() / "Documents" / f"{safe}.md"))
        path, _ = QFileDialog.getSaveFileName(self, "Save", default, "Markdown (*.md)")
        if not path:
            return
        md_path = Path(path).with_suffix(".md")
        pdf_path = md_path.with_suffix(".pdf")
        md_path.write_text(export.render_markdown(chosen, title), encoding="utf-8")
        writer = QPdfWriter(str(pdf_path))
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
        writer.setPageMargins(QMarginsF(16, 16, 16, 16), QPageLayout.Unit.Millimeter)
        doc = QTextDocument()
        doc.setHtml(export.render_html(chosen, title))
        printer = getattr(doc, "print", None) or getattr(doc, "print_")
        printer(writer)
        saved = [str(md_path), str(pdf_path)]
        self.cfg["export_mg8"] = self.mg8_box.isChecked()
        self.cfg["last_intent"] = self.intent.text().strip()
        if self.mg8_box.isChecked():
            unit = mg8.write_unit(chosen, title, md_path.with_name(md_path.stem + ".mg8"),
                                  intent=self.intent.text())
            self.cfg["last_unit"] = str(unit)
            saved.append(f"{unit.parent}/  (MG8 unit)")
        self.cfg["export_pipeline"] = self.pipe_box.isChecked()
        self.cfg["pipeline_format"] = "text" if self.fmt_text.isChecked() else "nych"
        self.cfg["pipeline_units"] = "combined" if self.g_combined.isChecked() else "marker"
        self.cfg["pipeline_run"] = self.run_box.isChecked()
        save_config(self.cfg)
        if self.pipe_box.isChecked():
            handoff_dir = md_path.with_name(md_path.stem + ".handoff")
            saved.append(f"{handoff_dir}/  (NYCH/MG8 handoff — {'running' if self.run_box.isChecked() else 'preparing'} "
                         "in the background, the panel will tell you when it's done)")
            parent = self.parent()
            if parent is not None and hasattr(parent, "start_pipeline"):
                parent.start_pipeline(chosen, handoff_dir, self.cfg["pipeline_format"],
                                      self.cfg["pipeline_units"], self.intent.text().strip(),
                                      self.run_box.isChecked())
        msg = "Saved:\n" + "\n".join(saved)
        if self.mg8_box.isChecked():
            msg += ("\n\nHand the .md or .pdf to any AI. When it answers, click Verify… and paste the "
                    "answer to check that every marker survived.")
        QMessageBox.information(self, "Export", msg)
        self.accept()


class VerifyDialog(QDialog):
    """Deterministic MG8 retention check of a receiving model's answer."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("Verify an AI answer (MG8 retention gates)")
        self.unit = QLineEdit(cfg.get("last_unit", ""))
        self.unit.setPlaceholderText("Path to unit.mg8 (written by Export with the MG8 option)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.unit, 1)
        row.addWidget(browse)
        self.label = QLineEdit()
        self.label.setPlaceholderText("Optional label, e.g. which model / date")
        self.response = QPlainTextEdit()
        self.response.setPlaceholderText("Paste the AI's full answer here…")
        self.result = QLabel("")
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        run = QPushButton("Run gates")
        run.clicked.connect(self._run)
        self.open_btn = QPushButton("Open trace folder")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns = QHBoxLayout()
        btns.addWidget(run)
        btns.addWidget(self.open_btn)
        btns.addStretch()
        btns.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("MG8 unit:"))
        lay.addLayout(row)
        lay.addWidget(self.label)
        lay.addWidget(self.response, 1)
        lay.addLayout(btns)
        lay.addWidget(self.result)
        foot = QLabel(f'Gates are evaluated by TExporT with fixed rules — no AI grades itself. '
                      f'Results are written as an append-only <a href="{MG8_URL}">MG8</a> .qson trace.')
        foot.setOpenExternalLinks(True)
        foot.setWordWrap(True)
        foot.setStyleSheet("color: #888; font-size: 10px;")
        lay.addWidget(foot)
        self.resize(640, 560)
        self._trace_dir = None

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose unit.mg8", self.unit.text() or str(Path.home()),
                                              "MG8 unit (*.mg8)")
        if path:
            self.unit.setText(path)

    def _run(self):
        unit = Path(self.unit.text().strip())
        if unit.is_dir():
            unit = unit / "unit.mg8"
        if not unit.is_file():
            QMessageBox.warning(self, "Verify", "Choose the unit.mg8 file from an MG8 export.")
            return
        try:
            out = mg8.verify(unit, self.response.toPlainText(), label=self.label.text().strip())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Verify", str(e))
            return
        self.cfg["last_unit"] = str(unit)
        save_config(self.cfg)
        colors = {"PASS": "#16a34a", "FAIL": "#dc2626", "INTERMEDIATE": "#d97706"}
        names = {"marker_coverage": "Every marker cited by ID",
                 "anchor_fidelity": "Key values reproduced verbatim",
                 "no_phantom_ids": "No made-up marker IDs"}
        rows = []
        for r in out["results"]:
            ev = r.get("evidence") or {}
            detail = ""
            if ev.get("missing_ids"):
                detail = "missing: " + ", ".join(ev["missing_ids"])
            elif ev.get("missing_anchors"):
                detail = "; ".join(f"{k}: " + ", ".join(v) for k, v in ev["missing_anchors"].items())
            elif ev.get("unknown_ids"):
                detail = "unknown: " + ", ".join(ev["unknown_ids"])
            rows.append(f'<p><b style="color:{colors[r["result"]]}">{r["result"]}</b> &nbsp; '
                        f'{names.get(r["gate_id"], r["gate_id"])} — {r["reason"]}'
                        + (f'<br><span style="color:#888">{_esc(detail)}</span>' if detail else "") + "</p>")
        rows.append(f'<p style="color:#888">Trace: {_esc(str(out["qson"]))}</p>')
        self.result.setText("".join(rows))
        self._trace_dir = out["qson"].parent
        self.open_btn.setEnabled(True)

    def _open(self):
        if self._trace_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._trace_dir)))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class PipelineSettingsDialog(QDialog):
    """Where the user's nych / mg8-engine install lives."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("NYCH / mg8-engine connection")
        self.ws = QLineEdit(str(nych_pipeline.workspace(cfg)))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.ws, 1)
        row.addWidget(browse)
        self.py = QLineEdit(cfg.get("nych_python", ""))
        self.py.setPlaceholderText(f"Auto: {nych_pipeline.find_python(cfg)}")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        test = QPushButton("Check connection")
        test.clicked.connect(self._probe)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("NYCH workspace (the folder holding your nych, mg8-engine and T.O.T.E-loops checkouts):"))
        lay.addLayout(row)
        lay.addWidget(QLabel("Python with nych + mg8-engine installed (leave empty to auto-detect the mg8-engine .venv):"))
        lay.addWidget(self.py)
        lay.addWidget(test)
        lay.addWidget(self.status)
        lay.addWidget(bb)
        self.resize(600, 340)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "NYCH workspace", self.ws.text())
        if d:
            self.ws.setText(d)

    def _apply(self):
        self.cfg["nych_workspace"] = self.ws.text().strip()
        if self.py.text().strip():
            self.cfg["nych_python"] = self.py.text().strip()
        else:
            self.cfg.pop("nych_python", None)

    def _probe(self):
        self._apply()
        try:
            r = nych_pipeline.probe(self.cfg)
        except Exception as e:  # noqa: BLE001
            self.status.setText(f'<span style="color:#dc2626">Not connected:</span> {_esc(str(e))}')
            return
        ok = lambda b: '<b style="color:#16a34a">✓</b>' if b else '<b style="color:#dc2626">✗</b>'  # noqa: E731
        n, m, f = r.get("nych", {}), r.get("mg8_engine", {}), r.get("files", {})
        self.status.setText(
            f"Python {_esc(r.get('version', '?'))} ({_esc(r.get('python', ''))})<br>"
            f"{ok(n.get('ok'))} nych {_esc(str(n.get('version') or ''))} {_esc(n.get('error', ''))}<br>"
            f"{ok(m.get('ok'))} mg8-engine {_esc(m.get('error', ''))}"
            f" — LLM: {_esc(str(m.get('llm_provider') or 'not configured (needed only for Run)'))}<br>"
            f"{ok(f.get('nych_bonit'))} nych.bonit &nbsp; {ok(f.get('mg8_bonit'))} mg8.bonit &nbsp; "
            f"{ok(f.get('tote_db'))} TOTE-loops database {'' if f.get('tote_db') else '(optional — lookups report a fallback)'}")

    def _save(self):
        self._apply()
        save_config(self.cfg)
        self.accept()


# --------------------------------------------------------------------------- main panel
class Panel(QWidget):
    sig_ready = Signal()
    sig_error = Signal(str)
    sig_ocr = Signal(object, int, str)
    sig_pipeline = Signal(object, str)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("TExporT")
        self.cfg = load_config()
        self.alpha = int(self.cfg.get("alpha", 215))
        self.source = None
        self.region: QRect | None = QRect(*self.cfg["region"]) if self.cfg.get("region") else None
        self.frame_size = None
        self.capturing = False
        self.busy = False
        self.last_hash = None
        self.stitcher = Stitcher()
        self.auto = None              # auto-scroll state while running
        self._pending_auto = False
        self.new_session()

        self.sig_ready.connect(self._on_source_ready)
        self.sig_error.connect(self._on_source_error)
        self.sig_ocr.connect(self._on_ocr)
        self.sig_pipeline.connect(self._on_pipeline)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self._tick)
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(120)
        self.auto_timer.timeout.connect(self._auto_tick)
        self._build_ui()
        self.resize(*self.cfg.get("size", [500, 660]))
        if not tesseract_available():
            self.status("tesseract missing — run: sudo apt install tesseract-ocr", warn=True)

    # ---- UI ------------------------------------------------------------
    def _build_ui(self):
        self.setStyleSheet("""
            QWidget { color: #f4f4f5; font-size: 12px; }
            QLabel#title { font-weight: 700; font-size: 13px; }
            QPushButton { background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.18);
                          border-radius: 6px; padding: 5px 9px; }
            QPushButton:hover { background: rgba(255,255,255,0.20); }
            QPushButton#rec[on="true"] { background: #b91c1c; border-color: #ef4444; }
            QPushButton#tiny { padding: 1px 7px; }
            QLineEdit { background: rgba(0,0,0,0.35); border: 1px solid rgba(255,255,255,0.15);
                        border-radius: 5px; padding: 4px; }
            QListWidget { background: rgba(0,0,0,0.30); border: 1px solid rgba(255,255,255,0.12);
                          border-radius: 6px; }
            QListWidget::item:selected { background: rgba(59,130,246,0.55); }
        """)
        self.header = QWidget()
        h = QHBoxLayout(self.header)
        h.setContentsMargins(0, 0, 0, 0)
        t = QLabel("◉ TExporT")
        t.setObjectName("title")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(60, 255)
        self.opacity.setValue(self.alpha)
        self.opacity.setFixedWidth(80)
        self.opacity.setToolTip("Panel transparency")
        self.opacity.valueChanged.connect(self._set_alpha)
        gear = QPushButton("⚙")
        gear.setObjectName("tiny")
        gear.setToolTip("Connect to your nych / mg8-engine install")
        gear.clicked.connect(lambda: PipelineSettingsDialog(self.cfg, self).exec())
        about = QPushButton("?")
        about.setObjectName("tiny")
        about.clicked.connect(self.about)
        mini = QPushButton("–")
        mini.setObjectName("tiny")
        mini.clicked.connect(self.showMinimized)
        close = QPushButton("×")
        close.setObjectName("tiny")
        close.clicked.connect(self.close)
        h.addWidget(t)
        h.addStretch()
        h.addWidget(self.opacity)
        h.addWidget(gear)
        h.addWidget(about)
        h.addWidget(mini)
        h.addWidget(close)

        self.title_edit = QLineEdit(self.session["title"])
        self.title_edit.setPlaceholderText("Name this capture (e.g. the conversation title)")
        self.title_edit.textChanged.connect(lambda s: self.session.__setitem__("title", s))

        b_share = QPushButton("Share…")
        b_share.setToolTip("Choose what to capture (pick the Claude window)")
        b_share.clicked.connect(lambda: self.start_source(fresh=True))
        b_region = QPushButton("Region…")
        b_region.clicked.connect(self.pick_region)
        self.b_rec = QPushButton("● Capture")
        self.b_rec.setObjectName("rec")
        self.b_rec.clicked.connect(self.toggle_capture)
        b_new = QPushButton("New")
        b_new.setToolTip("Save this capture and start a new one")
        b_new.clicked.connect(self._new_clicked)
        b_exp = QPushButton("Export…")
        b_exp.clicked.connect(self.export)
        b_ver = QPushButton("Verify…")
        b_ver.setToolTip("Check an AI's answer against an MG8 export (deterministic retention gates)")
        b_ver.clicked.connect(lambda: VerifyDialog(self.cfg, self).exec())
        self.auto_box = QCheckBox("Auto-scroll")
        self.auto_box.setToolTip("TExporT scrolls the shared window for you: 2 notches, read, repeat. "
                                 "On Ubuntu this needs a one-time 'remote interaction' permission.")
        self.auto_box.setChecked(bool(self.cfg.get("autoscroll", False)))
        self.auto_box.toggled.connect(lambda v: (self.cfg.__setitem__("autoscroll", v), save_config(self.cfg)))
        self.auto_box.toggled.connect(lambda v: (not v and self.auto is not None
                                                 and self.auto.get("phase") == "paused"
                                                 and self._auto_stop("Finished.")))
        row = QHBoxLayout()
        for b in (b_share, b_region, self.b_rec, self.auto_box, b_new, b_exp, b_ver):
            row.addWidget(b)
        self._lockables = [b_share, b_region, self.auto_box, b_new, b_exp, b_ver]

        self.status_lbl = QLabel("1) Share…  2) Region…  3) ● Capture, then scroll the conversation slowly.")
        self.status_lbl.setWordWrap(True)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setWordWrap(True)
        self.list.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.list.itemChanged.connect(self._item_edited)

        marks = QHBoxLayout()
        self._mark_buttons = []
        for label, key, fn, tip in (
                ("Mark", "M", self.mark_selection, "Mark the selected line(s)"),
                ("Mark ¶", "P", self.mark_paragraph, "Mark the whole paragraph(s) of the selection"),
                ("Note", "N", self.note, "Add/edit a note on the marker"),
                ("Unmark", "U", self.unmark, "Remove markers touching the selection"),
                ("Delete", "Delete", self.delete_lines, "Delete junk OCR lines")):
            b = QPushButton(f"{label} ({key})" if len(key) == 1 else label)
            b.setToolTip(tip + f"  [{key}]")
            b.clicked.connect(fn)
            marks.addWidget(b)
            self._lockables.append(b)
            self._mark_buttons.append(b)
            seq = QKeySequence(QKeySequence.StandardKey.Delete) if key == "Delete" else QKeySequence(key)
            sc = QShortcut(seq, self.list)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

        esc = QShortcut(QKeySequence("Escape"), self)
        esc.setContext(Qt.ShortcutContext.ApplicationShortcut)
        esc.activated.connect(lambda: self.auto and self._auto_stop("Stopped."))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 8)
        lay.addWidget(self.header)
        lay.addWidget(self.title_edit)
        lay.addLayout(row)
        lay.addWidget(self.status_lbl)
        lay.addWidget(self.list, 1)
        lay.addLayout(marks)
        grip_row = QHBoxLayout()
        hint = QLabel("Double-click a line to fix OCR text. Shift/Ctrl-click to select ranges.")
        hint.setStyleSheet("color: #a1a1aa; font-size: 10px;")
        grip_row.addWidget(hint)
        grip_row.addStretch()
        brand = QLabel(f'<a style="color:#fbbf24;text-decoration:none" href="{MG8_URL}">MG8-ready</a>'
                       '<span style="color:#71717a"> · American Milestone</span>')
        brand.setOpenExternalLinks(True)
        brand.setStyleSheet("font-size: 10px;")
        grip_row.addWidget(brand)
        grip_row.addWidget(QSizeGrip(self))
        lay.addLayout(grip_row)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(24, 24, 27, self.alpha)))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 12, 12)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 40:
            wh = self.windowHandle()
            if wh is not None:
                wh.startSystemMove()

    def _set_alpha(self, v):
        self.alpha = v
        self.update()

    def status(self, msg: str, warn: bool = False):
        self.status_lbl.setText(msg)
        self.status_lbl.setStyleSheet("color: #fca5a5;" if warn else "")

    # ---- session -------------------------------------------------------
    def new_session(self):
        self.session = {"id": uuid.uuid4().hex[:12], "title": "",
                        "source": "Claude desktop (screen capture)",
                        "captured_at": datetime.now().isoformat(timespec="seconds"),
                        "lines": [], "markers": []}
        self.stitcher = Stitcher()

    def save_session(self):
        if not self.session["lines"]:
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not self.session["title"].strip():
            self.session["title"] = f"Capture {self.session['captured_at'][:16].replace('T', ' ')}"
        (DATA_DIR / f"{self.session['id']}.json").write_text(json.dumps(self.session, indent=1))

    def _new_clicked(self):
        if self.capturing:
            self.toggle_capture()
        self.save_session()
        self.new_session()
        self.list.clear()
        self.title_edit.setText("")
        self.status("New capture started. Previous one is saved and can be combined at Export.")

    # ---- source & region ----------------------------------------------
    def _wants_remote(self) -> bool:
        return self.auto_box.isChecked() and session_is_wayland()

    def start_source(self, fresh=False):
        """fresh=True (the Share… button): always show the picker, never silently reuse the
        last choice — so you can pick the Claude window instead of the whole screen."""
        if self.source is not None:
            self.source.stop()
        remote = self._wants_remote()
        self._token_key = "rd_restore_token" if remote else "restore_token"
        token = None if fresh else self.cfg.get(self._token_key)
        self.source = make_source(token, remote=remote)
        if remote:
            self.status("Waiting for permission… pick the Claude window and allow remote interaction "
                        "(TExporT only sends scroll-wheel events).")
        else:
            self.status("Waiting for screen-share permission… pick the Claude window."
                        if session_is_wayland() else "Using the X11 screen.")
        self.source.start(self.sig_ready.emit, self.sig_error.emit)

    def _on_source_ready(self):
        if getattr(self.source, "restore_token", None):
            self.cfg[getattr(self, "_token_key", "restore_token")] = self.source.restore_token
            save_config(self.cfg)
        img = self.source.grab()
        saved = self.cfg.get("frame_size")
        if img is not None and saved and [img.width(), img.height()] != saved:
            self.region = None   # different window / monitor size than last time
        kind = {1: "whole screen", 2: "one window"}.get(getattr(self.source, "source_type", None), "")
        log("source ready:", self.source.name, kind)
        self.status(f"Sharing {kind or 'via ' + self.source.name}. Now set the region.")
        if self.region is None:
            self.pick_region()
        else:
            self.frame_size = img.size() if img is not None else None
            self.status(f"Sharing via {self.source.name}. Region kept from last time — "
                        "Region… to change it, ● Capture to start.")
        if self._pending_auto and self.region is not None:
            self._pending_auto = False
            self._auto_begin()

    def _on_source_error(self, msg):
        self.source = None
        self._pending_auto = False
        if getattr(self, "_token_key", "") == "rd_restore_token":
            self.cfg.pop("rd_restore_token", None)    # force a fresh dialog next time
            save_config(self.cfg)
        log("source error:", msg)
        self.status(f"Could not start capture: {msg}", warn=True)

    def pick_region(self):
        self._warned_cut = False
        if self.source is None:
            self.status("Click Share… first.", warn=True)
            return
        img = self.source.grab()
        if img is None:
            self.status("No frame yet — try again in a second.", warn=True)
            return
        dlg = RegionDialog(img, self.region, self)
        if dlg.exec() and dlg.region() is not None:
            self.region = dlg.region()
            self.frame_size = img.size()
            r = self.region
            self.cfg["region"] = [r.x(), r.y(), r.width(), r.height()]
            self.cfg["frame_size"] = [img.width(), img.height()]
            save_config(self.cfg)
            self.status("Region set. Press ● Capture, then scroll the conversation slowly "
                        "(about half a screen per second).")

    # ---- capture loop --------------------------------------------------
    def toggle_capture(self):
        if self.auto is not None:
            if self.auto.get("phase") == "paused":
                self._auto_resume()
            else:
                self._auto_stop("Stopped.")
            return
        if not self.capturing and self.auto_box.isChecked():
            self._auto_begin()
            return
        if not self.capturing:
            if self.source is None:
                self.status("Click Share… first.", warn=True)
                return
            if self.region is None:
                self.pick_region()
                if self.region is None:
                    return
            self.capturing = True
            self.last_hash = None
            self.timer.start()
            self.b_rec.setText("■ Stop")
            self.status("Capturing — scroll the conversation now. Top to bottom works best.")
        else:
            self.capturing = False
            self.timer.stop()
            self.b_rec.setText("● Capture")
            self.save_session()
            self.status(f"Stopped. {len(self.session['lines'])} lines captured and saved.")
        self.b_rec.setProperty("on", "true" if self.capturing else "false")
        self.b_rec.style().unpolish(self.b_rec)
        self.b_rec.style().polish(self.b_rec)

    def _tick(self):
        if self.busy or not self.capturing or self.source is None:
            return
        img = self.source.grab()
        if img is None:
            return
        if self.frame_size is not None and img.size() != self.frame_size:
            self.toggle_capture()
            self.status("The shared window changed size — set the Region again.", warn=True)
            self.region = None
            return
        crop = img.copy(self.region)
        thumb = crop.scaledToWidth(120, Qt.TransformationMode.FastTransformation) \
                    .convertToFormat(QImage.Format.Format_Grayscale8)
        h = hashlib.md5(image_bytes(thumb)).hexdigest()
        if h == self.last_hash:
            return
        self.last_hash = h
        self._submit_ocr(crop)

    def _frame_crop(self):  # noqa: D401
        """Current region crop and its hash, or (None, None)."""
        img = self.source.grab() if self.source is not None else None
        if img is None or self.region is None:
            return None, None
        if self.frame_size is not None and img.size() != self.frame_size:
            return "resized", None
        rect = self._read_rect if (self.auto is not None and getattr(self, "_read_rect", None)) else self.region
        crop = img.copy(rect)
        thumb = crop.scaledToWidth(120, Qt.TransformationMode.FastTransformation) \
                    .convertToFormat(QImage.Format.Format_Grayscale8)
        return crop, bytes(image_bytes(thumb))

    def _submit_ocr(self, crop):
        scale = int(self.cfg.get("ocr_scale", 1))
        factor = float(scale)
        if scale == 1 and getattr(self, "_line_px", 0) > 20:
            # big text (HiDPI / large fonts): read it smaller — faster, same accuracy (tested to 16px glyphs)
            factor = max(0.5, 17.0 / self._line_px)
        if factor != 1.0:
            crop = crop.scaled(max(1, int(crop.width() * factor)), max(1, int(crop.height() * factor)),
                               Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._ocr_factor = factor
        self._ocr_w = crop.width()
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        crop.save(buf, "PNG")
        png = qbytes(buf.data())
        self.busy = True
        dpi = max(70, int(144 * factor))

        def work():
            try:
                lines, height = ocr_png(png, dpi=dpi)
                self.sig_ocr.emit(lines, height, "")
            except OcrError as e:
                self.sig_ocr.emit([], 0, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _show_new_lines(self, res):
        if not res.added:
            return
        new = self.stitcher.lines[-res.added:]
        self.list.blockSignals(True)
        for l in new:
            d = {"text": l.text, "par_break": l.par_break, "gap": bool(l.meta.get("gap"))}
            self.session["lines"].append(d)
            it = QListWidgetItem(l.text)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.restyle()
        self.list.scrollToBottom()

    def _on_ocr(self, lines, height, err):
        self.busy = False
        if err:
            if self.auto is not None:
                self._auto_stop(f"OCR error: {err}", warn=True)
                return
            self.status(f"OCR error: {err}", warn=True)
            return
        if not self.capturing:
            return
        clean = clean_frame_lines(lines, height)
        if clean:
            hs = sorted(l.height for l in clean)
            self._line_px = hs[len(hs) // 2] / getattr(self, "_ocr_factor", 1.0)
            w = getattr(self, "_ocr_w", 0)
            cut = sum(1 for l in clean if w and l.right >= w - 8)
            early = self.auto is None or self.auto.setdefault("frames", 0) < 3
            if self.auto is not None:
                self.auto["frames"] += 1
            if early and cut >= max(3, len(clean) // 4) and not getattr(self, "_warned_cut", False):
                self._warned_cut = True
                log("ocr: text touches the region's right edge on", cut, "of", len(clean), "lines")
                if self.auto is not None:
                    self._auto_stop("Lines run past the right edge of the Region, so they're being cut off. "
                                    "Click Region… and draw it over the whole conversation column "
                                    "(include the right-aligned messages), then ● Capture again.", warn=True)
                    return
                self.status("⚠ Lines run past the right edge of the Region — redraw it wider.", warn=True)
        if self.auto is not None:
            self._auto_result(clean)
            return
        res = self.stitcher.feed(clean)
        self._show_new_lines(res)
        msg = {"new": f"+{res.added} lines ({len(self.session['lines'])} total). Keep scrolling.",
               "same": f"{len(self.session['lines'])} lines — waiting for you to scroll…",
               "behind": "That part is already captured — scroll down past it.",
               "empty": "No text found in the region.",
               "gap": "⚠ Scrolled too fast — a gap was recorded. Scroll back up a little to fill it."}[res.status]
        self.status(msg, warn=res.status == "gap")

    # ---- auto-scroll ---------------------------------------------------
    def _auto_begin(self):
        log("auto: begin; source=", type(self.source).__name__ if self.source else None,
            "can_scroll=", getattr(self.source, "can_scroll", None), "region=", self.region)
        if self.source is None or (session_is_wayland() and not is_hyprland()
                                   and not getattr(self.source, "can_scroll", False)):
            self._pending_auto = True       # (re)share with scroll permission, then continue
            self.start_source()
            return
        if not getattr(self.source, "can_scroll", False):
            if is_hyprland():
                self.status("Auto-scroll on Hyprland needs ydotool: sudo pacman -S ydotool, then "
                            "systemctl --user enable --now ydotool", warn=True)
            else:
                self.status("Auto-scroll on X11 needs xdotool: sudo apt install xdotool", warn=True)
            return
        if self.region is None:
            self.pick_region()
            if self.region is None:
                return
        self.capturing = True
        self.auto = {"ctl": AutoScroller(steps=int(self.cfg.get("autoscroll_notches", 2))),
                     "phase": "countdown", "count": 2, "prev": None, "stable": 0, "t0": time.time(),
                     "moved": True, "scroll_hash": None, "pre": 0, "retries": 0}
        for w in self._lockables:
            w.setEnabled(False)
        self.b_rec.setText("■ Stop")
        self.b_rec.setProperty("on", "true")
        self.b_rec.style().unpolish(self.b_rec)
        self.b_rec.style().polish(self.b_rec)
        self._shrink_panel()
        a = self.auto
        a["phase"] = "await_hover"
        self.status("Move the pointer onto the conversation to start auto-scroll…")
        a["click_deadline"] = time.time() + 20
        QTimer.singleShot(20500, self._auto_click_timeout)
        if not self.underMouse():                        # pointer already outside (e.g. keyboard start)
            QTimer.singleShot(300, self._auto_hover_check)

    def enterEvent(self, e):
        a = self.auto
        if a is not None and a.get("phase") in ("settle", "moving", "idle"):
            a["hover_pause"] = True
            self.status("Paused — pointer is on TExporT. Move it back onto the conversation to continue.")
        super().enterEvent(e)

    def leaveEvent(self, e):
        a = self.auto
        if a is not None:
            a["exit_global"] = QCursor.pos()        # just past the panel's edge
            if a.get("phase") == "await_hover":
                QTimer.singleShot(300, self._auto_hover_check)
            elif a.get("hover_pause"):
                a["hover_pause"] = False
                a.update(t0=time.time())
                self.status("Auto-scrolling… ■ Stop or Esc to end (hovering TExporT pauses).")
        super().leaveEvent(e)

    def _auto_hover_check(self):
        a = self.auto
        if a is not None and a.get("phase") == "await_hover" and not self.underMouse():
            a["phase"] = "starting"
            self._auto_go()

    def _fit_over_text(self):
        """Cover the conversation text (the Region) but stop short of one edge — the side the
        pointer left from — leaving a strip where the pointer rests on the window below."""
        a = self.auto
        reg = self._region_on_screen()
        c = a.get("exit_global") or QCursor.pos()
        if reg.contains(c):        # strip on the region edge nearest the pointer, wide enough to hold it
            d = {"left": c.x() - reg.left(), "right": reg.right() - c.x(),
                 "top": c.y() - reg.top(), "bottom": reg.bottom() - c.y()}
            side = min(d, key=d.get)
            sw = max(56, d[side] + 40)
        else:
            side, sw = "right", 56
        x, y, w, h = reg.x(), reg.y(), reg.width(), reg.height()
        if side == "left":
            x, w = x + sw, w - sw
        elif side == "right":
            w -= sw
        elif side == "top":
            y, h = y + sw, h - sw
        else:
            h -= sw
        self.list.show()
        for b in self._mark_buttons:
            b.show()
        self.setGeometry(QRect(x, y, max(w, self.minimumSizeHint().width()), max(h, self.minimumSizeHint().height())))
        self.raise_()
        log("auto: panel fitted over the text, strip on the", side, "side:", self.geometry())
        return side

    def _on_app_state(self, state):
        a = self.auto
        if a is None or a.get("phase") != "await_click":
            return
        if state != Qt.ApplicationState.ApplicationActive:
            a["phase"] = "starting"                      # the user clicked another window
            a["target"] = QCursor.pos()                  # ...here: that becomes the locked target
            QTimer.singleShot(250, self._auto_go)

    def _auto_click_timeout(self):
        a = self.auto
        if a is not None and a.get("phase") == "await_hover" and time.time() >= a["click_deadline"]:
            self._auto_stop("Didn't see the pointer leave the panel — press ● Capture again, then move "
                            "the pointer onto the conversation.", warn=True)

    def _region_on_screen(self):
        """The capture region in desktop coordinates (only meaningful for whole-screen shares)."""
        scr = self.screen() or QGuiApplication.primaryScreen()
        sg = scr.geometry()
        img = self.source.grab() if self.source is not None else None
        k = (img.width() / sg.width()) if img is not None and sg.width() else 1.0
        r = self.region
        return QRect(int(r.x() / k) + sg.x(), int(r.y() / k) + sg.y(), int(r.width() / k), int(r.height() / k))

    def _to_stream(self, p: QPoint):
        """Desktop point -> shared-stream coordinates (whole-screen shares only)."""
        scr = self.screen() or QGuiApplication.primaryScreen()
        sg = scr.geometry()
        img = self.source.grab() if self.source is not None else None
        k = (img.width() / sg.width()) if img is not None and sg.width() else 1.0
        return (p.x() - sg.x()) * k, (p.y() - sg.y()) * k

    def _read_band(self, g=None):
        """The part of the region to read. On whole-screen shares TExporT's own panel is in the
        picture, so read the taller full-width part of the region above or below it.
        Returns None if the panel leaves too little to read."""
        r = self.region
        if getattr(self.source, "source_type", 2) != 1:
            return r
        scr = self.screen() or QGuiApplication.primaryScreen()
        sg = scr.geometry()
        img = self.source.grab()
        k = (img.width() / sg.width()) if img is not None and sg.width() else 1.0
        g = g or self.frameGeometry()
        pr = QRect(int((g.x() - sg.x()) * k), int((g.y() - sg.y()) * k), int(g.width() * k), int(g.height() * k))
        if not pr.intersects(r):
            return r
        # only full-width bands: a band beside the panel would cut every line short
        cands = [QRect(r.x(), r.y(), r.width(), pr.top() - r.top() - 4),                  # above
                 QRect(r.x(), pr.bottom() + 4, r.width(), r.bottom() - pr.bottom() - 4)]  # below
        best = max(cands, key=lambda c: c.height())
        return best if best.height() >= r.height() * 0.35 else None

    def _auto_go(self):
        a = self.auto
        if a is None:
            return
        whole = getattr(self.source, "source_type", 2) == 1
        if whole:
            side = self._fit_over_text()                 # panel over the text, strip left for the pointer
        else:
            side = None
            self._unshrink_panel()                       # window share: desktop position unknown
        self._read_rect = self._read_band(self.frameGeometry() if whole else None)
        a["blink"] = self._read_rect is None
        if a["blink"]:
            # whole-screen share with the panel over the text: the panel vanishes for a split
            # second at each look, so only the conversation is read
            self._read_rect = self.region
            log("auto: whole screen with the panel over the text -> blink reads")
        elif self._read_rect != self.region:
            log("auto: reading band", self._read_rect, "to avoid the panel")
        a.update(phase="settle", t0=time.time(), stable=0, prev=None, moved=True, hover_pause=False)
        log("auto: scrolling starts; strip side", side)
        self.status("Auto-scrolling… keep the pointer on the conversation. ■ Stop or Esc to end "
                    "(hovering TExporT pauses).")
        self.auto_timer.start()

    def _blink_hide(self, hide):
        """Take the panel out of the picture for one frame. Hyprland ignores X11 window
        opacity, so there the window is unmapped and mapped again instead."""
        if is_hyprland():
            if hide:
                self.hide()
            else:
                self.show()
                self.raise_()
        else:
            self.setWindowOpacity(0.0 if hide else 1.0)

    def _blink_grab(self):
        """Whole-screen share with the panel on top: hide the panel for one fresh frame, take it,
        show the panel again. Returns (crop, view) or None while waiting for the frame."""
        a = self.auto
        b = a.get("blink_t")
        if b is None:
            if time.time() - a.get("shown_at", 0) < 0.25:
                return None                          # stay visible most of the time
            self._blink_hide(True)
            a["blink_t"] = time.time()
            a["blink_seq"] = getattr(self.source, "frame_seq", 0)
            QTimer.singleShot(60, self._auto_tick)
            return None
        seq = getattr(self.source, "frame_seq", None)
        waited = time.time() - b
        if seq is not None and seq < a["blink_seq"] + 2 and waited < (0.6 if is_hyprland() else 0.25):
            QTimer.singleShot(20, self._auto_tick)
            return None
        if seq is None and waited < 0.08:
            return None
        res = self._frame_crop()
        self._blink_hide(False)
        a["blink_t"] = None
        a["shown_at"] = time.time()
        if res[0] is not None and res[0] != "resized":
            a["last_h"] = res[1]
        return res

    def _view_now(self):
        """The current page view for movement checks (the last clean look in blink mode)."""
        a = self.auto
        if a is not None and a.get("blink"):
            return a.get("last_h")
        return self._frame_crop()[1]

    def _auto_tick(self):
        a = self.auto
        if a is None or a["phase"] != "settle":
            return
        if a.get("hover_pause"):
            return
        if a.get("blink"):
            got = self._blink_grab()
            if got is None:
                return
            crop, h = got
        else:
            crop, h = self._frame_crop()
        if crop is None:
            return
        if crop == "resized":
            self._auto_stop("The shared window changed size — set the Region again.", warn=True)
            self.region = None
            return
        if not a["moved"]:
            if not same_view(h, a["scroll_hash"]):
                a.update(moved=True, prev=h, stable=0)          # the page started moving
            elif time.time() - a["t0"] > 1.2 and not self.busy:
                a["phase"] = "idle"
                self._auto_decide(None)                          # the page didn't move
            return
        a["stable"] = a["stable"] + 1 if same_view(h, a["prev"]) else 0
        a["prev"] = h
        if a["stable"] < 1 or self.busy:
            return              # still animating, or the previous screen is still being read
        self._submit_ocr(crop)
        a["pre"] = a["ctl"].steps
        self._auto_move(a["pre"], h)                             # scroll ahead while this screen is read

    def _auto_result(self, clean):
        a = self.auto
        ctl = a["ctl"]
        if a.get("phase") == "paused":                   # keep what was read, don't scroll
            res = self.stitcher.feed(clean)
            self._show_new_lines(res)
            return
        if self.stitcher.lines and a["retries"] < 2:
            status, _ = self.stitcher.classify(clean)
            if status == "gap":
                a["retries"] += 1
                ctl.backed_off()
                back = -(a.get("pre", 0) + 1)
                log("auto: no overlap; backing up", back, "notches (retry", a["retries"], ") steps now", ctl.steps)
                self.status("Missed some text — backing up to re-read it…")
                self._auto_move(back, self._view_now())
                return
        a["retries"] = 0
        res = self.stitcher.feed(clean)
        self._show_new_lines(res)
        self._auto_decide(res.status, res.added, len(clean), already_scrolled=True)

    def _auto_decide(self, status, added=0, frame_lines=0, already_scrolled=False):
        a = self.auto
        if a is None:
            return
        ctl = a["ctl"]
        d = ctl.after_frame(status, added, frame_lines)
        n = len(self.session["lines"])
        log("auto:", "status=", status, "added=", added, "frame_lines=", frame_lines, "->", d.action, d.steps, d.reason)
        if d.action == "stop" and d.reason == "no_scroll" and not a.get("switched") \
                and hasattr(self.source, "scroll_method"):
            a["switched"] = True                                 # try the other scroll event type once
            self.source.scroll_method = "smooth" if self.source.scroll_method == "discrete" else "discrete"
            log("auto: no movement; switching scroll method to", self.source.scroll_method)
            a["ctl"] = ctl = AutoScroller(steps=ctl.steps)
            d = ctl.first()
        if d.action == "stop":
            if d.reason == "no_scroll":
                self._auto_stop("Scrolling isn't reaching the window (tried both scroll methods). "
                                "Press ● Capture again and click directly on the conversation text. "
                                f"Details: {LOG}", warn=True)
            else:
                gaps = f" ⚠ {ctl.gaps} gap(s) recorded." if ctl.gaps else ""
                self._auto_stop(f"Reached the end — {n} lines captured.{gaps}", warn=bool(ctl.gaps))
            return
        self.status(f"Auto-scrolling… {n} lines ({d.steps} notch{'es' if d.steps > 1 else ''}/step). "
                    "Hovering TExporT pauses; ■ Stop or Esc ends.")
        if not already_scrolled:
            self._auto_move(d.steps, self._view_now())

    def _auto_move(self, steps, scroll_hash=None):
        """Scroll whatever window the pointer is resting on (the conversation)."""
        a = self.auto
        if a is None:
            return
        a["phase"] = "moving"

        def do_scroll():
            if self.auto is not a or a.get("phase") == "paused":
                return
            if a.get("hover_pause") or self.underMouse():
                QTimer.singleShot(100, do_scroll)        # pointer is on TExporT: wait
                return
            try:
                px = self.region.height() * 0.25 * abs(steps) / 2 if self.region is not None else 0.0
                if getattr(self.source, "scroll_method", "discrete") == "smooth":
                    self.source.scroll(steps, pixels=px)
                else:
                    self.source.scroll(steps)
            except Exception as e:  # noqa: BLE001
                self._auto_stop(f"Scrolling failed: {e}", warn=True)
                return
            a.update(phase="settle", t0=time.time(), stable=0, prev=None, moved=False, scroll_hash=scroll_hash)
        do_scroll()

    def _auto_pause(self):
        """Pause without ending the run (▶ Resume continues)."""
        a = self.auto
        if a is None or a.get("phase") == "paused":
            return
        self.auto_timer.stop()
        a["phase"] = "paused"
        self.auto_box.setEnabled(True)
        self.b_rec.setText("▶ Resume")
        n = len(self.session["lines"])
        log("auto: paused by ctrl+click at", n, "lines")
        self.status(f"Unlocked and paused — {n} lines. ▶ Resume continues at the same spot; "
                    "Esc or untick Auto-scroll to finish.")

    def _auto_resume(self):
        a = self.auto
        if a is None or a.get("phase") != "paused":
            return
        self.auto_box.setEnabled(False)
        self.b_rec.setText("■ Stop")
        a.update(phase="settle", t0=time.time(), stable=0, prev=None, moved=True, blink_t=None)
        log("auto: resumed")
        self.status("Auto-scrolling… keep the pointer on the conversation. ■ Stop or Esc to end.")
        self.auto_timer.start()

    def _aim(self):
        """Put the pointer on the locked target for the scroll (Ubuntu delivers wheel events
        to whatever is under the pointer)."""
        a = self.auto
        if a is None or not a.get("aim") or not hasattr(self.source, "point_at"):
            return
        try:
            self.source.point_at(*a["aim"])
        except Exception:  # noqa: BLE001
            pass

    def _give_pointer_back(self, back: QPoint):
        """Return the pointer to where the user had it (whole-screen shares, where desktop
        positions are known)."""
        a = self.auto
        if a is None or getattr(self.source, "source_type", 2) != 1 or not hasattr(self.source, "point_at"):
            return
        t = a.get("target")
        if t is not None and abs(back.x() - t.x()) + abs(back.y() - t.y()) < 6:
            return
        bx, by = self._to_stream(back)
        QTimer.singleShot(45, lambda: self.auto is not None and self.source.point_at(bx, by))

    # shrink in place while waiting for the click; restore exactly when it lands
    def _shrink_panel(self):
        self._saved_geo = self.geometry()
        self._saved_alpha = self.alpha
        self.list.hide()
        for b in self._mark_buttons:
            b.hide()
        self.resize(self.width(), self.minimumSizeHint().height())
        self.move(self._saved_geo.topLeft())

    def _unshrink_panel(self):
        """Back to the exact size and place it had when ● Capture was pressed (still running)."""
        if getattr(self, "_saved_geo", None) is None:
            return
        self.list.show()
        for b in self._mark_buttons:
            b.show()
        self.setGeometry(self._saved_geo)
        self.raise_()
        self.update()

    def _restore_panel(self, ghost=False):
        """Back to the original size and place. ghost=True: click-through and see-through while
        auto-scroll runs, so scroll-wheel events reach the conversation underneath."""
        if getattr(self, "_saved_geo", None) is None:
            return
        self.list.show()
        for b in self._mark_buttons:
            b.show()
        self.setGeometry(self._saved_geo)
        self.update()

    def _end_panel_mode(self):
        self._read_rect = None
        self.setWindowOpacity(1.0)
        if getattr(self, "_saved_geo", None) is None:
            return
        self.alpha = self._saved_alpha
        self.list.show()
        for b in self._mark_buttons:
            b.show()
        self.setGeometry(self._saved_geo)
        self._saved_geo = None
        self.raise_()
        self.update()

    def _auto_stop(self, msg, warn=False):
        self.auto_timer.stop()
        self._end_panel_mode()
        self.auto = None
        self.capturing = False
        for w in self._lockables:
            w.setEnabled(True)
        self.b_rec.setText("● Capture")
        self.b_rec.setProperty("on", "false")
        self.b_rec.style().unpolish(self.b_rec)
        self.b_rec.style().polish(self.b_rec)
        self.save_session()
        self.status(msg, warn=warn)

    # ---- marking -------------------------------------------------------
    def _rows(self) -> list[int]:
        return sorted(self.list.row(i) for i in self.list.selectedItems())

    def _para_bounds(self, row: int) -> tuple[int, int]:
        L = self.session["lines"]
        a = row
        while a > 0 and not L[a].get("par_break") and not L[a].get("gap"):
            a -= 1
        b = row
        while b + 1 < len(L) and not L[b + 1].get("par_break") and not L[b + 1].get("gap"):
            b += 1
        return a, b

    def _add_marker(self, a: int, b: int):
        notes = []
        keep = []
        for m in self.session["markers"]:
            if m["end"] < a or m["start"] > b:
                keep.append(m)
            else:                       # overlapping: merge into the new one
                a, b = min(a, m["start"]), max(b, m["end"])
                if m.get("note"):
                    notes.append(m["note"])
        keep.append({"start": a, "end": b, "note": " | ".join(notes)})
        keep.sort(key=lambda m: m["start"])
        self.session["markers"] = keep

    @staticmethod
    def _ranges(rows: list[int]) -> list[tuple[int, int]]:
        out = []
        for r in rows:
            if out and r == out[-1][1] + 1:
                out[-1] = (out[-1][0], r)
            else:
                out.append((r, r))
        return out

    def mark_selection(self):
        rows = self._rows()
        if not rows:
            self.status("Select one or more lines first.", warn=True)
            return
        for a, b in self._ranges(rows):
            self._add_marker(a, b)
        self._after_mark_change()

    def mark_paragraph(self):
        rows = self._rows()
        if not rows:
            self.status("Select a line in the paragraph first.", warn=True)
            return
        a, _ = self._para_bounds(rows[0])
        _, b = self._para_bounds(rows[-1])
        self._add_marker(a, b)
        self._after_mark_change()

    def _marker_at(self, row):
        return next((m for m in self.session["markers"] if m["start"] <= row <= m["end"]), None)

    def note(self):
        rows = self._rows()
        m = self._marker_at(rows[0]) if rows else None
        if m is None:
            self.status("Select a marked line to add a note.", warn=True)
            return
        text, ok = QInputDialog.getText(self, "Marker note", "Why this matters:",
                                        QLineEdit.EchoMode.Normal, m.get("note", ""))
        if ok:
            m["note"] = text.strip()
            self._after_mark_change()

    def unmark(self):
        rows = set(self._rows())
        before = len(self.session["markers"])
        self.session["markers"] = [m for m in self.session["markers"]
                                   if not any(m["start"] <= r <= m["end"] for r in rows)]
        if len(self.session["markers"]) != before:
            self._after_mark_change()

    def delete_lines(self):
        rows = set(self._rows())
        if not rows:
            return
        L = self.session["lines"]
        mapping, j = {}, 0
        for i in range(len(L)):
            if i not in rows:
                mapping[i] = j
                j += 1
        new_markers = []
        for m in self.session["markers"]:
            kept = [mapping[i] for i in range(m["start"], m["end"] + 1) if i in mapping]
            if kept:
                new_markers.append({**m, "start": kept[0], "end": kept[-1]})
        for r in sorted(rows, reverse=True):
            L.pop(r)
            self.list.takeItem(r)
        self.session["markers"] = new_markers
        # keep the stitcher's tail in sync with what is shown
        self.stitcher.lines = [ln for k, ln in enumerate(self.stitcher.lines) if k not in rows]
        self.stitcher._norm = [x for k, x in enumerate(self.stitcher._norm) if k not in rows]
        self._after_mark_change()

    def _item_edited(self, item):
        row = self.list.row(item)
        if 0 <= row < len(self.session["lines"]):
            self.session["lines"][row]["text"] = item.text()

    def _after_mark_change(self):
        self.restyle()
        self.save_session()
        n = len(self.session["markers"])
        self.status(f"{n} marker{'s' if n != 1 else ''} in this capture.")

    def restyle(self):
        L = self.session["lines"]
        marked = {}
        for k, m in enumerate(self.session["markers"]):
            for r in range(m["start"], m["end"] + 1):
                marked[r] = (k + 1, m.get("note", ""))
        para = 0
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            if i > 0 and (L[i].get("par_break") or L[i].get("gap")):
                para += 1
            if i in marked:
                it.setBackground(QBrush(QColor(245, 158, 11, 200)))
                it.setForeground(QBrush(QColor("#111")))
                n, note = marked[i]
                it.setToolTip(f"Marker {n}" + (f": {note}" if note else ""))
            else:
                it.setBackground(QBrush(QColor(255, 255, 255, 18) if para % 2 else QColor(0, 0, 0, 0)))
                it.setForeground(QBrush(QColor("#fca5a5") if L[i].get("gap") else QColor("#f4f4f5")))
                it.setToolTip("")
        self.list.blockSignals(False)

    # ---- export / close ------------------------------------------------
    def export(self):
        self.save_session()
        if not list_sessions():
            self.status("Nothing captured yet.", warn=True)
            return
        ExportDialog(self.session["id"], self.cfg, self).exec()

    def start_pipeline(self, sessions, out_dir, fmt, units, intent, run):
        self.status(f"{'Running' if run else 'Preparing'} the NYCH → mg8-engine handoff…")

        def work():
            try:
                res = nych_pipeline.prepare(self.cfg, sessions, out_dir, fmt=fmt, granularity=units,
                                            intent=intent, run=run)
                self.sig_pipeline.emit(res, "")
            except Exception as e:  # noqa: BLE001
                self.sig_pipeline.emit(None, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_pipeline(self, res, err):
        if err:
            self.status("NYCH / mg8-engine step failed — see message.", warn=True)
            QMessageBox.warning(self, "NYCH / mg8-engine", f"{err}\n\nCheck the connection with ⚙ on the panel.")
            return
        lines = []
        for u in res["units"]:
            line = f"• {u['unit']}: {u['needs_mapping']} words to map" if res["format"] == "nych" else f"• {u['unit']}"
            if "run" in u:
                line += f" — run: {u['run']['result']}"
                if u["run"].get("reason"):
                    line += f" ({u['run']['reason'][:160]})"
            lines.append(line)
        root = Path(res["units"][0]["dir"]).parent if res["units"] else None
        self.status(f"Handoff ready: {len(res['units'])} unit(s), {res['format']} format.")
        box = QMessageBox(self)
        box.setWindowTitle("NYCH / mg8-engine handoff")
        box.setText(f"{len(res['units'])} unit(s) written ({res['format']} format) to:\n{root}\n\n"
                    + "\n".join(lines) + f"\n\nNext: {res['next_step']}")
        open_btn = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn and root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def about(self):
        box = QMessageBox(self)
        box.setWindowTitle("About TExporT")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<h3>TExporT {__version__}</h3>"
            "<p>Scroll-capture text from any window, mark what matters, and export a handoff "
            "document that tells the next AI exactly what it must not lose.</p>"
            f"<p><b>MG8-ready.</b> Exports can include an <a href='{MG8_URL}'>MG8</a> execution unit "
            "(.mg8 · .gst · .g8son · .ork · .gitson). <i>Verify…</i> checks an AI's answer with "
            "deterministic gates and writes an auditable .qson trace.</p>"
            "<p><b>Works with NYCH + mg8-engine.</b> With your nych encoder and mg8-engine installed "
            "(⚙), exports can produce the .gst / .ork / .bonit handoff for the LLM step in NYCH "
            "(symbolic) or text format, or run the whole pipeline.</p>"
            "<p>© 2026 American Milestone Inc. All rights reserved.<br>"
            "Third-party components: see THIRD_PARTY_NOTICES.</p>")
        box.exec()

    def closeEvent(self, e):
        self.timer.stop()
        self.save_session()
        self.cfg["alpha"] = self.alpha
        self.cfg["size"] = [self.width(), self.height()]
        save_config(self.cfg)
        if self.source is not None:
            self.source.stop()
        super().closeEvent(e)


def hyprland_window_rules():
    """Hyprland tiles new windows; make TExporT's panel and dialogs float, and keep the panel
    pinned above other windows on every workspace. Runtime-only rules (nothing is written to
    your config). Tries the current `windowrule` syntax, then the older `windowrulev2`."""
    import subprocess
    titles = ["TExporT", "Drag a box around the conversation text", "Export marked document",
              "Verify an AI answer.*", "NYCH / mg8-engine.*", "About TExporT"]
    rules = [f"float,title:^({t})$" for t in titles] + ["pin,title:^(TExporT)$"]
    for kw in ("windowrule", "windowrulev2"):
        try:
            out = subprocess.run(["hyprctl", "--batch", " ; ".join(f"keyword {kw} {r}" for r in rules)],
                                 capture_output=True, text=True, timeout=5).stdout
        except Exception as e:  # noqa: BLE001
            log("hyprland rules failed:", e)
            return
        log("hyprland rules", kw, out.strip().replace("\n", " ")[:200])
        if "invalid" not in out.lower() and "error" not in out.lower():
            return


def main(argv=None):
    import os
    import sys
    argv = sys.argv if argv is None else argv
    # On GNOME Wayland only X11 (XWayland) windows may ask to stay on top, so run the
    # panel through XWayland unless told otherwise. Capture still uses the portal.
    if "--native-wayland" not in argv and os.environ.get("DISPLAY") and session_is_wayland():
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    if is_hyprland():
        hyprland_window_rules()
    app = QApplication(argv)
    app.setApplicationName("texport")
    app.setApplicationDisplayName("TExporT")
    app.setDesktopFileName("texport")
    migrate_legacy()
    w = Panel()
    w.show()
    return app.exec()
