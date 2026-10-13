# Dynamix 曲绘档案

[English](README.md) · **简体中文**

C4Cat Entertainment Limited 出品的节奏游戏 **Dynamix**（原版）的个人曲绘档案，由 FridrichMethod（Zhaoyang Li）维护。仓库：<https://github.com/FridrichMethod/dynamix>。

只收录原版游戏。仅用于续作 Dynamix Universe 的美术不收录；两作共用的美术保留，并把另一作记录为关联游戏。

每个存档文件都按来源返回的原始字节保存，并附带来源记录：文件从哪里来、来源对它的描述、实际下载到的内容、署名的创作者，以及它如何对应到曲目。无法读取的来源、未能下载的文件和被排除的候选同样有记录，缺口始终可见，不会悄悄消失。

界面和构建工具改编自 DEEMO 曲绘档案（[FridrichMethod/deemo](https://github.com/FridrichMethod/deemo)，提交 `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`），后者又源自 Mashiro 的 [mashirozx/deemo](https://github.com/mashirozx/deemo)；没有复制该 DEEMO 档案的任何曲绘、音频、字体或数据（只改编了代码，见 [NOTICE](NOTICE)）。

**状态**：已发布 [v1.1.2](https://github.com/FridrichMethod/dynamix/releases/tag/v1.1.2)。网站通过 GitHub Pages 发布在 <https://fridrichmethod.github.io/dynamix/>（[可搜索图库](https://fridrichmethod.github.io/dynamix/archive.html) · [幻灯片](https://fridrichmethod.github.io/dynamix/)）；见[发布](#发布)。

## 浏览

共有两个静态页面，都读取本地目录 `data/catalog.js`，它由 `scripts/build_catalog.py` 根据来源清单生成。

- `index.html` 是幻灯片，由 [templates/slideshow.html](templates/slideshow.html) 渲染而成。它展示长边至少 1000 px 的单曲曲绘、曲包封面、宣传图和角色立绘，支持上一张/下一张、随机自动播放、截图按钮，并链接到每个文件和档案页。
- [archive.html](archive.html) 是可搜索的档案页。可搜索曲名、画师、作曲者和曲包；按来源类型、来源、类型和最小长边筛选；按曲名、来源或像素面积排序。网格每次加载 60 个结果，可继续加载更多。点击文件会打开查看器，显示文件本身、文件信息、每一条来源记录以及它的其他版本。

深度链接使用目录中的资产 ID：`archive.html#<asset id>` 在档案查看器中打开该文件，`index.html#<asset id>` 打开对应的幻灯片。

可以直接从文件夹打开页面（`file://`），也可以启动只监听本机的静态服务：

```sh
python3 -I -m http.server 8765 --bind 127.0.0.1
```

然后打开 <http://127.0.0.1:8765/archive.html>。页面放在 `/dynamix/` 这样的子目录下同样可用。浏览时只加载本地文件：没有远程字体、统计、音频，也没有其他网络请求。页面带有 `noindex,nofollow`，这只是请搜索引擎不要收录，并不是访问控制。

两个页面默认显示英文。每页的语言按钮（英文界面显示“中文”，中文界面显示“English”）在英文和简体中文之间切换，不发起任何网络请求。选择会记在浏览器中（`localStorage`，键名 `dynamix-lang`），并以 `?lang=zh-CN` 写入 URL（例如 `archive.html?lang=zh-CN`），因此用 `file://` 浏览时同样保持。只翻译界面文字：曲名、署名以及其他引用自来源的文字在两种语言下都按原文显示，清单中以英文写成的来源说明和维护者备注也按原文显示。

## 档案内容

### 类型与存储层级

每个文件有一个类型（kind）。类型决定网站提供原文件（`site` 层级）还是只提供预览图（`archive` 层级）：

| 层级 | 类型 | 存入 Git | 网站提供原文件 |
| --- | --- | --- | --- |
| `site` | `song_art`、`pack_cover`、`character`、`background`、`part`、`promotional`、`process` | 是 | 是 |
| `archive` | `reference`、`gameplay_screenshot`、`store_screenshot`，以及所有 PDF | 是 | 否：查看器显示预览图（PDF 没有预览图），并链接到 GitHub 仓库中的原文件 |

`reference` 指资料性图片和文档。按照 2026-10-10 的分类规则，凡是显示谱面难度等级的公告图都属于 `reference`，无论它预告的是一首曲目还是多首、来自 wiki 还是官方 X 或 Facebook 帖子（包括带等级标记的单曲谱面一览、“即将推出”或回归预告图）；主要内容是曲目或谱面列表的图片同样如此：活动与回归曲目阵容、“即将举行的活动”和“回归曲目”图，以及印有多张带曲名封面的曲包或联动阵容卡。这类图片中出现的封面美术另行存档为该曲目自己的 `song_art` 文件。更新说明、新闻稿 PDF 等文档也属于 `reference`。`promotional` 保留不含谱面等级信息、关于单首曲目、单个曲包或单个活动的图片：主视觉、活动和周年海报、单张联动主视觉、横幅、标志和字标。商店截图是应用商店的渲染版本，不是美术原稿。

`archive` 层级的原文件与其他文件一样保存在本仓库中；网站不提供它们只是为了控制体积。它们的目录条目带有 `archive_url`，即该文件在 `main` 分支上的 GitHub raw 地址（`https://github.com/FridrichMethod/dynamix/raw/main/...`），查看器会标注“原文件保存在仓库中”。这些链接跟随 `main` 分支，而不是网站所显示的版本：只有当包含该文件的提交已经位于 GitHub 仓库的 `main` 分支上时，链接才可用；该版本之后在 `main` 上撤回或移动的文件，在下一次发布之前链接会返回 404。每个版本的标签都按目录路径保留该版本的每个原文件，地址为 `https://github.com/FridrichMethod/dynamix/raw/<tag>/` 加上文件路径（见[发布](#发布)），Git 历史也保留这些字节。

### 不收录的内容

未存档的候选仍然记录在发现快照中，并附带原因，包括：

- 仅用于 Dynamix Universe 的美术（`universe_only`），以及不属于 Dynamix 的文件（`not_dynamix`）；
- 动态 GIF 录屏（`animated_capture`），它们连同元数据一起记录，但默认不下载；更大的静态版本（例如角色的 `-Raw.png`）是独立的候选，在其自身证据被接受时存档，`fetch_wikis.py --include-animated` 会把动态文件也一并下载，因此这一排除可以撤销；
- 人物照片（`person_photo`）、wiki 界面图片（`site_ui`），以及图标、按钮、评级徽章等游戏界面元素（`game_ui`）；
- 嵌入视频的占位条目（`video_placeholder`）、独立音乐发行物的封面（`external_release_art`）、同人图（`fanart`）、放大图（`upscaled`）和落选投稿（`rejected_submission`）；
- 其他文件的重定向或别名（`redirect_or_alias`），以及其他超出范围的文件（`out_of_scope_other`）。

从不获取付费或私有资料，也从不绕过登录、机器人验证或访问控制：这类来源记录为被阻止或需要人工访问。

“未存档”描述的是当前的文件树和目录。Git 历史从不改写，因此后来经复核撤回或重新分类的记录，其字节仍可在较早的提交中取得，尽管当前没有任何记录、目录条目或页面展示它们：一张后来查明是放大图的 wiki 照片（现为 `upscaled`）、属于独立发行物封面的 wiki eyecatch 图片（现为 `external_release_art`）、已被 Apple 对同一上传提供的 PNG 版本取代的 App Store JPEG 重新编码文件、记录在等待复核期间被撤回的画师个人插画（现为 `pending`），以及一张官方 X 的界面图条（现为 `site_ui`）。这些字节最早出现在以下提交中：放大图在 `a418229`，eyecatch 图片在 `6beba72`，App Store JPEG 在 `21a8d7a`，个人插画在 `765b5aa`，X 界面图条在 `74bf4e4`；撤回机制见 [docs/sources-wikis.zh-CN.md](docs/sources-wikis.zh-CN.md#重复文件修订与两个英文-wiki) 和 [docs/sources-artists.zh-CN.md](docs/sources-artists.zh-CN.md)。

### 文件按发布原样保存

不做任何放大、裁剪、重新压缩、调色、去背景或去水印。文件扩展名取自解码得到的实际格式，而不是 URL。同一作品的不同裁切、修订和版本是不同的文件；被替换或已从来源删除的上传仍然保留，并标明状态。唯一的派生图片是 `assets/thumbs/` 中的 WebP 预览图（长边 480 px），它们只是显示用副本，列在 `data/thumbs.json` 中；查看器、下载链接和仓库链接始终指向原文件。

“站点原文件”（site original）指来源提供的全尺寸文件，并不自动等于画师原稿：wiki 上传、商店渲染版本和平台重新编码的文件都会如实标注。

### Wiki 副本：Miraheze 与 Fandom

当前的 Miraheze 英文 wiki 以相同的文件名从旧 Fandom wiki 导入了文件。两者作为独立来源分别存档，各有自己的记录。请求 Fandom 文件时带上 `format=original`，否则它的 CDN 会返回转换后的 WebP。在 `data/sources/wikis.json` 记录的获取中，除一个文件名外，两个 wiki 都有的每个已接受文件名，Fandom 返回的这些字节都与 Miraheze 上同名文件完全相同，包括 Fandom API 为上传文件记录了另一个 SHA-1 和字节数的文件；这种差异会被记录，其原因没有得到确认，两个副本都不被说成是 Fandom 所记录的上传文件。剩下的那个文件名，Fandom 返回的字节与两个 wiki 记录的上传文件不同，因此这条 Fandom 记录标为平台转换（`transformed: true`），并作为单独的文件保留。多个来源中字节完全相同的文件合并为一个目录条目，列出所有来源记录，网站也只暂存一份。详细说明以及两个或更多 wiki 来源持有的目录条目的生成数量见 [docs/sources-wikis.zh-CN.md](docs/sources-wikis.zh-CN.md)。

## 来源记录模型

`data/catalog.json` 中每个不同的 SHA-256 对应一个条目。它的 `provenance` 列表保留这些字节的每一条来源记录，每条记录包括：

- **出处**：来源、来源页面、请求的下载地址和最终响应地址、原始文件名、获取时间，以及已知时的上游修订号。
- **`source_reported`**（来源声明的信息：URL、MIME 类型、字节数、尺寸、SHA-1、时间戳、修订号）与 **`download_observed`**（保存下来的字节的实际情况：内容类型、嗅探格式、字节数、解码尺寸、SHA-1、SHA-256）并列。**`comparisons`** 分别对 SHA-1、字节数、尺寸和格式说明二者一致（`true`）、不一致（`false`）还是无法比较（`null`）；不存在笼统的“已验证”标记。`transformed: true` 表示这些字节已知是平台重新编码的结果。
- **质量标签**：`source_variant`（`site_original`、`preview`、`unknown`）、`origin_status`（`artist_post`、`official`、`wiki_copy`、`repost`、`scan`、`unknown`）和 `master_status`（`confirmed` 或 `unverified`）。
- **署名**：`illustrators`（画师）和 `composers`（作曲者）是两个独立的列表，按原文复制；空列表表示未知，界面显示为“未知”。`credit_evidence` 引用写明每个名字的字段。作曲者绝不会被当作画师。
- **曲目对应**：`song_titles`、`song_ids` 和 `collections`，以及 `mapping_status`：`confirmed_primary`（画师本人或官方帖子写明了曲目）、`confirmed_wiki`（wiki 模板、图注或列表页写明了曲目）、`inferred`（证据较弱，需要复核）、`unmapped`（未对应）或 `conflict`（来源之间矛盾）。`mapping_evidence` 保存相应的 URL 和引用字段。
- **权利**：权利人、创作者、权利信息 URL、许可证和限制。权利人默认为 “C4Cat Entertainment Limited and/or the credited artist”；只有确实确认了许可证时，`license` 才会填写。
- **上游状态**：`current`、`removed`、`superseded`（较新的上传已存档时由 `superseded_by` 指向它）或 `unknown`；相关的裁切版本共享一个 `variant_group`。

**主记录**。条目自身的字段（标题、类型、文件、质量标签）取自其中一条记录，按固定规则选出：先看类别（`artists`、`official`、`wikis`、`archives` 依次优先）；同一类别内看来源，当前的 Miraheze wiki 排在旧 Fandom wiki 之前，因为 Miraheze 保存的正是两个英文 wiki 提供的字节，其他来源按 ID 排序；然后依次看版本（`site_original`、`preview`、`unknown`）、来源 ID 和资产 ID。因此即使 wiki 上有相同的字节，画师或官方的副本也会作为条目的主记录，其他记录保留在 `provenance` 中。

**标题**。标题是可读的名称，绝不是单纯的文件名。若 wiki 曲绘文件以 `confirmed_wiki` 或更强的证据只对应一首曲目，就使用该曲目的确切曲名；其他 wiki 文件使用 wiki 自己的图注，没有图注时使用去掉扩展名、下划线显示为空格的 `File:` 标题。其他类别的记录以其引用的帖子或页面文字作为标题。上游文件名始终保留在 `original_filename` 中。

曲目 ID 来自 `data/sources/song-index.json`，即根据各 wiki 整理、注明日期的曲目索引。它也列出不属于原版游戏的曲目（例如 Dynamix Universe 的曲目），这些曲目标为 `in_original_game: false`；只有 `in_original_game: true` 的曲目需要属于原版游戏的正面证据，也只有它们构成覆盖率分母。各项指标的定义见 [docs/coverage.zh-CN.md](docs/coverage.zh-CN.md)。

## 收藏统计

下方标记之间的数字在每次构建后由 `scripts/update_docs_stats.py` 根据 `data/coverage.json` 和 `data/catalog.json` 生成，不是手工填写的，并把记录数、文件数、图片数和曲目数作为不同的量分别报告。

<!-- STATS:BEGIN -->
<!-- Generated by scripts/update_docs_stats.py from data/coverage.json and data/catalog.json; rebuild the data and rerun the script instead of editing this block. -->

曲目索引日期：2026-10-10。各类别最近一次获取（清单的 `fetched_at`）：`wikis` 2026-10-11T04:27:27Z，`artists` 2026-10-10T21:00:40Z，`official` 2026-10-11T00:14:38Z，`archives` 2026-10-11T04:50:56Z。

| 项目 | 数值 |
| --- | --- |
| 来源 | 已登记 104：可获取 65，部分可获取 5，暂时无法访问 1，需人工访问 4，仅元数据 15，已排除 14 |
| 发现快照 | 完整 93，不完整 11，无快照 0 |
| 候选 | 10,183：已下载 7,138，重复字节 33，已排除 2,880，失败 7，被阻止 35，待定 90 |
| 最常见的排除原因 | `person_photo` 885，`video_placeholder` 572，`universe_only` 387，`game_ui` 291，`animated_capture` 288 |
| 待定候选（尚未决定） | 90 |
| 资产记录（每个来源的每个文件一条） | 7,138 |
| 唯一文件（不同的 SHA-256） | 3,773 |
| 完全重复的记录（资产记录减去唯一文件） | 3,365 |
| 图库图片 | 2,684 |
| 幻灯片图片 | 715 |
| 原版曲目分母中的曲目 | 537 |
| 有已确认曲绘的曲目 | 531（98.9%） |
| 有已确认画师或官方原图的曲目 | 18（3.4%） |
| 已确认曲绘长边 ≥ 1920 px 的曲目 | 54（10.1%） |
| 没有已确认曲绘的曲目 | 6：仅推断 6，仅冲突 0，无曲绘 0 |
| 已确认曲绘均无署名画师的曲目 | 256（47.7%） |
| 没有署名画师的美术条目 | 2,055 |
| 原文件字节数，site 层级（网站提供原文件） | 801,936,070 字节（801.9 MB） |
| 原文件字节数，archive 层级（原文件在仓库中，网站显示预览图） | 542,108,765 字节（542.1 MB） |
| 网站暂存估算 | 929,909,393 字节（929.9 MB），预算 950,000,000 字节（占 97.9%，在预算之内） |

<!-- STATS:END -->

## 可复现流程

### 环境

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 可选，仅浏览器测试需要：
.venv/bin/python -m pip install -r requirements-dev.txt
```

`requirements.txt` 固定了 Pillow 12.3.0、requests 2.34.2 和 beautifulsoup4 4.15.0；`requirements-dev.txt` 另外加入 Playwright 1.62.0，用来驱动已安装的 Google Chrome（不会下载浏览器）；`requirements-lint.txt` 固定代码质量工具 ruff、mypy、coverage.py 和 pre-commit（见[开发](#开发)）。CI 使用 Python 3.14；脚本可在 Python 3.10 及以上版本运行。预览图用 Pillow 12.3.0 和 libwebp 1.6.0 编码，`data/thumbs.json` 记录这两个版本；版本相同时重新生成的字节完全一致。所有脚本都以隔离模式（`python -I`）运行。

### 获取（联网）

每个来源类别有自己的抓取脚本，写入自己的清单 `data/sources/<family>.json`、发现快照 `data/discovery/<source>.json` 以及 `assets/public/<family>/` 下的文件：

```sh
.venv/bin/python -I scripts/fetch_wikis.py --metadata-only   # 枚举并分类，不下载任何文件
.venv/bin/python -I scripts/fetch_wikis.py --resume          # 下载已接受的候选，重新校验已存档文件的哈希
.venv/bin/python -I scripts/fetch_artists.py --resume
.venv/bin/python -I scripts/fetch_official.py --resume
.venv/bin/python -I scripts/fetch_archives.py --resume
```

所有网络访问都经过同一个共享客户端：显式的主机白名单，每次重定向都重新检查；同一主机一次只发一个请求，请求间隔至少 1 秒；遵守最长 60 秒的 `Retry-After`；HTTP 429、5xx 和超时最多尝试三次；遇到 401、403 或机器人验证不重试（记为被阻止）；单个响应上限 64 MiB；User-Agent 为 `DynamixArtworkArchive/1.0 (+https://github.com/FridrichMethod/dynamix)`。这些是本项目自己的默认值，不是来源公布的限制。下载先写入临时文件，解码并检查后才移动到位，因此中断的运行不会留下看似成功的记录。抓取脚本持有排他锁并原子地写入清单，同一类别的第二次运行会直接退出而不会争用；`--resume` 会重新计算已有文件的哈希，而不是只看文件是否存在。上游消失或变化的文件不会被丢弃：较早的字节继续保留，并以 `upstream_status` 标明。各来源的参数和限制见各来源文档（见[来源](#来源)）。

`fetch_wikis.py` 还会提出一份曲目索引草案 `data/discovery/song-index.wikis.json`；维护者审阅后将其整合进 `data/sources/song-index.json`，并保留各 wiki 之间的分歧（见 [docs/coverage.zh-CN.md](docs/coverage.zh-CN.md#元数据分歧)）。

### 构建与离线验证

```sh
.venv/bin/python -I scripts/fetch_wikis.py --verify
.venv/bin/python -I scripts/fetch_artists.py --verify
.venv/bin/python -I scripts/fetch_official.py --verify
.venv/bin/python -I scripts/fetch_archives.py --verify
.venv/bin/python -I scripts/build_thumbnails.py --prune
.venv/bin/python -I scripts/build_catalog.py --verify
.venv/bin/python -I scripts/build_coverage.py
.venv/bin/python -I scripts/update_docs_stats.py
```

`--verify` 完全离线地重新计算并解码每个存档文件，并核对每份清单与其发现快照。`build_thumbnails.py` 生成预览图（`--prune` 还会删除它自己列出、但原文件已不再需要的预览图；它从不改动原文件）。`build_catalog.py` 生成 `data/catalog.json`、`data/catalog.js` 和 `index.html`。`build_coverage.py` 生成 `data/coverage.json`，输入无法对账时拒绝写入。随后 `update_docs_stats.py` 重写本 README、`docs/coverage.md` 和 `docs/sources-*.md` 两种语言版本中的统计块；它只读取 `data/coverage.json` 和 `data/catalog.json`，两者的共同总数、各来源计数、字节总和、来源状态或获取快照不一致时拒绝使用（见 [docs/coverage.zh-CN.md](docs/coverage.zh-CN.md)），并且从不改动标记之外的文字。这些输出都不依赖当前时间，同样的输入重新构建得到完全相同的字节。只检查已提交的输出是否最新、不写入任何文件：

```sh
.venv/bin/python -I scripts/build_thumbnails.py --check
.venv/bin/python -I scripts/build_catalog.py --verify --check
.venv/bin/python -I scripts/build_coverage.py --check
.venv/bin/python -I scripts/update_docs_stats.py --check
```

`scripts/build_thumbnails.py --verify` 还会重新编码每张预览图并比较字节；它更慢，并且需要固定版本的 Pillow 和 libwebp。

### 网站暂存

```sh
out="$(mktemp -d)"
.venv/bin/python -I scripts/prepare_pages.py --output "$out/dynamix" --summary "$out/summary.json" --manifest "$out/manifest.sha256"
(cd "$out/dynamix" && sha256sum --check --quiet --strict ../manifest.sha256)
```

`prepare_pages.py` 只把 Git 跟踪的公开文件复制到检出目录之外的新目录：页面入口和署名文件、`assets/site/`、`docs/`（不含 `docs/implementation/`）、`licenses/`、`src/`、生成的目录和覆盖率文件、目录中指定的 `site` 层级原文件（每个 SHA-256 一份）以及目录中的预览图。脚本、测试、工作流、模板、来源清单和发现快照从不暂存。它拒绝符号链接和不安全路径、与 `HEAD` 不一致的文件（除非使用 `--allow-dirty`）、任何超过 100 MiB 的文件以及超过 950,000,000 字节的总量，并根据目录重新校验每个暂存原文件的哈希。如果总量会超出预算，首先不暂存 `data/catalog.json`（页面从不加载它：页面读取 `data/catalog.js`），并在摘要中记录；此时档案页指向完整清单的链接在网站上没有目标，但本仓库中的副本仍然保留；只有去掉它仍超出预算时才拒绝。如果仅 `site` 层级就超出预算，这会作为阻塞问题报告，绝不通过重新压缩或删除原文件来解决。白名单只限定网站可能发布的范围，并不是敏感内容检测器；提交任何文件前仍需检查其内容。

## 测试与 CI

```sh
.venv/bin/python -I tests/run_all.py
```

`tests/run_all.py` 在各自独立的解释器中运行每个 `tests/test_*.py` 模块；任何失败、没有运行任何测试的模块，或者一个模块都没有找到，都会导致失败。测试完全离线：只使用 `tests/fixtures/` 下的测试数据，从不访问来源网站。

浏览器验收测试驱动已安装的 Google Chrome：

```sh
.venv/bin/python -I tests/browser_smoke.py --fixture --browser /usr/bin/google-chrome
.venv/bin/python -I tests/browser_smoke.py --base http://127.0.0.1:8765/ --root . --browser /usr/bin/google-chrome
```

`--fixture` 根据 `tests/fixtures/ui/` 构建并自行提供多个站点（HTTP 根目录、`/dynamix/` 子路径、`file://`、空目录和恶意内容目录）；`--base` 检查按[浏览](#浏览)一节方式在本机提供的检出目录。两种运行在出现控制台错误、请求失败或任何离开本地服务器的请求时都会失败。

[Tests 工作流](.github/workflows/tests.yml)在每次推送和每个 PR 上以只读权限运行。lint 任务对每个文件运行全部 pre-commit 钩子（见[开发](#开发)）；任何钩子报告问题或会改动文件时它就失败，它不修复任何内容。离线任务在 coverage.py 下运行 `tests/run_all.py`，并运行 `build_catalog.py --verify --check`、`build_thumbnails.py --check`、`build_coverage.py --check`、`update_docs_stats.py --check`（文档中生成的统计区块是最新的）、对每个已有类别清单运行 `fetch_<family>.py --verify`，以及 `git diff --check`。它在运行摘要中公布 `scripts/` 的覆盖率，覆盖率低于 `pyproject.toml` 中的 `fail_under` 时失败，最后如果任何检查改动了检出目录也会失败。浏览器任务检查测试站点和本机提供的检出目录。站点任务用 `prepare_pages.py` 暂存网站，在 `/dynamix/` 下做冒烟测试，并作为普通 CI artifact 上传，保留七天；这不是 Pages 部署。所有 Action 都固定到完整的提交 SHA。

## 开发

代码质量工具和其他依赖一样固定版本：`requirements-lint.txt` 固定 ruff、mypy、coverage.py 和 pre-commit，`package.json` 与 `package-lock.json` 固定 ESLint 及其插件。ESLint 需要满足 `^20.19.0 || ^22.13.0 || >=24` 的 Node.js（`package.json` 中的 `engines`）；`npm ci --ignore-scripts` 把锁定的软件包原样安装到被 Git 忽略的 `node_modules/`，并且不运行它们的任何安装脚本。

```sh
.venv/bin/python -m pip install -r requirements-lint.txt
npm ci --ignore-scripts
.venv/bin/python -I -m pre_commit run --all-files
```

`pre-commit run --all-files` 对每个已跟踪文件运行 `.pre-commit-config.yaml` 中的全部钩子：ruff、ruff format、mypy、ESLint、actionlint，以及文件卫生检查（行尾空白、文件末尾换行、混合换行符、YAML/JSON/TOML 语法、合并冲突标记、大文件、私钥）。Tests 工作流的 lint 任务运行的正是这条命令。如果想在自己的克隆中每次提交时自动运行这些钩子，执行一次 `.venv/bin/python -I -m pre_commit install` 即可。每个工具也可以单独运行：

```sh
.venv/bin/python -I -m ruff check
.venv/bin/python -I -m ruff format --check
.venv/bin/python -I -m ruff format
.venv/bin/python -I -m mypy
npx --no-install eslint --max-warnings=0 src
```

`ruff check` 和 `ruff format --check` 只报告问题；`ruff format` 会应用格式化（行宽 120；ruff 和 mypy 按最低支持版本 Python 3.10 检查）。mypy 检查 `scripts/lib`（第一阶段，不启用 strict 模式）；分阶段扩大检查范围的计划见 `docs/implementation/09-frozen-contracts.md`。ESLint 检查 `src/**/*.js` 和 `src/**/*.css`，从不使用 `--fix` 运行：`src/` 按提交时的原样发布，因此发现的问题要在经过审阅的改动中手动修复；本项目也没有 JavaScript 或 CSS 格式化工具。

单元测试对 `scripts/` 的覆盖率，包括测试以子进程方式启动的每个脚本：

```sh
.venv/bin/python -I -m coverage run tests/run_all.py
.venv/bin/python -I -m coverage combine
.venv/bin/python -I -m coverage report
```

`coverage combine` 合并每个被测量进程的数据文件；总覆盖率低于 `pyproject.toml` 中的 `fail_under` 时，`coverage report` 失败。`fail_under` 是实测的基线，只能提高，不能降低。机械格式化提交列在 `.git-blame-ignore-revs` 中；执行 `git config blame.ignoreRevsFile .git-blame-ignore-revs` 之后，`git blame` 会跳过该提交。

任何代码质量工具或钩子都不读取也不改写 `assets/`、`data/`、`index.html`、`LICENSE`、`licenses/*.txt`、`src/vendor/` 和 `tests/fixtures/`。它们是按字节固定的文件（`.gitattributes` 中标为 `-text`；清单和测试数据记录其 SHA-256）以及生成的输出（`--check` 命令逐字节比较它们），格式化或修复工具只会破坏它们。

## 来源检查（手动、只读）

[Check sources 工作流](.github/workflows/check-sources.yml)只能在 Actions 页面手动启动，没有定时触发。它运行 `scripts/check_sources.py`，重新列出 Miraheze 和 Fandom 的文件元数据（`list=allimages`，不下载文件），与已提交的发现快照比较。报告列出新增文件、变化的文件（SHA-1、大小、尺寸、MIME 类型或上传时间）和被删除的文件；只有完整读取了在线列表时，文件才会算作被删除，否则只报告为“未见到”。报告和 JSON 摘要作为运行 artifact 上传。在线列表不完整时运行以状态码 3 退出，部分检查绝不会被当成干净的检查。

该工作流只有只读令牌：从不提交、推送、创建分支或 PR，也不改动数据。本地运行：

```sh
.venv/bin/python -I scripts/check_sources.py --report /tmp/dynamix-source-check.md --summary /tmp/dynamix-source-check.json
```

根据报告采取行动，意味着在本地运行抓取脚本、审阅差异，并像其他改动一样提交。

## 发布

`v1.1.2`（2026-10-12）是当前版本，以 [GitHub Release](https://github.com/FridrichMethod/dynamix/releases/tag/v1.1.2) 的形式发布；上面的网站由其标签所指的提交部署。`v1.1.1`（2026-10-12）是上一个版本，`v1.1.0`（2026-10-11）是再上一个版本。`v1.0.0`（2026-10-11）是首个版本，其 [GitHub Release](https://github.com/FridrichMethod/dynamix/releases/tag/v1.0.0) 以一个下载包提供该版本的完整存档：每个原文件一份，放在其目录路径下（包括 `archive` 层级的文件；同一字节在其他记录路径下的副本不重复收录，目录按 SHA-256 对应到文件），另附 `data/`（目录、覆盖率、来源清单与发现快照）、公开文档、`LICENSE`、`NOTICE`、`licenses/` 和 `SHA256SUMS` 校验清单。

- [Pages 工作流](.github/workflows/pages.yml)只部署提交在 `main` 上的 `vX.Y.Z` 标签：已发布的 Release，或在这样的标签上的手动触发。`vX.Y.Z` 标签是 `v` 加三个不带前导零的数字（`v1.10.0`，而不是 `v01.1.0`）。用这样的标签发布、且不是预发布版本的 GitHub Release 会部署该标签所指的提交，在这样的标签上手动触发（`gh workflow run pages.yml --ref v1.1.2`）会重新部署它。每次运行，无论是发布的运行、它的重新运行还是手动触发，都会先查询该标签的 GitHub Release，只有该 Release 已发布且不是预发布版本时才部署该标签所指的提交。重新运行沿用它所重复的那次运行的事件，因此当其 Release 之后变成草稿或预发布版本、或被删除时，查询也会拒绝这次重新运行。只重新运行部署任务，或重新运行不含第一个任务（“Release approval”）的任何任务，都不会部署任何内容；应重新运行整次运行（“Re-run all jobs”）。运行使用该标签所指提交中的工作流，因此 Release 查询适用于 `v1.1.1` 起的标签：较早标签上的运行会使用该标签自己的工作流，它不检查 Release，`github-pages` 环境会拒绝它的部署。对当前这次尝试的记录和在 GitHub 上对标签的检查适用于 `v1.1.2` 起的标签：在 `v1.1.1` 上的运行会使用该标签自己的工作流，两者都没有，因此这样的运行也应整次重新运行。推送、PR 和定时任务从不启动它。其他任何运行都会在构建任何内容之前失败关闭：在分支上的手动触发、不是 `vX.Y.Z` 的标签（例如 `v1.1`、`v1.1.0-rc1` 或 `v01.1.0`）、草稿或预发布的 Release、没有已发布且非预发布 Release 的标签（或查询失败）、在批准任务或构建任务检查时已在 GitHub 上指向另一个提交的标签（运行开始后被移动过），或者提交不在 `main` 上的标签。发布的运行会检查这些条件，在该标签的提交上重新运行全部测试，用 `prepare_pages.py` 暂存网站并部署。
- 之后推送到 `main` 的提交会被测试但不会部署，因此网站显示的是最近一次发布的提交，可能比 `main` 旧。
- 网站上 `archive` 层级文件的仓库链接（`archive_url`，`main` 上的 raw 地址）跟随 `main` 而不是已发布的提交：发布之后在 `main` 上撤回或移动的文件，在下一次发布之前链接会返回 404。该版本的标签保留其每个文件，地址为 `https://github.com/FridrichMethod/dynamix/raw/v1.1.2/` 加上文件路径。
- Tests 工作流在每次推送时准备并检查网站，但只上传 CI artifact，不能部署。

之后的发布需要：一个经过审阅且 CI 通过的 `main` 提交，并同时更新两份 README 中的状态一行和本节（它们会随网站一起暂存）；然后在该提交上用新的 `vX.Y.Z` 标签发布 GitHub Release。发布必须使用 `vX.Y.Z` 标签：工作流拒绝其他任何标签。仓库的 `github-pages` 环境只接受来自 `v1.1.1` 起的标签的部署，名为 `Release tags v*` 的仓库规则集只允许仓库管理员创建、移动或删除 `v*` 标签。

## 来源

| 类别 | 文档 | 内容 |
| --- | --- | --- |
| Wiki | [docs/sources-wikis.zh-CN.md](docs/sources-wikis.zh-CN.md) | 当前的 Miraheze 英文 wiki、旧 Fandom wiki，以及两个只读取曲目列表的日文 wiki。 |
| 画师 | [docs/sources-artists.zh-CN.md](docs/sources-artists.zh-CN.md) | 署名画师与作曲者明确指定的帖子和发行页面（X、pixiv、SoundCloud、Bandcamp、Bilibili、ArtStation、一个作品集网站），以及调研线索。 |
| 官方 | [docs/sources-official.zh-CN.md](docs/sources-official.zh-CN.md) | App Store、Google Play 和 TapTap 页面，官方 X、SoundCloud、Facebook 和 YouTube 账号，C4Cat 公司的 Facebook 页面，以及游戏官网和公司网站。 |
| 档案 | [docs/sources-archives.zh-CN.md](docs/sources-archives.zh-CN.md) | Wayback Machine 存档、一个媒体截图图集、媒体文章、一篇机构新闻稿，以及历史调研线索。 |

覆盖率指标及其定义：[docs/coverage.zh-CN.md](docs/coverage.zh-CN.md)。

## 目录结构

```text
assets/public/{wikis,artists,official,archives}/<source>/  存档文件，命名为 <slug>--<sha256[:12]>.<ext>
assets/thumbs/                         派生的 WebP 预览图（显示用副本，不是存档文件）
assets/site/                           本项目制作的图标（见 assets/site/README.md）
data/sources/<family>.json             类别清单：来源、资产记录、失败记录
data/sources/song-index.json           注明日期的曲目索引（in_original_game: true 的曲目构成覆盖率分母）
data/discovery/<source>.json           发现快照：每个候选及其处置结果
data/catalog.json, data/catalog.js     去重后带完整来源记录的目录；离线浏览数据
data/thumbs.json, data/coverage.json   预览图清单；覆盖率报告
scripts/                               抓取、构建、来源检查和网站暂存；scripts/lib/ 为共享库
src/, templates/slideshow.html         页面代码和幻灯片模板
archive.html, index.html               两个页面（index.html 为生成文件）
tests/                                 离线测试、测试数据和浏览器测试
docs/                                  来源与覆盖率文档（docs/implementation/ 为规划文档）
licenses/                              第三方许可文本和许可索引
LICENSE, NOTICE                        软件许可证和署名
```

公开文档（本 README、`docs/sources-*.md`、`docs/coverage.md` 和 `licenses/README.md`）各有后缀为 `.zh-CN.md` 的简体中文版本；`assets/site/README.md` 内含中文部分。

## 许可与署名

代码以 Apache License 2.0 授权（[LICENSE](LICENSE)）。软件许可证不适用于曲绘：Dynamix 的曲绘和游戏素材属于 C4Cat Entertainment Limited 和/或署名画师，在这里存档或发布文件不会改变其归属。不为任何曲绘推定许可；只有确实确认了许可证时，记录中才会写明。每个文件的权利、署名和来源 URL 记录在 `data/catalog.json` 中各条目的来源记录里。

[NOTICE](NOTICE) 列出哪些内容改编自 DEEMO 曲绘档案和 Mashiro 的原始图库，以及截图功能捆绑的第三方 JavaScript（html2canvas、punycode.js 和 Pace，均为 MIT 许可）。[许可索引](licenses/README.zh-CN.md)把仓库的每个部分对应到其许可或权利说明入口。
