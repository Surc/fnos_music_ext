# 迭代交接与来源记录

记录日期：2026-10-14（Asia/Shanghai）。机器可读记录见 [SOURCE_VERSIONS.json](../SOURCE_VERSIONS.json)，续作入口见 [AGENTS.md](../AGENTS.md)。

## 2026-10-14 酷我单 RID 播放续查（PR #6 仍未合并/发布）

在原 head `7b00ad42df5534b770d2644ea6f22769167a36b5` 接续，不覆盖正式 v2.8.0。
真实 musicdl 2.14.1 调用到达 CDN/音频时长门：FLAC 接口 200 空正文，MP3/CDN
200 但源站只交付 16009 字节、约 2.612 秒音频，与目标 252 秒不符。最新复测
搜索 5.40 秒返回 30 条含目标，播放 19.58 秒正确拒绝为 502，info 未误标可播。
尚不能判断权益、地区、出口或源策略中的具体原因；没有真实 fnOS 验收。

修复单曲解析与搜索自适应预算耦合、歌词阻塞、代理 GET/HEAD 过早取消，以及
无效 FLAC 挡住完整 MP3 的质量档选择。新增默认 resolve=30 秒 / 酷我代理
startup=40 秒，保留音频头、时长、坏链/试听和无损解码/完整下载检查。
提供不输出临时直链的分阶段日志与 `scripts/diagnose_kuwo.py`；NAS 操作、超时
生效方法与验收分层见 [诊断记录](KUWO_SEARCH_DIAGNOSIS.md)。

专项 78 passed；真实依赖离线合约覆盖合法 FLAC、坏 FLAC 回退合法 MP3、两档
都无效，以及播放不请求可选歌词。GitHub 本轮证据以 PR 当前 head 的 Checks 为准，
不沿用旧 1148 项结果；本地环境与验证范围见 [VALIDATION.md](VALIDATION.md)。
本轮先完成本地提交并交付补丁；GitHub 写入接口返回 403 后，用户于
2026-10-14 明确授权使用已登录网页提交最新补丁、新 CI 通过后合并，暂不发布。
当前提交与 CI 状态以 PR 页面为准；不把旧 head 的 CI 用作新增代码证据。
双来源采用 SHA、skill 1.0.0、VERSION、正式 tag 和附件均保持原记录。

## 2026-10-12 酷我搜索最小修复（未发布）

独立分支 `fix/kuwo-search-discovery` 基于 `34782d1543fd9291613fc47395a8a02a835e16f1`。
真实移动接口首屏第 4 条包含 DAYBREAK'S BELL 原唱 252 秒版 `kuwo:142276943`；
旧库搜索入口在当前环境没有取得响应，此外库内、服务和代理均存在直链前置过滤。
排查不能断言这些就是用户 2026-10-10 漏搜的唯一原因。

酷我改为一页元数据搜索，候选标记待验证，播放按 RID 解析并检查音频头/实际时长。
其他源保持原搜索过滤；网易发现、画像、严格下载补源与主音源互斥保持原结构。
真实搜索在网络超时调整为 10 秒后返回 30 条并包含目标；真实播放解析超时返回
502，未误标可播。新增单元/真实音频头测试、真实 musicdl 依赖离线合约；验证
结果见 [VALIDATION.md](VALIDATION.md)，调用链和复现方法见
[KUWO_SEARCH_DIAGNOSIS.md](KUWO_SEARCH_DIAGNOSIS.md)。

合法空结果和聚合宽限期取消不再误累计熔断。仅酷我避开库中重复 timeout 的
第三方解析路径，其他平台的依赖问题没有全局修改。双来源采用基线、skill 1.0.0、
VERSION 与正式 v2.8.0 保持原记录；不创建 Release，fnOS 实机验收待完成。

提交状态：[PR #6](https://github.com/Surc/fnos_music_ext/pull/6) 已提交至
`Surc/fnos_music_ext:main`，尚未合并或发布。GitHub 集成上传返回 403 后，
经用户授权使用已登录网页提交；远端代码文件树与本地已测试内容完全一致。
代码 head `5413bec8202662e26612a0e9dd51d479614ddfb1` 的
[PR CI](https://github.com/Surc/fnos_music_ext/actions/runs/38243434307)
11 项检查均通过，Python 3.11 / 3.13 各 1148 passed、4 skipped。
后续提交仅登记 PR 与该轮证据；最终 head 的状态以 PR Checks 为准。
不移动正式 tag，双来源采用 SHA 与 skill 版本不变。

## 两个来源的采用基线

| 来源与角色 | 跟踪分支 | 本次采用版本 | 固定提交 | Git tag 情况 |
| --- | --- | --- | --- | --- |
| [javycoder/fnos_music_ext](https://github.com/javycoder/fnos_music_ext)，基础工程 | `main`；本地远端 `upstream` | VERSION：`2.8.0` | `6973402df5e180cd49a00af4db48758504303c58` | `v2.8.0` 为 annotated tag，解析后指向此提交；对象 `71e721bbc0495603532a9117f0fd218f50bdd805` |
| [gzywd/fnos_music_ext](https://github.com/gzywd/fnos_music_ext)，发现功能参考 | `main`；本地远端 `reference` | VERSION：`2.9.30` | `08b0b23486d289619ab73575f42aa0d20e78c2b3` | 查询 `refs/tags/v2.9.30` 返回 404；使用 VERSION + SHA 锁定 |

2026-10-11 核对 GitHub：两个远端默认分支都是 main，本次采用的 head 如上。最初采用的 javycoder v2.7.0 `f036e1f7082363c5f5e4892d15209ce584b0769b` 保留在 JSON 的 baseline 中；表中是当前采用快照。原工程经普通 merge 保留完整历史；参考工程仅移植选定功能。

参考范围为 `proxy/playlists.py` 的发现分类、命名、顺序与缓存思路，以及 `musicbox-service/netease_ext.py` 的推荐歌单、私人 FM、分类与新碟接口语义。集成实现落在 `proxy/discovery.py`、`proxy/personalization.py`、`musicbox-service/discovery.py`，并接入代理、推荐、WebUI、容器及打包流程。原工程的部署、播放、数据恢复体系继续作为基础。

## 2026-10-11 上游合并完成

原工程 main / dev 与正式 v2.8.0 tag 指向 `6973402df5e180cd49a00af4db48758504303c58`，比既有基础多 30 个提交；参考 main 未变，仍为 `08b0b23486d289619ab73575f42aa0d20e78c2b3`。[PR #4](https://github.com/Surc/fnos_music_ext/pull/4) 已普通合并至 main，合并提交 `1fe3adf71c7f9a04e6fd1b7429c58d5120c270b7`；原始冲突解法保存在 `d56b8da17b4241024f5efa42ecfe85ac6a6d6c7e`。进入 main 后追加采用事件并推进 SHA。

包含 LX 多脚本启用、脚本兼容与缓存/熔断、播放跳歌修复、下载音质、储存目录、关于/日志与原生部署。冲突处理中重新接入六类发现、用户画像及严格下载补源，补齐原生备用进程、端口和 LX 补丁升级；保存目录的新入口为「储存 → 目录设置」。发现列表独立保留可播/非试听过滤，详情补全仍保留封面。Skill 1.0.0 与文件摘要未改变。

[PR CI](https://github.com/Surc/fnos_music_ext/actions/runs/38113398107) 11 项检查全部成功，验证 head 为 `4be7b7822bc71927dfc7cd24d1a916b031357561`，Python 3.11 / 3.13 各 1116 passed、4 skipped。Docker 与原生配置的下载补源真实进程合约、六组生产依赖合约和 FPK 构建/校验均通过。合并后的 [main CI](https://github.com/Surc/fnos_music_ext/actions/runs/38113626387) 同样 11 项成功、两版本各 1116 passed、4 skipped。跳过项和实机范围见 [VALIDATION.md](VALIDATION.md)。

本次不移动已发布 tag，不创建新 Release。musicdl 服务搜索流程及依赖重复 timeout 参数尚未独立修复；真实平台与 fnOS 验收另行记录。

## 已发布与继续开发

- VERSION `2.8.0`；最新正式发行 [v2.8.0](https://github.com/Surc/fnos_music_ext/releases/tag/v2.8.0)，于 2026-10-09 22:25:18（Asia/Shanghai）发布，tag 固定在 `3e9c6cdb4f8e90416457dde9a3dc3984831b023b`。正式附件及固定 tag 摘要已登记在 SOURCE_VERSIONS.json。
- v2.8.0 正式 FPK 为 `fnmusic-ext-2.8.0.fpk`，27,117,969 字节，SHA256：`22a3bc847d2cb8e929cf9d1832dfd7325e143e0ba800e01b8ce528dbb798790e`；配套 `.sha256` 为 88 字节。以 Release 工作流附件为准，同提交的 CI 构建包字节可能不同。
- 发行提交的 [CI](https://github.com/Surc/fnos_music_ext/actions/runs/37943422317) 11 项全部成功，Python 3.11 / 3.13 各 857 passed；[Release 工作流](https://github.com/Surc/fnos_music_ext/actions/runs/37944046519) 成功，打包测试 67 passed，正式 FPK 和校验文件已上传。
- v2.8.0 功能合并：[PR #3](https://github.com/Surc/fnos_music_ext/pull/3)，合并提交 `981c6b19b0a27a7bb24020c3a0a01b0cce691570`；[main CI](https://github.com/Surc/fnos_music_ext/actions/runs/37922716170) 11 项全部成功，Python 两版本各 857 passed。
- v2.7.1 功能合并：[PR #1](https://github.com/Surc/fnos_music_ext/pull/1)，合并提交 `0d5d2451d7acfa8f0162b89d628083d33f59c161`。
- 历史正式发行：[v2.7.1](https://github.com/Surc/fnos_music_ext/releases/tag/v2.7.1)，tag 固定在 `72df1bb053f9769ea13389fc7fffbe903f80541a`，含已更新的集成版文档。
- v2.7.1 正式 FPK 的 SHA256：`33dc5dc28c714f016ae1c403519075ad727425fe9f543ce4ecb1675681506bd6`。以 Release 附件及其配套校验文件为准；不同构建的包字节可能不同。
- 发行后的 main 追加完整资产摘要与交接资料；保留 v2.7.1、v2.8.0 的固定 tag 和来源采用历史。通过 [验证记录](VALIDATION.md) 区分 v2.7.1 的两版本各 807 passed 与 v2.8.0 的各 857 passed。
- v2.7.1 发布提交的 [CI](https://github.com/Surc/fnos_music_ext/actions/runs/37907664958) 与 [Release 工作流](https://github.com/Surc/fnos_music_ext/actions/runs/37908174138) 均成功。两个发行版的 fnOS 实机安装、升级及真实账号行为仍待验收。

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

## 本次独立实现：网易云下载的跨平台补源

状态：`released`，已包含在 [v2.8.0](https://github.com/Surc/fnos_music_ext/releases/tag/v2.8.0)。功能见 [PR #3](https://github.com/Surc/fnos_music_ext/pull/3)，实现分支 `feat/download-fallback`；已验证代码提交 `8550ecd63dbd40b197ce2d741591916111971a04` 的 [完整 CI](https://github.com/Surc/fnos_music_ext/actions/runs/37921652490) 11 项全部成功，Python 3.11 / 3.13 各 857 passed，包含真实容器契约与 FPK 构建校验。正式 tag、发行提交与资产摘要已登记；真实账号与 NAS 验收待完成。用户目标为：网易云因账号权益不可下载，或只有低于目标音质的资源时，自动尝试其他平台的同一首歌。

### v2.7.1 的已有行为

`proxy/app.py::resolve_netease_url` 只在网易云内部按音质档序尝试，缺省高音质模式从 lossless 向下重试。`_open_online_stream` 与 `_full_fetch_download` 根据曲目原 source 取流，下载失败没有跨平台重搜。容器和管理页按一个主音源运行；现有 musicdl 搜索、元数据、流 API 可作为补源基础，但需要补充生命周期和下载路由。

### 上游计划核对（2026-10-09）

当时两个 main 的 head 分别为 javycoder `f036e1f` 和 gzywd `08b0b23`；该段记录 2026-10-09 的计划核对，不代表当前 main 的采用 SHA。javycoder 的 [#39](https://github.com/javycoder/fnos_music_ext/issues/39#issuecomment-6064611243) 作者表示以后再试、目前仍一个音源；[#47](https://github.com/javycoder/fnos_music_ext/issues/47) 提出失败切源与目录需求，但未发现对应实现 PR 或确定版本承诺。其 dev 没有新增补源实现，Automatic_iteration 分支是旧搜索重试工作；gzywd 当前代码主要面向单一网易源。用户要求我们自己实现，因此本次以现有 musicdl API 适配独立下载链路，没有从上游推进采用 SHA。

### v2.8.0 实现

- `proxy/download_fallback.py`：配置、严格同曲匹配、全文件下载、实际编码 / 时长 / 解码验证、策略和来源伴随文件。
- `proxy/app.py`：手动下载及后台整轨保存接入；按 GUID 去重、限流、失败冷却及配置变化取消；低档缓存不阻止重新检查网易权益，真实扩展名及完整路径引用避免旧 MP3 遮住升级后的 FLAC。用户官方绑定仍使用各自请求凭证。
- `proxy/fallback_control.py`：现有非 root 容器内的私有 Socket 租约控制；独立 `musicdl-fallback` 进程按需启停，平台配置变化失效旧租约，停止 / 重启清理。
- 容器、主机端口映射、管理页六个配置键、默认关闭和 FPK 模块已接入。使用与保存目录见 [DOWNLOAD_FALLBACK.md](DOWNLOAD_FALLBACK.md)。

首版不改变网易列表的权限过滤，只处理有完整元数据的已知曲目；不混接流式 / Range 字节。音质判断描述真实文件编码，不能证明平台母带未经过有损转码。失败时保留现有低档文件，严格模式不将其作为下载成功返回；App 标准档仍可能对源文件转成 MP3。

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

本次新增单元与音频测试在本地执行；真实 Unix Socket 测试在本地因系统权限被拒而跳过，已在 GitHub Linux CI 通过。新增 `tests/integration/download_fallback_contract.py` 在实际镜像中验证非 root、按需启动、共享租约、平台切换、空闲停止、关闭和 Socket 清理，结果通过。详细结果以 [VALIDATION.md](VALIDATION.md) 的本次记录为准，旧 807 passed 不作为新功能证据。
