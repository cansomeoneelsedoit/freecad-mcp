"""AEC / GIS / CAD stack tools (QGIS, PDAL, IfcOpenShell, ezdxf) next to FreeCAD.

Enabled only when the environment variable ``AEC_STACK=1`` is set (the server
container in ``deploy/server`` does this). Everything works on one shared
workspace folder that FreeCAD's ``execute_code`` can read and write too, so a
job can hop between tools: PDAL -> QGIS -> ezdxf -> FreeCAD -> IfcOpenShell.

Files reach the workspace through ``fetch_file`` (a URL) or ``upload_file``
(text / base64), and leave it through ``get_download_link`` (a signed,
time-limited HTTPS link served by this same server under ``/files/``) or
``read_file_text``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

logger = logging.getLogger("FreeCADMCPserver")

WORKSPACE = Path(os.environ.get("AEC_WORKSPACE", "/home/mcp/work")).resolve()
AEC_PREFIX = Path(os.environ.get("AEC_PREFIX", "/opt/aec"))
AEC_PYTHON = AEC_PREFIX / "bin" / "python"
HELPERS_DIR = os.environ.get("AEC_HELPERS", "/opt/aec-helpers")
MAX_FETCH_BYTES = int(os.environ.get("AEC_MAX_FETCH_MB", "1024")) * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_OUTPUT_CHARS = 60_000
ALLOWED_COMMANDS = {
    "pdal": "Point clouds: pdal info / translate / pipeline (LAS/LAZ, E57, PLY ...)",
    "qgis_process": "QGIS Processing algorithms headless (qgis_process run <alg> -- ...)",
    "IfcConvert": "IfcOpenShell: IFC -> OBJ / GLB / DAE / STEP / SVG plans / IFC subsets",
    "ogr2ogr": "GDAL vector conversion and reprojection (DXF, GeoJSON, SHP, GPKG, KML ...)",
    "ogrinfo": "GDAL vector inspection",
    "gdalinfo": "GDAL raster inspection",
    "gdal_translate": "GDAL raster conversion",
    "gdalwarp": "GDAL raster reprojection / clipping",
    "gdaldem": "GDAL DEM tools (hillshade, slope, contours via gdal_contour)",
    "gdal_contour": "GDAL contour lines from a DEM",
}

Seconds = Annotated[float, Field(gt=0, le=1800)]


# --------------------------------------------------------------------------- helpers

def _child_env() -> dict[str, str]:
    """Environment for child processes: no secrets, offscreen Qt for QGIS."""
    env = {
        k: v
        for k, v in os.environ.items()
        if "TOKEN" not in k.upper() and "SECRET" not in k.upper() and "KEY" not in k.upper()
    }
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONPATH": HELPERS_DIR,
            "PYTHONUNBUFFERED": "1",
            "AEC_WORKSPACE": str(WORKSPACE),
            "PATH": f"{AEC_PREFIX / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}",
            "PROJ_DATA": str(AEC_PREFIX / "share" / "proj"),
            "GDAL_DATA": str(AEC_PREFIX / "share" / "gdal"),
            "HOME": os.environ.get("HOME", "/home/mcp"),
        }
    )
    env.pop("PYTHONHOME", None)
    return env


def _safe_path(rel: str, must_exist: bool = False) -> Path:
    p = (WORKSPACE / rel.lstrip("/\\")).resolve()
    if p != WORKSPACE and WORKSPACE not in p.parents:
        raise ValueError("path must stay inside the workspace")
    if must_exist and not p.exists():
        raise FileNotFoundError(f"{rel} does not exist in the workspace")
    return p


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    half = MAX_OUTPUT_CHARS // 2
    return text[:half] + f"\n... [{len(text) - MAX_OUTPUT_CHARS} characters cut] ...\n" + text[-half:]


def _run(argv: list[str], timeout: float) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=WORKSPACE,
            env=_child_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True,
        )
        return {
            "exit_code": proc.returncode,
            "seconds": round(time.time() - started, 1),
            "stdout": _clip(proc.stdout),
            "stderr": _clip(proc.stderr),
        }
    except subprocess.TimeoutExpired as e:
        return {
            "exit_code": None,
            "timed_out": True,
            "seconds": round(time.time() - started, 1),
            "stdout": _clip((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")),
            "stderr": _clip((e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")),
        }


def _check_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("only http(s) URLs are allowed")
    for info in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("URLs pointing at private or internal addresses are not allowed")


def _sign(secret: bytes, rel: str, expires: int) -> str:
    return hmac.new(secret, f"{expires}:{rel}".encode(), hashlib.sha256).hexdigest()[:40]


# --------------------------------------------------------------------------- guide

GUIDE: dict[str, str] = {
    "overview": """\
ONE shared workspace folder (paths are relative to it) is used by every tool:
- FreeCAD (3D parametric CAD + BIM): execute_code / create_object / get_view ...
  Inside FreeCAD code the workspace is  os.environ.get('AEC_WORKSPACE','/home/mcp/work')
  and ezdxf + ifcopenshell can be imported there too.
- run_aec_python: a separate Python 3.12 with qgis.core (PyQGIS), pdal, ifcopenshell
  (+ ifcopenshell.util / api / ifctester / ifcdiff / ifcpatch / ifcclash where installed),
  ezdxf, gdal/ogr, shapely, numpy. `from aec import *` gives init_qgis(), WORK.
- run_aec_command: pdal, qgis_process, IfcConvert, GDAL CLIs (no shell).
Files in: fetch_file(url) or upload_file(...). Files out: get_download_link(path)
(give the user that link) or read_file_text for small text results (DXF, IFC, CSV, JSON).
Pick the lightest tool: plain 2D drawing -> ezdxf; BIM data -> ifcopenshell; solids,
booleans, STEP/STL, FEM -> FreeCAD; coordinates, reprojection, maps -> QGIS/GDAL;
LiDAR -> PDAL. Keep heavy scans small (crop/decimate with PDAL first).""",
    "dxf_to_3d": """\
DXF outline -> 3D part (ezdxf + FreeCAD)
1. run_aec_python: read the DXF with ezdxf, list layers/entities, check units ($INSUNITS).
2. FreeCAD execute_code: import the DXF (importDXF.insert(path, doc.Name)) or rebuild the
   closed polyline as a Part.Wire -> Face -> extrude(FreeCAD.Vector(0,0,h)).
3. Booleans/fillets in FreeCAD, then export: Part.export / Mesh export -> STEP / STL.
4. get_view for a picture, get_download_link for the files.""",
    "3d_to_drawings": """\
3D model -> 2D drawings (FreeCAD + ezdxf)
1. FreeCAD: section/projection with TechDraw or Shape.slice / Drawing.projectEx.
2. Write the edges to DXF with ezdxf on named layers (OUTLINE, HIDDEN, DIMS, TEXT),
   add dimensions (msp.add_linear_dim) and a title block; doc.saveas('out/plan.dxf').
3. Optional: render a PNG/PDF with ezdxf.addons.drawing for a quick preview.""",
    "pointcloud_to_site": """\
LiDAR / point cloud -> site model (PDAL + QGIS/GDAL + FreeCAD/IFC)
1. run_aec_command pdal info --summary scan.laz   (CRS, bounds, point count)
2. PDAL pipeline JSON (write with upload_file or run_aec_python): readers.las ->
   filters.crop / filters.outlier / filters.smrf (ground) / filters.range Classification[2:2]
   -> writers.gdal (DTM GeoTIFF, resolution 0.25-1 m) and/or writers.las (cleaned).
3. gdal_contour / gdaldem hillshade; QGIS (init_qgis + processing) for reprojection,
   clipping to a site boundary, area/volume stats.
4. FreeCAD: build a terrain mesh/solid from the DTM grid; or IfcOpenShell: create an
   IfcSite with the terrain geometry and georeferencing (IfcMapConversion).""",
    "ifc_bim": """\
IFC / BIM (IfcOpenShell, FreeCAD BIM)
- Inspect: ifcopenshell.open(p); f.by_type('IfcWall'); ifcopenshell.util.element.get_psets(e)
- Quantities/schedules -> CSV/XLSX; ifcopenshell.util.selector.filter_elements(f, 'IfcDoor')
- Author/modify: ifcopenshell.api.run('root.create_entity', f, ifc_class='IfcWall') ...
- Validate: ifctester against an IDS file -> HTML/JSON report
- Compare versions: ifcdiff old.ifc new.ifc -> JSON of changed elements
- Convert: run_aec_command IfcConvert in.ifc out.glb  (or .obj/.dae/.stp/.svg plans)
- FreeCAD: import IFC through the BIM workbench (importIFC.insert) to edit as solids.""",
    "gis_context": """\
GIS context and coordinates (QGIS / GDAL)
- from aec import init_qgis; qgs = init_qgis(); then processing.run('native:...', {...})
  or qgis_process list / qgis_process run native:reprojectlayer -- INPUT=... TARGET_CRS=EPSG:7854
- Australian grids: GDA2020 MGA zones EPSG:7849-7859 (Adelaide = MGA54, EPSG:7854).
- DXF <-> GIS: ogr2ogr -f GPKG site.gpkg plan.dxf -a_srs EPSG:7854; and back with -f DXF.
- CityJSON <-> IFC (ifccityjson) bridges city models and buildings when installed.""",
}


# --------------------------------------------------------------------------- tools

def register(mcp: Any) -> None:
    """Register the AEC tools on *mcp* (FastMCP / MCPServer)."""
    import functools

    import anyio

    WORKSPACE.mkdir(parents=True, exist_ok=True)

    class _Registrar:
        """Register a sync tool, run in a worker thread so long jobs never block the server."""

        def tool(self, **_: Any):
            def deco(fn):
                @functools.wraps(fn)
                async def runner(*args: Any, **kwargs: Any) -> Any:
                    return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

                mcp.tool(structured_output=False)(runner)
                return fn

            return deco

    reg = _Registrar()

    @reg.tool()
    def aec_workflow_guide(topic: str = "overview") -> str:
        """How to combine FreeCAD, ezdxf, PDAL, QGIS/GDAL and IfcOpenShell on one job.

        Call this first for anything involving drawings, point clouds, maps or BIM.
        Topics: overview, dxf_to_3d, 3d_to_drawings, pointcloud_to_site, ifc_bim, gis_context.
        """
        if topic not in GUIDE:
            return f"Unknown topic. Choose one of: {', '.join(GUIDE)}\n\n" + GUIDE["overview"]
        return GUIDE[topic]

    @reg.tool()
    def aec_stack_info() -> str:
        """Versions of every tool in the stack and the workspace location."""
        code = (
            "import json, importlib\n"
            "out = {}\n"
            "for m in ['ezdxf','ifcopenshell','pdal','osgeo.gdal','shapely','numpy']:\n"
            "    try:\n"
            "        mod = importlib.import_module(m); out[m] = getattr(mod, '__version__', None) or getattr(mod, 'version', lambda: '?')()\n"
            "    except Exception as e: out[m] = f'missing ({e.__class__.__name__})'\n"
            "try:\n"
            "    from qgis.core import Qgis; out['qgis'] = Qgis.version()\n"
            "except Exception as e: out['qgis'] = f'missing ({e})'\n"
            "print(json.dumps(out))\n"
        )
        r = _run([str(AEC_PYTHON), "-c", code], 120)
        info = {"workspace": str(WORKSPACE), "libraries": r["stdout"].strip() or r["stderr"][-500:]}
        info["commands"] = {c: bool(shutil.which(c, path=_child_env()["PATH"])) for c in ALLOWED_COMMANDS}
        return json.dumps(info, indent=2)

    @reg.tool()
    def run_aec_python(code: str, timeout: Seconds = 300) -> str:
        """Run Python 3.12 with PyQGIS (qgis.core + processing), pdal, ifcopenshell, ezdxf,
        GDAL/OGR, shapely and numpy, in the shared workspace folder (the working directory).

        Use print() for results. `from aec import init_qgis, WORK` starts QGIS headless.
        Separate process per call: nothing is kept between calls except files you save.
        For FreeCAD geometry use execute_code instead (same workspace folder).
        """
        script = WORKSPACE / f".aec_run_{os.getpid()}_{time.time_ns()}.py"
        script.write_text(code, encoding="utf-8")
        try:
            r = _run([str(AEC_PYTHON), str(script)], timeout)
        finally:
            script.unlink(missing_ok=True)
        return json.dumps(r, indent=1)

    @reg.tool()
    def run_aec_command(program: str, args: list[str], timeout: Seconds = 300) -> str:
        """Run one command-line tool (no shell) in the workspace folder.

        program is one of: pdal, qgis_process, IfcConvert, ogr2ogr, ogrinfo, gdalinfo,
        gdal_translate, gdalwarp, gdaldem, gdal_contour. args is the argument list.
        """
        if program not in ALLOWED_COMMANDS:
            return json.dumps({"error": f"program must be one of {sorted(ALLOWED_COMMANDS)}"})
        exe = shutil.which(program, path=_child_env()["PATH"])
        if not exe:
            return json.dumps({"error": f"{program} is not installed in this container"})
        return json.dumps(_run([exe, *[str(a) for a in args]], timeout), indent=1)

    @reg.tool()
    def fetch_file(url: str, filename: str = "", folder: str = "inputs") -> str:
        """Download a file from a public http(s) URL into the workspace (default folder inputs/).

        Use this for files the user shares as links (DXF, IFC, LAS/LAZ, GeoTIFF, shapefile zip ...).
        """
        try:
            _check_public_url(url)
            name = filename or Path(urllib.parse.urlparse(url).path).name or "download.bin"
            dest = _safe_path(f"{folder}/{name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": "aec-mcp/1.0"})
            total = 0
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
                _check_public_url(resp.geturl())
                while chunk := resp.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_FETCH_BYTES:
                        raise ValueError(f"file is larger than {MAX_FETCH_BYTES // 1048576} MB")
                    out.write(chunk)
            return json.dumps({"saved": str(dest.relative_to(WORKSPACE)), "bytes": total})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)})

    @reg.tool()
    def upload_file(path: str, text: str = "", base64_data: str = "") -> str:
        """Write a file into the workspace from text (DXF, IFC, JSON pipelines, CSV, scripts)
        or from base64 (binary), max 25 MB. path is relative to the workspace."""
        try:
            dest = _safe_path(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = base64.b64decode(base64_data) if base64_data else text.encode("utf-8")
            if len(data) > MAX_UPLOAD_BYTES:
                return json.dumps({"error": "larger than 25 MB; share a link and use fetch_file"})
            dest.write_bytes(data)
            return json.dumps({"saved": str(dest.relative_to(WORKSPACE)), "bytes": len(data)})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)})

    @reg.tool()
    def list_files(folder: str = "", pattern: str = "*") -> str:
        """List files in the workspace (recursively) with sizes and modified times."""
        try:
            base = _safe_path(folder, must_exist=True)
            rows = []
            for p in sorted(base.rglob(pattern)):
                if p.is_file() and not p.name.startswith(".aec_run"):
                    st = p.stat()
                    rows.append(
                        {
                            "path": str(p.relative_to(WORKSPACE)),
                            "bytes": st.st_size,
                            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                        }
                    )
                if len(rows) >= 500:
                    break
            return json.dumps(rows, indent=1)
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)})

    @reg.tool()
    def read_file_text(path: str, max_chars: int = 50_000, offset: int = 0) -> str:
        """Read a text file from the workspace (DXF, IFC, CSV, JSON, logs). Use offset to page."""
        try:
            p = _safe_path(path, must_exist=True)
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0)
                data = f.read(offset + max(1, min(max_chars, 200_000)))
            return data[offset:]
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)})

    @reg.tool()
    def get_download_link(path: str, hours: int = 24) -> str:
        """Make a time-limited HTTPS download link for a workspace file, to give to the user."""
        try:
            p = _safe_path(path, must_exist=True)
            if not p.is_file():
                return json.dumps({"error": "not a file"})
            rel = str(p.relative_to(WORKSPACE))
            expires = int(time.time()) + max(1, min(hours, 168)) * 3600
            secret = _download_secret()
            if not secret:
                return json.dumps({"error": "download links are not configured on this server"})
            host = os.environ.get("PUBLIC_HOST", "")
            base = f"https://{host}" if host else ""
            url = f"{base}/files/{expires}/{_sign(secret, rel, expires)}/{urllib.parse.quote(rel)}"
            return json.dumps({"url": url, "expires_utc": time.strftime('%Y-%m-%d %H:%M', time.gmtime(expires)), "bytes": p.stat().st_size})
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)})


_SECRET: bytes | None = None


def _download_secret() -> bytes | None:
    return _SECRET


# --------------------------------------------------------------------------- downloads

class SignedDownloadMiddleware:
    """Serves GET /files/<expires>/<sig>/<path> without the bearer token; the
    HMAC signature (keyed from the listen token) is the authorisation."""

    def __init__(self, app: Any, token: str) -> None:
        global _SECRET
        self.app = app
        _SECRET = hashlib.sha256(b"aec-download-v1:" + token.encode()).digest()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/files/"):
            await self.app(scope, receive, send)
            return
        status, headers, body_path = 404, [(b"content-type", b"text/plain")], None
        try:
            _, _, expires_s, sig, rel = scope["path"].split("/", 4)
            rel = urllib.parse.unquote(rel)
            expires = int(expires_s)
            good = hmac.compare_digest(sig, _sign(_SECRET or b"", rel, expires))
            if scope["method"] in ("GET", "HEAD") and good and expires > time.time():
                p = _safe_path(rel, must_exist=True)
                if p.is_file():
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    status, body_path = 200, p
                    headers = [
                        (b"content-type", ctype.encode()),
                        (b"content-length", str(p.stat().st_size).encode()),
                        (b"content-disposition", f'attachment; filename="{p.name}"'.encode()),
                        (b"cache-control", b"no-store"),
                    ]
        except Exception:  # noqa: BLE001
            pass
        await send({"type": "http.response.start", "status": status, "headers": headers})
        if body_path is None or scope["method"] == "HEAD":
            await send({"type": "http.response.body", "body": b"" if body_path else b"Not found"})
            return
        with open(body_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b""})


def enabled() -> bool:
    return os.environ.get("AEC_STACK") == "1"
