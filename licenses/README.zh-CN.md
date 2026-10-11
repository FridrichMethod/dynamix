# 许可与权利索引

[English](README.md) · **简体中文**

本仓库既包含本项目可以授权的代码，也包含本项目无权授权的媒体。本索引把每个部分对应到其许可或权利说明入口。根目录的 [LICENSE](../LICENSE) 和 [NOTICE](../NOTICE) 是整个仓库的标准入口；本目录存放第三方许可文本。

| 范围 | 文件 | 许可或权利 |
| --- | --- | --- |
| 项目代码 | 页面代码（`archive.html`、`src/`、`templates/`）、脚本、测试、工作流和文档 | Apache License 2.0，见 [LICENSE](../LICENSE)。 |
| 改编自 DEEMO 曲绘档案的代码 | [NOTICE](../NOTICE) 中列出的文件，文件开头的说明也写明了这一点 | Apache License 2.0。派生自 [FridrichMethod/deemo](https://github.com/FridrichMethod/deemo) 的提交 `b14bf0d5f5a05e5382c319f3fd5019a4936820b8`，后者又派生自 Mashiro 的 [mashirozx/deemo](https://github.com/mashirozx/deemo)；署名见 [NOTICE](../NOTICE)。 |
| 幻灯片截图功能使用的第三方 JavaScript | [src/vendor/legacy-ui.js](../src/vendor/legacy-ui.js)，与 DEEMO 档案中的第三方 bundle 逐字节相同（最初是上游的 `src/main.js`） | 每个打包组件均为 MIT 许可：html2canvas 0.5.0-beta3（Copyright (c) 2012 Niklas von Hertzen，[html2canvas-MIT.txt](html2canvas-MIT.txt)）、html2canvas 内置的 punycode.js 1.2.4 模块（Copyright Mathias Bynens，[punycode-MIT.txt](punycode-MIT.txt)）和 Pace 1.0.0（Copyright (c) 2013 HubSpot, Inc.，[pace-MIT.txt](pace-MIT.txt)）。bundle 本身只有一行许可证名称为空的 html2canvas 头注释和一行 `/*! pace 1.0.0 */`，因此完整许可文本保存在本目录。 |
| 网站图标 | [assets/site/](../assets/site/)，说明见 [assets/site/README.md](../assets/site/README.md) | 本项目自行制作（简单几何图形和线条图标，不含游戏美术或第三方图标包），与页面代码一起适用 Apache License 2.0。 |
| 字体、音频、背景图片 | 无 | 页面使用系统字体和 CSS 背景；不分发任何字体、音频或背景图片。 |
| 存档的曲绘、截图和文档 | `assets/public/` | **不适用软件许可证。** Dynamix 的曲绘、游戏素材和游戏截图属于 C4Cat Entertainment Limited 和/或署名画师；媒体文档属于其发布者。每条记录的 `rights` 对象（权利人、创作者、权利信息 URL、许可证、限制）及其来源记录位于类别清单 `data/sources/*.json` 和 `data/catalog.json` 中。只有确实确认了许可证时 `rights.license` 才会填写；不会因为文件公开可见就推定许可。 |
| 派生的预览图 | `assets/thumbs/` | 存档文件缩小后的显示用副本；适用原文件的权利。不适用软件许可证。 |
| 生成的元数据 | `data/catalog.json`、`data/catalog.js`、`data/coverage.json`、`data/thumbs.json`、`data/sources/`、`data/discovery/` | 由本项目的脚本汇编。其中按原文引用了来源中的曲名、署名和其他文字；引用不会改变被引用内容的权利归属。 |

在本仓库中移动或存档文件，不会为其授予新的许可。欢迎在仓库中提交 issue 更正署名或权利信息。
