# Examples

## `mcp_server.py`

The library behind an MCP server, in one file. It exists so a reader can see what an
adapter is responsible for — building credentials, initializing the boundary once,
translating `DriveError` into the wire's idea of a failure, and keeping the process to one
corpus. Those four are marked in the module docstring.

It is an example, not a product: it authenticates nobody, so Streamable HTTP binds loopback
only, and nothing here is packaged or published. The library itself imports no MCP in any
configuration — the SDK is a development dependency of this repository, for this file.

Configure a corpus as described in the top-level `README.md`, then:

```bash
just example-stdio
just example-http     # http://127.0.0.1:8000/mcp
```

To drive it by hand, point the MCP Inspector at either transport:

```bash
npx @modelcontextprotocol/inspector             # then connect to the HTTP endpoint
npx @modelcontextprotocol/inspector --cli just example-stdio --method tools/list
```

Its four tools — `search_documents`, `list_folder`, `get_metadata`, `read_document` — map
one to one onto `DocumentService`, documented in `docs/api.md`.
