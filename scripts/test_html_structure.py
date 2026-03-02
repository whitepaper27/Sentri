"""Quick check: what HTML tags does Oracle docs actually use?"""

import sys
import urllib.request
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


class TagCounter(HTMLParser):
    """Count all tags in an HTML document."""

    def __init__(self):
        super().__init__()
        self.tags = Counter()
        self.heading_samples = []  # first 10 headings
        self.pre_count = 0
        self._in_heading = False
        self._heading_text = []
        self._heading_tag = ""

    def handle_starttag(self, tag, attrs):
        self.tags[tag.lower()] += 1
        if tag.lower() in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_tag = tag.lower()
            self._heading_text = []
        if tag.lower() == "pre":
            self.pre_count += 1

    def handle_endtag(self, tag):
        if tag.lower() in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if self._in_heading and len(self.heading_samples) < 10:
                text = "".join(self._heading_text).strip()
                if text:
                    self.heading_samples.append(f"<{self._heading_tag}> {text}")
            self._in_heading = False

    def handle_data(self, data):
        if self._in_heading:
            self._heading_text.append(data)


url = "https://docs.oracle.com/en/database/oracle/oracle-database/12.2/sqlrf/ALTER-TABLESPACE.html"
req = urllib.request.Request(url, headers={
    "User-Agent": "Mozilla/5.0 (compatible; SentriDBA/3.1)"
})

print(f"Fetching {url} ...")
with urllib.request.urlopen(req, timeout=30) as resp:
    html = resp.read().decode(resp.headers.get_content_charset() or "utf-8")

print(f"HTML size: {len(html)} bytes")

counter = TagCounter()
counter.feed(html)

print(f"\nTop 20 tags:")
for tag, count in counter.tags.most_common(20):
    print(f"  <{tag}>: {count}")

print(f"\n<pre> blocks: {counter.pre_count}")

print(f"\nHeading samples (first 10):")
for h in counter.heading_samples:
    print(f"  {h}")

# Also show a sample of the HTML around the first h2/h3
import re
# Find first <h2 or <h3 in the HTML
for pattern in [r'<h[23][^>]*>.*?</h[23]>', r'<div[^>]*class="[^"]*sect[^"]*"[^>]*>']:
    matches = re.findall(pattern, html[:20000], re.DOTALL | re.IGNORECASE)
    if matches:
        print(f"\nFirst 3 matches for {pattern}:")
        for m in matches[:3]:
            print(f"  {m[:200]}")
