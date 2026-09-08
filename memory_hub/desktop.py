"""Desktop entry points (Windows).

The global Ctrl+Space hotkey and its Tk search popup were removed on
2026-08-26: text search is served by the web UI (/), the JSON API
(/api/search, /api/ask) and the MCP tools, which is what Memory Hub is
actually used for. The optional tray icon lives in tray.py and is still
started from cli.main().
"""
