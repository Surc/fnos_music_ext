# 上游同步与移植边界

本项目保留原工程完整 Git 历史，基于 javycoder/fnos_music_ext v2.7.0 开发。

| 来源 | 固定提交 | 使用范围 |
| --- | --- | --- |
| https://github.com/javycoder/fnos_music_ext | `f036e1f7082363c5f5e4892d15209ce584b0769b` | 代理、三音源单选、AI 推荐、原生收藏/历史、管理页、Docker 和 FPK 基础 |
| https://github.com/gzywd/fnos_music_ext | `08b0b23486d289619ab73575f42aa0d20e78c2b3`（v2.9.30） | `proxy/playlists.py` 的发现分类、命名、顺序、缓存思路及 `musicbox-service/netease_ext.py` 的推荐歌单/FM/分类/新碟接口适配 |

两边使用 MIT 许可证。保留根目录 LICENSE 和作者提交历史。移植是按接口语义改写适配，不是把 Fork 部署/播放文件整套覆盖；具体新增模块为 `proxy/discovery.py`、`proxy/personalization.py`、`musicbox-service/discovery.py`。

## 分支和远端

- `main`：可测试和打包的集成版本。
- `upstream` 远端：`https://github.com/javycoder/fnos_music_ext.git`。
- `reference` 远端：`https://github.com/gzywd/fnos_music_ext.git`，用作后续功能参考。
- `origin`：你自己的 GitHub 仓库。
- 保留原工程历史，以普通 merge 更新，不重建仓库、不 squash 上游历史、不强推 main。

## GitHub 上更新

打开 Actions → **Sync upstream** → Run workflow。工作流创建同步分支，在合并结果上运行测试并打包，再创建 PR；它不会自动合并或发布到 NAS。GitHub 仓库需允许 Actions 创建 pull request。没有新上游提交时不创建 PR。

出现冲突时工作流停止，日志标出冲突；使用下面的本地流程解决。上游改变 VERSION、推荐路由或容器 COPY 清单时，需要检查本项目的版本号与新增模块是否仍被打包。FPK 应用 ID 沿用 `fnmusic-ext`，因此作为同一扩展的升级包安装，不能和原扩展并行接管同一个 socket。

## 本地更新

```bash
git switch main
git pull --ff-only origin main
bash ./scripts/sync_upstream.sh
python -m pytest -q
node --check webui-service/static/app.js
bash ./packaging/fpk/build.sh
git push -u origin "$(git branch --show-current)"
```

脚本要求干净工作区，先创建 `sync/upstream-*` 分支再 merge。冲突留在新分支；解决后 `git add`、`git merge --continue`。取消使用 `git merge --abort`，main 不受影响。不要对整个冲突文件使用 ours/theirs；保留上游修复，再手工重新接入新增推荐模块。

## 打包与发行

- 一键本地打包：`bash ./packaging/fpk/build.sh`，产物在 `dist/`，带 SHA256 文件。
- 不改代码在线打包：Actions → **Build FPK**，从 artifacts 下载 `.fpk`。
- 正式发行：修改 VERSION 和 CHANGELOG，测试通过后推送同版本 `vX.Y.Z` tag；既有 Release 工作流上传包和校验和。
- 新增发现缓存不会被打进包；安装包升级备份包含历史、收藏、歌单附加数据和推荐缓存。保持原工程的数据目录和恢复流程。
- Gitee 发行版推送只有同时设置 `GITEE_TOKEN` 和 `GITEE_REPO` 仓库变量时才启用；目标不再固定为原作者仓库。
