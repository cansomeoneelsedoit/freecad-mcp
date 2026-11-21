"""Make showcase pictures through a live BIZCAD server (one per tool combination).

    FREECAD_MCP_URL=... FREECAD_MCP_TOKEN_FILE=... python deploy/server/showcase.py OUTDIR
"""
import asyncio
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

from mcp import ClientSession

try:
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    def connect(url, headers):
        return streamable_http_client(url, http_client=httpx2.AsyncClient(headers=headers, timeout=900))
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client

    def connect(url, headers):
        return streamablehttp_client(url, headers=headers, timeout=60, sse_read_timeout=900)

URL = os.environ["FREECAD_MCP_URL"]
TOKEN = Path(os.environ["FREECAD_MCP_TOKEN_FILE"]).read_text().strip()
OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)


def txt(r):
    return " ".join(getattr(c, "text", "") for c in r.content)


async def py(s, code, label):
    r = json.loads(txt(await s.call_tool("run_aec_python", {"code": code, "timeout": 900})))
    print(f"[{label}] exit={r.get('exit_code')} {r.get('stdout','')[-300:]} {r.get('stderr','')[-400:] if r.get('exit_code') else ''}")
    return r


async def fc(s, code, label):
    out = txt(await s.call_tool("execute_code", {"code": code, "include_screenshot": False}))
    print(f"[{label}] {out[-300:]}")
    return out


async def view(s, name, view_name="Isometric"):
    r = await s.call_tool("get_view", {"view_name": view_name})
    imgs = [c for c in r.content if getattr(c, "type", "") == "image"]
    if imgs:
        (OUT / name).write_bytes(base64.b64decode(imgs[0].data))
        print(f"saved {name}")


async def fetch(s, rel, name):
    link = json.loads(txt(await s.call_tool("get_download_link", {"path": rel, "hours": 2})))
    url = link["url"] if link.get("url", "").startswith("http") else URL.rsplit("/mcp", 1)[0] + link["url"]
    (OUT / name).write_bytes(urllib.request.urlopen(url, timeout=60).read())
    print(f"saved {name}")


BRACKET = r"""
import FreeCAD, Part, os
W = os.environ.get('AEC_WORKSPACE', '/home/mcp/work'); os.makedirs(W + '/showcase', exist_ok=True)
for d in list(FreeCAD.listDocuments()): FreeCAD.closeDocument(d)
doc = FreeCAD.newDocument('Bracket')
base = Part.makeBox(100, 60, 8)
wall = Part.makeBox(100, 8, 70)
rib = Part.makePolygon([FreeCAD.Vector(46,8,8), FreeCAD.Vector(54,8,8), FreeCAD.Vector(54,8,8)])
gus = Part.Face(Part.makePolygon([FreeCAD.Vector(0,8,8), FreeCAD.Vector(0,50,8), FreeCAD.Vector(0,8,55), FreeCAD.Vector(0,8,8)])).extrude(FreeCAD.Vector(6,0,0))
g1 = gus.copy(); g1.translate(FreeCAD.Vector(12,0,0)); g2 = gus.copy(); g2.translate(FreeCAD.Vector(82,0,0))
s = base.fuse(wall).fuse(g1).fuse(g2)
for x in (25, 75):
    s = s.cut(Part.makeCylinder(5.5, 20, FreeCAD.Vector(x, 38, -5)))
    s = s.cut(Part.makeCylinder(5.5, 20, FreeCAD.Vector(x, -5, 45), FreeCAD.Vector(0,1,0)))
s = s.removeSplitter()
o = doc.addObject('Part::Feature', 'Bracket'); o.Shape = s; doc.recompute()
o.ViewObject.ShapeColor = (0.80, 0.45, 0.20)
s.exportStep(W + '/showcase/bracket.step')
import FreeCADGui as Gui
Gui.activeDocument().activeView().viewIsometric(); Gui.SendMsgToActiveView('ViewFit')
print('BRACKET volume', round(s.Volume))
"""

PLAN = r"""
import ezdxf, os
from ezdxf.addons.drawing import matplotlib as mpl_draw
os.makedirs('showcase', exist_ok=True)
doc = ezdxf.new('R2018', setup=True); doc.header['$INSUNITS'] = 6  # metres
for n, c in [('WALLS', 7), ('DOORS', 1), ('DIMS', 3), ('TEXT', 5)]: doc.layers.add(n, color=c)
m = doc.modelspace()
def wall(pts): m.add_lwpolyline(pts, close=True, dxfattribs={'layer': 'WALLS', 'const_width': 0.2})
wall([(0,0),(12,0),(12,8),(0,8)])
m.add_line((7,0),(7,8), dxfattribs={'layer':'WALLS'})
m.add_line((7,4.5),(12,4.5), dxfattribs={'layer':'WALLS'})
m.add_arc((7,1), 0.9, 0, 90, dxfattribs={'layer':'DOORS'})
m.add_arc((9,4.5), 0.9, 270, 360, dxfattribs={'layer':'DOORS'})
for t, p in [('LOUNGE 56 m2', (3.5,4)), ('BED 1', (9.5,6.3)), ('BATH', (9.5,2.2))]:
    m.add_text(t, height=0.35, dxfattribs={'layer':'TEXT'}).set_placement(p, align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
m.add_linear_dim(base=(0,-1.2), p1=(0,0), p2=(12,0), dimstyle='EZDXF', dxfattribs={'layer':'DIMS'}).render()
m.add_linear_dim(base=(-1.2,0), p1=(0,0), p2=(0,8), angle=90, dimstyle='EZDXF', dxfattribs={'layer':'DIMS'}).render()
doc.saveas('showcase/floorplan.dxf')
mpl_draw.qsave(m, 'showcase/floorplan.png', bg='#FFFFFF', dpi=160)
print('PLAN ok')
"""

IFC = r"""
import ifcopenshell, ifcopenshell.api as api, os
m = ifcopenshell.file(schema='IFC4')
proj = api.run('root.create_entity', m, ifc_class='IfcProject', name='Demo house')
api.run('unit.assign_unit', m)
ctx = api.run('context.add_context', m, context_type='Model')
body = api.run('context.add_context', m, context_type='Model', context_identifier='Body', target_view='MODEL_VIEW', parent=ctx)
site = api.run('root.create_entity', m, ifc_class='IfcSite', name='Site')
bldg = api.run('root.create_entity', m, ifc_class='IfcBuilding', name='House')
storey = api.run('root.create_entity', m, ifc_class='IfcBuildingStorey', name='Ground')
api.run('aggregate.assign_object', m, relating_object=proj, products=[site])
api.run('aggregate.assign_object', m, relating_object=site, products=[bldg])
api.run('aggregate.assign_object', m, relating_object=bldg, products=[storey])
import numpy as np
def place(el, x, y, rot):
    c, s = np.cos(np.radians(rot)), np.sin(np.radians(rot))
    mat = np.array([[c,-s,0,x],[s,c,0,y],[0,0,1,0],[0,0,0,1]], dtype=float)
    api.run('geometry.edit_object_placement', m, product=el, matrix=mat)
walls = [(0,0,0,12),(12,0,90,8),(12,8,180,12),(0,8,270,8),(7,0,90,8)]
for i,(x,y,r,L) in enumerate(walls):
    w = api.run('root.create_entity', m, ifc_class='IfcWall', name=f'Wall {i+1}')
    rep = api.run('geometry.add_wall_representation', m, context=body, length=L, height=2.7, thickness=0.2)
    api.run('geometry.assign_representation', m, product=w, representation=rep)
    place(w, x, y, r)
    api.run('spatial.assign_container', m, relating_structure=storey, products=[w])
    pset = api.run('pset.add_pset', m, product=w, name='Pset_WallCommon')
    api.run('pset.edit_pset', m, pset=pset, properties={'IsExternal': i < 4, 'FireRating': 'FRL 60/60/60' if i < 4 else '-/-/-'})
slab = api.run('root.create_entity', m, ifc_class='IfcSlab', name='Ground slab')
rep = api.run('geometry.add_slab_representation', m, context=body, depth=0.25)
api.run('geometry.assign_representation', m, product=slab, representation=rep)
api.run('spatial.assign_container', m, relating_structure=storey, products=[slab])
os.makedirs('showcase', exist_ok=True); m.write('showcase/house.ifc')
import ifcopenshell.util.element as ue, csv
with open('showcase/wall_schedule.csv','w',newline='') as f:
    wr = csv.writer(f); wr.writerow(['Name','External','FireRating'])
    for w in m.by_type('IfcWall'):
        p = ue.get_psets(w).get('Pset_WallCommon', {}); wr.writerow([w.Name, p.get('IsExternal'), p.get('FireRating')])
print('IFC walls', len(m.by_type('IfcWall')))
"""

IFC_IN_FREECAD = r"""
import FreeCAD, os, importlib
W = os.environ.get('AEC_WORKSPACE', '/home/mcp/work')
for d in list(FreeCAD.listDocuments()): FreeCAD.closeDocument(d)
doc = FreeCAD.newDocument('House')
try:
    import importers.importIFC as imp
except Exception:
    import importIFC as imp
imp.insert(W + '/showcase/house.ifc', doc.Name)
doc.recompute()
import FreeCADGui as Gui
Gui.activeDocument().activeView().viewIsometric(); Gui.SendMsgToActiveView('ViewFit')
print('IFC objects in FreeCAD', len(doc.Objects))
"""

TERRAIN = r"""
import numpy as np, pdal, json, os
os.makedirs('showcase', exist_ok=True)
rng = np.random.default_rng(7); n = 120000
x = rng.uniform(0, 400, n); y = rng.uniform(0, 300, n)
z = 30 + 18*np.exp(-((x-140)**2+(y-170)**2)/6000) + 10*np.exp(-((x-300)**2+(y-90)**2)/4000) + 0.02*x + rng.normal(0, 0.15, n)
cls = np.full(n, 2, dtype=np.uint8)
trees = rng.random(n) < 0.08; z[trees] += rng.uniform(3, 12, trees.sum()); cls[trees] = 5
x += 280000; y += 6132000   # Adelaide-ish MGA54 coordinates
arr = np.zeros(n, dtype=[('X','f8'),('Y','f8'),('Z','f8'),('Classification','u1')])
arr['X'], arr['Y'], arr['Z'], arr['Classification'] = x, y, z, cls
p = pdal.Pipeline(json.dumps([
  {'type':'writers.las','filename':'showcase/site.laz','a_srs':'EPSG:7854','compression':'laszip'}]), arrays=[arr]); p.execute()
g = pdal.Pipeline(json.dumps([
  {'type':'readers.las','filename':'showcase/site.laz'},
  {'type':'filters.range','limits':'Classification[2:2]'},
  {'type':'writers.gdal','filename':'showcase/dtm.tif','resolution':2.0,'output_type':'idw','gdaldriver':'GTiff'}])); g.execute()
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
fig = plt.figure(figsize=(9,5.2), dpi=140); ax = fig.add_subplot(projection='3d')
k = rng.choice(n, 25000, replace=False)
ax.scatter(x[k]-280000, y[k]-6132000, z[k], c=np.where(cls[k]==5, '#2e7d32', None) if False else z[k], s=0.4, cmap='terrain')
ax.view_init(32, -60); ax.set_box_aspect((4,3,0.9)); ax.set_axis_off()
fig.savefig('showcase/pointcloud.png', bbox_inches='tight', facecolor='white')
print('PDAL points', n)
"""

QGIS_MAP = r"""
import subprocess, os
subprocess.run(['gdaldem','hillshade','-z','2','showcase/dtm.tif','showcase/hillshade.tif'], check=True, capture_output=True)
subprocess.run(['gdal_contour','-a','elev','-i','2','showcase/dtm.tif','showcase/contours.gpkg'], check=True, capture_output=True)
from aec import init_qgis; init_qgis()
from qgis.core import (QgsRasterLayer, QgsVectorLayer, QgsProject, QgsMapSettings, QgsMapRendererParallelJob,
    QgsLineSymbol, QgsSingleSymbolRenderer, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor
hs = QgsRasterLayer('showcase/hillshade.tif', 'hillshade')
dtm = QgsRasterLayer('showcase/dtm.tif', 'dtm'); dtm.renderer().setOpacity(0.0) if False else None
ct = QgsVectorLayer('showcase/contours.gpkg', 'contours', 'ogr')
ct.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({'color':'#8a4b14','width':'0.35'})))
lab = QgsPalLayerSettings(); lab.fieldName = 'elev'; lab.enabled = True
tf = QgsTextFormat(); tf.setSize(7); tf.setColor(QColor('#5a2d0c')); lab.setFormat(tf)
ct.setLabeling(QgsVectorLayerSimpleLabeling(lab)); ct.setLabelsEnabled(True)
ms = QgsMapSettings(); ms.setLayers([ct, hs]); ms.setDestinationCrs(hs.crs()); ms.setExtent(hs.extent())
ms.setOutputSize(QSize(1400, 1050)); ms.setBackgroundColor(QColor('white'))
job = QgsMapRendererParallelJob(ms); job.start(); job.waitForFinished()
job.renderedImage().save('showcase/site_map.png', 'png')
print('QGIS map rendered', hs.crs().authid(), ct.featureCount(), 'contours')
"""


async def main():
    async with connect(URL, {"Authorization": f"Bearer {TOKEN}"}) as st:
        async with ClientSession(st[0], st[1]) as s:
            await s.initialize()
            await fc(s, BRACKET, "bracket"); await view(s, "1_bracket.png")
            await py(s, PLAN, "plan"); await fetch(s, "showcase/floorplan.png", "2_floorplan.png")
            await py(s, IFC, "ifc")
            await fc(s, IFC_IN_FREECAD, "ifc->freecad"); await view(s, "3_ifc_house.png")
            await py(s, TERRAIN, "terrain"); await fetch(s, "showcase/pointcloud.png", "4_pointcloud.png")
            await py(s, QGIS_MAP, "qgis"); await fetch(s, "showcase/site_map.png", "5_site_map.png")
            await fetch(s, "showcase/wall_schedule.csv", "wall_schedule.csv")


if __name__ == "__main__":
    asyncio.run(main())
