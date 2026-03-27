"""
generate_design_guide.py
========================
Combined Phase 1 (extraction) + Phase 2 (synthesis) script for the
Aggie UX Design System Guide generation workflow.

Phase 1  — Scrapes the Aggie UX Storybook site (aggieux.tamu.edu) using
           Playwright, saves structured JSON files to raw_data/.
Phase 2  — Reads those JSON files and writes a complete, dated Markdown
           design guide to design_guides/.

Usage:
  # Full run (extract then synthesize):
  python generate_design_guide.py

  # Extract only:
  python generate_design_guide.py --extract-only

  # Synthesize only (re-uses existing raw_data/ JSON files):
  python generate_design_guide.py --synthesize-only

  # Extract a single section then synthesize:
  python generate_design_guide.py --section components

  # Use local mirror instead of live site:
  python generate_design_guide.py --local

Requirements:
  pip install playwright requests
  playwright install chromium
"""

import os
import sys
import json
import re
import time
import argparse
import datetime
import requests
import concurrent.futures
import threading
from collections import defaultdict
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORK_DIR    = os.path.dirname(os.path.abspath(__file__))
BASE_URL    = "https://aggieux.tamu.edu"
LOCAL_BASE  = os.path.join(WORK_DIR, "aggieux.tamu.edu")
RAW_DIR     = os.path.join(WORK_DIR, "raw_data")
OUTPUT_DIR  = os.path.join(WORK_DIR, "design_guides")
USER_AGENT  = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
TIMEOUT_MS    = 10000   # 10 s per page navigation
STORY_WAIT_MS = 500     # extra wait after story loads
CONCURRENT_PAGES = 3    # Number of parallel extraction workers (each with own browser)

SECTION_PREFIXES = {
    "getting_started": ["getting-started-"],
    "components":      ["components-"],
    "specialized":     ["specialized-"],
    "navigation":      ["navigation-"],
    "templates":       ["templates-"],
}

# ---------------------------------------------------------------------------
# ─────────────────────────  PHASE 1: EXTRACTION  ──────────────────────────
# ---------------------------------------------------------------------------

def fetch_index(use_local: bool) -> dict:
    """Load index.json from local mirror or live site."""
    if use_local:
        path = os.path.join(LOCAL_BASE, "index.json")
        print(f"Loading index from local mirror: {path}")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        url = f"{BASE_URL}/index.json"
        print(f"Fetching index from: {url}")
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    return data.get("entries", {})


def section_for_entry(entry_id: str) -> Optional[str]:
    """Return the section key for an entry ID, or None if not tracked."""
    eid = entry_id.lower()
    for section, prefixes in SECTION_PREFIXES.items():
        if any(eid.startswith(p) for p in prefixes):
            return section
    return None


def get_hierarchy_path(title: str) -> str:
    """Convert a Storybook title (e.g., 'Components/Card/Inline') into a nested folder path."""
    parts = [p.strip().lower().replace(" ", "-").replace("&", "and") for p in title.split("/")]
    parts = [re.sub(r'[^a-z0-9\-]', '', p) for p in parts]
    return os.path.join(*parts)


def make_url(entry_id: str, view: str = "docs", base: str = BASE_URL) -> str:
    """Build an iframe URL for a story or docs entry."""
    return f"{base}/iframe.html?id={entry_id}&viewMode={view}"


def clean_text(text: str) -> str:
    """Strip excess whitespace from extracted text."""
    lines = [line.strip() for line in text.splitlines()]
    result, blank_count = [], 0
    for line in lines:
        if line == "":
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)
    return "\n".join(result).strip()


def extract_minimal_html(raw_html: str) -> str:
    """Strip Storybook scaffolding and Lit template comments from HTML."""
    # Lit template comments
    raw_html = re.sub(r"<!--\?lit\$\d+\$-->", "", raw_html)
    raw_html = re.sub(r"<!---->", "", raw_html)
    # Script / style / link / meta tags
    raw_html = re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    raw_html = re.sub(r"<style[^>]*>.*?</style>",   "", raw_html, flags=re.DOTALL | re.IGNORECASE)
    raw_html = re.sub(r"<link[^>]*/?>",   "", raw_html, flags=re.IGNORECASE)
    raw_html = re.sub(r"<meta[^>]*/?>",   "", raw_html, flags=re.IGNORECASE)
    # html/head/body wrappers
    raw_html = re.sub(r"</?(?:html|head|body)[^>]*>", "", raw_html, flags=re.IGNORECASE)
    # Storybook root divs
    raw_html = re.sub(r'<div[^>]+id=["\'](?:storybook-root|root|sb-root)["\'][^>]*>', "", raw_html, flags=re.IGNORECASE)
    # Collapse blank lines
    raw_html = re.sub(r"\n{3,}", "\n\n", raw_html)
    return raw_html.strip()


def scrape_docs_page(page, url: str) -> dict:
    """Scrape a Storybook docs-mode iframe: prose, code snippets, props table."""
    from playwright.sync_api import TimeoutError as PWTimeoutError
    result = {"prose": "", "code_snippets": [], "props_table": "", "raw_text": ""}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.wait_for_timeout(500)

        selectors_to_try = [".sbdocs-content", ".docs-story", "#storybook-root", "body"]
        full_text = ""
        for sel in selectors_to_try:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    full_text = el.inner_text()
                    if len(full_text.strip()) > 50:
                        break
            except Exception:
                continue

        result["raw_text"] = clean_text(full_text)
        result["prose"]    = clean_text(full_text)

        code_blocks = page.locator("pre code, pre").all_inner_texts()
        result["code_snippets"] = [c.strip() for c in code_blocks if len(c.strip()) > 10]

        try:
            table_els = page.locator("table.docblock-argstable, .docblock-argstable").all()
            if table_els:
                result["props_table"] = table_els[0].inner_text().strip()
        except Exception:
            pass

    except Exception as e:  # includes PWTimeoutError
        result["prose"] = f"[ERROR: {e}]"

    return result


def scrape_story_page(page, url: str, story_id: str, date_str: str, hierarchy_path: str) -> dict:
    """Scrape a Storybook story-mode iframe: rendered HTML and screenshot."""
    from playwright.sync_api import TimeoutError as PWTimeoutError
    result = {"rendered_html": "", "error": None, "screenshot_filename": None}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        page.wait_for_timeout(STORY_WAIT_MS)

        selectors = ["#storybook-root", "#root", ".sb-show-main", "body"]
        html = ""
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if el.count() > 0:
                    inner = el.inner_html()
                    if len(inner.strip()) > 20:
                        html = inner
                        img_dir = os.path.join(OUTPUT_DIR, f"v{date_str}", hierarchy_path)
                        os.makedirs(img_dir, exist_ok=True)
                        img_filename = f"{story_id}.png"
                        img_path = os.path.join(img_dir, img_filename)
                        try:
                            el.screenshot(path=img_path)
                            result["screenshot_filename"] = img_filename
                        except Exception:
                            pass
                        break
            except Exception:
                continue

        result["rendered_html"] = extract_minimal_html(html)

    except Exception as e:
        result["error"] = str(e)

    return result


def extract_title_task(title: str, section: str, base_url: str, date_str: str, section_docs: dict, section_stories: dict) -> dict:
    """Worker task to extract a single component/title. Launches its own Playwright instance for thread safety."""
    from playwright.sync_api import sync_playwright
    
    title_clean = title.strip()
    hierarchy_path = get_hierarchy_path(title_clean)
    entry_data = {
        "title": title_clean, "section": section,
        "hierarchy_path": hierarchy_path,
        "docs_url": None, "docs_content": {}, "stories": [],
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.on("console", lambda msg: None)

            if title in section_docs:
                docs_entry = section_docs[title]
                docs_id    = docs_entry["id"]
                docs_url   = make_url(docs_id, view="docs", base=base_url)
                entry_data["docs_url"] = f"{BASE_URL}/?path=/docs/{docs_id}"
                entry_data["docs_content"] = scrape_docs_page(page, docs_url)

            for story_entry in section_stories.get(title, []):
                sid        = story_entry["id"]
                sname      = story_entry.get("name", sid)
                story_url  = make_url(sid, view="story", base=base_url)
                public_url = f"{BASE_URL}/?path=/story/{sid}"
                story_data = scrape_story_page(page, story_url, sid, date_str, hierarchy_path)
                entry_data["stories"].append({
                    "story_id": sid, "story_name": sname, "story_url": public_url,
                    "rendered_html": story_data["rendered_html"], "error": story_data.get("error"),
                    "screenshot_filename": story_data.get("screenshot_filename")
                })
            browser.close()
    except Exception as e:
        print(f"Error in task for {title}: {e}")

    return entry_data


def extract_section(entries: dict, section: str, base_url: str, date_str: str) -> list:
    """Extract all docs + story data for one section using parallel workers."""
    print(f"\n--- Extracting section: {section} ---", flush=True)

    section_docs    = {}
    section_stories = {}

    for eid, entry in entries.items():
        if section_for_entry(eid) != section:
            continue
        title = entry.get("title", "")
        etype = entry.get("type", "")
        if etype == "docs":
            if title not in section_docs:
                section_docs[title] = entry
            else:
                existing_tags = section_docs[title].get("tags", [])
                new_tags = entry.get("tags", [])
                if "autodocs" not in new_tags and "autodocs" in existing_tags:
                    section_docs[title] = entry
        elif etype == "story":
            section_stories.setdefault(title, []).append(entry)

    all_titles = sorted(set(section_docs.keys()) | set(section_stories.keys()))
    results    = []
    total      = len(all_titles)

    print(f"  Parallelly extracting {total} titles using {CONCURRENT_PAGES} workers...", flush=True)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENT_PAGES) as executor:
        futures = {executor.submit(extract_title_task, title, section, base_url, date_str, section_docs, section_stories): title for title in all_titles}
        
        done_count = 0
        for future in concurrent.futures.as_completed(futures):
            title = futures[future]
            try:
                data = future.result()
                results.append(data)
                done_count += 1
                if done_count % 5 == 0 or done_count == total:
                    print(f"  [{done_count}/{total}] Progress: {title} done.", flush=True)
            except Exception as e:
                print(f"\n  ✗ ERROR extracting '{title}': {e}", flush=True)

    return results


def save_section(data: list, section: str, date_str: str) -> str:
    """Save extracted section data to a dated JSON file in raw_data/."""
    os.makedirs(RAW_DIR, exist_ok=True)
    filename = os.path.join(RAW_DIR, f"{section}_{date_str}.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    size_kb = os.path.getsize(filename) / 1024
    print(f"  ✓ Saved {len(data)} entries → {os.path.basename(filename)} ({size_kb:.1f} KB)", flush=True)
    return filename


def run_extraction(use_local: bool, sections_to_run: list, date_str: str) -> list:
    """Phase 1 entry point. Returns list of saved JSON file paths."""
    from playwright.sync_api import sync_playwright

    base_url = f"file://{os.path.abspath(LOCAL_BASE)}" if use_local else BASE_URL
    print(f"Using {'LOCAL mirror' if use_local else 'LIVE site'}: {base_url}")

    entries = fetch_index(use_local)
    print(f"Loaded {len(entries)} total entries from index.json")
    print(f"Sections to extract: {sections_to_run}")

    saved_files = []
    num_sections = len(sections_to_run)
    for idx, section in enumerate(sections_to_run, 1):
        print(f"\nProcessing section {idx}/{num_sections}: {section}", flush=True)
        try:
            data    = extract_section(entries, section, base_url, date_str)
            outfile = save_section(data, section, date_str)
            saved_files.append(outfile)
        except Exception as e:
            print(f"\n  ✗ ERROR extracting '{section}': {e}", flush=True)
            import traceback; traceback.print_exc()

    print(f"\n{'='*60}\nEXTRACTION COMPLETE\n{'='*60}")
    for f in saved_files:
        print(f"  {f} ({os.path.getsize(f)/1024:.1f} KB)")
    return saved_files


# ---------------------------------------------------------------------------
# ─────────────────────────  PHASE 2: SYNTHESIS  ───────────────────────────
# ---------------------------------------------------------------------------


import subprocess

def call_gemini(prompt: str, json_data: dict) -> str:
    """Calls the Gemini CLI to synthesize markdown from JSON data."""
    data_str = json.dumps(json_data)
    # Gemini CLI needs data piped in
    process = subprocess.run(
        ["gemini", "--prompt", prompt],
        input=data_str,
        text=True,
        capture_output=True,
        check=False
    )
    if process.returncode != 0:
        print(f"Gemini error: {process.stderr}", file=sys.stderr)
        return ""
    return process.stdout

def load_json(section: str, date_str: str) -> list:
    path = os.path.join(RAW_DIR, f"{section}_{date_str}.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def run_synthesis(date_str: str) -> str:
    print(f"\nBeginning Gemini CLI Synthesis for {date_str}...", flush=True)
    
    out_dir = os.path.join(OUTPUT_DIR, f"v{date_str}")
    os.makedirs(out_dir, exist_ok=True)
    
    progress_file = os.path.join(RAW_DIR, f"progress_{date_str}.json")
    if os.path.exists(progress_file):
        with open(progress_file) as f:
            progress = json.load(f)
    else:
        progress = {"index": False, "components": {}}
        
    progress_lock = threading.Lock()
    
    # Load all json
    gs = load_json("getting_started", date_str)
    comp = load_json("components", date_str)
    spec = load_json("specialized", date_str)
    nav  = load_json("navigation", date_str)
    tmpl = load_json("templates", date_str)
    
    all_components = gs + comp + spec + nav + tmpl
    
    # Categorize icons
    def categorize_icons(icons):
        categories = defaultdict(list)
        for icon in icons:
            if any(k in icon for k in ['arrow', 'angle', 'chevron', 'caret', 'backward', 'forward', 'step', 'rotate', 'replay', 'rewind', 'skip', 'play', 'pause', 'mute', 'volume']):
                categories['Navigation & Controls'].append(icon)
            elif any(k in icon for k in ['facebook', 'twitter', 'linkedin', 'github', 'instagram', 'youtube', 'snapchat', 'reddit', 'flickr', 'dropbox', 'google', 'slack', 'bluesky']):
                categories['Social Media'].append(icon)
            elif any(k in icon for k in ['file', 'folder', 'clipboard', 'book', 'newspaper', 'notebook', 'diploma', 'certificate', 'tag']):
                categories['Documents & Education'].append(icon)
            elif any(k in icon for k in ['user', 'people', 'family', 'person', 'profile', 'contact', 'address-book', 'id-card']):
                categories['Users & People'].append(icon)
            elif any(k in icon for k in ['circle', 'square', 'triangle', 'bolt', 'star', 'check', 'xmark', 'exclamation', 'question', 'info', 'ban']):
                categories['Shapes & Status'].append(icon)
            elif any(k in icon for k in ['building', 'house', 'city', 'landmark', 'school', 'hospital', 'hotel']):
                categories['Places & Buildings'].append(icon)
            elif any(k in icon for k in ['phone', 'envelope', 'message', 'comments', 'comment', 'bullhorn', 'fax', 'mobile']):
                categories['Communication'].append(icon)
            elif any(k in icon for k in ['car', 'bus', 'truck', 'plane', 'bicycle', 'ship', 'train', 'scooter', 'anchor']):
                categories['Transport'].append(icon)
            elif any(k in icon for k in ['sun', 'moon', 'cloud', 'rain', 'tornado', 'snowflake', 'sparkle', 'fire', 'water']):
                categories['Nature & Weather'].append(icon)
            else:
                categories['General/Miscellaneous'].append(icon)
        
        md = ""
        for cat, ids in sorted(categories.items()):
            md += f"#### {cat}\\n"
            md += ", ".join(f"`{i}`" for i in sorted(ids)) + "\\n\\n"
        return md

    icon_list_str = "(Could not extract icons from SVG)"
    try:
        import requests, re
        icon_resp = requests.get("https://aux.tamu.edu/icons/aux-sprite.svg", timeout=10)
        icons = re.findall(r'<symbol[^>]*id=[\'"]([^\'"]+)[\'"]', icon_resp.text)
        if icons:
            icon_list_str = categorize_icons(icons)
        else:
            icon_list_str = "No icons found"
    except Exception as e:
        pass

    # Generate Index (Manifest) if not done
    if not progress.get("index"):
        print("Synthesizing Index/Manifest...", flush=True)
        
    # Group components by section for a better TOC
    from collections import defaultdict
    categories = defaultdict(list)
    for c in all_components:
        hp = c.get('hierarchy_path', '')
        c['target_file_path'] = f"{hp}/{os.path.basename(hp)}.md"
        categories[c.get('section', 'General')].append(c)
        
    component_list_str = ""
    for cat, items in categories.items():
        cat_title = cat.replace('_', ' ').title()
        component_list_str += f"### {cat_title}\n"
        for c in items:
            component_list_str += f"* [{c.get('title')}](./{c.get('target_file_path')})\n"
        component_list_str += "\n"
        
        # Parse available CSS utilities from the CDN
        import re, requests
        try:
            resp = requests.get("https://aux.tamu.edu/v2/2.0.0/styles/aux-styles.css", timeout=10)
            classes = re.findall(r'\.([a-zA-Z0-9_-]+)', resp.text)
            utils = set()
            for cls in set(classes):
                if re.match(r'^(col|m|p|mt|mb|ml|mr|pt|pb|pl|pr|mx|px|my|py|d|align|justify|flex|hide|show)-[a-zA-Z0-9-]+$', cls) or cls in ['container', 'container-fluid', 'row', 'd-flex']:
                    utils.add(cls)
            utilities_str = ", ".join(sorted(utils))
        except Exception as e:
            utilities_str = "(Could not extract utilities from CSS)"

        color_palette_str = """
| Color Name | Hex Code | Description |
|---|---|---|
| Primary Maroon | `#500000` | The primary university brand color. |
| Secondary White | `#ffffff` | Used for backgrounds and text on maroon. |
| Cream | `#d6d3c4` | Accent color for sparse decoration. |
| Ivory | `#e9e4dc` | Subtle background/accent color. |
| Gray Scale | `#1a1a1a` to `#f2f2f2` | Gray 100-900 for dividers, borders, and text hierarchy. |
"""

        index_prompt = f"""
You are an expert design systems documentarian. 
Create an index.md manifest for the Aggie UX Design System.
Use the provided JSON data to inform your output, but do NOT exhaustively document every component here.
Instead, focus on acting as a high-level router and manifest.

CRITICAL INSTRUCTIONS:
- You MUST Hardcode the following CDN implementation (v2.0.0) in the Developer Tools section:
  CSS: <link rel="stylesheet" href="https://aux.tamu.edu/v2/2.0.0/styles/aux-styles.css">
  JS: <script src="https://aux.tamu.edu/v2/2.0.0/js/aux.js" defer></script>
- FONT LOADING: Explicitly state that `aux-styles.css` automatically imports the required brand fonts (Oswald, Open Sans, Work Sans). No manual `@import` is needed in the user's CSS; they only need to use the associated typography classes.
- EXPLICIT DENY-LIST: You MUST add a strong directive stating: "Aggie UX does NOT use Bootstrap or Tailwind. Do not use generic utility classes from other frameworks. Only use the utilities explicitly listed below."
- MASTER COLOR PALETTE: Include this summary table of brand colors:
{color_palette_str}
- GRID & UTILITIES SECTION: Name the layout section "Aggie UX Grid & Utilities" instead of "Layout & Utilities".
- GRID MODIFIERS: Explicitly mention that agents must only use the "Supported Modifiers" (e.g., `-sm`, `-md`, `-lg`, `-xl`, `-xxl`) shown in the list below. Do not assume pass-through Bootstrap modifiers (e.g. `g-4` or `offset-1`) exist unless they are in the utilities list.
- GENERAL BEST PRACTICES: You MUST include a section on "Development Best Practices" including:
  1. **Card Alignment**: Always use `margin-top: auto` on CTA links/buttons within cards to ensure alignment.
  2. **Icon Visibility (CORS)**: Warn that SVG icons will not load via `file://`. Provide the **SVG Sprite Injection** script (using `XMLHttpRequest`) as a robust alternative for local development.
- LAYOUT SCENARIO (ICONS + GRID): You MUST include a concrete HTML code snippet in the Aggie UX Grid & Utilities section demonstrating how to use grid classes (e.g., `d-grid`, `row`, `col`) combined with an icon (e.g., `<svg><use href="#aux_book"></use></svg>`).
- UTILITIES LIST: Include this full list of allowable utility classes in the Aggie UX Grid & Utilities section: {utilities_str}
- At the bottom of `index.md`, you MUST include a "Table of Contents" of all UI documentation files grouped by section.
Here is the master list of documentations to format:
{component_list_str}

Format clearly using Markdown.
"""
        index_data = {}
        result = call_gemini(index_prompt, index_data)
        if result:
            # Extract markdown if fenced
            match = re.search(r'```markdown\n(.*?)\n```', result, re.DOTALL)
            if match:
                content = match.group(1).strip()
            else:
                # remove everything before the first #
                match_hash = re.search(r'#.*', result, re.DOTALL)
                content = match_hash.group(0).strip() if match_hash else result.strip()
                
            with open(os.path.join(out_dir, "index.md"), "w") as f:
                f.write(content)
            with progress_lock:
                try:
                    with open(progress_file, "r") as f:
                        prog = json.load(f)
                except Exception:
                    prog = {"index": False, "components": {}}
                prog["index"] = True
                with open(progress_file, "w") as f:
                    json.dump(prog, f, indent=2)
            print("✓ Saved index.md", flush=True)
    else:
        print("- Skipping index.md (already done)", flush=True)
        
    # Group components
    batch_size = 5
    batches = []
    # Collect all pending components first
    pending_components = [c for c in all_components if not progress["components"].get(c.get("title", "").strip())]
    
    for i in range(0, len(pending_components), batch_size):
        batch = pending_components[i:i+batch_size]
        batches.append(batch)

    if not batches:
        print(f"\n✓ All synthesis operations complete in {out_dir}", flush=True)
        return out_dir

    print(f"Synthesizing {len(batches)} batches in parallel...")

    def process_batch_task(batch_idx, batch_data):
        try:
            for c in batch_data:
                hp = c.get('hierarchy_path', '')
                slug = os.path.basename(hp)
                c['target_file_path'] = f"{hp}/{slug}.md"
                
            print(f"  [Batch {batch_idx}] Starting synthesis of {len(batch_data)} components...", flush=True)
            
            prompt = f'''
You are an expert design systems documentarian. 
I am providing you with raw JSON scraped from a Storybook instance representing UI components.
Convert this into clean, concise Markdown component documentation. 

If you are documenting the \"Aggie UX Icon Library\" or an \"Icons\" section, you MUST list all of these available icon IDs categorized as follows so agents know what exists:
{icon_list_str}

Before outputting the files, you MUST output your analysis and reasoning inside <thought>...</thought> tags to explain any nuances or decisions.

For each component in the JSON array, output exactly like this formatting:

--- FILE: {{target_file_path}} ---
# {{Component Title}}
> Brief 1-sentence description.

**Storybook Reference:** {{url}}

## When to Use
- ...

## When NOT to Use
- ...

## Requirements
- Identify any Parent/Child relationships. If a component is useless without its parent (e.g., a Card inside a Card Group, or an Accordion inside an Accordion Group), clearly state that it must be placed inside its specific parent container class.

## Known Quirks & Best Practices
- **Card Alignment**: For Card components, explicitly document that using `margin-top: auto` on `.cta-link` or other terminal elements within `.card__content` is required to ensure consistent button alignment across a row of cards, regardless of description content length.
- **Icon Visibility (CORS)**: For any component using icons, explicitly warn that SVG icons using `<use href="...">` referencing a CDN sprite will NOT work when the HTML is viewed locally via `file://`. Recommend using a local web server OR including the **SVG Sprite Injection** script below for development:
  ```javascript
  <script>
      let icons = new XMLHttpRequest();
      icons.open("GET", "https://aux.tamu.edu/icons/aux-sprite.svg", true);
      icons.send();
      icons.onload = function (e) {{
          var div = document.createElement("div");
          div.style.display = "none";
          div.innerHTML = icons.responseText;
          document.body.insertBefore(div, document.body.childNodes[0]);
      }}
  </script>
  ```
- **Header & Navigation Precision**: Site-wide headers MUST use the exact class structures (e.g., `.main-header`, `.site-header__identity`, `.utility-nav`). Identities MUST use the nested structure: `.identity > .identity__logo` (containing `img` or `svg`) and `.identity__wordmark` (containing text spans). Never use raw `img` tags directly inside `.identity` or `.site-header__identity` without these brand-approved wrappers (e.g. `.identity__logo`). Ensure the mobile toggle and navigation overlay structures are present and IDENTICAL to the Storybook source, as these are required for `aux.js` to function.

## Variants
| Variant Name | Description |
|---|---|

## CSS Classes & Structure
| Class | Element | Purpose |
|---|---|---|

## Component Interactivity & Data Attributes
- Explicitly identify and document any required `data-*` attributes (like `data-details-toggle`) or specific `id` relationships necessary to hook into the core JS bundle.
- State clearly that `aux.js` auto-initializes elements with `data-` attributes on page load, requiring no additional JavaScript for standard components beyond including the script.

## JavaScript Dependencies
```html
<script src="https://aux.tamu.edu/v2/2.0.0/js/aux.js"></script>
```

## Accessibility
- ...

## Code Example
You MUST output an individual HTML code block for EVERY variant listed in the Variants table. Do not just show the primary example. Look through the `stories` array and generate the HTML snippet and screenshot for each mode/variant.
Wrap any placeholder, optional, or slotted sub-components within the HTML code blocks with explicit HTML comments (e.g., `<!-- Optional: Section Intro Component --> ... <!-- End Optional -->`). This teaches the agent which parts of the boilerplate are composable and what the core component actually is.

If you are documenting a "Template" component (e.g., any layout representing a whole page like a Standard Page or Home Page), you MUST output a FULL, valid HTML5 document for EVERY variant's code example. That means starting exactly with `<!DOCTYPE html>`, an `<html lang="en">` tag, `<head>` (containing the required CDN links for `aux-styles.css` and `aux.js`, plus standard meta charset/viewport), and `<body>` containing the template's layout (including the global header, main nav, and footer placeholders if available), ending with closing `</body>` and `</html>` tags.
You MUST provide the **exact, fully detailed HTML code** for the `<header>`, `<nav>`, and `<footer>` sections exactly as they appear in the provided `rendered_html` field, without summarizing, simplifying, omitting, or truncating any nodes. This includes all nested brand wrappers like `.identity__logo` and `.identity__wordmark`, and interactivity attributes like `aria-expanded` and `data-mobilemenu`. Do this instead of just outputting a bare div or a simplified header.

If the story object also includes a `screenshot_filename` field, embed it immediately above the HTML code block using this exact syntax (note the relative path './'):
![Variant Name](./{{screenshot_filename}})

```html
... clean HTML from `rendered_html` here ...
```
'''
            result = call_gemini(prompt, batch_data)
            if not result:
                print(f"  [Batch {batch_idx}] Failed. Gemini returned empty result.", flush=True)
                return

            # Print the thoughts for debugging
            thought_match = re.search(r'<thought>(.*?)</thought>', result, flags=re.DOTALL | re.IGNORECASE)
            if thought_match:
                print(f"  [Batch {batch_idx}] Thoughts:\n{thought_match.group(1).strip()}\n", flush=True)
                
            # Parse output into files
            current_file = None
            current_lines = []
            for line in result.splitlines():
                if line.startswith("--- FILE:") and line.endswith("---"):
                    if current_file:
                        file_path = os.path.join(out_dir, current_file)
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)
                        with open(file_path, "w") as f:
                            f.write("\n".join(current_lines).strip() + "\n")
                    current_file = line.replace("--- FILE:", "").replace("---", "").strip()
                    if current_file.startswith("/"):
                        current_file = current_file[1:]
                    current_lines = []
                else:
                    current_lines.append(line)
                    
            if current_file:
                file_path = os.path.join(out_dir, current_file)
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "w") as f:
                    f.write("\n".join(current_lines).strip() + "\n")
                    
            # Update progress safely
            with progress_lock:
                try:
                    with open(progress_file, "r") as f:
                        prog = json.load(f)
                except Exception:
                    prog = {"index": False, "components": {}}
                for c in batch_data:
                    prog["components"][c.get("title", "").strip()] = True
                with open(progress_file, "w") as f:
                    json.dump(prog, f, indent=2)
                
            print(f"  [Batch {batch_idx}] done.", flush=True)
        except Exception as e:
            print(f"  [Batch {batch_idx}] CRASHED: {e}", flush=True)
            import traceback
            traceback.print_exc()


    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for idx, batch_data in enumerate(batches, 1):
            futures.append(executor.submit(process_batch_task, idx, batch_data))
        concurrent.futures.wait(futures)
        
    print(f"\n✓ All synthesis operations complete in {out_dir}", flush=True)
    return out_dir

# ---------------------------------------------------------------------------
# MAIN EXECUTION
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggie UX Design Guide Generator")
    parser.add_argument("--extract-only", action="store_true", help="Run Phase 1 only")
    parser.add_argument("--synthesize-only", action="store_true", help="Run Phase 2 only")
    parser.add_argument("--local", action="store_true", help="Use local mirror instead of live site")
    parser.add_argument("--section", help="Extract and synthesize one section only")
    parser.add_argument("--date", help="Override the date (YYYY-MM-DD)")

    args = parser.parse_args()

    # Determine date string
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    date_str = args.date or today

    # Determine sections
    if args.section:
        if args.section not in SECTION_PREFIXES:
            print(f"Error: Unknown section '{args.section}'. Valid sections: {list(SECTION_PREFIXES.keys())}")
            sys.exit(1)
        sections = [args.section]
    else:
        sections = list(SECTION_PREFIXES.keys())

    # Phase 1: Extraction
    if not args.synthesize_only:
        try:
            run_extraction(use_local=args.local, sections_to_run=sections, date_str=date_str)
        except Exception as e:
            print(f"Extraction failed: {e}")
            if args.extract_only:
                sys.exit(1)

    # Phase 2: Synthesis
    if not args.extract_only:
        try:
            run_synthesis(date_str=date_str)
        except Exception as e:
            print(f"Synthesis failed: {e}")
            sys.exit(1)
