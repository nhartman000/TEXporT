import importlib.util
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

assert importlib.util.find_spec('texport.workspace_dialog'), 'Discoverable ICM GUI missing'
from PySide6.QtTest import QTest
from texport.qt import QApplication, QPushButton, Qt, QMimeData, QUrl, QPoint, QPointF, QDragEnterEvent, QDropEvent
from texport import app, packed, icm
from test_icm import SESSIONS
from test_packed import unit, gate

root = Path(sys.argv[1])
qapp = QApplication([])
with patch.object(app, 'list_sessions', return_value=SESSIONS):
    export_dialog = app.ExportDialog('capture-1', {})
    export_dialog.show()
    btn = export_dialog.findChild(QPushButton, 'icmWorkspaceButton')
    assert btn is not None, 'Actual Export entry not wired'
    # Non-modal open so both real entry and interactions can be exercised.
    QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    dialog = export_dialog.icm_dialog
    assert dialog.isVisible() and dialog.sessions == SESSIONS
    assert not dialog.save_workspace_btn.isEnabled()
    QTest.mouseClick(dialog.preview_btn, Qt.MouseButton.LeftButton)
    assert 'sources/compiled-markers.txt' in dialog.preview.toPlainText()
    dialog.approve.setChecked(True)
    assert dialog.save_workspace_btn.isEnabled()
    direct_path = root/'direct.zip'
    with patch.object(dialog, 'choose_save', return_value=str(direct_path)):
        QTest.mouseClick(dialog.save_workspace_btn, Qt.MouseButton.LeftButton)
    assert zipfile.is_zipfile(direct_path)
    QTest.mouseClick(dialog.prompt_btn, Qt.MouseButton.LeftButton)
    assert 'UNTRUSTED' in qapp.clipboard().text()
    proposal = icm.direct_plan(SESSIONS, 'AI Reviewed')
    proposal['groups'][0]['conditions'] = [{'text': 'Verify budget', 'marker_ids': ['M2']}]
    dialog.organize_editor.setPlainText(packed.dump(proposal).decode())
    assert not dialog.save_workspace_btn.isEnabled(), 'Editing must invalidate prior approval'
    QTest.mouseClick(dialog.preview_btn, Qt.MouseButton.LeftButton)
    dialog.approve.setChecked(True)
    ai_path = root/'ai.zip'
    with patch.object(dialog, 'choose_save', return_value=str(ai_path)):
        QTest.mouseClick(dialog.save_workspace_btn, Qt.MouseButton.LeftButton)
    with zipfile.ZipFile(ai_path) as z:
        assert packed.read(z.read('themes/uncategorized/unit.mg8'))['container']['kind'] == 'unit'
    dialog.organize_editor.setPlainText('{}')
    QTest.mouseClick(dialog.preview_btn, Qt.MouseButton.LeftButton)
    assert not dialog.save_workspace_btn.isEnabled() and 'Invalid' in dialog.status.text()
    QTest.mouseClick(dialog.direct_btn, Qt.MouseButton.LeftButton)
    assert icm.validate_plan(SESSIONS, dialog.organize_editor.toPlainText())['groups'][0]['conditions'] == []

    dialog.tabs.setCurrentIndex(1)
    source = root/'original.mg8'; source.write_bytes(unit())
    with patch.object(dialog, 'choose_unit', return_value=str(source)):
        QTest.mouseClick(dialog.load_unit_btn, Qt.MouseButton.LeftButton)
    assert 'gates/first.g8son' in dialog.unit_target.toPlainText()
    assert 'Check source' in dialog.unit_target.toPlainText(), 'Review must expose actual imported conditions'
    gates = []
    for i in (2, 3):
        p = root/f'new{i}.g8son'; p.write_bytes(b' '+packed.dump(gate(f'f{i}', f'g{i}'))); gates.append(p)
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(p)) for p in gates])
    # Real Qt move-only events must never import or negotiate MOVE.
    from PySide6.QtGui import QDragMoveEvent
    before_move = dialog.unit_blob
    for event_class, point, method in [
        (QDropEvent, QPointF(10, 10), dialog.unit_target.dropEvent),
        (QDragEnterEvent, QPoint(10, 10), dialog.unit_target.dragEnterEvent),
        (QDragMoveEvent, QPoint(10, 10), dialog.unit_target.dragMoveEvent),
    ]:
        move = event_class(point, Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        method(move)
        assert not move.isAccepted(), 'Move-only event must be rejected'
        assert dialog.unit_blob == before_move, 'Rejected move must not stage an import'
        mixed = event_class(point, Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        mixed.setDropAction(Qt.DropAction.MoveAction)
        if event_class is not QDropEvent:
            method(mixed)
            assert mixed.isAccepted() and mixed.dropAction() == Qt.DropAction.CopyAction
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(dialog.unit_target.viewport(), enter)
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    drop.setDropAction(Qt.DropAction.MoveAction)
    QApplication.sendEvent(dialog.unit_target.viewport(), drop)
    assert drop.isAccepted() and drop.dropAction() == Qt.DropAction.CopyAction
    assert 'g1 → g2 → g3' in dialog.unit_target.toPlainText()
    assert not dialog.save_unit_btn.isEnabled()
    dialog.unit_approve.setChecked(True)
    edited = root/'edited.mg8'
    with patch.object(dialog, 'choose_save', return_value=str(edited)):
        QTest.mouseClick(dialog.save_unit_btn, Qt.MouseButton.LeftButton)
    result = packed.read(edited.read_bytes())
    assert result['members']['gates/new2.g8son'] == gates[0].read_bytes()
    assert packed.read(source.read_bytes())['manifest']['gates'] == ['gates/first.g8son']
    empty_mime = QMimeData()
    empty_drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, empty_mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    dialog.unit_target.dropEvent(empty_drop)
    assert 'Empty' in dialog.status.text() and not empty_drop.isAccepted()
    dialog.unit_approve.setChecked(True)
    with patch.object(dialog, 'choose_save', return_value=str(source)):
        QTest.mouseClick(dialog.save_unit_btn, Qt.MouseButton.LeftButton)
    assert 'exists' in dialog.status.text().lower(), 'Original overwrite must be refused'

    dialog.tabs.setCurrentIndex(2)
    inputs = []
    for i in range(4):
        p = root/f'p{i}.mg8'; p.write_bytes(unit(f'unit{i}')); inputs.append(str(p))
    with patch.object(dialog, 'choose_units', return_value=inputs):
        QTest.mouseClick(dialog.add_units_btn, Qt.MouseButton.LeftButton)
    dialog.package_list.setCurrentRow(3)
    QTest.mouseClick(dialog.up_btn, Qt.MouseButton.LeftButton)
    assert dialog.package_list.item(2).text().endswith('p3.mg8')
    QTest.mouseClick(dialog.preview_package_btn, Qt.MouseButton.LeftButton)
    assert 'max_steps: 4' in dialog.package_preview.toPlainText()
    assert dialog.grab().save(str(root/'workspace-ui.png'))
    dialog.package_approve.setChecked(True)
    target = root/'four.mg8pk'
    with patch.object(dialog, 'choose_save', return_value=str(target)):
        QTest.mouseClick(dialog.save_package_btn, Qt.MouseButton.LeftButton)
    package = packed.read(target.read_bytes())
    assert package['manifest']['units'] == ['units/001.mg8', 'units/002.mg8', 'units/003.mg8', 'units/004.mg8']
    assert packed.read(package['members']['units/003.mg8'])['manifest']['unit_id'] == 'unit3'
    QTest.mouseClick(dialog.down_btn, Qt.MouseButton.LeftButton)
    assert not dialog.save_package_btn.isEnabled()
    remove_btn = next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Remove selected')
    QTest.mouseClick(remove_btn, Qt.MouseButton.LeftButton)
    assert dialog.package_list.count() == 3
    dialog.close(); export_dialog.close()
qapp.processEvents()
qapp.quit()
print('UI interactions complete; exiting 0')
