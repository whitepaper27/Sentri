"""Deep look at Oracle docs HTML structure."""

import sys
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

url = "https://docs.oracle.com/en/database/oracle/oracle-database/12.2/sqlrf/ALTER-TABLESPACE.html"
req = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (compatible; SentriDBA/3.1)"
})

with urllib.request.urlopen(req, timeout=30) as resp:
    html = resp.read().decode(resp.headers.get_content_charset() or "utf-8")

# Find what comes before "Purpose" text
idx = html.find("Purpose")
if idx > 0:
    print("=== HTML around 'Purpose' (200 chars before, 500 after) ===")
    print(html[max(0,idx-200):idx+500])
    print()

# Find what comes before "Prerequisites"
idx = html.find("Prerequisites")
if idx > 0:
    print("=== HTML around 'Prerequisites' ===")
    print(html[max(0,idx-200):idx+500])
    print()

# Find BIGFILE references
idx = html.find("BIGFILE")
if idx > 0:
    print("=== HTML around 'BIGFILE' ===")
    print(html[max(0,idx-200):idx+500])
    print()

# Find section structure (div.section with bold/strong titles)
print("=== Section title patterns ===")
# Look for bold text that acts as section titles
bold_patterns = re.findall(r'<p[^>]*>\s*<span[^>]*class="bold"[^>]*>([^<]+)</span>', html)
if bold_patterns:
    print(f"Found {len(bold_patterns)} bold section titles:")
    for b in bold_patterns[:15]:
        print(f"  {b.strip()}")

# Also check for <p class="subhead...">
subhead = re.findall(r'<p[^>]*class="[^"]*subhead[^"]*"[^>]*>([^<]+)', html)
if subhead:
    print(f"\nFound {len(subhead)} subheadings:")
    for s in subhead[:10]:
        print(f"  {s.strip()}")

# Check what classes the <p> tags have
p_classes = re.findall(r'<p\s+class="([^"]+)"', html)
from collections import Counter
print(f"\n<p> classes:")
for cls, count in Counter(p_classes).most_common(10):
    print(f"  class='{cls}': {count}")

# Check div classes
div_classes = re.findall(r'<div\s+class="([^"]+)"', html)
print(f"\n<div> classes:")
for cls, count in Counter(div_classes).most_common(10):
    print(f"  class='{cls}': {count}")

# Check span classes
span_classes = re.findall(r'<span\s+class="([^"]+)"', html)
print(f"\n<span> classes:")
for cls, count in Counter(span_classes).most_common(10):
    print(f"  class='{cls}': {count}")
