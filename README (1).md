# Thermodynamic Feasibility Tester

CL 202 project, Group IA, IIT Gandhinagar.

Website: https://keshavichhodavadia-del.github.io/thermodynamic-feasibility-tester/
Source:  https://github.com/keshavichhodavadia-del/thermodynamic-feasibility-tester

The tester takes a claim about an energy device and checks it against the First and Second Laws. A claim might be
"this engine makes 60 kW from 100 kW of heat at 600 K" or "this freezer has a COP of 8". The tester picks a model,
works out the balances and the maximum that thermodynamics allows, and gives one of three answers:

- feasible (within the stated model),
- not feasible (a balance fails or the claim is above the limit),
- insufficient data (the missing value is named).

Values are never guessed. If a balance fixes a missing number, such as an outlet temperature, it is calculated and
marked as calculated. Otherwise the tester asks for it.

## Quick start

Everything runs on Python 3.9+ with the standard library only.

```
python website/serve.py --port 0 --open        # website on your own computer (or Start_ThermoLab.cmd on Windows)
python auto_analyzer.py "A heat engine receives 100 kW from a hot source at 600 K, rejects heat at 300 K and produces 40 kW."
python thermodynamic_feasibility_tester_MASTER.py               # terminal menu
python thermodynamic_feasibility_tester_MASTER.py --input case.json
```

The hosted website needs no installation. It runs this same Python engine inside the browser through Pyodide. The
first calculation downloads the Python runtime, so it takes 10–20 s; later ones are instant.

## The nine models

| # | Model | Typical devices | What is checked |
|---|---|---|---|
| 1 | Fixed-temperature reservoirs | heat engine between two temperatures | First Law, S_gen ≥ 0, efficiency ≤ 1 − T_C/T_H |
| 2 | Finite hot and cold streams | waste-heat and exhaust-gas engines | exact W_max from S_gen = 0 for both streams; cold outlet from the First Law if not given |
| 3 | Steady control volume | turbine, compressor, pump, nozzle | mass, energy and entropy balances (each one when its data are given) |
| 4 | Closed system | piston-cylinder, sealed tank, full cycle | Q − W = ΔE, S_gen = ΔS − ΣQ/T ≥ 0 |
| 5 | Refrigerator / heat pump | fridge, freezer, AC, heat pump | Q_H = Q_C + W_in, COP ≤ Carnot COP |
| 6 | Heat exchanger (no shaft work) | water–water, oil–water | energy closure, S_gen ≥ 0, no temperature cross, exergy destroyed T0·S_gen |
| 7 | Mechanical harvesting | walking, pedalling, wind, hydro | output ≤ mechanical input; Betz limit for wind; ρgQH for hydro |
| 8 | Solar radiation | solar converters | Petela–Landsberg exergy of blackbody radiation |
| 9 | Electrochemical | fuel cells | output ≤ −ΔG × reaction rate (positive ΔG gives no work) |

Two helpers are not in the numbered list. The property calculator gives Δh, Δs, latent heat and the effective
temperature Δh/Δs. The fallback answers "model required" for things there is no validated model for, such as
transient cooling or tidal power, instead of forcing a wrong model onto them.

Each model has a fixed internal key (`fixed_reservoir_engine`, `finite_stream_engine`, `steady_control_volume`,
`closed_system`, `refrigeration_cycle`, `finite_stream_heat_recovery`, `mechanical_power`, `solar_blackbody`,
`reaction_gibbs`). Saved JSON cases use these keys, so they keep working if the display numbers change.

Every result includes a checks table with one row per balance or limit: the equation, the calculated value, the
criterion and PASS / FAIL / NOT EVALUATED / NOT APPLICABLE.

## How a written description is read

`auto_analyzer.py` handles the "Describe a scenario" box. It works in five steps:

1. It finds numbers with units and converts them to SI. It understands °C, °F, kW, MW, Btu/h, L/min, rpm, mol/s and
   g/s, as well as numbers written as words.
2. It decides what each number is from the words around it: heat in, heat out, work, stream inlet or outlet,
   reservoir temperature, wind speed, head, ΔG, and so on.
3. It separates flowing streams from fixed-temperature reservoirs and chooses the model. The result says which words
   led to that choice.
4. It calculates anything the equations fix uniquely and lists anything still missing.
5. It builds the engine case and runs it.

Defaults are only used when the text names the substance (e.g. Cp of water), and each one is listed in the result.
Energy amounts (kJ) and rates (kW) are never mixed.

## Website

The website source is in `website/dist/`. `docs/` is the built copy that GitHub Pages serves; refresh it with
`python build_pages.py` after any change.

The site has a form for each model and a box for plain-English descriptions. It also has 43 worked examples: one card
per model on the home page, and the rest in the example library. Each result shows:

- the verdict and the checks table;
- charts of the energy path, First-Law closure, entropy generation and temperatures;
- a sensitivity sweep that re-runs the model while one input changes;
- the exact Python functions used and the engine input file, which can be downloaded and re-run from the terminal.

## Terminal tools

```
python thermodynamic_feasibility_tester_MASTER.py --demo            # built-in demonstrations
python thermodynamic_feasibility_tester_MASTER.py --make-templates  # editable JSON input templates
python auto_analyzer.py --json "A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW."
python scenario_input.py "Can I generate electricity by walking?" --interactive
```

`scenario_input.py` is a question-and-answer version for the terminal. It can optionally use an OpenAI model to pull
quantities out of free text (`configure_openai.py` stores the key). Nothing else needs this, and every calculation
stays in Python.

## Tests

```
python thermodynamic_feasibility_tester_MASTER.py --self-test   # 27 formula checks
python -m unittest                                              # 83 tests, including all 63 team test cases
node --test website/test_ui.cjs                                 # 10 website tests (Node 18+)
```

`website_test_cases.py` holds the 63 scenarios from the team's test-case document. `test_upgrade.py` checks model
routing, missing-data handling and the checks table.

## Reproducing the manuscript and the Data folder

```
python data_export/validation_against_literature.py        # Table 1 of the manuscript
python manuscript_figures/make_figures.py <folder>          # Figures 2-4 (needs matplotlib and numpy)
python data_export/export_data.py ../../Data                # every file in the Data folder
```

`data_export/website_examples.json` is the list of website examples used by the export. It is read out of
`website/dist/app.js` with `node data_export/dump_examples.cjs website/dist data_export/website_examples.json`.

## Files

| File | Purpose |
|---|---|
| `thermodynamic_feasibility_tester_MASTER.py` | the calculation engine: models, balances, limits, verdicts, terminal interface |
| `auto_analyzer.py` | reads plain-English descriptions and chooses the model |
| `feasibility_examples.py` | demo cases, JSON templates and self-test cases |
| `website/dist/` | website: `index.html`, `app.js`, `style.css`, `engine-worker.js`, `solver.py` |
| `website/serve.py`, `Start_ThermoLab.cmd` | run the website locally |
| `website/local_scenarios.py`, `scenario_input.py` | phrase-based extractor used as a fallback and by the terminal Q&A tool |
| `configure_openai.py`, `website/openai_config.py` | optional OpenAI key setup for `scenario_input.py` |
| `build_pages.py`, `docs/` | build and output of the GitHub Pages site |
| `data_export/`, `manuscript_figures/` | scripts that produce the Data folder, Table 1 and the figures |
| `test_*.py`, `website_test_cases.py`, `website/test_ui.cjs` | tests |
| `run_mock_assignments.py`, `mock_cases/`, `validate_benchmarks.py` | the two CL 202 course assignments (terminal only; not part of the website, data or manuscript; kept because the tests and the terminal tool still use them) |
| `FLOWCHART.md` | flowchart of how a verdict is reached |

## Limits

A "feasible" verdict only means the claim passes the checks of the chosen model. It does not mean the device can be
built or will pay for itself. Heat capacities are constant or linear in T, so steam and refrigerant properties must
come from tables. Models 7–9 compare the output with the available resource and do not model losses inside the device.
Model 8 assumes undiluted blackbody radiation, and Model 9 needs ΔG at the operating conditions.
