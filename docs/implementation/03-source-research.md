# Dynamix artwork source research

Research date: **2026-10-10**. Scope: **original Dynamix only**, confirmed by the user. Parallel research covered official/publisher sources, artist portfolios, English/Japanese/Chinese/Korean community indexes, GitHub, historical/press sources and image platforms. All links below are public discovery/provenance links; this packet contains no production artwork downloads.

Evidence levels used here: **live API/bytes verified**, **page read**, **search-index lead**, **blocked/unavailable**, and **not established**. An indexed page can remain useful evidence while live retrieval fails. Access findings describe this session and may change. This is a broad researched starting inventory, not a proof that the entire internet or every historical artist upload has been exhausted.

## 1. Core acquisition sources

| ID / priority | Source and evidence | Intended use and next action |
| --- | --- | --- |
| `miraheze` / P0 | [Dynamix Wiki](https://dynamix.miraheze.org/wiki/Main_Page); **live API verified**, 894 articles and 4,469 images | Current English metadata/file inventory. Enumerate all continuation pages; filter original-game membership and media role. Statistics include unrelated roles and Universe. |
| `fandom` / P0 | [Legacy Dynamix Wiki](https://dynamixc4cat.fandom.com/wiki/Main_Page); **live API verified**, 756 articles and 4,924 images | Union with Miraheze; older files and revisions may differ. Preserve independent provenance and actual CDN observations. |
| `atwiki-ja` / P0 metadata, P1 media | [Japanese Dynamix wiki](https://w.atwiki.jp/dynamix2ch/); **page read**, explicitly announces migration to Miraheze | Original-game coverage cross-check and attachments. Use [rank/share song list](https://w.atwiki.jp/dynamix2ch/pages/25.html) and [characters](https://w.atwiki.jp/dynamix2ch/pages/33.html); do not assume any one list covers all events/packs. |
| `rhythmgame-ja` / P1 metadata | [Dynamix rhythm-game summary](https://wikiwiki.jp/rhythmgame/Dynamix); **page read** | Song/collaboration/artist leads; recheck song-list freshness separately from page modification time. |
| `artist-ariiol` / P0 | [Cosmic Pulse creator post](https://www.bilibili.com/opus/1022295438715256855); **search-index content**, dated 2025-01-14; live wiki attribution corroborated | Creator identifies their Dynamix illustration in indexed text. Resolve attached public original rendition and compare with the verified wiki copy. |
| `artist-sandsuna` / P0 | [Artist work history](https://sandsuna.art/pages/Works); **page read**, names MassacrE illustration/design for RAVON and Dynamix | Follow the specific project, not the entire unrelated portfolio. See original/variant lead below. |
| `apple-store` / P1 references | [Apple Dynamix](https://apps.apple.com/us/app/dynamix/id945955095); **live page and sample bytes verified** | Native-sized store screenshots; label them `store_screenshot`, not song art. Original app ID `945955095`. |
| `google-play` / P1 references | [Google Play Dynamix](https://play.google.com/store/apps/details?id=com.c4cat.dynamix&hl=en_US); **live page and sample bytes verified** | Package `com.c4cat.dynamix`; screenshots/icons only, excluding avatars/site logos. Decode dimensions from bytes. |
| `taptap` / P2 references | [TapTap listing](https://www.taptap.cn/app/7082), [details](https://www.taptap.cn/app/7082/all-info); **page read**, C4Cat identified | Secondary store screenshots; visible update text is historical, not a current song denominator. |

The [Japanese wiki](https://w.atwiki.jp/dynamix2ch/) supplies migration evidence; the API inventories demonstrate that Fandom and Miraheze currently differ. Neither image count is an artwork count or a song count. The Fandom host is **`dynamixc4cat.fandom.com`**; `dynamix.fandom.com` concerns unrelated Sierra/Dynamix games.

Verified endpoints:

- [Miraheze statistics](https://dynamix.miraheze.org/w/api.php?action=query&meta=siteinfo&siprop=statistics&format=json)
- [Fandom statistics](https://dynamixc4cat.fandom.com/api.php?action=query&meta=siteinfo&siprop=statistics&format=json)
- [Miraheze sample allimages](https://dynamix.miraheze.org/w/api.php?action=query&list=allimages&ailimit=8&aifrom=%28XD%29&aiprop=url%7Csize%7Cmime%7Csha1&format=json)
- [Cosmic Pulse wikitext](https://dynamix.miraheze.org/w/api.php?action=parse&page=Cosmic+Pulse&prop=wikitext&format=json)

Use MediaWiki's documented [Allimages](https://www.mediawiki.org/wiki/API:Allimages) and [Imageinfo](https://www.mediawiki.org/wiki/API:Imageinfo) APIs. Follow returned continuation fields exactly. Enumerate file metadata, then use templates/categories/file usage for classification and mapping. Fetch revision/original metadata without assuming `-HQ`, `-Raw`, `-Still`, or `_D` means a full illustration. Source SHA-1 and downloaded-file SHA-256 serve different purposes; decoding separately verifies dimensions and readability.

## 2. Creator-original and cross-game variant seeds

| Work / creator | Evidence and exact links | Acquisition status / pitfalls |
| --- | --- | --- |
| Cosmic Pulse / ariiol | [Creator Bilibili post](https://www.bilibili.com/opus/1022295438715256855); [creator X post](https://x.com/ariiol_/status/1879155241788293570); [song page](https://dynamix.miraheze.org/wiki/Cosmic_Pulse) with `Cover Artist=ariiol` | Wiki PNG verified at 2048×1152. Highest resolution on the original artist posts is not yet verified. Keep composer Yunosuke separate. |
| MassacrE \|\| 3rc4224M / Sand Suna | [Artist portfolio project](https://sandgodoven.artstation.com/projects/6baqbW), [canonical ArtStation](https://www.artstation.com/artwork/6baqbW), [work history](https://sandsuna.art/pages/Works) | Indexed portfolio explicitly labels Square, 16:9 and Original compositions and both game commissions. Live request returned 403; asset dimensions unverified. Keep distinct variants and rights notice. |
| (XD) / HAT | [Song page](https://dynamix.miraheze.org/wiki/%28XD%29); template credits HAT; [file page](https://dynamix.miraheze.org/wiki/File:%28XD%29Dynamix.png) | 1024×576 PNG verified. Creator account identity unresolved; do not equate HAT with HastyHat/An Yuki by name similarity. |
| Moonlight of Sand Castle / NanoKun | [Dynamix](https://dynamix.miraheze.org/wiki/Moonlight_of_Sand_Castle), [Arcaea](https://wikiwiki.jp/arcaea/Moonlight%20of%20Sand%20Castle), [Japanese Dynamix entry](https://w.atwiki.jp/dynamix2ch/pages/50.html) | Cross-wiki illustrator attribution; primary artist post unresolved. Compare Dynamix/Arcaea/Hachi Hachi crops/logos without substituting one game's cover for another. |
| WHIRLWIND / Keiyuu & RED-FOX | [Song/variant page](https://dynamixc4cat.fandom.com/wiki/WHIRLWIND); [PABAT! 2017 entry](https://k-bms.com/party_pabat/party2017.jsp?board_num=17&num=43&odtype=a&order=reg) | Upstream entry had human-verification challenge. Follow manually through ordinary access if available; no automated bypass. Preserve Normal/Hard/Extra/Superhard variants. |
| Tech-Nique / MayMiyuriAe lead | [Composer post](https://soundcloud.com/whos-raphiiel/tech-nique?in=ez2teshin%2Fsets%2Fdynamix-submission-2021), [credited illustrator account](https://www.instagram.com/maymiyuriae12/) | Illustrator credited on music post, but image identity versus game jacket is unverified; might be compilation art. |

Additional creator-side discovery:

- [TQ☆ creator profile](https://twpf.jp/tq2501) lists Dynamix work and track names; [DCWH portfolio](https://profile.dcwh.me/) establishes creator identity. These are composer leads, not verified illustration catalogs.
- [Synthtonix Supernova](https://soundcloud.com/synthtonixmusic/supernova-dynamix-open-submission-2023) explicitly describes successful inclusion; verify its image separately.
- [Dragon_Klub DeaDLiNE](https://dorakura.bandcamp.com/album/deadline) and [OVERDRIVE. Creigis](https://drive-5.bandcamp.com/track/creigis-long-ver) establish game music relationships. Album cover art is not automatically the game jacket.
- [Reku Mochizuki foriio](https://www.foriio.com/rekumczk/wk_AWpdEXuTgqMCkdYYeW6J3TviVezQFHg8SCtVWrMP7QyhCuCCPA) credits ELYXiVN ELYXiUM composition, not illustration.
- [Legacy artists index](https://dynamixc4cat.fandom.com/wiki/Artists_%28Legacy%29) is principally a composer index; TangJuan/Se-U-Ra remain song-specific follow-up leads, not a blanket illustrator mapping.
- Negative-control fixture: [Beyond Ephemera](https://veirtunes.bandcamp.com/track/beyond-ephemera) explicitly calls itself a **rejected submission**. Its Dynamix keyword must not make it an in-game artwork record.

Artist search must proceed from the complete song/illustrator index during implementation. The seeds above are examples, not the full roster. Preserve primary post identity and page index for multipage posts; never assign all pages the same song solely from the post title.

## 3. Official, historical, secondary and blocked sources

| Source | Observed evidence/access | Disposition / follow-up |
| --- | --- | --- |
| [Official game site](https://dynamix.c4-cat.com/) / [HTTP](http://dynamix.c4-cat.com/) | Browser retrieval error; independent HTTPS probe failed DNS resolution | Record temporary unavailability. Recheck once during implementation; inspect public historical snapshots if a valid capture is found. No working current art library verified. |
| [C4Cat](https://c4-cat.com/) | Coming-soon/contact placeholder | Identity lead; no game catalog observed. |
| [Official Facebook](https://www.facebook.com/c4cat.dynamix/) | Linked by stores; retrieval “Temporarily Blocked” | Seed exact public post permalinks when available. No claim that whole history can be enumerated. |
| [Dynamix X](https://x.com/c4c4cat) | Community/composer corroboration; direct retrieval error | Official-social lead, with account identity rechecked before ingest. No scraping around login/challenges. |
| [HKSTP release](https://www.hkstp.org/zh-cn/park-life/news-and-events/news/press-release-20160809), [appendix PDF](https://www.hkstp.org/media/blqg5pwq/appendix3.pdf) | Institutional press source | Historical reference/press images; not verified song-art masters. Preserve PDF bytes if acquired. |
| [4Gamer gallery](https://www.4gamer.net/games/306/G030626/screenshot.html) | Five screenshots, dated 2015-06-18 | Historical press screenshots only; do not inflate jacket coverage. |
| [Moegirl canonical](https://zh.moegirl.org.cn/Dynamix) | Canonical URL identified; body unavailable | Chinese metadata lead. Prefer canonical evidence over mirrors. |
| [Moegirl song-list mirror](https://moegirl.icu/Dynamix/%E6%9B%B2%E7%9B%AE%E5%88%97%E8%A1%A8) | Search-index lead says 537 songs as of 2025-04-24 | Historical count only, not current coverage target. |
| [Namu](https://namu.wiki/w/Dynamix) | Linked by Japanese wiki; limited direct access | Korean metadata/credit follow-up. |
| [LibreWiki](https://librewiki.net/wiki/Dynamix) | Search-index article | Korean secondary metadata; no high-res collection verified. |
| [Taskeren API notes](https://github.com/Taskeren/dynamix-api-document) | Public repo documents game API investigation | Metadata research only. Not an official contract or artwork archive; do not start private game-server/credential-based extraction. |
| [DyNode](https://github.com/NordLandeW/DyNode) | Chart tool; credits C4Cat game assets separately from MIT code | Possible provenance lead; not a validated comprehensive art repository. |
| [VSRG format docs](https://github.com/kangalio/vsrg-format-docs) | Dynamix chart-format documentation | Format reference, not artwork corpus. |
| [Duitang collaboration repost](https://api.duitang.com/blog/?id=1008753762) | Low-confidence repost | Trace original source first; no auto-admission from tags. |
| [Chinese Wikipedia](https://zh.wikipedia.org/wiki/Dynamix) | Original-game article | App identity only; no Commons artwork collection established. |

**Searched without a validated corpus or asset in this pass:** dedicated Dynamix BWIKI; Internet Archive art collections; a publisher downloadable press kit/artbook; Pixiv and DeviantArt original-game illustration posts; Wikimedia Commons; community high-resolution image packs. This is not evidence of absence. ArtStation did yield the Sand Suna project through the official-source agent even though broader platform searches were unproductive. Do not copy DEEMO's existing BWIKI/Pixiv IDs into Dynamix or invent endpoints because those adapters existed there.

Paid artbooks, app bundles from unofficial APK mirrors, unexplained bulk archives, and gameplay videos are not substitutes for verified public illustration sources. Do not obtain audio or paid/private source material for this artwork task. Public historical snapshots are eligible discovery leads only after the original page, capture timestamp and exact file can be established.

Excluded sequel identifiers: [Apple Universe `6741094831`](https://apps.apple.com/us/app/dynamix-universe/id6741094831), [Google Universe `com.c4cat.dynamix2`](https://play.google.com/store/apps/details?id=com.c4cat.dynamix2), [Universe Linktree](https://linktr.ee/dynamixuniverse), and [Universe Japanese wiki](https://wikiwiki.jp/dynamixuv/). These help classify mixed inventories; they are not acquisition goals.

## 4. Verified resolution and content-type samples

The research agents retrieved small public samples into memory using bounded HTTP requests, decoded them with Pillow, and computed the hashes listed below. The Google screenshot was decoded but not hashed in this pass. No production assets were written into this repository. API-only metadata and actual-byte observations are deliberately separated below; repeat representative probes before relying on old values.

| Sample / source page | Observed decoded bytes | Interpretation |
| --- | --- | --- |
| [CosmicPulseDynamix file page](https://dynamix.miraheze.org/wiki/File:CosmicPulseDynamix.png), [PNG](https://static.wikitide.net/dynamixwiki/8/8d/CosmicPulseDynamix.png) | PNG, 2048×1152, 3,651,648 bytes | Strong artwork seed; wiki copy does not prove the artist's maximum available resolution. |
| [(XD) file page](https://dynamix.miraheze.org/wiki/File:%28XD%29Dynamix.png), [PNG](https://static.wikitide.net/dynamixwiki/0/0a/%28XD%29Dynamix.png) | PNG, 1024×576, 685,481 bytes | Original-game cover candidate with separate HAT attribution evidence. |
| [WHIRLWIND file page](https://dynamixc4cat.fandom.com/wiki/File:WHIRLWINDBG.png), [CDN response](https://static.wikia.nocookie.net/dynamixc4cat/images/9/9b/WHIRLWINDBG.png/revision/latest?cb=20210409081347) | WEBP, 200×200, 17,642 bytes | `.png` path does not guarantee PNG response or original-upload bytes. |
| [Apple phone sample](https://is1-ssl.mzstatic.com/image/thumb/Purple123/v4/f0/9c/ad/f09cad41-0b41-d96d-5c48-be8c76b110f4/pr_source.png/2688x1242bb.jpeg) | JPEG, 2688×1242, 684,583 bytes | Native-sized store rendition, not song artwork or a master. |
| [Apple tablet sample](https://is1-ssl.mzstatic.com/image/thumb/Purple113/v4/a8/9c/0c/a89c0c45-e49f-72ab-0a80-754c422d057f/pr_source.png/2732x2048bb.jpeg) | JPEG, 2732×2048, 892,570 bytes | Store screenshot. |
| [Google screenshot sample](https://play-lh.googleusercontent.com/P3sU0wnQ9kUeB-hjBw50MTW0Kil_mGlLjdlFWKISt5Y0O-GYB46LNk1UQdqw90ub57SkQa98W5haM7R-5lWzkP4=w1052-h592) | PNG, 1052×591, 133,590 bytes | Advertised URL height was 592; actual decode is authoritative. |

Observed SHA-256 values, for reproducible checks rather than permanent upstream guarantees:

```text
CosmicPulseDynamix.png  705fcc93ccf012ae1dbdda73e637983966cb97f78bc61aa2c30d629263ca9ef1
(XD)Dynamix.png        cf2d108a143532be87218fb442af01061a09382afc193cd469664814515ad833
WHIRLWINDBG response   f4668c22db01b7fe4f4bafe4362c90cc5ac68ddb8f05fc8fced952ce61f57ed6
Apple phone sample    33d4b3dd47aabfc437f60d6a3037d1b12c51ada76dfe050b762b40d0015cc97c
Apple tablet sample   5aea1e99441c3c3d5d5140d9514592195e041f746a33e17a08a5f84da60cb872
```

API-reported metadata only: [(XD)BG-HQ.png](https://static.wikitide.net/dynamixwiki/b/b4/%28XD%29BG-HQ.png) 494×426; [(XD)Parts-HQ.png](https://static.wikitide.net/dynamixwiki/f/fd/%28XD%29Parts-HQ.png) 492×471; [WHIRLWINDBG-HQ.png](https://static.wikitide.net/dynamixwiki/c/c3/WHIRLWINDBG-HQ.png) 480×475. Conversely, [(XD)_D.jpg](https://static.wikitide.net/dynamixwiki/d/d2/%28XD%29_D.jpg) is 2048×1152 but its gallery context identifies **gameplay showing D-shaped notes**. Size ranking must follow semantic classification.

Apple screenshot metadata was found under `data[0].data.shelfMapping.product_media_phone_.items[i].screenshot` and `product_media_pad_`, with template, width, height, crop and variants. Treat this as observed page structure, not a stable published API. The fetched JPEGs used its native dimensions. Store variants must not be described as untouched masters.

Migration samples showed matching source PNG hashes in some cases and JPEGs differing by 18 bytes and SHA-1 in others. Keep exact-byte dedup separate from advisory pixel/perceptual similarity. Do not collapse distinct stored versions automatically.

## 5. Search log and continuation protocol

All query families below were searched on **2026-10-10**; results were checked against exact game identity. Search-engine coverage and social access are incomplete.

| Query family / representative queries | Outcome |
| --- | --- |
| `Dynamix C4Cat official website artwork song jacket`, `Dynamix C4Cat press kit`, official Facebook/Twitter artwork | Stores, blocked/dead official endpoints, institutional press |
| `Dynamix rhythm game artwork wiki illustration original C4Cat`, `Dynamix wiki song jackets images` | English wiki inventories and migration; Japanese index |
| `Dynamix 曲绘 原图`, `"Dynamix" "原画"`, `"Dynamix" "官方" "曲绘"`, `"Dynamix" "曲绘" "图包"` | ariiol creator post, repost/gameplay false positives; no verified comprehensive pack |
| `Dynamix 曲絵 イラスト`, `Dynamix ジャケット pixiv` | Artist/credit leads and Japanese metadata |
| `"Dynamix" "Sand Suna"`, `"MassacrE" "Sand" artstation`, Dynamix HenryTz/meganegirai | Sand Suna project; other name matches need song-specific verification |
| `Dynamix site:pixiv.net/artworks`, Dynamix ArtStation C4Cat, Dynamix DeviantArt | One separately traced ArtStation project; no validated Pixiv/DeviantArt originals yet |
| `"Dynamix" "wiki" "中文"`, `site:wiki.biligame.com "Dynamix"`, interlanguage links to Korean sources | Moegirl/Namu/LibreWiki leads; no dedicated verified BWIKI corpus |
| `"Dynamix" "github" "rhythm"`, `"Dynamix" "曲绘" github`, Dynamix C4Cat artwork archive | Chart/API tools, not a complete image collection |
| `"Dynamix" site:commons.wikimedia.org`, `site:archive.org "Dynamix" "C4Cat"`, Dynamix C4Cat artbook/wallpaper | No relevant verified image archive; many unrelated Dynamix entities |
| `"Dynamix" "C4Cat" "bandcamp"`, Dynamix OST C4Cat | Composer releases; rejected submissions and unrelated performers need exclusion |
| Live `siteinfo`, `allimages`, `imageinfo`, `parse`; bounded image retrieval | Wiki endpoints, sample hashes/dimensions/MIME, creator-template evidence |

During implementation, append an exact URL/query, time, evidence level, result and next action for every new source. Build the original-game song/credit index before expanding artist searches. For remaining gaps search exact song + credited illustrator + `original`/`原图`/`原畫`/`原画`/`曲絵`/`ジャケット`; follow credited profiles and collaboration origins. Supplement Korean-title/credit searches where source evidence provides those names. Record no-result and blocked outcomes honestly.

Start per-host acquisition conservatively: one request at a time, at least one second between requests, cached metadata, bounded retry/backoff and `Retry-After`. These are project defaults, **not discovered service limits**. Do not bypass 403/login/challenges, extract private credentials, or scan private game services. A source can remain represented by provenance and an access limitation even when the original file cannot be collected.

No automated refresh should convert a transient error into “source removed” or shrink the coverage denominator. Compare only completed inventories; keep previous successfully archived versions. See [data contracts](07-data-contracts.md) for the accounting and mismatch rules.
