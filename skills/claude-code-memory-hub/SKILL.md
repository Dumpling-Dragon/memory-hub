---
name: claude-code-memory-hub
description: Search prior AI chats and local project history through the unified local Memory Hub, including configured Gemini and Yuanbao exports.
---

# Unified Memory Hub

Use Memory Hub for local, read-only history questions. The service address, authentication requirement and privacy mode are runtime configuration; obtain the current copyable prompt from GET /api/agent-prompt instead of assuming a port or machine path.

Prefer MCP tools: memory_search, memory_continue, memory_project_context, and memory_ask. Run the configured project environment with python -m memory_hub.mcp, or use the local HTTP endpoints returned by the service. Common endpoints are GET /api/search and POST /api/ask.

Results include source, title, locator, source_updated_at and metadata_json. Treat metadata_only as a pointer rather than recovered conversation text. Unknown dates remain unknown. Cite actual returned sources and avoid large raw dumps.

Queries are read-only retrieval. memory_ask may send recalled context to the user's configured model endpoint, so follow the service privacy boundary and the user's disclosure instructions. Never print credentials incidentally.

Web exports for Gemini and Yuanbao are read from configured web_export_roots; do not assume a personal export directory. Do not write, delete, or modify source records. Do not present migration databases or local backups as the active Memory Hub. Live vector coverage comes from /api/status.
