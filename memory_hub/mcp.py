"""Minimal read-only MCP stdio bridge; no third-party MCP dependency required."""
from __future__ import annotations

import json
import sys

from .config import load_config
from .db import init_db, search
from . import ai


TOOLS = [
    {"name": "memory_search", "description": "Search the local Memory Hub index.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "source": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "memory_continue", "description": "Find recent context for a project or topic.", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "memory_project_context", "description": "Find records for a workspace path or project name.", "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]}},
    {"name": "memory_ask", "description": "Answer a question using local Memory Hub records, with citations. Requires optional local AI configuration.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["question"]}},
]


def _response(message_id: object, result: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": message_id, "result": result}, ensure_ascii=False)


class RPCError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def validate_tool(params):
    if not isinstance(params, dict):
        raise RPCError(-32602, "Invalid parameters")
    name = params.get("name")
    tool = next((tool for tool in TOOLS if tool["name"] == name), None)
    if tool is None:
        raise RPCError(-32602, "Unknown tool")
    arguments = params.get("arguments", {})
    schema = tool["inputSchema"]
    if not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]) or any(key not in arguments for key in schema.get("required", [])):
        raise RPCError(-32602, "Invalid tool arguments")
    for key, value in arguments.items():
        if key == "limit":
            valid = type(value) is int and 1 <= value <= (20 if name == "memory_ask" else 100)
        else:
            valid = isinstance(value, str) and bool(value.strip()) and len(value) <= 4000
        if not valid:
            raise RPCError(-32602, f"Invalid {key}")
    return name, arguments


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    init_db()
    for line in sys.stdin:
        message_id = None
        try:
            try:
                request = json.loads(line)
            except ValueError as error:
                raise RPCError(-32700, "Parse error") from error
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                raise RPCError(-32600, "Invalid request")
            method = request.get("method")
            message_id = request.get("id")
            if "id" not in request:
                continue
            if method == "initialize":
                print(_response(message_id, {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "memory-hub", "version": "0.1.0"}}), flush=True)
            elif method == "tools/list":
                print(_response(message_id, {"tools": TOOLS}), flush=True)
            elif method == "tools/call":
                name, arguments = validate_tool(request.get("params", {}))
                if name == "memory_project_context":
                    rows = search("", project=arguments.get("project"), limit=20)
                    text = json.dumps(rows, ensure_ascii=False)
                elif name == "memory_ask":
                    text = json.dumps(ai.ask(load_config(), arguments.get("question", ""), arguments.get("limit", 10)), ensure_ascii=False)
                else:
                    rows = search(arguments.get("query", ""), arguments.get("source"), limit=arguments.get("limit", 12))
                    text = json.dumps(rows, ensure_ascii=False)
                print(_response(message_id, {"content": [{"type": "text", "text": text}]}), flush=True)
            elif method == "ping":
                print(_response(message_id, {}), flush=True)
            else:
                raise RPCError(-32601, "Method not found")
        except RPCError as error:
            print(json.dumps({"jsonrpc": "2.0", "id": message_id, "error": {"code": error.code, "message": error.message}}), flush=True)
        except Exception as error:
            print(_response(message_id, {"isError": True, "content": [{"type": "text", "text": str(error)}]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
