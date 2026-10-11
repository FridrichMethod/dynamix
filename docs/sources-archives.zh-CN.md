# 档案与媒体来源

[English](sources-archives.md) · **简体中文**

本文档说明 `archives` 来源类别：公开网页档案、历史媒体资料以及与之相关的调研线索。`scripts/fetch_archives.py` 把结果写入 `data/sources/archives.json`，并为每个来源写一份发现快照 `data/discovery/<source>.json`。这些来源填补在线网站已不再覆盖的缺口，例如已无法解析的官方网站，以及平台在没有登录或 Referer 时拒绝提供的画师原图。

## 来源

| 来源 ID | 来源 | 读取方式 |
| --- | --- | --- |
| `archives:wayback` | [Internet Archive Wayback Machine](https://web.archive.org/) | 向 CDX API（`https://web.archive.org/cdx/search/cdx`）发出明确的查询；以原始形式获取存档 |
| `archives:4gamer` | [4Gamer.net Dynamix 截图图集](https://www.4gamer.net/games/306/G030626/screenshot.html) | 图集页面和该网站介绍本作的专栏文章各读取一次；取图集中的文件和专栏中的图片 |
| `archives:press` | 关于 Dynamix 的媒体文章（巴哈姆特 GNN、QooApp、unwire.hk、T客邦、ifanr、HK01、4Gamer） | 每个明确指定的文章页面读取一次；文章正文中的每张图片都由规则决定 |
| `archives:hkstp` | [香港科技园 2016-08-09 新闻稿](https://www.hkstp.org/zh-cn/park-life/news-and-events/news/press-release-20160809) | 读取一次页面；取其中的图片和附录 PDF |
| `archives:archive-leads` | 历史与二手资料的调研线索 | 不发请求：调研中的 wiki、合作游戏 wiki、百科、媒体、论坛和代码仓库，各自保留结论 |

## Wayback Machine

从不整体抓取该档案。每个种子是针对一个已知 URL 或路径前缀的 CDX 查询：

- 官方网站 `dynamix.c4-cat.com` 的图片路径、上传目录，以及同一 WordPress 网站在其原始 IP 地址下的路径；
- 游戏的 MyCard 页面和下载主机；
- 香港科技园的附录 PDF，其原始 URL 现在返回 HTTP 404；
- 署名画师特定 pixiv 原图（`i.pximg.net/img-original/…`）的 ArchiveTeam 存档；pixiv 拒绝向本项目提供这些在线文件（见 [sources-artists.zh-CN.md](sources-artists.zh-CN.md)）。

CDX 分页通过 `showResumeKey` 继续，直到不再返回 resume key。分页有上限：resume key 重复或达到页数上限时，列表停止并保留该 key，快照随之保持未完成。存档按 URL 和内容摘要分组，每个不同载荷的最早一次存档成为一个候选。文件以 `id_` 形式获取（`https://web.archive.org/web/<timestamp>id_/<original URL>`），它返回存档的原始字节，不经过回放页面的改写；回放页面保留为记录的 `page_url`。

`source_reported` 保存原始 URL、存档的 MIME 类型、存档时间以及换算成十六进制 SHA-1 的 CDX 摘要，因此 `comparisons.sha1_match` 能说明收到的字节是否正是存档的载荷。CDX 的 `length` 是压缩后的 WARC 记录大小，而不是文件大小；它只作备注，不参与比较。

画师 pixiv 原图的存档标为 `origin_status: artist_post`，因为这些字节就是 pixiv 为该帖子提供的文件。它的曲目对应只有通过引用原 pixiv 帖子的 `primary_evidence` 才能是 `confirmed_primary`，并且如果没有任何画师类别快照为同一文件佐证该证据，`fetch_archives.py --verify` 会失败。官方网站的存档标为 `official`；WordPress 生成的缩略图标为 `preview`，同一图片的更大版本也已存档时，缩略图会被排除。

被排除的 Wayback 候选包括官方页面的标志、商店徽章、占位图、背景和其他装饰（`site_ui`），应用图标（`game_ui`），合作方的图标（`not_dynamix`），以及游戏归属未确定的小图标（`out_of_scope_other`）。图注说明其在 Dynamix 中的使用尚未确定的图片保持待定。

## 媒体来源

4Gamer 图集保存了日期为 2015-06-18、供媒体报道使用的历史截图；它们是标为 `repost` 的 `gameplay_screenshot` 副本，不是曲绘原稿，属于 `archive` 层级。`archives:4gamer` 还列出该网站 2015-06-17 介绍本作的专栏文章中的图片：其中的缩略图（从截图裁切而来、这些截图的全尺寸文件已在 `archives:press` 下存档；另有一张裁切自曲目未能确定的曲绘）和专栏横幅被排除。

`archives:press` 读取调研找到的明确文章，来自巴哈姆特 GNN、QooApp、unwire.hk、T客邦、ifanr、HK01 和 4Gamer 专栏。读取到的文章正文中的每张图片，以及在该文章的条目配置了时的 `og:image`，都成为一个候选，以上传的文件为键，并由规则逐一决定：游戏截图、宣传图、曲绘和资料图按规则给出的类型存档；Dynamix Universe 的素材、其他游戏的图片、界面元素、二维码和人物照片附原因排除；没有规则的图片保持待定，无法读取的文章记为失败。媒体副本标为 `repost`，其中出现的曲目只对应为 `inferred` 或不做对应，因为媒体文章既不是画师本人的帖子，也不是官方帖子。这些文章被转载的较低分辨率副本记录在 `archives:archive-leads` 中，而不是存档。

这些来源的记录以其引用的页面作为标题：文章标题，或者在页面标题只有游戏名时使用图集页面的标题，再加上图片说明和位置。文件名保留在 `original_filename` 中。

香港科技园新闻稿提到了 C4Cat 的 Dynamix。其中关于 C4Cat 的附录是一份 PDF（`reference`，`archive` 层级）；直接下载返回 HTTP 404，作为失败记录，同一份 PDF 从其 Wayback 存档中存档。关于其他公司的附录以 `not_dynamix` 排除，发布会合影以 `person_photo` 排除。

## 调研线索

`archives:archive-leads` 记录没有可获取文件或属于其他类别的线索：wiki 类别已经读取的社区 wiki 页面、曲绘属于合作方文件的合作游戏 wiki（仅作署名证据）、百科和元数据页面、谱面工具和游戏 API 调研笔记（不使用私有游戏服务）、可信度低的转载，以及返回机器人验证或 HTTP 403 的页面（记为被阻止，不绕过）。一个第三方的、数 GB 大小的 Fandom wiki 图片打包作为待定线索记录，没有下载：来历不明的批量打包不能代替可追溯的来源。

## 证据级别与访问限制

Wayback CDX API 为 `live_api`；媒体页面和文章为 `page_read`；线索登记为 `search_index`。Wayback Machine 只保存被抓取过的内容：没有存档并不能证明文件从未存在，存档的完整程度也只取决于当时的抓取。每个 CDX 列表都记录是否已读到末尾。

## 数量

下方标记之间的数字在每次构建后由 `scripts/update_docs_stats.py` 根据 `data/coverage.json`（`archives` 类别的 `sources.records`）和 `data/catalog.json` 生成，不是手工填写的。

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## 权利

存档副本不会改变归属。权利人默认为 “C4Cat Entertainment Limited and/or the credited artist”。证据写明其他权利人时，记录会写该权利人（香港科技园附录写明 Hong Kong Science and Technology Parks Corporation；画师的说明可能写明合作游戏的发行方），没有确认权利人时则留空。这些文件都没有确认任何许可证，因此 `rights.license` 保持为空；被网页档案收录并不构成作品的许可。

## 命令

```sh
.venv/bin/python -I scripts/fetch_archives.py --metadata-only     # 发现并记录候选；不下载
.venv/bin/python -I scripts/fetch_archives.py --resume            # 获取；重新校验已存档文件，而不是重新下载
.venv/bin/python -I scripts/fetch_archives.py --resume --source archives:wayback
.venv/bin/python -I scripts/fetch_archives.py --verify            # 离线
```

参数、锁、原子写入、错误处理和退出状态与 `fetch_artists.py` 相同（见 [sources-artists.zh-CN.md](sources-artists.zh-CN.md#命令)）。锁默认位于本类别的 `assets/public/archives/` 目录上。
