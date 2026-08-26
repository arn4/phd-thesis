#!/bin/sh
# ---------------------------------------------------------------------------
# LaTeX Workshop build hook (see .vscode/settings.json, tool "latexmk-thesis").
#
# Builds the root file it is handed, exactly as a bare latexmk call would, and
# then -- only when that root is the defence deck -- rebuilds the presenter-notes
# variant on top.  slides-notes.tex \inputs slides/slides.tex, so it is a second
# *root*, not a dependency: editing the deck must refresh both PDFs, or the
# notes variant silently goes stale while the plain deck is rebuilt on save.
# (Fired from the slides-notes.tex tab it is handled by the first call below,
# thanks to the `% !TeX root' magic comment that file pins on itself.)
#
# Both decks share build/: unlike thesis-print.tex the deck has no \include, so
# every aux file is jobname-named and build/slides.* never collides with
# build/slides-notes.*.  A shared out_dir is what lets LaTeX Workshop find the
# notes PDF at all -- latex.outDir is one global setting, so a build/notes/
# would be invisible to the viewer and to SyncTeX.
#
# Everything runs from the repo root, because every input path in this repo
# (figures, \include, \subimport) is repo-root-relative.
#
# Usage: build.sh <root-file-without-extension>
# ---------------------------------------------------------------------------
set -e

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
doc=$1

cd "$root"

latexmk -r "$root/latexmkrc" -pdflua -synctex=1 \
    -interaction=nonstopmode -file-line-error "$doc"

# %DOC% arrives without an extension; compare against the deck's stem.
case "$doc" in
    */slides/slides | slides/slides)
        latexmk -r "$root/latexmkrc" -pdflua -synctex=1 \
            -interaction=nonstopmode -file-line-error \
            "$root/slides/slides-notes.tex"
        ;;
esac
