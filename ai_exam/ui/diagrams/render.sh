#!/usr/bin/env bash
# Re-render every phase_*.mmd in this directory to phase_*.svg via mmdc.
# Run after editing any .mmd source. The SVGs are what the Documentation
# page actually serves — checking them in keeps the page hermetic (no
# browser-side CDN, no runtime mermaid execution).
set -e
cd "$(dirname "$0")"
for f in phase_*.mmd; do
  mmdc -i "$f" -o "${f%.mmd}.svg" -p puppeteer.json
done
echo "rendered $(ls phase_*.svg | wc -l) diagrams"
