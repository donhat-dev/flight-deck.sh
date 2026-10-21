# OpenCode OpenAI bridge

An OpenAI-compatible chat API in front of the OpenCode CLI on this machine. Anything that speaks `/v1/chat/completions` (Workflow Studio's LLM connector, an OpenAI SDK, curl) can use the models OpenCode is signed into, the free group `opencode/*-free` among them, without a provider key of its own. Brief: `docs/briefs/2026-10-19-opencode-openai-bridge.md`.

## Run

```bash
./run.sh                      # opencode serve in an empty work dir + the bridge, foreground
curl -s http://127.0.0.1:3002/health
curl -s http://127.0.0.1:3002/v1/models -H 'Authorization: Bearer any' | head -c 400
```

The bridge listens on `127.0.0.1:3002` and `172.17.0.1:3002`. Containers reach it as `http://host.docker.internal:3002/v1` (the `odoo19` compose maps that name to the host gateway). Settings are environment variables, listed at the top of `bridge.py`: `BRIDGE_PORT`, `BRIDGE_HOSTS`, `BRIDGE_TOKEN` (unset: any bearer is accepted; the bridge is reachable only from this machine and its containers), `BRIDGE_AGENT`, `BRIDGE_DEFAULT_MODEL`, `BRIDGE_DENY`, `OPENCODE_URL`.

OpenCode's own sign-in is what the bridge uses: `opencode providers` shows it, the bridge never reads `~/.local/share/opencode/auth.json`.

To stop both by hand: Ctrl-C in the terminal running `run.sh`, or `pkill -f "[o]pencode-openai-bridge/bridge.py"` and `pkill -f "[o]pencode serve"` from a shell whose own command line does not contain those words (a `pkill -f` inside a longer command that also starts the server matches itself and kills the shell).

## What a request becomes

- `model`: `<providerID>/<modelID>` as `opencode models` prints it; `auto` or none is `BRIDGE_DEFAULT_MODEL` (`opencode/deepseek-v4.1-flash-free`).
- `messages`: system messages become the session's `system`; one user message is the prompt as is; a longer conversation is written out as a transcript and the model answers its last message.
- `response_format: json_schema` and `json_object`: the schema (or "a JSON object") is written into the instructions and the JSON is read out of the text; the response says `opencode.structured_output: prompt`. `BRIDGE_NATIVE_SCHEMA=1` tries OpenCode's native structured output first (a forced tool call) and falls back per model when it is refused; off by default because the free DeepSeek model refuses the tool call and the free GLM model answered in text, and both attempts hung for minutes under `explore`.
- `reasoning_effort` `low` / `high` / `max` is passed as the model variant.
- `stream: true` is refused with 400: nothing here streams.
- Each request is one OpenCode session, created with a `permission` ruleset that denies `edit`, `bash`, `task`, `webfetch`, `websearch` and `external_directory`, and deleted when the answer is back (aborted first when it timed out).
- The answer carries `X-Routed-Via: <providerID>/<modelID>`; `usage` comes from OpenCode's token counts (`completion_tokens` includes reasoning tokens).

## Why the `explore` agent

OpenCode's free tier answers 403 ("can only be used from within OpenCode") unless the request looks like OpenCode's own: a built-in agent with its system prompt and its tool definitions. A custom agent, a built-in agent with `tools: {"*": false}`, and `permission: {"*": "deny"}` were all refused (2026-10-19). `explore` is built in, has only glob, grep and read (the model said so when asked to create a file, and no file appeared) and is the cheapest: about 1.6k prompt tokens per call against 14–16k for `plan` or `build`. The per-session deny rules are a second guard for an agent that does have acting tools; they have not been exercised against one, because `explore` has none. The agent name comes from `BRIDGE_AGENT` only, never from a request.

## Errors

OpenAI shape, `{"error": {"message", "type", "code"}}`: 400 for a bad request or `stream`, 401 for a wrong bearer or OpenCode's `ProviderAuthError`, 413 for a context overflow, the provider's own 4xx for an `APIError`, 502 when OpenCode or the model fails. The log (stdout) has one line per request with ids, model, status and token counts, never the text.
