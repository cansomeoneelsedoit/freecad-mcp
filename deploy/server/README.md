# CAD & GIS connector for BizGPT (FreeCAD + ezdxf + IfcOpenShell + PDAL + QGIS)

One container, one MCP endpoint. FreeCAD runs with its full GUI on a virtual
screen (Xvfb). The MCP add-on listens on 127.0.0.1:9875 inside the container
only. Next to it, a conda-forge environment holds QGIS (PyQGIS + qgis_process),
PDAL, IfcOpenShell (+ IfcConvert, ifctester, ifcdiff, ifcpatch), ezdxf and GDAL.
All tools share one workspace folder (`/home/mcp/work`), so a job can move
between them: for example DXF to 3D part, point cloud to terrain to IFC site, or IFC
to schedule or GLB.

## Tools the AI sees

| Tool | What it does |
| --- | --- |
| FreeCAD tools (18) | `execute_code`, `create_object`, `get_view` (screenshot), `run_fem_analysis` ... |
| `aec_workflow_guide` | Recipes for combining the tools (call first) |
| `aec_stack_info` | Versions of everything |
| `run_aec_python` | Python 3.12 with qgis, pdal, ifcopenshell, ezdxf, gdal, shapely |
| `run_aec_command` | pdal, qgis_process, IfcConvert, GDAL CLIs (allow-list, no shell) |
| `fetch_file` / `upload_file` | Get input files in (public URLs only / text or base64) |
| `list_files` / `read_file_text` | Look at results |
| `get_download_link` | Signed HTTPS link (default 24 h) to hand a file to the user |

## Deploy (Coolify)

- Dockerfile: `/deploy/server/Dockerfile`, port 8796, memory limit 2 GB+.
- Env: `FREECAD_MCP_LISTEN_TOKEN` (32+ random chars), `PUBLIC_HOST` (the domain).
- Optional: mount a persistent volume on `/home/mcp/work`.
- Check: `FREECAD_MCP_URL=https://<domain>/mcp FREECAD_MCP_TOKEN_FILE=<file> python deploy/server/smoke_test_aec.py`

## Connect in BizGPT (Integrations > Custom > Add custom connector)

- Name: `CAD & GIS`
- MCP server URL: `https://<domain>/mcp`
- Auth: Bearer token = the `FREECAD_MCP_LISTEN_TOKEN` value

## Security

`execute_code` and `run_aec_python` run arbitrary Python inside the container,
so the bearer token is equivalent to a shell in that container. Keep it to
trusted accounts. Child processes get no token or other secrets in their
environment, `fetch_file` refuses private and internal addresses, and download links
are HMAC-signed, path-checked and expire. One container = one shared FreeCAD
session and one shared workspace for everyone holding the token.
