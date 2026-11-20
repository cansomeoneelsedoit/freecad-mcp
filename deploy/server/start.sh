#!/bin/sh
# Starts the virtual screen, FreeCAD (restarted if it exits) and the MCP server.
# Required: FREECAD_MCP_LISTEN_TOKEN (32+ chars). Optional: PUBLIC_HOST (the public
# domain, e.g. freecad.example.com), PORT (default 8796), FREECAD_MCP_EXTRA_ARGS.
set -eu

if [ "${#FREECAD_MCP_LISTEN_TOKEN}" -lt 32 ]; then
    echo "FREECAD_MCP_LISTEN_TOKEN must be set to 32+ random characters" >&2
    exit 1
fi

Xvfb :99 -screen 0 1600x1000x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
i=0; while [ $i -lt 50 ] && [ ! -e /tmp/.X11-unix/X99 ]; do i=$((i+1)); sleep 0.2; done

(
    # FreeCAD must never see the MCP token
    unset FREECAD_MCP_LISTEN_TOKEN
    while true; do
        echo "starting FreeCAD"
        /opt/freecad/AppRun >>/tmp/freecad.log 2>&1 || true
        echo "FreeCAD exited; restarting in 3s"
        sleep 3
    done
) &

set -- --transport streamable-http --listen-host 0.0.0.0 --listen-port "${PORT:-8796}"
if [ -n "${PUBLIC_HOST:-}" ]; then
    set -- "$@" --allowed-host "$PUBLIC_HOST"
fi
# shellcheck disable=SC2086
exec freecad-mcp "$@" ${FREECAD_MCP_EXTRA_ARGS:-}
