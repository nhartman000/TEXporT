"""Qt binding shim. Prefers PySide6 (LGPL — allows a proprietary app);
falls back to PyQt6 (GPL) for local development only."""
try:
    from PySide6 import QtCore, QtGui, QtWidgets
    Signal = QtCore.Signal
    API = "PySide6"
except ImportError:  # pragma: no cover
    from PyQt6 import QtCore, QtGui, QtWidgets
    Signal = QtCore.pyqtSignal
    API = "PyQt6"

for _mod in (QtCore, QtGui, QtWidgets):
    for _name in dir(_mod):
        if _name.startswith("Q") or _name == "Qt":
            globals()[_name] = getattr(_mod, _name)


def image_bytes(img) -> bytes:
    bits = img.constBits()
    if hasattr(bits, "setsize"):          # PyQt6 sip.voidptr
        bits.setsize(img.sizeInBytes())
    return bytes(bits)


def qbytes(qbytearray) -> bytes:
    data = qbytearray.data()
    return data if isinstance(data, bytes) else bytes(data)
