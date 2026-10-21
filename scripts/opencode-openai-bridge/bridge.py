#!/usr/bin/env python3
"""OpenAI-compatible chat API in front of `opencode serve`.

Answers GET /v1/models and POST /v1/chat/completions in the OpenAI shape and
turns each completion into one OpenCode session: create, prompt, delete. The
models are whatever the OpenCode CLI on this machine is signed into, named
`<providerID>/<modelID>` (opencode/deepseek-v4.1-flash-free, deepseek/deepseek-flash).
The bridge owns no key: OpenCode's credentials stay in OpenCode.

Settings (environment):
  BRIDGE_HOSTS          addresses to listen on, comma separated (default 127.0.0.1,172.17.0.1:
                        the host and the Docker bridge, which containers reach as host.docker.internal)
  BRIDGE_PORT           port (default 3002)
  BRIDGE_TOKEN          when set, requests must send `Authorization: Bearer <token>`; empty accepts any bearer
  OPENCODE_URL          the OpenCode server (default http://127.0.0.1:4097)
  BRIDGE_AGENT          the OpenCode agent the sessions use (default explore: read-only, small prompt,
                        and a built-in one, which OpenCode's free tier requires)
  BRIDGE_DEFAULT_MODEL  model for `auto` or a missing model (default opencode/deepseek-v4.1-flash-free)
  BRIDGE_TIMEOUT        seconds to wait for an answer (default 80: under Workflow Studio's 90 s step timeout)
  BRIDGE_NATIVE_SCHEMA  1 tries OpenCode's native json_schema output first (default 0: the schema is
                        written into the instructions and the JSON read from the text)
  BRIDGE_DENY           permissions denied in every session (default edit,bash,task,webfetch,websearch,external_directory)

Only stdlib. Logs one line per request: ids, model, status, tokens; never the text.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOSTS = [h.strip() for h in os.environ.get("BRIDGE_HOSTS", "127.0.0.1,172.17.0.1").split(",") if h.strip()]
PORT = int(os.environ.get("BRIDGE_PORT", "3002"))
TOKEN = os.environ.get("BRIDGE_TOKEN", "")
OPENCODE_URL = os.environ.get("OPENCODE_URL", "http://127.0.0.1:4097").rstrip("/")
AGENT = os.environ.get("BRIDGE_AGENT", "explore")
DEFAULT_MODEL = os.environ.get("BRIDGE_DEFAULT_MODEL", "opencode/deepseek-v4.1-flash-free")
TIMEOUT = float(os.environ.get("BRIDGE_TIMEOUT", "80"))
# OpenCode's native structured output (a forced tool call) hung or failed on the free
# models under the explore agent (2026-10-19), so the schema goes into the
# instructions unless this is set to 1.
NATIVE_SCHEMA = os.environ.get("BRIDGE_NATIVE_SCHEMA", "0") == "1"
# Tools the sessions may never use. The read-only ones (read, glob, grep, list) stay
# defined: OpenCode's free tier refuses a request that carries no tools at all.
DENY = [p.strip() for p in os.environ.get("BRIDGE_DENY", "edit,bash,task,webfetch,websearch,external_directory").split(",") if p.strip()]

log = logging.getLogger("bridge")


class BridgeError(Exception):
    def __init__(self, status, message, kind="invalid_request_error", code=None):
        super().__init__(message)
        self.status, self.message, self.kind, self.code = status, message, kind, code


# --- OpenCode -----------------------------------------------------------------

def opencode(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(OPENCODE_URL + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read()
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")[:300]
        raise BridgeError(502, "OpenCode answered %s on %s %s: %s" % (exc.code, method, path, text), "server_error")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BridgeError(502, "OpenCode is not reachable at %s: %s" % (OPENCODE_URL, exc), "server_error")
    return json.loads(raw) if raw else None


def list_models():
    data = opencode("GET", "/config/providers")
    models = []
    for provider in data.get("providers", []):
        for model_id, model in (provider.get("models") or {}).items():
            cost = model.get("cost")
            models.append({
                "id": "%s/%s" % (provider["id"], model_id),
                "object": "model",
                "created": 0,
                "owned_by": provider["id"],
                "name": model.get("name") or model_id,
                "free": bool(cost) and not (cost.get("input") or cost.get("output")),
                "context": (model.get("limit") or {}).get("context"),
            })
    return models


def split_model(name):
    name = (name or "auto").strip()
    if name in ("", "auto"):
        name = DEFAULT_MODEL
    if "/" not in name:
        raise BridgeError(400, "model must be <provider>/<model>, e.g. %s" % DEFAULT_MODEL, code="model_not_found")
    provider_id, model_id = name.split("/", 1)
    return name, provider_id, model_id


# --- OpenAI request -> OpenCode prompt -----------------------------------------

def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content
                         if isinstance(part, dict) and part.get("type") in (None, "text"))
    return "" if content is None else str(content)


def build_prompt(messages):
    """System messages become OpenCode's `system`; the rest one user text: the
    last user message alone, or a transcript when the conversation has turns."""
    if not isinstance(messages, list) or not messages:
        raise BridgeError(400, "messages must be a non-empty list")
    system, turns = [], []
    for message in messages:
        role, text = message.get("role"), content_text(message.get("content"))
        if role in ("system", "developer"):
            system.append(text)
        elif role in ("user", "assistant"):
            turns.append((role, text))
        elif role == "tool":
            turns.append(("user", "Tool result:\n" + text))
    if not turns:
        raise BridgeError(400, "messages need at least one user message")
    if len(turns) == 1:
        prompt = turns[0][1]
    else:
        lines = ["The conversation so far, then answer the last user message."]
        for role, text in turns:
            lines.append("%s: %s" % ("User" if role == "user" else "Assistant", text))
        prompt = "\n\n".join(lines)
    return "\n\n".join(system), prompt


def schema_of(response_format):
    """(native OpenCode format, instruction for the prompt fallback, required keys)."""
    if not isinstance(response_format, dict):
        return None, "", []
    kind = response_format.get("type")
    if kind == "json_schema":
        spec = response_format.get("json_schema") or {}
        schema = spec.get("schema") or response_format.get("schema") or {}
        instruction = ("Answer only with a JSON object that matches this JSON schema, with no words "
                       "or markdown around it:\n" + json.dumps(schema, ensure_ascii=False))
        # retryCount 0: one refusal is enough, the bridge's own fallback takes over.
        return {"type": "json_schema", "schema": schema, "retryCount": 0}, instruction, list(schema.get("required") or [])
    if kind == "json_object":
        return None, "Answer only with a JSON object, with no words or markdown around it.", []
    return None, "", []


FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text, required):
    """The JSON object in a model's text: fenced, or from the first { to the last }."""
    candidates = [m.strip() for m in FENCE.findall(text)] + [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict) and all(key in value for key in required):
            return value
    raise BridgeError(502, "the model's answer is not the JSON the schema asks for", "server_error")


# --- OpenCode answer -> OpenAI response -----------------------------------------

# Models whose native structured output failed once in this process: they get the
# instruction form straight away, which saves the failed round trip (a minute on a
# thinking model) that would push a caller past its own timeout.
NATIVE_FAILED = set()

ERROR_STATUS = {"ProviderAuthError": 401, "ContextOverflowError": 413, "ContentFilterError": 400,
                "MessageOutputLengthError": 502, "StructuredOutputError": 502, "MessageAbortedError": 502}


def raise_for(error):
    name = error.get("name") or "UnknownError"
    data = error.get("data") or {}
    message = data.get("message") or name
    if name == "APIError":
        status = data.get("statusCode") or 502
        status = status if 400 <= int(status) < 600 else 502
        raise BridgeError(status, message, "upstream_error", code=name)
    raise BridgeError(ERROR_STATUS.get(name, 502), message, "upstream_error", code=name)


def ask(agent, provider_id, model_id, system, prompt, fmt, variant):
    session = opencode("POST", "/session", {
        "title": "bridge " + time.strftime("%Y-%m-%d %H:%M:%S"), "agent": agent,
        "permission": [{"permission": name, "pattern": "*", "action": "deny"} for name in DENY]})
    sid = session["id"]
    body = {"agent": agent, "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": prompt}]}
    if system:
        body["system"] = system
    if fmt:
        body["format"] = fmt
    if variant:
        body["variant"] = variant
    try:
        return sid, opencode("POST", "/session/%s/message" % sid, body, timeout=TIMEOUT)
    except BridgeError:
        # A session still waiting (a permission question, a slow model) is stopped before it goes.
        try:
            opencode("POST", "/session/%s/abort" % sid, timeout=5)
        except BridgeError:
            pass
        raise
    finally:
        try:
            opencode("DELETE", "/session/%s" % sid)
        except BridgeError as exc:  # the answer matters more than the cleanup
            log.warning("session %s not deleted: %s", sid, exc.message)


def complete(request):
    if request.get("stream"):
        raise BridgeError(400, "streaming is not supported; send stream: false")
    name, provider_id, model_id = split_model(request.get("model"))
    system, prompt = build_prompt(request.get("messages"))
    fmt, instruction, required = schema_of(request.get("response_format"))
    variant = request.get("reasoning_effort") if request.get("reasoning_effort") in ("low", "high", "max") else None
    agent = AGENT  # never from the request: a caller could name an agent that acts

    if fmt and (not NATIVE_SCHEMA or name in NATIVE_FAILED):
        fmt = None
    native = bool(fmt)
    if fmt is None and instruction:
        system = (system + "\n\n" + instruction).strip()
    try:
        sid, answer = ask(agent, provider_id, model_id, system, prompt, fmt, variant)
        error = (answer.get("info") or {}).get("error")
        if error:
            raise_for(error)
    except BridgeError as exc:
        # Some models cannot take OpenCode's structured output (a forced tool call):
        # ask once more with the schema written into the instructions.
        # DeepSeek's thinking mode refuses the forced tool call (400 tool_choice); other
        # models answer in text and OpenCode gives up (StructuredOutputError).
        structured_failed = (exc.status == 400 and "tool_choice" in exc.message) or exc.code == "StructuredOutputError"
        if not (native and structured_failed):
            raise
        native = False
        NATIVE_FAILED.add(name)
        log.info("native structured output failed for %s (%s); using the instruction form from now on", name, exc.code or exc.status)
        sid, answer = ask(agent, provider_id, model_id, (system + "\n\n" + instruction).strip(), prompt, None, variant)
        error = (answer.get("info") or {}).get("error")
        if error:
            raise_for(error)

    info = answer.get("info") or {}
    parts = answer.get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text" and not p.get("synthetic") and not p.get("ignored"))
    structured = info.get("structured")
    if structured is None and (instruction or fmt):
        structured = extract_json(text, required)
    content = json.dumps(structured, ensure_ascii=False) if structured is not None else text

    tokens = info.get("tokens") or {}
    finish = next((p.get("reason") for p in parts if p.get("type") == "step-finish"), None) or info.get("finish") or "stop"
    finish_reason = "length" if finish == "length" else "stop"
    usage = {
        "prompt_tokens": int(tokens.get("input") or 0) + int((tokens.get("cache") or {}).get("read") or 0),
        "completion_tokens": int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
        "completion_tokens_details": {"reasoning_tokens": int(tokens.get("reasoning") or 0)},
    }
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
    response = {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": name,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
        "usage": usage,
        "opencode": {"session": sid, "agent": agent, "structured_output": "native" if native else ("prompt" if (fmt or instruction) else None)},
    }
    return response, {"X-Routed-Via": "%s/%s" % (info.get("providerID") or provider_id, info.get("modelID") or model_id)}


# --- HTTP -----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "opencode-openai-bridge/0.1"

    def log_message(self, fmt, *args):  # one line per request is written by the handlers
        pass

    def _send(self, status, body, headers=None):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, exc):
        self._send(exc.status, {"error": {"message": exc.message, "type": exc.kind, "code": exc.code}})

    def _authorized(self):
        if not TOKEN:
            return True
        header = self.headers.get("Authorization", "")
        return header.startswith("Bearer ") and header[7:].strip() == TOKEN

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            if path in ("/health", ""):
                try:
                    opencode("GET", "/global/health", timeout=5)
                    up = True
                except BridgeError:
                    up = False
                self._send(200 if up else 503, {"ok": up, "opencode": OPENCODE_URL, "agent": AGENT})
                return
            if path == "/v1/models":
                if not self._authorized():
                    raise BridgeError(401, "missing or wrong bearer token", "authentication_error")
                self._send(200, {"object": "list", "data": list_models()})
                log.info("GET /v1/models 200")
                return
            raise BridgeError(404, "no route %s" % path)
        except BridgeError as exc:
            log.info("GET %s %s %s", path, exc.status, exc.code or exc.kind)
            self._error(exc)

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/")
        started = time.time()
        model = "-"
        try:
            if path != "/v1/chat/completions":
                raise BridgeError(404, "no route %s" % path)
            if not self._authorized():
                raise BridgeError(401, "missing or wrong bearer token", "authentication_error")
            length = int(self.headers.get("Content-Length") or 0)
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                raise BridgeError(400, "the body is not JSON")
            model = request.get("model") or "auto"
            response, headers = complete(request)
            self._send(200, response, headers)
            usage = response["usage"]
            log.info("POST %s 200 model=%s via=%s %.1fs prompt=%s completion=%s structured=%s",
                     path, response["model"], headers["X-Routed-Via"], time.time() - started,
                     usage["prompt_tokens"], usage["completion_tokens"], response["opencode"]["structured_output"])
        except BridgeError as exc:
            log.info("POST %s %s model=%s %.1fs %s: %s", path, exc.status, model, time.time() - started,
                     exc.code or exc.kind, exc.message[:200])
            self._error(exc)
        except Exception as exc:  # noqa: BLE001 - the client gets a 500, the log the cause
            log.exception("POST %s failed", path)
            self._error(BridgeError(500, "bridge error: %s" % exc, "server_error"))


def serve():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    servers = []
    for host in HOSTS:
        try:
            server = ThreadingHTTPServer((host, PORT), Handler)
        except OSError as exc:
            log.error("cannot listen on %s:%s: %s", host, PORT, exc)
            continue
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True, name="listen-" + host).start()
        log.info("listening on http://%s:%s/v1 -> %s (agent %s, default model %s, token %s)",
                 host, PORT, OPENCODE_URL, AGENT, DEFAULT_MODEL, "required" if TOKEN else "any")
    if not servers:
        raise SystemExit(1)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        for server in servers:
            server.shutdown()


if __name__ == "__main__":
    serve()
