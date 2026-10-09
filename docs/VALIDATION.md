# 集成版验证记录（2026-10-09）

| 检查 | 结果 |
| --- | --- |
| 推荐、发现接口、画像、原生歌单、管理页与 FPK 结构专项 | 最终代码重跑 109 passed |
| 新增发现与画像自动测试 | 19 个 Python 用例，涵盖账号变化、只读歌单、分页、重启缓存、失败降级、官方数据库按用户隔离和 AI 补充 |
| 管理页 Node 行为测试 | 通过；新增中文上移/下移按钮及保存配置行为断言 |
| Python 编译、前端语法、工作流 YAML、改动空白检查 | 通过 |
| musicbox 真实生产依赖的离线导入与 CLI/API 合约 | production_import=pass、offline_contract=pass（NetEase-MusicBox 0.5.3） |
| fnpack 1.2.3 完整打包 | 成功生成 fnmusic-ext-2.7.1.fpk，并生成 SHA256 文件 |
| FPK 内容检查 | manifest、cmd/main、图标、app.tgz 完整；新增 API/发现/画像模块存在；没有本地 .env 和发现缓存数据 |
| Docker 镜像构建 | 当前环境没有 Docker，未执行；Dockerfile 已把新增 musicbox 模块加入 COPY，GitHub CI 保留原工程的镜像检查 |
| NAS 实机安装、升级、卸载、移动端播放 | 未执行，需要实际 fnOS 设备和网易云登录账号 |
| GitHub 创建、提交与 Actions 运行 | 此记录生成时尚未执行；当前连接缺少创建/Fork 仓库的动作，待使用网页创建入口 |

## 全工程回归的环境限制

在同一环境、相同依赖、相同 NO_PROXY/PATH 配置下，对未修改上游固定提交 `f036e1f` 和集成代码分别执行完整 pytest：

- 未修改上游：749 passed、37 failed、2 errors。
- 首轮集成：767 passed、38 failed、2 errors。
- 两者差集只有管理页最小 DOM 测试桩不支持新控件使用的 replaceChildren。补齐标准 DOM 操作并增加实际排序行为测试后，该测试和相关专项全部通过。
- 共同失败来自此执行环境受限的进程身份/生命周期探测与 Unix Socket 创建，涉及安装锁、安装可靠性、应用启动兼容性、管理页网关和 FPK 前置检查。没有修改或删除这些原工程测试。不能据此宣称全工程测试已经全部通过。

相关测试在 GitHub Actions 的 Ubuntu 环境中保留执行。实际网易云在线响应、版权及账号权限没有使用真实账号验证，自动测试采用对应接口的真实结构加模拟响应。
