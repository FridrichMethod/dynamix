/* Shared language switch for the static pages: English by default, Simplified Chinese on request.
   The choice is kept in localStorage and mirrored in ?lang= so file:// browsing keeps it as well.
   Adapted from FridrichMethod/deemo src/i18n.js (Apache-2.0, commit b14bf0d5f5a05e5382c319f3fd5019a4936820b8);
   modified for the Dynamix archive, 2026. See NOTICE. */
"use strict";
(() => {
  const DEFAULT = "en", ALTERNATE = "zh-CN", STORAGE_KEY = "dynamix-lang";
  const messages = {[DEFAULT]: {}, [ALTERNATE]: {}};
  const listeners = [];
  let ready = false;
  function normalize(value) {
    const tag = String(value ?? "").trim().toLowerCase();
    if (tag === "en" || tag.startsWith("en-")) return DEFAULT;
    if (tag === "zh" || tag.startsWith("zh-")) return ALTERNATE;
    return null;
  }
  function storedLang() {
    try { return normalize(localStorage.getItem(STORAGE_KEY)); } catch { return null; }
  }
  // An explicit ?lang= also becomes the stored choice: links and URL rewrites drop the parameter for English, so a
  // stale stored zh-CN would otherwise win again on reload or on the other page.
  const requested = normalize(new URLSearchParams(location.search).get("lang"));
  if (requested) try { localStorage.setItem(STORAGE_KEY, requested); } catch { /* ?lang= still carries the choice */ }
  let lang = requested || storedLang() || DEFAULT;
  document.documentElement.lang = lang;
  const has = (key) => Object.hasOwn(messages[lang], key) || Object.hasOwn(messages[DEFAULT], key);
  function t(key, vars) {
    const text = messages[lang][key] ?? messages[DEFAULT][key] ?? key;
    return vars == null ? text : text.replace(/\{(\w+)\}/g, (match, name) => Object.hasOwn(vars, name) ? String(vars[name]) : match);
  }
  // Same-site relative links carry ?lang= for the non-default language; absolute URLs and bare fragments are left alone.
  function localizeHref(href) {
    const match = /^([^?#]*)(\?[^#]*)?(#.*)?$/s.exec(href ?? "");
    if (!match || /^([a-z][a-z\d+.-]*:|\/\/)/i.test(href) || (!match[1] && !match[2] && match[3])) return href;
    const params = new URLSearchParams(match[2] || "");
    if (lang === DEFAULT) params.delete("lang"); else params.set("lang", lang);
    const query = params.toString();
    return match[1] + (query ? `?${query}` : "") + (match[3] || "");
  }
  // The toggle's label and title when src/i18n/common.js has not registered (or failed to load). The markup is the
  // English page's, which names zh-CN; the other way needs no table. Captured before the first relabelling.
  const staticToggles = new WeakMap();
  function toggleText(button, key) {
    if (has(key)) return t(key);
    if (!staticToggles.has(button)) staticToggles.set(button, {"lang.toggle": button.textContent.trim(), "lang.toggle.title": button.title});
    if (lang === DEFAULT) return staticToggles.get(button)[key];
    return key === "lang.toggle" ? "English" : "Switch to English";
  }
  // Runs again after each register(); keys whose table has not registered yet keep the page's static English text.
  function apply(root = document) {
    document.documentElement.lang = lang;
    for (const node of root.querySelectorAll("[data-i18n]")) if (has(node.dataset.i18n)) node.textContent = t(node.dataset.i18n);
    for (const node of root.querySelectorAll("[data-i18n-attr]")) {
      for (const pair of node.dataset.i18nAttr.split(";")) {
        const [attribute, key] = pair.split(":").map((part) => part.trim());
        if (attribute && key && has(key)) node.setAttribute(attribute, t(key));
      }
    }
    for (const link of root.querySelectorAll("a[data-lang-link]")) link.setAttribute("href", localizeHref(link.getAttribute("href")));
    for (const button of root.querySelectorAll("[data-lang-toggle]")) {
      // The label names the other language in that language, so only its span carries that language's tag;
      // the button inherits the page language, which its title is written in.
      const label = document.createElement("span");
      label.lang = lang === DEFAULT ? ALTERNATE : DEFAULT;
      label.textContent = toggleText(button, "lang.toggle");
      const title = toggleText(button, "lang.toggle.title");
      button.replaceChildren(label);
      button.removeAttribute("lang");
      button.title = title;
    }
  }
  function setLang(value) {
    const next = normalize(value) || DEFAULT;
    if (next === lang) return;
    lang = next;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch { /* ?lang= still carries the choice */ }
    // The query is localized on its own: a URL with only a fragment (archive.html#<id>, index.html#<id>) gains ?lang= too.
    try { history.replaceState(history.state, "", location.pathname + localizeHref(location.search) + location.hash); } catch { /* some file:// contexts reject history updates */ }
    apply();
    for (const listener of listeners) listener(lang);
  }
  document.addEventListener("click", (event) => {
    const toggle = event.target instanceof Element && event.target.closest("[data-lang-toggle]");
    if (!toggle) return;
    event.preventDefault();
    // Go to the language the label names (its lang tag). apply() labels the toggle from the page language as soon as
    // the page is parsed, with or without src/i18n/common.js; before that the static label names zh-CN, so a zh-CN
    // page stays put instead of doing the opposite of what the label says.
    const named = normalize(toggle.querySelector("[lang]")?.getAttribute("lang"));
    setLang(named || (lang === DEFAULT ? ALTERNATE : DEFAULT));
  });
  function markReady() { ready = true; apply(); }
  // Deferred scripts run once the DOM is parsed ("interactive"), before DOMContentLoaded, which waits for every deferred
  // script, including the archive's catalog; each table then applies as soon as it registers.
  if (document.readyState !== "loading") markReady(); else document.addEventListener("DOMContentLoaded", markReady);
  window.DYNAMIX_I18N = Object.freeze({
    defaultLang: DEFAULT,
    languages: [DEFAULT, ALTERNATE],
    get lang() { return lang; },
    register(table) {
      for (const code of Object.keys(messages)) Object.assign(messages[code], table[code]);
      if (ready) apply();
    },
    t, has, apply, setLang, localizeHref,
    // Source names stay as the manifests record them unless a table defines source.<id> for both languages.
    sourceName: (id, fallback) => has(`source.${id}`) ? t(`source.${id}`) : fallback,
    onChange(listener) { listeners.push(listener); },
  });
})();
