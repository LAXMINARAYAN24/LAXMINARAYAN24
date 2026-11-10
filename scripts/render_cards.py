#!/usr/bin/env python3
"""Render the GitHub profile cards (animated SVGs) into cards/.

    python scripts/render_cards.py                 # animated cards -> cards/
    python scripts/render_cards.py static=1        # final frame, no animation -> cards-static/
    STATIC=1 python scripts/render_cards.py        # same as static=1

Inputs : data/profile.json, data/contributions.json, assets/fonts/*.woff2,
         assets/portrait.(jpg|png|webp) (optional)
Outputs: cards/*.svg and preview.html (open it to see the README layout; with static=1 it is
         written as preview-static.html and points at cards-static/ for screenshots).

The cards are shown through <img> tags, so each SVG is fully self-contained:
  * one <style> block holds the @font-face rules and CSS @keyframes (no style="" attributes,
    no SMIL, no scripts, no external requests);
  * fonts are subset to the characters each card draws (fontTools.subset, the library behind
    pyftsubset) and embedded as base64 WOFF2 data URIs;
  * every element is drawn in its final state and the keyframes only define the "from" state, so
    removing the animation (reduced motion, static mode) always leaves the final frame;
  * delays are staggered across and within cards so the page plays top to bottom;
  * @media (prefers-reduced-motion: reduce) switches every animation off.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import html
import io
import json
import math
import os
import sys
import unicodedata
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- colours
# Tokens copied from the portfolio's src/styles.css (dark theme), converted from OKLCH to sRGB.
def oklch(L: float, C: float, h: float) -> str:
    a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m_ = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s_ = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (
        4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
        -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
        -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_,
    )

    def enc(x: float) -> int:
        x = min(1.0, max(0.0, x))
        x = 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
        return round(x * 255)

    return "#%02x%02x%02x" % tuple(enc(v) for v in rgb)


BG = oklch(0.16, 0.02, 260)            # --background
CARD = oklch(0.21, 0.025, 260)         # --card
BORDER = oklch(0.28, 0.03, 260)        # --border
FG = oklch(0.97, 0.01, 250)            # --foreground
MUTED_FG = oklch(0.68, 0.03, 255)      # --muted-foreground
PRIMARY = oklch(0.78, 0.17, 180)       # --primary (teal)
ACCENT = oklch(0.72, 0.19, 320)        # --accent (magenta)
TAG_COLOURS = {                        # --chart-1..4
    "AI": oklch(0.78, 0.17, 180),
    "Vision": oklch(0.72, 0.19, 320),
    "Security": oklch(0.70, 0.18, 50),
    "Full-Stack": oklch(0.75, 0.15, 240),
}
HEAT = [oklch(0.26, 0.03, 260), oklch(0.40, 0.07, 185), oklch(0.52, 0.11, 183),
        oklch(0.65, 0.15, 181), oklch(0.78, 0.17, 180)]

# Tailwind's default sans stack is what the site renders; Inter is embedded as its closest match.
FONT_STACK = "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

FULL_W, HALF_W, TILE_W = 820, 406, 267      # 100%, 49.5% and 32.55% of an 820px column
PAD = 40


# --------------------------------------------------------------------------- fonts
class Font:
    def __init__(self, path: Path):
        self.path = path
        self.tt = TTFont(str(path))
        self.upem = self.tt["head"].unitsPerEm
        self.cmap = self.tt.getBestCmap()
        self.hmtx = self.tt["hmtx"]

    def width(self, text: str, size: float, ls: float = 0.0) -> float:
        total = 0
        for ch in text:
            total += self.hmtx[self.cmap.get(ord(ch), ".notdef")][0]
        return (total * size / self.upem + ls * len(text)) * 1.015   # small safety margin

    def subset_b64(self, chars: set[str]) -> str:
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern"]
        opts.hinting = False
        opts.name_IDs = []
        opts.notdef_outline = False
        font = subset.load_font(str(self.path), opts)
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(sorted(chars | {" "})))
        sub.subset(font)
        buf = io.BytesIO()
        font.flavor = "woff2"
        font.save(buf)
        return base64.b64encode(buf.getvalue()).decode()


def load_fonts() -> dict[int, Font]:
    d = ROOT / "assets" / "fonts"
    return {400: Font(d / "Inter-400.woff2"), 600: Font(d / "Inter-600.woff2")}


FONTS: dict[int, Font] = {}


def wrap(text: str, size: float, max_w: float, weight: int = 400, ls: float = 0.0) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and FONTS[weight].width(trial, size, ls) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------- card scaffold
ICONS = {  # 24x24 stroke icons (Lucide, ISC licence)
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/>'
             '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "linkedin": '<path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/>'
                '<rect width="4" height="12" x="2" y="9"/><circle cx="4" cy="4" r="2"/>',
    "file": '<path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/>'
            '<path d="M14 2v6h6"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "arrow": '<path d="M7 17 17 7"/><path d="M7 7h10v10"/>',
}


def tile_slug(tile: dict) -> str:
    ascii_label = unicodedata.normalize("NFKD", tile["label"]).encode("ascii", "ignore").decode()
    return "tile-" + ascii_label.lower().replace(" ", "-")


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def icon(name: str, x: float, y: float, size: float, stroke: str, sw: float = 2) -> str:
    # The transform lives on an inner <g>: animation classes go on the outer wrapper only.
    return (f'<g transform="translate({x:.1f} {y:.1f}) scale({size / 24:.4f})" fill="none" stroke="{stroke}" '
            f'stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</g>')


class Card:
    def __init__(self, slug: str, width: int, title: str, base: float, glow: str = PRIMARY):
        self.slug, self.w, self.title, self.base, self.glow = slug, width, title, base, glow
        self.parts: list[str] = []
        self.delays: set[int] = set()
        self.chars: dict[int, set[str]] = {400: set(), 600: set()}
        self.n = 0

    # animation helpers -------------------------------------------------------
    def dcls(self, t: float) -> str:
        ms = int(round(t * 1000))
        self.delays.add(ms)
        return f"d{ms}"

    def next_delay(self) -> float:
        t = self.base + 0.2 + self.n * 0.07
        self.n += 1
        return t

    def wrap_anim(self, markup: str, anim: str = "rise", delay: float | None = None) -> str:
        t = self.next_delay() if delay is None else delay
        return f'<g class="{anim} {self.dcls(t)}">{markup}</g>'

    # drawing helpers ---------------------------------------------------------
    def add(self, markup: str, anim: str | None = "rise", delay: float | None = None) -> None:
        self.parts.append(self.wrap_anim(markup, anim, delay) if anim else markup)

    def text(self, x: float, y: float, s: str, size: float, weight: int = 400, fill: str = FG,
             anchor: str = "start", ls: float = 0.0, anim: str | None = "rise",
             delay: float | None = None, inner: str | None = None) -> None:
        self.chars[weight].update(s)
        attrs = f'x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" fill="{fill}"'
        if anchor != "start":
            attrs += f' text-anchor="{anchor}"'
        if ls:
            attrs += f' letter-spacing="{ls}"'
        body = inner if inner is not None else esc(s)
        self.add(f"<text {attrs}>{body}</text>", anim, delay)

    def rect(self, x, y, w, h, rx=0, fill="none", stroke=None, sw=1, extra="", anim: str | None = "rise",
             delay: float | None = None) -> None:
        a = f'x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" fill="{fill}"'
        if stroke:
            a += f' stroke="{stroke}" stroke-width="{sw}"'
        self.add(f"<rect {a} {extra}/>", anim, delay)

    # output ------------------------------------------------------------------
    def css(self, static: bool) -> str:
        out = []
        for weight, chars in self.chars.items():
            if chars:
                data = FONTS[weight].subset_b64(chars)
                out.append("@font-face{font-family:Inter;font-style:normal;font-weight:%d;"
                           "src:url(data:font/woff2;base64,%s) format('woff2')}" % (weight, data))
        if not static:
            out += [
                "@keyframes fade{from{opacity:0}}",
                "@keyframes rise{from{opacity:0;transform:translateY(10px)}}",
                "@keyframes grow{from{transform:scaleY(0)}}",
                "@keyframes draw{from{transform:scaleX(0)}}",
                "@keyframes pulse{50%{opacity:.3}}",
                ".fade{animation:fade .6s ease-out backwards}",
                ".rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) backwards}",
                ".grow{animation:grow .9s cubic-bezier(.2,.7,.2,1) backwards;transform-box:fill-box;transform-origin:50% 100%}",
                ".draw{animation:draw .8s cubic-bezier(.2,.7,.2,1) backwards;transform-box:fill-box;transform-origin:0 50%}",
                ".pulse{animation:pulse 2.2s ease-in-out infinite}",
            ]
            out += [f".d{ms}{{animation-delay:{ms / 1000:g}s}}" for ms in sorted(self.delays)]
            out.append("@media (prefers-reduced-motion:reduce){*{animation:none!important}}")
        return "".join(out)

    def svg(self, h: float, static: bool) -> str:
        w = self.w
        shell = (
            f'<g>'
            f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1:.1f}" rx="16" fill="{CARD}" stroke="{BORDER}"/>'
            f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1:.1f}" rx="16" fill="url(#glow)"/></g>'
        )
        defs = (
            f'<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PRIMARY}"/>'
            f'<stop offset="1" stop-color="{ACCENT}"/></linearGradient>'
            f'<linearGradient id="gv" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{PRIMARY}"/>'
            f'<stop offset="1" stop-color="{ACCENT}"/></linearGradient>'
            f'<radialGradient id="glow" cx=".5" cy="0" r=".75"><stop offset="0" stop-color="{self.glow}" '
            f'stop-opacity=".16"/><stop offset=".6" stop-color="{self.glow}" stop-opacity="0"/></radialGradient>'
        )
        css = self.css(static)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h:.0f}" viewBox="0 0 {w} {h:.0f}" '
            f'font-family="{FONT_STACK}" role="img" aria-label="{html.escape(self.title)}">'
            f"<title>{esc(self.title)}</title><defs>{defs}</defs><style>{css}</style>"
            f'{shell}{"".join(self.parts)}</svg>\n'
        )


# --------------------------------------------------------------------------- data
def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fmt_date(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def fmt_n(n: int) -> str:
    return f"{n:,}"


def compute_stats(days: list[dict]) -> dict:
    rows = sorted(((dt.date.fromisoformat(d["date"]), int(d["count"])) for d in days))
    counts = [c for _, c in rows]
    i = len(rows) - 1
    if i >= 0 and counts[i] == 0:        # today not contributed yet: the streak is still alive
        i -= 1
    current = 0
    while i >= 0 and counts[i] > 0:
        current += 1
        i -= 1
    longest = run = 0
    for c in counts:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    busiest = max(rows, key=lambda r: (r[1], r[0])) if rows else (dt.date.today(), 0)

    last = rows[-1][0]
    months, y, m = [], last.year, last.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    totals = {k: 0 for k in months}
    for d, c in rows:
        if (d.year, d.month) in totals:
            totals[(d.year, d.month)] += c
    return {
        "rows": rows, "current": current, "longest": longest, "active": sum(1 for c in counts if c > 0),
        "span": len(rows), "busiest_date": busiest[0], "busiest_count": busiest[1],
        "months": months, "totals": totals, "total": sum(counts),
    }


# --------------------------------------------------------------------------- cards
def header(c: Card, label: str, title: str, right: str | None = None) -> None:
    c.text(PAD, 46, label.upper(), 11, 600, PRIMARY, ls=2.2)
    c.text(PAD, 76, title, 22, 600, FG, ls=-0.4)
    if right:
        c.text(c.w - PAD, 76, right, 13, 400, MUTED_FG, anchor="end")


def build_hero(profile: dict, base: float) -> tuple[Card, float]:
    first, last = profile["name"]
    c = Card("hero", FULL_W, f"{first} {last}: {profile['tagline']}", base)
    R = 62
    portrait = load_portrait(profile)
    avail = FULL_W - 2 * PAD - ((2 * R + 40) if portrait else 0)

    pill_text = profile["status"]
    pill_w = FONTS[400].width(pill_text, 12) + 42
    c.chars[400].update(pill_text)
    y0 = 38
    pd = c.next_delay()
    c.add(f'<rect x="{PAD}" y="{y0}" width="{pill_w:.1f}" height="28" rx="14" fill="{BG}" fill-opacity=".6" '
          f'stroke="{BORDER}"/><circle class="pulse" cx="{PAD + 16}" cy="{y0 + 14}" r="3.5" fill="{PRIMARY}"/>',
          delay=pd)
    c.text(PAD + 29, y0 + 18.5, pill_text, 12, 400, MUTED_FG, delay=pd)

    size, ls = 56, -1.4
    name_y = y0 + 28 + 68
    gx = PAD + FONTS[600].width(first + " ", size, ls)
    gw = FONTS[600].width(last, size, ls)
    gradient = (f'<defs><linearGradient id="gn" gradientUnits="userSpaceOnUse" x1="{gx:.0f}" y1="{name_y - 48}" '
                f'x2="{gx + gw:.0f}" y2="{name_y}"><stop offset="0" stop-color="{PRIMARY}"/>'
                f'<stop offset="1" stop-color="{ACCENT}"/></linearGradient></defs>')
    c.parts.append(gradient)
    c.chars[600].update(first + " " + last)
    c.add(f'<text x="{PAD}" y="{name_y}" font-size="{size}" font-weight="600" fill="{FG}" letter-spacing="{ls}">'
          f'{esc(first)} <tspan fill="url(#gn)">{esc(last)}</tspan></text>')
    c.rect(PAD, name_y + 20, 64, 3, rx=1.5, fill="url(#g)", anim="draw")

    y = name_y + 56
    for line in wrap(profile["tagline"], 17, avail):
        c.text(PAD, y, line, 17, 400, MUTED_FG)
        y += 26
    h = max(y - 26 + 40, 2 * R + 76)

    if portrait:
        mime, b64 = portrait
        cx, cy = FULL_W - PAD - R, h / 2
        c.parts.append(f'<defs><clipPath id="pc"><circle cx="{cx}" cy="{cy}" r="{R}"/></clipPath></defs>')
        c.add(f'<circle cx="{cx}" cy="{cy}" r="{R + 6}" fill="none" stroke="url(#g)" stroke-width="3"/>'
              f'<image href="data:{mime};base64,{b64}" x="{cx - R}" y="{cy - R}" width="{2 * R}" height="{2 * R}" '
              f'preserveAspectRatio="xMidYMid slice" clip-path="url(#pc)"/>', "fade", c.base + 0.35)
    return c, h


def load_portrait(profile: dict) -> tuple[str, str] | None:
    candidates = [profile.get("portrait", "")] + [f"assets/portrait.{e}" for e in ("jpg", "jpeg", "png", "webp")]
    for rel in candidates:
        p = ROOT / rel if rel else None
        if p and p.is_file():
            if p.stat().st_size > 90_000:
                print(f"warning: {rel} is {p.stat().st_size // 1024} KB; resize it to ~300px to keep the card small",
                      file=sys.stderr)
            mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}[
                p.suffix.lstrip(".").lower()]
            return mime, base64.b64encode(p.read_bytes()).decode()
    return None


def build_tile(tile: dict, base: float) -> tuple[Card, float]:
    c = Card(tile_slug(tile), TILE_W, f"{tile['label']}: {tile['title']}", base)
    h, px = 148, 22
    c.add(f'<rect x="{px}" y="22" width="40" height="40" rx="10" fill="url(#g)"/>'
          + icon(tile["icon"], px + 10, 32, 20, BG, 2))
    c.add(icon("arrow", TILE_W - px - 18, 24, 18, MUTED_FG, 2))
    c.text(px, 90, tile["label"].upper(), 11, 600, PRIMARY, ls=2.0)
    c.text(px, 112, tile["title"], 18, 600, FG, ls=-0.3)
    c.text(px, 132, tile["sub"], 12, 400, MUTED_FG)
    return c, h


def build_activity(stats: dict, base: float) -> tuple[Card, float]:
    c = Card("activity", FULL_W, f"Contributions per month. Current streak {stats['current']} days, "
             f"{stats['active']} active days, busiest day {stats['busiest_count']} contributions.", base)
    header(c, "Activity", "Contributions per month", f"{fmt_n(stats['total'])} in the last {stats['span']} days")

    gap, top, bh = 14, 100, 84
    bw = (FULL_W - 2 * PAD - 2 * gap) / 3
    boxes = [
        ("CURRENT STREAK", str(stats["current"]), plural(stats["current"], "day"),
         f"Longest: {stats['longest']} {plural(stats['longest'], 'day')}"),
        ("ACTIVE DAYS", str(stats["active"]), f"of {stats['span']}", f"{round(100 * stats['active'] / max(1, stats['span']))}% of days"),
        ("BUSIEST DAY", str(stats["busiest_count"]), plural(stats["busiest_count"], "contribution"),
         fmt_date(stats["busiest_date"])),
    ]
    for i, (label, value, unit, sub) in enumerate(boxes):
        x = PAD + i * (bw + gap)
        d = c.next_delay()
        c.rect(x, top, bw, bh, rx=12, fill=BG, stroke=BORDER, extra='fill-opacity=".55"', delay=d)
        c.text(x + 18, top + 26, label, 10, 600, MUTED_FG, ls=1.6, delay=d + 0.05)
        c.text(x + 18, top + 58, value, 30, 600, PRIMARY, ls=-0.8, delay=d + 0.1)
        vw = FONTS[600].width(value, 30, -0.8)
        c.text(x + 18 + vw + 7, top + 58, unit, 13, 400, MUTED_FG, delay=d + 0.1)
        c.text(x + bw - 18, top + 58, sub, 12, 400, MUTED_FG, anchor="end", delay=d + 0.15)

    # monthly bars
    plot_top, plot_h = top + bh + 44, 118
    base_y = plot_top + plot_h
    grid_delay = c.next_delay()
    for frac in (0, 0.5, 1):
        gy = base_y - plot_h * frac
        c.add(f'<line x1="{PAD}" x2="{FULL_W - PAD}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="{BORDER}" '
              f'stroke-opacity="{1 if frac == 0 else .55}" stroke-dasharray="{"0" if frac == 0 else "3 5"}"/>',
              "fade", grid_delay)
    slot = (FULL_W - 2 * PAD) / 12
    peak = max(stats["totals"].values()) or 1
    bar_w = min(36, slot * 0.58)
    start = c.next_delay()
    for i, key in enumerate(stats["months"]):
        val = stats["totals"][key]
        cx = PAD + slot * (i + 0.5)
        bar_h = 0 if val == 0 else max(3, plot_h * val / peak)
        t = start + i * 0.07
        if bar_h:
            c.rect(cx - bar_w / 2, base_y - bar_h, bar_w, bar_h, rx=6, fill="url(#gv)", anim="grow", delay=t)
        c.text(cx, base_y - bar_h - 9, str(val), 12, 600, FG if val else MUTED_FG, anchor="middle", delay=t + 0.25)
        c.text(cx, base_y + 20, MONTHS[key[1] - 1], 12, 400, MUTED_FG, anchor="middle", delay=t + 0.1)
        if i == 0 or key[1] == 1:
            c.text(cx, base_y + 35, str(key[0]), 10, 400, MUTED_FG, anchor="middle", delay=t + 0.1)
    return c, base_y + 56


def build_repo(repo: dict, base: float, height: float | None = None) -> tuple[Card, float]:
    colour = TAG_COLOURS.get(repo["tag"], PRIMARY)
    c = Card(f"repo-{repo['slug']}", HALF_W, f"{repo['name']}: {repo['blurb']}", base, glow=colour)
    pad, inner = 24, HALF_W - 48
    lines = wrap(repo["blurb"], 13, inner)[:4]
    # stack chips
    chips, x, y = [], 0.0, 0.0
    for tech in repo["stack"]:
        cw = FONTS[400].width(tech, 11) + 20
        if x and x + cw > inner:
            x, y = 0.0, y + 30
        chips.append((tech, x, y, cw))
        x += cw + 8
    chips_h = (y + 22) if chips else 0
    blurb_end = 112 + (len(lines) - 1) * 19
    chips_top = blurb_end + 20
    needed = chips_top + chips_h + 20 + 48
    h = max(needed, height or 0)

    tag = repo["tag"].upper()
    tag_w = FONTS[600].width(tag, 10, 1.3) + 22
    c.rect(pad, 24, tag_w, 22, rx=11, fill=colour, stroke=colour, sw=1,
           extra='fill-opacity=".12" stroke-opacity=".45"')
    c.text(pad + 11, 39, tag, 10, 600, colour, ls=1.3)
    if repo.get("period"):
        c.text(HALF_W - pad, 39, repo["period"], 11, 400, MUTED_FG, anchor="end")
    c.text(pad, 82, repo["name"], 21, 600, FG, ls=-0.4)
    for i, line in enumerate(lines):
        c.text(pad, 112 + i * 19, line, 13, 400, MUTED_FG)
    for tech, cx, cy, cw in chips:
        t = c.next_delay()
        c.chars[400].update(tech)
        c.add(f'<rect x="{pad + cx:.1f}" y="{chips_top + cy:.1f}" width="{cw:.1f}" height="22" rx="11" fill="{BG}" '
              f'fill-opacity=".6" stroke="{BORDER}"/><text x="{pad + cx + cw / 2:.1f}" y="{chips_top + cy + 15:.1f}" '
              f'font-size="11" font-weight="400" fill="{MUTED_FG}" text-anchor="middle">{esc(tech)}</text>', delay=t)
    fy = h - 48
    c.add(f'<line x1="{pad}" x2="{HALF_W - pad}" y1="{fy}" y2="{fy}" stroke="{BORDER}"/>', "fade", c.next_delay())
    label = "View repository"
    c.text(pad, fy + 29, label, 12.5, 600, PRIMARY)
    c.add(icon("arrow", pad + FONTS[600].width(label, 12.5) + 6, fy + 17.5, 15, PRIMARY, 2.2))
    return c, h


def build_graph(stats: dict, base: float) -> tuple[Card, float]:
    rows = stats["rows"]
    c = Card("graph", FULL_W, f"Contribution graph: {fmt_n(stats['total'])} contributions in the last year", base)
    header(c, "Contribution graph", f"{fmt_n(stats['total'])} contributions in the last year")

    offset = (rows[0][0].weekday() + 1) % 7               # Sunday = row 0
    n_cols = math.ceil((offset + len(rows)) / 7)
    left = PAD + 30
    pitch = (FULL_W - PAD - left) / n_cols
    cell = pitch - 3.2
    top = 118

    nz = sorted(cnt for _, cnt in rows if cnt > 0)
    q = lambda p: nz[min(len(nz) - 1, int(p * len(nz)))] if nz else 0
    t1, t2, t3 = q(.25), q(.5), q(.75)
    level = lambda n: 0 if n == 0 else 1 if n <= t1 else 2 if n <= t2 else 3 if n <= t3 else 4

    for r, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.text(PAD, top + r * pitch + cell - 1, name, 10, 400, MUTED_FG, delay=base + 0.3)

    cols: dict[int, list[str]] = {}
    month_marks, prev_month, prev_col = [], None, -9
    for idx, (d, cnt) in enumerate(rows):
        col, row = (offset + idx) // 7, (offset + idx) % 7
        cols.setdefault(col, []).append(
            f'<rect x="{left + col * pitch:.1f}" y="{top + row * pitch:.1f}" width="{cell:.1f}" '
            f'height="{cell:.1f}" rx="2.5" fill="{HEAT[level(cnt)]}"/>')
        if row == 0 or idx == 0:
            if d.month != prev_month and col - prev_col >= 3:
                month_marks.append((col, MONTHS[d.month - 1]))
                prev_col = col
            prev_month = d.month
    for col, name in month_marks:
        c.text(left + col * pitch, top - 10, name, 10, 400, MUTED_FG, delay=base + 0.3)
    for col in sorted(cols):
        c.parts.append(f'<g class="fade {c.dcls(base + 0.3 + col * 0.016)}">{"".join(cols[col])}</g>')

    grid_bottom = top + 7 * pitch
    ly = grid_bottom + 28
    x = FULL_W - PAD
    c.text(x, ly + 9, "More", 11, 400, MUTED_FG, anchor="end", delay=base + 1.2)
    x -= FONTS[400].width("More", 11) + 8
    for lv in range(4, -1, -1):
        x -= 13
        c.rect(x, ly, 11, 11, rx=2.5, fill=HEAT[lv], delay=base + 1.2)
        x -= 3
    c.text(x - 5, ly + 9, "Less", 11, 400, MUTED_FG, anchor="end", delay=base + 1.2)
    return c, ly + 40


# --------------------------------------------------------------------------- output
def write_card(card: Card, h: float, out: Path, static: bool) -> None:
    (out / f"{card.slug}.svg").write_text(card.svg(h, static), encoding="utf-8")


def write_preview(profile: dict, out_name: str, static: bool) -> None:
    folder = out_name
    tiles = "\n".join(
        f'<a href="{t["url"]}"><img src="{folder}/{tile_slug(t)}.svg" width="32.55%" alt="{html.escape(t["label"])}"></a>'
        for t in profile["tiles"])
    repos = "\n".join(
        f'<a href="{r["url"]}"><img src="{folder}/repo-{r["slug"]}.svg" width="49.5%" alt="{html.escape(r["name"])}"></a>'
        for r in profile["repos"])
    page = f"""<!doctype html><meta charset="utf-8"><title>Profile cards preview{' (static)' if static else ''}</title>
<style>body{{margin:0;background:#0d1117;font:14px system-ui}}.wrap{{max-width:830px;margin:0 auto;padding:24px;text-align:center}}
img{{vertical-align:top;margin:2px 0}}a{{display:inline}}</style>
<div class="wrap">
<img src="{folder}/hero.svg" width="100%" alt="hero">
{tiles}
<img src="{folder}/activity.svg" width="100%" alt="activity">
{repos}
<img src="{folder}/graph.svg" width="100%" alt="contribution graph">
</div>
"""
    name = "preview-static.html" if static else "preview.html"
    (ROOT / name).write_text(page, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("opts", nargs="*", help="static=1 renders the final frame without animation")
    ap.add_argument("--static", action="store_true")
    ap.add_argument("--profile", default=str(ROOT / "data" / "profile.json"))
    ap.add_argument("--data", default=str(ROOT / "data" / "contributions.json"))
    ap.add_argument("--out", default=None, help="output folder (default: cards/, or cards-static/ with static=1)")
    args = ap.parse_args()
    static = args.static or "static=1" in args.opts or os.environ.get("STATIC", "").lower() in ("1", "true", "yes")

    FONTS.update(load_fonts())
    profile = load_json(Path(args.profile))
    contrib = load_json(Path(args.data))
    if contrib.get("demo"):
        print("WARNING: data/contributions.json is DEMO data; run scripts/fetch_contributions.py for real numbers",
              file=sys.stderr)
    stats = compute_stats(contrib["days"])

    folder = args.out or ("cards-static" if static else "cards")
    out = ROOT / folder
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.svg"):
        old.unlink()

    # Top-to-bottom schedule: each card starts a little after the one above it. The card backgrounds are
    # always visible; only their content animates, so a card that scrolls into view late (browsers start an
    # embedded SVG's animation when it is first painted) shows an empty card for a moment, never a gap.
    cards: list[tuple[Card, float]] = [build_hero(profile, 0.0)]
    for i, tile in enumerate(profile["tiles"]):
        cards.append(build_tile(tile, 0.35 + i * 0.1))
    cards.append(build_activity(stats, 0.75))
    repos = profile["repos"]
    # first pass for the tallest repo card, second pass so each row of cards lines up
    tallest = max(build_repo(r, 0)[1] for r in repos)
    for i, repo in enumerate(repos):
        cards.append(build_repo(repo, 1.1 + (i // 2) * 0.25 + (i % 2) * 0.1, tallest))
    cards.append(build_graph(stats, 1.8))

    for card, h in cards:
        write_card(card, h, out, static)
    write_preview(profile, folder, static)
    kb = sum(p.stat().st_size for p in out.glob("*.svg")) / 1024
    print(f"{'static' if static else 'animated'}: wrote {len(cards)} cards to {folder}/ ({kb:.0f} KB total)")


if __name__ == "__main__":
    main()
