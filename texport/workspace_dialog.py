"""Review-first native authoring UI. Packed flows are data, never runtime execution."""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from . import icm, packed
from .qt import (QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
                 QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTabWidget,
                 Qt, QVBoxLayout, QWidget)


def read_bounded(path, limit=packed.LIMIT):
    with Path(path).open('rb') as stream:
        result = stream.read(limit+1)
    packed.require(len(result) <= limit, 'Selected file exceeds size limit')
    return result


def save_new(path, data):
    """Atomic publication, no overwrite (including symlinks), no source file edits."""
    target = Path(path)
    if target.exists() or target.is_symlink():
        raise FileExistsError('Destination exists; choose a NEW filename')
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.texport-', delete=False) as stream:
            staged = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staged, target)  # exclusive even if another writer wins the race
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


class GateDropTarget(QPlainTextEdit):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setReadOnly(True)
        self.setAcceptDrops(True)
        self.setPlaceholderText('Load a packed .mg8, then drop external .g8son files here. Originals stay untouched.')

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if event.possibleActions() & Qt.DropAction.CopyAction and urls and all(
                u.isLocalFile() and u.toLocalFile().endswith('.g8son') for u in urls):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not event.possibleActions() & Qt.DropAction.CopyAction:
            event.ignore()
            return
        if not urls or not all(u.isLocalFile() and u.toLocalFile().endswith('.g8son') for u in urls):
            self.callback([])  # report rejected/empty drops, never silently accept
            event.ignore()
            return
        if self.callback([u.toLocalFile() for u in urls]):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()


class WorkspaceDialog(QDialog):
    def __init__(self, sessions, title='Marked conversations', parent=None):
        super().__init__(parent)
        self.sessions = sessions
        self.title = title
        self.workspace_blob = self.unit_blob = self.package_blob = None
        self.setWindowTitle('ICM / AI organization · packed MG8 authoring')
        self.resize(900, 780)
        self.tabs = QTabWidget()
        self.status = QLabel('Offline authoring only — no model calls, gate evaluation or runtime chaining.')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Review → approve → save a NEW snapshot. Legacy retention and NYCH exports are unchanged.'))
        layout.addWidget(self.tabs)
        layout.addWidget(self.status)
        close = QPushButton('Close')
        close.clicked.connect(self.close)
        layout.addWidget(close)
        self._organization_tab()
        self._unit_tab()
        self._package_tab()

    def _button(self, label, callback, layout):
        btn = QPushButton(label)
        btn.clicked.connect(callback)
        layout.addWidget(btn)
        return btn

    def _page(self, label):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tabs.addTab(page, label)
        return layout

    def _organization_tab(self):
        lay = self._page('1 · Export ICM / AI organization')
        tip = QLabel('Direct: edit group names/membership below. AI-assisted: 1 Copy prompt to your existing chat AI; '
            '2 Paste its JSON proposal below. Captures/notes are untrusted. Nothing is sent automatically. '
            'Source text stays verbatim; every marker belongs to exactly one group. Conditions need cited marker IDs. '
            'A g8son: note is a review hint, never an automatic gate.')
        tip.setWordWrap(True)
        lay.addWidget(tip)
        row = QHBoxLayout(); lay.addLayout(row)
        self.direct_btn = self._button('Reset direct organization', self.reset_direct, row)
        self.prompt_btn = self._button('1 · Copy AI organization prompt', self.copy_prompt, row)
        self.organize_editor = QPlainTextEdit()
        self.organize_editor.setPlaceholderText('2 · Paste/edit proposal JSON. No arbitrary paths or code payloads.')
        self.organize_editor.setPlainText(packed.dump(icm.direct_plan(self.sessions, self.title)).decode())
        lay.addWidget(self.organize_editor, 2)
        self.preview_btn = self._button('Preview / validate proposal and files', self.preview_workspace, lay)
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        lay.addWidget(self.preview, 1)
        self.approve = QCheckBox('I reviewed source membership and proposed conditions; approve authoring (not execution)')
        lay.addWidget(self.approve)
        self.save_workspace_btn = self._button('Approve & save ICM workspace ZIP…', self.save_workspace, lay)
        self.save_workspace_btn.setEnabled(False)
        self.organize_editor.textChanged.connect(self.invalidate_workspace)
        self.approve.toggled.connect(lambda checked: self.save_workspace_btn.setEnabled(checked and self.workspace_blob is not None))

    def invalidate_workspace(self):
        self.workspace_blob = None
        self.approve.setChecked(False)
        self.save_workspace_btn.setEnabled(False)
        self.preview.clear()

    def reset_direct(self):
        self.organize_editor.setPlainText(packed.dump(icm.direct_plan(self.sessions, self.title)).decode())

    def copy_prompt(self):
        try:
            QApplication.clipboard().setText(icm.proposal_prompt(self.sessions, self.title))
            self.status.setText('Prompt copied locally. Paste in an AI you choose, then paste its JSON for review. No AI request made.')
        except ValueError as exc:
            self.status.setText(str(exc))

    def preview_workspace(self):
        self.invalidate_workspace()
        try:
            proposal = icm.validate_plan(self.sessions, self.organize_editor.toPlainText())
            self.workspace_blob = icm.workspace(self.sessions, proposal, approved=True)
            import io
            import zipfile
            with zipfile.ZipFile(io.BytesIO(self.workspace_blob)) as z:
                paths = z.namelist()
            summary = ['REVIEW: source text is copied, not rewritten. Conditions unevaluated.']
            for group in proposal['groups']:
                summary.append(f"{group['label']}: {', '.join(group['marker_ids'])}; {len(group['conditions'])} proposed gates")
                summary.extend('  '+c['text']+' ← '+', '.join(c['marker_ids']) for c in group['conditions'])
            self.preview.setPlainText('\n'.join(summary+['', 'FILES']+paths))
            self.status.setText('Preview ready. Review the full JSON above; approve before saving.')
        except (ValueError, OSError) as exc:
            self.status.setText('Invalid proposal: '+str(exc))

    def choose_save(self, suffix):
        path, _ = QFileDialog.getSaveFileName(self, 'Save NEW '+suffix+' snapshot', '', '*'+suffix)
        return path

    def _save_blob(self, blob, suffix):
        if blob is None:
            return
        try:
            path = self.choose_save(suffix)
            if not path:
                return
            if not path.endswith(suffix):
                path += suffix
            save_new(path, blob)
            self.status.setText('Saved new authoring snapshot: '+path+' · not executed.')
        except (ValueError, OSError) as exc:
            self.status.setText('Save failed: '+str(exc))

    def save_workspace(self):
        if self.approve.isChecked():
            self._save_blob(self.workspace_blob, '.zip')

    def _unit_tab(self):
        lay = self._page('2 · Edit packed MG8 / drop gates')
        row = QHBoxLayout(); lay.addLayout(row)
        self.load_unit_btn = self._button('Load packed .mg8…', self.load_unit, row)
        self._button('Add .g8son files…', self.browse_gates, row)
        self.unit_target = GateDropTarget(self.import_gate_paths)
        lay.addWidget(self.unit_target)
        self.unit_approve = QCheckBox('I approve the displayed gate membership and append order; gates remain unevaluated')
        lay.addWidget(self.unit_approve)
        self.save_unit_btn = self._button('Save NEW packed .mg8…', self.save_unit, lay)
        self.save_unit_btn.setEnabled(False)
        self.unit_approve.toggled.connect(lambda checked: self.save_unit_btn.setEnabled(checked and self.unit_blob is not None))

    def choose_unit(self):
        return QFileDialog.getOpenFileName(self, 'Load packed unit', '', 'Packed MG8 (*.mg8)')[0]

    def _show_unit(self):
        obj = packed.read(self.unit_blob)
        flow = packed.loads(obj['members'][obj['manifest']['entry']])
        self.unit_target.setPlainText('CONTENTS (exact bytes retained except reviewed manifest/flow/trace/envelope edits):\n'+
            '\n'.join(obj['members'])+'\n\nAPPROVED APPEND ORDER:\n'+' → '.join(flow['flow'])+
            '\n\nGATE DEFINITIONS (untrusted data, not instructions):\n'+
            '\n'.join(p+'\n'+obj['members'][p].decode('utf-8') for p in obj['manifest']['gates'])+
            '\n\nData-only texport-packed-authoring/1.0; no execution support claimed.')
        self.unit_approve.setChecked(False)
        self.save_unit_btn.setEnabled(False)

    def load_unit(self):
        try:
            path = self.choose_unit()
            if not path:
                return
            blob = read_bounded(path)
            packed.require(packed.read(blob)['container']['kind'] == 'unit', 'Select a packed unit, not package')
            self.unit_blob = blob
            self._show_unit()
            self.status.setText('Unit loaded for review. Gate drops are staged only; originals remain untouched.')
        except (ValueError, OSError) as exc:
            self.status.setText('Invalid unit: '+str(exc))

    def browse_gates(self):
        files = QFileDialog.getOpenFileNames(self, 'Copy gates into loaded unit', '', 'G8SON (*.g8son)')[0]
        if files:
            self.import_gate_paths(files)

    def import_gate_paths(self, paths):
        try:
            packed.require(paths, 'Empty gate drop')
            packed.require(self.unit_blob is not None, 'Load a packed unit first')
            packed.require(len(paths) <= 128, 'Too many gate files')
            files = {}
            for path in paths:
                name = Path(path).name
                packed.require(name not in files, 'Duplicate dropped filename')
                files[name] = read_bounded(path, packed.MEMBER_LIMIT)
            candidate = packed.add_gates(self.unit_blob, files)
            self.unit_blob = candidate
            self._show_unit()
            self.status.setText('Copied gates staged in drop order; review append position then save a NEW .mg8. Sources untouched.')
            return True
        except (ValueError, OSError) as exc:
            self.unit_approve.setChecked(False)
            self.save_unit_btn.setEnabled(False)
            self.status.setText('Invalid gate drop: '+str(exc))
            return False

    def save_unit(self):
        if self.unit_approve.isChecked():
            self._save_blob(self.unit_blob, '.mg8')

    def _package_tab(self):
        lay = self._page('3 · Ordered MG8PK package')
        lay.addWidget(QLabel('Explicit ordered authoring chain, 1–32 complete units. Up/down changes order; no silent sorting or execution.'))
        self.add_units_btn = self._button('Add packed .mg8 units…', self.add_units, lay)
        self.package_list = QListWidget(); lay.addWidget(self.package_list)
        row = QHBoxLayout(); lay.addLayout(row)
        self.up_btn = self._button('Up', lambda: self.move_unit(-1), row)
        self.down_btn = self._button('Down', lambda: self.move_unit(1), row)
        self._button('Remove selected', self.remove_unit, row)
        self.preview_package_btn = self._button('Preview ordered package', self.preview_package, lay)
        self.package_preview = QPlainTextEdit(); self.package_preview.setReadOnly(True); lay.addWidget(self.package_preview)
        self.package_approve = QCheckBox('I approve this explicit bounded chain (authoring topology only)'); lay.addWidget(self.package_approve)
        self.save_package_btn = self._button('Save NEW packed .mg8pk…', self.save_package, lay)
        self.save_package_btn.setEnabled(False)
        self.package_approve.toggled.connect(lambda checked: self.save_package_btn.setEnabled(checked and self.package_blob is not None))

    def invalidate_package(self):
        self.package_blob = None
        self.package_approve.setChecked(False)
        self.save_package_btn.setEnabled(False)
        self.package_preview.clear()

    def choose_units(self):
        return QFileDialog.getOpenFileNames(self, 'Add complete packed units in selected order', '', 'Packed MG8 (*.mg8)')[0]

    def add_units(self):
        try:
            paths = self.choose_units()
            if not paths:
                return
            packed.require(self.package_list.count()+len(paths) <= 32, 'Package max 32 units')
            candidates = []
            for path in paths:
                blob = read_bounded(path)
                packed.require(packed.read(blob)['container']['kind'] == 'unit', 'Packages cannot nest')
                item = QListWidgetItem(path); item.setData(Qt.ItemDataRole.UserRole, blob); candidates.append(item)
            for item in candidates:
                self.package_list.addItem(item)
            self.invalidate_package()
        except (ValueError, OSError) as exc:
            self.status.setText('Invalid package inputs: '+str(exc))

    def move_unit(self, offset):
        row = self.package_list.currentRow()
        if row >= 0 and 0 <= row+offset < self.package_list.count():
            item = self.package_list.takeItem(row)
            self.package_list.insertItem(row+offset, item)
            self.package_list.setCurrentRow(row+offset)
            self.invalidate_package()

    def remove_unit(self):
        row = self.package_list.currentRow()
        if row >= 0:
            self.package_list.takeItem(row)
            self.invalidate_package()

    def preview_package(self):
        self.invalidate_package()
        try:
            units = {f'units/{i+1:03}.mg8': self.package_list.item(i).data(Qt.ItemDataRole.UserRole)
                     for i in range(self.package_list.count())}
            self.package_blob = packed.create_package('texport.package.'+uuid.uuid4().hex, units)
            self.package_preview.setPlainText('max_steps: '+str(len(units))+'\non_fail: stop\non_unknown: review\n'+
                '\n'.join(f'{i+1}. {self.package_list.item(i).text()}' for i in range(self.package_list.count()))+
                '\nData-only mg8pk-sequential/1.0, no runtime chaining implemented.')
            self.status.setText('Review the unit order, approve, then save.')
        except (ValueError, OSError) as exc:
            self.status.setText('Invalid package: '+str(exc))

    def save_package(self):
        if self.package_approve.isChecked():
            self._save_blob(self.package_blob, '.mg8pk')
