# 迭代交接与来源记录

记录日期：2026-10-09（Asia/Shanghai）。机器可读记录见 [SOURCE_VERSIONS.json](../SOURCE_VERSIONS.json)，续作入口见 [AGENTS.md](../AGENTS.md)。

## 两个来源的采用基线

| 来源与角色 | 跟踪分支 | 本次采用版本 | 固定提交 | Git tag 情况 |
| --- | --- | --- | --- | --- |
| [javycoder/fnos_music_ext](https://github.com/javycoder/fnos_music_ext)，基础工程 | `main`；本地远端 `upstream` | VERSION：`2.7.0` | `f036e1f7082363c5f5e4892d15209ce584b0769b` | `v2.7.0` 为 annotated tag，解析后指向此提交 |
| [gzywd/fnos_music_ext](https://github.com/gzywd/fnos_music_ext)，发现功能参考 | `main`；本地远端 `reference` | VERSION：`2.9.30` | `08b0b23486d289619ab73575f42aa0d20e78c2b3` | 查询 `refs/tags/v2.9.30` 返回 404；使用 VERSION + SHA 锁定 |

2026-10-09 核对 GitHub：两个远端的默认分支都是 main，分支 head 都与上表相同。以后远端 head 可能变化；上表记录本次实际采用的快照。原工程经 Git merge 保留完整历史；参考工程移植了选定功能，其整个 main 没有合并进来。

参考范围为 `proxy/playlists.py` 的发现分类、命名、顺序与缓存思路，以及 `musicbox-service/netease_ext.py` 的推荐歌单、私人 FM、分类与新碟接口语义。集成实现落在 `proxy/discovery.py`、`proxy/personalization.py`、`musicbox-service/discovery.py`，并接入代理、推荐、WebUI、容器及打包流程。原工程的部署、播放、数据恢复体系继续作为基础。

## 已发布与继续开发

- 集成版本：VERSION `2.7.1`。
- 功能合并：[PR #1](https://github.com/Surc/fnos_music_ext/pull/1)，合并提交 `0d5d2451d7acfa8f0162b89d628083d33f59c161`。
- 正式发行：[v2.7.1](https://github.com/Surc/fnos_music_ext/releases/tag/v2.7.1)，tag 固定在 `72df1bb053f9769ea13389fc7fffbe903f80541a`，含已更新的集成版文档。
- 正式 FPK 的 SHA256：`33dc5dc28c714f016ae1c403519075ad727425fe9f543ce4ecb1675681506bd6`。以 Release 附件及其配套校验文件为准；不同构建的包字节可能不同。
- 本次追加来源与维护资料进入后续 main，现有发布快照可从 tag 独立复现。通过 [验证记录](VALIDATION.md) 查看功能 PR 的 11 项 CI 与两版本 Python 各 807 passed 的证据。
- 发布提交的 [CI](https://github.com/Surc/fnos_music_ext/actions/runs/37907664958) 与 [Release 工作流](https://github.com/Surc/fnos_music_ext/actions/runs/37908174138) 均成功。fnOS 实机安装、升级和真实账号在线播放仍待验收。

## Skill 信息与可复用数据

| 项目 | 记录 |
| --- | --- |
| 名称 | `maintain-fnos-music` |
| 版本 | `1.0.0`；由 SOURCE_VERSIONS.json 记录，避免写入不兼容的 frontmatter 字段 |
| 仓库副本 | [skills/maintain-fnos-music/SKILL.md](../skills/maintain-fnos-music/SKILL.md) |
| 入口与 UI 数据 | 根目录 AGENTS.md；[agents/openai.yaml](../skills/maintain-fnos-music/agents/openai.yaml) |
| 使用场景 | 续作、双来源升级、功能移植、FPK 构建发行、验证和交接 |
| 可读取的数据 | 来源 JSON、迭代与上游文档、功能说明、实际验证记录、现有构建脚本与工作流 |
| 格式 | skill-creator 的 YAML name/description 与 agents/openai.yaml 规范 |
| 一致性 | SOURCE_VERSIONS.json 保存两份 skill 文件的 SHA256；`python3 scripts/verify_provenance.py` 校验 |
| 分发 | 仓库副本随工程克隆；同名个人 skill 通过 Skills 工作区保存，可用 `$maintain-fnos-music` 调用 |

维护资料只包含工程事实、方法与公开构建证据。Cookie、API 密钥、用户历史与缓存不属于 skill 数据。项目资料不保存旧会话的临时路径，后续任务从当前仓库和当前授权恢复上下文。

## 后续来源升级如何记账

1. 获取两个远端，先比较 JSON 中已采用的 SHA 与新 head；先记录检查结果。
2. 在独立分支普通 merge 原工程，或逐项移植参考工程，记录采用/未采用的范围。
3. 完成对应测试、构建和评审。写入集成提交/PR，保留原始基线和历次采用事件，再推进 `last_integrated_commit` 或 `last_ported_commit`。
4. 同步 README、CHANGELOG 与功能文档。新功能发行后增加发布快照和正式资产摘要；保留旧发行的固定记录。
5. 运行来源校验；修改 skill 时提升版本并同步个人 skill 与仓库副本。

完整命令与冲突规则见 [UPSTREAM.md](UPSTREAM.md)。`--check-git` 需要完整 Git 历史与 reference 基线对象；执行 `git fetch origin --tags`、`git fetch upstream main`、`git fetch reference main` 后使用。

## 待实现：网易云下载的跨平台补源

状态：`planned`，尚未进入 v2.7.1。用户目标为：网易云因账号权益不可下载，或只有低于目标音质的资源时，自动尝试其他平台的同一首歌。

### 已有行为

`proxy/app.py::resolve_netease_url` 只在网易云内部按音质档序尝试，缺省高音质模式从 lossless 向下重试。`_open_online_stream` 与 `_full_fetch_download` 根据曲目原 source 取流，下载失败没有跨平台重搜。容器和管理页按一个主音源运行；现有 musicdl 搜索、元数据、流 API 可作为补源基础，但需要补充生命周期和下载路由。

### 目标行为与实现边界

- 保留网易云歌单与曲目展示入口。在后台下载阶段判断失败或实际音质低于用户目标，再尝试配置的平台顺序；通过用户设置选择严格音质或允许降级。
- 利用 musicdl 聚合能力，按可用平台配置候选来源；下载各平台当前可获取的曲目版本。其他平台是否提供对应资源和音质取决于其可用资源与账号权限。
- 用规范化歌名、歌手、版本标记与时长交叉匹配；排除不同版本、翻唱、Live、伴奏、试听片段等不符候选。证据不足时返回未找到。
- 校验实际完整音频、解码和音质元数据；文件扩展名或转码码率不能证明无损或提升源音质。保存实际来源、平台曲目 ID、匹配依据与真实格式，避免把 MP3 转 FLAC 视为无损。
- 对补源服务采用独立于主音源选择的按需生命周期和请求预算。限流、去重、失败冷却，并在停止、重启和配置变化时正确清理；不通过同时设置多个主音源开关实现。
- 覆盖手动下载与后台整轨落库。在线播放/断点续传作为独立设计：不同平台音频字节不能在同一 Range 请求中途拼接。
- 完成后更新管理页开关、默认值与用户文档、容器启动/配置热更新、Docker COPY、FPK 打包和升级保留流程。

### 实现验收

覆盖无 URL、低于目标音质、正常网易优先、成功跨平台匹配、版本或时长不符、试听排除、全部来源失败、目标音质全部不满足、允许降级、重复并发、服务停止/配置切换、下载来源元数据、多用户隔离及完整文件校验。运行生产依赖合约、完整 CI 与 FPK 构建，再在 fnOS 实机验证下载与落库。以实现提交和实际结果更新状态。
