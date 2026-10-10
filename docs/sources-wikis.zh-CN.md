# Wiki 来源

[English](sources-wikis.md) · **简体中文**

本文档说明 `wikis` 来源类别：`scripts/fetch_wikis.py` 如何读取 Dynamix 社区 wiki，如何判断哪些文件属于原版游戏以及属于什么类型，以及记录保存在哪里。结果位于 `data/sources/wikis.json`（来源、资产记录、失败记录），以及每个来源一份的发现快照 `data/discovery/<source>.json`，后者列出每个候选的处置结果及其依据。

## 来源

| 来源 ID | 网站 | 用途 | 平台 |
| --- | --- | --- | --- |
| `wikis:miraheze` | [Miraheze 上的 Dynamix Wiki](https://dynamix.miraheze.org/wiki/Main_Page) | 文件和曲目元数据；当前的英文社区 wiki | MediaWiki |
| `wikis:fandom` | [Fandom 上的 Dynamix Wiki](https://dynamixc4cat.fandom.com/wiki/Main_Page) | 文件和曲目元数据；旧英文 wiki | MediaWiki |
| `wikis:atwiki-ja` | [atwiki 上的 Dynamix wiki](https://w.atwiki.jp/dynamix2ch/)（日文） | 仅读取原版游戏的曲目列表；不取文件 | atwiki |
| `wikis:rhythmgame-ja` | [音游 wiki：Dynamix](https://wikiwiki.jp/rhythmgame/Dynamix)（日文，wikiwiki.jp） | 一张曲目表，仅作佐证；不取文件 | wikiwiki |

本作的 Fandom wiki 是 `dynamixc4cat.fandom.com`；`dynamix.fandom.com` 属于无关的游戏，不使用。Miraheze wiki 是英文社区在 2026 年迁往的新站；其中大部分文件由维护脚本于 2026-08-09 从 Fandom 导入，因此 Miraheze 上的上传时间是导入时间，而不是最初的上传日期。

## 访问方式

### MediaWiki API（Miraheze 与 Fandom）

两个英文 wiki 都通过公开的 MediaWiki API 读取（`https://dynamix.miraheze.org/w/api.php` 和 `https://dynamixc4cat.fandom.com/api.php`），不登录：

- `list=allimages`：wiki 中每个文件的当前 URL、大小、MIME 类型、SHA-1 和上传时间；
- `generator=allimages` 加 `prop=fileusage|categories`：嵌入每个文件的页面，以及文件本身的分类；
- `generator=allpages`（主命名空间，不含重定向）：每篇条目的当前 wikitext、修订 ID 和分类；
- `prop=imageinfo&iiprop=metadata`：GIF 文件的帧数；
- `action=expandtemplates`：由模板拼出的曲名。

每个列表都会一直翻页，直到 API 不再返回 continuation。只有每个列表都读到末尾，快照才会是 `discovery_complete: true`；否则保留未完成的 continuation 令牌和错误，未读取的部分绝不会被当作“没有文件”。原始 API 响应不提交；使用 `--cache-dir` 可以复用它们来恢复一次中断的发现过程。

Miraheze 从 `static.wikitide.net` 原样提供文件；对抽样文件而言，API 报告的 SHA-1 与实际提供的字节一致。

### Fandom 的 CDN 与 `format=original`

Fandom 的 CDN（`static.wikia.nocookie.net`）默认对 `/revision/latest` 地址返回转换后的 WebP，即使路径以 `.png` 结尾。因此每次从 Fandom 下载都会附加 `format=original`，以取得存储的原文件。保存的文件按其字节解码出的格式命名，从不依据 URL 后缀。

对于许多 Fandom 文件，Fandom API 报告的大小和 SHA-1 与 `format=original` 返回的字节不同，而字节本身与 Miraheze 的副本一致。这一点按实际情况记录：API 的值保留在 `source_reported`，实测值保留在 `download_observed`，`comparisons` 把不一致的字段标为 `false`。这不被视为文件的第二个版本。

### 日文曲目列表

两个日文 wiki 以 HTML 页面读取，每个登记的页面请求一次，并作为不可信 HTML 解析。只读取表头中同时有曲名列和署名列的表格；各行按原文保存。atwiki 读取的页面是 Rank・Share 解禁曲目列表、曲包列表、活动及 frag 解禁曲目列表、联动曲目列表和删除曲目列表；wikiwiki 读取其唯一的 Dynamix 曲目表。附件不做枚举，因此这两个来源只提供曲目列表证据而不提供文件，它们的快照保持 `discovery_complete: false`。失败或被阻止的页面记为错误，绝不当作空列表。

## 分类

wiki 的文件命名空间远不止美术：还有个人资料照片、界面图标、游戏截图、公告图、视频以及 Dynamix Universe 的素材。因此一个 wiki 的文件总数既不是曲绘数量，也不是曲目数量。

### 类型

文件的类型取决于 wiki 在哪里、以什么方式使用它。由于 Miraheze 以相同文件名导入了 Fandom 的文件，证据按文件名在 Miraheze 和 Fandom 之间合并；每个判断都保留证据字符串，写明页面、上下文以及引用的图注或字段。最强的证据起决定作用：

1. 曲目模板的封面字段 `Imgsuffix`，或原版曲包页面中的 “Package …” 图片；
2. 图库或链接的图注、经过审阅的列表页或章节（例如 Characters 和 Parts 页面），或者文件分类；
3. 从原版列表链接的图片、曲目页 Trivia 或 Notes 章节中的图片，或者界面页面上有文档说明的尺寸规则；
4. 文件名惯例（`…Dynamix.png`、`…BG-HQ.png`、`…-Raw.png` 等），只在页面用法无法确定类型时用来补充。`-HQ`、`-Raw` 这类后缀并不能说明图片是否是完整插画。

只有文件名证据的文件，或者被同样多页面指向不同类型而打平的文件，保持 `pending` 并附具体原因，而不是靠猜测分类。大图不因为尺寸大就算作曲绘：`(XD)_D.jpg` 的尺寸为 2048×1152 px，但它所在的页面把它放在游戏音符说明中，因此它是游戏截图。

谱面一览、回归、新谱面和更新说明的公告图属于资料性图片，类型为 `reference`；游戏截图为 `gameplay_screenshot`。两者都会存档，但网站上只提供预览图（`archive` 层级）。主视觉、横幅、活动视觉和预告图为 `promotional`。

### 游戏

文件的游戏归属是使用它的各个上下文的游戏的并集：`Template:Song` 表示原版游戏，`Template:SongUV` 表示 Dynamix Universe，以某一作命名的页面和分类计入该作。同时讨论两作的页面、艺术家列表或首页本身不确定游戏，文件名也从不确定游戏。在同时带有两作模板的曲目页上，每一作的内容位于各自的标签页中，标签页之后的页面级图库列出原版游戏的活动物品。若文件的类型来自曲目页，且其 Fandom 上传时间早于该 wiki 第一个以 Universe 命名的上传，则计为原版游戏素材，证据字符串会写明这一点。未发布或已取消曲目的封面（`Template:SongOther`）保持待定，因为它们在游戏中的使用没有得到证实。

两作共用的文件以 `mixed` 接受，并在 `game_associations` 中保留 `dynamix_universe`；指明为其他游戏或活动制作的图注（“Artwork in …”）同样会把该游戏记为关联。只有仅在 Universe 上下文中使用的文件才会被排除。

### 排除与待定候选

每个候选恰好有一个处置结果：`downloaded`、`duplicate_bytes`、`excluded`、`failed`、`blocked` 或 `pending`。wiki 使用的排除原因包括 `universe_only`、`animated_capture`、`person_photo`（作曲者、艺术家和谱师的个人资料图片）、`site_ui`（wiki 皮肤、标志、导航模板）、`game_ui`（图标、按钮、评级徽章、商店中的 bits）、`external_release_art`（独立音乐发行物的封面）、`video_placeholder`（`video/*` 条目，它们是嵌入的视频而不是文件）和 `out_of_scope_other`。

动态 GIF 录屏默认排除；wiki 提供静态的 `-Raw.png` 版本时，会存档该静态图。`--include-animated` 也会接受动态文件，因此这一选择可以撤销。待定候选的原因为 `awaiting_download`、`unresolved_role` 或 `unresolved_game`，覆盖率报告会列出它们。

## 重复文件、修订与两个英文 wiki

每个 wiki 在自己的路径下保存自己的记录（`assets/public/wikis/miraheze/…` 和 `assets/public/wikis/fandom/…`）。两个 wiki 提供完全相同的字节时，Git 只存储一个 blob，目录把两条记录合并成一个条目，其来源记录列出两个来源；网站也只暂存一份。在同一个 wiki 内，字节相同的第二个文件名记为 `duplicate_bytes`，指向已有记录，而不再写入另一个文件。字节不同的文件即使看起来相似，也始终作为不同文件保留。

重新上传会保留文件名，因此保留同一个候选。新字节成为当前记录，旧字节继续存档并标为 `upstream_status: superseded`，较新的记录已存档时由 `superseded_by` 指向它。从 wiki 消失的文件保留其存档记录，标为 `removed`。如果当前证据不再接受某个已存档的文件，抓取脚本不写入任何内容，列出该文件供复核，并以状态码 4 退出。

## 曲目索引

`fetch_wikis.py` 提出 `data/discovery/song-index.wikis.json`，维护者把它整合进覆盖率分母 `data/sources/song-index.json`。曲目按英文页面标题分组，确切曲名取原版 `Template:Song` 的 `Title`（Miraheze 优先）；其他写法作为别名。在 `Template:Song` 中，`Artist`（或 `Compound Artist Link`）是作曲者，`Cover Artist`（或 `Cover Art`）是画师。两者分开按原文保存，`N/A` 等占位符表示未知，作曲者字段中明确标注的画师说明会从作曲署名中去掉，名字从不按 `&`、`,` 或 `x` 拆分。画师只关联到该曲目自己的封面文件，绝不关联到其页面上的每一张图。

只有存在正面证据时，曲目才计为原版游戏的一部分（`in_original_game: true`）：位于非 `Category:Unreleased` 页面上的原版 `Template:Song`，或 atwiki 原版曲目列表中的一行。wikiwiki 的表格只作佐证，单凭它不会加入曲目。只有 `Template:SongUV` 的页面是 Universe 曲目，`Template:SongOther` 页面是未发布曲目；两者都不进入分母。atwiki 的删除曲目列表（“已删除或从未实装”）会把曲目标为 `removed`，但单凭它不会把曲目加入分母；只有原版曲目页同样确认时，这样的曲目才会计入。wiki 之间有分歧时，草案记录分歧而不是默默选择其一（见[元数据分歧](coverage.zh-CN.md#元数据分歧)）。

## 证据级别与访问限制

英文 wiki 通过在线 API 读取（`live_api`）；日文 wiki 是页面读取（`page_read`）。2026-10-10 对两个英文 wiki 的仅元数据发现把每个 continuation 都读到了末尾；之后的快照是否完整，记录在其 `discovery_complete` 字段和覆盖率报告中，而不是默认假定。没有任何 wiki 需要登录，遇到错误也不会设法越过：401、403 或机器人验证记为 `blocked`，不重试。下载失败保留在清单的 `failures` 中，附带阶段、错误类别和尝试次数。

## 数量

下方标记之间的数字在构建目录时由 `data/coverage.json`（`wikis` 类别的 `sources.records`）生成，不是手工填写的。

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## 权利

wiki 上传的是游戏美术的副本，标为 `origin_status: wiki_copy` 和 `master_status: unverified`：即使是找到的最大副本，wiki 文件也不是画师原稿。权利仍属于 C4Cat Entertainment Limited 和/或署名画师；曲目模板写明画师时，记录的 `rights.creator` 为该画师，`rights.license` 保持为空，因为没有确认任何许可证。wiki 的内容许可并不能为其托管的游戏美术确立许可。

## 命令

```sh
.venv/bin/python -I scripts/fetch_wikis.py --metadata-only            # 枚举并分类；不下载
.venv/bin/python -I scripts/fetch_wikis.py --resume                   # 下载已接受的候选；重新校验已存档文件
.venv/bin/python -I scripts/fetch_wikis.py --resume --source miraheze --limit 100
.venv/bin/python -I scripts/fetch_wikis.py --resume --only "File:CosmicPulseDynamix.png"
.venv/bin/python -I scripts/fetch_wikis.py --verify                   # 离线
```

每次运行都会枚举两个英文 wiki，因为分类需要合并两者的证据；`--source` 限定写入哪个来源的快照、下载哪个来源。不带 `--resume` 的运行检查已存档文件是否存在且大小与记录一致；`--resume` 会重新计算它们的哈希。`--limit` 限制一次运行的下载尝试次数，`--only` 只下载指定的候选。抓取脚本持有排他锁（`--lock-file`，默认位于仓库之外、按检出目录区分的工作目录中；可用 `--work-dir` 更改），在发出下一个请求之前先把每次下载写入日志，并每隔几分钟原子地重写清单和快照，因此中断的运行最多丢失正在下载的那个文件，下一次运行会先重放日志。`--verify` 离线运行：校验清单和每个快照、核对处置结果、重新计算并解码每个文件、报告没有记录的文件，并检查曲目索引草案。

退出状态：0 完成；1 有下载失败或被阻止；2 发现过程不完整；3 另一个运行持有锁；4 某个已存档文件不再被接受（未写入任何内容，需要复核）；130 被中断（输出已写到最后一次完成的下载为止）。
