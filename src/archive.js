/* FridrichMethod's local provenance catalog for original Dynamix artwork. No network fetches are needed to browse.
   Every UI string comes from src/i18n/{common,archive}.js through DYNAMIX_I18N; catalog values are shown verbatim and
   only ever as text (textContent / setAttribute), never parsed as HTML.
   Adapted from FridrichMethod/deemo src/archive.js (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8);
   modified for the Dynamix archive, 2026. See NOTICE. */
"use strict";
(() => {
  const catalog = window.DYNAMIX_CATALOG;
  const i18n = window.DYNAMIX_I18N;
  const {t} = i18n;
  const $ = (id) => document.getElementById(id);
  if (!catalog || !Array.isArray(catalog.assets)) {
    const showMissing = () => { $("count").textContent = t("results.missing_catalog"); };
    showMissing();
    i18n.onChange(showMissing);
    return;
  }
  const FAMILIES = ["artists", "official", "wikis", "archives"];
  const COMPARISONS = ["sha1_match", "bytes_match", "dimensions_match", "format_match"];
  // A label for a token when the tables have one; otherwise the recorded token as it is.
  const label = (prefix, token) => i18n.has(`${prefix}.${token}`) ? t(`${prefix}.${token}`) : String(token ?? "");
  const familyName = (family) => label("family", family);
  // Sentences in a row: English puts a space between them, Chinese (after its full stop) none.
  const sentences = (parts) => parts.filter(Boolean).reduce((first, second) => t("text.sentences", {first, second}), "");
  const kindName = (kind) => label("kind", kind);
  const sources = Array.isArray(catalog.sources) ? catalog.sources : [];
  const sourceInfo = new Map(sources.map((source) => [source.id, source]));
  const sourceName = (id, fallback) => i18n.sourceName(id, fallback || sourceInfo.get(id)?.name || id || "");
  const assets = catalog.assets;
  const byId = new Map(assets.map((asset) => [asset.id, asset]));
  // The grid browses the gallery (artwork and screenshots); "References and documents" browses every other file.
  const gallery = assets.filter((asset) => asset.gallery);
  const references = assets.filter((asset) => !asset.gallery);
  const records = (asset) => Array.isArray(asset.provenance) && asset.provenance.length ? asset.provenance : [asset];
  const longEdge = (asset) => Math.max(Number(asset.width) || 0, Number(asset.height) || 0);
  const isRaster = (asset) => asset.format !== "PDF" && Number(asset.width) > 0 && Number(asset.height) > 0;
  // Small files are labelled as such; screenshots and references are what they are and carry no size label.
  const LOW_RESOLUTION = 1000;
  const ARTWORK = new Set(["song_art", "pack_cover", "character", "background", "part", "promotional", "process"]);
  const isSmall = (asset) => ARTWORK.has(asset.kind) && isRaster(asset) && longEdge(asset) < LOW_RESOLUTION;
  const fields = ["query", "family", "source", "kind", "minimum", "sort"];
  const STATUSES = ["available", "partially_available", "temporarily_unavailable", "manual_access", "metadata_only", "excluded"];
  let filtered = [], shown = 0, current = -1, imageFailed = false;
  const size = (bytes) => typeof bytes !== "number" ? "" : bytes >= 1048576 ? `${(bytes / 1048576).toFixed(2)} MiB` : `${Math.max(1, Math.round(bytes / 1024))} KiB`;
  const textValue = (value) => typeof value === "string" ? value : value == null ? "" : Array.isArray(value) ? value.map(textValue).join(" ") : JSON.stringify(value);
  const names = (value) => (Array.isArray(value) ? value : value ? [value] : []).map(textValue).filter(Boolean);
  const dimensions = (asset) => isRaster(asset) ? `${asset.width} × ${asset.height}` : "";
  const metaLine = (asset) => [dimensions(asset), asset.format, size(asset.bytes)].filter(Boolean).join(" · ");
  const basename = (path) => String(path || "").split("/").pop();
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  // Maintainer and source notes in the manifests are English prose, shown verbatim; the tag keeps a zh-CN page from
  // voicing them as Chinese.
  function note(tag, text) {
    const node = element(tag, textValue(text));
    node.lang = "en";
    return node;
  }
  // Only absolute http(s) URLs become links; anything else (javascript:, data:, relative junk) is shown as plain text.
  function httpUrl(href) {
    try {
      const url = new URL(String(href ?? ""));
      return ["http:", "https:"].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }
  // Catalog files are linked by site-relative paths (assets/..., URL-encoded); a value with a scheme (javascript:, data:,
  // http:), a leading slash, or any space or control character (which URL parsing would drop, so "java\tscript:" would
  // still run) is never used as a link or an image source: a bad catalog entry cannot run code or leave the site.
  const siteUrl = (value) => typeof value === "string" && value && !/[\u0000-\u0020\u007f]/.test(value)
    && !/^([a-z][a-z\d+.-]*:|[\\/])/i.test(value) ? value : null;
  function sourceLink(text, href) {
    const url = httpUrl(href);
    if (!url) return element("span", text);
    const node = element("a", text);
    node.href = url;
    node.target = "_blank";
    node.rel = "noopener noreferrer";
    return node;
  }
  function paragraph(...children) {
    const node = element("p");
    node.append(...children);
    return node;
  }
  // The index and the query share one fold: NFKC turns full-width and compatibility forms into plain ones,
  // toLowerCase() ignores the browser locale (toLocaleLowerCase() maps I to dotless ı under tr/az), katakana folds to
  // hiragana, and every colon becomes ": " (one space after it, none before). The query is then split on whitespace.
  const fold = (text) => text.normalize("NFKC").toLowerCase()
    .replace(/[\u30a1-\u30f6]/g, (kana) => String.fromCharCode(kana.charCodeAt(0) - 0x60)).replace(/\s*:\s*/g, ": ");
  // Search covers the verbatim source name and its localized display name, so the index is built per language.
  function searchable(asset) {
    return fold([asset.title, asset.original_filename, asset.illustrators, asset.composers, asset.song_titles, asset.collections,
      ...records(asset).flatMap((p) => [p.source_name, sourceName(p.source_id, p.source_name), p.page_url, p.illustrators, p.composers, p.song_titles, p.collections])]
      .map(textValue).join(" "));
  }
  const searchIndexes = new Map();
  function searchText() {
    if (!searchIndexes.has(i18n.lang)) searchIndexes.set(i18n.lang, new Map(assets.map((asset) => [asset.id, searchable(asset)])));
    return searchIndexes.get(i18n.lang);
  }
  const pool = () => $("kind").value === "reference" ? references : gallery;
  function renderCount() {
    $("count").textContent = t("results.count", {count: filtered.length, total: pool().length});
  }
  // The per-source list, grouped by source type; with a source type chosen it lists only that type's sources. Only
  // sources with files in the catalog are offered, so every choice can match something.
  const counts = new Map();
  for (const asset of assets) for (const p of records(asset)) counts.set(p.source_id, (counts.get(p.source_id) || 0) + 1);
  function renderSourceOptions() {
    const select = $("source"), keep = select.value, family = $("family").value;
    const first = select.options[0];
    const groups = [];
    for (const name of FAMILIES) {
      if (family && family !== name) continue;
      const members = sources.filter((source) => source.family === name && counts.get(source.id));
      if (!members.length) continue;
      const group = document.createElement("optgroup");
      group.label = familyName(name);
      for (const source of members) {
        const option = element("option", sourceName(source.id, source.name));
        option.value = source.id;
        group.append(option);
      }
      groups.push(group);
    }
    select.replaceChildren(first, ...groups);
    select.value = [...select.options].some((option) => option.value === keep) ? keep : "";
  }
  function filter() {
    const index = searchText();
    const terms = fold($("query").value).split(/\s+/).filter(Boolean);
    const family = $("family").value, source = $("source").value, kind = $("kind").value;
    const minimum = Number($("minimum").value) || 0;
    filtered = pool().filter((asset) =>
      terms.every((term) => index.get(asset.id).includes(term)) &&
      (!family || records(asset).some((p) => p.family === family)) &&
      (!source || records(asset).some((p) => p.source_id === source)) &&
      (!kind || kind === "reference" || asset.kind === kind) &&
      longEdge(asset) >= minimum);
    // Array sort is stable and the pool is in catalog order (type, then title), so ties keep it. "Source first" puts
    // artists' own posts first, then official channels, wikis and archives. Numeric collation keeps "Vol. 2" before
    // "Vol. 10"; the page language picks the collation.
    if ($("sort").value === "source") filtered.sort((a, b) => FAMILIES.indexOf(a.family) - FAMILIES.indexOf(b.family));
    if ($("sort").value === "title") {
      const collator = new Intl.Collator(i18n.lang, {numeric: true});
      filtered.sort((a, b) => collator.compare(String(a.title), String(b.title)));
    }
    if ($("sort").value === "resolution") filtered.sort((a, b) => (b.width || 0) * (b.height || 0) - (a.width || 0) * (a.height || 0));
    renderCount();
    $("grid").replaceChildren();
    $("empty").dataset.i18n = assets.length ? "results.empty" : "results.empty_catalog";
    $("empty").textContent = t($("empty").dataset.i18n);
    $("empty").hidden = filtered.length > 0;
    shown = 0;
    more();
    writeUrl();
  }
  // Filters live in the query string; the open image lives in the fragment (archive.html#<asset id>).
  function filterQuery() {
    const params = new URLSearchParams();
    for (const field of fields) if ($(field).value && !(field === "sort" && $(field).value === "source") && !(field === "minimum" && $(field).value === "0")) params.set(field, $(field).value);
    if (i18n.lang !== i18n.defaultLang) params.set("lang", i18n.lang);
    const query = params.toString();
    return query ? `?${query}` : "";
  }
  function writeUrl() {
    const hash = $("viewer").open && filtered[current] ? `#${encodeURIComponent(filtered[current].id)}` : "";
    try { history.replaceState(null, "", `${location.pathname}${filterQuery()}${hash}`); } catch { /* file:// remains usable */ }
  }
  // Sets the filters from a query string. Values that match no option are ignored, so each select keeps showing the
  // default it applies.
  function applyQuery(search) {
    $("filters").reset();
    renderSourceOptions();
    const params = new URLSearchParams(search);
    for (const field of fields) {
      const value = params.get(field), control = $(field);
      if (value === null) continue;
      if (!control.options || [...control.options].some((option) => option.value === value)) control.value = value;
      if (field === "family") renderSourceOptions();
    }
  }
  // A card's preview: the derived thumbnail when there is one, else the original when this site publishes it. A failed
  // thumbnail falls back once to a published original; after that (or for an archive-only file) a labelled tile
  // replaces the image, so a broken preview never turns into a stream of requests.
  function placeholder(button, asset) {
    button.querySelector("img")?.remove();
    if (button.querySelector(".card-placeholder")) return;
    const tile = element("span", null, "card-placeholder");
    tile.dataset.i18n = asset.format === "PDF" ? "card.pdf" : "card.no_preview";
    tile.textContent = t(tile.dataset.i18n);
    button.append(tile);
  }
  function cardImage(button, asset) {
    const original = asset.site_original && isRaster(asset) ? siteUrl(asset.url) : null;
    const preview = siteUrl(asset.thumb);
    const first = preview || original;
    if (!first) { placeholder(button, asset); return; }
    const image = element("img");
    image.addEventListener("error", () => {
      if (preview && original && image.getAttribute("src") === preview) image.src = original;
      else placeholder(button, asset);
    });
    image.src = first;
    image.alt = asset.title;
    image.loading = "lazy";
    image.decoding = "async";
    if (isRaster(asset)) {
      image.width = asset.width;
      image.height = asset.height;
    }
    button.append(image);
  }
  // Language-dependent parts of a card; card.children is [image button, title, size line, source line, tag, flags].
  function localizeCard(card, asset) {
    const [button, , , source, tag, flags] = card.children;
    button.setAttribute("aria-label", t("card.view", {title: asset.title}));
    const tile = button.querySelector(".card-placeholder");
    if (tile) tile.textContent = t(tile.dataset.i18n);
    const name = sourceName(asset.source_id, asset.source_name);
    const credit = names(asset.illustrators).join(" / ");
    source.textContent = [credit && !name.startsWith(credit) ? credit : null, name].filter(Boolean).join(" · ");
    const extra = records(asset).length > 1 ? [t("card.records", {count: records(asset).length})] : [];
    tag.textContent = [`${familyName(asset.family)} — ${kindName(asset.kind)}`, ...extra].join(" · ");
    const marks = [];
    if (!asset.site_original) marks.push(t("card.archive_only"));
    if (isSmall(asset)) marks.push(t("card.low_resolution", {size: longEdge(asset)}));
    flags.textContent = marks.join(" · ");
    flags.hidden = !marks.length;
  }
  function more() {
    const start = shown, button = $("more"), focused = document.activeElement === button;
    const stop = Math.min(shown + 60, filtered.length);
    const fragment = document.createDocumentFragment();
    for (let index = shown; index < stop; index++) {
      const asset = filtered[index];
      const card = element("article", null, "card");
      card.dataset.id = asset.id;
      const button = element("button", null, "card-image");
      button.type = "button";
      cardImage(button, asset);
      button.addEventListener("click", () => open(index));
      card.append(button, element("h2", asset.title), element("p", metaLine(asset)), element("p"), element("p", null, "tag"), element("p", null, "flags"));
      localizeCard(card, asset);
      fragment.append(card);
    }
    $("grid").append(fragment);
    shown = stop;
    button.hidden = shown >= filtered.length;
    // The last "Load more" hides itself; keyboard focus moves on to the first card it added instead of falling to <body>.
    if (focused && button.hidden && shown > start) $("grid").children[start].querySelector(".card-image").focus();
  }
  // Shows a catalog entry in the viewer, clearing the filters first (as the reset button does) when they hide it and
  // switching to the references view for a file outside the gallery; focus moves to the new title, since the control
  // that asked for it is replaced with the rest of the viewer's text.
  function show(asset, focusTitle = true) {
    if (!filtered.includes(asset)) {
      $("filters").reset();
      if (!asset.gallery) $("kind").value = "reference";
      renderSourceOptions();
      filter();
    }
    const index = filtered.indexOf(asset);
    if (index < 0) return;
    open(index);
    if (focusTitle) {
      $("viewer-title").tabIndex = -1;
      $("viewer-title").focus();
    }
  }
  function creditRow(list, key, values) {
    const shownNames = names(values);
    list.append(element("dt", t(key)));
    list.append(shownNames.length ? element("dd", shownNames.join(" / ")) : element("dd", t("credit.unknown"), "unknown"));
  }
  function renderCredits(asset) {
    const list = $("viewer-credits");
    list.replaceChildren();
    creditRow(list, "credit.illustrator", asset.illustrators);
    creditRow(list, "credit.composer", asset.composers);
    if (names(asset.song_titles).length) {
      list.append(element("dt", t("viewer.songs")), element("dd", names(asset.song_titles).join(" / ")));
    }
    if (names(asset.collections).length) {
      list.append(element("dt", t("viewer.collections")), element("dd", names(asset.collections).join(" / ")));
    }
    list.append(element("dt", t("viewer.kind")), element("dd", kindName(asset.kind)));
  }
  function renderVariants(asset) {
    const box = $("variants");
    box.replaceChildren();
    const others = (Array.isArray(asset.variants) ? asset.variants : []).filter((id) => id !== asset.id);
    if (asset.variant_role) box.append(paragraph(element("span", t("variants.role", {role: textValue(asset.variant_role)}))));
    if (!others.length) return;
    box.append(element("p", t("variants.title", {count: others.length})));
    for (const id of others) {
      const other = byId.get(id);
      if (!other) { box.append(paragraph(element("span", textValue(id)))); continue; }
      const button = element("button", [other.title, dimensions(other), other.variant_role ? textValue(other.variant_role) : null].filter(Boolean).join(" · "));
      button.type = "button";
      button.addEventListener("click", () => show(other));
      box.append(paragraph(button));
    }
  }
  // An evidence entry is a URL, prose, or an object with url / field / quote; each part is shown as text or a link.
  function evidenceItem(entry) {
    const item = element("li");
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      const parts = [];
      if (entry.role) parts.push(label("credit.role", entry.role));
      if (entry.name) parts.push(textValue(entry.name));
      if (entry.field) parts.push(textValue(entry.field));
      if (parts.length) item.append(element("span", parts.join(" · ")));
      if (entry.quote) item.append(document.createTextNode(parts.length ? " — " : ""), note("q", entry.quote));
      if (entry.url) item.append(document.createTextNode(" "), sourceLink(httpUrl(entry.url) ? t("provenance.evidence_link") : textValue(entry.url), entry.url));
      const known = new Set(["role", "name", "field", "quote", "url"]);
      const rest = Object.keys(entry).filter((key) => !known.has(key));
      if (rest.length) item.append(document.createTextNode(" "), note("span", rest.map((key) => `${key}: ${textValue(entry[key])}`).join("; ")));
      return item;
    }
    const text = textValue(entry);
    item.append(httpUrl(text) ? sourceLink(text, text) : note("span", text));
    return item;
  }
  function evidenceList(entries) {
    const list = element("ul");
    for (const entry of entries) list.append(evidenceItem(entry));
    return list;
  }
  // source_reported (what the source advertised) against the archived file (asset fields measured from its bytes).
  function comparisonLine(asset, p, key) {
    const reported = p.source_reported || {}, value = p.comparisons ? p.comparisons[key] : undefined;
    const field = t(`comparison.${key}`);
    const pairs = {
      sha1_match: [reported.sha1, asset.sha1],
      bytes_match: [reported.bytes, asset.bytes],
      dimensions_match: [reported.width && reported.height ? `${reported.width} × ${reported.height}` : null, dimensions(asset) || null],
      format_match: [reported.mime, asset.mime || asset.format],
    };
    const [said, seen] = pairs[key];
    if (value === true) return element("p", t("comparison.matched", {field}));
    if (value === false) return element("p", t("comparison.mismatched", {field, reported: textValue(said) || "—", observed: textValue(seen) || "—"}), "comparison-false");
    return element("p", t("comparison.unknown", {field}));
  }
  function upstreamNotes(p) {
    const notes = [element("p", t("provenance.upstream", {status: label("upstream", p.upstream_status)}))];
    // superseded_by names a source record's asset ID: a catalog entry's own ID, or the asset_id of one of its records.
    const newer = p.superseded_by && (byId.get(p.superseded_by) || assets.find((asset) => records(asset).some((record) => record.asset_id === p.superseded_by)));
    if (newer) {
      const button = element("button", t("provenance.superseded_by", {title: newer.title, size: dimensions(newer) || newer.format}));
      button.type = "button";
      button.addEventListener("click", () => show(newer));
      notes.push(paragraph(button));
    } else if (p.superseded_by) {
      notes.push(element("p", t("provenance.superseded_missing", {id: textValue(p.superseded_by)})));
    }
    return notes;
  }
  function provenanceBlock(asset, p) {
    const block = element("section", null, "provenance-item");
    const heading = paragraph(sourceLink(sourceName(p.source_id, p.source_name), p.page_url), document.createTextNode(` · ${familyName(p.family)}`));
    block.append(heading);
    const links = [];
    if (httpUrl(p.page_url)) links.push(sourceLink(t("provenance.page"), p.page_url));
    if (httpUrl(p.download_url)) links.push(sourceLink(t("provenance.file_url"), p.download_url));
    if (httpUrl(p.resolved_url) && p.resolved_url !== p.download_url) links.push(sourceLink(t("provenance.resolved_url"), p.resolved_url));
    if (links.length) block.append(paragraph(...links.flatMap((link, index) => index ? [document.createTextNode(" · "), link] : [link])));
    if (!httpUrl(p.download_url) && p.download_url) block.append(note("p", p.download_url));
    if (names(p.illustrators).length) block.append(element("p", t("provenance.illustrators", {names: names(p.illustrators).join(" / ")})));
    if (names(p.composers).length) block.append(element("p", t("provenance.composers", {names: names(p.composers).join(" / ")})));
    if (names(p.song_titles).length) block.append(element("p", t("provenance.song_titles", {titles: names(p.song_titles).join(" / ")})));
    if (names(p.collections).length) block.append(element("p", t("provenance.collections", {names: names(p.collections).join(" / ")})));
    const quality = p.quality || {};
    block.append(element("p", t("provenance.quality", {
      variant: label("quality.source_variant", quality.source_variant ?? "unknown"),
      origin: label("quality.origin_status", quality.origin_status ?? "unknown"),
      master: label("quality.master_status", quality.master_status ?? "unverified"),
    })));
    if (quality.notes) block.append(note("p", quality.notes));
    const rights = p.rights || {};
    block.append(element("p", rights.holder ? t("provenance.rights_holder", {holder: textValue(rights.holder)}) : t("provenance.rights_holder_unknown")));
    if (rights.creator) block.append(element("p", t("provenance.rights_creator", {creator: textValue(rights.creator)})));
    block.append(element("p", rights.license ? t("provenance.license", {license: textValue(rights.license)}) : t("provenance.license_none")));
    if (rights.restrictions) block.append(note("p", rights.restrictions));
    if (httpUrl(rights.url)) block.append(paragraph(sourceLink(t("provenance.rights_url"), rights.url)));
    block.append(element("p", t("provenance.mapping", {status: label("mapping", p.mapping_status ?? "unmapped")})));
    const mapping = Array.isArray(p.mapping_evidence) ? p.mapping_evidence : [];
    if (mapping.length) block.append(evidenceList(mapping));
    const credits = Array.isArray(p.credit_evidence) ? p.credit_evidence : [];
    if (credits.length) block.append(element("p", t("provenance.credit_evidence")), evidenceList(credits));
    for (const key of COMPARISONS) block.append(comparisonLine(asset, p, key));
    if (p.upstream_status && p.upstream_status !== "current") block.append(...upstreamNotes(p));
    if (p.notes) block.append(note("p", p.notes));
    return block;
  }
  function fileInfo(asset) {
    const observed = asset.download_observed || {};
    const lines = [
      [t("file.path"), asset.path], [t("file.original_filename"), asset.original_filename], ["SHA-256", asset.sha256], ["SHA-1", asset.sha1],
      [t("file.mime"), [asset.mime, observed.content_type && observed.content_type !== asset.mime ? t("file.served_as", {type: observed.content_type}) : null].filter(Boolean).join(" · ")],
      [t("file.fetched"), asset.fetched_at], [t("file.revision"), asset.upstream_revision], [t("file.id"), asset.id],
      [t("file.games"), Array.isArray(asset.game_associations) && asset.game_associations.length ? asset.game_associations.join(", ") : null],
    ];
    if (asset.transformed === true) lines.push([t("file.transformed"), t("file.transformed_yes")]);
    return lines.filter(([, value]) => value != null && value !== "").map(([name, value]) => t("file.line", {name, value: textValue(value)})).join("\n");
  }
  // The image area and the note under the title. An image that failed to load is hidden (as a card shows a tile
  // instead), and every message names the link the viewer actually offers for this file.
  function renderImageState(asset) {
    const image = $("full-image"), hasImage = Boolean(image.getAttribute("src"));
    const repository = !asset.site_original && httpUrl(asset.archive_url);
    const link = repository ? t("viewer.repository") : asset.site_original && siteUrl(asset.url) ? t("viewer.original") : null;
    image.hidden = imageFailed;
    $("no-preview").hidden = hasImage;
    $("no-preview").textContent = link ? t("viewer.no_preview", {link}) : t("viewer.no_preview_no_link");
    $("image-error").hidden = !imageFailed;
    $("image-error").textContent = !link ? t("viewer.image_error_no_link") : t(asset.site_original ? "viewer.image_error" : "viewer.preview_error", {link});
    const notes = [];
    if (!asset.site_original) notes.push(t(!repository ? "viewer.archive_only_no_link" : hasImage && !imageFailed ? "viewer.archive_only" : "viewer.archive_only_no_preview"));
    if (isSmall(asset)) notes.push(t("viewer.low_resolution", {size: longEdge(asset)}));
    $("archive-note").textContent = sentences(notes);
    $("archive-note").hidden = !notes.length;
  }
  // Language-dependent parts of the viewer; open() sets the image and the language-neutral fields.
  function localizeViewer(asset) {
    $("slideshow").href = i18n.localizeHref(`index.html#${encodeURIComponent(asset.id)}`);
    const download = $("download");
    download.textContent = t(download.dataset.i18n);
    renderImageState(asset);
    renderCredits(asset);
    renderVariants(asset);
    $("file-info").textContent = fileInfo(asset);
    $("provenance").replaceChildren(...records(asset).map((p) => provenanceBlock(asset, p)));
  }
  // The viewer shows the original only when this site publishes it; an archive-only file shows its preview and links
  // the original in the repository instead, so no link here ever points at a file the site does not serve.
  function open(index) {
    current = (index + filtered.length) % filtered.length;
    const asset = filtered[current];
    const image = $("full-image");
    const local = siteUrl(asset.url);
    const source = asset.site_original && isRaster(asset) ? local : siteUrl(asset.thumb);
    imageFailed = false;
    if (source) image.src = source; else image.removeAttribute("src");
    image.alt = asset.title;
    $("viewer-title").textContent = asset.title;
    $("viewer-meta").textContent = metaLine(asset);
    $("position").textContent = `${current + 1} / ${filtered.length}`;
    const download = $("download");
    const repository = httpUrl(asset.archive_url);
    if (asset.site_original) {
      if (local) download.href = local; else download.removeAttribute("href");
      download.download = basename(asset.path);
      download.removeAttribute("target");
      download.removeAttribute("rel");
      download.dataset.i18n = "viewer.download";
      download.hidden = !local;
    } else {
      download.removeAttribute("download");
      download.dataset.i18n = "viewer.repository";
      download.hidden = !repository;
      if (repository) {
        download.href = repository;
        download.target = "_blank";
        download.rel = "noopener noreferrer";
      } else download.removeAttribute("href");
    }
    $("original").hidden = !(asset.site_original && local);
    if (asset.site_original && local) $("original").href = local; else $("original").removeAttribute("href");
    $("slideshow").hidden = !asset.slideshow;
    localizeViewer(asset);
    if (!$("viewer").open) $("viewer").showModal();
    writeUrl();
  }
  $("full-image").addEventListener("error", () => {
    if (!$("full-image").getAttribute("src") || !$("viewer").open || !filtered[current]) return;
    imageFailed = true;
    renderImageState(filtered[current]);
  });
  $("filters").addEventListener("submit", (event) => event.preventDefault());
  for (const field of fields) {
    $(field).addEventListener(field === "query" ? "input" : "change", () => {
      if (field === "family") renderSourceOptions();
      filter();
    });
  }
  $("reset-filters").addEventListener("click", () => { $("filters").reset(); renderSourceOptions(); filter(); });
  $("more").addEventListener("click", more);
  $("close").addEventListener("click", () => $("viewer").close());
  // The browser hands focus back to the card that opened the viewer; after Prev/Next that is the wrong card and may be
  // far away, so focus the card of the image last shown instead (rendering cards up to it) and bring it into view.
  $("viewer").addEventListener("close", () => {
    writeUrl();
    if (!filtered[current]) return;
    while (shown <= current) more();
    const card = $("grid").children[current];
    card.querySelector(".card-image").focus({preventScroll: true});
    card.scrollIntoView({block: "nearest"});
  });
  $("previous").addEventListener("click", () => open(current - 1));
  $("next").addEventListener("click", () => open(current + 1));
  document.addEventListener("keydown", (event) => {
    if (!$("viewer").open || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowLeft") { event.preventDefault(); open(current - 1); }
    if (event.key === "ArrowRight") { event.preventDefault(); open(current + 1); }
  });
  // archive.html#<asset id> opens that file, from a shared link, the inventory's reference list or the Back button.
  function hashAsset(hash) {
    try { return byId.get(decodeURIComponent(hash.slice(1))); } catch { return undefined; }
  }
  function openFromHash(hash) {
    const asset = hashAsset(hash);
    if (asset && !($("viewer").open && filtered[current] === asset)) show(asset, false);
  }
  // Back and Forward (and a link to #<id>) land on a URL this page wrote: its filters are applied when they differ from
  // the page's, and its fragment opens that file or, naming none, closes the viewer, so the page and the URL agree.
  function followLocation() {
    const hash = location.hash, refilter = location.search !== filterQuery();
    if (refilter) current = -1;  // the old position means nothing under other filters; no URL written meanwhile names it
    if (!hashAsset(hash) && $("viewer").open) $("viewer").close();
    if (refilter) {
      applyQuery(location.search);
      filter();  // rewrites the URL without the fragment until the file is open again
    }
    openFromHash(hash);
  }
  window.addEventListener("popstate", followLocation);
  window.addEventListener("hashchange", followLocation);
  // The retrieval statuses the catalog's sources actually have, with counts; an absent status is never mentioned.
  function statusSummary() {
    const tally = new Map();
    for (const source of sources) tally.set(source.status, (tally.get(source.status) || 0) + 1);
    const rank = (status) => STATUSES.includes(status) ? STATUSES.indexOf(status) : STATUSES.length;
    const parts = [...tally].sort(([a], [b]) => rank(a) - rank(b)).map(([status, number]) => t("inventory.status_count", {status: label("status", status), count: number}));
    return parts.length ? t("inventory.statuses", {statuses: parts.join(" · ")}) : "";
  }
  function attemptsText(value) {
    if (typeof value !== "number") return t("inventory.attempts.unknown");
    const form = `inventory.attempts.${new Intl.PluralRules(i18n.lang).select(value)}`;
    return t(i18n.has(form) ? form : "inventory.attempts.other", {count: value});
  }
  // Rebuilt from scratch on every language change, so rows are never duplicated.
  function renderInventory() {
    const summary = catalog.summary || {};
    $("inventory-summary").textContent = sentences([t("inventory.summary", {
      sources: summary.source_count ?? sources.length, records: summary.source_asset_records ?? 0,
      files: summary.unique_files ?? assets.length, gallery: summary.gallery_images ?? gallery.length, failures: summary.failure_count ?? 0,
    }), statusSummary()]);
    // A reference file's source is often named like the file itself; the second link then says what it leads to.
    $("references").replaceChildren(...references.map((asset) => {
      const link = element("a", `${asset.title} (${[asset.format, size(asset.bytes)].filter(Boolean).join(", ")})`);
      link.href = `#${encodeURIComponent(asset.id)}`;
      const p = element("p");
      const source = sourceName(asset.source_id, asset.source_name);
      p.append(link, document.createTextNode(` · ${kindName(asset.kind)}${asset.site_original ? "" : ` · ${t("card.archive_only")}`} · `),
        sourceLink(source === asset.title ? t("inventory.source_page") : source, asset.page_url));
      return p;
    }));
    $("sources").replaceChildren(...sources.map((source) => {
      const row = element("tr"), name = element("td"), status = element("td");
      name.append(sourceLink(sourceName(source.id, source.name), source.url), element("br"), element("span", familyName(source.family), "label"));
      const states = [label("status", source.status)];
      if (source.discovery_complete === false) states.push(t("inventory.incomplete"));
      status.append(element("span", `${states.join(" · ")} · `));
      status.append(note("span", source.notes));
      row.append(name, element("td", String(counts.get(source.id) || 0)), status);
      return row;
    }));
    const failures = Array.isArray(catalog.failures) ? catalog.failures : [];
    const wasOpen = $("failures").querySelector("details")?.open ?? false;
    $("failures").replaceChildren();
    if (failures.length) {
      const details = element("details"), list = element("ul");
      details.open = wasOpen;
      details.append(element("summary", t("inventory.failures", {count: failures.length})));
      for (const failure of failures) {
        const item = element("li");
        const where = sourceName(failure.source_id, null) || t("inventory.failure_source");
        item.append(sourceLink(where, failure.url), document.createTextNode(` · ${t("inventory.failure", {
          phase: label("phase", failure.phase), error: label("error", failure.error_class),
          status: failure.http_status ?? "—", attempts: attemptsText(failure.attempts),
        })}`));
        if (failure.retriable) item.append(document.createTextNode(` · ${t("inventory.retriable")}`));
        if (failure.manual) item.append(document.createTextNode(` · ${t("inventory.manual")}`));
        if (failure.message) item.append(document.createTextNode(" · "), note("span", failure.message));
        list.append(item);
      }
      details.append(list);
      $("failures").append(details);
    }
  }
  // The first filter() rewrites the URL (without a fragment while no image is open), so the requested image is read first.
  const requestedHash = location.hash;
  applyQuery(location.search);
  renderInventory();
  filter();
  openFromHash(requestedHash);
  // Static [data-i18n] text is handled by the runtime; this re-renders the script-built text in place. Filters, the
  // number of cards shown and the open image stay as they are. A search query is matched again, because the index
  // holds localized source names; a title sort is redone in the new language's collation.
  i18n.onChange(() => {
    renderSourceOptions();
    if ($("query").value.trim() || $("sort").value === "title") {
      const previous = shown, openId = $("viewer").open ? filtered[current]?.id : null;
      filter();
      while (shown < Math.min(previous, filtered.length)) more();
      if (openId) {
        const index = filtered.findIndex((asset) => asset.id === openId);
        if (index >= 0) open(index); else { current = -1; $("viewer").close(); }
      }
    } else {
      renderCount();
      const cards = $("grid").children;
      for (let index = 0; index < cards.length; index++) localizeCard(cards[index], filtered[index]);
      if ($("viewer").open && filtered[current]) localizeViewer(filtered[current]);
    }
    renderInventory();
  });
})();
