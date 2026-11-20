"""Second pass: IFC house via IfcConvert -> STEP -> FreeCAD view, and the floor plan with metre dims."""
import asyncio, base64, json, os, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from showcase import connect, txt, py, fc, view, fetch, URL, TOKEN, OUT, PLAN  # noqa: E402
from mcp import ClientSession

PLAN2 = PLAN.replace(
    "dimstyle='EZDXF', dxfattribs={'layer':'DIMS'}).render()",
    "dimstyle='EZDXF', override={'dimlfac': 1, 'dimpost': '<> m', 'dimdec': 2}, dxfattribs={'layer':'DIMS'}).render()",
)

HOUSE = r"""
import FreeCAD, Part, os
W = os.environ.get('AEC_WORKSPACE', '/home/mcp/work')
for d in list(FreeCAD.listDocuments()): FreeCAD.closeDocument(d)
doc = FreeCAD.newDocument('House')
sh = Part.read(W + '/showcase/house.stp')
cols = [(0.85,0.85,0.82), (0.75,0.55,0.40)]
for i, solid in enumerate(sh.Solids):
    o = doc.addObject('Part::Feature', f'E{i}'); o.Shape = solid
    o.ViewObject.ShapeColor = cols[0] if solid.BoundBox.ZLength > 1 else cols[1]
doc.recompute()
import FreeCADGui as Gui
Gui.activeDocument().activeView().viewIsometric(); Gui.SendMsgToActiveView('ViewFit')
print('HOUSE solids', len(sh.Solids))
"""


async def main():
    async with connect(URL, {"Authorization": f"Bearer {TOKEN}"}) as st:
        async with ClientSession(st[0], st[1]) as s:
            await s.initialize()
            await py(s, PLAN2, "plan2"); await fetch(s, "showcase/floorplan.png", "2_floorplan.png")
            r = json.loads(txt(await s.call_tool("run_aec_command", {"program": "IfcConvert", "args": ["-y", "showcase/house.ifc", "showcase/house.stp"]})))
            print("[ifcconvert]", r.get("exit_code"), r.get("stderr", "")[-200:])
            await fc(s, HOUSE, "house"); await view(s, "3_ifc_house.png")


asyncio.run(main())
