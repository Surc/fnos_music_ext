# fnmusic-ext 飞牛音乐扩展 · 网易云发现与本地画像集成版

[![CI](https://github.com/Surc/fnos_music_ext/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Surc/fnos_music_ext/actions/workflows/ci.yml)

本仓库由 **Surc** 维护，基于 `javycoder/fnos_music_ext` v2.7.0，开发版本 **v2.8.0**，正式 Release 仍为 **v2.7.1**。在原工程的三音源切换、在线搜播、歌词封面、边听边存和 AI 推荐基础上，加入 **网易云发现歌单、私人 FM、按飞牛用户自己历史与收藏优化的每日推荐**；v2.8.0 候选新增可配置的 **网易云下载跨平台补源**。保留原工程完整 Git 历史，方便持续同步上游。

代理在宿主机接管飞牛音乐 Unix Socket，音源与管理页运行在一个 Docker 容器。飞牛官方程序、nginx 配置与数据库保持原样，本地画像只读官方数据库；停止扩展可恢复官方直连。

**开始使用：** [下载 FPK](https://github.com/Surc/fnos_music_ext/releases) → fnOS 应用中心手动安装 → 选择网易云 → 管理页扫码登录 →「播放与推荐」开启发现与本地画像。

| 你想做什么 | 说明入口 |
| --- | --- |
| 安装、从原版升级、迁移脚本部署 | [安装与部署](docs/INSTALL.md) |
| 打开新功能、区分两种推荐、调整顺序 | [网易云发现与本地画像](docs/PERSONALIZATION.md) |
| 网易下载失败或音质不足时尝试其他平台 | [下载补源：开启、目录与边界](docs/DOWNLOAD_FALLBACK.md) |
| 打包 FPK、跟进原工程、解决冲突 | [打包与上游同步](docs/UPSTREAM.md) |
| 查看实际测试与实机验证范围 | [验证记录](docs/VALIDATION.md) |
| 交给部署 Agent 操作 | [Agent 安装说明](docs/AGENT_INSTALL.md) |
| 接续迭代、核对双来源版本与维护 skill | [迭代交接](docs/ITERATION.md) · [来源清单](SOURCE_VERSIONS.json) · [Agent 入口](AGENTS.md) |

## 本版新增功能

| 功能 | 实际行为 | 使用条件 |
| --- | --- | --- |
| 我的及收藏歌单 | 网易账号自建歌单和订阅的他人歌单 | 网易云音源 + 扫码登录 |
| 为你推荐歌单 | 当前网易账号的官方推荐歌单 | 网易云音源 + 扫码登录 |
| 排行榜 | 按网易云榜单浏览曲目 | 网易云音源 |
| 分类歌单 | 按华语、怀旧、流行等分类浏览，可调数量 | 网易云音源 |
| 新碟上架 | 浏览新专辑与专辑曲目 | 网易云音源 |
| 私人 FM | 当前网易账号的 FM 候选，以五分钟缓存的歌单快照展示 | 网易云音源 + 扫码登录 |
| 飞牛本地画像 | 当前飞牛用户的播放次数、近期历史和收藏用于每日推荐 | 开启「结合我的飞牛历史和收藏」 |
| 顺序与缓存 | 中文上移 / 下移、过期后台刷新、每日缓存预热 | 管理页「播放与推荐」 |
| 持续迭代与打包 | 上游同步脚本、同步 PR 工作流、FPK 在线打包 | Git / GitHub Actions |
| 下载跨平台补源（v2.8.0 候选） | 网易整轨不可用或音质不足时，严格匹配其他平台的完整同版音频，记录实际来源 | 网易主音源 + 管理页「边听边存」显式开启；v2.7.1 不含 |

**个性化范围：** 飞牛「每日推荐」按每个飞牛用户自己的记录处理；「为你推荐歌单」「私人 FM」由 NAS 上当前登录的同一个网易云账号提供。家庭成员的飞牛画像、收藏独立，但网易云账号仍共享，本版没有实现每个飞牛用户分别绑定网易云账号。

发现歌单为只读，不把加歌、移歌、删歌或本地听歌事件写回网易云。曲目按账号可播放权限过滤，实际数量可能小于云端总数。下载补源首版不改变此过滤，只处理可取得完整元数据的已知曲目。

## 保留的基础能力

- **三主音源互斥切换**：网易云 musicbox、[musicdl](https://github.com/CharlesPikachu/musicdl) 多平台聚合、洛雪 lxmusic 自定义源，同一时间启用一个主音源；下载补源使用按需辅助服务。
- **原生在线搜播**：飞牛音乐 Web / App 搜索结果本地优先，支持播放、歌词、封面与收藏；规则见 [搜索分页](docs/SEARCH_PAGINATION.md)。
- **音质与保存**：高音质 / 平衡 / 流畅、边听边存、保存到本地曲库、封面内嵌和可选 `.lrc` 下载。
- **推荐与多用户**：独立热门 / 每日推荐，用户各自的收藏和历史，可选 OpenAI 兼容 API 的 AI 补充。
- **管理与恢复**：飞牛管理员通过统一网关进入管理页；常用设置保存后生效，支持启停、升级备份和还原直连。

## 安装并开启新功能

### FPK 安装 / 升级

前提：fnOS 已安装并启动官方「飞牛音乐」，应用中心已安装 Docker，并使用飞牛管理员操作。

1. 从 [本仓库 Releases](https://github.com/Surc/fnos_music_ext/releases) 下载 `fnmusic-ext-2.7.1.fpk` 与对应 `.sha256`，在「应用中心 → 手动安装」选择 FPK。
2. 初始音源选择 **网易云 musicbox**，安装后打开桌面「**fnMusic 扩展管理**」（`/app/fnmusic-ext`）。
3. 在「**音乐源**」完成网易云扫码登录。
4. 在「**播放与推荐**」开启「**每日推荐**」「**结合我的飞牛历史和收藏**」。
5. 同页「**网易云发现**」开启「**显示发现歌单**」，勾选六类内容，设置分类名称、每类数量和预热时间。
6. 用「**上移 / 下移**」调整分类顺序，点击「**保存并生效**」，回到飞牛音乐刷新歌单列表。首次抓取会在后台执行，可稍后刷新。

新安装默认写入发现与本地画像开关；已有 `.env` 保留自定义值。只替换源码、未执行升级安装时，新配置可能缺失，应在管理页显式开启并保存。管理员控制开关，各用户在自己的飞牛音乐账号下使用独立画像。

本版「我的及收藏歌单」已包含自建和订阅歌单，无需同时开启原工程「音乐源 → 网易账号歌单」。取消 `mine` 类别或关闭发现后，旧开关仍可单独提供自建歌单。

已有原版 FPK 时，在应用中心安装本版作为同应用升级；应用 ID 仍为 `fnmusic-ext`，升级流程备份并恢复配置、登录态、收藏与历史。脚本部署迁移、部署冲突处理见 [INSTALL.md](docs/INSTALL.md)。

v2.8.0 的下载补源在「边听边存」开启，选择备用平台顺序、目标编码与是否允许降级；保存目录仍由该页「保存路径」决定。文件旁记录实际平台 / 曲目 ID 和音频信息。严格模式未找到完整同版资源就失败；无损编码检测不能证明母带未经过有损转码。当前候选版本及实际测试状态见 [补源说明](docs/DOWNLOAD_FALLBACK.md) 和 [迭代记录](docs/ITERATION.md)。

### 脚本安装

```bash
sudo apt-get update && sudo apt-get install -y python3 python3-venv git
git clone https://github.com/Surc/fnos_music_ext.git fnmusic_ext
cd fnmusic_ext
chmod +x install.sh extend.sh restore.sh proxy/run_proxy.sh
./install.sh --non-interactive --sources musicbox --webui --extend
```

随后在管理页扫码并按上面的步骤开启功能。musicdl、洛雪和交互安装见 [安装指南](docs/INSTALL.md)。洛雪脚本由使用者提供，支持 URL、上传 `.js` 或从 NAS 选择；FPK 向导不要求填写脚本，装好后配置。

## 新功能配置参考

建议用管理页设置；高级用户可编辑部署目录的 `.env`，不要用模板覆盖现有配置。下表为新安装默认值，完整键项见 [.env.example](.env.example)。

| 配置键 | 默认值 | 作用 |
| --- | --- | --- |
| `FNMUSIC_DISCOVERY_ENABLED` | `true` | 发现总开关，还需启用网易云音源 |
| `FNMUSIC_PERSONALIZATION_ENABLED` | `true` | 每日推荐结合当前飞牛用户的历史与收藏 |
| `FNMUSIC_NETEASE_CHANNELS` | `mine,nrec,toplist,category,newalbum,fm` | 显示的发现类别 |
| `FNMUSIC_NETEASE_CHANNEL_ORDER` | `daily,hot,mine,nrec,toplist,category,newalbum,fm` | 每日、热门与六类发现的顺序 |
| `FNMUSIC_NETEASE_CHANNEL_LIMIT` | `8` | 每类最多歌单 / 专辑数，范围 1–50；FM 为一个歌单 |
| `FNMUSIC_NETEASE_CATEGORY` | `华语` | 分类歌单名称 |
| `FNMUSIC_PLAYLIST_TRACK_LIMIT` | `300` | 普通歌单 / 专辑最多获取曲目数 |
| `FNMUSIC_PLAYLIST_TRACK_CACHE_TTL` | `21600` | 普通曲目缓存秒数；FM 固定五分钟 |
| `FNMUSIC_PLAYLIST_REFRESH_AT` | `04:30` | NAS 本地时间每日预热；留空关闭定时预热 |
| `FNMUSIC_PERSONALIZATION_REFRESH_S` | `21600` | 每日推荐缓存最长六小时，跨日也重建 |
| `FNMUSIC_PERSONALIZATION_MIN_REFRESH_S` | `1800` | 画像变化时最短重建间隔，默认半小时 |
| `FNMUSIC_NETEASE_PLAYLIST_ORDER` | 空 | 高级单歌单优先级，填 `daily` / `hot` 或虚拟歌单 GUID |

原有音质、边听边存、歌词、推荐和 AI 设置继续有效：`FNMUSIC_QUALITY_MODE`、`FNMUSIC_TEE_SAVE_ENABLED`、`FNMUSIC_LYRIC_AUTO_DL`、`FNMUSIC_RECOMMEND_DAILY` / `FNMUSIC_RECOMMEND_HOT`、`FNMUSIC_LLM_*`。音源开关继续三选一。

## FPK 打包与持续迭代

**在线打包：** Actions → **Build FPK** → Run workflow，选择 `main`；成功后从 **Artifacts → fnmusic-ext-fpk** 下载 ZIP，解压取出 FPK 和 SHA256。已发布版本从 Releases 下载。

**本地打包：** 需要 `rsync`，缺少 fnpack 时脚本会下载官方版本。

```bash
sudo apt-get update && sudo apt-get install -y rsync
bash ./packaging/fpk/build.sh
```

产物为 `dist/fnmusic-ext-<VERSION>.fpk` 和 `.sha256`，版本来源为 [VERSION](VERSION)。本地 `.env`、登录数据、历史、收藏和发现缓存不会打入安装包。

**同步原工程：** Actions → **Sync upstream**，或在干净工作区执行 `bash ./scripts/sync_upstream.sh`。先创建同步分支、合并上游并验证，再创建 PR；有冲突时停止，不自动合并 `main`。权限设置、冲突处理和发行流程见 [UPSTREAM.md](docs/UPSTREAM.md)。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 没有六类发现内容 | 确认运行本集成版、网易云音源与发现总开关已开启；首次抓取后刷新 |
| 有公共榜单，没有我的歌单 / 推荐 / FM | 私人内容需要有效网易云扫码登录 |
| 收藏的他人歌单不显示 | 开启本版「我的及收藏歌单」；旧「网易账号歌单」只提供自建歌单 |
| 家庭成员看到相同 FM / 网易推荐歌单 | 候选来自 NAS 上共享的网易云账号；本地画像只处理飞牛侧每日推荐 |
| 刚听歌 / 收藏后推荐没变 | 默认半小时冷却，六小时或跨日重建，访问歌单时后台触发 |
| 歌单曲目很少 / 为空 | 检查账号权限、版权、网络和音源服务；可播过滤可能减少曲目 |
| WebUI 打不开 / 没有新开关 | 用飞牛管理员从桌面图标进入；确认 WebUI 已启用，升级完成、容器已重建，并刷新浏览器 |

```bash
./extend.sh          # 启用 / 自检；手动改 .env 后重新验收
./restore.sh         # 恢复官方直连，保留配置和数据
curl -s --unix-socket /var/run/trim_music.socket http://localhost/_ext/healthz
```

## 开发与验证

新增模块为 `musicbox-service/discovery.py`（网易云 API）、`proxy/discovery.py`（歌单与缓存）、`proxy/personalization.py`（用户画像），由原有代理、推荐和管理页接入。固定上游提交与维护边界见 [UPSTREAM.md](docs/UPSTREAM.md)。

v2.7.1 的 GitHub CI 已通过 **11 项检查**：Python 3.11 / 3.13 各 **807 个测试**、六组生产依赖合约、Docker 及 FPK。v2.8.0 补源候选的本地相关回归为 **197 passed、1 Socket 权限跳过**，本地 FPK 已构建；新代码的完整 CI 尚待运行。各版本证据分开记录在 [VALIDATION.md](docs/VALIDATION.md)。**fnOS 实机安装、升级与真实账号下载播放尚未验收。**

```bash
python3 -m pytest
node --check webui-service/static/app.js
```

## 来源与许可

- 基础工程：[javycoder/fnos_music_ext](https://github.com/javycoder/fnos_music_ext)，保留 MIT 许可证与完整提交历史。
- 功能参考：[gzywd/fnos_music_ext](https://github.com/gzywd/fnos_music_ext) v2.9.30，按接口语义适配发现分类、推荐、FM、新碟和缓存；范围见 [移植说明](docs/UPSTREAM.md)。
- 音源依赖：[darknessomi/musicbox](https://github.com/darknessomi/musicbox)、[CharlesPikachu/musicdl](https://github.com/CharlesPikachu/musicdl) 和洛雪音乐社区。

代码许可见 [LICENSE](LICENSE)。音乐版权与账号权益由原平台决定，请支持正版并遵守平台协议。洛雪源由使用者提供，导入前确认脚本来源。
