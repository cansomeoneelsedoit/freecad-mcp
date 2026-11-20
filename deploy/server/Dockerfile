# FreeCAD + MCP add-on + MCP server in ONE container, for a server with no display
# (Coolify, BizGPT custom connector). FreeCAD's GUI runs on a virtual X screen (Xvfb);
# the add-on listens on 127.0.0.1:9875 inside the container only; the MCP server is
# the only thing published (streamable HTTP, bearer token required).
#
# Build from the repo root:  docker build -f deploy/server/Dockerfile -t freecad-mcp-server .
# Run:  docker run -d -e FREECAD_MCP_LISTEN_TOKEN=... -e PUBLIC_HOST=cad.example.com \
#         -p 127.0.0.1:8796:8796 freecad-mcp-server

# ---- 1. FreeCAD, unpacked from the official AppImage (checksum verified)
FROM debian:bookworm-slim AS freecad
ARG TARGETARCH
ARG FREECAD_VERSION=1.1.4
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN set -eu; \
    case "${TARGETARCH:-amd64}" in amd64) arch=x86_64 ;; arm64) arch=aarch64 ;; *) echo "unsupported arch $TARGETARCH"; exit 1 ;; esac; \
    file="FreeCAD_${FREECAD_VERSION}-Linux-${arch}-py311.AppImage"; \
    base="https://github.com/FreeCAD/FreeCAD/releases/download/${FREECAD_VERSION}"; \
    curl -fsSL --retry 3 -o freecad.AppImage "$base/$file"; \
    curl -fsSL --retry 3 -o freecad.sha256 "$base/$file-SHA256.txt"; \
    echo "$(grep -oE '[0-9a-f]{64}' freecad.sha256 | head -1)  freecad.AppImage" | sha256sum -c -; \
    chmod +x freecad.AppImage; \
    ./freecad.AppImage --appimage-extract >/dev/null; \
    mv squashfs-root freecad; \
    rm freecad.AppImage freecad.sha256

# ---- 2. Runtime
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini xvfb xauth \
        libgl1 libegl1 libgl1-mesa-dri libglu1-mesa libopengl0 \
        libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 libxcb-xfixes0 \
        libfontconfig1 libfreetype6 libdbus-1-3 libxrender1 libxi6 libsm6 libice6 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY --from=freecad /opt/freecad /opt/freecad

# MCP server
COPY pyproject.toml README.md LICENSE /src/
COPY addon/FreeCADMCP/package.xml /src/addon/FreeCADMCP/package.xml
COPY src /src/src
RUN pip install --no-cache-dir /src && rm -rf /src

RUN useradd --create-home --uid 1000 mcp && mkdir -p /home/mcp/work && chown mcp:mcp /home/mcp/work
COPY --chown=mcp:mcp addon/FreeCADMCP /opt/freecad-mcp-addon/FreeCADMCP
COPY --chmod=0755 deploy/server/start.sh /usr/local/bin/freecad-mcp-start
COPY --chmod=0755 deploy/server/healthcheck.py /usr/local/bin/freecad-mcp-healthcheck

USER mcp
WORKDIR /home/mcp/work
ENV HOME=/home/mcp \
    DISPLAY=:99 \
    QT_QPA_PLATFORM=xcb \
    LIBGL_ALWAYS_SOFTWARE=1 \
    PORT=8796

# Put the add-on where FreeCAD looks for user add-ons, and switch its RPC auto-start on.
RUN set -eu; \
    userdir="$(/opt/freecad/AppRun freecadcmd -c 'import FreeCAD; print(FreeCAD.getUserAppDataDir())' 2>/dev/null | tail -1)"; \
    echo "FreeCAD user dir: $userdir"; test -n "$userdir"; \
    mkdir -p "$userdir/Mod"; \
    cp -r /opt/freecad-mcp-addon/FreeCADMCP "$userdir/Mod/"; \
    printf '{"remote_enabled": false, "allowed_ips": "127.0.0.1", "auto_start_rpc": true, "auth_token": ""}\n' \
        > "$userdir/freecad_mcp_settings.json"; \
    echo "$userdir" > /home/mcp/.freecad-userdir

VOLUME ["/home/mcp/work"]
EXPOSE 8796
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD ["python", "/usr/local/bin/freecad-mcp-healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/freecad-mcp-start"]
