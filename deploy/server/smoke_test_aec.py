"""End-to-end check of the combined stack (FreeCAD + ezdxf + IfcOpenShell + PDAL + QGIS).

    FREECAD_MCP_URL=https://cad.example.com/mcp FREECAD_MCP_TOKEN_FILE=~/token \
        python deploy/server/smoke_test_aec.py
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mcp import ClientSession

try:  # mcp 2.x
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    def connect(url, headers):
        return streamable_http_client(url, http_client=httpx2.AsyncClient(headers=headers, timeout=600))
except ImportError:  # mcp 1.x
    from mcp.client.streamable_http import streamablehttp_client

    def connect(url, headers):
        return streamablehttp_client(url, headers=headers, timeout=60, sse_read_timeout=600)

URL = os.environ.get("FREECAD_MCP_URL", "http://127.0.0.1:8796/mcp")
TOKEN = Path(os.path.expanduser(os.environ["FREECAD_MCP_TOKEN_FILE"])).read_text().strip()
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:300]}")


def text_of(res):
    return " ".join(getattr(c, "text", "") for c in res.content)


async def py(s, code, timeout=600):
    r = json.loads(text_of(await s.call_tool("run_aec_python", {"code": code, "timeout": timeout})))
    return r.get("exit_code") == 0, (r.get("stdout", "") + r.get("stderr", "")[-800:]).strip()


DXF = """
import ezdxf
doc = ezdxf.new('R2018', setup=True); doc.header['$INSUNITS'] = 4
msp = doc.modelspace(); doc.layers.add('OUTLINE', color=7)
msp.add_lwpolyline([(0,0),(80,0),(80,40),(0,40)], close=True, dxfattribs={'layer':'OUTLINE'})
msp.add_circle((20,20), 6, dxfattribs={'layer':'OUTLINE'})
import os; os.makedirs('jobs/smoke', exist_ok=True); doc.saveas('jobs/smoke/plate.dxf'); print('DXF OK')
"""

FREECAD = """
import os, ezdxf, FreeCAD, Part
W = os.environ.get('AEC_WORKSPACE', '/home/mcp/work')
d = ezdxf.readfile(W + '/jobs/smoke/plate.dxf'); msp = d.modelspace()
pl = msp.query('LWPOLYLINE')[0]; pts = [FreeCAD.Vector(x, y, 0) for x, y in pl.get_points('xy')]
outer = Part.Face(Part.makePolygon(pts + [pts[0]]))
c = msp.query('CIRCLE')[0]
hole = Part.Face(Part.Wire(Part.makeCircle(c.dxf.radius, FreeCAD.Vector(c.dxf.center.x, c.dxf.center.y, 0))))
solid = outer.cut(hole).extrude(FreeCAD.Vector(0, 0, 10))
doc = FreeCAD.newDocument('AecSmoke'); o = doc.addObject('Part::Feature', 'Plate'); o.Shape = solid; doc.recompute()
solid.exportStep(W + '/jobs/smoke/plate.step')
print('PLATE_VOLUME', round(solid.Volume, 1))
"""

IFC = """
import ifcopenshell, ifcopenshell.api as api
m = ifcopenshell.file(schema='IFC4')
proj = api.run('root.create_entity', m, ifc_class='IfcProject', name='Smoke')
api.run('unit.assign_unit', m)
ctx = api.run('context.add_context', m, context_type='Model')
body = api.run('context.add_context', m, context_type='Model', context_identifier='Body', target_view='MODEL_VIEW', parent=ctx)
site = api.run('root.create_entity', m, ifc_class='IfcSite', name='Site')
api.run('aggregate.assign_object', m, relating_object=proj, products=[site])
wall = api.run('root.create_entity', m, ifc_class='IfcWall', name='Wall A')
rep = api.run('geometry.add_wall_representation', m, context=body, length=5, height=3, thickness=0.2)
api.run('geometry.assign_representation', m, product=wall, representation=rep)
api.run('spatial.assign_container', m, relating_structure=site, products=[wall])
m.write('jobs/smoke/wall.ifc'); print('IFC walls', len(m.by_type('IfcWall')))
"""

PDAL = """
import json, pdal
p = pdal.Pipeline(json.dumps([
  {"type": "readers.faux", "count": 20000, "mode": "random", "bounds": "([0,100],[0,100],[0,5])"},
  {"type": "writers.las", "filename": "jobs/smoke/cloud.laz", "compression": "laszip"}]))
print('PDAL points', p.execute())
"""

QGIS = """
from aec import init_qgis
init_qgis()
from qgis.core import QgsCoordinateReferenceSystem as C, QgsCoordinateTransform as T, QgsProject, QgsPointXY
t = T(C('EPSG:4326'), C('EPSG:7854'), QgsProject.instance())
p = t.transform(QgsPointXY(138.6007, -34.9285))   # Adelaide GPO
print('MGA54', round(p.x()), round(p.y()))
import processing; print('ALGS', len(__import__('qgis.core', fromlist=['QgsApplication']).QgsApplication.processingRegistry().algorithms()))
"""


async def main():
    async with connect(URL, {"Authorization": f"Bearer {TOKEN}"}) as streams:
        async with ClientSession(streams[0], streams[1]) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            check("AEC tools listed", {"run_aec_python", "get_download_link", "aec_workflow_guide"} <= set(tools), f"{len(tools)} tools")
            info = text_of(await s.call_tool("aec_stack_info", {}))
            check("stack versions", "missing" not in info, info.replace("\n", " "))

            ok, out = await py(s, DXF); check("ezdxf drew plate.dxf", ok and "DXF OK" in out, out)
            ex = await s.call_tool("execute_code", {"code": FREECAD, "include_screenshot": False})
            out = text_of(ex)
            check("FreeCAD read DXF + extruded (80x40x10 minus hole)", "PLATE_VOLUME" in out, out)
            ok, out = await py(s, IFC); check("IfcOpenShell wrote wall.ifc", ok and "IFC walls 1" in out, out)
            r = json.loads(text_of(await s.call_tool("run_aec_command", {"program": "IfcConvert", "args": ["-y", "jobs/smoke/wall.ifc", "jobs/smoke/wall.glb"]})))
            check("IfcConvert IFC -> GLB", r.get("exit_code") == 0, r.get("stderr", "")[-300:] or r.get("stdout", "")[-300:])
            ok, out = await py(s, PDAL); check("PDAL wrote cloud.laz", ok and "PDAL points 20000" in out, out)
            r = json.loads(text_of(await s.call_tool("run_aec_command", {"program": "pdal", "args": ["info", "--summary", "jobs/smoke/cloud.laz"]})))
            check("pdal info", r.get("exit_code") == 0 and "20000" in r.get("stdout", ""), r.get("stderr", "")[-200:])
            ok, out = await py(s, QGIS); check("QGIS reprojects Adelaide to MGA54", ok and "MGA54 28" in out, out)

            files = text_of(await s.call_tool("list_files", {"folder": "jobs/smoke"}))
            check("files listed", all(n in files for n in ["plate.dxf", "plate.step", "wall.ifc", "wall.glb", "cloud.laz"]), files.replace("\n", " "))
            link = json.loads(text_of(await s.call_tool("get_download_link", {"path": "jobs/smoke/plate.step", "hours": 1})))
            url = link.get("url", "")
            if url.startswith("/"):
                url = URL.rsplit("/mcp", 1)[0] + url
            try:
                body = urllib.request.urlopen(url, timeout=30).read()
                check("download link works without token", body.startswith(b"ISO-10303-21"), f"{len(body)} bytes")
            except Exception as e:
                check("download link works without token", False, e)
            try:
                urllib.request.urlopen(url.replace("/plate.step", "/wall.ifc"), timeout=30)
                check("tampered link refused", False)
            except urllib.error.HTTPError as e:
                check("tampered link refused", e.code == 404)

            await s.call_tool("execute_code", {"code": "import FreeCAD\nFreeCAD.closeDocument('AecSmoke')", "include_screenshot": False})
    failed = [n for n, ok in results if not ok]
    print("ALL PASS" if not failed else f"FAILED: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
