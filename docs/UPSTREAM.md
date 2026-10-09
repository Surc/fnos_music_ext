# 集成版打包、上游同步与移植边界

本项目是 Surc/fnos_music_ext v2.7.1，保留原工程完整 Git 历史，基于 javycoder/fnos_music_ext v2.7.0 开发。功能使用见 [PERSONALIZATION.md](PERSONALIZATION.md)，安装见 [INSTALL.md](INSTALL.md)。

双来源的分支、VERSION、真实 tag、完整提交、采用范围、正式发行摘要与 skill 数据统一记录在 [SOURCE_VERSIONS.json](../SOURCE_VERSIONS.json)。本次快照、下一版待办与更新记录步骤见 [ITERATION.md](ITERATION.md)，Agent 从 [AGENTS.md](../AGENTS.md) 接续。参考工程 2.9.30 来自 VERSION 文件；查询 v2.9.30 tag 返回 404，因此按 SHA 锁定。

| 来源 | 固定提交 | 使用范围 |
| --- | --- | --- |
| https://github.com/javycoder/fnos_music_ext | `f036e1f7082363c5f5e4892d15209ce584b0769b` | 代理、三音源单选、AI 推荐、原生收藏/历史、管理页、Docker 和 FPK 基础 |
| https://github.com/gzywd/fnos_music_ext | `08b0b23486d289619ab73575f42aa0d20e78c2b3`（v2.9.30） | `proxy/playlists.py` 的发现分类、命名、顺序、缓存思路及 `musicbox-service/netease_ext.py` 的推荐歌单/FM/分类/新碟接口适配 |

两边使用 MIT 许可证。保留根目录 LICENSE 和作者提交历史。移植是按接口语义改写适配，不是把 Fork 部署/播放文件整套覆盖；具体新增模块为 `proxy/discovery.py`、`proxy/personalization.py`、`musicbox-service/discovery.py`。

## 开始维护本仓库

新克隆只自动得到 `origin`；下面添加的 `upstream` 用于合并原工程，`reference` 用于查看功能参考版本。

```bash
git clone https://github.com/Surc/fnos_music_ext.git
cd fnos_music_ext
git remote add upstream https://github.com/javycoder/fnos_music_ext.git
git remote add reference https://github.com/gzywd/fnos_music_ext.git
```

## 分支和远端

- `main`：可测试和打包的集成版本。
- `upstream` 远端：`https://github.com/javycoder/fnos_music_ext.git`。
- `reference` 远端：`https://github.com/gzywd/fnos_music_ext.git`，用作后续功能参考。
- `origin`：你自己的 GitHub 仓库。
- 保留原工程历史，以普通 merge 更新，不重建仓库、不 squash 上游历史、不强推 main。
- 将两条 main 的最后检查提交与实际已合并 / 已移植提交分开记录。完成变更进入本项目主分支后保留旧基线、追加采用记录，再推进来源清单；检查或下载新版本不表示已经采用。

## GitHub 上更新

打开 Actions → **Sync upstream** → Run workflow。工作流创建同步分支，在合并结果上运行测试并打包，再创建 PR；它不会自动合并或发布到 NAS。GitHub 仓库需允许 Actions 创建 pull request：在 Settings → Actions → General → Workflow permissions 勾选 Allow GitHub Actions to create and approve pull requests 并保存；工作流只创建 PR，不执行批准或自动合并。没有新上游提交时不创建 PR。未启用该权限时也可用本地流程推送同步分支，在网页手动创建 PR。

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
- 不改代码在线打包：Actions → **Build FPK** → Run workflow，选择 `main`；成功后在运行页 Artifacts 下载 `fnmusic-ext-fpk` ZIP，解压获取 `.fpk` 和 `.sha256`。
- 安装已发布版本：从 [本仓库 Releases](https://github.com/Surc/fnos_music_ext/releases) 下载 FPK，在 fnOS 应用中心手动安装。
- 正式发行：修改 VERSION 和 CHANGELOG，测试通过后推送同版本 `vX.Y.Z` tag；Release 工作流执行打包测试、构建 FPK，并创建 / 更新 GitHub Release 的说明、FPK 与校验和。网页创建同版本 tag / Release 时也可能触发它，因此发行说明应维护在 CHANGELOG 对应版本段落。
- GitHub 自带 Source code ZIP / tar.gz 对应该 tag 的源码；含完整 Git 历史的源码备份另外提供，克隆本仓库也能得到完整历史。
- 新增发现缓存不会被打进包；安装包升级备份包含历史、收藏、歌单附加数据和推荐缓存。保持原工程的数据目录和恢复流程。
- Gitee 发行版推送只有同时设置 `GITEE_TOKEN` 和 `GITEE_REPO` 仓库变量时才启用；目标不再固定为原作者仓库。
