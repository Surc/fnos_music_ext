#!/usr/bin/env python3
"""Read-only check of recorded source baselines, releases and skill fingerprints."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def repo_file(relative):
    path = (ROOT / relative).resolve()
    require(path.is_relative_to(ROOT), f"Path leaves repository: {relative}")
    require(path.is_file(), f"Missing recorded file: {relative}")
    return path


def git(*args):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=False,
    )
    require(result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def commit(value, label):
    require(isinstance(value, str) and HEX40.fullmatch(value), f"Invalid commit: {label}")


def validate(check_git=False):
    data = json.loads(repo_file("SOURCE_VERSIONS.json").read_text(encoding="utf-8"))
    require(data["schema_version"] == 1, "Unsupported SOURCE_VERSIONS schema")
    version = repo_file("VERSION").read_text(encoding="utf-8").strip()
    require(data["project"]["version"] == version, "Project version differs from VERSION")
    repo_file(data["project"]["context_entry"])
    repo_file(data["project"]["iteration_document"])

    sources = {source["id"]: source for source in data["sources"]}
    require(set(sources) == {"upstream", "reference"}, "Record both source roles")
    for name, source in sources.items():
        baseline = source["baseline"]
        commit(baseline["commit"], f"{name}.baseline")
        commit(source["last_observed_head"]["commit"], f"{name}.last_observed_head")
        adopted_key = "last_integrated_commit" if name == "upstream" else "last_ported_commit"
        version_key = "last_integrated_version" if name == "upstream" else "last_ported_version"
        commit(source[adopted_key], f"{name}.{adopted_key}")
        require(source["tracking_branch"], f"Missing tracking branch: {name}")
        require(source[version_key], f"Missing adopted version: {name}")
        if check_git:
            for sha in dict.fromkeys((baseline["commit"], source[adopted_key])):
                require(git("cat-file", "-t", sha) == "commit", f"Not a commit: {sha}")
            require(
                git("show", f"{baseline['commit']}:{source['version_file']}").strip() == baseline["version"],
                f"Baseline VERSION mismatch: {name}",
            )
            require(
                git("show", f"{source[adopted_key]}:{source['version_file']}").strip() == source[version_key],
                f"Adopted VERSION mismatch: {name}",
            )
            if name == "upstream":
                git("merge-base", "--is-ancestor", source[adopted_key], "HEAD")

    for event in data["integrations"]:
        commit(event["merge_commit"], "integrations.merge_commit")
        for name, sha in event["sources"].items():
            require(name in sources, f"Unknown integration source: {name}")
            commit(sha, f"integrations.sources.{name}")
        for relative in event["implemented_modules"]:
            repo_file(relative)

    for release in data["releases"]:
        require(release["tag"] == f"v{release['version']}", "Release tag/version mismatch")
        commit(release["commit"], "releases.commit")
        for asset in release["assets"]:
            require(HEX64.fullmatch(asset["sha256"]), f"Invalid asset SHA256: {asset['name']}")
            require(asset["size_bytes"] > 0, f"Empty asset: {asset['name']}")
        if check_git:
            actual = git("rev-parse", f"refs/tags/{release['tag']}^{{commit}}")
            require(actual == release["commit"], f"Release tag moved: {release['tag']}")

    for skill in data["skills"]:
        for relative, expected in skill["files"].items():
            path = repo_file(f"{skill['repository_path']}/{relative}")
            require(HEX64.fullmatch(expected), f"Invalid skill SHA256: {relative}")
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            require(actual == expected, f"Skill fingerprint mismatch: {path.relative_to(ROOT)}")
        body = repo_file(f"{skill['repository_path']}/SKILL.md").read_text(encoding="utf-8")
        require(body.startswith("---\n"), "SKILL.md needs YAML frontmatter")
        require(f"name: {skill['name']}\n" in body.split("---", 2)[1], "Skill name mismatch")
        ui = repo_file(f"{skill['repository_path']}/agents/openai.yaml").read_text(encoding="utf-8")
        require(f"${skill['name']}" in ui, "Skill default prompt must mention its name")
        for relative in skill["context_files"]:
            repo_file(relative)

    specs = data["toolchain"]["python_dependency_specs"]
    for dependency in specs.values():
        require(
            dependency["requirement"] in repo_file(dependency["file"]).read_text(encoding="utf-8").splitlines(),
            f"Dependency requirement changed: {dependency['file']}",
        )
    lxserver = data["toolchain"]["lxserver"]
    lock = repo_file(lxserver["canonical_file"]).read_text(encoding="utf-8")
    for key, value in (("LXSERVER_TAG", lxserver["tag"]), ("LXSERVER_ZIP_NAME", lxserver["artifact"]),
                       ("LXSERVER_SHA256", lxserver["sha256"])):
        require(f"{key}={value}" in lock.splitlines(), f"lxserver lock mismatch: {key}")

    for work in data["planned_work"]:
        repo_file(work["design_document"])
        require(work["status"] in {"planned", "in_progress", "implemented", "released"}, "Invalid work status")
        if work["status"] == "planned":
            require(work["included_in_release"] is False, "Planned work cannot be recorded as released")

    print(f"Provenance OK: {len(sources)} sources, {len(data['releases'])} release snapshot(s), "
          f"{len(data['skills'])} skill(s), VERSION {version}")
    if check_git:
        print("Git baseline objects, upstream ancestry and release tags verified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-git", action="store_true",
                        help="Also check full-history Git objects; fetch origin tags and both sources first.")
    args = parser.parse_args()
    try:
        validate(args.check_git)
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Provenance error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
