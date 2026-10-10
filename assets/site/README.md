# Site presentation assets

**English** · 简体中文见下文

Every file in `assets/site/` was made for this project. None is copied from the DEEMO reference gallery, from
Dynamix or C4Cat, or from an icon set; no game artwork, logo, font, background image or audio is included. The pages
use system font stacks and CSS-drawn backgrounds, so nothing else is needed for presentation.

| File | Used by | Origin |
| --- | --- | --- |
| `icons/favicon.svg` | Favicon (both pages), web app manifest | Project-made plain geometry: a dark rounded square, three cyan lanes and a white diamond, written by hand as SVG shapes. |
| `icons/favicon.ico` (16, 32, 48 px), `icons/favicon-16x16.png`, `icons/favicon-32x32.png`, `icons/apple-touch-icon.png` (180 px), `icons/android-chrome-192x192.png`, `icons/android-chrome-512x512.png`, `icons/mstile-150x150.png` | Favicons, touch icon, `og:image`, manifest, Windows tile (`browserconfig.xml`) | The same geometry rasterized by `python -I tests/test_layout_icons.py --write` (Pillow); that test also checks the committed files against the geometry. |
| `icons/camera.svg` | Slideshow screenshot button | Project-made line drawing (camera body and lens). |
| `icons/repository.svg` | Slideshow link to the GitHub repository | Project-made line drawing (code brackets); deliberately not a GitHub logo. |
| `icons/hourglass.svg` | Slideshow shuffle-autoplay button | Project-made line drawing (hourglass). |
| `icons/save.svg` | Slideshow screenshot save button | Project-made line drawing (download arrow into a tray). |

These icons are covered by the repository's software license together with the page code. Third-party JavaScript
bundled for the screenshot feature (`src/vendor/legacy-ui.js`) has its own MIT notices in `licenses/`.

## 简体中文

`assets/site/` 中的每个文件都是本项目自行制作的，没有从 DEEMO 参考图库、Dynamix 或 C4Cat、或任何图标包中复制；
不包含任何游戏美术、标志、字体、背景图片或音频。页面使用系统字体和 CSS 绘制的背景。

| 文件 | 用途 | 来源 |
| --- | --- | --- |
| `icons/favicon.svg` | 两个页面的网站图标、Web 应用清单 | 本项目手写的简单几何图形：深色圆角方块、三条青色轨道和一个白色菱形。 |
| `icons/favicon.ico`（16、32、48 px）、`icons/favicon-16x16.png`、`icons/favicon-32x32.png`、`icons/apple-touch-icon.png`（180 px）、`icons/android-chrome-192x192.png`、`icons/android-chrome-512x512.png`、`icons/mstile-150x150.png` | 网站图标、触摸图标、`og:image`、清单、Windows 磁贴（`browserconfig.xml`） | 由 `python -I tests/test_layout_icons.py --write`（Pillow）按同一几何图形栅格化；该测试也会核对已提交文件与几何图形是否一致。 |
| `icons/camera.svg` | 幻灯片截图按钮 | 本项目绘制的线条图标（相机机身与镜头）。 |
| `icons/repository.svg` | 幻灯片中指向 GitHub 仓库的链接 | 本项目绘制的线条图标（代码括号），刻意不使用 GitHub 标志。 |
| `icons/hourglass.svg` | 幻灯片随机自动播放按钮 | 本项目绘制的线条图标（沙漏）。 |
| `icons/save.svg` | 幻灯片截图保存按钮 | 本项目绘制的线条图标（下载箭头）。 |

这些图标与页面代码一起适用本仓库的软件许可证。截图功能捆绑的第三方 JavaScript（`src/vendor/legacy-ui.js`）
的 MIT 许可声明见 `licenses/`。
