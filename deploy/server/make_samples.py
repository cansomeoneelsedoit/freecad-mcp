"""Make the BIZCAD sample test files (on the server) and download them to OUTDIR."""
import asyncio, json, sys
from pathlib import Path
sys.argv = sys.argv[:2]
sys.path.insert(0, str(Path(__file__).parent))
import showcase as sc  # noqa: E402  (module-level main() only runs under __main__ guard below)
from mcp import ClientSession

GEN = r"""
import os, json, numpy as np, ezdxf, pdal
os.makedirs('samples', exist_ok=True)

# 1. Site boundary DXF on GDA2020 / MGA zone 54 (metres), inside the LiDAR area
doc = ezdxf.new('R2018', setup=True); doc.header['$INSUNITS'] = 6
doc.layers.add('BOUNDARY', color=1); doc.layers.add('TEXT', color=7)
m = doc.modelspace()
pts = [(280060,6132045),(280250,6132030),(280330,6132120),(280300,6132255),(280140,6132270),(280050,6132180)]
m.add_lwpolyline(pts, close=True, dxfattribs={'layer':'BOUNDARY'})
m.add_text('LOT 101  GDA2020 MGA54', height=4, dxfattribs={'layer':'TEXT'}).set_placement((280120, 6132150))
doc.saveas('samples/site_boundary_mga54.dxf')

# 2. LiDAR LAZ: ground (class 2), trees (class 5) and noise outliers (class 1), MGA54
rng = np.random.default_rng(11); n = 150000
x = rng.uniform(0, 400, n); y = rng.uniform(0, 300, n)
z = 30 + 18*np.exp(-((x-140)**2+(y-170)**2)/6000) + 10*np.exp(-((x-300)**2+(y-90)**2)/4000) + 0.02*x + rng.normal(0, 0.12, n)
cls = np.full(n, 2, dtype=np.uint8)
trees = rng.random(n) < 0.07; z[trees] += rng.uniform(3, 14, trees.sum()); cls[trees] = 5
noise = rng.random(n) < 0.004; z[noise] += rng.choice([-25, 40], noise.sum()); cls[noise] = 1
arr = np.zeros(n, dtype=[('X','f8'),('Y','f8'),('Z','f8'),('Classification','u1'),('Intensity','u2')])
arr['X'] = x + 280000; arr['Y'] = y + 6132000; arr['Z'] = z; arr['Classification'] = cls
arr['Intensity'] = rng.integers(200, 4000, n)
pdal.Pipeline(json.dumps([{'type':'writers.las','filename':'samples/site_lidar_mga54.laz','a_srs':'EPSG:7854',
  'compression':'laszip','scale_x':0.01,'scale_y':0.01,'scale_z':0.01,'offset_x':'auto','offset_y':'auto','offset_z':'auto'}]), arrays=[arr]).execute()

# 3. Messy architectural DXF to inspect (mixed layers, blocks, units in mm)
d2 = ezdxf.new('R2013', setup=True); d2.header['$INSUNITS'] = 4
for name, col in [('A-WALL',7),('A-DOOR',1),('A-GLAZ',4),('A-FURN',8),('A-ANNO-DIMS',3),('A-ANNO-TEXT',2),('0-OLD',9)]:
    d2.layers.add(name, color=col)
ms = d2.modelspace()
ms.add_lwpolyline([(0,0),(15000,0),(15000,9000),(0,9000)], close=True, dxfattribs={'layer':'A-WALL','const_width':230})
for xx in (5000, 10000): ms.add_line((xx,0),(xx,9000), dxfattribs={'layer':'A-WALL'})
chair = d2.blocks.new('CHAIR'); chair.add_lwpolyline([(0,0),(450,0),(450,450),(0,450)], close=True)
for i in range(6): ms.add_blockref('CHAIR', (1500+i*600, 4000), dxfattribs={'layer':'A-FURN'})
for xx in (1200, 6200, 11200): ms.add_arc((xx, 0), 820, 0, 90, dxfattribs={'layer':'A-DOOR'})
for xx in (2500, 7500, 12500): ms.add_line((xx-900, 9000), (xx+900, 9000), dxfattribs={'layer':'A-GLAZ'})
for t, p in [('MEETING', (2500,6500)), ('OFFICE 1', (7500,6500)), ('OFFICE 2', (12500,6500))]:
    ms.add_text(t, height=250, dxfattribs={'layer':'A-ANNO-TEXT'}).set_placement(p)
ms.add_linear_dim(base=(0,-1500), p1=(0,0), p2=(15000,0), dimstyle='EZDXF', override={'dimlfac':1}, dxfattribs={'layer':'A-ANNO-DIMS'}).render()
ms.add_circle((20000, 20000), 500, dxfattribs={'layer':'0-OLD'})   # stray old geometry far away
d2.saveas('samples/office_plan_mm.dxf')

# 4. IFC house with doors; some walls/doors deliberately missing a fire rating
import ifcopenshell, ifcopenshell.api as api
f = ifcopenshell.file(schema='IFC4')
proj = api.run('root.create_entity', f, ifc_class='IfcProject', name='Sample house')
api.run('unit.assign_unit', f)
ctx = api.run('context.add_context', f, context_type='Model')
body = api.run('context.add_context', f, context_type='Model', context_identifier='Body', target_view='MODEL_VIEW', parent=ctx)
site = api.run('root.create_entity', f, ifc_class='IfcSite', name='Lot 101')
bldg = api.run('root.create_entity', f, ifc_class='IfcBuilding', name='House')
st = api.run('root.create_entity', f, ifc_class='IfcBuildingStorey', name='Ground floor')
api.run('aggregate.assign_object', f, relating_object=proj, products=[site])
api.run('aggregate.assign_object', f, relating_object=site, products=[bldg])
api.run('aggregate.assign_object', f, relating_object=bldg, products=[st])
def place(el, x, y, rot):
    c, s = np.cos(np.radians(rot)), np.sin(np.radians(rot))
    api.run('geometry.edit_object_placement', f, product=el, matrix=np.array([[c,-s,0,x],[s,c,0,y],[0,0,1,0],[0,0,0,1]], float))
walls = [('W01 North',0,0,0,12,'FRL 60/60/60',True),('W02 East',12,0,90,8,'FRL 60/60/60',True),
         ('W03 South',12,8,180,12,None,True),('W04 West',0,8,270,8,'FRL 60/60/60',True),
         ('W05 Party',7,0,90,8,None,False),('W06 Bath',7,4.5,0,5,'-/-/-',False)]
for name,x,y,r,L,fr,ext in walls:
    w = api.run('root.create_entity', f, ifc_class='IfcWall', name=name)
    api.run('geometry.assign_representation', f, product=w, representation=api.run('geometry.add_wall_representation', f, context=body, length=L, height=2.7, thickness=0.2))
    place(w, x, y, r); api.run('spatial.assign_container', f, relating_structure=st, products=[w])
    props = {'IsExternal': ext}
    if fr: props['FireRating'] = fr
    api.run('pset.edit_pset', f, pset=api.run('pset.add_pset', f, product=w, name='Pset_WallCommon'), properties=props)
for name, fr in [('D01 Entry', 'FRL -/30/30'), ('D02 Bed', None), ('D03 Bath', None)]:
    dr = api.run('root.create_entity', f, ifc_class='IfcDoor', name=name)
    api.run('spatial.assign_container', f, relating_structure=st, products=[dr])
    if fr:
        api.run('pset.edit_pset', f, pset=api.run('pset.add_pset', f, product=dr, name='Pset_DoorCommon'), properties={'FireRating': fr})
f.write('samples/sample_house.ifc')
for p in sorted(os.listdir('samples')): print(p, os.path.getsize('samples/' + p))
"""

FILES = ["site_boundary_mga54.dxf", "site_lidar_mga54.laz", "office_plan_mm.dxf", "sample_house.ifc"]


async def main():
    async with sc.connect(sc.URL, {"Authorization": f"Bearer {sc.TOKEN}"}) as st:
        async with ClientSession(st[0], st[1]) as s:
            await s.initialize()
            await sc.py(s, GEN, "samples")
            for name in FILES:
                await sc.fetch(s, f"samples/{name}", name)


if __name__ == "__main__":
    asyncio.run(main())
