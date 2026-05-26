$pdf_mode = 4;  # lualatex
$lualatex = 'lualatex -interaction=nonstopmode -synctex=1 -file-line-error %O %S';
$out_dir  = 'build';
# biblatex needs biber, not bibtex; latexmk picks biber when $biber is set.
$biber = 'biber %O %B';
$bibtex_use = 2;
