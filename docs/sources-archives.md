# Archive and press sources

**English** · [简体中文](sources-archives.zh-CN.md)

This document describes the `archives` source family: public web archives, historical press material and research leads about them. `scripts/fetch_archives.py` writes the results to `data/sources/archives.json` and one discovery snapshot per source, `data/discovery/<source>.json`. These sources fill gaps that the live sites no longer cover, such as the official website, which no longer resolves, and artist originals that their platform refuses to serve without a login or Referer.

## Sources

| Source ID | Source | How it is read |
| --- | --- | --- |
| `archives:wayback` | [Internet Archive Wayback Machine](https://web.archive.org/) | Explicit queries to the CDX API (`https://web.archive.org/cdx/search/cdx`); captures fetched raw |
| `archives:4gamer` | [4Gamer.net Dynamix screenshot gallery](https://www.4gamer.net/games/306/G030626/screenshot.html) | One read each of the gallery page and of the site's column article about the game; the gallery's files and the column's images |
| `archives:press` | Press articles about Dynamix (Bahamut GNN, QooApp, unwire.hk, T客邦, ifanr, HK01, 4Gamer) | One read per explicit article page; every image in the article body, each decided by a rule |
| `archives:hkstp` | [HKSTP press release of 2016-08-09](https://www.hkstp.org/zh-cn/park-life/news-and-events/news/press-release-20160809) | One page read; its images and appendix PDFs |
| `archives:archive-leads` | Historical and secondary research leads | No request: wikis, partner-game wikis, encyclopedias, press, forums and repositories from the research sweep, each with its own decision |

## Wayback Machine

The archive is never crawled as a whole. Each seed is one CDX query for a known URL or path prefix:

- the image paths of the official website `dynamix.c4-cat.com`, its uploads directory and the same WordPress site under its raw IP address;
- the MyCard page and the download host of the game;
- the HKSTP appendix PDF, whose original URL now returns HTTP 404;
- ArchiveTeam captures of specific pixiv originals (`i.pximg.net/img-original/…`) of credited illustrators, whose live files pixiv refuses to serve to this project (see [sources-artists.md](sources-artists.md)).

CDX pages are followed with `showResumeKey` until no resume key is returned. Paging is bounded: a repeated resume key or the page limit stops a listing with its key kept open, and the snapshot then stays incomplete. Captures are grouped by URL and content digest, and the earliest capture of each distinct payload becomes one candidate. Files are fetched with the `id_` form (`https://web.archive.org/web/<timestamp>id_/<original URL>`), which returns the archived bytes without the replay page's rewriting; the replay page is kept as the record's `page_url`.

`source_reported` holds the original URL, the archived MIME type, the capture time and the CDX digest converted to a hex SHA-1, so `comparisons.sha1_match` shows whether the received bytes are exactly the archived payload. The CDX `length` is the compressed WARC record size, not the file size; it is noted but not compared.

A capture of an artist's pixiv original is labelled `origin_status: artist_post`, because the bytes are the file pixiv served for that post. Its mapping can be `confirmed_primary` only through `primary_evidence` that quotes the original pixiv post, and `fetch_archives.py --verify` fails when no artists snapshot corroborates that evidence for the same file. Captures of the official website are `official`; WordPress-generated thumbnails are labelled `preview`, and a thumbnail is excluded when a larger rendition of the same image is also archived.

Excluded Wayback candidates include logos, store badges, spacers, backgrounds and other decoration of the official pages (`site_ui`), app icons (`game_ui`), collaboration partners' icons (`not_dynamix`) and small icons whose game identity is not established (`out_of_scope_other`). An image whose caption says that its use in Dynamix was still undecided stays pending.

## Press sources

The 4Gamer gallery holds historical screenshots dated 2015-06-18, supplied for press coverage; they are `gameplay_screenshot` copies labelled `repost`, not jacket masters, and belong to the archive tier. `archives:4gamer` also lists the images of the site's column article of 2015-06-17 about the game: its thumbnails, cropped from screenshots whose full-size files are archived, and its banner are excluded.

`archives:press` reads explicit articles that the research found, from Bahamut GNN, QooApp, unwire.hk, T客邦, ifanr, HK01 and the 4Gamer column. Every image in the body of a read article, and its `og:image`, becomes a candidate keyed by the uploaded file, and a rule decides each one: gameplay screenshots, promotional visuals, song art and reference graphics are archived with the kind the rule gives; Dynamix Universe material, images of other games, interface elements, QR codes and photos of people are excluded with their reasons; an image without a rule stays pending, and an article that cannot be read is recorded as a failure. Press copies are labelled `repost`, and the songs they show are mapped as `inferred` or not at all, because a press article is neither the artist's nor an official post. Lower-resolution syndicated copies of these articles are recorded in `archives:archive-leads` instead of being archived.

Records of these sources are titled from the page they cite: the article headline, or the gallery page's title when its heading is only the game name, with the image caption and its position. The file name stays in `original_filename`.

The HKSTP press release names C4Cat's Dynamix. Its appendix about C4Cat is a PDF (`reference`, archive tier); the direct download returned HTTP 404 and is recorded as a failure, and the same PDF is archived from its Wayback capture. Appendices about other companies are excluded as `not_dynamix`, and the launch-event group photo as `person_photo`.

## Research leads

`archives:archive-leads` records leads that have no acquirable file or that belong to another family: community wiki pages that the wikis family already reads, partner-game wikis whose jackets are the partner's files (credit evidence only), encyclopedia and metadata pages, chart tools and game-API research notes (private game services are not used), low-confidence reposts, and pages that answered with a bot challenge or HTTP 403 (recorded as blocked, not bypassed). A third-party multi-gigabyte image dump of the Fandom wiki is recorded as a pending lead and was not fetched: an unexplained bulk archive is not a substitute for a traceable source.

## Evidence levels and access limitations

The Wayback CDX API is `live_api`; the press pages and articles are `page_read`; the leads registry is `search_index`. The Wayback Machine holds only what was captured: a missing capture is not evidence that a file never existed, and a capture is only as complete as the crawl that made it. Each CDX listing records whether it was followed to its end.

## Counts

The numbers between the markers below are generated by `scripts/update_docs_stats.py` from `data/coverage.json` (`sources.records` of the `archives` family) and `data/catalog.json` after each build; they are not written by hand.

<!-- STATS:BEGIN -->
<!-- STATS:END -->

## Rights

An archived copy does not change ownership. The rights holder defaults to "C4Cat Entertainment Limited and/or the credited artist". A record names another holder when its evidence does (the HKSTP appendix names Hong Kong Science and Technology Parks Corporation; an artist's caption can name a partner game's publisher) and leaves the holder empty when none is established. No license is established for any of these files, so `rights.license` stays empty; being captured by a web archive does not license a work.

## Commands

```sh
.venv/bin/python -I scripts/fetch_archives.py --metadata-only     # discover and record candidates; no downloads
.venv/bin/python -I scripts/fetch_archives.py --resume            # acquire; re-hash archived files instead of downloading them again
.venv/bin/python -I scripts/fetch_archives.py --resume --source archives:wayback
.venv/bin/python -I scripts/fetch_archives.py --verify            # offline
```

The options, locking, atomic writes, error handling and exit status are the same as for `fetch_artists.py` (see [sources-artists.md](sources-artists.md#commands)). The lock defaults to the family's `assets/public/archives/` directory.
