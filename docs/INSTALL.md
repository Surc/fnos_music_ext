# 集成版安装、升级与部署指南

适用于 [Surc/fnos_music_ext](https://github.com/Surc/fnos_music_ext)。正式 Release v2.7.1 增加网易云发现、私人 FM 和每个飞牛用户自己的历史 / 收藏画像；开发版本 v2.8.0 候选增加 [网易下载跨平台补源](DOWNLOAD_FALLBACK.md)，尚未发布。功能设置见 [发现与推荐说明](PERSONALIZATION.md)，打包与原工程更新见 [UPSTREAM.md](UPSTREAM.md)。

适用环境：飞牛 NAS（fnOS）已安装并启动「飞牛音乐」官方应用。本项目采用无侵入接管设计，**完全不修改**飞牛官方 nginx 配置、不 Patch 官方二进制、不改动官方数据库。

> 💡 **自动化部署提示**：若使用 AI Agent（如 OpenCode、Claude Code、Cursor 等）进行全流程自动化部署与自检验收，请直接查阅 [Agent 安装提示词](AGENT_INSTALL.md)。

---

## 0. 前置准备

- **操作系统**：fnOS（Debian 12 基础系统）；
- **基础运行组件**：Python 3.11+ 及 `python3-venv`（核心代理运行在宿主机）：
  ```bash
  sudo apt-get update && sudo apt-get install -y python3 python3-venv git
  ```
- **管理员权限**：具备 `sudo` 执行权限的管理员账号（非交互安装需免密 sudo）；
- **官方音乐应用**：必须先在 fnOS「应用中心」安装并启动「飞牛音乐」（确保存在 `/var/run/trim_music.socket`）；
- **Docker（必需）**：v2.0.0 起**仅支持 Docker 部署**，必须先在 fnOS「应用中心」安装 Docker。未安装 Docker 的机器执行 `install.sh` 会直接报错退出；**脚本绝不会擅自安装 Docker 引擎**。

克隆项目并进入根目录赋予执行权限（**仅脚本安装需要**，fpk 安装可跳过）：

```bash
git clone https://github.com/Surc/fnos_music_ext.git fnmusic_ext
cd fnmusic_ext
chmod +x install.sh extend.sh restore.sh proxy/run_proxy.sh
```

---

## 1. 部署形态说明

v2.0.0 起部署形态固定为两部分：

1. **核心代理（宿主机 systemd）**：`fnmusic-ext.service` 运行在项目内独立虚拟环境 `.venv-proxy`，负责零侵入接管 `/var/run/trim_music.socket`。核心代理必须在宿主机运行——塞入容器会面临跨容器 socket 权限穿透问题。
2. **音源单容器（Docker）**：`fnmusic-sources` 由 supervisor 管理 musicdl / musicbox / lxmusic / WebUI 与辅助程序，**按 `.env` 启动当前主音源**。v2.8.0 候选开启补源时另启轻量控制服务，独立 musicdl 备用进程仅下载时启动、空闲后停止。运行期切源在 WebUI 内完成（写配置 + 同容器 stop/start）。升级到含新辅助模块 / 端口的版本需执行正常 FPK 升级或 `install.sh` 重建容器。

| 端口 | 绑定地址 | 用途 |
| :--- | :--- | :--- |
| 8768 | 127.0.0.1 | musicdl 音源（仅代理访问） |
| 8770 | 127.0.0.1 | musicbox 音源（仅本机；扫码走已登录的管理页） |
| 8772 | 127.0.0.1 | lxmusic 音源（仅代理访问） |
| 8774 | 127.0.0.1 | 管理 WebUI（仅本机；浏览器走飞牛网关，仅管理员） |
| 8776 | 127.0.0.1 | v2.8.0 候选下载补源，按需启动（仅代理访问） |

数据卷：项目目录 `sources-data/`（网易登录态、洛雪源脚本缓存与状态）；仓库目录挂载到容器 `/repo`（WebUI 读写 `.env` 用）。容器无特权、不挂 docker.sock。

---

## 2. 一键安装与配置

### 方式 A：应用中心 fpk 安装（推荐）

从 [本仓库 GitHub Releases](https://github.com/Surc/fnos_music_ext/releases) 下载 `fnmusic-ext-<版本>.fpk` 与对应 `.sha256`，在 fnOS「应用中心 → 手动安装」选择 FPK。自行构建可用 Actions → **Build FPK** → Run workflow，成功后下载 **Artifacts → fnmusic-ext-fpk** 并解压。

1. 向导中选择**初始音源**；使用本版网易云发现请选择 musicbox。选择 lxmusic 时向导不要求源脚本，装好后在管理页配置；
2. 保持「安装完成后立即启用扩展」开启，安装即自动完成容器构建、代理接管与全链路验收；
3. 桌面出现「fnMusic 扩展管理」图标，用飞牛管理员点击打开（`/app/fnmusic-ext`）；在「音乐源」完成网易扫码，在「播放与推荐」开启「每日推荐」「结合我的飞牛历史和收藏」「显示发现歌单」，勾选类别后「保存并生效」。回到飞牛音乐刷新列表。

### 从原版 FPK 升级

应用 ID 沿用 `fnmusic-ext`，直接在应用中心安装本版作为同应用升级。升级会备份并恢复原有 `.env`、网易云登录、收藏、播放历史等数据；不要并行部署另一份代理。已有自定义开关值会保留，升级后请检查「播放与推荐」的新设置。新键缺失时可在管理页开启并保存，不要直接用 `.env.example` 覆盖现有配置。

家庭成员的飞牛画像独立，网易云发现仍使用 NAS 上共享的扫码账号；并未增加逐用户绑定网易账号。私人歌单、推荐和 FM 需要有效网易云登录。

下载补源升级后默认关闭，在「边听边存」显式开启。保存位置沿用同页「保存路径」，可填写 NAS 文件系统目录；留空优先官方共享音乐库目录，否则落扩展缓存。开启不改变网易曲目列表的权限过滤。目标、降级策略与来源文件说明见 [DOWNLOAD_FALLBACK.md](DOWNLOAD_FALLBACK.md)。

日常启停在应用中心完成：「停止」秒级还原官方直连，「启动」恢复扩展。卸载会先自动备份配置与数据到存储卷根目录（`fnmusic-ext-backup-<时间戳>.tar.gz`）再清理。

命令行等价操作（需 root）：

```bash
sudo appcenter-cli install-fpk fnmusic-ext-<版本>.fpk   # 安装
sudo appcenter-cli list                                 # 查看状态
sudo appcenter-cli stop fnmusic-ext                     # 停止（还原官方直连）
sudo appcenter-cli start fnmusic-ext                    # 启动（恢复扩展）
sudo appcenter-cli uninstall fnmusic-ext                # 卸载（自动备份后清理）
```

> 升级请在应用中心内安装新版本 fpk；`install-fpk` 在已安装时不会自动升级。升级脚本会自动备份并恢复用户数据（.env、网易云登录、收藏、播放历史）。
>
> 曾用 git clone 脚本方式部署的用户：请先在原部署目录执行 `sudo ./restore.sh` 释放部署登记，再安装 fpk，否则安装会因部署冲突而中止（保护既有部署不被静默接管）。

### 方式 B：交互向导安装（脚本方式）

```bash
./install.sh
```

向导自动执行环境预检，依次引导：

1. **音源三选一**（v2.0.0 起互斥单选）：
   - `1` 网易云 musicbox：安装后自动进入扫码登录；
   - `2` musicdl：进入平台多选子菜单（默认精选酷我+咪咕；全部平台编号见 [../musicdl-service/PLATFORMS.md](../musicdl-service/PLATFORMS.md)）；
   - `3` 洛雪 lxmusic：直接安装（无源状态），源脚本装好在管理页 WebUI 配置；也可在安装命令附 `--lx-source-url`（URL / 本机 `.js` 路径），安装时进行「下载→初始化→搜索→解析→探活」全链路校验；
2. **是否安装管理 WebUI**（仅本机 8774，默认否；打开时走飞牛管理员登录）；
3. **大模型每日推荐（可选）**：OpenAI 兼容 API；本地画像模式下也可补充网易日推，没有模型配置仍能使用网易发现和本地画像；
4. **一键启用**：确认后自动调用 `./extend.sh` 接管验收。

### 非交互静默部署（自动化脚本）

```bash
# 网易云 + WebUI
./install.sh --non-interactive --sources musicbox --webui --extend

# musicdl（酷我+咪咕；平台粒度用短名或编号）
./install.sh --non-interactive --sources musicdl-kuwo,musicdl-migu --extend

# 洛雪自定义源（--lx-source-url 可选：http(s) URL / 本机 .js 路径 / 留空无源安装）
./install.sh --non-interactive --sources lxmusic \
  --lx-source-url 'https://example.com/your-source.js' --extend
./install.sh --non-interactive --sources lxmusic \
  --lx-source-url "$HOME/scripts/my-source.js" --extend   # 本机路径自动复制进数据卷
./install.sh --non-interactive --sources lxmusic --webui --extend  # 无源安装，装后在管理页配置

# 附带大模型推荐兜底（密钥仅写入本地 .env，权限 600）
./install.sh --non-interactive --sources musicdl --enable-recommend \
  --llm-base-url 'https://api.openai.com/v1' \
  --llm-api-key '<KEY>' --llm-model 'gpt-4o-mini' --extend
```

常用参数：`--sources`（音源三选一）、`--lx-source-url`（可选：http(s) URL 或
宿主机 `.js` 路径，路径会自动复制进数据卷）、`--lx-skip-verify`
（跳过洛雪源可用性校验直接激活，源是否可用装好后在管理页 WebUI 查看）、
`--webui` / `--no-webui`、`--extend`（安装后自动接管）、`--adopt`（迁移部署登记）、
`--qr`（仅扫码登录）。`--mode` 参数已随 host 模式移除。

---

## 3. 启用、切换与还原

```bash
# 启用扩展接管（含全链路验收，失败自动回滚）
./extend.sh

# 手动改 .env 后使其对容器生效（重启容器重选进程集 + 重新验收）
./extend.sh

# 还原官方原生直连（停止音源容器；保留 .env 与全部数据）
./restore.sh

# 彻底清理卸载（额外删除 .env、登录态、缓存、收藏、历史）
./restore.sh --full
```

**运行期换源/调参**：用飞牛管理员打开桌面「fnMusic 扩展管理」（若已装 WebUI），
在「音乐源」分区单选切换——写配置与容器内进程切换一步完成，代理侧由热重载
同步，全程无需命令行。音质偏好、推荐开关、边听边存、LLM 同理。

### 单机多副本约束与部署迁移（--adopt）

代理单元名、音源容器名与安装锁在本机全局唯一，且本机会登记当前部署目录
（`/var/lib/fnmusic-ext/deployment`）。因此：

- 请只维护一份部署目录。从**另一份仍存在的仓库副本**执行安装/扩展/还原会被
  拒绝，并提示先回到原部署目录操作；原目录执行 `./restore.sh` 释放部署后，
  新目录即可正常安装；
- 确认要把部署迁移到当前目录（例如旧目录准备废弃）时，追加 `--adopt`：
  ```bash
  ./install.sh --non-interactive --sources musicbox --webui --adopt --extend
  ```
  接管成功后登记自动指向当前目录。`--adopt` 同时作用于部署登记与代理 unit 的
  跨目录检查；`extend.sh` / `restore.sh` 同样支持 `--adopt`；
- 原登记目录或代理 unit 指向的目录已被删除时不拦截，任意目录可直接重新安装
  （无法保护的废弃部署会被自动接管）；
- 安装锁若被**本副本**挂起的旧进程占用（如向导停在扫码登录），新命令会自动
  终止旧进程并接管；锁若属于另一份副本或无关进程则绝不终止，仅报告后退出。

---

## 4. 网易云登录扫码（musicbox 源）

网易云部分 VIP 或无损音质曲目需要登录。任选其一：

```bash
# 终端交互式扫码（推荐；ASCII 二维码、过期自动刷新）
./install.sh --qr        # 或 ./netease_login.sh / ./extend.sh --qr
```

或登录管理页后在「音乐源」扫码。
登录凭证持久化在 `sources-data/`，无需重复扫码。

---

## 5. 洛雪自定义源（lxmusic 源）

播放解析依赖你提供的洛雪自定义源脚本（社区格式，`@name/@version` 头部注释的 JS）：

- **管理页配置（推荐）**：WebUI「音乐源 → 洛雪自定义源」提供三种方式——
  1. **粘贴 URL**：`http(s)://.../*.js`（原有方式）；
  2. **上传 .js 文件**：从电脑选择脚本上传，落盘到数据卷
     `sources-data/lxmusic/uploads/`，以 `file:///data/lxmusic/uploads/<名字>.js`
     形态参与后续流程（随数据卷持久化，重启/升级不丢）；
  3. **从 NAS 选择**：飞牛桌面内打开管理页时可用（走 fnOS 开放 API
     `pickUserFile` 文件选择器；直连 8774 的浏览器环境自动隐藏该按钮），
     选中 NAS 上的 `.js` 后由宿主侧网关代读内容，等效于上传。
  任一方式选定后点「测试」（下载→初始化→内置搜索→128k 解析→Range 探活），
  通过后保存即热切换；
- **安装时配置（可选）**：非交互 `--lx-source-url` 接受 `http(s)` URL 或宿主机
  `.js` 文件路径（自动复制进数据卷并转 `file://`）；安装器在容器内运行
  `verify_source.py` 做全链路校验，失败按分类提示。不提供则无源安装，
  装好后在管理页配置；fpk 安装向导不再出现任何洛雪源输入项；
- **生效范围**：源脚本只在容器内 Node 沙箱中运行、仅可发起 HTTP 请求；
  搜索/歌词/热门榜单始终走内置平台接口，不依赖源脚本；
- 未配置源时 lxmusic 的播放解析不可用（搜索/榜单不受影响），`/healthz` 的
  `user_source` 字段与 `./extend.sh` 输出均会提示。

源脚本是第三方代码，请仅使用可信来源，并仅访问您有权收听的内容。

---

## 6. 推荐歌单工作机制

本版把每日 / 热门推荐与六类网易云发现接入原生歌单列表；显示内容和顺序由管理页「播放与推荐」控制，详细行为见 [PERSONALIZATION.md](PERSONALIZATION.md)。

**「每日推荐 MM-DD」**（`FNMUSIC_RECOMMEND_DAILY` 控制），按当前音源裁剪：

1. **本地画像候选**：开启画像且有记录时，从偏好的两位歌手中尝试选择可播曲目；
2. **网易日推**：musicbox 登录后获取候选，按当前飞牛用户的历史 / 收藏做排除和排序；
3. **AI 补充**：配置有效 `FNMUSIC_LLM_*` 后使用，画像模式下为 AI 留出补充位置；
4. **本地曲库兜底**：网络 / AI 候选不足时尝试本地现存曲目，无本地曲库或可播候选时不保证有内容。

发现类别为「我的及收藏歌单」「为你推荐歌单」「排行榜」「分类歌单」「新碟上架」「私人 FM」。私人三类需要网易登录；它们是网易云账号的内容，不会被飞牛本地画像重排。普通曲目默认六小时缓存，FM 五分钟，每日推荐默认六小时或跨日重建，画像变化后最短半小时才重建。

**「热门推荐」**（`FNMUSIC_RECOMMEND_HOT` 控制），榜单原味、不排除已收藏曲目：

1. **网易热歌榜**：musicbox 源（免登录）；
2. **洛雪榜单**：lxmusic 源启用时聚合酷狗 TOP500 / 酷我飙升榜 / 网易新歌速递
   （按 `LX_SOURCES` 平台过滤）；全部榜单不可用时该歌单不显示。

**封面图标**：两个歌单的封面都取列表里第一首带可用封面直链的曲目（依次向后找）；
全部曲目都没有封面时封面接口返回 404，客户端显示自带默认样式（不伪造占位图）。

---

## 7. 健康检查与验收

```bash
# 代理端点健康状态
curl -s --unix-socket /var/run/trim_music.socket http://localhost/_ext/healthz

# 各音源容器内健康端点
curl -s http://127.0.0.1:8768/healthz   # musicdl（若启用）
curl -s http://127.0.0.1:8770/healthz   # musicbox（若启用）
curl -s http://127.0.0.1:8772/healthz   # lxmusic（若启用；含 user_source 状态）
curl -s http://127.0.0.1:8774/healthz   # WebUI（若启用）

# 本地自动化测试集（无需 Docker/飞牛环境）
python3 -m pytest
```

---

## 8. 常见问题排查

### 安装报「未检测到 docker」

v2.0.0 起仅支持 Docker 部署。请先在 fnOS「应用中心」安装 Docker 后重试；
`docker` 命令存在但 daemon 未运行时同样会报错退出。

### 升级自 v1.x 的音源切换问题

v2.0.0 结构变化较大（三容器→单容器、数据目录迁移、配置键增删），**推荐升级流程**：
`git pull` → `./restore.sh`（先还原官方直连并清理旧部署，兼容 v1.x 旧容器/宿主机服务；
`.env` 与数据保留）→ `./install.sh` 全新安装。直接原地升级同样支持。

v1.x 允许多音源并存，v2.0.0 起三音源互斥单选。升级安装时检测到旧 `.env` 多源
并存会要求重新三选一（非交互需 `--sources` 指定唯一音源）。此后换源在 WebUI
里秒级完成，不必重装。

### 洛雪源测试失败

WebUI 或安装向导的错误分类含义：**下载失败**（URL 不可达/超 9MB）、**格式无效**
（缺少洛雪源头部注释）、**格式不支持**（musicApi.json 类 JSON API 源——本项目只支持
洛雪桌面版自定义源 JS 脚本）、**初始化失败**（脚本运行报错，多为与主流源规范不兼容）、
**无可用平台**、**解析失败**（源声明平台均无法出直链）、**搜索取不到样本**（源脚本
初始化正常，但内置搜索接口限流/波动导致无法验证——不代表源不可用，稍后重试即可）。
报告里的"平台明细"按平台给出 ok/failed/untested 细分结论。依次检查 URL、换源后重试。

### fpk 安装一直卡在 55% 左右（issue #24）

55% 对应 fpk 安装器执行 `install.sh` 的阶段，本身可能要几分钟，卡住几乎都是
**网络拉取慢**（国内直连境外源受限）。排查步骤：

1. **看真实进度**：安装日志在 `/var/log/apps/fnmusic-ext-install.log`（卸载也不删）。
   v2.5.0 起拉取镜像/等待服务每 30 秒打一行心跳，能看到"仍在拉取…（已等 Ns）"即代表
   在正常推进，耐心等完即可。
2. **自测网络**（任一失败即网络受限）：
   ```bash
   curl -s -o /dev/null -m 8 -w '%{http_code}\n' https://mirrors.tencent.com/pypi/simple/
   curl -s -o /dev/null -m 8 -w '%{http_code}\n' https://docker.m.daocloud.io/v2/
   ```
3. **为 Docker 配代理后重试**（社区反馈最有效的解法）：编辑 Docker 的
   `daemon.json`（fnOS 上通常在 `/usr/local/apps/docker/config/daemon.json` 或
   fnOS Docker 设置界面）加 `proxies` 段，重启 Docker 后重新安装。
4. **手动换镜像源**：`FNMUSIC_DOCKER_MIRRORS="docker.m.daocloud.io docker.1ms.run"`
   或直接 `BASE_IMAGE=docker.m.daocloud.io/library/python:3.13-slim ./install.sh`。
5. 反馈问题时运行 `bash scripts/collect_support_info.sh`，把输出整段贴到 issue
   （只读收集，不含任何密钥）。

### 构建时报 `failed to resolve source metadata for python:3.13-slim ... 401 Unauthorized` 或拉取超时

多为系统 Docker daemon 全局镜像加速器异常。安装脚本会在构建前自动探测可用源
（国内镜像直连优先、官方源兜底），结果缓存到 `.env` 的 `FNMUSIC_BASE_IMAGE`，
全程不修改系统 Docker 配置。手动指定：

```bash
BASE_IMAGE=docker.m.daocloud.io/library/python:3.13-slim ./install.sh
```

自定义镜像候选列表：环境变量 `FNMUSIC_DOCKER_MIRRORS`（空格分隔，按序尝试）。

### 容器日志刷 `PermissionError: [Errno 13] Permission denied: '/app/app.py'`

v1.2.1 及更早的已知问题（umask 077 检出导致镜像内非 root 用户读不了源码），
v1.2.2 起已修复。若遇旧镜像残留：`git pull && ./install.sh` 重建即可。
