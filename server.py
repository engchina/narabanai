# python server.py  →  http://localhost:8000
import json, os, threading, uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import (BACKEND, CROWD, HERE, KVM, PARTY, PRESETS, PROBES, PROVIDER,
                  live_sandboxes, probe_worker, publish_card, sweep, worker)

JOBS = {}  # ponytail: in-memory, one demo laptop; lost on restart


# Tier 1 は 10 vCPU。画面の ＋/− でこの上限まで増やせる。枠が上がったら env で引き上げる。
FANOUT_MAX = int(os.getenv("FANOUT_MAX", "8"))


def start_job(strategies, crowd, use_ai, chaos, kvm):
    n = len(strategies)
    job = {"logs": [[] for _ in range(n)], "codes": [[] for _ in range(n)],
           "results": [None] * n, "done": [False] * n}
    ex = ThreadPoolExecutor(n)
    for i in range(n):
        def done(f, i=i):
            job["results"][i], job["done"][i] = f.result(), True
        ex.submit(worker, i, strategies[i], crowd, use_ai, job["logs"][i], chaos,
                  job["codes"][i], kvm).add_done_callback(done)
    ex.shutdown(wait=False)
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = job
    return jid


def start_probe_job():
    """案1: 攻撃コードを並列の Sandbox で実行し、全部防がれるのを見せる。"""
    n = len(PROBES)
    job = {"logs": [[] for _ in range(n)], "codes": [[] for _ in range(n)],
           "results": [None] * n, "done": [False] * n, "probe": True}
    ex = ThreadPoolExecutor(n)
    for i in range(n):
        def done(f, i=i):
            job["results"][i], job["done"][i] = f.result(), True
        ex.submit(probe_worker, i, job["logs"][i], job["codes"][i]).add_done_callback(done)
    ex.shutdown(wait=False)
    jid = uuid.uuid4().hex[:8]
    JOBS[jid] = job
    return jid


class H(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")  # 再ビルド後に古い画面を掴ませない
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css")}
        if self.path in static:
            name, ctype = static[self.path]
            self.send(open(os.path.join(HERE, "web", name), "rb").read(), ctype)
        elif self.path == "/api/config":
            self.send({"presets": [p[0] for p in PRESETS], "crowd": list(CROWD),
                       "ai": BACKEND["ready"],
                       "provider": PROVIDER, "party": PARTY, "fanout_max": FANOUT_MAX, "kvm": KVM,
                       "probes": [p[0] for p in PROBES],
                       "show_probes": os.getenv("SHOW_PROBES") == "1"})  # 隔離の証明は既定で隠す
        elif self.path == "/api/sandboxes":
            try:
                self.send({"sandboxes": live_sandboxes()})
            except Exception as e:
                self.send({"sandboxes": [], "error": f"{type(e).__name__}"})
        elif self.path.startswith("/api/jobs/") and self.path[10:] in JOBS:
            self.send(JOBS[self.path[10:]])
        else:
            self.send({"error": "not found"}, code=404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/run":
            # 空文字は Sandbox を 1 個食うだけで何も比較できない。入口で落とす。
            strategies = [t for t in (str(s)[:2000].strip() for s in body["strategies"]) if t][:FANOUT_MAX]
            strategies = strategies or [p[0] for p in PRESETS]
            crowd = CROWD.get(body.get("crowd"), 1.0)
            self.send({"id": start_job(strategies, crowd, bool(body.get("use_ai")), bool(body.get("chaos")),
                                       bool(body.get("kvm")))})
        elif self.path == "/api/probe":
            self.send({"id": start_probe_job()})
        elif self.path == "/api/share":
            try:
                self.send({"url": publish_card(body["plan"])})
            except Exception as e:
                self.send({"error": f"{type(e).__name__}: {e}"}, code=500)
        else:
            self.send({"error": "not found"}, code=404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    gone = sweep()  # 前回の残骸で vCPU 枠を食われたままデモに入らない
    print(f"残骸を掃除: {len(gone)} 個 {gone}" if gone else "残骸なし", flush=True)
    print("Narabanai → http://localhost:8000", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8000), H).serve_forever()
