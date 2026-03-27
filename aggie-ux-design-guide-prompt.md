# Aggie UX Design Guide — Generation Prompt

## Overview

This document is a **repeatable, agentic workflow prompt**. When given to an AI agent
(such as Antigravity or a similar coding assistant), it will produce a complete, dated
Aggie UX Design System Guide in Markdown in a **multi-file directory format**. The resulting guide is primarily intended as a structured input for AI coding agents building TAMU websites, with secondary use
by human developers.

The process has **two phases**:

- **Phase 1 (Extraction):** `generate_design_guide.py` scrapes the Aggie UX Storybook
  site using Playwright and saves structured JSON to `raw_data/`.
- **Phase 2 (Agentic Synthesis):** The same script acts as an orchestrator, using the `gemini` CLI to synthesize clean markdown in small, isolated batches. It outputs an `index.md` and individual component files in `design_guides/vYYYY-MM-DD/`.

---

## Instructions for the Agent

You are an expert Design Systems Engineer and Technical Documentarian. Your goal is to
run the generation script and handle any errors. 

### Locate Your Working Directory

This prompt file (`aggie-ux-design-guide-prompt.md`) is always stored alongside
`generate_design_guide.py` in the same directory. Determine the absolute path first:

```bash
DIR="$(cd "$(dirname "aggie-ux-design-guide-prompt.md")" && pwd)"
echo "Working directory: $DIR"
```

Verify that `generate_design_guide.py` exists before proceeding:

```bash
test -f "$DIR/generate_design_guide.py" && echo "✓ Script found" || echo "✗ ERROR: generate_design_guide.py not found"
```

Verify that the `gemini` CLI is installed and accessible by the script:

```bash
gemini --version || echo "✗ ERROR: Gemini CLI not installed or not in PATH. Please install it first."
```

---

## Run the Guide Generator

### Step 1 — Install Dependencies (if needed)

```bash
pip3 show playwright requests > /dev/null 2>&1 || pip3 install playwright requests
playwright install chromium --quiet 2>/dev/null || true
```

### Step 2 — Run the Combined Script

Run the full pipeline (extract + synthesize) in one command:

```bash
cd "$DIR"
python3 generate_design_guide.py
```

This will:
1. Fetch `index.json` from https://aggieux.tamu.edu
2. Scrape all docs and story iframes using a headless Chromium browser
3. Save structured JSON to `$DIR/raw_data/` 
4. Pass batches of JSON to the `gemini` CLI and write markdown files.
5. Create `$DIR/design_guides/vYYYY-MM-DD/` containing `index.md` and `components/`.

> **Resumability:** If the script crashes during Phase 2 (Gemini rate limits, etc.), you can safely re-run it. It reads `progress.json` and skips already-synthesized components!

### Common Flags

| Flag | Purpose |
|---|---|
| `--extract-only` | Run Phase 1 only (save raw JSON, do not synthesize) |
| `--synthesize-only` | Run Phase 2 only (requires existing `raw_data/` JSON files) |
| `--section components` | Extract and synthesize one section only |
| `--local` | Use local mirror at `$DIR/aggieux.tamu.edu/` instead of live site |
| `--date 2026-01-01` | Override the date in output folder names |

---

## Output

The script produces a directory:

```
design_guides/vYYYY-MM-DD/
├── index.md             <-- The Manifest: Branding, tokens, and CDN links
└── components/          <-- Hierarchical directories for each component matching Storybook
    └── accordion-group/
        └── accordion/
            ├── accordion.md
            └── example-accordion.png

raw_data/
├── progress_{date_str}.json <-- Internal resume state tracking
```

Each component entry follows this template:

```markdown
--- FILE: components/[hierarchy]/[component_name].md ---
# [Component Name]
> [One-sentence description]

**Storybook Reference:** <url>

## When to Use
- ...

## When NOT to Use
- ...

## Requirements
- Parent/Child relationships (e.g., must be inside `.card-group`).

## Known Quirks & Best Practices
- **Image Placeholders**: When generating an initial page layout, ALWAYS use plain black boxes (or solid inline styles/placeholders) in place of photos.
- **CTA Feature Backgrounds**: When creating an inline `style` for the `.cta-feature` background, the gradient MUST fade to fully transparent (e.g., `rgba(0,0,0,0)`) on one side so the image isn't blocked out. Example: `style="background-image: linear-gradient(45deg, rgba(80,0,0,0.8), rgba(80,0,0,0) 60%), url('...');"`
- **Card Alignment & Link Classes**: Documentation for Card components MUST include instructions on using `margin-top: auto` on `.link--cta` (or other tail elements) within `.card__content` to ensure buttons/links align perfectly to the bottom of cards in a group. NEVER add manual inline `<svg>` elements inside `.link--cta`, as the framework CSS automatically generates the trailing arrow using a pseudo-element.
- **CTA Link SVG Sizing**: The default framework sizing for CTA SVGs or pseudo-elements (`.link--cta::after`, `.btn--cta svg`) is often visually overpowering. When generating HTML templates, ALWAYS include a globally applied `<style>` block in the `<head>` of the html document to scale them to a more reasonable size like `1rem`:
  ```css
  .link--cta::after {
      width: 1rem !important;
      height: 1rem !important;
      mask-size: 1rem !important;
      -webkit-mask-size: 1rem !important;
  }
  .btn--cta svg {
      width: 1rem !important;
      height: 1rem !important;
  }
  ```
- **Strict Component Class Usage**: Explicitly instruct the AI to always include variant classes (e.g., `--elegant`, `--gray-100`, `--standard`) to avoid "Default" styling which can sometimes lead to unexpected layouts without them.
- **Heading Group Wrappers**: Mandate the use of `<div class="heading-group">` with the appropriate modifier (`--display`, `--feature`, `--card`) for every component that includes a heading and superhead.
- **Standard Page Header (Sidebar Pages)**: Sibling Structure is Critical: In the Standard Page Header component, the `.page-header__image` must be a direct sibling of `.page-header__container`. It should never be nested inside a row or col grid if you want the official split-screen layout. Explicit Coloring: When using the `.page-header--maroon` background, you must explicitly apply the `.text-white` utility class to all content (superheads, headings, and descriptions) to ensure brand-compliant "On-Dark" visibility.
- **Site Identity & Brand Consistency**: Avoid Title Duplication: The `.site-title` should contain the site’s unique name (e.g., "Tropical Fish Explorer"), while the `.identity` container should strictly house the official Texas A&M Brand Logo (`primaryTAM.png`). Avoid using the `identity__wordmark` if it redundantly repeats the site title. Utility Nav Alignment: The `utility-nav` should be a top-level child of `root-inner` and should use the `.utility-nav__left` container for primary branding text like "Texas A&M University."
- **Sidebar Component Compatibility**: Media Avoidance: Components like `cta-feature` and `media-feature` are high-impact, full-width elements. They should never be used on pages with a sidebar, as they "break" the narrow column intent. Recommendation: Use smaller, modular components like a custom `callout-box` or a simple `heading-group` with text for specialized content (Facts, Tips, etc.) inside a sidebar-restricted column.
- **Hero Component (`hero--main`) Precision**: Content Overlay: For the primary home page hero (`hero--main`), the `.hero__image` is a sibling to the `.hero__container`. Ensure the `hero-button-group` is used for all CTA links to maintain correct button spacing and responsive stacking. Button Sizing: For the Home Page hero, always use the `btn--primary` (maroon) or `btn--white` variant based on the image contrast to meet accessibility standards.
- **Structural Integrity**: Mandatory Root Wrapper: Ensure all pages wrap their main content in `<div id="root"><div id="root-inner">` to guarantee that sticky sidebars and global layout utilities (like `main-header`) function as intended by the `aux.js` library. Emphasize that the `utility-nav` should never be nested inside another `nav` or `header` tag; it must be a top-level child of `root-inner`.
- **Icon Visibility (CORS)**: Every page containing `<svg><use ...></use></svg>` icons MUST include a warning that icons will NOT load when viewing the HTML via `file://` due to browser CORS security. Recommend using a local web server (e.g., `python3 -m http.server`) OR including the **SVG Sprite Injection** script for local development robustness:
  ```javascript
  <script>
      let icons = new XMLHttpRequest();
      icons.open("GET", "https://aux.tamu.edu/icons/aux-sprite.svg", true);
      icons.send();
      icons.onload = function (e) {
          var div = document.createElement("div");
          div.style.display = "none";
          div.innerHTML = icons.responseText;
          document.body.insertBefore(div, document.body.childNodes[0]);
      }
  </script>
  ```
- **Header & Navigation Precision**: Site-wide headers MUST use the exact class structures (e.g., `.main-header`, `.site-header__identity`, `.utility-nav`). Identities MUST use the nested structure: `.identity > .identity__logo` (containing `img` or `svg`) and `.identity__wordmark` (containing text spans). Never use raw `img` tags directly inside `.identity` or `.site-header__identity` without these brand-approved wrappers. Ensure the mobile toggle and navigation overlay structures are present and accurate to the Storybook source, as these are required for `aux.js` to function.
- **Dividers**: Dividers can be used to visually break up web pages between sections. Do not use a divider to create an underline effect beneath a heading. Icon Dividers are available in styles (Thumbs Up, Hash) and colors (Gray, Primary Maroon, Gold, White). Decorative Dividers use the class `.divider-dots` and Icon Dividers use the class `.divider-icon--[style]` combined with a color utility like `.primary-brand`.
- **Media Feature Angle Layout & Full-Width Structure**: To ensure the text box "floats" over the slanted image correctly, the `.media-feature` component MUST NOT be wrapped in a `.section-wrap` or `.container` element. It should be placed as a direct child of the main content so it can expand full-width. Additionally, `.media-feature__media` MUST ALWAYS come before `.media-feature__content` in the HTML DOM structure, and the outer container MUST include a background color modifier class (e.g., `.media-feature--gray-100`, `.media-feature--maroon`, or `.media-feature--gray-900`). To place the image on the right (and text on the left), simply apply the `.media-feature--right` modifier class to the outer parent container instead of swapping the underlying DOM nodes.
- **Breadcrumbs**: For pages using a standard, landing, or home page header, breadcrumbs (`<nav class="breadcrumbs">`) should be displayed directly below the page header component. The breadcrumbs navigation should contain an ordered list (`<ol>`) of `.breadcrumbs__item` list items, using the `-current` modifier on the active page and the `#aux_house-chimney` icon inside an anchor or unlinked element for the Home root label.
- **Accordions (Details Collection)**: When implementing accordions, NEVER use fake classes like `accordion` or `accordion-group`. Always wrap the collection in a `.details-collection` container containing a `.details-collection__container` with the `data-details-collection` attribute. The expand/collapse button must use `.details-collection__toggle` with `data-details-toggle`. The individual accordions must use HTML5 `<details class="details">` populated with an HTML5 `<summary>` tag that contains a heading span (e.g. `<span class="ns-h3">`), followed by the expanded body inside a `.details__content` wrapper.

## Variants
| Variant Name | Description |
|---|---|

## CSS Classes & Structure
| Class | Element | Purpose |
|---|---|---|

## Component Interactivity & Data Attributes
- Documentation of `data-*` attributes and `id` links.
- Note: `aux.js` auto-initializes on page load.

## JavaScript Dependencies
```html
<script src="https://aux.tamu.edu/v2/2.0.0/js/aux.js"></script>
```

## Accessibility
- ...

## Code Example
> **Note for Templates:** Every template example MUST be a full HTML5 document (`<!DOCTYPE html>` through `</html>`) including CDN links. You MUST provide the **exact, fully detailed HTML code** for the `<header>`, `<nav>`, and `<footer>` sections exactly as they appear in the Storybook's `rendered_html` output, without summarizing, simplifying, omitting, or truncating any nodes. This includes all nested brand wrappers like `.identity__logo` and `.identity__wordmark`, and accessibility attributes like `aria-expanded` and `data-mobilemenu`.
![Variant Name](./screenshot.png)
```html
<!-- Optional: Sub Component Name -->
<div>...</div>
```
<!-- (Code Example blocks repeat for ALL variants) -->
```



---

## File Structure Reference

```
Aggie-UX-antigravity/
├── aggie-ux-design-guide-prompt.md     ← This file (do not delete)
├── generate_design_guide.py            ← Extraction + synthesis orchestrator
├── design_guides/
│   └── vYYYY-MM-DD/                    ← Generated artifact directory
└── aggieux.tamu.edu/                   ← Optional local mirror (for --local flag)
```
