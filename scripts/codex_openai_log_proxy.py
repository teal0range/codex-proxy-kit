#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S.%fZ")


def ensure_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        message = "%s - - [%s] %s\n" % (
            self.client_address[0],
            self.log_date_time_string(),
            fmt % args,
        )
        self.server.stderr_log.write(message)
        self.server.stderr_log.flush()

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")

    def _proxy(self, method: str) -> None:
        started = time.time()
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(content_length) if content_length else b""
        target_url = self.server.upstream_base.rstrip("/") + self.path

        request_id = utc_now()
        req_path = self.server.log_dir / f"{request_id}.request.json"
        res_path = self.server.log_dir / f"{request_id}.response.log"

        incoming_headers = {k: v for k, v in self.headers.items()}
        with req_path.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "timestamp": request_id,
                    "method": method,
                    "path": self.path,
                    "target_url": target_url,
                    "headers": incoming_headers,
                    "body_text": ensure_text(raw_body),
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        headers = dict(incoming_headers)
        headers.pop("Host", None)
        if "Content-Length" in headers:
            headers["Content-Length"] = str(len(raw_body))

        try:
            upstream = self.server.session.request(
                method=method,
                url=target_url,
                headers=headers,
                data=raw_body if raw_body else None,
                stream=True,
                timeout=(20, 600),
                proxies=self.server.upstream_proxies,
            )
        except Exception as exc:
            self._send_json(502, {"error": "proxy_upstream_error", "detail": repr(exc)})
            return

        response_headers = dict(upstream.headers)
        content_type = response_headers.get("Content-Type", "")
        is_sse = "text/event-stream" in content_type.lower()
        filtered_headers = {
            k: v
            for k, v in response_headers.items()
            if k.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}
        }

        self.send_response(upstream.status_code)
        for key, value in filtered_headers.items():
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()

        with res_path.open("w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "timestamp": request_id,
                        "status_code": upstream.status_code,
                        "response_headers": response_headers,
                        "is_sse": is_sse,
                    },
                    ensure_ascii=False,
                )
            )
            f.write("\n")

            if is_sse:
                for chunk in upstream.iter_content(chunk_size=None):
                    if not chunk:
                        continue
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    f.write(ensure_text(chunk))
                    f.flush()
            else:
                chunks: list[bytes] = []
                for chunk in upstream.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    self.wfile.write(chunk)
                self.wfile.flush()
                f.write(ensure_text(b"".join(chunks)))
                f.flush()

        upstream.close()
        elapsed = time.time() - started
        self.server.stderr_log.write(
            f"{request_id} {method} {self.path} -> {upstream.status_code} {elapsed:.3f}s\n"
        )
        self.server.stderr_log.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=18021)
    parser.add_argument("--upstream-base", default="https://api.openai.com")
    parser.add_argument(
        "--log-dir",
        default=os.path.expanduser("~/.local/share/codex-openai-log-proxy/logs"),
    )
    parser.add_argument("--upstream-http-proxy", default=os.environ.get("HTTP_PROXY", ""))
    parser.add_argument("--upstream-https-proxy", default=os.environ.get("HTTPS_PROXY", ""))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.listen_host, args.listen_port), ProxyHandler)
    server.log_dir = log_dir
    server.upstream_base = args.upstream_base.rstrip("/")
    server.session = requests.Session()
    server.upstream_proxies = {
        "http": args.upstream_http_proxy,
        "https": args.upstream_https_proxy,
    }
    server.stderr_log = sys.stderr

    print(
        f"codex-openai-log-proxy listening on http://{args.listen_host}:{args.listen_port} "
        f"-> {server.upstream_base}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
