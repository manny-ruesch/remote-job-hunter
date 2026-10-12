import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"

from rjh.http import HttpClient, RateLimiter   # noqa: E402
from rjh.models import Job                      # noqa: E402
from rjh.profile import load_profile            # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def example_profile():
    return load_profile(ROOT / "profile.example.yaml")


class FakeTransport:
    """responder(call) -> (status, payload, headers). Enregistre chaque requête."""

    def __init__(self, responder):
        self.responder, self.calls = responder, []

    def __call__(self, method, url, headers, body, timeout):
        call = {"method": method, "url": url, "headers": headers, "body": json.loads(body) if body else None}
        self.calls.append(call)
        res = self.responder(call)                       # UN seul appel : les compteurs de tests en dépendent
        if isinstance(res, tuple):
            status, payload, *rest = res
            hdrs = rest[0] if rest else None
        else:
            status, payload, hdrs = 200, res, None
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, hdrs or {}, raw


def make_http(responder, **kw):
    t = FakeTransport(responder)
    return HttpClient(transport=t, limiter=RateLimiter(0), sleep=lambda s: None, **kw), t


class MockServer:
    """Vrai serveur HTTP local : teste ce qui part réellement sur le fil avec le transport urllib."""

    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def __enter__(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                req = {"method": self.command, "path": self.path.split("?")[0], "query": self.path.partition("?")[2],
                       "headers": {k.lower(): v for k, v in self.headers.items()}, "body": json.loads(body) if body else None}
                outer.requests.append(req)
                res = outer.handler(req)
                status, payload, hdrs = res[0], res[1], (res[2] if len(res) > 2 else None)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in (hdrs or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _do

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=lambda: self.srv.serve_forever(poll_interval=0.01), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def make_jobs(n, **over):
    return [Job(source="t", source_id=str(i), company=f"Co{i}", title=f"Automation Engineer {i}", url=f"https://x/{i}",
                location="Remote - Europe", description=f"n8n python workflows number {i}. " * 8, **over) for i in range(1, n + 1)]
