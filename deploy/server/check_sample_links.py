"""Have BIZCAD itself fetch each public sample link and read it with the right library."""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import showcase as sc  # noqa: E402
from mcp import ClientSession

BASE = "https://raw.githubusercontent.com/cansomeoneelsedoit/freecad-mcp/samples/samples/"
FILES = ["site_boundary_mga54.dxf", "site_lidar_mga54.laz", "office_plan_mm.dxf", "sample_house.ifc"]
CHECK = r"""
import ezdxf, ifcopenshell, json, pdal
d = ezdxf.readfile('linkcheck/site_boundary_mga54.dxf'); print('boundary', len(d.modelspace().query('LWPOLYLINE')), 'polyline')
d = ezdxf.readfile('linkcheck/office_plan_mm.dxf'); print('office layers', sorted(l.dxf.name for l in d.layers))
f = ifcopenshell.open('linkcheck/sample_house.ifc'); print('ifc walls', len(f.by_type('IfcWall')), 'doors', len(f.by_type('IfcDoor')))
p = pdal.Pipeline(json.dumps([{'type':'readers.las','filename':'linkcheck/site_lidar_mga54.laz'}])); print('laz points', p.execute())
print('crs', 'EPSG:7854' if '7854' in p.metadata['metadata']['readers.las']['srs']['json'].__str__() else p.metadata['metadata']['readers.las']['srs'].get('horizontal','?')[:60])
"""


async def main():
    async with sc.connect(sc.URL, {"Authorization": f"Bearer {sc.TOKEN}"}) as st:
        async with ClientSession(st[0], st[1]) as s:
            await s.initialize()
            for f in FILES:
                print(f, sc.txt(await s.call_tool("fetch_file", {"url": BASE + f, "folder": "linkcheck"})))
            await sc.py(s, CHECK, "read back")


if __name__ == "__main__":
    asyncio.run(main())
