# gdrive-scoped

Read-only access to one configured Google Drive folder and its current descendants, for LLM
tools. The folder may be in My Drive or in one Shared Drive.

It is a **library**. The core takes credentials and a location as arguments, reads no
environment variable, and imports no MCP — so an adapter decides what it becomes: an MCP
server, a hosted provider, an agent's toolset, a script.

```python
from gdrive_scoped import DocumentService, ScopedDrive, create_gateway
from gdrive_scoped.credentials import refresh_token_credentials
from gdrive_scoped.location import DriveKind, DriveLocation

location = DriveLocation(DriveKind.SHARED_DRIVE, shared_drive_id="0A...")
gateway = create_gateway(refresh_token_credentials(...), location)
scoped = ScopedDrive(gateway, location, root_folder_id="1U...")
await scoped.initialize()

service = DocumentService(scoped)
results = await service.search_documents("safety report", limit=10)
chunk = await service.read_document(results.items[0].id)
```

`examples/mcp_server.py` is the same thing behind an MCP server, and is the shortest complete
statement of what an adapter is responsible for.

## What the boundary is

Items are addressed by their Drive file ID. What bounds the corpus is not the shape of the
handle but a **live ancestry proof on every call**: the item is re-read from Drive, its
location and parent checked against a map of the subtree, and refused if it does not belong.
A file that moves out, is trashed, or loses its permissions is refused immediately — the one
exception being a *folder* moved out of the subtree, which keeps serving until the folder map
expires (ADR 0005; set the TTL to `0` to close that window at the cost of an enumeration per
request).

The Drive Identity needs the root folder shared with it, and nothing more: no query addresses
a Shared Drive by ID, so membership is not required (ADR 0010). Discovery enumerates every
folder the identity can see and keeps the corpus, so its cost is the identity's reach — keep
that reach to the corpus.

Every decision, allow or refuse, is one structured record on the `gdrive_scoped.audit` logger,
and so is every completed operation, carrying the caller when the adapter names one.

`docs/architecture.md` has the layers, `docs/api.md` the service contract, `docs/adr/` the
durable decisions, and `docs/gotchas.md` the Drive-specific traps that shaped both.

## Install

```bash
pip install gdrive-scoped              # the library
pip install "gdrive-scoped[bench]"     # plus the benchmark harness
```

Python 3.12+.

## Development

Requires `uv` and `just`. Without `just`, every recipe is one `uv run` — read the `justfile`.

```bash
just setup
just check     # format, lint, types, tests
```

The default suite is hermetic and never contacts Google Drive.

## Running against a real Drive

Everything below needs one Drive corpus described in the environment: copy `.env.example` to
`.env` (git-ignored) and fill it in. These variables are read only by this repository's own
entry points (`gdrive_scoped.env`), never by the library:

| Variable | |
| --- | --- |
| `GDRIVE_DRIVE_KIND` | `shared_drive` or `my_drive`. Required — there is no default: it is an assertion about where the root lives, checked against every item, and a defaulted assertion asserts nothing. |
| `GDRIVE_SHARED_DRIVE_ID` | Required for `shared_drive`, and must be unset for `my_drive`. Asserted on every item; never sent as a query parameter, so membership of the drive is not needed. |
| `GDRIVE_ROOT_FOLDER_ID` | The folder ID from a `https://drive.google.com/drive/folders/<id>` URL. The alias `root` is refused: the whole of a Drive is not a corpus. |
| `GDRIVE_FOLDER_MAP_TTL_SECONDS` | Optional, default 60. `0` re-enumerates every request. |
| `GDRIVE_OAUTH_CLIENT_ID`<br>`GDRIVE_OAUTH_CLIENT_SECRET`<br>`GDRIVE_OAUTH_REFRESH_TOKEN` | A stored refresh token for the Drive Identity. Set all three, or none to fall back to Application Default Credentials. |

For a developer credential instead of a refresh token:

```bash
gcloud auth application-default login \
  --client-id-file=/absolute/path/to/client_secret.json \
  --scopes=https://www.googleapis.com/auth/drive.readonly
```

Keep OAuth client files out of the repository; `.gitignore` rejects the usual filenames as a
second line of defence.

Every entry point prints `identity: <address>` before anything else. A credential carries no
name, and the two identities describe different deployments: the bot user's reach is the
corpus, a developer's is everything they can see — and discovery enumerates that reach. The
census that costs the bot user one page cost one developer identity 9,878 folders in 10
pages and 13 s. `just` loads `.env` on its own, and a variable already exported in the shell
wins; an entry point run directly with `uv run` needs them exported first (`set -a; source
.env; set +a`). With no `GDRIVE_OAUTH_*` set, the entry points fall back to Application
Default Credentials, and the identity line is what tells you.

### What is in the corpus

```bash
just census
```

Folder count and depth, file count, the MIME histogram, and the share any extractor can read.
It opens no document, so it is cheap enough to run against production — and it is the only
honest way to prioritise extractors, since the formats a corpus holds are never the ones
anybody guesses.

### Live tests

```bash
just test-live
```

Read-only metadata and listing against the configured root. Naming a known document adds a
recursive search, a metadata revalidation and one bounded read:

```bash
export GDRIVE_LIVE_SEARCH_QUERY="distinctive keywords"
export GDRIVE_LIVE_EXPECTED_SOURCE="Nested Folder/Document.md"
```

### Retrieval evaluation and benchmark

```bash
just evaluate     # do the expected sources come back at all
just benchmark    # how long search and reads take, with a regression gate
```

Both read the same 5–10 cases. The evaluation needs them hand-written — only a person knows what a corpus should be able to answer. The benchmark derives its own when the file is absent, one readable document per MIME type, each verified findable, so it runs against a corpus nobody has written cases for yet. See `evaluation/README.md`.

### The example server

```bash
just example-stdio    # for an MCP host, or `npx @modelcontextprotocol/inspector`
just example-http     # loopback Streamable HTTP on http://127.0.0.1:8000/mcp
```

See `examples/README.md`. It authenticates nobody, so it binds loopback only.
