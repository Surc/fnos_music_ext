"""Small Unix-socket lease manager for the separate musicdl fallback process.

Runs as the existing container user. No Docker socket, TCP control endpoint,
administrator impersonation or user credentials. Idle/expired leases stop the
auxiliary process, independently of the selected primary provider.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import stat
import struct
import time
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from proxy.env_merge import parse_env_file

logger = logging.getLogger("fallback_control")
PREFIX = "FNMUSIC_DOWNLOAD_FALLBACK_"


def allowed(values: dict) -> bool:
    def flag(key):
        return str(values.get(key, "false")).lower() in ("true", "1", "yes")
    return (flag(PREFIX + "ENABLED") and flag("FNMUSIC_NETEASE_ENABLED")
            and not flag("FNMUSIC_MUSICDL_ENABLED") and not flag("FNMUSIC_LX_ENABLED"))


class LeaseManager:
    def __init__(self, env_path: str = "/repo/.env", socket_path: str = "/data/download-fallback.sock"):
        self.env_path = Path(env_path)
        self.socket_path = Path(socket_path)
        self.leases: dict[str, float] = {}
        self.lock = asyncio.Lock()
        self.started = False
        self.fingerprint = ""
        self.idle_since = 0.0
        self.lease_s = 60.0
        self.idle_s = 30.0
        self.connections: set[asyncio.Task] = set()

    async def healthy(self) -> bool:
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", 8006), 2)
            writer.write(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return b" 200 " in await asyncio.wait_for(reader.readline(), 2)
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            if writer:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()

    def config(self) -> dict:
        values, _ = parse_env_file(self.env_path)
        return dict(values)

    async def ctl(self, operation: str) -> bool:
        process = await asyncio.create_subprocess_exec(
            os.environ.get("FALLBACK_SUPERVISORCTL", "supervisorctl"),
            "-c", os.environ.get("SUPERVISOR_CONF", "/etc/supervisor/supervisord.conf"),
            operation, "musicdl-fallback", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), 25)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if operation == "status":
            # STARTING/BACKOFF can also become a live orphan after a controller restart.
            return any(state in raw for state in (b"RUNNING", b"STARTING", b"BACKOFF"))
        if process.returncode != 0:
            logger.warning("auxiliary process %s failed (exit=%s)", operation, process.returncode)
        return process.returncode == 0

    async def stop(self) -> None:
        # Also retry stopping after a controller restart; do not trust in-memory state.
        if self.started or await self.ctl("status"):
            if not await self.ctl("stop"):
                raise RuntimeError("cannot stop fallback process")
        self.started = False
        self.leases.clear()
        self.idle_since = 0.0

    async def reap(self) -> None:
        async with self.lock:
            values = self.config()
            fingerprint = str(values.get(PREFIX + "SOURCES", "kuwo,migu"))
            if not allowed(values) or (self.started and fingerprint != self.fingerprint):
                await self.stop()
                return
            now = time.monotonic()
            self.leases = {key: expiry for key, expiry in self.leases.items() if expiry > now}
            if self.started and not self.leases:
                self.idle_since = self.idle_since or now
                if now - self.idle_since >= self.idle_s:
                    await self.stop()

    async def handle(self, operation: str, lease: str) -> dict:
        async with self.lock:
            values = self.config()
            if operation == "release":
                self.leases.pop(lease, None)
                if not self.leases:
                    self.idle_since = time.monotonic()
                return {"ok": True}
            if not allowed(values):
                await self.stop()
                return {"ok": False, "error": "disabled"}
            if operation == "acquire":
                if len(self.leases) >= 4:
                    return {"ok": False, "error": "busy"}
                fingerprint = str(values.get(PREFIX + "SOURCES", "kuwo,migu"))
                if self.started and fingerprint != self.fingerprint:
                    await self.stop()
                if not self.started:
                    if not await self.ctl("status") and not await self.ctl("start"):
                        return {"ok": False, "error": "start_failed"}
                    self.started = True
                    self.fingerprint = fingerprint
                    deadline = time.monotonic() + 12
                    while not await self.healthy():
                        if time.monotonic() >= deadline:
                            await self.stop()
                            return {"ok": False, "error": "unhealthy"}
                        await asyncio.sleep(0.25)
                lease = uuid4().hex
            elif operation == "renew":
                if self.leases.get(lease, 0) <= time.monotonic():
                    return {"ok": False, "error": "expired"}
            else:
                return {"ok": False, "error": "invalid_operation"}
            self.leases[lease] = time.monotonic() + self.lease_s
            self.idle_since = 0.0
            return {"ok": True, "lease": lease}

    async def connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self.connections.add(task)
        try:
            transport_socket = writer.get_extra_info("socket")
            _pid, uid, _gid = struct.unpack("3i", transport_socket.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, os.getuid()):
                result = {"ok": False, "error": "unauthorized_peer"}
            else:
                request = json.loads(await asyncio.wait_for(reader.readline(), 3))
                result = await self.handle(str(request.get("op", "")), str(request.get("lease", "")))
            writer.write(json.dumps(result).encode() + b"\n")
            await writer.drain()
        except (ValueError, TypeError, OSError, asyncio.TimeoutError, RuntimeError, AttributeError):
            logger.info("control request rejected or unavailable")
        finally:
            self.connections.discard(task)
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def run(self, stop_event: asyncio.Event) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.lstat().st_mode):
                raise RuntimeError("control socket path is not a socket")
            # A supervisor restart may leave its old socket; never remove arbitrary files.
            self.socket_path.unlink()
        # Finish orphan cleanup before accepting leases; startup must not stop a new lease.
        await self.stop()
        old_umask = os.umask(0o077)
        try:
            server = await asyncio.start_unix_server(self.connection, path=str(self.socket_path), limit=2048)
        finally:
            os.umask(old_umask)
        os.chmod(self.socket_path, 0o600)
        try:
            async with server:
                while not stop_event.is_set():
                    try:
                        await self.reap()
                    except (OSError, RuntimeError):
                        logger.warning("lease reconciliation failed; will retry")
                    try:
                        await asyncio.wait_for(stop_event.wait(), 5)
                    except asyncio.TimeoutError:
                        pass
        finally:
            server.close()
            await server.wait_closed()
            for task in list(self.connections):
                task.cancel()
            if self.connections:
                await asyncio.gather(*self.connections, return_exceptions=True)
            await self.stop()
            with suppress(FileNotFoundError):
                self.socket_path.unlink()


async def main() -> None:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)
    await LeaseManager(os.environ.get("FNMUSIC_ENV_FILE", "/repo/.env"),
                       os.environ.get("FALLBACK_CONTROL_SOCKET", "/data/download-fallback.sock")).run(stop_event)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
