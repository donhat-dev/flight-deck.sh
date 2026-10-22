# Flight AI Chat

MuK AI chat on Flightdoo (`flight_ai_chat`). Extends `muk_ai` + `muk_mcp` (vendored, unmodified) with:

- **Per-chat tool switches** (FC-1): a wrench button on the popup window header lists the agent's tools grouped Read/Write and turns them off for that chat only — a deny list stored on the session (`flight_disabled_tool_names`), enforced in the catalogue (schemas, prompt block, `tool_load`) and at dispatch.
- **Session tab** (FC-2): the full chat's side panel gets a Session tab showing the pinned screen and the same tool switches.
- **Editor screen context** (FC-3): the open flow in the Workflow Studio editor (name, id, selected steps) pins as `client_action` view context and lands in the `<ui_ctx>` prompt tag and the context chip.

## Checks

- FC-1: popup header Tools button; toggles persist per session; off tools are neither offered nor callable.
- FC-2: full chat → side panel → Session tab shows screen + the same switches.
- FC-3: with the editor open, the chat's context chip reads the flow name; `<ui_ctx>` carries it.
- FC-5: `tests/` (`post_install`) pass on `wfs_port`.

## Retired

- FC-4, the provider "Workflow Studio LLM connector" (2026-10-22): it read an `api_key` secret the LLM connector no longer has since the AI harness answers it (OWS `1273d2a`). `harness_muk_ai`'s provider "AI Harness" serves the chat instead; the old provider and model rows are removed by `migrations/19.0.1.2.0` (nothing referenced them).
