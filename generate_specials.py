"""
THETOYSAREOUT — Hourly Specials & Organic Layout Generator
Runs via GitHub Action every hour. Uses Claude API to generate fresh
drops, sessions, and merch specials, then injects them into index.html.
Also shifts the visual layout organically based on time of day.
"""

import anthropic
import json
import math
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


# ── Organic Layout Engine ────────────────────────────────────────────

def generate_organic_css():
    """Generate CSS overrides that shift the visual feel based on time."""
    # Use hour as a continuous wave for smooth transitions
    t = HOUR / 24.0  # 0.0 to 1.0
    wave = math.sin(t * math.pi * 2)      # -1 to 1, peaks at 6h
    wave2 = math.cos(t * math.pi * 2)     # -1 to 1, peaks at 0h/24h

    # ── Accent color: pink hue drifts through the day ──
    # Base pink: hsl(334, 100%, 59%) = #ff2d8e
    # Night: cooler/bluer (320°), Morning: warmer (340°), Afternoon: hot (345°), Evening: purple (315°)
    hue_shift = {
        range(0, 6): -14,    # 320° — cold neon
        range(6, 12): 6,     # 340° — warm sunrise
        range(12, 18): 11,   # 345° — hot peak
        range(18, 24): -19,  # 315° — purple night
    }
    hue_offset = 0
    for r, v in hue_shift.items():
        if HOUR in r:
            hue_offset = v
            break
    accent_hue = 334 + hue_offset + random.randint(-3, 3)

    # ── Background darkness shifts ──
    # Deeper black at night, slightly lifted in afternoon
    bg_lightness = max(1, min(4, int(2 + wave * 1.5)))

    # ── Grain intensity: heavier at night, lighter during day ──
    grain_opacity = round(0.08 + (1 - t if t < 0.5 else t) * 0.08, 3)

    # ── Hero glow radius: expands in afternoon, contracts at night ──
    glow_size = int(55 + wave2 * 15 + random.randint(-5, 5))

    # ── Section spacing: breathes with time ──
    section_pad_min = int(56 + wave * 12)
    section_pad_max = int(110 + wave * 20)

    # ── Drop grid: column count shifts ──
    if 0 <= HOUR < 6:
        grid_min = "300px"  # fewer, wider cards at night
    elif 12 <= HOUR < 18:
        grid_min = "220px"  # more cards, tighter grid at peak
    else:
        grid_min = "260px"  # default

    # ── Border style variations ──
    border_styles = {
        range(0, 6): "1px solid rgba(255,255,255,.04)",    # barely visible
        range(6, 12): "1px solid var(--border)",            # clean
        range(12, 18): "1px solid rgba(255,45,142,.15)",    # pink tint
        range(18, 24): "1px solid rgba(201,168,76,.08)",    # gold whisper
    }
    border_style = "1px solid var(--border)"
    for r, v in border_styles.items():
        if HOUR in r:
            border_style = v
            break

    # ── Animation speeds: slower at night, snappier in afternoon ──
    breathe_speed = round(5 + (1 - abs(wave)) * 6, 1)  # 5-11s
    pulse_speed = round(2.5 + (1 - abs(wave)) * 3, 1)   # 2.5-5.5s

    # ── Card hover transform: subtle shifts ──
    hover_y = random.choice([-3, -4, -5, -6])
    hover_scale = round(1 + random.uniform(0.005, 0.02), 3)

    # ── Hero name sizing: pulses slightly ──
    hero_size_min = random.choice([52, 56, 60])
    hero_size_max = random.choice([140, 150, 160])

    # ── Scroll hint opacity ──
    scroll_opacity = round(0.12 + random.uniform(0, 0.12), 2)

    # ── Sub-label letter-spacing: tighter or wider ──
    label_spacing = round(0.3 + random.uniform(0, 0.2), 2)

    css = f"""
/*ORGANIC-GENERATED {NOW.strftime('%Y-%m-%d %H:%M')} Berlin*/
:root{{
  --pink:hsl({accent_hue},100%,59%);
  --black:hsl(0,0%,{bg_lightness}%);
  --dim:hsl(0,0%,{bg_lightness + 3}%);
  --mid:hsl(0,0%,{bg_lightness + 5}%);
}}
#grain{{opacity:{grain_opacity}}}
#hero-bg{{background:radial-gradient(ellipse at 50% 60%, hsla({accent_hue},100%,59%,.08) 0%, transparent {glow_size}%)}}
@keyframes breathe{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.007)}}}}
#hero-name{{animation:breathe {breathe_speed}s ease-in-out infinite;font-size:clamp({hero_size_min}px,12vw,{hero_size_max}px)}}
@keyframes pulse{{0%,100%{{transform:scale(1);box-shadow:0 0 0 0 hsla({accent_hue},100%,59%,.4)}}60%{{transform:scale(1.8);box-shadow:0 0 0 14px hsla({accent_hue},100%,59%,0)}}}}
#hero-dot{{animation:pulse {pulse_speed}s ease-in-out infinite}}
.sect{{padding:clamp({section_pad_min}px,10vh,{section_pad_max}px) clamp(24px,6vw,100px);border-top:{border_style}}}
.s-label{{letter-spacing:{label_spacing}em}}
.drop-grid{{grid-template-columns:repeat(auto-fill,minmax({grid_min},1fr))}}
.drop-card{{border:{border_style}}}
.drop-card:hover{{border-color:var(--pink);transform:translateY({hover_y}px) scale({hover_scale})}}
.tier:hover{{transform:translateY({hover_y}px)}}
.live-card{{border:{border_style}}}
.merch-item{{border:{border_style}}}
#scroll-hint{{opacity:{scroll_opacity}}}
"""
    return css.strip()


# ── Content Builders (use existing CSS classes) ──────────────────────

def build_drops_html(drops):
    """Generate HTML for the drops section using existing CSS classes."""
    html = ""
    for d in drops:
        sold_out = d.get("sold_out", False)
        sold_class = " sold-out" if sold_out else ""
        stock_text = "SOLD OUT" if sold_out else f'<b>{d["stock_left"]}</b> / {d["stock_total"]} übrig'

        html += f"""
    <div class="drop-card{sold_class}">
      <div class="drop-tag">{d['number']}</div>
      <div class="drop-name">{d['title']}</div>
      <div class="drop-desc">{d['subtitle']}</div>
      <div class="drop-bottom">
        <div class="drop-price">{d['price']} €</div>
        <div class="drop-stock">{stock_text}</div>
      </div>
    </div>"""
    return html


def build_live_html(session):
    """Generate HTML for the live session section using existing CSS classes."""
    return f"""<div class="live-card">
    <div class="live-dot"></div>
    <div class="live-date">{session['date']}</div>
    <div class="live-info">{session['title']}<br>{session['time']} Uhr · Exklusiver Livestream<br>Nur für Ticketinhaber</div>
    <button class="btn btn-p" onclick="alert('Coming soon.')">{session['price']}€ · Ticket sichern →</button>
    <div class="live-slots">Plätze: <b>{session['tickets_left']}</b> / {session['tickets_total']} verfügbar</div>
  </div>"""


def build_merch_html(merch):
    """Generate HTML for the merch section using existing CSS classes."""
    return f"""<div class="merch-item">
    <div class="merch-img">{merch['type'].upper()}</div>
    <div class="merch-info">
      <div class="merch-name">{merch['name']}</div>
      <div class="merch-desc">Nur {merch['stock_left']} von {merch['stock_total']} — Single Drop. Limitiert.</div>
      <div class="merch-bottom">
        <div class="merch-price">{merch['price']} €</div>
        <div class="merch-ed">Edition {random.randint(1,99):03d} · {merch['stock_total']} Stück</div>
      </div>
      <button class="btn btn-p" style="width:100%;margin-top:16px" onclick="alert('Coming soon.')">Vorbestellen →</button>
    </div>
  </div>"""


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


def inject_into_html(specials):
    """Replace dynamic sections in index.html with fresh content."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Inject organic CSS overrides
    organic_css = generate_organic_css()
    html = re.sub(
        r"(<!--ORGANIC-CSS-START-->).*?(<!--ORGANIC-CSS-END-->)",
        rf"\1<style>\n{organic_css}\n</style>\2",
        html,
        flags=re.DOTALL,
    )

    # Replace drops section
    drops_html = build_drops_html(specials["drops"])
    html = re.sub(
        r"(<!--DROPS-START-->).*?(<!--DROPS-END-->)",
        rf"\1{drops_html}\n  \2",
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

    print(f"[{NOW.strftime('%Y-%m-%d %H:%M')}] Specials + layout updated:")
    print(f"  Drops: {', '.join(d['title'] for d in specials['drops'])}")
    print(f"  Live: {specials['live_session']['title']}")
    print(f"  Merch: {specials['merch']['name']}")
    print(f"  Watchers: {specials['watchers']}")
    print(f"  Headline: {specials['headline']}")
    print(f"  Accent hue: shifted for {HOUR}:00")
    print(f"  Layout: organic override applied")


if __name__ == "__main__":
    specials = generate_specials()
    inject_into_html(specials)
