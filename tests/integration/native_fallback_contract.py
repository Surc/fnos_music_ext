"""Run the native Supervisor template with real dependencies inside the CI image.

Uses a temporary public-code checkout and generated configuration, without a
systemd install or any provider network request. This is a process contract,
not evidence of installation on fnOS.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import download_fallback_contract as lifecycle


def main(source: Path) -> None:
    assert os.getuid() == 1000, "run as the existing non-root image user"
    with tempfile.TemporaryDirectory(prefix="fnmusic-native-contract-") as tmp:
        repo = Path(tmp) / "repo"
        run = repo / "sources-native"
        data = repo / "sources-data"
        for relative in (
            "VERSION", "proxy/__init__.py", "proxy/env_merge.py", "proxy/version.py",
            "proxy/fnlog.py", "proxy/fallback_control.py", "musicdl-service/app.py",
            "musicdl-service/hardening.py", "musicbox-service/app.py",
            "musicbox-service/runner.py", "musicbox-service/netease_ext.py",
            "musicbox-service/discovery.py",
        ):
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        run.mkdir()
        data.mkdir()
        conf = run / "supervisord.conf"
        template = (source / "container/supervisord-native.conf.in").read_text()
        for key, value in {"REPO": repo, "RUN": run, "DATA": data, "VENV": sys.prefix}.items():
            template = template.replace(f"@{key}@", str(value))
        conf.write_text(template)
        lifecycle.CONTROL = data / "download-fallback.sock"
        lifecycle.ENV = repo / ".env"
        lifecycle.CONF = str(conf)
        lifecycle.HEALTH_URL = "http://127.0.0.1:8776/healthz"
        lifecycle.write_config()
        env = dict(os.environ, SUPERVISOR_CONF=str(conf))
        with (run / "launch.log").open("w") as log:
            supervisor = subprocess.Popen(["supervisord", "-c", str(conf)], env=env,
                                          stdout=log, stderr=subprocess.STDOUT)
            try:
                lifecycle.eventually(lambda: (run / "supervisor.sock").exists(), 15)
                subprocess.run(["supervisorctl", "-c", str(conf), "start", "musicbox", "fallback-control"],
                               check=True, timeout=30)
                lifecycle.main()
                print("PASS: native paths and host port preserve the fallback lifecycle")
            finally:
                supervisor.terminate()
                try:
                    supervisor.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    supervisor.kill()
                    supervisor.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    main(parser.parse_args().source_root)
