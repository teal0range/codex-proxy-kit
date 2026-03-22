#!/usr/bin/env python3
import argparse
import json
import re
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urljoin


def json_compact(obj):
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(obj)


def flatten_content(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(x for x in (flatten_content(item) for item in content) if x)
    if not isinstance(content, dict):
        return str(content)

    ctype = content.get("type")
    if ctype in ("input_text", "output_text", "text", "summary_text"):
        return content.get("text", "") or ""
    if ctype == "reasoning":
        summary = content.get("summary")
        if summary:
            return flatten_content(summary)
        return content.get("text", "") or ""
    if ctype in ("input_image", "image", "image_url"):
        return "[image]"
    if ctype in ("input_file", "file"):
        name = content.get("filename") or content.get("file_id") or content.get("id") or "file"
        return f"[{ctype}:{name}]"
    if "text" in content and isinstance(content.get("text"), str):
        return content["text"]
    slim = {k: v for k, v in content.items() if k not in ("annotations", "status", "id")}
    return json_compact(slim)


def normalize_text_part(part):
    if isinstance(part, str):
        return {"type": "input_text", "text": part}
    if not isinstance(part, dict):
        return {"type": "input_text", "text": str(part)}

    ptype = part.get("type")
    if ptype in ("input_text", "text"):
        return {"type": "input_text", "text": part.get("text", "") or ""}
    if ptype in ("output_text", "summary_text", "reasoning_text"):
        return {"type": "input_text", "text": part.get("text", "") or ""}
    if ptype == "reasoning":
        return {"type": "input_text", "text": flatten_content(part)}
    if ptype in ("input_image", "image_url"):
        out = {"type": "input_image"}
        if "image_url" in part:
            out["image_url"] = part.get("image_url")
        if "detail" in part:
            out["detail"] = part.get("detail")
        return out
    if ptype == "input_file":
        out = {"type": "input_file"}
        for key in ("file_id", "filename"):
            if key in part:
                out[key] = part[key]
        return out
    if "text" in part:
        return {"type": "input_text", "text": part.get("text", "") or ""}
    return {"type": "input_text", "text": json_compact(part)}


def stringify_tool_payload(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(x for x in (flatten_content(v) for v in value) if x)
    if isinstance(value, dict):
        if "text" in value and isinstance(value.get("text"), str):
            return value["text"]
        return json_compact(value)
    return str(value)


def canonical_tool_name(name):
    if not name:
        return "tool"
    if name.startswith("functions."):
        return name.split(".", 1)[1]
    return name


def normalize_tool_name(item, fallback):
    return item.get("name") or item.get("server_label") or item.get("recipient_name") or fallback


def normalize_tool_call_item(item):
    name = canonical_tool_name(normalize_tool_name(item, item.get("type") or "function"))
    call_id = item.get("call_id") or item.get("id") or f"call_{abs(hash(json_compact(item)))}"
    args = item.get("arguments")
    if args is None:
        args = item.get("input")
    if args is None:
        args = {}
    if not isinstance(args, str):
        args = json_compact(args)
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": args}


def normalize_tool_output_item(item):
    call_id = item.get("call_id") or item.get("id") or f"call_{abs(hash(json_compact(item)))}"
    output = item.get("output")
    if output is None:
        output = item.get("content")
    if output is None:
        output = item.get("result")
    return {"type": "function_call_output", "call_id": call_id, "output": stringify_tool_payload(output)}


def normalize_message_item(item):
    role = item.get("role", "user")
    if role == "system":
        role = "developer"
    content = item.get("content")
    if isinstance(content, list):
        norm = [normalize_text_part(part) for part in content]
    else:
        norm = [normalize_text_part(content)]
    return {"type": "message", "role": role, "content": norm}


def normalize_input_item_structured(item):
    if isinstance(item, str):
        return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": item}]}
    if not isinstance(item, dict):
        return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": str(item)}]}

    itype = item.get("type")
    if itype == "message" or "role" in item:
        return normalize_message_item(item)
    if itype in ("function_call", "custom_tool_call", "mcp_call", "local_shell_call", "shell_call", "apply_patch_call"):
        return normalize_tool_call_item(item)
    if itype in ("function_call_output", "custom_tool_call_output", "local_shell_call_output", "shell_call_output", "apply_patch_call_output", "mcp_approval_response"):
        return normalize_tool_output_item(item)
    return {"type": "message", "role": item.get("role", "user"), "content": [{"type": "input_text", "text": flatten_content(item)}]}


def normalize_tool_def(tool):
    if not isinstance(tool, dict):
        return None
    if tool.get("type") == "function":
        out = {"type": "function"}
        for key in ("name", "description", "parameters", "strict"):
            if key in tool and tool.get(key) is not None:
                out[key] = tool[key]
        if "function" in tool and isinstance(tool["function"], dict):
            fn = tool["function"]
            out["name"] = fn.get("name", out.get("name"))
            if fn.get("description") is not None:
                out["description"] = fn["description"]
            if fn.get("parameters") is not None:
                out["parameters"] = fn["parameters"]
        if out.get("name"):
            out["name"] = canonical_tool_name(out["name"])
        return out if out.get("name") else None

    name = canonical_tool_name(normalize_tool_name(tool, tool.get("type") or "tool"))
    params = tool.get("parameters") or tool.get("input_schema") or {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    out = {"type": "function", "name": name, "parameters": params}
    if tool.get("description") is not None:
        out["description"] = tool["description"]
    return out


def extract_text_from_response_message(item):
    texts = []
    for part in item.get("content") or []:
        if isinstance(part, dict) and part.get("type") == "output_text":
            text = part.get("text")
            if text:
                texts.append(text)
    return "\n".join(texts)


_TOOL_CALL_BLOCK_RE = re.compile(
    r"<\|tool_call_begin\|>\s*([A-Za-z0-9_.-]+)(?::\d+)?\s*<\|tool_call_argument_begin\|>\s*(\{.*?\})\s*<\|tool_call_end\|>",
    re.S,
)


def parse_tool_call_markup(text):
    calls = []
    if not text or "<|tool_call_begin|>" not in text:
        return calls
    for idx, match in enumerate(_TOOL_CALL_BLOCK_RE.finditer(text)):
        raw_name = match.group(1).strip()
        raw_args = match.group(2).strip()
        try:
            args = json_compact(json.loads(raw_args))
        except Exception:
            args = raw_args
        calls.append(
            {
                "type": "function_call",
                "call_id": f"{canonical_tool_name(raw_name)}:{idx}",
                "name": canonical_tool_name(raw_name),
                "arguments": args,
            }
        )
    return calls


def rewrite_response_output_for_codex(resp_obj):
    if not isinstance(resp_obj, dict) or not isinstance(resp_obj.get("output"), list):
        return resp_obj
    new_output = []
    changed = False
    for item in resp_obj["output"]:
        if not isinstance(item, dict):
            new_output.append(item)
            continue
        if item.get("type") == "function_call":
            out = dict(item)
            out["call_id"] = item.get("call_id") or item.get("id") or "tool_call_0"
            out["name"] = canonical_tool_name(item.get("name"))
            out["arguments"] = item.get("arguments") or "{}"
            new_output.append(out)
            changed = True
            continue
        if item.get("type") == "message":
            parsed_calls = parse_tool_call_markup(extract_text_from_response_message(item))
            if parsed_calls:
                new_output.extend(parsed_calls)
                changed = True
                continue
        new_output.append(item)
    if changed:
        resp_obj = dict(resp_obj)
        resp_obj["output"] = new_output
    return resp_obj


def rewrite_response_model_for_display(resp_obj, requested_model):
    if not isinstance(resp_obj, dict) or not requested_model:
        return resp_obj
    changed = False
    out = dict(resp_obj)
    if out.get("model") != requested_model:
        out["model"] = requested_model
        changed = True
    if isinstance(out.get("response"), dict):
        nested = dict(out["response"])
        if nested.get("model") != requested_model:
            nested["model"] = requested_model
            out["response"] = nested
            changed = True
    return out if changed else resp_obj


def sse_event(event, data):
    return (f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n").encode("utf-8")


def build_sse_from_response(resp_obj):
    chunks = []
    seq = 0
    created = dict(resp_obj)
    created["status"] = "in_progress"
    created["output"] = []
    created["usage"] = None
    chunks.append(sse_event("response.created", {"response": created, "sequence_number": seq, "type": "response.created"}))
    seq += 1
    chunks.append(sse_event("response.in_progress", {"response": created, "sequence_number": seq, "type": "response.in_progress"}))
    seq += 1
    for output_index, item in enumerate(resp_obj.get("output") or []):
        chunks.append(
            sse_event(
                "response.output_item.added",
                {"item": item, "output_index": output_index, "sequence_number": seq, "type": "response.output_item.added"},
            )
        )
        seq += 1
        chunks.append(
            sse_event(
                "response.output_item.done",
                {"item": item, "output_index": output_index, "sequence_number": seq, "type": "response.output_item.done"},
            )
        )
        seq += 1
    chunks.append(sse_event("response.completed", {"response": resp_obj, "sequence_number": seq, "type": "response.completed"}))
    chunks.append(b"data: [DONE]\n\n")
    return b"".join(chunks)


def normalize_input_item(item):
    if isinstance(item, str):
        return ("user", item)
    if not isinstance(item, dict):
        return ("user", str(item))

    itype = item.get("type")
    if itype == "message":
        return (item.get("role", "user"), flatten_content(item.get("content")))
    if itype in ("function_call", "custom_tool_call", "mcp_call", "local_shell_call", "shell_call", "apply_patch_call"):
        name = item.get("name") or item.get("server_label") or itype
        args = item.get("arguments") or item.get("input") or {}
        return ("assistant", f"[{itype}:{name}] {json_compact(args)}")
    if itype in ("function_call_output", "custom_tool_call_output", "local_shell_call_output", "shell_call_output", "apply_patch_call_output", "mcp_approval_response"):
        out = item.get("output") or item.get("content") or item.get("result") or item
        text = flatten_content(out) if not isinstance(out, dict) else json_compact(out)
        return ("user", f"[{itype}] {text}")
    role = item.get("role", "user")
    text = flatten_content(item.get("content"))
    if text:
        return (role, text)
    return (role, f"[{itype or 'item'}] {json_compact(item)}")


def normalize_responses_payload(payload):
    changed = False
    payload = dict(payload)
    if isinstance(payload.get("tools"), list):
        norm_tools = []
        for tool in payload.get("tools") or []:
            norm = normalize_tool_def(tool)
            if norm:
                norm_tools.append(norm)
        if norm_tools != payload.get("tools"):
            payload["tools"] = norm_tools
            changed = True

    raw_input = payload.get("input")
    if isinstance(raw_input, list) and payload.get("tools"):
        norm_items = [normalize_input_item_structured(item) for item in raw_input]
        if norm_items != raw_input:
            payload["input"] = norm_items
            changed = True
        return payload, changed

    if isinstance(raw_input, list):
        lines = []
        for item in raw_input:
            role, text = normalize_input_item(item)
            if text and text.strip():
                lines.append(f"[{role}]\n{text.strip()}")
        payload["input"] = "\n\n".join(lines) if lines else ""
        changed = True
    elif isinstance(raw_input, dict):
        role, text = normalize_input_item(raw_input)
        payload["input"] = f"[{role}]\n{text.strip()}" if text else ""
        changed = True
    return payload, changed


def load_models_config(path):
    cfg_path = Path(path)
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw_models = data.get("models") or []
    if not raw_models:
        raise SystemExit(f"models config is empty: {cfg_path}")

    models = []
    lookup = {}
    for idx, raw in enumerate(raw_models):
        name = raw.get("name")
        if not name:
            raise SystemExit(f"models[{idx}] missing name")
        upstream_base = (raw.get("upstream_base") or "").rstrip("/")
        if not upstream_base:
            raise SystemExit(f"models[{idx}] missing upstream_base")
        target_model = raw.get("target_model") or name
        aliases = list(raw.get("aliases") or [])
        model = {
            "name": name,
            "target_model": target_model,
            "upstream_base": upstream_base,
            "context_window": raw.get("context_window"),
            "owned_by": raw.get("owned_by", "codex-proxy-kit"),
            "normalize_responses": raw.get("normalize_responses", True),
            "rewrite_response_output": raw.get("rewrite_response_output", True),
            "synthesize_stream": raw.get("synthesize_stream", True),
            "aliases": aliases,
            "extra_model_fields": raw.get("extra_model_fields") or {},
        }
        models.append(model)
        lookup[name] = model
        for alias in aliases:
            lookup[alias] = model

    default_model = data.get("default_model") or models[0]["name"]
    if default_model not in lookup:
        raise SystemExit(f"default_model not found in models: {default_model}")
    return {
        "models": models,
        "lookup": lookup,
        "default_model": default_model,
    }


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _send_bytes(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _route_for_model(self, requested_model):
        route = self.server.models_lookup.get(requested_model)
        if route:
            return route
        return self.server.models_lookup[self.server.default_model]

    def _build_models_payload(self):
        data = []
        for model in self.server.models:
            item = {
                "id": model["name"],
                "object": "model",
                "created": 0,
                "owned_by": model["owned_by"],
                "root": model["name"],
            }
            if model.get("context_window") is not None:
                item["context_window"] = model["context_window"]
            item.update(model["extra_model_fields"])
            data.append(item)
        return {"object": "list", "data": data}

    def do_GET(self):
        try:
            if self.path == "/v1/models":
                body = json.dumps(self._build_models_payload(), ensure_ascii=False).encode("utf-8")
                self._send_bytes(200, body)
                return
            if self.path == "/healthz":
                self._send_bytes(200, b'{"ok":true}')
                return
            route = self.server.models[0]
            upstream_url = urljoin(route["upstream_base"] + "/", self.path.lstrip("/"))
            req = urllib.request.Request(upstream_url, method="GET")
            with urllib.request.urlopen(req, timeout=self.server.timeout_seconds) as resp:
                self._send_bytes(resp.status, resp.read(), resp.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self._send_bytes(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except Exception as e:
            self.server.log(f"GET {self.path} ERROR {e!r}")
            self._send_bytes(500, json.dumps({"error": str(e)}).encode())

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        upstream_headers = {"Content-Type": "application/json"}
        auth = self.headers.get("authorization")
        if auth:
            upstream_headers["Authorization"] = auth

        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = None

        route = self.server.models_lookup[self.server.default_model]
        requested_model = None
        client_wants_stream = False
        if isinstance(body, dict):
            requested_model = body.get("model") or self.server.default_model
            route = self._route_for_model(requested_model)
            client_wants_stream = bool(body.get("stream"))

        if self.path in ("/v1/responses", "/v1/chat/completions") and isinstance(body, dict):
            body["model"] = route["target_model"]
            if self.path == "/v1/responses" and route["normalize_responses"]:
                if client_wants_stream and route["synthesize_stream"]:
                    body["stream"] = False
                body, changed = normalize_responses_payload(body)
                if changed:
                    preview = str(body.get("input", ""))[:1000]
                    self.server.log(f"MODEL {requested_model} -> {route['target_model']} @ {route['upstream_base']}")
                    self.server.log(f"NORMALIZED /v1/responses input\n{preview}\n---")
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

        upstream_url = urljoin(route["upstream_base"] + "/", self.path.lstrip("/"))
        req = urllib.request.Request(upstream_url, data=raw, headers=upstream_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.server.timeout_seconds) as resp:
                ctype = resp.headers.get("Content-Type", "application/json")
                response_body = resp.read()
                if self.path == "/v1/responses" and "application/json" in ctype:
                    try:
                        obj = json.loads(response_body.decode("utf-8"))
                        if route["rewrite_response_output"]:
                            obj = rewrite_response_output_for_codex(obj)
                        obj = rewrite_response_model_for_display(obj, requested_model)
                        response_body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                        if client_wants_stream and route["synthesize_stream"]:
                            response_body = build_sse_from_response(obj)
                            ctype = "text/event-stream; charset=utf-8"
                    except Exception as e:
                        self.server.log(f"REWRITE /v1/responses output failed {e!r}")
                self.send_response(resp.status)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", resp.headers.get("Cache-Control", "no-cache"))
                self.send_header("Content-Length", str(len(response_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(response_body)
                self.wfile.flush()
        except urllib.error.HTTPError as e:
            body = e.read()
            self.server.log(f"UPSTREAM HTTPError {self.path} {e.code}\n{body.decode('utf-8', 'replace')}\n---")
            self._send_bytes(e.code, body, e.headers.get("Content-Type", "application/json"))
        except Exception as e:
            self.server.log(f"UPSTREAM ERROR {self.path} {e!r}\n{traceback.format_exc()}---")
            self._send_bytes(502, json.dumps({"error": str(e)}).encode())


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=18011)
    parser.add_argument("--log-dir", default=str(Path.home() / ".local" / "share" / "codex-vllm-proxy" / "logs"))
    parser.add_argument("--models-config", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    return parser.parse_args()


def main():
    args = parse_args()
    model_cfg = load_models_config(args.models_config)
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "proxy.log"

    def log(msg):
        with log_path.open("a", encoding="utf-8") as f:
            f.write(msg.rstrip() + "\n")

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), ProxyHandler)
    server.models = model_cfg["models"]
    server.models_lookup = model_cfg["lookup"]
    server.default_model = model_cfg["default_model"]
    server.timeout_seconds = args.timeout
    server.log = log
    log(
        f"Starting proxy on http://{args.listen_host}:{args.listen_port} "
        f"with models={[model['name'] for model in server.models]}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("Proxy stopped by KeyboardInterrupt")


if __name__ == "__main__":
    main()
