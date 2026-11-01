#!/usr/bin/env python3
"""Build the website from cv.tex.

    python3 build.py              # writes the site into _site/
    python3 build.py --serve      # same, then previews at http://localhost:8000

Reads only the macros that cv.tex defines for content (\\entry, \\pub,
\\sectionhead, skillgroup, ...) plus plain paragraphs and inline formatting.
Standard library only; no installs needed.
"""

import html
import re
import shutil
import urllib.parse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "cv.tex"
STATIC = ROOT / "static"
OUT = ROOT / "_site"
PDF = ROOT / "cv.pdf"

warnings = []


class TexError(Exception):
    pass


# ----------------------------------------------------------------------
#  low-level reading
# ----------------------------------------------------------------------

SRC_RAW = ""      # the file exactly as written, used to report line numbers
IDXMAP = []       # IDXMAP[k] = index in SRC_RAW of character k of the stripped text


def strip_comments(s):
    """Drop TeX comments: a % (not \\%) eats to the end of the line, newline
    included. Also records where each surviving character came from, so errors
    can name the line in the file the author is actually looking at."""
    out, idxmap = [], []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "%" and (i == 0 or s[i - 1] != "\\"):
            j = s.find("\n", i)
            i = n if j < 0 else j + 1
        else:
            out.append(s[i])
            idxmap.append(i)
            i += 1
    return "".join(out), idxmap


def line_of(stripped_index):
    """Line number in the original file for a position in the stripped text."""
    if not IDXMAP:
        return 1
    k = max(0, min(stripped_index, len(IDXMAP) - 1))
    return SRC_RAW.count("\n", 0, IDXMAP[k]) + 1


def preflight(text):
    """Catch the authoring mistakes that pdfLaTeX tolerates but this parser
    cannot, and report them with line numbers instead of a parse error."""
    problems = []
    for m in re.finditer(r"\\href\s*\{", text):
        try:
            url, j = read_group(text, m.end() - 1)
        except TexError:
            continue
        line = line_of(m.start())
        k = skip_ws(text, j)
        if k >= len(text) or text[k] != "{":
            shown = " ".join(url.split())[:48]
            problems.append(
                f"line {line}: \\href{{{shown}}} has only one argument. "
                "It needs two: \\href{url}{link text}. "
                "If you do not have the URL yet, use \\todo{...} instead.")
            continue
        if url != url.strip():
            problems.append(
                f"line {line}: the URL inside \\href starts or ends with a "
                "space, so the link will not work. Remove the space.")
        if re.search(r"\s", url.strip()) and not url.strip().startswith("mailto:"):
            shown = " ".join(url.split())[:48]
            problems.append(
                f"line {line}: the URL \"{shown}\" contains a space. "
                "A URL cannot contain spaces — use \\todo{...} for a placeholder.")
        if "&" in url and "\\&" not in url:
            problems.append(
                f"line {line}: the URL contains a bare & — write it as \\& "
                "or the link will be cut short.")
    return problems


def skip_ws(s, i):
    while i < len(s) and s[i] in " \t\n":
        i += 1
    return i


def read_group(s, i):
    """s[i:] starts (after whitespace) with '{'. Return (inside, index after '}')."""
    i = skip_ws(s, i)
    if i >= len(s) or s[i] != "{":
        snippet = s[max(0, i - 40):i + 40].replace("\n", " ")
        raise TexError(f"expected '{{' near: ...{snippet}...")
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    snippet = s[i:i + 60].replace("\n", " ")
    raise TexError(f"unbalanced braces starting at: {snippet}...")


def read_args(s, i, n):
    args = []
    for _ in range(n):
        a, i = read_group(s, i)
        args.append(a)
    return args, i


# ----------------------------------------------------------------------
#  zero-argument macros from the preamble (\me, \linksep, ...)
# ----------------------------------------------------------------------

def preamble_macros(preamble):
    macros = {}
    for m in re.finditer(r"\\newcommand\{\\([A-Za-z]+)\}\s*\{", preamble):
        try:
            body, _ = read_group(preamble, m.end() - 1)
        except TexError:
            continue
        macros[m.group(1)] = body
    return macros


# ----------------------------------------------------------------------
#  inline TeX -> HTML
# ----------------------------------------------------------------------

MATH = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "epsilon": "ε", "eta": "η", "theta": "θ", "kappa": "κ", "lambda": "λ",
    "mu": "μ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Omega": "Ω",
    "cdot": "·", "times": "×", "pm": "±", "sim": "∼", "approx": "≈",
    "leq": "≤", "geq": "≥", "le": "≤", "ge": "≥", "to": "→",
    "rightarrow": "→", "leftarrow": "←", "circ": "°", "prime": "′",
    "infty": "∞", "degree": "°",
}

# commands whose single argument is rendered as-is (styling dropped)
PASSTHROUGH = {"textsf", "texttt", "textrm", "textsc", "textnormal",
               "MakeUppercase", "mbox", "text", "small", "footnotesize"}

# zero-argument commands to drop silently
DROP = {"noindent", "par", "normalfont", "selectfont", "relax",
        "large", "Large", "small", "footnotesize", "scriptsize", "bfseries",
        "itshape", "ttfamily", "sffamily", "centering", "hfill", "protect"}


def unescape_url(u):
    return re.sub(r"\\([%#_&$~])", r"\1", u.strip())


def conv_math(s, macros):
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+)\s*", s[i:])
            if m:
                name = m.group(1)
                out.append(MATH.get(name, ""))
                if name not in MATH:
                    warnings.append(f"unknown math command \\{name} dropped")
                i += len(m.group(0))
            else:
                out.append(html.escape(s[i + 1:i + 2], quote=False))
                i += 2
        elif c in "^_":
            tag = "sup" if c == "^" else "sub"
            if i + 1 < len(s) and s[i + 1] == "{":
                g, j = read_group(s, i + 1)
            else:
                g, j = s[i + 1:i + 2], i + 2
            out.append(f"<{tag}>{conv_math(g, macros)}</{tag}>")
            i = j
        elif c == "{":
            g, j = read_group(s, i)
            out.append(conv_math(g, macros))
            i = j
        elif c in " \t\n":
            i += 1
        else:
            out.append(html.escape(c, quote=False))
            i += 1
    return "".join(out)


def conv(s, macros, depth=0):
    """Convert a run of inline TeX into HTML."""
    if depth > 20:
        raise TexError("macro expansion too deep")
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]

        if c == "\\":
            # control symbols: \\ \, \  \& \% ...
            if i + 1 < n and not s[i + 1].isalpha():
                ch = s[i + 1]
                i += 2
                if ch == "\\":
                    out.append("<br>")
                    i = skip_ws(s, i)
                    if i < n and s[i] == "[":           # \\[2pt]
                        i = s.index("]", i) + 1
                elif ch == ",":
                    out.append("\u2009")
                elif ch in " \t\n":
                    out.append(" ")
                elif ch == "-":
                    pass
                else:
                    out.append(html.escape(ch, quote=False))
                continue

            m = re.match(r"\\([A-Za-z]+)[ \t\n]*", s[i:])
            name = m.group(1)
            i += len(m.group(0))

            def args(k):
                nonlocal i
                a, i = read_args(s, i, k)
                return a

            if name in ("textit", "emph", "venue"):
                out.append(f"<em>{conv(args(1)[0], macros, depth + 1)}</em>")
            elif name == "textbf":
                out.append(f"<strong>{conv(args(1)[0], macros, depth + 1)}</strong>")
            elif name == "textcolor":
                colour, body = args(2)
                inner = conv(body, macros, depth + 1)
                # the accent colour is meaningful (surname, highlights); keep it
                out.append(f'<span class="accent">{inner}</span>'
                           if colour.strip() == "accent" else inner)
            elif name == "href":
                url, text = args(2)
                url = html.escape(unescape_url(url), quote=True)
                out.append(f'<a href="{url}">{conv(text, macros, depth + 1)}</a>')
            elif name == "url":
                (url,) = args(1)
                u = unescape_url(url)
                out.append(f'<a href="{html.escape(u, quote=True)}">'
                           f"{html.escape(u, quote=False)}</a>")
            elif name == "textsuperscript":
                out.append(f"<sup>{conv(args(1)[0], macros, depth + 1)}</sup>")
            elif name == "textsubscript":
                out.append(f"<sub>{conv(args(1)[0], macros, depth + 1)}</sub>")
            elif name == "todo":
                out.append(f'<span class="todo">[{conv(args(1)[0], macros, depth + 1)}]</span>')
            elif name == "pubtag":
                out.append(f' <span class="tag">{conv(args(1)[0], macros, depth + 1)}</span>')
            elif name == "tagline":
                out.append(f' <span class="tag tag-live">{conv(args(1)[0], macros, depth + 1)}</span>')
            elif name in ("quad", "qquad"):
                out.append('<span class="sep"></span>')
            elif name == "textcopyright":
                out.append("©")
            elif name in ("ldots", "dots"):
                out.append("…")
            elif name == "LaTeX":
                out.append("LaTeX")
            elif name in ("vspace", "hspace", "needspace"):
                args(1)
            elif name in PASSTHROUGH:
                if s[skip_ws(s, i):skip_ws(s, i) + 1] == "{":
                    out.append(conv(args(1)[0], macros, depth + 1))
            elif name in DROP:
                pass
            elif name in macros:
                out.append(conv(macros[name], macros, depth + 1))
            else:
                warnings.append(f"unknown command \\{name}; kept its text only")
                while i < n and s[skip_ws(s, i):skip_ws(s, i) + 1] == "{":
                    out.append(conv(args(1)[0], macros, depth + 1))

        elif c == "{":
            g, j = read_group(s, i)
            out.append(conv(g, macros, depth + 1))
            i = j
        elif c == "}":
            i += 1
        elif c == "$":
            k = s.find("$", i + 1)
            if k < 0:
                raise TexError("unclosed $ in: " + s[i:i + 50])
            out.append(conv_math(s[i + 1:k], macros))
            i = k + 1
        elif c == "~":
            out.append("\u00a0")
            i += 1
        elif s.startswith("---", i):
            out.append("—")
            i += 3
        elif s.startswith("--", i):
            out.append("–")
            i += 2
        elif s.startswith("``", i):
            out.append("“")
            i += 2
        elif s.startswith("''", i):
            out.append("”")
            i += 2
        elif c in " \t\n":
            out.append(" ")
            i = skip_ws(s, i)
        else:
            out.append(html.escape(c, quote=False))
            i += 1

    text = "".join(out)
    text = re.sub(r" {2,}", " ", text)
    return text.strip() if depth == 0 else text


def plain(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


# ----------------------------------------------------------------------
#  block-level scan of the document body
# ----------------------------------------------------------------------

BLOCK = {"photo": 1, "eyebrow": 1, "name": 1, "position": 1, "links": 1,
         "webpage": 2, "callout": 1, "sectionhead": 2, "entry": 3,
         "pub": 3, "pubgroup": 1, "footer": 1}
IGNORE = {"vspace": 1, "needspace": 1, "noindent": 0, "par": 0,
          "begingroup": 0, "endgroup": 0, "medskip": 0, "smallskip": 0,
          "bigskip": 0, "newpage": 0, "clearpage": 0, "pagebreak": 0,
          "sectionrule": 0, "hfill": 0, "vfill": 0, "hspace": 1,
          "centering": 0, "raggedright": 0, "raggedleft": 0}


def scan(body):
    blocks, buf, i, n = [], [], 0, len(body)

    def flush():
        text = "".join(buf).strip()
        if text:
            blocks.append(("para", text))
        buf.clear()

    while i < n:
        c = body[i]
        if c == "\\":
            m = re.match(r"\\([A-Za-z]+\*?)", body[i:])
            if not m:
                buf.append(body[i:i + 2])
                i += 2
                continue
            name = m.group(1)
            after = i + len(m.group(0))

            if name in BLOCK:
                flush()
                a, i = read_args(body, after, BLOCK[name])
                blocks.append((name, *a))
            elif name in IGNORE:
                flush()
                _, i = read_args(body, after, IGNORE[name])
            elif name == "begin":
                (env,), j = read_args(body, after, 1)
                flush()
                if env == "multicols":
                    _, i = read_args(body, j, 1)
                elif env == "minipage":
                    # pure page layout: ignore the wrapper, keep the content
                    k = skip_ws(body, j)
                    if k < len(body) and body[k] == "[":      # optional [t]
                        k = body.index("]", k) + 1
                    _, i = read_args(body, k, 1)              # {width}
                elif env == "skillgroup":
                    (title,), j = read_args(body, j, 1)
                    end = body.find(r"\end{skillgroup}", j)
                    if end < 0:
                        raise TexError(f"skillgroup '{title}' has no \\end{{skillgroup}}")
                    inner, items, k = body[j:end], [], 0
                    while True:
                        k = inner.find(r"\skill", k)
                        if k < 0:
                            break
                        (item,), k = read_args(inner, k + len(r"\skill"), 1)
                        items.append(item)
                    blocks.append(("skillgroup", title, items))
                    i = end + len(r"\end{skillgroup}")
                else:
                    raise TexError(f"environment '{env}' is not supported on the website")
            elif name == "end":
                (env,), i = read_args(body, after, 1)
                flush()
            else:
                buf.append(m.group(0))
                i = after
        elif c == "{":
            _, j = read_group(body, i)
            buf.append(body[i:j])
            i = j
        elif c == "\n" and re.match(r"\n[ \t]*\n", body[i:]):
            flush()
            i = skip_ws(body, i)
        else:
            buf.append(c)
            i += 1
    flush()
    return blocks


# ----------------------------------------------------------------------
#  HTML rendering
# ----------------------------------------------------------------------

def render_header(blocks, macros, have_photo):
    photo, text = "", []
    for b in blocks:
        kind = b[0]
        if kind == "photo":
            if have_photo(b[1]):
                fn = b[1].strip()
                src = urllib.parse.quote(fn)          # spaces -> %20, etc.
                photo = (f'<img class="photo" src="{html.escape(src, quote=True)}" '
                         f'alt="Portrait of {{NAME}}">')
                if fn != src:
                    warnings.append(
                        f"photo '{fn}' has spaces or odd characters in its name; "
                        "it works, but a plain name like photo.jpg is safer "
                        "(LaTeX dislikes spaces in image filenames).")
        elif kind == "eyebrow":
            text.append(f'<p class="eyebrow">{conv(b[1], macros)}</p>')
        elif kind == "name":
            text.append(f"<h1>{conv(b[1], macros)}</h1>")
        elif kind == "position":
            text.append(f'<p class="position">{conv(b[1], macros)}</p>')
        elif kind == "links":
            text.append(f'<p class="links">{conv(b[1], macros)}</p>')
        elif kind == "para":
            text.append(f"<p>{conv(b[1], macros)}</p>")
    return photo, "\n".join(text)


def render_body(blocks, macros):
    out, open_section, skills_open = [], False, False

    def close_skills():
        nonlocal skills_open
        if skills_open:
            out.append("</div>")
            skills_open = False

    for b in blocks:
        kind = b[0]
        if kind != "skillgroup":
            close_skills()

        if kind == "sectionhead":
            if open_section:
                out.append("</section>")
            out.append("<section>")
            open_section = True
            out.append(f'<p class="eyebrow">{conv(b[1], macros)}</p>')
            if b[2].strip():
                out.append(f"<h2>{conv(b[2], macros)}</h2>")
        elif kind == "para":
            out.append(f"<p>{conv(b[1], macros)}</p>")
        elif kind == "callout":
            out.append(f'<div class="callout">{conv(b[1], macros)}</div>')
        elif kind == "pubgroup":
            out.append(f'<p class="group-head">{conv(b[1], macros)}</p>')
        elif kind == "entry":
            title, date, sub = (conv(x, macros) for x in b[1:])
            sub_html = f'<p class="entry-sub">{sub}</p>' if sub else ""
            out.append(
                '<div class="entry"><div class="entry-body">'
                f"<h3>{title}</h3>{sub_html}</div>"
                f'<div class="entry-meta">{date}</div></div>')
        elif kind == "pub":
            num, title, auth = (conv(x, macros) for x in b[1:])
            out.append(
                f'<div class="pub"><div class="pub-num">{num}</div><div>'
                f'<p class="pub-title">{title}</p>'
                f'<p class="pub-authors">{auth}</p></div></div>')
        elif kind == "skillgroup":
            if not skills_open:
                out.append('<div class="skills">')
                skills_open = True
            items = "".join(f"<li>{conv(x, macros)}</li>" for x in b[2])
            out.append(f'<div class="skill-group"><h3>{conv(b[1], macros)}</h3>'
                       f"<ul>{items}</ul></div>")
        else:
            warnings.append(f"\\{kind} is not used on this part of the website")

    close_skills()
    if open_section:
        out.append("</section>")
    return "\n".join(out)


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<link rel="stylesheet" href="style.css">
</head>
<body>
<main>
<nav>{nav}</nav>
<header class="masthead">
<div class="masthead-text">
{header}
</div>
{photo}
</header>
{body}
{footer}
</main>
</body>
</html>
"""


def build():
    if not SOURCE.exists():
        raise TexError(f"{SOURCE.name} not found next to build.py")
    global SRC_RAW, IDXMAP
    SRC_RAW = SOURCE.read_text(encoding="utf-8")
    raw, IDXMAP = strip_comments(SRC_RAW)

    problems = preflight(raw)
    if problems:
        raise TexError("\n  " + "\n  ".join(problems))

    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", raw, re.S)
    if not m:
        raise TexError("could not find \\begin{document} ... \\end{document}")
    macros = preamble_macros(raw[:m.start()])
    body = m.group(1)

    header_blocks, pages, footer = [], [], ""
    for b in scan(body):
        if b[0] == "webpage":
            pages.append({"slug": b[1].strip(), "label": b[2].strip(), "blocks": []})
        elif b[0] == "footer":
            footer = conv(b[1], macros)
        elif pages:
            pages[-1]["blocks"].append(b)
        else:
            header_blocks.append(b)

    if not pages:
        raise TexError("no \\webpage{...}{...} markers found in cv.tex")
    if pages[0]["slug"] != "index":
        warnings.append("first \\webpage is not 'index'; the site will have no home page")

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    if STATIC.exists():
        for f in STATIC.iterdir():
            if f.is_file():
                shutil.copy2(f, OUT / f.name)

    def have_photo(fn):
        fn = fn.strip()
        ok = (STATIC / fn).exists()
        if not ok:
            imgs = sorted(f.name for f in STATIC.iterdir()
                          if f.is_file() and f.suffix.lower() in
                          (".jpg", ".jpeg", ".png", ".gif", ".webp")) \
                   if STATIC.exists() else []
            if imgs:
                warnings.append(
                    f"\\photo{{{fn}}} asks for static/{fn}, which does not exist. "
                    f"static/ contains: {', '.join(imgs)}. "
                    f"Either rename the file to {fn}, or change the name inside "
                    f"\\photo{{...}} in cv.tex to match. Names are case-sensitive.")
            else:
                warnings.append(
                    f"\\photo{{{fn}}} asks for static/{fn}, and static/ holds no "
                    "image at all; the page will show no photo.")
        return ok

    photo, header = render_header(header_blocks, macros, have_photo)
    name = next((plain(conv(b[1], macros)) for b in header_blocks if b[0] == "name"), "Home")
    photo = photo.replace("{NAME}", html.escape(name))

    first_para = next((b[1] for b in pages[0]["blocks"] if b[0] == "para"), "")
    description = plain(conv(first_para, macros))
    if len(description) > 160:
        description = description[:157].rsplit(" ", 1)[0] + "…"

    has_pdf = PDF.exists()
    if has_pdf:
        shutil.copy2(PDF, OUT / "cv.pdf")
    else:
        warnings.append("cv.pdf not found; 'CV (PDF)' link left out of the menu")

    for p in pages:
        p["file"] = "index.html" if p["slug"] == "index" else f"{p['slug']}.html"

    for p in pages:
        items = []
        for q in pages:
            current = ' aria-current="page"' if q is p else ""
            items.append(f'<a href="{q["file"]}"{current}>{html.escape(q["label"])}</a>')
        if has_pdf:
            items.append('<a href="cv.pdf">CV (PDF)</a>')
        title = name if p["slug"] == "index" else f"{name} — {p['label']}"
        (OUT / p["file"]).write_text(PAGE.format(
            title=html.escape(title),
            description=html.escape(description, quote=True),
            nav="\n".join(items),
            header=header,
            photo=photo,
            body=render_body(p["blocks"], macros),
            footer=f"<footer>{footer}</footer>" if footer.strip() else "",
        ), encoding="utf-8")

    (OUT / ".nojekyll").write_text("")

    todos = re.findall(r"\\todo\{([^{}]*)\}", body)
    print(f"Built {len(pages)} pages into {OUT.relative_to(ROOT)}/: "
          + ", ".join(p["file"] for p in pages) + (", cv.pdf" if has_pdf else ""))
    if todos:
        print(f"\n{len(todos)} placeholder(s) still visible on the site:")
        for t in todos:
            print("  - " + " ".join(t.split()))
    for w in dict.fromkeys(warnings):
        print("warning: " + w)


if __name__ == "__main__":
    try:
        build()
    except TexError as e:
        sys.exit(f"cv.tex problem: {e}")
    if "--serve" in sys.argv:
        import functools
        import http.server
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(OUT))
        print("\nPreview at http://localhost:8000  (Ctrl+C to stop)")
        http.server.ThreadingHTTPServer(("", 8000), handler).serve_forever()
