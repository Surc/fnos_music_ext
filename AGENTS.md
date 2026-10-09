# 项目维护入口

本仓库为 `Surc/fnos_music_ext` 飞牛音乐集成版。开始续作时读取：

1. [SOURCE_VERSIONS.json](SOURCE_VERSIONS.json)：双来源固定提交、采用范围、发布快照和 skill 数据。
2. [docs/ITERATION.md](docs/ITERATION.md)：本次交接、已完成行为及待实现需求。
3. [维护 skill](skills/maintain-fnos-music/SKILL.md)：同步、移植、验证与发行工作流。
4. 按任务读取 [上游同步](docs/UPSTREAM.md)、[功能说明](docs/PERSONALIZATION.md)、[验证记录](docs/VALIDATION.md)。

## 维护约定

- 原工程 `upstream/main` 用普通 merge，保留完整 Git 历史；功能参考 `reference/main` 按实际选定范围适配。
- 将完整提交 SHA 作为采用基线。区分 VERSION 文件的版本号、真实 tag、远端最新提交和本仓库已采用提交。
- 推进来源基线时保留原始基线与采用历史，写明对应集成提交/PR和范围；仅查看上游不推进基线。
- 修改运行代码时保留数据备份、socket 恢复、非 root 服务、多用户隔离、发现歌单只读与三音源互斥行为。
- 新功能同步更新使用文档和实际验证结果；在相应实现与测试完成前将需求保持为 `planned`。
- 修改来源记录或 skill 后运行 `python3 scripts/verify_provenance.py`。涉及源合并的完整克隆可加 `--check-git`。
- 更改 skill 时更新 `SOURCE_VERSIONS.json` 中的 skill 版本与文件 SHA256，并同步个人 Skills 中的同名 skill。
- 正式附件以 Release 工作流的 FPK 与校验文件为准。保持已发布 tag 的固定提交，另记后续 main 的变化。
