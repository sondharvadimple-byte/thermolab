Manuscript source (LaTeX), CL 202 Group IA

Written in the ACS achemso class with the Industrial & Engineering Chemistry Research options, two-column layout.
The compiled PDF is CL202_GroupIA_Manuscript.pdf in the main folder.

  manuscript.tex     the manuscript
  references.bib     the 13 references
  figures/           Figures 2-4 (Figure 1 is drawn in TikZ inside manuscript.tex)

To compile on Overleaf, upload this folder and compile with pdfLaTeX. Overleaf already has the achemso class.
To compile locally:

  pdflatex manuscript && bibtex manuscript && pdflatex manuscript && pdflatex manuscript

The figures are produced by Codes/thermodynamic-feasibility-tester/manuscript_figures/make_figures.py from the
engine's own results.
