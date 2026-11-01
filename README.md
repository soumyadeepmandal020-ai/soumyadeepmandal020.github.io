# Soumyadeep Mandal — website and CV

Everything comes from **one file: `cv.tex`**. Edit it, push, and GitHub
rebuilds both the website and `cv.pdf` within about two minutes.

## Editing

- Open `cv.tex`, on GitHub (pencil icon) or on your computer.
- `\todo{...}` marks a placeholder. Placeholders show up highlighted on the live site
  until you replace them.
- `\webpage{publications}{Publications}` starts a new page of the site. Everything
  after it, up to the next `\webpage`, goes on that page. The PDF ignores these,
  so the CV stays one document.
- Add a paper by copying an existing `\pub{...}{...}{...}` block.
- Add an award or degree by copying an `\entry{...}{...}{...}` block.

## Photo

Put a square photo at `static/photo.jpg`. It appears on the website only.

## Preview on your own computer (optional)

```
python3 build.py --serve
```

Then open http://localhost:8000. Compile `cv.tex` with pdfLaTeX first if you want
the CV (PDF) link to work locally.

## Files

| File | What it is |
|---|---|
| `cv.tex` | The content. The only file you normally edit. |
| `static/style.css` | Colours, fonts and spacing of the website. |
| `static/` | Anything placed here (photo, extra PDFs) is published as-is. |
| `build.py` | Turns `cv.tex` into web pages. No need to touch it. |
| `.github/workflows/deploy.yml` | Tells GitHub to rebuild and publish on every push. |
