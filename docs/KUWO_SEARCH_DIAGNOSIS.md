# 酷我 DAYBREAK'S BELL 搜索排查与最小修复

2026-10-12，未发布开发分支 `fix/kuwo-search-discovery`，基于 main
`34782d1543fd9291613fc47395a8a02a835e16f1`。不移动 `v2.8.0` tag，不创建 Release。
比较对象为 HotDownloader main `07e86c3d06243cf1350925c0dd3be704005abd29`，
实际安装检查的 musicdl 为 2.14.1；生产依赖仍沿用 `musicdl>=2.14.0`。

## 2026-10-14 播放 502 续查（仍未合并/发布）

本轮仍使用真实 musicdl **2.14.1**，未注入账号、Cookie 或第三方解析源。
最终代码的真实请求：搜索约 **5.40 秒**返回 30 条且包含目标；FLAC
`convert_url2` HTTP 200 但正文为空；MP3 解析 HTTP 200 且返回地址；CDN HEAD
HTTP 200，声明总长 **16,009 字节**。随后实际音频头解析得 **2.612 秒**，
与搜索元数据 **252 秒**不符，`/stream` 约 **19.58 秒**返回 **502**，没有
Location；`/info` 仍是 `unresolved / verified=false`。

这次已到达音频时长验证阶段，拒播直接原因是源站交付异常短音频，不能以
HTTP 200 或链接后缀当作完整歌曲。尚无证据判定是账号权益、地区/出口策略、
酷我接口策略还是当前源文件问题；当前出口未能取得完整版本，也没有 fnOS
或其他地区网络的对照结果。不能据此宣称歌曲在所有设备都不可用。

此前代码也有可修复的预算和质量档问题：

- 单 RID 解析复用搜索的自适应预算，默认 12 秒，慢搜索还可能缩到 8 秒。
  实际调用有 FLAC/MP3 解析、CDN HEAD/GET、可选歌词、音频头 GET 等串行阶段。
  本轮旧同步函数完整走完约 31.71 秒，12 秒不足以看到最终结果。
- musicdl 在取得音频后仍同步请求 `newlyric.kuwo.cn`。当前播放适配器跳过该
  可选请求并保留已有歌词缓存；不会因缺少歌词而阻止播放，也不改其他音源歌词流程。
- 飞牛 GET 每次只给 6 秒、总计 12 秒，HEAD 只给 4 秒，即使服务后来能完成
  验证，代理也会先取消。酷我现在用独立 40 秒启动窗口；其他平台的原有窗口保持。
- 原先在库选中音质档后才做时长检查，坏 FLAC 可能挡住同 RID 的完整 MP3。
  现在每个候选档在库停止尝试前就通过原有音频门；失败继续尝试 MP3，未放宽校验。

`MUSICDL_RESOLVE_TIMEOUT` 默认 30 秒，与搜索熔断/自适应惩罚分开；官方取链和
库探活的每次请求取剩余预算与网络超时的较小值。异步 deadline 到期后不会
写入晚到的 verified 缓存，底层线程尚未退出时仍占用既有单源工作槽。
时长门仍有网络空闲超时；40 秒代理窗口还需容纳 CDN 首字节与原有无损解码探针，
慢链不保证一定在窗口内完成。没有通过禁用坏链、试听或解码检查换取成功。

日志仅记录 RID、阶段、状态、实际时长/总字节、耗时和异常类型。禁用此单曲
适配器中 musicdl 原始 URL 文件日志，并移除服务取流异常中的直链/原始异常文本。
诊断脚本不跟随 302、不输出 Location、Cookie 或临时地址。

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

## NAS 候选版本的分阶段诊断

以下仅适用于已自行部署 **PR #6 候选代码**的测试环境；正式 v2.8.0 的 FPK
没有这些新增日志和脚本。本轮没有向 NAS 部署、覆盖正式附件、移动 tag、合并 PR
或创建发行。不要将通过自动化测试视作已安装/实机通过。

在候选代码的仓库根目录运行（Python 标准库即可，不需要凭据）：

```bash
python3 scripts/diagnose_kuwo.py --service-url http://127.0.0.1:8768 --proxy
```

脚本依次输出 health、单源 search、无重定向跟随的 resolve、info；仅当解析得到
302 后才经服务端代理读取最多 1024 字节。返回码 0 只表示该轮解析/首段读取成功，
不代表完整下载、完整解码或 App 播放已验收。服务重启后必须先搜索恢复 RID 缓存。
容器内服务端口是 8001，宿主默认 8768；实际非默认端口用 `--service-url` 指定。
不要使用 `curl -v` / `curl -i` 分享音频响应头，Location 内可能有临时凭据。

慢出口可在候选部署的 `.env` 临时设置：

```dotenv
MUSICDL_REQUEST_TIMEOUT=10
MUSICDL_SEARCH_TIMEOUT=15
MUSICDL_RESOLVE_TIMEOUT=30
FNMUSIC_SEARCH_TIMEOUT=20
FNMUSIC_KUWO_STARTUP_TIMEOUT=40
```

前三个超时由 musicdl **进程启动时**读取：`.env` 优先于继承的进程环境。
原生测试部署按现有 `fnmusic-sources` 服务重启；Docker 测试部署重启 `musicdl`
程序即可重新读取挂载的 `/repo/.env`。只改宿主 shell 的 export 不会改变已运行的
容器/Supervisor 子进程。代理的两个 FNMUSIC 参数由现有 `.env` 观察器热重载；
关闭观察器时需重启代理。服务 `/healthz.timeouts` 可确认有效的三个 musicdl 预算。
必要时只按实测放大，例如 resolve=50 / startup=60，并相应提高脚本 `--timeout`；
不能通过搜索超时替代播放预算，不能指望延长超时使 2.612 秒短音频变成完整歌曲。

在管理页现有日志、Docker 的 `docker logs fnmusic-sources`，或原生部署的
`sources-native/` 音源日志中查看下表；飞牛代理另看原有 proxy 日志：

| 最后阶段/现象 | 可定位范围 | 需要的下一项对照 |
| --- | --- | --- |
| search errors / ReadTimeout | 搜索请求尚未取得有效元数据 | 有效 request/search 预算、NAS 到搜索端点连通性；单独 sources=kuwo 排除聚合宽限期 |
| `rid_request` 后失败或 `rid_response` 无地址 | 官方单 RID 接口没有交付音频候选 | 状态与每档耗时；同 NAS 的直连/现有代理出口分别复测，不分享正文中的链接 |
| `link_head` / `link_get` 超时或 4xx/5xx | 地址已取得，CDN 探活受阻 | NAS 的 DNS、出口、HTTP(S) 与 CDN 连通性；与官方解析接口故障区分 |
| `audio_http` / `audio_header` / `audio_range` | 错误页、损坏/未知头或不合法 Range | 状态/异常类型与候选格式；保留拒绝，不用 HTTP 200 替代验证 |
| `audio_duration outcome=mismatch` | 拿到音频但实际长度不符 | expected_s、actual_s、origin_bytes；本轮为 252 / 2.612 / 16009 |
| `Refresh failed ... TimeoutError/SourceBusy` | 总解析预算耗尽或前一工作线程仍未退出 | 有效 resolve_timeout、前一轮最后阶段与耗时；待线程退出后重试，避免连续点击制造等待 |
| 服务 302，但代理 `service_stream status=502` 或 `Stream startup failed ... TimeoutError` | 服务端后续代理取流/解码/启动窗口 | 带 --proxy 的首段测试、代理窗口、无损探针与实际客户端是否先发 HEAD |

网络对照必须来自实际 NAS：同一 RID、同一候选代码、相同预算，在直连与用户已有
可用代理出口各测一次；不改账号、不取消校验。若一侧超时另一侧取得完整音频，
可支持网络环境差异；若两侧都取得相同短音频，只能认定两次测试未获得完整资源。
本轮执行环境可访问搜索/官方解析/CDN，故最终 502 不是单纯连接超时；仍不能
代替用户家庭网络，也不能排除地区/出口造成的资源差异。

验收应分别记录：①目标出现在搜索；②服务 302 + info verified=true；
③服务代理与飞牛代理 GET/HEAD/Range 的状态和首字节；④fnOS App 能播放到
252 秒结尾、拖动正常；⑤完整下载落库并解码成功，未混入其他 RID/电视剪辑。
本轮仅自动化验证了完整 252 秒合成 FLAC/MP3 的时长门与服务 Range 字节路径、
慢首字节的代理 GET/HEAD、错误音频拒绝和真实依赖离线合约；真实目标的②—⑤未通过。
