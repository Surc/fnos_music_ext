"""Offline checks run with each service's real production dependencies installed."""
import argparse
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch


def check_musicdl(module):
    """Real Kuwo parser + fake transport: single RID and timeout compatibility."""
    from musicdl import musicdl
    from musicdl.modules.sources.kuwo import KuwoMusicClient
    from musicdl.modules.utils import AudioLinkTester
    import curl_cffi
    assert callable(musicdl.MusicClient) and curl_cffi.requests.Session
    entry = {"keyword": "DAYBREAK'S BELL", "item": {
        "id": "kuwo:142276943", "source": "kuwo", "title": "DAYBREAK'S BELL",
        "artist": "L'Arc〜en〜Ciel", "duration_s": 252, "playback_status": "unresolved"}}
    for accepted_exts, expected_ext in [({"flac", "mp3"}, "flac"), ({"mp3"}, "mp3"), (set(), None)]:
        requests, audio_checks = [], []
        def get(client, url, **kwargs):
            assert "mobi.kuwo.cn/mobi.s" in url  # lyrics must never reach transport
            assert 0 < kwargs["timeout"] <= module.CONF["request_timeout"]
            requests.append(url)
            ext = "flac" if len(requests) == 1 else "mp3"
            return SimpleNamespace(status_code=200, text="http://audio.invalid/fixture." + ext,
                                   content=b"", raise_for_status=lambda: None)
        def head(tester, method, url, **kwargs):
            assert method == "HEAD"
            assert 0 < kwargs["request_overrides"]["timeout"] <= module.CONF["request_timeout"]
            ext = url.rsplit(".", 1)[-1]
            return {"ok": True, "status_code": 200, "download_url": url,
                    "ctype": "audio/" + ext, "file_size_bytes": 10_080_000,
                    "resp": SimpleNamespace(headers={}, close=lambda: None)}
        def audio(url, headers, ext, expected_s):
            assert expected_s == 252
            audio_checks.append(ext)
            return ext in accepted_exts
        with patch.object(KuwoMusicClient, "get", get), \
                patch.object(AudioLinkTester, "request", head), \
                patch.object(module, "_kuwo_audio_matches_sync", side_effect=audio), \
                patch.object(KuwoMusicClient, "_parsewiththirdpartapis", side_effect=AssertionError("unexpected third-party parse")):
            result = module._resolve_kuwo_entry_sync(entry)
        if expected_ext:
            item, _, _ = result
            assert item["id"] == entry["item"]["id"] and item["verified"] is True
            assert item["ext"] == expected_ext
        else:
            assert result is None
        # A rejected FLAC must not prevent trying a complete MP3 of the same RID.
        assert audio_checks == (["flac"] if expected_ext == "flac" else ["flac", "mp3"])
        assert len(requests) == len(audio_checks)


def check_musicbox():
    from NEMbox import cli

    parser = cli._build_parser()
    commands = [
        ["search", "fixture", "--type", "song", "--json"],
        ["song", "url", "123", "--quality", "lossless", "--json"],
        ["song", "info", "123", "--json"],
        ["artist", "123", "--limit", "5", "--json"],
        ["album", "123", "--json"],
        ["playlist", "show", "123", "--json"],
        ["auth", "login", "--no-wait", "--json"],
    ]
    for command in commands:
        assert getattr(parser.parse_args(command), "handler", None), command

    class Api:
        def songs_url(self, ids):
            assert ids == [123]
            return [{"id": 123, "code": 200, "url": "https://audio.invalid/fixture.flac"}]

    # Update notices are not part of the offline response contract.
    cli._update_notice = lambda: None
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = cli.dispatch(Api(), parser.parse_args(commands[1]))
    payload = json.loads(output.getvalue())
    assert status == 0 and payload["ok"] is True
    assert payload["data"]["code"] == 200
    assert payload["data"]["url"].endswith(".flac")
    executable = Path(sys.executable).parent / "musicbox"
    result = subprocess.run([str(executable), "song", "url", "--help"], capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"--quality" in result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("service", choices=["musicdl", "musicbox", "lxmusic"])
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--require-non-root", action="store_true")
    args = parser.parse_args()
    if args.require_non_root:
        assert os.geteuid() != 0, "Production service must run as non-root"
    with tempfile.TemporaryDirectory(prefix="fnmusic-contract-") as home:
        os.environ.update(HOME=home, XDG_CACHE_HOME=home + "/cache", XDG_DATA_HOME=home + "/data",
                          XDG_CONFIG_HOME=home + "/config", XDG_RUNTIME_DIR=home + "/run")
        for name in ("cache", "data", "config", "run"):
            Path(home, name).mkdir()
        sys.path.insert(0, str(args.app_dir.resolve()))
        module = importlib.import_module("app")
        assert module.app is not None
        if args.service == "musicbox":
            check_musicbox()
        elif args.service == "musicdl":
            check_musicdl(module)
        else:
            from lxserver_client import SUPPORTED_PLATFORMS
            from verify_source import parse_script_meta
            assert "kw" in SUPPORTED_PLATFORMS
            meta = parse_script_meta(
                "/*\n * @name ci-src\n * @version 1.0.0\n * @author ci\n"
                " * @description contract\n */\n"
            )
            assert meta["name"] == "ci-src"
        print(json.dumps({"service": args.service, "production_import": "pass", "offline_contract": "pass"}))


if __name__ == "__main__":
    main()
