"""Helpers for run_aec_python:  from aec import init_qgis, WORK"""
import os
import sys
from pathlib import Path

WORK = Path(os.environ.get("AEC_WORKSPACE", "/home/mcp/work"))
_QGS = None


def init_qgis():
    """Start QGIS headless once and enable Processing. Returns the QgsApplication.

    After this:  import processing; processing.run('native:buffer', {...})
    """
    global _QGS
    if _QGS is not None:
        return _QGS
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication

    prefix = os.environ.get("QGIS_PREFIX_PATH", sys.prefix)
    QgsApplication.setPrefixPath(prefix, True)
    _QGS = QgsApplication([], False)
    _QGS.initQgis()
    plugins = Path(prefix) / "share" / "qgis" / "python" / "plugins"
    if plugins.is_dir() and str(plugins) not in sys.path:
        sys.path.append(str(plugins))
    try:
        from processing.core.Processing import Processing
        from qgis.analysis import QgsNativeAlgorithms

        Processing.initialize()
        QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
    except Exception as exc:  # noqa: BLE001
        print(f"[aec] QGIS Processing not initialised: {exc}", file=sys.stderr)
    return _QGS


__all__ = ["WORK", "init_qgis"]
