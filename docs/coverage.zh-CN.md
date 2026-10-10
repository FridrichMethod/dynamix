# 覆盖率

[English](coverage.md) · **简体中文**

`data/coverage.json` 报告档案已有什么、还缺什么。`scripts/build_coverage.py` 根据曲目索引（`data/sources/song-index.json`）、发现快照（`data/discovery/*.json`）、类别清单（`data/sources/<family>.json`）和目录生成它。每项指标都是单独定义的计数：记录数、文件数、图片数和曲目数是不同的量，从不互相替代。报告的 `definitions` 字段同样载有这些定义。

报告的日期取自其输入（曲目索引的 `as_of`、每份清单的 `fetched_at` 和每个快照的 `finished_at`），从不取当前时间，因此同样的输入重新构建得到完全相同的字节。输入无法对账时 `build_coverage.py` 拒绝写入：包括每个来源的处置合计、候选与资产或失败记录之间的链接、来源计数，以及资产的曲目 ID 与曲目索引的对照。`build_coverage.py --check` 把已提交的报告与重新构建的结果比较，不写入任何文件。

## 当前数字

下方标记之间的数字在构建目录时由 `data/coverage.json` 生成，不是手工填写的。

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## 来源

`sources` 按类别和 `status` 统计登记的来源：

| 状态 | 含义 |
| --- | --- |
| `available` | 已读取该来源，其范围内的文件可以获取。 |
| `partially_available` | 只能读取来源的一部分（例如只渲染了一部分的列表，或完整历史需要登录的账号）。 |
| `temporarily_unavailable` | 上次检查时无法访问该来源（例如主机名无法解析）。 |
| `manual_access` | 存在登录、Referer 检查或机器人验证；如实记录，从不绕过。 |
| `metadata_only` | 来源提供证据（曲目列表、署名、身份），但没有可获取的文件。 |
| `excluded` | 来源的所有候选都超出范围（例如反例对照）。 |

`sources.enumeration` 把来源分为 `complete`（发现快照把每个列表都读到了末尾）、`incomplete`（仍有未完成的列表，或该来源无法整体枚举）和 `not_enumerated`（没有快照）。不完整的枚举绝不会被解读为“没有更多内容”。`registered_without_record` 列出没有任何清单声明的已登记来源 ID。`sources.records` 的每一行给出一个来源的状态、证据级别、完整性、未完成的 continuation、发现错误、按处置分类的候选、排除原因、资产记录、以该来源记录为主记录的目录条目，以及按错误类别分类的失败。

证据级别：`live_api`（读取了在线 API 或 JSON 接口）、`page_read`（读取了网页）、`search_index`（调研线索，未发请求即记录）、`blocked`（唯一一次读取被拒绝）、`not_established`（没有成功的读取）。

## 候选与处置

候选是一次发现过程找到的一个上游文件或条目。快照中的每个候选恰好有一个处置结果：

| 处置 | 含义 |
| --- | --- |
| `downloaded` | 该来源为它保存了自己的资产记录和文件。 |
| `duplicate_bytes` | 同一来源已在另一个候选下存档了相同的字节；它指向那条记录，不再写入文件。 |
| `excluded` | 超出范围，原因取自固定词表（见下文）。 |
| `failed` | 下载或检查失败；清单的 `failures` 记录阶段、错误类别、HTTP 状态和尝试次数。 |
| `blocked` | 访问被拒绝（401、403、登录、Referer 检查或机器人验证），且不重试。 |
| `pending` | 尚未决定：`awaiting_download`、`unresolved_role`、`unresolved_game`，或附有原因的人工暂缓。 |

对每个来源，`downloaded + duplicate_bytes + excluded + failed + blocked + pending` 等于候选数，否则构建失败。`candidates.by_exclusion_reason` 使用的原因为 `universe_only`、`not_dynamix`、`animated_capture`、`person_photo`、`site_ui`、`game_ui`、`video_placeholder`、`external_release_art`、`fanart`、`upscaled`、`rejected_submission`、`redirect_or_alias` 和 `out_of_scope_other`。`candidates.by_game` 统计候选的游戏归属（`dynamix`、`dynamix_universe`、`mixed`、`unknown`、`not_dynamix`）。

## 文件与存储层级

| 指标 | 定义 |
| --- | --- |
| `source_asset_records` | 各类别清单中的资产记录，每个来源的每个文件一条。 |
| `unique_files` | 目录条目：不同的 SHA-256 值。多个来源的相同字节只计一次。 |
| `exact_duplicate_records` | `source_asset_records` 减去 `unique_files`。 |
| `gallery_images` | 属于栅格图片且类型不是 `reference` 的目录条目。 |
| `slideshow_images` | 类型为 `song_art`、`pack_cover`、`promotional` 或 `character`，且长边至少 1000 px 的图库条目。 |
| `by_kind`、`by_format` | 按类型和按解码格式统计的目录条目。 |
| `by_tier` | 网站提供原文件（`site`）或只提供预览图（`archive`）的目录条目。 |
| `variant_groups`、`entries_in_variant_groups`、`entries_with_variants` | 相关裁切或版本组成的组，以及其中的条目。不同版本是不同的文件，从不让曲目数量翻倍。 |
| `records_by_upstream_status` | 上游状态为 `current`、`removed`、`superseded`、`unknown` 或未设置的记录。 |
| `transformed_records` | 存储的字节已知为平台重新编码结果的记录。 |
| `gallery_long_edge` | 按解码长边统计的图库条目：小于 1000 px、小于 1920 px、至少 1920 px。 |
| `entries_with_preview` | 带 WebP 预览图的目录条目。 |

## 曲目

分母是 `data/sources/song-index.json` 中 `in_original_game: true` 的曲目集合。只有存在属于原版游戏的正面证据时，曲目才会进入分母：英文 wiki 上不属于 `Category:Unreleased` 的页面中的原版 `Template:Song`，或日文原版 wiki 列表中的一行。仅属于 Dynamix Universe 的曲目、未发布曲目，以及只出现在作为佐证的 wikiwiki 表格中的曲目都不进入分母（`outside_denominator`）。索引注明日期（`index_as_of`）；它并不声称是游戏当前的曲目总数。`denominator_removed` 和 `denominator_universe_also` 统计已从游戏中删除、或同时出现在 Dynamix Universe 中的曲目。

曲绘指类型为 `song_art` 的图库条目。它只通过写明某曲目的来源记录关联该曲目，并采用这些记录的对应状态；从不通过匹配标题来关联曲目。已确认的曲绘是由对应状态为 `confirmed_primary` 或 `confirmed_wiki` 的记录关联的曲绘。

| 指标 | 定义 |
| --- | --- |
| `with_any_artwork` | 有已确认曲绘的分母曲目。下面所有按曲目统计的指标都只计已确认的曲绘。 |
| `inferred_only` | 没有已确认曲绘、且所有曲绘关联都是 `inferred` 的曲目。 |
| `conflict_only` | 没有已确认曲绘、且至少有一个 `conflict` 关联的曲目。 |
| `missing_art` | 没有任何曲绘关联的曲目。`with_any_artwork + inferred_only + conflict_only + missing_art` 等于分母。 |
| `with_any_linked_asset` | 被任何类型、任何对应状态的目录条目（包括截图）提到的曲目。 |
| `with_confirmed_original` | 曲绘中有一条来自画师或官方类别的记录，其 `origin_status` 为 `artist_post` 或 `official`、`mapping_status` 为写明该曲目的 `confirmed_primary`，且为站点原文件的曲目。这并不表示找到了画师原稿。 |
| `with_long_edge_ge_1920` | 已确认曲绘的解码长边至少为 1920 px 的曲目。 |
| `low_resolution_only` | 已确认曲绘的长边全部小于 1920 px 的曲目。`best_long_edge` 按曲目最大的已确认曲绘分组（小于 1000、小于 1920、至少 1920 px）。 |
| `with_site_original`、`variant_unknown`、`preview_only` | 按已确认曲绘中最好的版本给曲目分类：有站点原文件、只有未知版本、或只有预览图和平台重新编码。三者之和等于 `with_any_artwork`。 |
| `unknown_illustrator` | 已确认曲绘的画师列表全部为空的曲目。 |

1920 px 分组只衡量显示尺寸。至少 1920 px 的文件并不因此就是真品、完整、未经裁切或画师原稿，较小的文件也不能说明是否存在更大的副本。记录的版本在 `quality.source_variant` 为 `site_original` 且不是已知平台转换时为 `site_original`；在其版本为 `preview` 或属于已知平台转换时为 `preview`；其余为 `unknown`。

## 对应状态

| 状态 | 含义 |
| --- | --- |
| `confirmed_primary` | 画师本人或官方的帖子或页面写明了曲目；引用的字段保存在 `mapping_evidence` 中。 |
| `confirmed_wiki` | wiki 曲目模板、图注或经过审阅的列表页写明了曲目。 |
| `inferred` | 证据较弱（例如从曲目列表链接的图片，或者依据调研来源而非获取到的页面）；需要复核。 |
| `unmapped` | 没有关联曲目。角色立绘、背景和宣传图本身常常没有对应曲目。 |
| `conflict` | 来源之间对曲目有分歧。 |

## 缺口

`gaps` 列出各项计数背后的 ID，以便逐一复核：`missing_art_songs`、`inferred_only_songs`、`conflict_only_songs`、`low_resolution_only_songs`、`preview_only_songs`、`variant_unknown_songs`、`unknown_illustrator_songs` 和 `linked_outside_denominator`（被文件关联但不在分母中的曲目）；`unknown_illustrator_assets`（没有署名画师的美术条目，不含截图和资料图）；`unmapped_assets`、`conflict_assets` 和 `inferred_only_assets`；以及每个快照中的 `pending_candidates`、`blocked_candidates` 和 `failed_candidates`。

## 字节

| 指标 | 定义 |
| --- | --- |
| `records_by_family` | 每个类别所有记录的字节数（被多条记录持有的文件按每条记录各计一次）。 |
| `unique_by_primary_family`、`unique_total` | 目录条目的字节数，每个条目只计一次，按条目主记录所属类别统计。 |
| `by_tier` | `site` 层级和 `archive` 层级原文件的字节数。 |
| `previews` | WebP 预览图的字节数。 |
| `catalog_json`、`catalog_script`、`index_html`、`coverage_json` | 生成文件的大小。 |
| `staged_site_estimate` | `site` 层级原文件、预览图和上述生成文件之和。不包括 `prepare_pages.py` 同样会暂存的静态页面、文档和图标；确切的预算由 `prepare_pages.py` 执行。 |
| `pages_budget`、`within_pages_budget` | 950,000,000 字节的网站预算，以及估算值是否在预算之内。 |

## 元数据分歧

来源之间并不总是一致，档案会记录分歧，而不是悄悄消解它们。

- **曲目索引分歧。** 当 wiki 之间对某首曲目有分歧时，曲目索引保留一条 `disagreements` 记录，写明曲目、字段和各来源的值。以这种方式记录的字段包括 `title`（例如 Miraheze 与日文 wiki 拼写不同的曲名）、`composers`、`presence`（某来源列出、但英文 wiki 上没有曲目页面的曲目）、`release_status`（出现在日文删除曲目列表中的曲目）和 `in_original_game`。`fetch_wikis.py` 在 `data/discovery/song-index.wikis.json` 中提出这些记录；覆盖率报告统计的是 `data/sources/song-index.json` 中整合后的列表，即 `mismatches.song_index_disagreements`（曲目和字段）。
- **来源声明不一致。** `mismatches.source_reported` 列出来源声明的值（SHA-1、字节数、尺寸或 MIME 类型）与保存的字节不一致的每条记录。来源没有声明的值无法比较，从不算作不一致。
- **记录之间的分歧。** `mismatches.record_disagreements` 列出字节相同、但不同来源的记录对类型或版本组意见不一的文件。
- **曲目索引与记录的对照。** `mismatches.song_index_artwork` 列出曲目索引中的 `artwork_asset_ids` 与写明该曲目的记录不一致的曲目。
- **其他交叉检查。** `mismatches.transformed_records` 列出已知的平台重新编码；`duplicate_bytes_other_source` 列出相同字节已由另一个来源存档的候选；`successor_not_primary` 列出继任记录不是其目录条目主记录的被替换记录。

分歧是待复核的事项。只有凭证据、通过修改来源规则或整合后的索引并重新构建来解决，绝不通过删除持不同意见的记录来解决。
