"""Manual REPL client for the BiciMAD MCP server — no LLM involved.

Connects to src/mcp/server/server.py as a stdio subprocess and lets you
drive it by hand: list tools/resources/prompts, call a tool with JSON
arguments, read a resource, get a prompt. The point is to exercise the raw
MCP protocol yourself before Fase 4 puts a model in the loop — if something
breaks here, it's the transport/protocol, not a model's tool-choice.

Usage:
    python -m src.mcp.client.repl [--verbose]

Commands:
    tools                          list available tools
    call <tool> <json-args>        call a tool, e.g. call get_station_status {"station_id": 1}
    resources                      list resources and resource templates
    read <uri>                     read a resource, e.g. read bicimad://stations
    prompts                        list prompts
    prompt <name> <json-args>      get a prompt, e.g. prompt plan_trip {"origen": "Sol", ...}
    help                           show this list
    exit                           close the session and quit (Ctrl-D also works)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

from mcp.client.stdio import StdioServerParameters, stdio_client

from mcp import Client, MCPError

_REPO_ROOT = Path(__file__).resolve().parents[3]

_HELP = """\
Comandos disponibles:
  tools                          lista las tools
  call <tool> <json-args>        llama a una tool, ej: call get_station_status {"station_id": 1}
  resources                      lista resources y resource templates
  read <uri>                     lee un resource, ej: read bicimad://stations
  prompts                        lista prompts
  prompt <name> <json-args>      obtiene un prompt, ej: prompt plan_trip {"origen": "Sol", "destino": "Atocha", "hora": "18:00"}
  help                           muestra esta lista
  exit                           cierra la sesión y sale (Ctrl-D también vale)\
"""


def _server_params() -> StdioServerParameters:
    """Launch src/mcp/server/server.py as a subprocess, same interpreter, repo root as cwd."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp.server.server"],
        cwd=str(_REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# --verbose: a minimal pass-through Transport that prints every JSON-RPC
# message crossing the wire, in both directions, before handing it to the
# real MCP client machinery. See "Transport" protocol in mcp.client: an
# async context manager that just needs to yield (read_stream, write_stream).
# ---------------------------------------------------------------------------


def _print_wire(direction: str, item: Any) -> None:
    if isinstance(item, Exception):
        print(f"{direction} <exception> {item}")
        return
    print(f"{direction} {item.message.model_dump_json(exclude_none=True)}")


class _VerboseReadStream:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def receive(self) -> Any:
        item = await self._inner.receive()
        _print_wire("<-", item)
        return item

    async def aclose(self) -> None:
        await self._inner.aclose()

    def __aiter__(self) -> _VerboseReadStream:
        return self

    async def __anext__(self) -> Any:
        item = await self._inner.__anext__()
        _print_wire("<-", item)
        return item

    async def __aenter__(self) -> _VerboseReadStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        result: bool | None = await self._inner.__aexit__(exc_type, exc_val, exc_tb)
        return result


class _VerboseWriteStream:
    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def send(self, item: Any) -> None:
        _print_wire("->", item)
        await self._inner.send(item)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> _VerboseWriteStream:
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        result: bool | None = await self._inner.__aexit__(exc_type, exc_val, exc_tb)
        return result


class _VerboseStdioTransport:
    """Wraps stdio_client(); satisfies mcp.client.Transport by delegation."""

    def __init__(self, params: StdioServerParameters) -> None:
        self._params = params
        self._cm: AbstractAsyncContextManager[Any] | None = None

    async def __aenter__(self) -> tuple[_VerboseReadStream, _VerboseWriteStream]:
        cm = stdio_client(self._params)
        self._cm = cm
        read_stream, write_stream = await cm.__aenter__()
        return _VerboseReadStream(read_stream), _VerboseWriteStream(write_stream)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        assert self._cm is not None
        result: bool | None = await self._cm.__aexit__(exc_type, exc_val, exc_tb)
        return result


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def _cmd_tools(client: Client) -> None:
    result = await client.list_tools()
    for tool in result.tools:
        print(f"- {tool.name}: {tool.description}")


async def _cmd_call(client: Client, rest: str) -> None:
    parts = rest.split(maxsplit=1)
    if not parts:
        print("Uso: call <tool> <json-args>")
        return
    tool_name, raw_args = parts[0], parts[1] if len(parts) > 1 else "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        print(f"Argumentos inválidos (debe ser JSON): {e}")
        return

    result = await client.call_tool(tool_name, args)
    print(f"is_error: {result.is_error}")
    if result.structured_content is not None:
        print("structured_content:", json.dumps(result.structured_content, ensure_ascii=False))
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            print("content:", text)


async def _cmd_resources(client: Client) -> None:
    resources = await client.list_resources()
    for r in resources.resources:
        print(f"- {r.uri}  ({r.name}): {r.description}")
    templates = await client.list_resource_templates()
    for t in templates.resource_templates:
        print(f"- {t.uri_template}  ({t.name}, plantilla): {t.description}")


async def _cmd_read(client: Client, uri: str) -> None:
    if not uri:
        print("Uso: read <uri>")
        return
    try:
        result = await client.read_resource(uri)
    except MCPError as e:
        # Resource failures surface as a protocol-level exception, not a
        # result with an error flag — unlike call_tool(). See PLAN_MCP.md,
        # Fase 2.
        print(f"Error leyendo el resource: {e}")
        return
    for content in result.contents:
        text = getattr(content, "text", None)
        print(text if text is not None else content)


async def _cmd_prompts(client: Client) -> None:
    result = await client.list_prompts()
    for p in result.prompts:
        print(f"- {p.name}: {p.description}")


async def _cmd_prompt(client: Client, rest: str) -> None:
    parts = rest.split(maxsplit=1)
    if not parts:
        print("Uso: prompt <nombre> <json-args>")
        return
    name, raw_args = parts[0], parts[1] if len(parts) > 1 else "{}"
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as e:
        print(f"Argumentos inválidos (debe ser JSON): {e}")
        return

    try:
        result = await client.get_prompt(name, args)
    except MCPError as e:
        print(f"Error obteniendo el prompt: {e}")
        return
    for message in result.messages:
        text = getattr(message.content, "text", None)
        print(f"[{message.role}] {text}")


async def _dispatch(client: Client, line: str) -> None:
    parts = line.split(maxsplit=1)
    cmd, rest = parts[0], parts[1] if len(parts) > 1 else ""

    if cmd == "help":
        _print_help()
    elif cmd == "tools":
        await _cmd_tools(client)
    elif cmd == "call":
        await _cmd_call(client, rest)
    elif cmd == "resources":
        await _cmd_resources(client)
    elif cmd == "read":
        await _cmd_read(client, rest)
    elif cmd == "prompts":
        await _cmd_prompts(client)
    elif cmd == "prompt":
        await _cmd_prompt(client, rest)
    else:
        print(f"Comando desconocido: {cmd!r}. Escribe 'help' para ver los comandos disponibles.")


def _print_help() -> None:
    print(_HELP)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run(verbose: bool) -> None:
    params = _server_params()
    server_target: StdioServerParameters | _VerboseStdioTransport = (
        _VerboseStdioTransport(params) if verbose else params
    )

    async with Client(server_target) as client:
        print("Conectado al servidor bicimad. Escribe 'help' para ver comandos, 'exit' para salir.")
        while True:
            try:
                line = input("bicimad> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("exit", "quit"):
                break
            try:
                await _dispatch(client, line)
            except Exception as e:  # noqa: BLE001 — REPL keeps running after a bad command
                print(f"Error: {e}")

    print("Sesión cerrada.")


def main() -> None:
    parser = argparse.ArgumentParser(description="REPL manual para el servidor MCP de BiciMAD")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Muestra cada mensaje JSON-RPC crudo (entrante y saliente)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.verbose))


if __name__ == "__main__":
    main()
