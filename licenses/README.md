# Licenses and rights index

**English** · [简体中文](README.zh-CN.md)

The repository mixes code that this project may license with media that it may not. This index maps each part to its license or rights entry point. The root [LICENSE](../LICENSE) and [NOTICE](../NOTICE) are the standard entry points for the whole repository; this directory holds the third-party license texts.

| Scope | Files | License or rights |
| --- | --- | --- |
| Project code | Page code (`archive.html`, `src/`, `templates/`), scripts, tests, workflows and documentation | Apache License 2.0, see [LICENSE](../LICENSE). |
| Code adapted from the DEEMO artwork archive | The files listed in [NOTICE](../NOTICE), which say so in a notice at their top | Apache License 2.0. Derived from [FridrichMethod/deemo](https://github.com/FridrichMethod/deemo) at commit `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`, which is itself derived from Mashiro's [mashirozx/deemo](https://github.com/mashirozx/deemo); attribution in [NOTICE](../NOTICE). |
| Third-party JavaScript for the slideshow screenshot | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js), byte-identical to the vendor bundle of the DEEMO archive (originally the upstream `src/main.js`) | MIT License for each bundled component: html2canvas 0.5.0-beta3 (Copyright (c) 2012 Niklas von Hertzen, [html2canvas-MIT.txt](html2canvas-MIT.txt)), the punycode.js 1.2.4 module built into html2canvas (Copyright Mathias Bynens, [punycode-MIT.txt](punycode-MIT.txt)) and Pace 1.0.0 (Copyright (c) 2013 HubSpot, Inc., [pace-MIT.txt](pace-MIT.txt)). The bundle carries only an html2canvas banner with a blank license name and a bare `/*! pace 1.0.0 */` line, so the full texts are kept here. |
| Site icons | [assets/site/](../assets/site/), described in [assets/site/README.md](../assets/site/README.md) | Made for this project (plain geometry and line drawings, no game artwork or third-party icon set) and covered by the Apache License 2.0 together with the page code. |
| Fonts, audio, background images | None | The pages use system fonts and CSS backgrounds; no font, audio or background image is distributed. |
| Archived artwork, screenshots and documents | `assets/public/` | **Not covered by the software license.** Dynamix artwork, game assets and game screenshots belong to C4Cat Entertainment Limited and/or the credited artists; press documents belong to their publishers. Each record's `rights` object (holder, creator, rights URL, license, restrictions) and its provenance are in the family manifests `data/sources/*.json` and in `data/catalog.json`. `rights.license` is empty unless a license has actually been established; no license is inferred from a file being public. |
| Derived previews | `assets/thumbs/` | Reduced display copies of archived files; the rights of the original apply. Not covered by the software license. |
| Generated metadata | `data/catalog.json`, `data/catalog.js`, `data/coverage.json`, `data/thumbs.json`, `data/sources/`, `data/discovery/` | Compiled by this project's scripts. They quote titles, credits and other strings from the sources verbatim; quoting them does not change who holds rights in the quoted material. |

Moving or archiving a file in this repository does not grant it a new license. Corrections to attribution or rights information are welcome as an issue in the repository.
