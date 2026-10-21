# Flight AI Chat

MuK AI chat on Flightdoo (`flight_ai_chat`). Extends `muk_ai` + `muk_mcp` (vendored, unmodified) with:

- **Per-chat tool switches** (FC-1): a wrench button on the popup window header lists the agent's tools grouped Read/Write and turns them off for that chat only — a deny list stored on the session (`flight_disabled_tool_names`), enforced in the catalogue (schemas, prompt block, `tool_load`) and at dispatch.
- **Session tab** (FC-2): the full chat's side panel gets a Session tab showing the pinned screen and the same tool switches.
- **Editor screen context** (FC-3): the open flow in the Workflow Studio editor (name, id, selected steps) pins as `client_action` view context and lands in the `<ui_ctx>` prompt tag and the context chip.
- **LLM connector provider** (FC-4): a `muk_ai.provider` record "Workflow Studio LLM connector" points at the LLM connector (`workflow_connector_llm.connector_llm`, by default the OpenCode bridge at `http://host.docker.internal:3002/v1`) and reads the bearer token from the connector account's `api_key` secret on every request — the key is never copied onto the provider.

## Configuration

- Provider, model (`opencode/deepseek-v4.1-flash-free`), and the connector link ship in `data/provider.xml` (noupdate).
- The endpoint is the connector's **Base URL**; the key is the connector account's **API Key** secret — set them once on the connector/account, never on the provider.
- On the provider form, "Workflow Studio LLM connector" shows connector/account fields instead of API Key/Region. Pick another account there, or leave the field empty for the connector's default.

## Known gap

The provider class is MuK's OpenAI one, which calls `/v1/responses`; the OpenCode bridge serves only `/v1/chat/completions`, so the chat assistant cannot talk to the bridge yet. A Chat Completions adapter is not built.

## Checks

- FC-1: popup header Tools button; toggles persist per session; off tools are neither offered nor callable.
- FC-2: full chat → side panel → Session tab shows screen + the same switches.
- FC-3: with the editor open, the chat's context chip reads the flow name; `<ui_ctx>` carries it.
- FC-4: `provider._get_client().headers()` bears the account secret; `api_url` is the connector URL.
- FC-5: `tests/` (`post_install`) pass on `wfs_port`.
