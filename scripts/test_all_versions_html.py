"""Check HTML structure across all Oracle doc versions."""

import sys
import re
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

URLS = {
    "12c": "https://docs.oracle.com/en/database/oracle/oracle-database/12.2/sqlrf/ALTER-TABLESPACE.html",
    "19c": "https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ALTER-TABLESPACE.html",
    "21c": "https://docs.oracle.com/en/database/oracle/oracle-database/21/sqlrf/ALTER-TABLESPACE.html",
    "23ai": "https://docs.oracle.com/en/database/oracle/oracle-database/23/sqlrf/ALTER-TABLESPACE.html",
}

UA = "Mozilla/5.0 (compatible; SentriDBA/3.1)"


def analyze_html(version, html):
    """Analyze key structural patterns in Oracle HTML."""
    print(f"\n{'=' * 60}")
    print(f"  {version} — {len(html)} bytes")
    print(f"{'=' * 60}")

    # Count heading tags
    for tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        count = len(re.findall(rf'<{tag}[\s>]', html, re.I))
        if count:
            print(f"  <{tag}>: {count}")

    # Count <pre> blocks
    pre_count = len(re.findall(r'<pre[\s>]', html, re.I))
    print(f"  <pre>: {pre_count}")

    # Check for subhead classes (Oracle's section titles)
    for cls in ("subhead1", "subhead2", "subhead3"):
        matches = re.findall(rf'class="[^"]*{cls}[^"]*"', html)
        if matches:
            print(f"  class~'{cls}': {len(matches)}")

    # Check for bold spans (sub-section titles)
    bold_spans = re.findall(r'<span[^>]*class="bold"[^>]*>([^<]{3,60})</span>', html)
    if bold_spans:
        print(f"  <span class='bold'>: {len(bold_spans)} (first 5: {bold_spans[:5]})")

    # Check for sect1/sect2/sect3 divs
    for cls in ("sect1", "sect2", "sect3"):
        count = len(re.findall(rf'class="[^"]*{cls}[^"]*"', html))
        if count:
            print(f"  div.{cls}: {count}")

    # Check <p> class varieties
    p_classes = re.findall(r'<p\s+class="([^"]+)"', html)
    if p_classes:
        top = Counter(p_classes).most_common(5)
        print(f"  <p> classes: {dict(top)}")

    # Check for <article>, <main>, <section> semantic tags
    for tag in ("article", "main", "section"):
        count = len(re.findall(rf'<{tag}[\s>]', html, re.I))
        if count:
            print(f"  <{tag}>: {count}")

    # Check for figure/img (syntax diagrams)
    fig_count = len(re.findall(r'<(?:figure|img)[\s>]', html, re.I))
    if fig_count:
        print(f"  figures/images: {fig_count}")

    # Show first "section title" for each format
    subhead_sample = re.findall(r'<p[^>]*class="subhead1"[^>]*>([^<]+)', html)
    if subhead_sample:
        print(f"  subhead1 samples: {subhead_sample[:5]}")

    # Check for <dt>/<dd> (definition lists — used in some Oracle versions)
    dt_count = len(re.findall(r'<dt[\s>]', html, re.I))
    if dt_count:
        print(f"  <dt> (def list): {dt_count}")


for version, url in URLS.items():
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
        analyze_html(version, html)
    except Exception as e:
        print(f"\n{version}: FAILED — {e}")
