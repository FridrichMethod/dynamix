# 官方来源

[English](sources-official.md) · **简体中文**

本文档说明 `official` 来源类别：原版游戏的商店页面、C4Cat 的官方账号和官方网站。`scripts/fetch_official.py` 把结果写入 `data/sources/official.json`，并为每个来源写一份发现快照 `data/discovery/<source>.json`。只使用原版游戏的标识；续作的页面（App Store ID `6741094831`、Google Play 包名 `com.c4cat.dynamix2`）排除在外。

商店截图是应用商店对游戏画面和菜单的渲染版本，不是美术作品。它们的类型为 `store_screenshot`，属于 `archive` 层级：原文件存档在仓库中，网站只显示预览图并链接原文件。

## 来源

| 来源 ID | 来源 | 读取方式 | 说明 |
| --- | --- | --- | --- |
| `official:apple-store` | [App Store，Dynamix（ID 945955095）](https://apps.apple.com/us/app/dynamix/id945955095) | 商品页面 `serialized-server-data` 中的截图栏，以及按地区调用 iTunes `lookup` API 取得的较早的地区专属截图组 | 截图按原生尺寸请求。对于 PNG 上传，请求 Apple 的无损 PNG 渲染版本，而不是页面加载的 JPEG；页面声明的 JPEG 版本保留在 `source_reported`。这些是商店重新渲染的文件，标为 `transformed: true`。 |
| `official:google-play` | [Google Play，Dynamix（`com.c4cat.dynamix`）](https://play.google.com/store/apps/details?id=com.c4cat.dynamix) | 三种语言区域的商品页面 | 文件以 `=s0`（原生尺寸）请求。页面中 `=wW-hH` URL 的尺寸只作为备注保留；尺寸取自解码后的字节。商店应用图标以 `game_ui` 排除。 |
| `official:taptap` | [TapTap，Dynamix（7082）](https://www.taptap.cn/app/7082) | 页面的 `__NUXT_DATA__` 数据；文件取其 `original_url` | 写明 C4Cat 为开发者的次要商店页面。每个文件的 MD5 与其上传文件名中的 MD5 标记比较：不一致说明 TapTap 重新编码了上传文件，记为 `transformed: true`，并在字节完全一致的版本已从其他商店存档时注明它。经过裁切和缩放的横幅版本以 `upscaled` 排除；横幅的上传文件本身会存档。页面上可见的更新说明是历史内容。 |
| `official:x` | [Dynamix 官方 X（@c4c4cat）](https://x.com/c4c4cat) | 通过公开嵌入接口读取明确的帖子，包括调研从存档的帖子页面中找回 ID 的帖子；嵌入时间线有响应时也会读取 | 账号 ID 3166512194。X 会重新编码上传的图片（`name=orig` 是 X 上最大的副本）。完整历史需要登录，其余存档的帖子 ID 也没有处理，因此该来源为 `partially_available`，其快照中的这些列表保持未完成。 |
| `official:soundcloud-c4cat` | [C4Cat 官方 SoundCloud](https://soundcloud.com/c4cat) | `/tracks` 页面渲染出的每首曲目 | SoundCloud 只在服务端渲染列表的一部分，因此列表保持未完成（`partially_available`）。没有自己封面（只显示上传者头像）的曲目被排除；在 Dynamix 中的使用未得到证实的上传保持待定。 |
| `official:facebook` | [Dynamix 官方 Facebook 页面](https://www.facebook.com/c4cat.dynamix/) | 仅限明确的帖子永久链接，在未登录状态下读取 | 未登录时不提供时间线，因此该来源为 `partially_available`。提供 Open Graph 数据的永久链接会给出其 `og:image`，并以 `og:title` 和 `og:description` 作为证据；该图片是 Facebook 重新编码并缩放过的版本（`preview`，`transformed: true`），不是上传的原文件。重定向到登录页面的永久链接记为 `blocked`。 |
| `official:facebook-c4cats` | [C4Cat 公司 Facebook 页面](https://www.facebook.com/c4cats/) | 在未登录状态下读取一次页面 | 未登录时只提供头像，而头像的游戏归属没有确认，因此保持待定；页面上的其他照片需要人工访问（`metadata_only`）。 |
| `official:youtube` | [C4 Cat YouTube 频道（@C4CatEntertainment）](https://www.youtube.com/@C4CatEntertainment) | 对每个明确指定的视频读取 oEmbed；读取频道页面以取得横幅 | 视频列表来自调研对该频道的枚举，不再重新读取，因此该来源为 `partially_available`。只指定与 Dynamix 相关的视频（其中包括会被排除的 Universe 更新视频，以及游戏归属尚未确定、保持待定的视频），并且 oEmbed 必须指向官方频道。每个视频取 YouTube 生成的最大缩略图（`maxresdefault`，否则 `sddefault`，再否则 `hqdefault`），这是视频的 JPEG 渲染版本（`preview`，`transformed: true`），不是上传的图片。 |
| `official:website` | [Dynamix 官方网站](https://dynamix.c4-cat.com/) | 一次可用性探测 | `temporarily_unavailable`：2026-10-10 该主机名无法解析。网站的历史存档在 `archives:wayback` 下存档（见 [sources-archives.zh-CN.md](sources-archives.zh-CN.md)）。 |
| `official:c4cat` | [C4Cat 公司网站](https://c4-cat.com/) | 读取一次页面 | 即将上线/联系方式的占位页面，没有游戏目录；仅作身份证据（`metadata_only`）。 |
| `official:official-leads` | 官方渠道的调研线索 | 不发请求 | 调研中没有可获取文件的线索（例如已停止的商店页面、周边商店、需要账号才能进入的社区服务器、需要登录或并不存在的社交账号），各自保留结论。 |

## 分类

商店和 TapTap 截图为 `store_screenshot`。官方 X、Facebook 和 YouTube 账号的图片遵循 wiki 使用的同一规则（见 [sources-wikis.zh-CN.md](sources-wikis.zh-CN.md#类型)）：显示谱面难度等级的公告图（无论针对一首曲目还是多首），以及主要内容是曲目或谱面列表的图片（回归曲目和活动阵容、回归预告、印有多张带曲名封面的曲包和联动阵容卡）为 `reference`；主视觉、活动和周年海报、单张联动主视觉、横幅、标志和字标为 `promotional`；展示游戏画面的帖子图片或视频缩略图为 `gameplay_screenshot`。C4Cat SoundCloud 上的官方发行封面为 `song_art`。商店应用图标以 `game_ui` 排除（品牌标识而非美术），TapTap 预告片的封面帧以 `video_placeholder` 排除，平台的裁切或放大版本以 `upscaled` 排除，内容为人物照片的缩略图以 `person_photo` 排除，Dynamix Universe 的素材以 `universe_only` 排除。与画师类别一样，每个候选都由明确的种子规则分类：不会仅凭关键词接受任何内容，提到 Dynamix Universe 的候选保持待定，除非规则允许。

同一张截图常常出现在多个商店中。字节完全相同的文件合并为一个目录条目，其来源记录列出每个商店；因重新编码而不同的文件保持分开，并注明哪一个是字节完全一致的上传文件。

记录以其引用的文字作为标题：X 帖子的正文、Facebook 帖子的 `og:title`、YouTube 视频的标题或 SoundCloud 曲目的标题；商店截图按所在的商品页面和顺序命名。文件名保留在 `original_filename` 中。

## 证据级别与访问限制

商店页面、SoundCloud 和 Facebook 页面为 `page_read`；X 嵌入接口和 YouTube 的 oEmbed 为 `live_api`；探测失败的官方网站为 `not_established`；线索登记为 `search_index`。从不绕过登录、机器人验证或访问控制：Facebook 时间线、完整的 X 历史和 YouTube 频道的视频列表都没有被枚举，因此它们的快照保留未完成的 continuation 及原因，绝不会被计为完整。官方网站探测失败会连同错误类别一起记录；这并不意味着网站内容从未存在过。

## 数量

下方标记之间的数字在每次构建后由 `scripts/update_docs_stats.py` 根据 `data/coverage.json`（`official` 类别的 `sources.records`）和 `data/catalog.json` 生成，不是手工填写的。

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## 权利

官方文件标为 `origin_status: official` 和 `master_status: unverified`。商店渲染版本和平台重新编码（Apple 和 TapTap 的渲染、X 的重新编码、Facebook 的 Open Graph 版本、YouTube 生成的缩略图）在 `quality.notes` 中如实说明，Facebook 和 YouTube 的版本标为 `preview`。商店截图和官方账号图片的权利人记录为 C4Cat Entertainment Limited；涉及其他游戏的联动美术，除非其来源写明权利人，否则权利人留空。这些文件都没有确认任何许可证，因此 `rights.license` 保持为空；商店页面公开可见并不构成许可。

## 命令

```sh
.venv/bin/python -I scripts/fetch_official.py --metadata-only     # 发现并记录候选；不下载
.venv/bin/python -I scripts/fetch_official.py --resume            # 获取；重新校验已存档文件，而不是重新下载
.venv/bin/python -I scripts/fetch_official.py --resume --source official:apple-store
.venv/bin/python -I scripts/fetch_official.py --verify            # 离线
```

参数、锁、原子写入、错误处理和退出状态与 `fetch_artists.py` 相同（见 [sources-artists.zh-CN.md](sources-artists.zh-CN.md#命令)）。锁默认位于本类别的 `assets/public/official/` 目录上。
