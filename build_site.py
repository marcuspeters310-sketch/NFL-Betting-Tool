"""
Turn web/NFL Board.html (built by export_board.py) into a complete web page
for GitHub Pages at _site/index.html.

The board template is written without <!doctype>/<html>/<head> tags because
the Claude artifact host adds those itself. GitHub Pages serves files as-is,
so this script adds them.
"""
from pathlib import Path

from config import PROJECT_ROOT

SOURCE = PROJECT_ROOT / "web" / "NFL Board.html"
SITE = PROJECT_ROOT / "_site"

HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🏈</text></svg>">
"""


def main() -> None:
    body = SOURCE.read_text(encoding="utf-8")
    if "__BOARD_DATA__" in body:
        raise SystemExit("Board data was not inserted into the page; not publishing.")
    SITE.mkdir(exist_ok=True)
    (SITE / "index.html").write_text(HEAD + body + "\n</html>\n", encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote {SITE / 'index.html'} ({(SITE / 'index.html').stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
