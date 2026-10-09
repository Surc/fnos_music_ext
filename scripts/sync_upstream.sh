#!/usr/bin/env bash
# Create a reviewable integration branch without altering main or force-pushing.
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
if [ -n "$(git status --porcelain)" ]; then
    echo "工作区有未提交内容，请先提交或暂存后重试。" >&2
    exit 1
fi
upstream_url="https://github.com/javycoder/fnos_music_ext.git"
if ! git remote get-url upstream >/dev/null 2>&1; then
    git remote add upstream "${upstream_url}"
fi
if [ "$(git remote get-url upstream)" != "${upstream_url}" ]; then
    echo "upstream 地址与预期不同，请先检查远端配置。" >&2
    exit 1
fi
git fetch upstream main
if git merge-base --is-ancestor upstream/main HEAD; then
    echo "当前分支已包含上游最新提交。"
    exit 0
fi
sync_sha="$(git rev-parse --short=12 upstream/main)"
sync_branch="sync/upstream-${sync_sha}-$(date +%s)"
git switch -c "${sync_branch}"
if ! git merge --no-ff upstream/main -m "Merge upstream ${sync_sha}"; then
    echo "合并存在冲突，已保留在 ${sync_branch}，请检查 git status 后解决。" >&2
    echo "需要取消本次合并时执行 git merge --abort；主分支未修改。" >&2
    exit 2
fi
echo "已准备 ${sync_branch}。运行 python -m pytest 和 packaging/fpk/build.sh 后提交 PR。"
