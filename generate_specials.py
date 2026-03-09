"""
THETOYSAREOUT — Hourly Specials Generator
Runs via GitHub Action every hour. Uses Claude API to generate fresh
drops, sessions, and merch specials, then injects them into index.html.
"""

import anthropic
import json
import random
import re
import os
from datetime import datetime, timezone, timedelta

# ── Config ───────────────────────────────────────────────────────────
BERLIN = timezone(timedelta(hours=1))
NOW = datetime.now(BERLIN)
HOUR = NOW.hour

# Scarcity ranges shift by time of day
if 0 <= HOUR < 6:      # Nacht — mysterious drops
    VIBE = "dark, mysterious, after-hours"
    STOCK_RANGE = (3, 12)
    WATCHERS = random.randint(40, 90)
elif 6 <= HOUR < 12:    # Morgen — fresh energy
    VIBE = "fresh, raw, morning energy"
    STOCK_RANGE = (8, 25)
    WATCHERS = random.randint(80, 160)
elif 12 <= HOUR < 18:   # Nachmittag — peak hype
    VIBE = "peak hype, exclusive, limited"
    STOCK_RANGE = (2, 8)
    WATCHERS = random.randint(150, 300)
else:                   # Abend — intimate
    VIBE = "intimate, late-night, exclusive"
    STOCK_RANGE = (5, 15)
    WATCHERS = random.randint(100, 200)

PROMPT = f"""You are the creative director for THETOYSAREOUT, an underground
artist brand. The current vibe is: {VIBE}. Time: {NOW.strftime('%H:%M')} Berlin.

Generate fresh content for the website. Return ONLY valid JSON, no markdown:

{{
  "drops": [
    {{
      "number": "DROP {random.randint(4, 99):03d}",
      "title": "<creative 2-3 word title, all caps>",
      "subtitle": "<one edgy line, German>",
      "price": <number between 10 and 50>,
      "stock_total": {random.randint(*STOCK_RANGE) + 10},
      "stock_left": {random.randint(*STOCK_RANGE)},
      "sold_out": false
    }},
    {{
      "number": "DROP {random.randint(4, 99):03d}",
      "title": "<creative 2-3 word title, all caps>",
      "subtitle": "<one edgy line, German>",
      "price": <number between 10 and 50>,
      "stock_total": {random.randint(*STOCK_RANGE) + 10},
      "stock_left": 0,
      "sold_out": true
    }},
    {{
      "number": "DROP {random.randint(4, 99):03d}",
      "title": "<creative 2-3 word title, all caps>",
      "subtitle": "<one edgy line, German>",
      "price": <number between 15 and 75>,
      "stock_total": {random.randint(*STOCK_RANGE) + 10},
      "stock_left": {random.randint(*STOCK_RANGE)},
      "sold_out": false
    }}
  ],
  "live_session": {{
    "title": "<creative session name, German>",
    "date": "<next upcoming date, format: DD.MM.YYYY>",
    "time": "<time like 21:00>",
    "tickets_total": {random.randint(20, 50)},
    "tickets_left": {random.randint(3, 15)},
    "price": {random.choice([5, 8, 10])}
  }},
  "merch": {{
    "name": "<creative merch item name, caps>",
    "type": "<T-Shirt/Hoodie/Cap/Poster>",
    "price": {random.choice([25, 35, 45, 55])},
    "stock_total": {random.randint(30, 100)},
    "stock_left": {random.randint(5, 25)}
  }},
  "headline": "<one-line German tagline for the hero section, edgy and short>",
  "watchers": {WATCHERS}
}}

Rules:
- All customer-facing text in German
- Titles are English, all caps
- Keep it raw, underground, authentic
- Never generic — every title should feel unique
- Subtitles should create urgency/FOMO
"""


def generate_specials():
    """Call Claude API and return specials dict."""
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model="claude-haiku-4-5-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": PROMPT}],
    )
    text = msg.content[0].text.strip()
    # Strip markdown code fences if present
    text = re.sub(r"^```json?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def build_drops_html(drops):
    """Generate HTML for the drops section."""
    html = ""
    for d in drops:
        sold_out = d.get("sold_out", False)
        badge = '<span style="background:var(--pink);color:var(--black);padding:2px 10px;font-size:.7rem;letter-spacing:2px">SOLD OUT</span>' if sold_out else ""
        stock_info = f'<span style="color:var(--pink);font-size:.85rem">{d["stock_left"]}/{d["stock_total"]} left</span>' if not sold_out else ""
        btn = f'<button onclick="alert(\'Coming soon.\')" style="padding:10px 30px;background:var(--pink);color:var(--black);border:none;font-family:\'Bebas Neue\',sans-serif;font-size:1.1rem;letter-spacing:2px;cursor:pointer">{d["price"]}€ — JETZT</button>' if not sold_out else '<button disabled style="padding:10px 30px;background:var(--border);color:#555;border:none;font-family:\'Bebas Neue\',sans-serif;font-size:1.1rem;letter-spacing:2px;cursor:not-allowed">VERGRIFFEN</button>'

        html += f"""
<div style="border:1px solid var(--border);padding:28px;{'opacity:.5;' if sold_out else ''}">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:.75rem;color:var(--pink);letter-spacing:3px">{d['number']}</span>
    {badge}
  </div>
  <h3 style="font-family:'Bebas Neue',sans-serif;font-size:1.6rem;letter-spacing:3px;margin-bottom:4px">{d['title']}</h3>
  <p style="font-size:.85rem;color:#888;margin-bottom:16px">{d['subtitle']}</p>
  <div style="display:flex;justify-content:space-between;align-items:center">
    {btn}
    {stock_info}
  </div>
</div>"""
    return html


def build_live_html(session):
    """Generate HTML for the live session section."""
    return f"""
<div style="border:1px solid var(--pink);padding:32px;text-align:center">
  <p style="font-size:.75rem;color:var(--pink);letter-spacing:4px;margin-bottom:8px">NÄCHSTE SESSION</p>
  <h3 style="font-family:'Bebas Neue',sans-serif;font-size:2rem;letter-spacing:3px;margin-bottom:8px">{session['title']}</h3>
  <p style="color:#888;margin-bottom:4px">{session['date']} — {session['time']} Uhr</p>
  <p style="color:var(--pink);font-size:.9rem;margin-bottom:20px">{session['tickets_left']}/{session['tickets_total']} Tickets übrig</p>
  <button onclick="alert('Coming soon.')" style="padding:12px 40px;background:var(--pink);color:var(--black);border:none;font-family:'Bebas Neue',sans-serif;font-size:1.1rem;letter-spacing:2px;cursor:pointer">{session['price']}€ — TICKET SICHERN</button>
</div>"""


def build_merch_html(merch):
    """Generate HTML for the merch section."""
    return f"""
<div style="border:1px solid var(--border);padding:28px;text-align:center">
  <div style="width:100%;height:200px;background:var(--mid);display:flex;align-items:center;justify-content:center;margin-bottom:16px">
    <span style="font-family:'Bebas Neue',sans-serif;font-size:1.5rem;letter-spacing:4px;color:#333">{merch['type'].upper()}</span>
  </div>
  <h3 style="font-family:'Bebas Neue',sans-serif;font-size:1.4rem;letter-spacing:3px;margin-bottom:4px">{merch['name']}</h3>
  <p style="color:var(--pink);font-size:.85rem;margin-bottom:16px">Nur {merch['stock_left']} von {merch['stock_total']} — Single Drop</p>
  <button onclick="alert('Coming soon.')" style="padding:10px 30px;background:var(--pink);color:var(--black);border:none;font-family:'Bebas Neue',sans-serif;font-size:1.1rem;letter-spacing:2px;cursor:pointer">{merch['price']}€ — KAUFEN</button>
</div>"""


def inject_into_html(specials):
    """Replace dynamic sections in index.html with fresh content."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Replace drops section
    drops_html = build_drops_html(specials["drops"])
    html = re.sub(
        r"(<!--DROPS-START-->).*?(<!--DROPS-END-->)",
        rf"\1{drops_html}\2",
        html,
        flags=re.DOTALL,
    )

    # Replace live session section
    live_html = build_live_html(specials["live_session"])
    html = re.sub(
        r"(<!--LIVE-START-->).*?(<!--LIVE-END-->)",
        rf"\1{live_html}\2",
        html,
        flags=re.DOTALL,
    )

    # Replace merch section
    merch_html = build_merch_html(specials["merch"])
    html = re.sub(
        r"(<!--MERCH-START-->).*?(<!--MERCH-END-->)",
        rf"\1{merch_html}\2",
        html,
        flags=re.DOTALL,
    )

    # Replace watcher count
    html = re.sub(
        r"(<!--WATCHERS-START-->).*?(<!--WATCHERS-END-->)",
        rf"\g<1>{specials['watchers']}\g<2>",
        html,
        flags=re.DOTALL,
    )

    # Replace headline
    html = re.sub(
        r"(<!--HEADLINE-START-->).*?(<!--HEADLINE-END-->)",
        rf"\g<1>{specials['headline']}\g<2>",
        html,
        flags=re.DOTALL,
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"[{NOW.strftime('%Y-%m-%d %H:%M')}] Specials updated:")
    print(f"  Drops: {', '.join(d['title'] for d in specials['drops'])}")
    print(f"  Live: {specials['live_session']['title']}")
    print(f"  Merch: {specials['merch']['name']}")
    print(f"  Watchers: {specials['watchers']}")
    print(f"  Headline: {specials['headline']}")


if __name__ == "__main__":
    specials = generate_specials()
    inject_into_html(specials)
