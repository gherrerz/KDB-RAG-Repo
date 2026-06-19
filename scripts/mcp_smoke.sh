#!/usr/bin/env bash
# Smoke test manual del servidor MCP (transporte streamable HTTP).
# Uso: ./scripts/mcp_smoke.sh [BASE_URL] [MCP_TOKEN]
#   BASE_URL   default http://127.0.0.1:8000
#   MCP_TOKEN  opcional; se envía como header X-MCP-Token si MCP_API_TOKEN está configurado.
set -euo pipefail
BASE="${1:-http://127.0.0.1:8000}"
TOKEN="${2:-}"
ACCEPT="Accept: application/json, text/event-stream"
AUTH=(); [ -n "$TOKEN" ] && AUTH=(-H "X-MCP-Token: $TOKEN")
# Headers de identidad opcionales: el servidor MCP los reenvía a cada tool.
AUTH+=(-H "x-role-id: smoke-role" -H "x-user-id: smoke-user" -H "x-country-id: cl")

# 1) initialize -> obtener mcp-session-id de las cabeceras
HDRS=$(curl -s -D - -o /tmp/mcp_init.txt "${AUTH[@]}" -X POST "$BASE/mcp" \
  -H "Content-Type: application/json" -H "$ACCEPT" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}')
SID=$(printf "%s" "$HDRS" | grep -i '^mcp-session-id:' | awk '{print $2}' | tr -d '\r')
echo "session id: $SID"

# 2) notificacion initialized
curl -s -o /dev/null "${AUTH[@]}" -X POST "$BASE/mcp" \
  -H "Content-Type: application/json" -H "$ACCEPT" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3) tools/list
echo "--- tools/list ---"
curl -s "${AUTH[@]}" -X POST "$BASE/mcp" \
  -H "Content-Type: application/json" -H "$ACCEPT" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | sed 's/^data: //' | grep -o '"name":"[a-z_]*"' | sort -u

# 4) tools/call de ejemplo (storage_health, sin args)
echo "--- tools/call storage_health ---"
curl -s "${AUTH[@]}" -X POST "$BASE/mcp" \
  -H "Content-Type: application/json" -H "$ACCEPT" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"storage_health","arguments":{}}}' \
  | sed 's/^data: //' | head -c 400; echo
