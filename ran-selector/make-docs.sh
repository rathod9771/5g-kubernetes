#!/usr/bin/env bash
# Regenerate dashboard documentation assets from the deck.
# Run from the ran-selector directory after changing RAN_Architectures.pptx.
#
#   ./make-docs.sh
#
# Produces:
#   docs/RAN_Architectures.pptx   (source, left in place)
#   docs/RAN_Architectures.pdf    (for the "Open PDF" link and download)
#   docs/slides/slide-NN.jpg      (one image per slide, used by the viewer)

set -euo pipefail

DOCS="$(cd "$(dirname "$0")" && pwd)/docs"
PPTX="$DOCS/RAN_Architectures.pptx"
SLIDES="$DOCS/slides"

[ -f "$PPTX" ] || { echo "missing: $PPTX"; exit 1; }

for bin in soffice pdftoppm; do
  command -v "$bin" >/dev/null || {
    echo "missing '$bin'."
    echo "  soffice   -> sudo apt install libreoffice-impress"
    echo "  pdftoppm  -> sudo apt install poppler-utils"
    exit 1
  }
done

echo "converting to PDF..."
soffice --headless --convert-to pdf --outdir "$DOCS" "$PPTX" >/dev/null 2>&1

echo "rendering slides..."
rm -rf "$SLIDES"; mkdir -p "$SLIDES"
pdftoppm -jpeg -r 130 -jpegopt quality=88 "$DOCS/RAN_Architectures.pdf" "$SLIDES/slide"

COUNT=$(ls -1 "$SLIDES"/slide-*.jpg 2>/dev/null | wc -l)
echo "done: $COUNT slides in $SLIDES"
du -sh "$SLIDES"
echo
echo "restart the dashboard to pick them up:  sudo systemctl restart ran-selector"
