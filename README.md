# univariate.org

Source of Mark Simkin's website. A small Python script turns Markdown posts into a static site;
GitHub Actions builds and deploys it on every push to `master`.

## Writing

```sh
make new name=counting-roots title="Counting roots"   # creates drafts/counting-roots.md
make preview                                           # http://localhost:8000, rebuilds on save
make preview host=0.0.0.0                              # same, reachable from your phone on the same Wi-Fi
make publish name=counting-roots                       # moves it to posts/ and dates it today
git add posts/counting-roots.md && git commit -m "Publish counting-roots" && git push
```

- `drafts/` is ignored by git: drafts never leave your machine. The preview shows them with a
  "draft" label; the public site does not include them. `make new` starts each draft from
  `templates/post-template.md`, which shows the math and lemma/proof syntax.
- `posts/` holds published posts. Everything in it is public after a push.
- The first `make` run creates a local Python environment in `.venv/` (Markdown and Pygments).

## Post format

```markdown
---
title: Counting roots: the univariate Schwartz–Zippel lemma
date: 2026-11-02
summary: Optional. Otherwise the first sentence of the post is the excerpt.
---

Text with inline math $p \in \mathbb{F}[X]$ and display math:

$$ \Pr[p(r) = q(r)] \le \frac{d}{|\mathbb{F}|} $$

## A numbered section

::: lemma Optional title
Numbered automatically (also theorem, corollary, proposition, claim, definition, remark, example).
:::

::: proof
Ends with a box.
:::
```

A `|` in the title sets where the large post heading breaks on wide screens
(`title: Provable security, random oracles, | and hash functions`); it is ignored on phones and
everywhere else the title appears. Without it, the browser balances the lines itself.

Notes to yourself go in HTML comments (`<!-- remember to cite X -->`): they show in the file but
never on the site, not even in the page source.

Code blocks are fenced as usual, optionally with a file name after the language
(` ```python horner.py `). Also supported: footnotes (`[^1]`), tables, `\$` for a literal dollar sign,
and `\( \)` / `\[ \]` as math delimiters. Math is rendered in the browser with KaTeX (self-hosted in
`static/katex`).

## Other content

- Publications: `data/publications.json`, newest first. The home page shows the entries marked
  `"selected": true` (in list order); remove the flag to keep a paper off the home page. It is always
  listed on /publications/.
- Bio, tagline and contact line: the constants at the top of `tools/site.py`.
- Page structure: `templates/`. Styles: `static/css/site.css`.

## Repository layout

```
posts/        published posts (Markdown)
drafts/       local drafts (git-ignored)
data/         publications.json
templates/    HTML templates
static/       css, js, fonts (Geist Mono), KaTeX, favicon
tools/site.py the generator: build, serve, new, publish
.github/      the deploy workflow
```
