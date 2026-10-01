"""Agentic loop client for the BiciMAD MCP server — an LLM in the loop.

Unlike repl.py (Fase 3), here the model decides which MCP tools to call and
with what arguments. This file is the translation layer between the MCP
protocol (which knows nothing about "tool_use" or Anthropic) and the
Anthropic Messages API (which knows nothing about MCP) — see
docs/PLAN_MCP.md, Fase 4.

Usage:
    python -m src.mcp.client.agent

Requires ANTHROPIC_API_KEY in .env or the environment — read via
src.common.config.settings, like the rest of the project's configuration
(see Settings.anthropic_api_key for why it's unprefixed).

Commands:
    /stations            load the bicimad://stations resource as context
                          for the rest of the session
    /plan <json-args>     use the plan_trip prompt as the next turn, e.g.
                          /plan {"origen": "Sol", "destino": "Atocha", "hora": "18:00"}
    exit                  close the session and quit (Ctrl-D also works)
    anything else          sent to the model; it may call MCP tools to answer
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import anthropic
from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam, ToolUseBlock
from mcp.client.stdio import StdioServerParameters

from mcp import Client, MCPError
from src.common.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]

MODEL = "claude-haiku-4-5-20251001"
MAX_TOOL_ITERATIONS = 8


def _server_params() -> StdioServerParameters:
    """Launch src/mcp/server/server.py as a subprocess, same interpreter, repo root as cwd."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "src.mcp.server.server"],
        cwd=str(_REPO_ROOT),
    )


def _mcp_tools_to_anthropic(tools: list[Any]) -> list[ToolParam]:
    """Translate MCP's ListToolsResult.tools to Anthropic's tools=[...] format.

    MCP doesn't know Anthropic's API exists; this mapping is the client's
    job. The only real difference is the field name (input_schema here vs.
    the wire alias inputSchema) — the JSON Schema itself is used as-is.
    """
    return [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in tools
    ]


async def _call_tool(mcp_client: Client, block: ToolUseBlock) -> ToolResultBlockParam:
    print(f"  -> llamando a tool: {block.name}({json.dumps(block.input, ensure_ascii=False)})")
    result = await mcp_client.call_tool(block.name, block.input)
    texts = [getattr(c, "text", None) for c in result.content]
    text = "\n".join(t for t in texts if t is not None) or "(sin contenido)"
    return {
        "type": "tool_result",
        "tool_use_id": block.id,
        "content": text,
        "is_error": result.is_error,
    }


async def _run_turn(
    anthropic_client: anthropic.AsyncAnthropic,
    mcp_client: Client,
    tools: list[ToolParam],
    messages: list[MessageParam],
    system: str | None,
) -> None:
    """Run one user turn to completion.

    Calls the model, executes any tool_use blocks it asks for via MCP,
    feeds the results back, and repeats until the model stops calling
    tools or MAX_TOOL_ITERATIONS is reached.
    """
    for _ in range(MAX_TOOL_ITERATIONS):
        response = await anthropic_client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system if system is not None else anthropic.omit,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text":
                print(block.text)

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            return

        tool_results = [await _call_tool(mcp_client, b) for b in tool_uses]
        messages.append({"role": "user", "content": tool_results})

    print(f"[Límite de {MAX_TOOL_ITERATIONS} llamadas a tools alcanzado sin respuesta final.]")


async def _cmd_stations(mcp_client: Client) -> str | None:
    try:
        result = await mcp_client.read_resource("bicimad://stations")
    except MCPError as e:
        print(f"Error leyendo el resource: {e}")
        return None
    texts = [getattr(c, "text", None) for c in result.contents]
    text = "\n".join(t for t in texts if t is not None)
    print(f"[Catálogo de estaciones cargado como contexto: {len(text)} caracteres]")
    return f"Catálogo completo de estaciones BiciMAD (bicimad://stations), en JSON:\n{text}"


async def _cmd_plan(mcp_client: Client, raw_args: str) -> str | None:
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError as e:
        print(f"Argumentos inválidos (debe ser JSON): {e}")
        return None
    try:
        result = await mcp_client.get_prompt("plan_trip", args)
    except MCPError as e:
        print(f"Error obteniendo el prompt: {e}")
        return None
    parts = [getattr(m.content, "text", None) for m in result.messages]
    return "\n".join(p for p in parts if p is not None)


async def _run() -> None:
    params = _server_params()
    anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    system: str | None = None
    messages: list[MessageParam] = []

    async with Client(params) as mcp_client:
        tools_result = await mcp_client.list_tools()
        tools = _mcp_tools_to_anthropic(tools_result.tools)
        print(f"Conectado al servidor bicimad ({len(tools)} tools). 'exit' para salir.")

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

            if line == "/stations" or line.startswith("/stations "):
                context = await _cmd_stations(mcp_client)
                if context is not None:
                    system = context
                continue

            if line == "/plan" or line.startswith("/plan "):
                raw_args = line[len("/plan") :].strip()
                user_text = await _cmd_plan(mcp_client, raw_args)
                if user_text is None:
                    continue
                line = user_text

            messages.append({"role": "user", "content": line})
            try:
                await _run_turn(anthropic_client, mcp_client, tools, messages, system)
            except Exception as e:  # noqa: BLE001 — REPL keeps running after a bad turn
                print(f"Error: {e}")

    print("Sesión cerrada.")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
