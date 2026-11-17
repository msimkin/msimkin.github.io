#!/usr/bin/env python3
"""Static site generator for univariate.org.

    python3 tools/site.py build [--drafts] [--out _site]   build the site
    python3 tools/site.py serve [--port 8000]               preview with drafts, rebuilds on save
    python3 tools/site.py new <slug> [--title "..."]        start a draft in drafts/<slug>.md
    python3 tools/site.py publish <slug>                    move drafts/<slug>.md to posts/, date it today

Posts are Markdown files with a small front matter block:

    ---
    title: Counting roots: the univariate Schwartz–Zippel lemma
    date: 2026-11-02
    summary: optional one-line excerpt (otherwise the first sentence is used)
    ---

Math: $...$ and \\(...\\) inline, $$...$$ and \\[...\\] display (rendered by KaTeX in the browser).
Theorem-like blocks:  ::: lemma [optional title]  ...  :::   (also theorem, corollary, proposition,
claim, definition, remark, example, and proof, which gets a closing box).
Code: fenced blocks, optionally with a file name: ```python horner.py
"""
import argparse, datetime as dt, hashlib, html, json, os, re, shutil, sys, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import markdown
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://univariate.org"
TAGLINE = ("Cryptography researcher at Flashbots and tenure-track professor at Aarhus University. "
           "I write here about cryptography, algorithms, and the math in between.")
BIO = ("I am a tenure-track professor at Aarhus University and a cryptography researcher at Flashbots. "
       "I received my PhD from Aarhus University in 2019, advised by Ivan Damgård and co-advised by "
       "Claudio Orlandi, and stayed on as a postdoctoral researcher with Kasper Green Larsen. On the "
       "industry side, I was a cryptography researcher at the Ethereum Foundation before joining Flashbots.")
CONTACT = "firstname at univariate.org"
# shown under the email, in this order: (label, url). Add or remove lines freely.
LINKS = [
    ("x.com/Simk1n", "https://x.com/Simk1n"),
    ("github.com/msimkin", "https://github.com/msimkin"),
    ("dblp.org/pid/58/2782sb", "https://dblp.org/pid/58/2782sb.html"),
]
HOME_POSTS = 5          # posts shown on the home page
THEOREM_KINDS = ["theorem", "lemma", "corollary", "proposition", "claim", "definition", "remark", "example"]
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
FULL_MONTHS = ("January February March April May June July August September October November "
               "December").split()

esc = html.escape


# ---------------------------------------------------------------------------------------------
# posts

class Post:
    def __init__(self, path, draft):
        self.path, self.draft, self.slug = path, draft, path.stem
        meta, self.source = parse_front_matter(path.read_text(encoding="utf-8"), path)
        self.title = meta.get("title") or self.slug.replace("-", " ").capitalize()
        d = meta.get("date")
        try:
            self.date = dt.date.fromisoformat(d) if d else dt.date.today()
        except ValueError:
            sys.exit(f"{path}: date must look like 2026-11-02, not {d!r}")
        self.summary = meta.get("summary") or ""
        self.body, self.has_math, words = render_markdown(self.source)
        self.minutes = max(1, round(words / 220))
        ex = self.summary or first_paragraph_text(self.body)
        if self.summary:
            self.ex1, self.ex2 = ex, ""
        else:
            sentences = split_sentences(ex)
            self.ex1 = sentences[0] if sentences else ""
            self.ex2 = sentences[1] if len(sentences) > 1 else ""
        self.url = f"/writing/{self.slug}/"
        self.excerpt_has_math = "\\(" in self.ex1 + self.ex2


def parse_front_matter(text, path):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        sys.exit(f"{path}: front matter starts with --- but never ends")
    meta = {}
    for line in text[3:end].strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            sys.exit(f"{path}: front matter line without a colon: {line!r}")
        key, value = line.split(":", 1)          # split on the first colon only
        meta[key.strip().lower()] = value.strip().strip('"').strip("'")
    return meta, text[end + 4:].lstrip("\n")


def load_posts(drafts):
    posts = [Post(p, False) for p in sorted((ROOT / "posts").glob("*.md"))]
    if drafts and (ROOT / "drafts").is_dir():
        posts += [Post(p, True) for p in sorted((ROOT / "drafts").glob("*.md"))]
    slugs = [p.slug for p in posts]
    dup = {s for s in slugs if slugs.count(s) > 1}
    if dup:
        sys.exit(f"Two posts share a file name: {', '.join(sorted(dup))}")
    return sorted(posts, key=lambda p: (p.date, p.slug), reverse=True)


# ---------------------------------------------------------------------------------------------
# markdown with math, theorem blocks and highlighted code

FENCE = re.compile(r"^(?P<ind>[ \t]*)(?P<f>```+|~~~+)[ \t]*(?P<info>[^\n`]*)\n(?P<code>.*?)^(?P=ind)(?P=f)[ \t]*$", re.M | re.S)
INLINE_CODE = re.compile(r"(`+)(?!`).+?(?<!`)\1(?!`)", re.S)
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
INLINE_MATH = re.compile(r"\\\((.+?)\\\)|(?<![\\$\w])\$(?![\s$])((?:\\\$|[^$\n])+?)(?<![\s\\])\$(?![\w$])", re.S)
BLOCK_OPEN = re.compile(r"^:::[ \t]*([A-Za-z]+)[ \t]*(.*)$")


class Stash:
    """Swap fragments for markdown-proof placeholders and put them back after conversion."""
    def __init__(self):
        self.items = {}

    def put(self, kind, value):
        key = f"XQ{kind}{len(self.items)}QX"
        self.items[key] = value
        return key

    def restore(self, text):
        for _ in range(4):                        # blocks can contain math and code placeholders
            new = re.sub(r"<p>(XQ(?:BLOCK|DMATH)\d+QX)</p>", r"\1", text)
            new = re.sub(r"XQ[A-Z]+\d+QX", lambda m: self.items.get(m.group(0), m.group(0)), new)
            if new == text:
                break
            text = new
        return text


def highlight_code(code, info):
    parts = info.split()
    lang = parts[0] if parts else ""
    name = " ".join(parts[1:])
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    out = highlight(code, lexer, HtmlFormatter(linespans="L"))
    out = re.search(r"<pre[^>]*>(.*)</pre>", out, re.S).group(1).replace("<span></span>", "", 1)
    out = re.sub(r'<span id="L-\d+">', '<span class="ln">', out).replace("\n</span>", "</span>").rstrip("\n")
    bar = ""
    if lang or name:
        bar = f'<div class="code-bar" aria-hidden="true"><span>{esc(name)}</span><span>{esc(lang)}</span></div>'
    return f'<div class="code">{bar}<pre><code>{out}</code></pre></div>'


def render_markdown(src):
    stash = Stash()
    counters = {"no": 0, "kinds": {}}

    # 1. fenced code blocks (anywhere, also inside ::: blocks)
    src = FENCE.sub(lambda m: "\n" + m.group("ind") + stash.put("BLOCK", highlight_code(m.group("code"), m.group("info").strip())) + "\n", src)

    # 2. math, outside inline code spans
    def stash_math(text):
        text = text.replace("\\$", stash.put("TXT", "$"))
        text = DISPLAY_MATH.sub(lambda m: stash.put("DMATH", "\\[" + esc((m.group(1) or m.group(2)).strip()) + "\\]"), text)
        return INLINE_MATH.sub(lambda m: stash.put("MATH", "\\(" + esc((m.group(1) or m.group(2)).strip()) + "\\)"), text)
    pieces, last = [], 0
    for m in INLINE_CODE.finditer(src):
        pieces.append(stash_math(src[last:m.start()]))
        pieces.append(m.group(0))
        last = m.end()
    pieces.append(stash_math(src[last:]))
    src = "".join(pieces)
    has_math = "XQMATH" in src or "XQDMATH" in src

    # 3. theorem-like blocks
    def convert(text):
        md = markdown.Markdown(extensions=["footnotes", "tables", "toc", "sane_lists", "smarty", "attr_list"],
                               extension_configs={"toc": {"permalink": False}})
        return md.convert(text)

    lines, out, i = src.split("\n"), [], 0
    while i < len(lines):
        m = BLOCK_OPEN.match(lines[i])
        if not m:
            out.append(lines[i]); i += 1; continue
        kind, title = m.group(1).lower(), m.group(2).strip()
        j = i + 1
        while j < len(lines) and lines[j].strip() != ":::":
            j += 1
        inner = convert("\n".join(lines[i + 1:j]))
        if kind == "proof":
            label = f'<i class="lbl">{esc(title) if title else "Proof"}.</i> '
            inner = re.sub(r"(</p>)(?![\s\S]*</p>)", '<span class="qed">∎</span>\\1', inner, count=1) if "</p>" in inner else inner + '<span class="qed">∎</span>'
            cls = "proof"
        else:
            n = counters["kinds"].get(kind, 0) + 1
            counters["kinds"][kind] = n
            name = kind.capitalize() if kind in THEOREM_KINDS else esc(m.group(1))
            label = f'<b class="lbl">{name} {n}{" (" + esc(title) + ")" if title else ""}.</b> '
            cls = "theorem"
        inner = inner.replace("<p>", "<p>" + label, 1) if inner.startswith("<p>") else label + inner
        out.append("\n" + stash.put("BLOCK", f'<div class="{cls}">{inner}</div>') + "\n")
        i = j + 1
    src = "\n".join(out)

    body = convert(src)
    # number the sections (## in markdown)
    def number(m):
        counters["no"] += 1
        return f'{m.group(1)}<span class="no">{counters["no"]}</span>'
    body = re.sub(r"(<h2[^>]*>)", number, body)
    body = re.sub(r"^<p>", '<p class="lede">', body, count=1)
    body = stash.restore(body)
    text = re.sub(r"<[^>]+>", " ", body)
    words = len(re.findall(r"[A-Za-z0-9]+", re.sub(r"\\\[.*?\\\]|\\\(.*?\\\)", " x ", text, flags=re.S)))
    return body, has_math, words


def first_paragraph_text(body):
    m = re.search(r"<p[^>]*>(.*?)</p>", body, re.S)
    if not m:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()


def split_sentences(text):
    """Split at sentence ends that are not inside \\( ... \\)."""
    out, start, depth, i = [], 0, 0, 0
    while i < len(text):
        if text.startswith("\\(", i):
            depth += 1; i += 2; continue
        if text.startswith("\\)", i):
            depth = max(0, depth - 1); i += 2; continue
        if depth == 0 and text[i] in ".?!" and (i + 1 == len(text) or text[i + 1] == " "):
            out.append(text[start:i + 1].strip()); start = i + 1
        i += 1
    if text[start:].strip():
        out.append(text[start:].strip())
    return out


# ---------------------------------------------------------------------------------------------
# pieces of pages

def fmt_short(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def fmt_long(d):
    return f"{d.day} {FULL_MONTHS[d.month - 1]} {d.year}"


def nowrap_title(title):
    """Keep compounds joined by a dash (Schwartz–Zippel) on one line."""
    return re.sub(r"(\S+[–-]\S+)", r'<span class="nw">\1</span>', esc(title))


def positions(n_items, lead, more):
    """Grid position classes for n_items cells (+ a trailing 'more' cell), in the three- and the
    two-module layout. With lead, the first item is the large newest post."""
    cls = []
    # three modules
    if lead and n_items == 1:
        three = ["c3-l s3-3"]
    elif lead and n_items == 2:
        three = ["c3-l s3-2", "c3-r"]
    elif lead:
        three = ["c3-l s3-2 tall", "c3-r", "c3-r"] + [["c3-l", "c3-m", "c3-r"][(i - 3) % 3] for i in range(3, n_items)]
    else:
        three = [["c3-l", "c3-m", "c3-r"][i % 3] for i in range(n_items)]
    if more:
        if lead and n_items <= 1:
            k = 0
        elif lead and n_items == 2:
            k = 0
        elif lead:
            k = (n_items - 3) % 3
        else:
            k = n_items % 3
        three.append(["c3-l", "c3-m", "c3-r"][k] + ("" if k == 2 else f" s3-{3 - k}"))
    # two modules
    two = []
    for i in range(n_items):
        if lead and i == 0:
            two.append("s2-2")
        else:
            j = (i - 1) % 2 if lead else i % 2
            two.append("c2-l" if j == 0 else "c2-r")
    if more:
        j = ((n_items - 1) % 2) if lead else (n_items % 2)
        two.append("c2-l s2-2" if j == 0 else "c2-r")
    for a, b in zip(three, two):
        cls.append(f"{a} {b}")
    return add_bottoms(cls)


def add_bottoms(cls):
    """Cells with no cell directly below them (in a given layout) draw their own bottom hairline:
    nb3 / nb2 / nb1 for the three-, two- and one-module layouts. The grid itself has no bottom
    border, so an unfinished last row ends exactly where its cells end."""
    def spans(c, layout):
        t = c.split()
        if layout == 3:
            cs = 3 if "s3-3" in t else 2 if "s3-2" in t else 1
            return cs, (2 if "tall" in t else 1)
        return (2 if "s2-2" in t else 1), 1

    out = [c for c in cls]
    for ncols in (3, 2):
        occupied, placed, r0, c0 = set(), [], 0, 0
        for c in cls:
            cs, rs = spans(c, ncols)
            r, col = r0, c0
            while True:
                if col + cs > ncols:
                    r, col = r + 1, 0
                    continue
                if all((r + dr, col + dc) not in occupied for dr in range(rs) for dc in range(cs)):
                    break
                col += 1
            occupied |= {(r + dr, col + dc) for dr in range(rs) for dc in range(cs)}
            placed.append((r, col, cs, rs))
            r0, c0 = r, col + cs
        for i, (r, col, cs, rs) in enumerate(placed):
            if any((r + rs, col + dc) not in occupied for dc in range(cs)):
                out[i] += f" nb{ncols}"
    if out:
        out[-1] += " nb1"
    return out


def post_cell(post, classes, lead=False):
    meta = f'<time datetime="{post.date.isoformat()}">{fmt_short(post.date)}</time><span class="sep">·</span>{post.minutes} min'
    if post.draft:
        meta += '<span class="sep">·</span><span class="draft">draft</span>'
    ex = esc(post.ex1)
    if lead and post.ex2:
        ex += f' <span class="x2">{esc(post.ex2)}</span>'
    ex_html = f'<p class="ex">{ex}</p>' if ex else ""
    return (f'<li class="cell{" lead" if lead else ""} {classes}"><p class="m">{meta}</p>'
            f'<a class="ti" href="{post.url}">{nowrap_title(post.title)}</a>{ex_html}</li>')


def more_cell(classes, href, label):
    return f'<li class="cell more {classes}"><p class="m">&nbsp;</p><a class="ti" href="{href}">{label} →</a></li>'


def preprint_id(url):
    """'http://ia.cr/2025/1384' -> 'ePrint 2025/1384', 'https://arxiv.org/abs/2504.13757' -> 'arXiv 2504.13757'."""
    m = re.search(r"(?:ia\.cr|eprint\.iacr\.org)/(\d{4}/\d+)", url)
    if m:
        return "ePrint " + m.group(1)
    m = re.search(r"arxiv\.org/abs/([\w.]+)", url)
    return "arXiv " + m.group(1) if m else "Preprint"


def pub_cell(pub, classes, in_year_group=False):
    """in_year_group: the group label already shows the year, so the venue is shown without it."""
    link = pub["links"][0]["url"] if pub["links"] else None
    venue = pub["venue"]
    if venue == "In Submission":
        # under the "In submission" label, say where to read it instead of repeating the label
        venue = preprint_id(link) if link else ""
    elif in_year_group and pub.get("year"):
        venue = re.sub(r"\s*%d$" % pub["year"], "", venue) or venue
    title = esc(pub["title"])
    ti = f'<a class="ti" href="{esc(link)}">{title}</a>' if link else f'<span class="ti">{title}</span>'
    extra = ""
    if len(pub["links"]) > 1:
        extra = '<p class="links">' + "".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in pub["links"]) + "</p>"
    return (f'<li class="cell {classes}"><p class="m">{esc(venue) or "&nbsp;"}</p>{ti}'
            f'<p class="ex">{esc(pub["authors"])}</p>{extra}</li>')


def fillers(n_items, lead):
    """Empty cells that complete the last row, so every row has its hairlines and crosses.
    Returns (needed in the three-module layout, needed in the two-module layout)."""
    if lead:                     # lead layouts without a 'more' cell only occur for 1-3 posts
        return 0, (n_items - 1) % 2
    return (3 - n_items % 3) % 3, n_items % 2


def with_fillers(cells, n_items, lead):
    """If the last row is unfinished, append one invisible marker cell right after the last cell.
    It draws only the registration crosses where that row's lines end (no lines, no box), and is
    shown only in the layouts where the row is unfinished."""
    f3, f2 = fillers(n_items, lead)
    if not (f3 or f2):
        return cells
    pos = positions(n_items + 1, lead=lead, more=False)[-1]
    pos = " ".join(t for t in pos.split() if not t.startswith("nb"))
    show = (" f3" if f3 else "") + (" f2" if f2 else "")
    cells.append(f'<li class="cell static filler{show} {pos}" aria-hidden="true"></li>')
    return cells


def writing_grid(posts, home):
    if not posts:
        return ('      <div class="empty"><p class="ti">Nothing published yet.</p>'
                '<p class="ex">Notes on cryptography, algorithms, and the math in between are on their way.</p></div>')
    shown = posts[:HOME_POSTS] if home else posts
    more = home and len(posts) >= 4
    pos = positions(len(shown), lead=True, more=more)
    cells = [post_cell(p, pos[i], lead=(i == 0)) for i, p in enumerate(shown)]
    if more:
        cells.append(more_cell(pos[-1], "/writing/", "All writing"))
    else:
        cells = with_fillers(cells, len(shown), lead=True)
    return '      <ul class="mod w">\n        ' + "\n        ".join(cells) + "\n      </ul>"


def plain_grid(cells_fn, items, tag="ul", cls="mod"):
    pos = positions(len(items), lead=False, more=False)
    cells = with_fillers([cells_fn(it, pos[i]) for i, it in enumerate(items)], len(items), lead=False)
    return f'      <{tag} class="{cls}">\n        ' + "\n        ".join(cells) + f"\n      </{tag}>"


def nav(current):
    items = [("writing", "/writing/"), ("publications", "/publications/"), ("about", "/#about")]
    cur = ' aria-current="page"'
    return "".join(f'<a href="{href}"{cur if name == current else ""}><span class="s">/</span>{name}</a>'
                   for name, href in items)


# ---------------------------------------------------------------------------------------------
# rendering

TEMPLATES = ROOT / "templates"
MATH_HEAD = ('<link rel="stylesheet" href="/static/katex/katex.min.css">\n'
             '<script defer src="/static/katex/katex.min.js"></script>\n'
             '<script defer src="/static/katex/auto-render.min.js" onload="univariateMath()"></script>')


def fill(template, **values):
    text = (TEMPLATES / template).read_text(encoding="utf-8")
    def sub(m):
        key = m.group(1)
        if key not in values:
            sys.exit(f"templates/{template}: no value for {{{{ {key} }}}}")
        return str(values[key])
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, text)


class Builder:
    def __init__(self, out, drafts, reload=False):
        self.out, self.drafts, self.reload = Path(out), drafts, reload
        css = (ROOT / "static/css/site.css").read_bytes() + (ROOT / "static/js/site.js").read_bytes()
        self.version = hashlib.sha1(css).hexdigest()[:10]

    def page(self, rel, content, title, description, current="", math=False, og_type="website", og_title=None):
        url = BASE_URL + "/" + rel.replace("index.html", "")
        reload_js = ""
        if self.reload:
            reload_js = ("<script>(function(){var v=null;setInterval(function(){fetch('/__reload',{cache:'no-store'})"
                         ".then(function(r){return r.text()}).then(function(t){if(v&&t!==v)location.reload();v=t})"
                         ".catch(function(){})},700)})();</script>")
        doc = fill("base.html", title=esc(title), description=esc(description), url=url, og_type=og_type,
                   og_title=esc(og_title or title), version=self.version, math_head=MATH_HEAD if math else "",
                   nav=nav(current), content=content, year=dt.date.today().year, reload=reload_js)
        path = self.out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(doc, encoding="utf-8")

    def build(self):
        posts = load_posts(self.drafts)
        pubs = json.loads((ROOT / "data/publications.json").read_text(encoding="utf-8"))
        tmp = self.out.with_name(self.out.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        final_out, self.out = self.out, tmp
        shutil.copytree(ROOT / "static", tmp / "static")
        (tmp / ".nojekyll").write_text("")

        # home
        selected = [p for p in pubs if p.get("selected")] or [p for p in pubs if p["venue"] != "In Submission"][:5]
        pos = positions(len(selected), lead=False, more=True)
        cells = [pub_cell(p, pos[i]) for i, p in enumerate(selected)] + [more_cell(pos[-1], "/publications/", "All publications")]
        pubs_html = '      <ol class="mod p">\n        ' + "\n        ".join(cells) + "\n      </ol>"
        home = fill("home.html", tagline="".join(f'<span class="tl">{esc(t)}</span>' for t in split_sentences(TAGLINE)), writing=writing_grid(posts, home=True),
                    publications=pubs_html, bio=esc(BIO), contact=esc(CONTACT),
                    links="".join(f'<li><a href="{esc(u)}" rel="me">{esc(l)}</a></li>' for l, u in LINKS))
        self.page("index.html", home, "Mark Simkin", TAGLINE,
                  math=any(p.excerpt_has_math for p in posts[:HOME_POSTS]))

        # writing archive, grouped by year
        if posts:
            parts = []
            for year in sorted({p.date.year for p in posts}, reverse=True):
                group = [p for p in posts if p.date.year == year]
                parts.append(f'      <p class="group"><b>{year}</b></p>')
                parts.append(plain_grid(lambda p, c: post_cell(p, c), group, cls="mod w"))
            archive = "\n".join(parts)
        else:
            archive = writing_grid([], home=False)
        self.page("writing/index.html", fill("list.html", name="writing", intro="", content=archive),
                  "Writing · Mark Simkin", "Writing by Mark Simkin on cryptography, algorithms, and the math in between.",
                  current="writing", math=any(p.excerpt_has_math for p in posts))

        # posts
        for i, post in enumerate(posts):
            newer = posts[i - 1] if i > 0 else None
            older = posts[i + 1] if i + 1 < len(posts) else None
            foot_cells = []
            if older:
                foot_cells.append(("older", older))
            if newer:
                foot_cells.append(("newer", newer))
            pos = positions(len(foot_cells), lead=False, more=True)
            items = [f'<li class="cell {pos[k]}"><p class="m">{label}</p><a class="ti" href="{p.url}">{nowrap_title(p.title)}</a></li>'
                     for k, (label, p) in enumerate(foot_cells)]
            items.append(more_cell(pos[-1], "/writing/", "All writing"))
            foot = '    <ul class="mod post-foot-grid">\n      ' + "\n      ".join(items) + "\n    </ul>"
            meta = f'<time datetime="{post.date.isoformat()}">{fmt_long(post.date)}</time><span class="sep">·</span>{post.minutes} min read'
            if post.draft:
                meta += '<span class="sep">·</span><span class="draft">draft, not published</span>'
            content = fill("post.html", slug=esc(post.slug), title_html=nowrap_title(post.title), meta=meta,
                           body=post.body, post_foot=foot)
            self.page(f"writing/{post.slug}/index.html", content, f"{post.title} · Mark Simkin",
                      post.summary or post.ex1 or TAGLINE, current="writing", math=post.has_math,
                      og_type="article", og_title=post.title)

        # publications, grouped: in submission, then by year
        groups = [("In submission", [p for p in pubs if p["venue"] == "In Submission"])]
        for year in sorted({p["year"] for p in pubs if p["year"]}, reverse=True):
            groups.append((str(year), [p for p in pubs if p["year"] == year and p["venue"] != "In Submission"]))
        parts = []
        for label, items in groups:
            if items:
                parts.append(f'      <p class="group"><b>{esc(label)}</b></p>')
                parts.append(plain_grid(lambda p, c, y=(label != "In submission"): pub_cell(p, c, in_year_group=y),
                                        items, tag="ol", cls="mod p"))
        self.page("publications/index.html",
                  fill("list.html", name="publications", intro="", content="\n".join(parts)),
                  "Publications · Mark Simkin", "Publications by Mark Simkin.", current="publications")

        # 404, sitemap, robots
        self.page("404.html", fill("404.html"), "Not found · Mark Simkin", "Page not found.")
        urls = ["/", "/writing/", "/publications/"] + [p.url for p in posts if not p.draft]
        (tmp / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                         + "".join(f"  <url><loc>{BASE_URL}{u}</loc></url>\n" for u in urls) + "</urlset>\n")
        (tmp / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n")

        if final_out.exists():
            shutil.rmtree(final_out)
        tmp.rename(final_out)
        self.out = final_out
        return posts


# ---------------------------------------------------------------------------------------------
# commands

def cmd_build(args):
    posts = Builder(args.out, args.drafts).build()
    print(f"built {args.out}/ with {len(posts)} post(s){' (drafts included)' if args.drafts else ''}")


def watched_state():
    files = []
    for d in ["posts", "drafts", "templates", "static", "data"]:
        if (ROOT / d).is_dir():
            files += [p for p in (ROOT / d).rglob("*") if p.is_file()]
    return {str(p): p.stat().st_mtime for p in files}


def cmd_serve(args):
    out = ROOT / "_preview"
    builder_state = {"version": "0"}

    def rebuild():
        try:
            Builder(out, drafts=True, reload=True).build()
            builder_state["version"] = str(time.time())
            print(time.strftime("%H:%M:%S"), "rebuilt")
        except SystemExit as e:
            print("build error:", e)

    rebuild()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(out), **kw)

        def do_GET(self):
            if self.path == "/__reload":
                body = builder_state["version"].encode()
                self.send_response(200); self.send_header("Content-Type", "text/plain")
                self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)
                return
            super().do_GET()

        def send_error(self, code, message=None, explain=None):
            page = out / "404.html"
            if code == 404 and page.exists():
                body = page.read_bytes()
                self.send_response(404); self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers(); self.wfile.write(body)
            else:
                super().send_error(code, message, explain)

        def log_message(self, *a):
            pass

    def watch():
        state = watched_state()
        while True:
            time.sleep(0.4)
            now = watched_state()
            if now != state:
                state = now
                rebuild()

    threading.Thread(target=watch, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"preview with drafts at http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}/  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()


def cmd_new(args):
    slug = re.sub(r"[^a-z0-9-]+", "-", args.slug.lower()).strip("-")
    if not slug:
        sys.exit("give the post a name, e.g. make new name=counting-roots")
    path = ROOT / "drafts" / f"{slug}.md"
    if path.exists() or (ROOT / "posts" / f"{slug}.md").exists():
        sys.exit(f"{slug}.md already exists")
    path.parent.mkdir(exist_ok=True)
    title = args.title or slug.replace("-", " ").capitalize()
    text = (TEMPLATES / "post-template.md").read_text(encoding="utf-8")
    path.write_text(text.replace("{{ title }}", title).replace("{{ date }}", dt.date.today().isoformat()), encoding="utf-8")
    print(f"created {path.relative_to(ROOT)}  (open it, then run: make preview)")


def cmd_publish(args):
    src = ROOT / "drafts" / f"{args.slug}.md"
    if not src.exists():
        sys.exit(f"no draft named drafts/{args.slug}.md")
    dst = ROOT / "posts" / src.name
    if dst.exists():
        sys.exit(f"posts/{src.name} already exists")
    text = src.read_text(encoding="utf-8")
    today = dt.date.today().isoformat()
    if re.search(r"(?m)^date:", text):
        text = re.sub(r"(?m)^date:.*$", f"date: {today}", text, count=1)
    Post(src, True)                               # fail early if the post does not render
    dst.parent.mkdir(exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    src.unlink()
    print(f"moved to posts/{src.name}, dated {today}.\nTo put it online:\n  git add posts/{src.name} && git commit -m \"Publish {args.slug}\" && git push")


def main():
    ap = argparse.ArgumentParser(description="univariate.org site generator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--drafts", action="store_true"); b.add_argument("--out", default=str(ROOT / "_site"))
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8000); s.add_argument("--host", default="127.0.0.1")
    n = sub.add_parser("new"); n.add_argument("slug"); n.add_argument("--title")
    p = sub.add_parser("publish"); p.add_argument("slug")
    args = ap.parse_args()
    {"build": cmd_build, "serve": cmd_serve, "new": cmd_new, "publish": cmd_publish}[args.cmd](args)


if __name__ == "__main__":
    main()
