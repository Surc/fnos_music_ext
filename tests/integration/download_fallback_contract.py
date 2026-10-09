"""Offline contract inside the actual source container, as its non-root user.

Needs the normal entrypoint running with NetEase + fallback enabled and a writable
/repo/.env. Verifies the real Supervisor/Unix-socket/HTTP lifecycle; no provider
search, account credential or external network is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import time
import urllib.request


CONTROL = Path("/data/download-fallback.sock")
ENV = Path("/repo/.env")
CONF = "/etc/supervisor/supervisord.conf"


def status(program: str) -> str:
    return subprocess.run(["supervisorctl", "-c", CONF, "status", program],
                          capture_output=True, text=True, timeout=10).stdout


def eventually(check, budget: float = 45) -> None:
    deadline = time.monotonic() + budget
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("container lifecycle condition timed out")
        time.sleep(0.5)


def rpc(operation: str, lease: str = "") -> dict:
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(50)
        client.connect(str(CONTROL))
        client.sendall(json.dumps({"op": operation, "lease": lease}).encode() + b"\n")
        with client.makefile("rb") as file:
            return json.loads(file.readline())


def write_config(*, enabled: bool = True, sources: str = "kuwo,migu") -> None:
    staged = ENV.with_suffix(".contract.tmp")
    staged.write_text("FNMUSIC_NETEASE_ENABLED=true\nFNMUSIC_MUSICDL_ENABLED=false\n"
                      "FNMUSIC_LX_ENABLED=false\nFNMUSIC_WEBUI_ENABLED=false\n"
                      f"FNMUSIC_DOWNLOAD_FALLBACK_ENABLED={str(enabled).lower()}\n"
                      f"FNMUSIC_DOWNLOAD_FALLBACK_SOURCES={sources}\n", encoding="utf-8")
    os.replace(staged, ENV)


def main() -> None:
    assert os.getuid() == 1000, "contract must run as the container service user"
    eventually(lambda: CONTROL.exists() and "RUNNING" in status("fallback-control"))
    assert stat.S_IMODE(CONTROL.stat().st_mode) == 0o600
    assert CONTROL.stat().st_uid == os.getuid()
    assert "RUNNING" in status("musicbox")
    for primary in ("musicdl", "lxmusic", "lxserver", "musicdl-fallback"):
        assert "STOPPED" in status(primary), status(primary)

    first = rpc("acquire")
    assert first.get("ok"), first
    running = status("musicdl-fallback")
    assert "RUNNING" in running, running
    with urllib.request.urlopen("http://127.0.0.1:8006/healthz", timeout=5) as response:
        assert response.status == 200
    second = rpc("acquire")
    assert second.get("ok"), second
    assert status("musicdl-fallback").split("pid ")[1].split(",")[0] == running.split("pid ")[1].split(",")[0]
    assert rpc("renew", first["lease"]).get("ok")
    assert rpc("release", first["lease"]).get("ok")
    assert "RUNNING" in status("musicdl-fallback"), "another active lease must retain the process"

    write_config(sources="migu,kuwo")
    eventually(lambda: "STOPPED" in status("musicdl-fallback"), 15)
    assert not rpc("renew", second["lease"]).get("ok"), "config change must invalidate old leases"
    third = rpc("acquire")
    assert third.get("ok"), third
    assert rpc("release", third["lease"]).get("ok")
    eventually(lambda: "STOPPED" in status("musicdl-fallback"))
    assert "RUNNING" in status("musicbox"), "fallback must preserve the primary"
    assert "RUNNING" in status("fallback-control")

    fourth = rpc("acquire")
    assert fourth.get("ok"), fourth
    write_config(enabled=False)
    eventually(lambda: "STOPPED" in status("musicdl-fallback"), 15)
    assert not rpc("acquire").get("ok"), "disabled fallback must refuse new downloads"
    subprocess.run(["supervisorctl", "-c", CONF, "stop", "fallback-control"], check=True, timeout=20)
    assert not CONTROL.exists(), "shutdown must remove its private control socket"
    assert "STOPPED" in status("musicdl-fallback")
    print("PASS: non-root fallback starts on demand, shares leases, expires on config/idle, and stops cleanly")


if __name__ == "__main__":
    main()
