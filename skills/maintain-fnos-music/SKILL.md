---
name: maintain-fnos-music
description: 维护 Surc/fnos_music_ext 飞牛音乐集成项目。用于接续开发、核对 javycoder 和 gzywd 两个来源的分支与版本、同步上游、移植发现或推荐功能、验证并发布 FPK，以及更新来源和迭代记录。
---

# 飞牛音乐项目维护

## 建立当前上下文

1. 定位任务指定的 `Surc/fnos_music_ext` 工作副本；缺少工作副本时按任务授权克隆 `https://github.com/Surc/fnos_music_ext.git`。用 `git remote -v` 和 `git status --short --branch` 核对仓库与未提交修改。
2. 从仓库根目录读取 `AGENTS.md`、`SOURCE_VERSIONS.json`、`docs/ITERATION.md`；按任务读取 `docs/UPSTREAM.md`、`docs/PERSONALIZATION.md` 和 `docs/VALIDATION.md`。以当前仓库中的文件为准，避免沿用旧会话的临时路径或记忆。
3. 从 `SOURCE_VERSIONS.json` 取得完整 SHA、实际分支、移植范围、已发布提交、附件摘要、skill 版本与校验值。将来源文件的版本号、真实 Git tag、当前远端 head、已合并/已移植提交分别处理；来源出现新提交不表示本仓库已采用。
4. 用 `python3 scripts/verify_provenance.py` 检查来源和 skill 记录。涉及同步或发行时，先获取远端并核实 ancestry/tag；完整克隆并获取 reference 后可加 `--check-git`。

## 维护双来源

- 对 `upstream=https://github.com/javycoder/fnos_music_ext.git` 的 `main` 使用普通 merge。保留原工程完整历史，在干净的集成分支上运行 `bash scripts/sync_upstream.sh`；按已有授权提交、创建或合并 PR。
- 对 `reference=https://github.com/gzywd/fnos_music_ext.git` 的 `main` 按选定接口语义移植。先对照上次已移植 SHA 和新 SHA 的差异；记录实际采用的文件、功能与未采用范围。避免整套替换部署、播放和生命周期代码。
- 为来源更新保留旧基线与采用记录；只有对应变更确实进入主分支后，才推进 `last_integrated_commit` 或 `last_ported_commit`。失败、仅查看、未合并的尝试不推进采用基线。
- 对冲突逐段保留上游修复并重新接入扩展；避免对完整冲突文件使用 ours/theirs、重建历史或强推主分支。

## 保持项目行为

- 保持 `fnmusic-ext` 应用 ID、官方 Unix Socket 恢复、数据目录、升级备份和非 root 音源服务契约。
- 对照新增模块 `proxy/discovery.py`、`proxy/personalization.py`、`musicbox-service/discovery.py` 及其代理、WebUI、Docker COPY 和 FPK 打包入口。
- 保持飞牛用户自己的历史/收藏与每日推荐隔离；区分 NAS 共用网易账号的推荐歌单/FM。保持发现歌单只读，避免将本地播放记录写回网易云。
- 保持现有主音源三选一。处理跨平台下载需求前，读取 `docs/ITERATION.md` 的待实现条目和实际下载代码；明确区分已经发布与仅设计的行为。
- 采用其他平台曲目时核对歌名、歌手、版本和时长；保留实际平台/曲目 ID 与音质信息，避免把转码、试听、翻唱或 Live 当作原版高音质结果。

## 验证与发行

1. 根据变更运行有意义的验证。功能修改运行相关测试与全工程 pytest，前端运行 Node 检查；部署/依赖修改核对生产合约、Docker 和生命周期；纯资料修改检查 JSON、链接和 skill 格式。
2. 对照 `.github/workflows/ci.yml` 的实际结果，记录测试使用的提交与环境。将原有 807 passed 等结果保留为历史证据，不当作任何新代码已经通过的证据。
3. 使用 `bash packaging/fpk/build.sh` 或 Actions 的 Build FPK。用包内文件、SHA256 和实际依赖验证新增模块。排除 `.env`、登录态、历史、收藏、缓存与临时产物。
4. 发行新功能时更新 `VERSION` 和 `CHANGELOG.md` 对应版本段，核对 `vX.Y.Z` tag 与 VERSION 一致；沿用 Release 工作流生成的正式附件与摘要。将已发布 tag 作为固定证据，并区分发行后继续变化的 main。
5. 从 Release API/页面记录正式资产的 SHA256；不要以另一次本地构建的字节摘要代替正式发行附件。源码 ZIP 与含完整历史的备份分别说明。
6. 更新 `SOURCE_VERSIONS.json`、`docs/ITERATION.md`、用户使用文档与验证记录。修改 skill 时提升其版本、更新文件摘要，并同步仓库副本与个人 Skills 中的同名 skill。
7. 如实报告 CI、构建和实机结果。仅在实际 fnOS 验收后报告安装、升级、网易登录和播放成功；自动测试与 healthz 不替代这些结论。

## 交接输出

给出本次提交/PR/Release、双来源采用基线、skill 版本、实际验证结果和下一项待办。将授权和凭证按当次会话处理；不在项目资料中保存 Cookie、API 密钥、个人会话或临时下载地址。
