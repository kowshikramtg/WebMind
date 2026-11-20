"""One command for the whole Lumen Valley demo.

    python examples/run_demo.py                 # WebMind on :8080 + the site on :8000
    python examples/run_demo.py --llm none      # no language model (instant, extractive answers)

It starts WebMind and the host app (which serves the site and signs in the demo accounts), waits until both are
ready, and prints the one URL to open. It refuses to start if a port is already taken, because another server
on port 8000 (for example the old `python -m http.server 8000`) would not know who is signed in and the assistant
would treat everyone as a public visitor. Press Ctrl+C to stop both.

Secrets default to demo values suitable for localhost only; export WEBMIND_JWT_SECRET / WEBMIND_ADMIN_API_KEY
to use your own. Logs: examples/basic-website/data/logs/.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess  # noqa: S404 - launches this repository's own servers
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "basic-website"
LOGS = DEMO / "data" / "logs"


def in_use(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - localhost URLs
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":  # the venv launcher spawns a child: kill the whole tree
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)  # noqa: S603, S607
    else:
        proc.terminate()


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)  # show progress immediately, also when piped to a file
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--llm", choices=["auto", "ollama", "none"], default="auto")
    ap.add_argument("--webmind-port", type=int, default=8080)
    ap.add_argument("--site-port", type=int, default=8000)
    args = ap.parse_args()

    busy = [(p, n) for p, n in ((args.webmind_port, "WebMind"), (args.site_port, "the site")) if in_use(p)]
    if busy:
        for port, name in busy:
            print(f"Port {port} (needed for {name}) is already in use by another program.")
        print(
            "Stop the old server(s) first (an old `python -m http.server`, `webmind serve` or host app window), or pick other\n"
            "ports with --webmind-port / --site-port. Starting anyway would leave you talking to a server that does not know\n"
            "who is signed in."
        )
        return 2

    use_llm = args.llm == "ollama" or (args.llm == "auto" and http_ok("http://localhost:11434/api/tags", 1.5))
    config = DEMO / ("webmind-ollama.yaml" if use_llm else "webmind.yaml")
    env = dict(os.environ)
    env.setdefault("WEBMIND_JWT_SECRET", "demo-signing-secret-for-local-testing-only-0123456789")
    env.setdefault("WEBMIND_ADMIN_API_KEY", "demo-admin-key-local-0123456789")
    env["WEBMIND_URL"] = f"http://localhost:{args.webmind_port}"
    env["PORT"] = str(args.site_port)
    env["WEBMIND_DEMO_CONFIG"] = str(DEMO / "webmind.yaml")
    for needed in (DEMO / "generated" / "records.db", DEMO / "data" / "webmind.db"):
        if not needed.exists():
            print(
                f"Missing {needed.relative_to(ROOT)}. First run:\n  python examples/basic-website/setup_demo.py\n"
                f"  webmind index --config examples/basic-website/webmind.yaml"
            )
            return 2
    LOGS.mkdir(parents=True, exist_ok=True)
    print(f"Answers: {'Ollama (llama3.2)' if use_llm else 'extractive, no language model'}   config: {config.name}")

    procs: list[subprocess.Popen] = []
    try:
        wm_log = (LOGS / "webmind.log").open("w", encoding="utf-8")
        host_log = (LOGS / "site.log").open("w", encoding="utf-8")
        wm = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "webmind.cli.main",
                "serve",
                "--config",
                str(config),
                "--port",
                str(args.webmind_port),
            ],
            cwd=ROOT,
            env=env,
            stdout=wm_log,
            stderr=subprocess.STDOUT,
        )
        procs.append(wm)
        site = subprocess.Popen(  # noqa: S603
            [sys.executable, str(ROOT / "examples" / "fastapi-integration" / "app.py")],
            cwd=ROOT,
            env=env,
            stdout=host_log,
            stderr=subprocess.STDOUT,
        )
        procs.append(site)

        deadline = time.time() + 90
        want = {
            "WebMind": f"http://127.0.0.1:{args.webmind_port}/v1/health",
            "the site": f"http://127.0.0.1:{args.site_port}/api/whoami",
        }
        ready: set[str] = set()
        while len(ready) < len(want) and time.time() < deadline:
            for proc, name in ((wm, "WebMind"), (site, "the site")):
                if proc.poll() is not None:
                    print(f"\n{name} stopped unexpectedly (exit {proc.returncode}). Log: {LOGS}")
                    return 1
            for name, url in want.items():
                if name not in ready and http_ok(url):
                    ready.add(name)
                    print(f"  ready: {name}")
            time.sleep(0.5)
        if len(ready) < len(want):
            print(f"\nTimed out waiting for {sorted(set(want) - ready)}. Logs: {LOGS}")
            return 1

        print(
            f"\nOpen  http://localhost:{args.site_port}/   (the assistant is the button at the bottom right)\n"
            f"      Sign in at http://localhost:{args.site_port}/login  (or the menu at the bottom left)\n"
            f"      WebMind API docs: http://localhost:{args.webmind_port}/docs\n"
            "Press Ctrl+C to stop."
        )
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A server stopped; shutting down. See the logs in", LOGS)
        return 1
    except KeyboardInterrupt:
        print("\nStopping...")
        return 0
    finally:
        for proc in procs:
            stop(proc)


if __name__ == "__main__":
    sys.exit(main())
