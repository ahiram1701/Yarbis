"""Cliente MCP (Model Context Protocol) sincrono para Yarbis.

Permite que Yarbis se conecte como cliente a servidores MCP arbitrarios y use sus
herramientas. Implementa JSON-RPC 2.0 sobre dos transportes, sin depender del SDK
oficial (async): stdio (subproceso local) y HTTP streamable (httpx).

Flujo por servidor: initialize -> notifications/initialized -> tools/list (cache)
-> tools/call. Las conexiones son persistentes y cacheadas por nombre de servidor.

Seguridad: conectar servidores MCP arbitrarios ejecuta comandos locales (stdio) o
llama endpoints (HTTP). El gate de activacion vive en la capa de tools/estado.
"""

import atexit
import json
import queue
import re
import subprocess
import threading
import time

from process_utils import no_window_creationflags

try:
    import httpx
except Exception:  # pragma: no cover - httpx viene en requirements
    httpx = None

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "yarbis", "version": "1.0"}
DEFAULT_TIMEOUT_SECONDS = 60
MAX_SERVERS = 12
_SAFE_TOKEN = re.compile(r"[^a-zA-Z0-9_]+")

_CONNECTIONS: dict[str, "_Connection"] = {}
_QUALIFIED: dict[str, tuple[str, str]] = {}
_MANAGER_LOCK = threading.RLock()


class MCPError(RuntimeError):
    pass


def _safe_token(value: str) -> str:
    token = _SAFE_TOKEN.sub("_", str(value or "").strip()).strip("_")
    return token or "x"


def qualified_tool_name(server: str, tool: str) -> str:
    return f"mcp__{_safe_token(server)}__{_safe_token(tool)}"


class _Connection:
    """Conexion viva a un servidor MCP. Subclases: stdio y http."""

    def __init__(self, name: str, config: dict):
        self.name = str(name)
        self.config = dict(config or {})
        self.lock = threading.RLock()
        self._id = 0
        self.tools: list[dict] = []
        self.initialized = False

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # --- API que implementan las subclases ---
    def _send_request(self, method: str, params: dict, timeout: float) -> dict:
        raise NotImplementedError

    def _send_notification(self, method: str, params: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # --- Protocolo MCP comun ---
    def initialize(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
        with self.lock:
            result = self._send_request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
                timeout,
            )
            self._send_notification("notifications/initialized", {})
            self.initialized = True
            return result

    def list_tools(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict]:
        with self.lock:
            if not self.initialized:
                self.initialize(timeout)
            result = self._send_request("tools/list", {}, timeout)
        tools = result.get("tools", []) if isinstance(result, dict) else []
        self.tools = [t for t in tools if isinstance(t, dict) and t.get("name")]
        return self.tools

    def call_tool(self, tool: str, arguments: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
        with self.lock:
            if not self.initialized:
                self.initialize(timeout)
            return self._send_request(
                "tools/call",
                {"name": str(tool), "arguments": arguments or {}},
                timeout,
            )


class _StdioConnection(_Connection):
    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        command = str(config.get("command", "")).strip()
        if not command:
            raise MCPError("El servidor stdio requiere 'command'.")
        args = config.get("args", []) or []
        if not isinstance(args, list):
            raise MCPError("'args' debe ser una lista.")
        env = config.get("env", {}) or {}
        cwd = str(config.get("cwd", "")).strip() or None

        import os as _os

        proc_env = dict(_os.environ)
        if isinstance(env, dict):
            proc_env.update({str(k): str(v) for k, v in env.items()})

        try:
            self.proc = subprocess.Popen(
                [command, *[str(a) for a in args]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                cwd=cwd,
                env=proc_env,
                creationflags=no_window_creationflags(),
            )
        except OSError as exc:
            raise MCPError(f"No pude lanzar el servidor MCP '{name}': {exc}") from exc

        # Hilo lector: parsea cada linea JSON a una cola para que _read_until
        # tenga timeout real (readline() bloqueante colgaria el ciclo del agente).
        self._queue: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=self._reader_loop, name=f"mcp-{name}-reader", daemon=True)
        self._reader.start()

    def _reader_loop(self) -> None:
        stdout = self.proc.stdout
        while True:
            line = stdout.readline()
            if line == "":
                break  # EOF: el proceso termino
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                self._queue.put(message)

    def _send_notification(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _send_request(self, method: str, params: dict, timeout: float) -> dict:
        request_id = self._next_id()
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        message = self._read_until(request_id, timeout)
        if "error" in message and message["error"]:
            err = message["error"]
            raise MCPError(f"Error MCP en {method}: {err.get('message', err)}")
        return message.get("result", {})

    def _write(self, message: dict) -> None:
        if self.proc.poll() is not None:
            raise MCPError(f"El servidor MCP '{self.name}' termino (codigo {self.proc.returncode}).")
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"No pude escribir al servidor MCP '{self.name}': {exc}") from exc

    def _read_until(self, request_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + max(1.0, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = self._queue.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise MCPError(f"El servidor MCP '{self.name}' termino (codigo {self.proc.returncode}).")
                continue
            if message.get("id") == request_id:
                return message
            # notificaciones u otros ids: ignorar y seguir esperando
        raise MCPError(f"Timeout esperando respuesta de '{self.name}' ({_format_timeout(timeout)}).")

    def close(self) -> None:
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        except Exception:
            pass
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


def _format_timeout(timeout: float) -> str:
    return f"{int(timeout)}s"


class _HttpConnection(_Connection):
    def __init__(self, name: str, config: dict):
        super().__init__(name, config)
        if httpx is None:
            raise MCPError("httpx no esta disponible para transporte HTTP.")
        self.url = str(config.get("url", "")).strip()
        if not self.url:
            raise MCPError("El servidor http requiere 'url'.")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        extra = config.get("headers", {}) or {}
        if isinstance(extra, dict):
            headers.update({str(k): str(v) for k, v in extra.items()})
        self.session_id = ""
        self._client = httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS, headers=headers)

    def _post(self, message: dict, timeout: float):
        headers = {}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            response = self._client.post(self.url, json=message, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise MCPError(f"Error HTTP con '{self.name}': {exc}") from exc
        sid = response.headers.get("Mcp-Session-Id") or response.headers.get("mcp-session-id")
        if sid:
            self.session_id = sid
        return response

    def _send_notification(self, method: str, params: dict) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params}, DEFAULT_TIMEOUT_SECONDS)

    def _send_request(self, method: str, params: dict, timeout: float) -> dict:
        request_id = self._next_id()
        response = self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
            timeout,
        )
        if response.status_code >= 400:
            raise MCPError(f"HTTP {response.status_code} de '{self.name}' en {method}: {response.text[:200]}")
        message = _extract_jsonrpc(response, request_id)
        if message is None:
            raise MCPError(f"Respuesta MCP vacia de '{self.name}' en {method}.")
        if message.get("error"):
            err = message["error"]
            raise MCPError(f"Error MCP en {method}: {err.get('message', err)}")
        return message.get("result", {})

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


def _extract_jsonrpc(response, request_id: int) -> dict | None:
    """Extrae el mensaje JSON-RPC de una respuesta http (JSON directo o SSE)."""
    content_type = (response.headers.get("content-type") or "").lower()
    text = response.text or ""
    if "text/event-stream" in content_type or text.lstrip().startswith("event:") or "\ndata:" in text:
        chosen = None
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                message = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict):
                if message.get("id") == request_id:
                    return message
                if chosen is None and ("result" in message or "error" in message):
                    chosen = message
        return chosen
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _build_connection(config: dict) -> _Connection:
    transport = str(config.get("transport", "")).strip().lower()
    name = str(config.get("name", "")).strip()
    if not name:
        raise MCPError("El servidor MCP requiere 'name'.")
    if transport == "stdio":
        return _StdioConnection(name, config)
    if transport == "http":
        return _HttpConnection(name, config)
    raise MCPError(f"Transporte MCP no soportado: {transport!r} (usa 'stdio' o 'http').")


def connect(config: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict]:
    """Conecta (o reconecta) a un servidor MCP y devuelve sus herramientas."""
    name = str(config.get("name", "")).strip()
    if not name:
        raise MCPError("El servidor MCP requiere 'name'.")
    with _MANAGER_LOCK:
        if name in _CONNECTIONS:
            _CONNECTIONS.pop(name).close()
        if len(_CONNECTIONS) >= MAX_SERVERS:
            raise MCPError(f"Limite de servidores MCP conectados alcanzado ({MAX_SERVERS}).")
        connection = _build_connection(config)
        _CONNECTIONS[name] = connection
    tools = connection.list_tools(timeout)
    _rebuild_qualified()
    return tools


def disconnect(name: str) -> bool:
    with _MANAGER_LOCK:
        connection = _CONNECTIONS.pop(str(name).strip(), None)
    if connection:
        connection.close()
        _rebuild_qualified()
        return True
    return False


def disconnect_all() -> None:
    with _MANAGER_LOCK:
        connections = list(_CONNECTIONS.values())
        _CONNECTIONS.clear()
        _QUALIFIED.clear()
    for connection in connections:
        connection.close()


def is_connected(name: str) -> bool:
    with _MANAGER_LOCK:
        return str(name).strip() in _CONNECTIONS


def list_tools(name: str, refresh: bool = False, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[dict]:
    with _MANAGER_LOCK:
        connection = _CONNECTIONS.get(str(name).strip())
    if connection is None:
        raise MCPError(f"Servidor MCP no conectado: {name}")
    if refresh or not connection.tools:
        tools = connection.list_tools(timeout)
        _rebuild_qualified()
        return tools
    return connection.tools


def call_tool(name: str, tool: str, arguments: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    with _MANAGER_LOCK:
        connection = _CONNECTIONS.get(str(name).strip())
    if connection is None:
        raise MCPError(f"Servidor MCP no conectado: {name}")
    result = connection.call_tool(tool, arguments or {}, timeout)
    return _render_tool_result(result)


def call_qualified(qualified_name: str, arguments: dict, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> str:
    server, tool = resolve_qualified(qualified_name)
    if not server:
        raise MCPError(f"Herramienta MCP desconocida: {qualified_name}")
    return call_tool(server, tool, arguments, timeout)


def resolve_qualified(qualified_name: str) -> tuple[str, str]:
    key = str(qualified_name or "").strip()
    with _MANAGER_LOCK:
        if key in _QUALIFIED:
            return _QUALIFIED[key]
    return ("", "")


def _render_tool_result(result: dict) -> str:
    """Convierte el resultado MCP (content blocks) en texto para el modelo."""
    if not isinstance(result, dict):
        return str(result)
    parts = []
    for block in result.get("content", []) or []:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            parts.append(str(block.get("text", "")))
        elif block_type == "resource":
            resource = block.get("resource", {})
            parts.append(str(resource.get("text") or resource.get("uri") or resource))
        else:
            parts.append(json.dumps(block, ensure_ascii=False))
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        structured = result.get("structuredContent")
        if structured is not None:
            text = json.dumps(structured, ensure_ascii=False)
    if result.get("isError"):
        return f"[MCP devolvio error]\n{text}" if text else "[MCP devolvio error sin detalle]"
    return text or "(sin salida)"


def _tool_to_schema(server: str, tool: dict) -> dict:
    name = qualified_tool_name(server, tool.get("name", ""))
    description = str(tool.get("description", "") or f"Herramienta MCP {tool.get('name')} del servidor {server}")
    input_schema = tool.get("inputSchema")
    if not isinstance(input_schema, dict):
        input_schema = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description[:400],
            "parameters": input_schema,
        },
    }


def _rebuild_qualified() -> None:
    with _MANAGER_LOCK:
        _QUALIFIED.clear()
        for server, connection in _CONNECTIONS.items():
            for tool in connection.tools:
                real_name = str(tool.get("name", ""))
                if not real_name:
                    continue
                _QUALIFIED[qualified_tool_name(server, real_name)] = (server, real_name)


def tool_specs() -> list[dict]:
    """Schemas (formato function-calling) de TODAS las tools MCP conectadas."""
    with _MANAGER_LOCK:
        connections = list(_CONNECTIONS.items())
    specs = []
    for server, connection in connections:
        for tool in connection.tools:
            if tool.get("name"):
                specs.append(_tool_to_schema(server, tool))
    return specs


def status() -> list[dict]:
    with _MANAGER_LOCK:
        return [
            {"name": name, "transport": conn.config.get("transport", ""), "tools": len(conn.tools)}
            for name, conn in _CONNECTIONS.items()
        ]


atexit.register(disconnect_all)
