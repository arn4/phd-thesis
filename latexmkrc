$pdf_mode = 4;  # lualatex
$lualatex = 'lualatex -interaction=nonstopmode -synctex=1 -file-line-error %O %S';
$out_dir  = 'build';
# \include{chapters/foo} and \subimport{papers/YYMM/}{...} write aux files
# into matching subdirs of $out_dir; lualatex won't create them, so pre-make.
# Both trees are made unconditionally: thesis-print.tex is built with an
# explicit -outdir=build/print, and a CLI -outdir is applied only *after* this
# rc file has run, so there is no way to detect the target from here.
use File::Path qw(make_path);
for my $dir ($out_dir, "$out_dir/print") {
    make_path("$dir/chapters", "$dir/front-matter");
    for my $p (glob('papers/[0-9]*')) {
        make_path("$dir/$p", "$dir/$p/sections", "$dir/$p/appendices");
    }
}
# biblatex needs biber, not bibtex; latexmk picks biber when $biber is set.
$biber = 'biber %O %B';
$bibtex_use = 2;
