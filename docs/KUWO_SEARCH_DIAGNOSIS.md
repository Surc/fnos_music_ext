# 酷我 DAYBREAK'S BELL 搜索排查与最小修复

2026-10-12，未发布开发分支 `fix/kuwo-search-discovery`，基于 main
`34782d1543fd9291613fc47395a8a02a835e16f1`。不移动 `v2.8.0` tag，不创建 Release。
比较对象为 HotDownloader main `07e86c3d06243cf1350925c0dd3be704005abd29`，
实际安装检查的 musicdl 为 2.14.1；生产依赖仍沿用 `musicdl>=2.14.0`。

## 歌曲在哪个阶段消失

| 阶段 | 原 main 的行为 | 本次证据与处理 |
| --- | --- | --- |
| 平台搜索入口 | musicdl 2.14.1 使用 `www.kuwo.cn/search/searchMusicBykeyWord` | 当前环境调用没有取得响应对象，库捕获 `raise_for_status` 的 AttributeError 后返回空列表；这不是 NAS 实机复现 |
| 库内逐曲解析 | 酷我 `_search` 先解析直链，只 append `with_valid_download_url` 的歌曲 | 元数据在库内即可消失，单独放宽服务过滤不够；酷我搜索改为直接读取元数据 |
| 请求参数 | musicdl 的部分第三方解析函数同时使用显式 `timeout=10` 和 `**request_overrides` | 服务传入 timeout 会发生重复关键字错误；本次酷我路径不调用这些第三方解析器，不对整个依赖做猴子补丁 |
| 服务过滤 | `_is_candidate_playable` 要求 HTTP 直链；`_search_playable` 再探活 | 酷我元数据候选不走该过滤，保留原有其他音源的直链过滤及相关测试 |
| 代理过滤 | fetch、去重、合并列表再次调用可播判定，空 download_url 会被过滤 | 新增只接受酷我待验证元数据的展示判定；可播判定本身仍拒绝待验证项 |
| 播放刷新 | 按原关键词重搜首屏再寻找同 ID | 酷我元数据条目按缓存中的 RID 单独解析，不受再次搜索排名或首屏限制影响 |

HotDownloader 的 [search.rs](https://github.com/lerdb/HotDownloader/blob/07e86c3d06243cf1350925c0dd3be704005abd29/crates/hotdownloader-core/src/platforms/kuwo/search.rs)
调用 `http://search.kuwo.cn/r.s`，一次获取元数据，`pn=page-1`、`rn=limit`；
[link.rs](https://github.com/lerdb/HotDownloader/blob/07e86c3d06243cf1350925c0dd3be704005abd29/crates/hotdownloader-core/src/platforms/kuwo/link.rs)
在下载时再请求指定 RID 和音质。这里只采用搜索接口语义，没有移植 HotDownloader
的签名下载、加密格式、解密或多音源结构。

## 实际接口观察

酷我移动搜索返回 TOTAL=72，首屏 30 条；目标 `kuwo:142276943` 在第 4 条，
标题 DAYBREAK'S BELL，歌手 L'Arc〜en〜Ciel，专辑 デイブレイクズベル，时长 252 秒。
首屏另有约 98 秒的电视剪辑版和不同歌手/专辑的录音，不能视作同一个完整版本。
因此旧 10 条封顶不足以解释本次目标漏搜，但会限制其他低排名歌曲。

本次环境中，默认 `MUSICDL_REQUEST_TIMEOUT=5` 的服务请求发生 ReadTimeout，
搜索返回空列表并保留 errors，未作为成功空结果缓存。将该网络超时设为 10 秒后，
`/search?limit=30&sources=kuwo` 在约 6.82 秒返回 30 条，包含上述目标，
`playback_status=unresolved`、`verified=false`。curl_cffi、httpx 与 urllib
对相同移动接口的另一轮请求均约 7.45 秒取得首屏 10 条，包含目标。
这是当前环境的延迟观察，不代表用户 NAS 网络；不能将本次网络失败认定为
2026-10-10 漏搜的唯一原因。

该目标的 `/stream` 在本轮单曲解析预算内未完成，约 12.02 秒返回 502，
没有 Location，`/info` 仍保持待验证。不据此宣称歌曲可播放、可下载或为无损，
也没有进行 fnOS App 实机验收。旧库搜索入口失败、库内直链过滤与代理过滤分别
是可定位的丢失点，用户当次具体命中了哪个点仍需 NAS 日志佐证。

## 修复边界与安全检查

- 仅酷我用一页元数据搜索，单次 HTTP 请求 `rn<=30`，关键词由 httpx 编码一次。
  仍只参与首屏，不增加深分页；其他平台的旧一页封顶和搜索流程保持原实现。
- 待验证项不含 download_url，不标为 verified，不提供推测的码率、采样率或位深。
  标题/字段明确标识试听、无效 RID、重复 RID、缺标题的条目不进入候选列表。
- 播放时调用 musicdl 的官方明文音频解析函数，仅解析选中的 RID。该私有依赖
  接口通过真实生产依赖的离线合约检测；未引入第三方解析请求或加密音频支持。
- 取一个最多 256 KiB 的音频头样本，拒绝 HTTP 错误、HTML/JSON、压缩传输、
  损坏头及无法确定时长的资源。FLAC 用 STREAMINFO，MP3 用 Xing/VBRI 或
  CBR 码率与源站完整长度，与预期时长比较，误差上限为 max(3 秒, 2%)。
  不能把 Range 样本的字节长度当成完整文件长度。
- 过期重新按 RID 验证，失败清除旧 URL 和 verified 标记；超时 worker 不能
  晚到后写回播放缓存。坏无损档降级为 MP3 时，对替换音频也做时长检查。
- 这些是播放前的头部/时长检查，不能证明整首音频每个字节都完整或母带无损。
  保留既有无损解码探针、完整下载校验、同曲匹配和网易跨平台下载补源策略。
- 合法空搜索重置已有源失败计数；聚合 fast-return / slow-grace 取消不作为
  上游失败累计熔断或收紧自适应预算。真正的超时/HTTP/结构错误仍进入熔断。
  宽限期本身仍可能令慢源本轮没有显示，响应 errors 可区分这一情况。
- 保留三主音源互斥、网易发现只读、用户画像/收藏隔离、非 root 部署与官方
  Socket 恢复契约。不修改正式 v2.8.0 或双来源采用基线。

## 在 NAS 核对

使用当前启用的 musicdl 服务，先查看 `/healthz` 的 breaker_open 与
adaptive_timeouts，再请求单独的 `sources=kuwo`，排除其他源的宽限期影响：

```bash
curl -sS --get http://127.0.0.1:8768/search \
  --data-urlencode "keyword=DAYBREAK'S BELL" \
  --data-urlencode 'sources=kuwo' --data-urlencode 'limit=30'
curl -sS --get http://127.0.0.1:8768/info \
  --data-urlencode 'id=kuwo:142276943'
curl -sS --get http://127.0.0.1:8768/stream \
  --data-urlencode 'id=kuwo:142276943' -o /dev/null -w '%{http_code}\n'
```

最后一个命令不跟随重定向、不下载整首；302 表示本轮验证通过，502 表示解析
或验证失败。排查只记录曲目 ID、耗时、状态和错误阶段，勿公开临时直链或凭据。
若 metadata 请求超出默认 5 秒，可调整 MUSICDL_REQUEST_TIMEOUT，并让
MUSICDL_SEARCH_TIMEOUT 与代理 FNMUSIC_SEARCH_TIMEOUT 覆盖实际网络预算。

自动回归和实际限制见 [VALIDATION.md](VALIDATION.md)。
