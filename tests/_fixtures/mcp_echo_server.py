"""Servidor MCP stub minimo (stdio) para tests del cliente MCP de Yarbis.

Implementa lo justo del protocolo: initialize, notifications/initialized,
tools/list y tools/call, con una sola herramienta `echo`. JSON-RPC 2.0
delimitado por linea sobre stdin/stdout.
"""

import json
import sys


TOOLS = [
    {
        "name": "echo",
        "description": "Devuelve el texto recibido.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]


def _send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def _handle(message):
    method = message.get("method")
    msg_id = message.get("id")
    if method == "initialize":
        _send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo-stub", "version": "1.0"},
            },
        })
    elif method == "notifications/initialized":
        pass  # notificacion, sin respuesta
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = message.get("params", {})
        args = params.get("arguments", {})
        if params.get("name") == "echo":
            text = str(args.get("text", ""))
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": f"echo: {text}"}]},
            })
        else:
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": "tool no encontrada"},
            })
    elif msg_id is not None:
        _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "metodo no soportado"}})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        _handle(message)


if __name__ == "__main__":
    main()
