# 032 — Markdown Rendering Improvements

## Goal

Improve the assistant message rendering pipeline to deliver a better reading experience: add copy-to-clipboard on code blocks, language badges, XSS sanitization, and upgrade the underlying markdown library. All changes are purely frontend — no backend or model changes required.

---

## Background

### Current rendering pipeline

```
message.content
  → {{ message.content|json_script:message.id }}   (XSS-safe storage in <script> tag)
  → renderMarkdown() JS function                    (reads json_script, calls marked.parse())
  → el.innerHTML = marked.parse(src)                (direct innerHTML injection — no sanitization)
  → hljs.highlightElement(block)                    (called after parse, auto-detects language)
```

### Libraries in use

| Library | Version | Status |
|---------|---------|--------|
| `marked` | 4.3.0 (Apr 2023) | ⚠️ 2+ years old; v15 released |
| `highlight.js` | 11.9.0 | ✅ Still current in 11.x series |
| `DOMPurify` | not loaded | ❌ Missing |

### Identified problems

| # | Problem | Impact |
|---|---------|--------|
| 1 | No copy button on code blocks | High UX friction — users must manually select text |
| 2 | No language badge on code blocks | Can't identify language at a glance |
| 3 | No DOMPurify sanitization | XSS risk: if tool output (e.g. MCP result) is reflected in a message, `marked.parse()` output injected as `innerHTML` is unguarded |
| 4 | `hljs.highlightElement()` post-parse | highlight.js runs after marked; does not receive the language hint from the fenced code block — relies entirely on auto-detection, which fails on short snippets |
| 5 | `marked@4.3.0` quirks | Nested list rendering bugs, GFM table alignment issues, deprecated renderer API patterns |
| 6 | `data-rendered='1'` guard is fragile | HTMX partial swaps that replace a subtree containing a rendered element do not re-trigger — content silently stays unrendered if the parent is swapped in |

---

## Proposed Solution

Upgrade `marked` to v15, add `DOMPurify`, and write a custom `code` renderer that:
- passes the fenced language directly to `hljs.highlight()` (bypassing auto-detection)
- wraps every code block in a `<div class="code-block-wrapper">` containing a header bar with a language badge and copy button

No backend changes. All changes are in `chat/templates/chat/base_chat.html` and `chat/templates/chat/conversation.html`.

---

## Detailed Design

### 1. CDN library updates (`base_chat.html` `<head>`)

Replace:
```html
<script src="https://cdn.jsdelivr.net/npm/marked@4.3.0/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github-dark.min.css">
<script src="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/highlight.min.js"></script>
```

With:
```html
<script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github-dark.min.css">
<script src="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/highlight.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3/dist/purify.min.js"></script>
```

`highlight.js@11.9.0` stays — it is current and stable. Only `marked` is upgraded.

---

### 2. New CSS — code block wrapper (`base_chat.html` `<style>`)

Add after existing `.md-content pre code` rule. The existing `.md-content pre` rule is adjusted so `border`, `border-radius`, and `margin` move to the wrapper:

```css
/* Code block chrome */
.code-block-wrapper {
  position: relative;
  margin: 0.75rem 0;
  border-radius: 0.6rem;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.code-block-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background-color: #2a2b2e;
  padding: 0.3rem 0.75rem 0.3rem 1rem;
  user-select: none;
}

.code-lang-badge {
  font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
  font-size: 0.7rem;
  color: #6b7280;
  letter-spacing: 0.03em;
}

.copy-btn {
  font-size: 0.7rem;
  color: #9ca3af;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0.15rem 0.5rem;
  border-radius: 0.25rem;
  transition: color 0.15s, background-color 0.15s;
  line-height: 1.4;
}
.copy-btn:hover { color: #f3f4f6; background-color: rgba(255, 255, 255, 0.07); }
.copy-btn.copied { color: #4ade80; }

/* Remove double-border from pre inside wrapper */
.md-content .code-block-wrapper pre {
  margin: 0;
  border: none;
  border-radius: 0;
}
```

---

### 3. Custom marked renderer + DOMPurify (`conversation.html` inline `<script>`)

Replace the current renderer setup block:

```js
// OLD
var renderer = new marked.Renderer();
renderer.image = function(href, title, text) {
  var titleAttr = title ? ' title="' + title + '"' : '';
  return '<img src="' + (href || '') + '" alt="' + (text || '') + '"' + titleAttr + '>';
};
marked.setOptions({ renderer: renderer, breaks: true, gfm: true });
```

With:

```js
// NEW
(function () {
  var renderer = new marked.Renderer();

  // ── Image: keep existing behaviour ──
  renderer.image = function (href, title, text) {
    var titleAttr = title ? ' title="' + title + '"' : '';
    return '<img src="' + (href || '') + '" alt="' + (text || '') + '"' + titleAttr + '>';
  };

  // ── Code block: highlight + wrapper with header ──
  renderer.code = function (code, lang) {
    var validLang = lang && hljs.getLanguage(lang) ? lang : null;
    var highlighted = validLang
      ? hljs.highlight(code, { language: validLang }).value
      : hljs.highlightAuto(code).value;
    var displayLang = validLang || 'code';
    return (
      '<div class="code-block-wrapper">' +
        '<div class="code-block-header">' +
          '<span class="code-lang-badge">' + displayLang + '</span>' +
          '<button class="copy-btn" data-copy>Copy</button>' +
        '</div>' +
        '<pre><code class="hljs' + (validLang ? ' language-' + validLang : '') + '">' +
          highlighted +
        '</code></pre>' +
      '</div>'
    );
  };

  marked.use({ renderer: renderer, breaks: true, gfm: true });
})();
```

**Notes on `marked@15` API change**: `marked.setOptions()` is deprecated in v15; use `marked.use({ ...options })` instead. The renderer is passed the same way. The `renderer.code` signature in v15 receives `(token)` object when using the new extension API, but the classic `Renderer` subclass form still receives `(code, lang, escaped)` — use that form for backward-compat with the existing code style.

---

### 4. `renderMarkdown()` — add DOMPurify (`conversation.html`)

```js
function renderMarkdown() {
  document.querySelectorAll('[data-markdown]:not([data-rendered])').forEach(function (el) {
    var srcId = el.dataset.srcId;
    var src;
    if (srcId) {
      var scriptEl = document.getElementById(srcId);
      src = scriptEl ? JSON.parse(scriptEl.textContent) : '';
    } else {
      src = el.textContent;
    }
    if (!src) return;

    var raw = marked.parse(src);
    el.innerHTML = DOMPurify.sanitize(raw, {
      ADD_TAGS: ['pre', 'code'],
      ADD_ATTR: ['class', 'data-copy'],
    });
    el.dataset.rendered = '1';

    // No explicit hljs.highlightElement() call — highlighting is done
    // inside renderer.code() above, before sanitization.
  });
}
```

Remove the old `el.querySelectorAll('pre code').forEach(hljs.highlightElement)` loop — it is no longer needed because highlighting happens at render time inside the custom `renderer.code`.

---

### 5. Copy button click handler

Add a **delegated event listener** on `document` (not per-button), so it works for all present and future code blocks including those injected by HTMX:

```js
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.copy-btn[data-copy]');
  if (!btn) return;
  var code = btn.closest('.code-block-wrapper');
  if (!code) return;
  var text = code.querySelector('code').innerText;
  navigator.clipboard.writeText(text).then(function () {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(function () {
      btn.textContent = 'Copy';
      btn.classList.remove('copied');
    }, 2000);
  }).catch(function () {
    // Clipboard API unavailable (e.g. non-HTTPS in dev)
    btn.textContent = 'Failed';
    setTimeout(function () { btn.textContent = 'Copy'; }, 2000);
  });
});
```

---

### 6. HTMX re-render robustness

The existing `document.addEventListener('htmx:afterSwap', renderMarkdown)` handler already covers most cases. However, if the `json_script` tag itself is replaced by a swap before `data-rendered` is removed, the element could be left blank.

Add a guard: if the `json_script` source element is missing (removed by HTMX), skip silently rather than injecting empty content:

```js
if (!src) return;   // already added above — this covers the null-scriptEl case
```

No further change needed.

---

## Spec 030 Streaming Compatibility

Spec 030 adds a `_streaming_round` synthetic entry. Streaming reasoning text is displayed in `_tool_progress.html` (not via `renderMarkdown()`), so the streaming path is unaffected by this spec.

If Spec 030 later adds streaming to the final assistant message content, the streaming chunk should be injected into the `json_script` tag and `data-rendered` cleared to force a re-render. That is a Spec 030 concern — not in scope here.

---

## Out of Scope

- **Line numbers** in code blocks — useful but adds complexity (column alignment with hljs). Deferred.
- **Expand / collapse** for long code blocks — deferred.
- **Inline diff rendering** — for `diff` language blocks, `hljs.highlight` already applies diff colors. No extra work needed.
- **MathJax / KaTeX** — LaTeX rendering not required for current agent use cases.
- **`_tool_progress.html` trace reasoning text** — trace reasoning is plain text, not markdown. Not affected.
- **User message bubbles** — rendered with `white-space: pre-wrap` (no markdown). Not changed.
- **Telegram / other interfaces** — markdown rendering there is handled by the Telegram Bot API itself. No change.

---

## Security Notes

- `DOMPurify.sanitize()` runs after `marked.parse()`. The `ADD_TAGS` and `ADD_ATTR` allowlist preserves `<pre>`, `<code>` and `class` / `data-copy` attributes that the renderer emits, while stripping any event handlers or `javascript:` hrefs.
- The copy button uses `data-copy` attribute rather than an inline `onclick=""` — inline event handlers would be stripped by DOMPurify. The delegated listener on `document` handles clicks.
- `navigator.clipboard.writeText()` requires a secure context (HTTPS or localhost). The `.catch()` branch displays "Failed" silently without breaking anything.

---

## Files Changed

| File | Change |
|------|--------|
| `chat/templates/chat/base_chat.html` | Upgrade `marked` CDN tag; add `DOMPurify` CDN tag; add code-block CSS |
| `chat/templates/chat/conversation.html` | Replace renderer setup; update `renderMarkdown()`; add copy delegated listener |

No Python files changed. No migrations. No new dependencies in `pyproject.toml`.

---

## Test Plan

See `.testreport/032-markdown-rendering-improvements.md` after implementation.

| # | Test | Type | Description |
|---|------|------|-------------|
| 1 | `test_code_block_has_wrapper_div` | Unit (JS / template render) | Fenced code block in message produces `.code-block-wrapper` in DOM |
| 2 | `test_code_block_has_language_badge` | Unit | ` ```python ` block shows `python` in `.code-lang-badge` |
| 3 | `test_code_block_unknown_lang_shows_code` | Unit | Unrecognised lang (e.g. ` ```foobar `) falls back to `code` badge |
| 4 | `test_copy_button_present` | Unit | `.copy-btn` is rendered inside every code block |
| 5 | `test_copy_button_click_changes_text` | E2E (Playwright) | Click copy button → button text changes to "Copied!" |
| 6 | `test_copy_button_resets_after_2s` | E2E | After 2 s, copy button returns to "Copy" |
| 7 | `test_xss_script_tag_stripped` | Unit | Message content with `<script>alert(1)</script>` → stripped from rendered HTML |
| 8 | `test_xss_onerror_attr_stripped` | Unit | `<img onerror="alert(1)">` in content → `onerror` attribute removed |
| 9 | `test_inline_code_renders` | Unit | `` `foo` `` renders as `<code>foo</code>` with `.md-content code` styling |
| 10 | `test_table_renders` | Unit | GFM table in content renders `<table>` with correct column count |
| 11 | `test_htmx_swap_triggers_rerender` | E2E | HTMX swap inserting new assistant message → message is rendered (not blank) |
| 12 | `test_no_double_render` | Unit | `renderMarkdown()` called twice → `data-rendered` guard prevents double-parse |
