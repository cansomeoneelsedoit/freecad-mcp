# FreeCAD start-up tweak for the BIZCAD live view: there is no window manager on the
# virtual screen, so make the main window fill it (and keep it filled), and open on
# the Part Design workbench with a 3D view ready.
import os

import FreeCADGui as Gui

try:
    from PySide6 import QtCore
except ImportError:  # older FreeCAD
    from PySide2 import QtCore

W, H = (int(v) for v in os.environ.get("BIZCAD_SCREEN", "1600x1000").split("x"))


def _fill():
    mw = Gui.getMainWindow()
    if mw is not None and (mw.geometry().width() != W or mw.geometry().height() != H):
        mw.setGeometry(0, 0, W, H)


def _start():
    _fill()
    try:
        Gui.activateWorkbench("PartDesignWorkbench")
    except Exception:
        pass
    timer = QtCore.QTimer(Gui.getMainWindow())
    timer.timeout.connect(_fill)
    timer.start(3000)


QtCore.QTimer.singleShot(2500, _start)
