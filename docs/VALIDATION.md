# 集成版验证记录（2026-10-09）

以下 v2.7.1 历史证据对应功能 PR #1 合并提交 `0d5d2451d7acfa8f0162b89d628083d33f59c161`。v2.8.0 下载补源的验证单列在后文，旧结果不代表新增代码已验证。

## GitHub 的实际结果

- [功能合并 PR #1](https://github.com/Surc/fnos_music_ext/pull/1) 已合并至 `main`，保留原工程完整 Git 历史。
- [PR CI](https://github.com/Surc/fnos_music_ext/actions/runs/37904563594) 的 11 项检查全部成功。
- [合并后的 main CI](https://github.com/Surc/fnos_music_ext/actions/runs/37905113582) 同样全部成功。

| 检查 | 结果 |
| --- | --- |
| Python 3.11 全工程 pytest | 807 passed |
| Python 3.13 全工程 pytest | 807 passed |
| Shellcheck 与 shell 语法 | 通过 |
| Python 编译与前端 Node 行为 | 通过 |
| musicdl / musicbox / lxmusic 真实生产依赖合约 | Python 3.11、3.13 共六组通过 |
| 单容器 Docker 镜像构建 | 通过 |
| 三音源非 root 合约与按需 musicdl 健康检查 | 通过 |
| fnpack 1.2.3 FPK 构建与 SHA256 校验 | 通过，GitHub CI 产物名 fnmusic-ext-fpk |
| FPK 骨架和模块 | manifest、cmd/main、图标、app.tgz 完整；新发现 API / 缓存 / 画像模块齐全 |
| 本地数据排除 | 包中不含本地 .env、发现缓存、登录态、历史或收藏数据 |
| fnOS 实机安装、升级、卸载、App 播放 | 尚未执行 |
| 真实网易云登录与在线曲目权限 | 尚未用真实账号验证 |

## 新功能测试覆盖

本地相关专项最终重跑 109 passed；新增发现与画像测试覆盖账号变化、私人列表隔离、只读歌单、分页、重启缓存恢复、失败降级、官方数据库按用户隔离、推荐排序及 AI 补充。管理页 Node 测试覆盖中文上移 / 下移和保存配置行为。

musicbox 另以真实 NetEase-MusicBox 0.5.3 执行离线导入与 CLI/API 合约，结果通过。自动测试采用真实方法返回结构和模拟 HTTP 响应；不能替代实际账号版权、网易云连通性与 NAS 播放验收。

## 本地完整回归的历史环境限制

GitHub 验证前，在同一受限执行环境对原工程固定提交 `f036e1f` 和集成代码运行完整 pytest：原工程 749 passed、37 failed、2 errors；首轮集成 767 passed、38 failed、2 errors。

两者差集仅为管理页最小 DOM 测试桩缺少新控件使用的标准操作，修复后相关专项通过。共同失败涉及进程身份 / 生命周期与 Unix Socket 权限。原工程测试没有被删除或跳过来规避问题；后续真实 GitHub Ubuntu 环境已完成上述两版本各 807 项全工程测试。

## NAS 实机验收建议

在实际 fnOS 设备分别确认：安装和旧版升级、管理页新设置、网易扫码、发现歌单和 FM、不同飞牛用户的每日推荐、原生搜索及手机播放、停止还原、卸载后的数据备份。只报告实际执行的结论，healthz 通过不等于在线歌曲一定可播放。

## v2.8.0 下载补源（2026-10-09）

分支 `feat/download-fallback`，基于 main `6a0a30d18c7828f59c068f2aa8411b098c153185` 独立实现；双来源采用 SHA 和维护 skill 1.0.0 未变。[PR #3](https://github.com/Surc/fnos_music_ext/pull/3) 的最终运行代码提交为 `8550ecd63dbd40b197ce2d741591916111971a04`，[完整 CI](https://github.com/Surc/fnos_music_ext/actions/runs/37921652490) 11 项全部成功；合并提交 `981c6b19b0a27a7bb24020c3a0a01b0cce691570` 的 [main CI](https://github.com/Surc/fnos_music_ext/actions/runs/37922716170) 同样全部通过。发行仅调整说明与记录，运行代码保持相同。

正式 [v2.8.0](https://github.com/Surc/fnos_music_ext/releases/tag/v2.8.0) 固定在 `3e9c6cdb4f8e90416457dde9a3dc3984831b023b`；该提交的 [CI](https://github.com/Surc/fnos_music_ext/actions/runs/37943422317) 11 项全部成功，Python 3.11 / 3.13 各 857 passed，无失败或跳过。[Release 工作流](https://github.com/Surc/fnos_music_ext/actions/runs/37944046519) 打包测试 67 passed，fnpack 1.2.3 构建及 GitHub Release 上传成功。

正式附件 `fnmusic-ext-2.8.0.fpk` 为 27,117,969 字节，GitHub Release API / 页面公布的 SHA256 为 `22a3bc847d2cb8e929cf9d1832dfd7325e143e0ba800e01b8ce528dbb798790e`；配套校验文件 88 字节，其自身 SHA256 为 `59a0114a4961c86cb8374b91f7b4c6f2011e33e3eaca2506ce6b43a52ab5620b`。同提交的 CI 实际检查了 FPK 骨架、新补源模块与 SHA256 文件，打包测试验证私有数据排除。当前执行环境未能重新下载正式附件，因此正式资产摘要依据 Release API / 页面，包内证据依据同提交 CI 与打包测试；不以 CI 或本地另一构建的摘要替代正式资产。发行后 main 记录这些结果，固定 tag 保持不变。

| 检查 | 当前实际结果 |
| --- | --- |
| 相关代理 / 下载 / 管理页 / 容器 / musicdl 测试 | 集成回归 197 passed、1 skipped；随后 musicdl 排序及部分结果同步专项 72 passed，最终缓存升级 / tee / 转码专项 87 passed、1 skipped（本地 Socket 权限） |
| 实际生成音频 | ffmpeg 生成 FLAC、320k MP3 并完整检测；损坏、错误后缀、试听与码率策略有覆盖 |
| 本地 Unix Socket | 环境拒绝创建 Socket；新增一个真实通信测试本地跳过，GitHub Linux CI 已通过，无跳过 |
| 本地全工程首轮 | 765 passed、40 failed、2 errors；包含旧 Socket / 进程权限限制、一个 musicdl 并行计时波动及两个新容器预期失败（已修正，专项重跑通过） |
| 本地全工程收尾 | 815 passed、37 failed、1 skipped、2 errors；37 + 2 均涉及既有 Socket / 进程权限限制；并行探活排序修复后不再出现该项失败 |
| GitHub Python 3.11 / 3.13 完整 CI | 各 857 passed，无失败或跳过；不是沿用 v2.7.1 的 807 passed |
| 三服务两版本真实生产依赖合约 | musicdl / musicbox / lxmusic，Python 3.11 / 3.13 六组全部通过 |
| Shell、编译及前端行为 | CI 通过；前端 Node 行为包含在全工程测试 |
| 实际容器中的下载补源生命周期 | Docker 镜像与离线 non-root 契约通过：按需启动、共享租约、切配置、空闲停止和关闭清理；三原音源服务合约保持通过 |
| fnpack 1.2.3 构建及包内新模块 | 本地与 CI 构建通过；manifest 2.8.0、两个新代理模块 / Supervisor 配置及私有数据排除验证通过；CI 产物 fnmusic-ext-fpk 含 FPK 与通过校验的 SHA256 文件 |
| fnOS 与真实平台资源 | 尚未验收 |

新增关键场景包括网易优先、下载失败 / 音质不足触发、按平台顺序补源、完整歌手 / 版本 / 时长严格匹配、试听和坏文件拒绝、目标全不满足时严格失败或允许降级、并发共用文件且按用户凭证分别绑定、来源记录无凭据、低档旧文件保留和高档引用优先、失败冷却及配置变化取消。下载补源默认关闭，对既有 v2.7.1 行为单独做回归。

CI 首轮 Python 3.13 暴露并行探活的部分结果与最终排序不一致，已修复为同一组有序结果；另一次 fnpack 下载发生连接重置，最终上述 CI 的下载、构建及校验成功。最终专项同时覆盖低档缓存存在时网易重新提供无损、真实 FLAC 扩展名与引用、保留旧 MP3，以及备用服务不可用时不选更差资源。自动验证不代表真实平台一定有可用版权资源，也不替代 fnOS 安装、升级、App 下载和曲库扫描 / 绑定验收。
