# Codes: Thermodynamic Feasibility Tester

CL 202 project, Group IA, IIT Gandhinagar

- Website: https://keshavichhodavadia-del.github.io/thermodynamic-feasibility-tester/
- GitHub:  https://github.com/keshavichhodavadia-del/thermodynamic-feasibility-tester

The complete code is in `thermodynamic-feasibility-tester/`. Its README covers the models, the website, the terminal
tools and the tests in detail. This page is the short version for running and checking the project.

## What it does

You describe an energy device and what it is claimed to do, either in plain English or in a form. The tester picks
one of nine models (heat engines, stream-to-stream engines, turbines and other steady-flow devices, closed systems,
refrigerators and heat pumps, heat exchangers, mechanical harvesters, solar converters and fuel cells). It then
checks the claim against the mass, energy and entropy balances and the model's thermodynamic limit, and answers
feasible, not feasible or insufficient data. Every check behind the answer is shown with its equation.

## Running it

You need Python 3.9 or newer. The code uses only the standard library. Node 18+ is needed only for the website tests,
and matplotlib/numpy only for redrawing the manuscript figures. From inside `thermodynamic-feasibility-tester/`:

```
python website/serve.py --port 0 --open      # the website, locally (Windows: double-click Start_ThermoLab.cmd)
python auto_analyzer.py "A freezer at -18 °C in a 25 °C kitchen claims a COP of 8 while removing 300 W."
python thermodynamic_feasibility_tester_MASTER.py               # menu-driven terminal version
python thermodynamic_feasibility_tester_MASTER.py --input case.json
```

Any result on the website can be downloaded as `case.json` and run with the last command.

## Checking it

```
python thermodynamic_feasibility_tester_MASTER.py --self-test   # 27 checks
python -m unittest                                              # 83 tests (includes the 63 team test cases)
node --test website/test_ui.cjs                                 # 10 website tests
```

## Reproducing the submitted results

```
python data_export/validation_against_literature.py                   # manuscript Table 1
python manuscript_figures/make_figures.py ../../Manuscript_LaTeX_source/figures
python data_export/export_data.py ../../Data                          # the whole Data folder
```

## Main files

- `thermodynamic_feasibility_tester_MASTER.py`: the engine (all nine models, balances, limits, verdicts)
- `auto_analyzer.py`: turns a written description into an engine case
- `website/dist/`: the website; `docs/` is the same site built for GitHub Pages
- `data_export/`, `manuscript_figures/`: produce the Data folder, Table 1 and the figures
- `test_*.py`, `website_test_cases.py`, `website/test_ui.cjs`: tests
- `FLOWCHART.md`: how a verdict is reached, step by step
