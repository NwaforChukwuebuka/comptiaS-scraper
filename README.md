# CompTIA SY0-701 Scraper & PDF Compiler

This tool scrapes the FreeBrainDumps SY0-701 question pages and compiles a clean PDF with inline images. It also exports all data to JSON.

## Setup

1. Install Python 3.12+
2. (Recommended) Create a virtual environment

```bash
python3.12 -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

3. Install dependencies

```bash
pip install -r requirements.txt
```

## Usage

Scrape pages 1 through 3 and build outputs under `out/` with images in `images/`:

```bash
python scrape_sy0_701.py --start 1 --end 3 --out_dir out --images_dir images
```

Options:
- `--start` Start page number (inclusive). Default: 1
- `--end` End page number (inclusive). Default: 3
- `--out_dir` Output directory for JSON and PDF. Default: `out`
- `--images_dir` Directory to store downloaded images. Default: `images`
- `--delay` Seconds to wait between page fetches. Default: `0.5`

Outputs:
- JSON: `out/sy0-701_questions.json`
- PDF: `out/comptia_sy0-701_past_questions.pdf`
- Images: saved to the chosen `images/` directory

## Notes
- Be respectful of the target website; adjust `--delay` to reduce load.
- The parser is tailored to the current HTML structure (panels with `p.lead`, `ol.rounded-list`, and `Answer(s)` blocks). If the site changes, update selectors in `scrape_sy0_701.py`.
