#!/usr/bin/env python3
"""Draws the animated terminal for the GitHub profile README from terminal.txt.

    pip install fonttools brotli
    python build_terminal.py              # writes dist/terminal-dark.svg and dist/terminal-light.svg
    python build_terminal.py --show       # also prints the terminal as plain text

The workflow in .github/workflows/terminal.yml runs this whenever terminal.txt
changes and every Monday, and puts the two pictures on the `output` branch.
"""

import argparse
import base64
import datetime as dt
import html
import io
import json
import os
import random
import re
import sys
import urllib.request

from fontTools import subset
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))

# Who and where -----------------------------------------------------------------

GITHUB_USER = "OrosmanAS"  # whose numbers fill in {{uptime}}, {{repos}}, ...
PROMPT = "<v><b>oros@bigbrain</b></v> <g>~</g> <d>$</d> "
TITLE = "oros@bigbrain: ~"

# Colours -------------------------------------------------------------------------
# o: output, w: typed commands, v/g/d: the <v> <g> <d> tags in terminal.txt,
# s1-s4: the <1> to <4> tags, shades of violet from light to deep for drawings.

THEMES = {
    "dark": dict(card="#13111a", bar="#1a1724", line="#2c2839", dots="#3b3649",
                 o="#b4aec6", w="#efecf7", v="#b69cff", g="#f0c674", d="#6f6983",
                 s1="#d8cbff", s2="#b69cff", s3="#9376f0", s4="#7254d6",
                 cursor="#b69cff"),
    "light": dict(card="#ffffff", bar="#f6f8fa", line="#d0d7de", dots="#d0d7de",
                  o="#424a53", w="#1f2328", v="#6c47e0", g="#9a6700", d="#8c959f",
                  s1="#b9a5ff", s2="#9678f5", s3="#7655e3", s4="#5835c4",
                  cursor="#6c47e0"),
}
COLOURS = ("o", "w", "v", "g", "d", "s1", "s2", "s3", "s4")

# Layout, in pixels -----------------------------------------------------------------

WIDTH = 800
FONT_SIZE = 14
LINE_H = 22
PAD_X = 28
BAR_H = 36
PAD_TOP = 16
PAD_BOTTOM = 20
RADIUS = 10

# Timing, in seconds -----------------------------------------------------------------

START = 0.5   # before the first prompt
THINK = 0.45  # prompt up, before the first key
KEY = 0.06    # average time per key
ENTER = 0.3   # after the last key, before the output
LINE = 0.03   # between output lines
NEXT = 0.6    # after the output, before the next prompt
BLINK = 1.1   # cursor blink at the last prompt
FLIP = .01    # each "appear" animation lasts this long, so it starts this much early

FONTS = {400: "fonts/JetBrainsMono-Regular.woff2", 700: "fonts/JetBrainsMono-Bold.woff2"}

# Block characters are drawn as shapes so they join up without seams:
# (x, y, width, height) as fractions of a cell, plus opacity for the shades.
BLOCKS = {
    "█": (0, 0, 1, 1, 1), "▀": (0, 0, 1, .5, 1), "▄": (0, .5, 1, .5, 1),
    "▌": (0, 0, .5, 1, 1), "▐": (.5, 0, .5, 1, 1),
    "░": (0, 0, 1, 1, .25), "▒": (0, 0, 1, 1, .5), "▓": (0, 0, 1, 1, .75),
}
# Box-drawing characters are drawn as 1px lines from the middle of the cell
# to the edges named here (up, down, left, right), so trees join up too.
BOX = {"─": "lr", "│": "ud", "┌": "dr", "┐": "dl", "└": "ur", "┘": "ul",
       "├": "udr", "┤": "udl", "┬": "dlr", "┴": "ulr", "┼": "udlr"}
DRAWN = set(BLOCKS) | set(BOX)
TAG = re.compile(r"</?([vgdwb1-4])>")
LIVE = re.compile(r"\{\{(\w+)\}\}")


def font_metrics():
    font = TTFont(os.path.join(HERE, FONTS[400]))
    upem = font["head"].unitsPerEm
    scale = FONT_SIZE / upem
    return font["hmtx"]["a"][0] * scale, font["OS/2"].sCapHeight * scale


CW, CAP = font_metrics()                        # cell width, capital height
COLS = int((WIDTH - 2 * PAD_X) // CW)
BASE = (LINE_H + CAP) / 2                       # baseline, from the top of a row
CUR_TOP, CUR_H = BASE - 0.95 * FONT_SIZE, 1.22 * FONT_SIZE


# Reading terminal.txt ---------------------------------------------------------------

def styled(text, colour):
    """Turns tagged text into cells: (character, colour, bold)."""
    cells, colours, bold, pos = [], [colour], 0, 0
    for m in TAG.finditer(text):
        cells += [(ch, colours[-1], bold > 0) for ch in text[pos:m.start()]]
        closing, tag = m.group(0).startswith("</"), m.group(1)
        if tag == "b":
            bold = max(0, bold - 1) if closing else bold + 1
        elif closing:
            colours = colours[:-1] or [colour]
        else:
            colours.append("s" + tag if tag.isdigit() else tag)
        pos = m.end()
    return cells + [(ch, colours[-1], bold > 0) for ch in text[pos:]]


def fit(cells, width, where, warnings):
    if len(cells) <= width:
        return cells
    text = "".join(c[0] for c in cells)
    warnings.append(f"{where} is {len(cells)} characters, cut to {width}: {text[:40]}...")
    return cells[:width - 1] + [("…",) + cells[width - 1][1:]]


def read_terminal(path, live, warnings):
    """Returns rows of ("cmd" | "out", cells)."""
    prompt_len = len(styled(PROMPT, "w"))
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f.read().splitlines(), 1):
            if raw.startswith("#"):
                continue
            line = LIVE.sub(lambda m: value(live, m.group(1), n), raw.rstrip().expandtabs(4))
            line = re.sub(r"\b1 (star|follower|repo)s\b", r"1 \1", line)
            if line == "$" or line.startswith("$ "):
                rows.append(("cmd", fit(styled(line[2:], "w"), COLS - prompt_len, f"Line {n}", warnings)))
            else:
                rows.append(("out", fit(styled(line, "o"), COLS, f"Line {n}", warnings)))
    while rows and rows[-1] == ("out", []):
        rows.pop()
    while rows and rows[0] == ("out", []):
        rows.pop(0)
    return rows


def value(live, name, n):
    if live is None:
        raise SystemExit(f"Line {n} uses {{{{{name}}}}}, but the live numbers couldn't be read.")
    if name not in live:
        raise SystemExit(f"Line {n}: there's no live number called {{{{{name}}}}}. "
                         f"Known ones: {', '.join('{{%s}}' % k for k in live)}")
    return live[name]


# Live numbers ---------------------------------------------------------------------

def live_numbers():
    def get(path):
        req = urllib.request.Request("https://api.github.com" + path, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "profile-terminal"})
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    user = get(f"/users/{GITHUB_USER}")
    repos = get(f"/users/{GITHUB_USER}/repos?per_page=100&type=owner")
    joined = dt.date.fromisoformat(user["created_at"][:10])
    return {
        "uptime": age(joined, dt.datetime.now(dt.timezone.utc).date()),
        "repos": str(user["public_repos"]),
        "stars": str(sum(r["stargazers_count"] for r in repos if not r["fork"])),
        "followers": str(user["followers"]),
    }


def age(start, today):
    months = (today.year - start.year) * 12 + today.month - start.month - (today.day < start.day)
    years, months = divmod(months, 12)
    parts = [f"{n} {unit}{'s' * (n != 1)}" for n, unit in ((years, "year"), (months, "month")) if n]
    if not parts:
        days = (today - start).days
        parts = [f"{days} day{'s' * (days != 1)}"]
    return ", ".join(parts)


# Drawing ---------------------------------------------------------------------------

def esc(text):
    return html.escape(text, quote=True)


def num(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def box_lines(sides, x, top):
    """The 1px lines of one box-drawing cell, as (x, y, width, height, opacity)."""
    cx, cy = x + CW / 2, top + LINE_H / 2
    lines = {"l": (x, cy - .5, CW / 2 + .5, 1), "r": (cx - .5, cy - .5, CW / 2 + .5, 1),
             "u": (cx - .5, top, 1, LINE_H / 2 + .5), "d": (cx - .5, cy - .5, 1, LINE_H / 2 + .9)}
    return [lines[s] + (1,) for s in sides]


def shapes(cells, col0, top):
    """Rectangles for the block and box-drawing characters in a row."""
    out, i = [], 0
    while i < len(cells):
        ch, colour, _ = cells[i]
        if ch not in DRAWN:
            i += 1
            continue
        j = i + 1
        while j < len(cells) and cells[j][:2] == (ch, colour):
            j += 1
        x = PAD_X + (col0 + i) * CW
        if ch == "─":                               # a run of these is one line
            boxes = [(x, top + LINE_H / 2 - .5, (j - i) * CW, 1, 1)]
        elif ch in BOX:
            boxes = [line for k in range(j - i) for line in box_lines(BOX[ch], x + k * CW, top)]
        else:
            bx, by, bw, bh, alpha = BLOCKS[ch]
            # Full-width blocks merge into one shape; a hair of overlap hides seams.
            runs = [(0, j - i)] if bw == 1 else [(k, 1) for k in range(j - i)]
            boxes = [(x + (k + bx) * CW, top + by * LINE_H,
                      (n - 1 + bw) * CW + (.4 if bx + bw == 1 else 0),
                      bh * LINE_H + (.4 if by + bh == 1 else 0), alpha) for k, n in runs]
        for bx, by, bw, bh, alpha in boxes:
            op = "" if alpha == 1 else f' opacity="{alpha}"'
            out.append(f'<rect class="{colour}" x="{num(bx)}" y="{num(by)}" '
                       f'width="{num(bw)}" height="{num(bh)}"{op}/>')
        i = j
    return out


def words(cells, col0):
    """Runs of ordinary characters with one style: (column, text, colour, bold)."""
    out, i = [], 0
    while i < len(cells):
        ch, colour, bold = cells[i]
        if ch == " " or ch in DRAWN:
            i += 1
            continue
        j = i + 1
        while (j < len(cells) and cells[j][1:] == (colour, bold)
               and cells[j][0] != " " and cells[j][0] not in DRAWN):
            j += 1
        out.append((col0 + i, "".join(c[0] for c in cells[i:j]), colour, bold))
        i = j
    return out


def tspans(cells, col0):
    return "".join(
        f'<tspan x="{num(PAD_X + col * CW)}" class="{colour}{" b" if bold else ""}">{esc(text)}</tspan>'
        for col, text, colour, bold in words(cells, col0))


def row_group(cells, col0, top, at):
    parts = shapes(cells, col0, top)
    spans = tspans(cells, col0)
    if spans:
        parts.append(f'<text y="{num(top + BASE)}">{spans}</text>')
    if not parts:
        return ""
    return f'<g class="a" style="animation-delay:{at - FLIP:.3f}s">{"".join(parts)}</g>'


def timeline(rows):
    """When each row appears, and when each typed character lands."""
    t, first = START, True
    when, keys = {}, {}
    for r, (kind, cells) in enumerate(rows):
        if kind == "cmd":
            t = t if first else t + NEXT
            first = False
            when[r] = t
            rng = random.Random(f"{r}:{''.join(c[0] for c in cells)}")
            k, times = t + THINK, []
            for i, cell in enumerate(cells):
                if i:
                    pause = 0.08 if cells[i - 1][0] == " " else 0
                    k += max(0.025, KEY + rng.uniform(-0.035, 0.045) + pause)
                times.append(round(k, 3))                # whole milliseconds, as written out
            keys[r] = times
            t = (times[-1] if times else t + THINK) + ENTER
        else:
            t += LINE
            when[r] = t
    return when, keys, t + NEXT


def build(rows, theme, fonts_css):
    c = THEMES[theme]
    prompt = styled(PROMPT, "w")
    when, keys, last = timeline(rows)
    total_rows = len(rows) + 2                      # a blank row, then the waiting prompt
    height = BAR_H + PAD_TOP + total_rows * LINE_H + PAD_BOTTOM

    body, frames = [], []

    def cursor(r, top, typed):
        x0 = PAD_X + len(prompt) * CW
        geo = f'x="{num(x0)}" y="{num(top + CUR_TOP)}" width="{num(CW)}" height="{num(CUR_H)}"'
        p = when.get(r, last)
        on = f"on {FLIP}s step-end {p - FLIP:.3f}s both"
        if typed is None:                           # the last prompt: blink for ever
            anim = f"{on},blink {BLINK}s linear {p:.3f}s infinite"
            return f'<rect class="k last" {geo} style="animation:{anim}"/>'
        off = (typed[-1] if typed else p + THINK) + ENTER
        anim = on
        if typed:
            span = typed[-1] - p + .001
            stops = [f"0%{{transform:translateX(0)}}"]
            stops += [f"{(t - p) / span * 100:.4f}%{{transform:translateX({num((i + 1) * CW)}px)}}"
                      for i, t in enumerate(typed)]
            stops.append(f"100%{{transform:translateX({num(len(typed) * CW)}px)}}")
            frames.append(f"@keyframes m{r}{{{''.join(stops)}}}")
            anim += f",m{r} {span:.3f}s step-end {p:.3f}s both"
        anim += f",off {FLIP}s step-end {off:.3f}s forwards"
        return f'<rect class="k" {geo} style="animation:{anim}"/>'

    for r, (kind, cells) in enumerate(rows):
        top = BAR_H + PAD_TOP + r * LINE_H
        if kind == "out":
            body.append(row_group(cells, 0, top, when[r]))
            continue
        body.append(row_group(prompt, 0, top, when[r]))
        for i, (ch, colour, bold) in enumerate(cells):
            if ch == " ":
                continue
            x = PAD_X + (len(prompt) + i) * CW
            body.append(f'<text class="a {colour}{" b" if bold else ""}" x="{num(x)}" '
                        f'y="{num(top + BASE)}" style="animation-delay:{keys[r][i] - FLIP:.3f}s">{esc(ch)}</text>')
        body.append(cursor(r, top, keys[r]))

    top = BAR_H + PAD_TOP + (total_rows - 1) * LINE_H
    body.append(row_group(prompt, 0, top, last))
    body.append(cursor(len(rows) + 1, top, None))

    plain = "\n".join(("".join(ch for ch, *_ in prompt) if kind == "cmd" else "")
                      + "".join(ch for ch, *_ in cells) for kind, cells in rows)
    css = (fonts_css
           + "text{font-family:TermMono,ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace;"
           + f"font-size:{FONT_SIZE}px;font-variant-ligatures:none;fill:{c['o']}}}"
           + "".join(f".{k}{{fill:{c[k]}}}" for k in COLOURS)
           + "g rect{shape-rendering:crispEdges}"
           + f".b{{font-weight:700}}.k{{fill:{c['cursor']};visibility:hidden}}"
           + f".t{{font-size:12px;fill:{c['d']}}}"
           + f".a{{animation:on {FLIP}s step-end both}}"
           + "@keyframes on{from{visibility:hidden}to{visibility:visible}}"
           + "@keyframes off{from,to{visibility:hidden}}"
           + "@keyframes blink{0%,50%{opacity:1}50.01%,100%{opacity:0}}"
           + "".join(frames)
           + "@media (prefers-reduced-motion:reduce){.a,.k{animation:none!important}.k.last{visibility:visible}}")
    w, h, rad = WIDTH, height, RADIUS
    bar = (f"M.5 {BAR_H}V{rad + .5}A{rad} {rad} 0 0 1 {rad + .5} .5H{w - rad - .5}"
           f"A{rad} {rad} 0 0 1 {w - .5} {rad + .5}V{BAR_H}Z")
    return "".join([
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-labelledby="title desc">',
        f'<title id="title">Terminal: {esc(TITLE)}</title><desc id="desc">{esc(plain)}</desc>',
        f"<style>{css}</style>",
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="{rad}" fill="{c["card"]}"/>',
        f'<path d="{bar}" fill="{c["bar"]}"/>',
        f'<path d="M.5 {BAR_H + .5}H{w - .5}" stroke="{c["line"]}"/>',
        "".join(f'<circle cx="{x}" cy="{BAR_H / 2}" r="5.5" fill="{c["dots"]}"/>' for x in (22, 40, 58)),
        f'<text class="t" x="{w / 2}" y="{num(BAR_H / 2 + 4.3)}" text-anchor="middle">{esc(TITLE)}</text>',
        *body,
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="{rad}" fill="none" stroke="{c["line"]}"/>',
        "</svg>\n",
    ])


# Fonts -----------------------------------------------------------------------------

def embedded_fonts(rows, warnings):
    """@font-face rules with JetBrains Mono, cut down to the characters in use."""
    used = {400: set(TITLE), 700: set()}
    for _, cells in rows + [("cmd", styled(PROMPT, "w"))]:
        for ch, _, bold in cells:
            if ch != " " and ch not in DRAWN:
                used[700 if bold else 400].add(ch)
    css = ""
    for weight, chars in used.items():
        if not chars:
            continue
        # recalcTimestamp=False keeps the output identical from one build to the next
        font = TTFont(os.path.join(HERE, FONTS[weight]), recalcTimestamp=False)
        missing = sorted(ch for ch in chars if ord(ch) not in font.getBestCmap())
        if missing:
            warnings.append(f"Not in the font, so shown in a fallback font: {' '.join(missing)}")
        opts = subset.Options()
        opts.layout_features = []                   # no ligatures: every character keeps its cell
        opts.hinting = False
        opts.name_IDs = [0, 1, 2, 3, 4, 5, 6, 13, 14]   # keeps the copyright and the OFL licence
        sub = subset.Subsetter(opts)
        sub.populate(unicodes={ord(ch) for ch in chars})
        sub.subset(font)
        font.flavor = "woff2"
        buf = io.BytesIO()
        font.save(buf)
        data = base64.b64encode(buf.getvalue()).decode()
        css += (f"@font-face{{font-family:TermMono;font-weight:{weight};"
                f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")
    return css


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(HERE, "dist"), help="folder for the two SVGs")
    ap.add_argument("--show", action="store_true", help="print the terminal as plain text")
    args = ap.parse_args()

    source = os.path.join(HERE, "terminal.txt")
    with open(source, encoding="utf-8") as f:
        needs_live = bool(LIVE.search(f.read()))
    live = None
    if needs_live:
        try:
            live = live_numbers()
        except Exception as e:  # network or rate limit: stop rather than draw wrong numbers
            raise SystemExit(f"Couldn't read the live numbers from GitHub: {e}")

    warnings = []
    rows = read_terminal(source, live, warnings)
    fonts_css = embedded_fonts(rows, warnings)
    os.makedirs(args.out, exist_ok=True)
    for theme in THEMES:
        svg = build(rows, theme, fonts_css)
        path = os.path.join(args.out, f"terminal-{theme}.svg")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
        print(f"{path}  {len(svg) // 1024} KB")

    _, _, last = timeline(rows)
    print(f"{len(rows)} rows, {COLS} columns, done typing after {last:.1f}s")
    if live:
        print("Live numbers: " + ", ".join(f"{k} = {v}" for k, v in live.items()))
    if args.show:
        prompt = "".join(ch for ch, *_ in styled(PROMPT, "w"))
        for kind, cells in rows:
            print((prompt if kind == "cmd" else "") + "".join(ch for ch, *_ in cells))
    for w in warnings:
        print("Warning: " + w)


if __name__ == "__main__":
    main()
