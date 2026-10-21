# OpenCode as an OpenAI-compatible endpoint

Feature: OpenCode OpenAI bridge
Goal: Workflow Studio's LLM connector, and anything else that speaks the OpenAI chat API, can use the models the OpenCode CLI on this machine is signed into, the free group (`opencode/*-free`) first, without a provider key of its own.
Now: the LLM connector points at FreeLLMAPI, which has no provider key yet; OpenCode 1.18.32 lists ten free models (DeepSeek V4.1 Flash, GLM-5.3 Flash, …) and a DeepSeek account, but only through its own CLI and session API.
Direction: a small host service in front of `opencode serve`, answering `/v1/models` and `/v1/chat/completions` in the OpenAI shape (`response_format: json_schema` mapped onto OpenCode's structured output, `X-Routed-Via: <provider>/<model>`), listening on 127.0.0.1 and the Docker bridge address so the Odoo container reaches it as `host.docker.internal`; OWS gets a second connector record and an account, data only.
Constraints: no code in OWS; OpenCode's credentials stay in OpenCode (the bridge never reads `auth.json`); the bridge listens on no other address; one OpenCode session per request, deleted after; the agent it uses has no tools and runs in an empty directory; test against the live `flightdeck_odoo` only with data (a connector, an account, a probe flow archived after).
Done when: the checks OB-1 to OB-5 below hold.
Not now: streaming; multi-turn sessions kept on the bridge; images or files; running OpenCode in a container; replacing FreeLLMAPI (it stays the first connector for when its keys exist); a systemd unit.
Refs: OpenCode server OpenAPI (`GET /doc` of `opencode serve`); OWS brief `repos/open-workflow-studio/docs/briefs/2026-10-19-fill-with-ai.md` (FA-2, the connector); OWS ADR-021 §7 (another base URL is a duplicated connector); `flight-deck.sh/docker/freellmapi/compose.yml` (the first endpoint); session 70e3feaf.

## Done when

- OB-1 `GET /v1/models` with a bearer lists OpenCode's models as `provider/model` ids, the free ones among them.
- OB-2 `POST /v1/chat/completions` with `model: opencode/deepseek-v4.1-flash-free` returns the OpenAI shape (`choices[0].message.content`, `finish_reason`, `usage`, `model`) and the header `X-Routed-Via: opencode/deepseek-v4.1-flash-free`.
- OB-3 the same call with `response_format: {type: json_schema, …}` returns content that parses and has the schema's required keys; the answer's `opencode.structured_output` says whether OpenCode's native format (`native`) or the schema written into the instructions (`prompt`) produced it.
- OB-4 both calls answer from inside `flightdeck-odoo19` at `http://host.docker.internal:<port>/v1`.
- OB-5 on `flightdeck_odoo`: a connector "LLM (OpenCode)" with that base URL and an account whose Test Connection lists the models; a Chat step run on the free DeepSeek model shows `routed_via: opencode/deepseek-v4.1-flash-free` and a parsed `json`; the If step's Ask bar answers with `writer: model` through it.

## Definitions

- **Bridge**: the host process that translates the OpenAI chat API into OpenCode's session API; it owns no model and no key.
- **Free group**: OpenCode's `opencode/*-free` models, cost 0 in its provider list.

## Decisions

- Chose to front `opencode serve` (one long-lived process, HTTP session API) over spawning `opencode run` per request, because a run pays the CLI start each time and its JSON event stream has to be re-parsed, while the server answers `POST /session/{id}/message` synchronously with the assistant message and its parts.
- Chose the built-in `explore` agent with per-session deny rules (edit, bash, task, webfetch, websearch, external_directory) over an agent of the bridge's own, because OpenCode's free tier answers 403 "can only be used from within OpenCode" to a custom agent, to a built-in agent with `tools: {"*": false}` and to `permission: {"*": "deny"}` (all three probed 2026-10-19), while `explore` passes and costs about 1.6k prompt tokens against 14–16k for `plan` or `build`; its tools are glob, grep and read, and the model confirmed it has no write tool when asked to create a file.
- Chose to bind the bridge on 127.0.0.1 and 172.17.0.1 (what `host.docker.internal` resolves to inside `flightdeck-odoo19`; checked with a throwaway HTTP server: the container got 200), not 0.0.0.0, because the LAN must not reach the user's OpenCode account.
- Chose a second connector record "LLM (OpenCode)" on `flightdeck_odoo` rather than repointing the LLM connector, because ADR-021 §7 makes another base URL a duplicated connector and FreeLLMAPI stays the first.
- Chose the instruction form of `json_schema` by default (the schema written into the system message, the JSON read out of the text; `BRIDGE_NATIVE_SCHEMA=1` tries OpenCode's native `format` first, with `retryCount: 0` and a per-model memory of refusals), because the native form is a forced tool call that the free DeepSeek model refuses (400 "Thinking mode does not support this tool_choice"), the free GLM model answered in text under `explore` (`StructuredOutputError`, it worked once under `plan`), and both attempts then hung long enough (163 s, then 180 s) to push Workflow Studio's 90 s step past its timeout; OB-3 was reworded to say which form answered.
- Chose to refuse `stream: true` with 400 rather than fake a stream, because OpenCode's session API answers whole and Workflow Studio never streams.
- Chose one session per request, created with the deny rules, aborted on timeout and deleted after, because the bridge keeps no conversation and `opencode.db` must not grow with probes.
- Chose a bridge-level token that is optional (`BRIDGE_TOKEN`; unset accepts any bearer), because the bridge listens only on loopback and the Docker bridge address and the connector's key slot still needs some text; the account on Odoo holds a local placeholder, not a provider secret.
- Chose Python stdlib only (`http.server`, `urllib`) in `flight-deck.sh/scripts/opencode-openai-bridge/`, started by `run.sh` beside `opencode serve`, because the service has two routes and no dependency to manage; a container build was left out (OpenCode's sign-in lives in the user's home).
- Chose `usage.completion_tokens` to include reasoning tokens (with `completion_tokens_details.reasoning_tokens` beside it), because that is what the provider counts and bills.
- Chose a default bridge timeout of 80 s, because Workflow Studio's Chat step gives up at 90 s and a bridge that answers 502 first leaves the step with the cause instead of a bare read timeout.
- Noted for the probes: a connector's `_call` (the account's Test Connection and the shell) sends through `requests` and `_read_answer`, not through the LLM `_exchange` hook, so its answer has no `json` or `routed_via`; only a flow run shows them.
- Checked 2026-10-19: OB-1 (36 models, 10 of them `opencode/*-free`), OB-2 (`pong`, `X-Routed-Via: opencode/deepseek-v4.1-flash-free`, 1,606 prompt tokens, 3–6 s), OB-3 (DeepSeek and GLM free: parsed JSON with both keys, `structured_output: prompt`), OB-4 (from `flightdeck-odoo19`: `/v1/models` 200 and a completion 200 at `host.docker.internal:3002`), OB-5 (connector 5 "LLM (OpenCode)", account 4, Test Connection lists the models; Fill with AI run 8917 and Ask bar run 8921 with `writer: model`, `routed_via: opencode/deepseek-v4.1-flash-free`) hold. Found on the way and fixed in OWS: the Ask bar's savepoint around a run that commits (`workflow_connector_llm`, 26/26 tests, live upgraded). Both now run from `run.sh` in the tmux session `freellmapi` (work dir `~/.local/state/opencode-openai-bridge/work`, holding only `opencode.json`); after a reboot, `./run.sh` starts both again. The request-side `agent` field was removed: the agent comes from `BRIDGE_AGENT` only.

