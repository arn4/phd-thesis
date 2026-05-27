$pdf_mode = 4;  # lualatex
$lualatex = 'lualatex -interaction=nonstopmode -synctex=1 -file-line-error %O %S';
$out_dir  = 'build';
# \include{chapters/foo} and \subimport{papers/YYMM/}{...} write aux files
# into matching subdirs of $out_dir; lualatex won't create them, so pre-make.
use File::Path qw(make_path);
make_path("$out_dir/chapters", "$out_dir/front-matter");
for my $p (glob('papers/[0-9]*')) {
    make_path("$out_dir/$p", "$out_dir/$p/sections", "$out_dir/$p/appendices");
}
# biblatex needs biber, not bibtex; latexmk picks biber when $biber is set.
$biber = 'biber %O %B';
$bibtex_use = 2;
