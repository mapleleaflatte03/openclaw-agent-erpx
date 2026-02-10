from __future__ import annotations

import socket
import threading
import time
from typing import Any

import httpx
import uvicorn

# manual_qa_test.py is a standalone script, not a pytest test module.
collect_ignore = ["manual_qa_test.py"]


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_uvicorn_in_thread(app: Any, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config=config)
    server.install_signal_handlers = False  # required when running in a thread

    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    # Wait until server is ready
    for _ in range(50):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.1)
    else:
        server.should_exit = True
        t.join(timeout=2)
        raise RuntimeError("uvicorn did not start")

    return server, t


def stop_uvicorn(server: uvicorn.Server, thread: threading.Thread) -> None:
    server.should_exit = True
    thread.join(timeout=5)

