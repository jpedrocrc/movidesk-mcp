<div align="center">

# Movidesk MCP Server

**Give Claude, ChatGPT or Codex read-only access to everything in your [Movidesk](https://www.movidesk.com) help desk.**

Tickets, customers, SLAs, time tracking, satisfaction surveys and more, through the
[Model Context Protocol](https://modelcontextprotocol.io).

[![PyPI](https://img.shields.io/pypi/v/movidesk-mcp-server?color=2563eb&label=PyPI)](https://pypi.org/project/movidesk-mcp-server/)
[![Python](https://img.shields.io/badge/python-3.10%2B-2563eb)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](LICENSE)
[![Read-only](https://img.shields.io/badge/access-read--only-16a34a)](#-security)
[![MCP](https://img.shields.io/badge/MCP-stdio-7c3aed)](https://modelcontextprotocol.io)

[Quick start](#-quick-start) · [What you can ask](#-what-you-can-ask) · [Available data](#-available-data) · [Tools](#-tools) · [Development](#-development)

</div>

---

## 🚀 Quick start

**1. Install**

```bash
pip install movidesk-mcp-server
```

**2. Get your API token**

In Movidesk, go to **Settings → Account → Parameters → Environment tab → API key**.

**3. Connect your AI client**

<details open>
<summary><b>Claude Desktop</b></summary>

Edit `claude_desktop_config.json`. On Windows it is in `%APPDATA%\Claude\`; on macOS, in `~/Library/Application Support/Claude/`.

```json
{
  "mcpServers": {
    "movidesk": {
      "command": "movidesk-mcp-server",
      "env": {
        "MOVIDESK_TOKEN": "your-token-here"
      }
    }
  }
}
```

Then quit Claude Desktop completely (on Windows: tray icon → **Quit**) and open it again.

</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add movidesk -e MOVIDESK_TOKEN=your-token-here -- movidesk-mcp-server
```

</details>

<details>
<summary><b>Codex CLI, Codex IDE extension and ChatGPT desktop app</b></summary>

All three share the same configuration file, so you only need to set it up once.

Using the Codex CLI:

```bash
codex mcp add movidesk --env MOVIDESK_TOKEN=your-token-here -- movidesk-mcp-server
```

Or edit `~/.codex/config.toml` (on Windows: `%USERPROFILE%\.codex\config.toml`):

```toml
[mcp_servers.movidesk]
command = "movidesk-mcp-server"

[mcp_servers.movidesk.env]
MOVIDESK_TOKEN = "your-token-here"
```

In the ChatGPT desktop app you can also add it from **Settings → MCP servers → Add server → STDIO**, then select **Restart**.

> [!NOTE]
> ChatGPT on the web can't use this server: it doesn't read local configuration and only connects to remote MCP servers. Use the ChatGPT desktop app or Codex instead.

</details>

**Updating**

```bash
pip install --upgrade movidesk-mcp-server
```

Restart your AI client afterwards. Your configuration stays the same.

## 💬 What you can ask

Ask in plain language, in any language:

- *"List the Movidesk endpoints and what each one returns"*
- *"Get ticket 12345 with all its actions and status history"*
- *"Summarize the tickets created in September: status, teams and SLA compliance"*
- *"Which tickets did the Support team resolve this week?"*
- *"Which contacts from ACME are still active?"*

## 📦 Available data

All endpoints below were tested against a live Movidesk account.

| Endpoint | What it returns |
|---|---|
| `tickets` | Tickets updated in the last 90 days: details, clients, actions, time entries, expenses, attachments, owner and status history, custom fields, assets |
| `tickets/past` | Older tickets, in the same format |
| `tickets/htmldescription` | HTML of a ticket's actions (recent or old tickets) |
| `tickets/merged` | Merged tickets, by ticket or by date range |
| `persons` | People, companies and departments (agents and clients), with e-mails, contacts, addresses and organizations |
| `services` | Service catalog |
| `activity` | Activities used for time tracking |
| `timeAgreement` | Hour contracts |
| `timeAgreementConsumption` | Hour consumption of a contract for a period |
| `survey/questions`, `survey/questions/{id}` | Satisfaction survey questions |
| `survey/responses` | Satisfaction survey responses (NPS, smiley faces, etc.) |
| `kb/article`, `article/{id}` | Knowledge base search and full articles |
| `storage/download` | Attachment download by hash |

Fields, expansions and limits for each endpoint are documented in [`ENDPOINTS.md`](src/movidesk_mcp/ENDPOINTS.md) (in Portuguese).

## 🧰 Tools

Tool names are in Portuguese; your AI assistant picks the right one from your request.

| Tool | Description |
|---|---|
| `movidesk_get` | Calls any `GET` endpoint and passes every parameter through unchanged. With `paginar=True` it paginates automatically up to `max_registros`. `arquivo_saida` saves the full result to disk. |
| `listar_endpoints` | Returns the API map (endpoints, fields, expansions, limits), so the assistant knows what it can request |
| `listar_tickets` | Lists tickets with filters, fields, expansions and a creation date range; `incluir_antigos=True` also searches `tickets/past` |
| `buscar_ticket` | Fetches a full ticket by number or protocol, falling back to `tickets/past`; `incluir_html=True` adds the actions' HTML |
| `listar_pessoas` | Lists people, companies and departments, with name search |
| `resumo_tickets` | Ticket statistics for a period: counts by status, team, owner, category, urgency, service, origin and day, plus resolution time and SLA compliance |

The resource `movidesk://endpoints` exposes the full API map as Markdown.

<details>
<summary><b>Example of a generic call</b></summary>

```json
{
  "endpoint": "tickets",
  "params": {
    "$select": "id,subject,status,createdDate",
    "$filter": "createdDate ge 2026-09-01T00:00:00.00z and ownerTeam eq 'Suporte'",
    "$expand": "owner,actions($select=id,origin;$expand=timeAppointments($expand=createdBy)),customFieldValues($expand=items)",
    "$orderby": "id desc"
  },
  "paginar": true,
  "max_registros": 500
}
```

</details>

## 🚦 Limits and behavior

| Topic | Behavior |
|---|---|
| **Rate limit** | Movidesk allows 10 requests/minute from 07:01 to 18:59 (Brasília time) and has no limit from 19:00 to 07:00. The server throttles calls locally during business hours. |
| **Error lockout** | 3 failed requests lock the API for 60 s, then 120 s, then 300 s. The server validates what it can before calling the API (for example, the `$select` that ticket listings require) and retries `429` responses after the `retry-after` delay. |
| **Errors** | `401`, `404`, `400` (with the API's message) and timeouts come back as JSON: `{"erro": ..., "mensagem": ...}`. Timeouts and `5xx` errors are retried with backoff. |
| **Large responses** | Responses longer than `MOVIDESK_MAX_CHARS` are truncated, with a hint to narrow the query with `$select`/`$filter`. Use `arquivo_saida` to save the complete result. |
| **Old tickets** | `/tickets` only returns tickets updated in the last 90 days; older ones live in `/tickets/past`. |

<details>
<summary><b>Optional environment variables</b></summary>

| Variable | Default | Purpose |
|---|---|---|
| `MOVIDESK_RATE_LIMIT` | `10` | Requests per minute (`0` disables local throttling) |
| `MOVIDESK_RATE_LIMIT_MODO` | `horario` | `horario` throttles only from 07:01 to 18:59; `sempre` throttles 24 h |
| `MOVIDESK_PAGE_SIZE` | `100` | Page size for automatic pagination |
| `MOVIDESK_MAX_RETRIES` | `3` | Retries on `429`, `5xx` and timeouts |
| `MOVIDESK_MAX_ESPERA` | `320` | Longest wait (seconds) accepted for a `429` retry |
| `MOVIDESK_TIMEOUT` | `60` | Per-request timeout (seconds) |
| `MOVIDESK_MAX_CHARS` | `60000` | Maximum response size before truncation |
| `MOVIDESK_LOG_LEVEL` | `WARNING` | Log level (always written to stderr) |

</details>

## 🔒 Security

- **Read-only.** The server only sends `GET` requests. The telephony endpoints (`asterisk_*`) are blocked because they record calls even though they use `GET`, and they stay blocked even with `permitir_nao_listados=True`.
- **Token handling.** The token is read only from `MOVIDESK_TOKEN`, never written to logs, and removed from every response and error message.
- **Real customer data.** The API key gives access to the whole account, so conversations may contain personal data (names, e-mails, tax IDs, ticket content). Request only what you need and follow your company's data protection policy.
- **One token per person.** Never share your token in chat, e-mail or GitHub issues. If it leaks, generate a new key in Movidesk right away.

## 📥 Other installation methods

Requires Python 3.10 or newer.

| Method | Command |
|---|---|
| PyPI | `pip install movidesk-mcp-server` |
| uv, without installing | `uvx movidesk-mcp-server` (in `claude_desktop_config.json`: `"command": "uvx", "args": ["movidesk-mcp-server"]`) |
| GitHub | `pip install git+https://github.com/jpedrocrc/movidesk-mcp` |

<details>
<summary><b>Windows: "command not found"</b></summary>

`pip` installs the executable in `...\Python3xx\Scripts`. If that folder is not on your `PATH`, your AI client cannot find `movidesk-mcp-server`. Use the full path to the `.exe` in `"command"`, or set `"command": "python"` and `"args": ["-m", "movidesk_mcp"]`.

</details>

## 🛠 Development

<details>
<summary><b>Project structure</b></summary>

| File | Contents |
|---|---|
| `pyproject.toml` | Package metadata, dependencies and the `movidesk-mcp-server` command |
| `src/movidesk_mcp/server.py` | MCP server and tools |
| `src/movidesk_mcp/ENDPOINTS.md` | API map. The server reads the JSON block at the end of this file, so supporting a new endpoint only takes adding it there. |
| `test_server.py` | Offline tests and coverage tests against the real API |

</details>

**Local setup**

```bash
git clone https://github.com/jpedrocrc/movidesk-mcp
cd movidesk-mcp
pip install -e .
```

With `-e`, code changes take effect without reinstalling; just restart your AI client. To switch back to the published version, run `pip uninstall -y movidesk-mcp-server`, then `pip install movidesk-mcp-server`.

> [!NOTE]
> The package requires `mcp<2`. Version 2.x of the `mcp` SDK renamed `FastMCP`, and the server has not been migrated yet.

**Tests**

```bash
python test_server.py --offline
```

Runs without network access. It checks the API map, tool registration and the safeguards (blocked writes, required `$select`, token masking).

```bash
python test_server.py
```

Needs `MOVIDESK_TOKEN`. It calls every mapped endpoint with `$top=1` (or `limit=1`/`pageSize=1`) and reuses the IDs it finds to test endpoints that need one. During business hours it takes about 2 minutes because of the rate limit.

To try the server in the MCP Inspector (requires `uv` and `npx`), run the command below and set `MOVIDESK_TOKEN` under *Environment Variables* before connecting:

```bash
mcp dev src/movidesk_mcp/server.py
```

<details>
<summary><b>Releasing a new version</b></summary>

1. Bump the version in `pyproject.toml` (`version`) and `src/movidesk_mcp/__init__.py` (`__version__`). PyPI never accepts the same version number twice.
2. Run both test modes.
3. Build:
   ```powershell
   Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue; uv build
   ```
4. Publish with a PyPI token scoped to the `movidesk-mcp-server` project:
   ```powershell
   $env:UV_PUBLISH_TOKEN = "pypi-..."; uv publish
   ```
5. Check in a clean environment: `pip install --upgrade movidesk-mcp-server`, then `pip show movidesk-mcp-server`.
6. Commit and push to GitHub.

If a published version is broken, **yank** it on PyPI (project → Releases → version → *Yank*) and publish the fix as a new version. Don't delete files: deletion is permanent and the file name can never be reused.

</details>

## 📋 Changelog

| Version | Changes |
|---|---|
| **1.0.1** | Requires `mcp<2`. With 1.0.0, a clean install pulled `mcp` 2.x and the server failed to start. |
| 1.0.0 | First release. **Yanked** from PyPI because of the issue above. |

## 📄 License

[MIT](LICENSE) © João Pedro Rodrigues
