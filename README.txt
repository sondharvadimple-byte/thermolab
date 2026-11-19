DATA - Thermodynamic Feasibility Tester (CL 202, Group IA)
=========================================================

All files here are output from the project code. To regenerate them, run this from
Codes/thermodynamic-feasibility-tester:

    python data_export/export_data.py ../../Data

There is one pair of files per model:

  Model_<n>_<name>.csv            inputs and results, one row per case (opens in Excel)
  Model_<n>_<name>_results.txt    the same cases written out in full: inputs, verdict, reasons and every
                                  balance/limit check (PASS / FAIL / NOT EVALUATED)

Both files use the same Case ID (M1-01, M1-02, ...), so a row in the CSV can be looked up in the TXT.

What was run
------------
126 cases in total:
  - all 43 worked examples on the website (E01-E43),
  - all 63 scenarios in the team's test-case document
    (Literature/Project_documents/Test_cases_63_scenarios.pdf),
  - 20 more written scenarios that test model selection and unit handling.

  File                                          cases   website / test-case doc / additional
  Model_1_Fixed_reservoir_heat_engine            12      4 / 6 / 2
  Model_2_Finite_hot_and_cold_stream_engine      10      4 / 6 / 0
  Model_3_Steady_control_volume                  13      4 / 6 / 3
  Model_4_Closed_system                          10      4 / 5 / 1
  Model_5_Refrigerator_and_heat_pump             12      5 / 2 / 5
  Model_6_Heat_exchanger                         14      5 / 6 / 3
  Model_7_Mechanical_energy_harvesting           20      7 / 10 / 3
  Model_8_Solar_radiation_conversion             12      4 / 7 / 1
  Model_9_Electrochemical_conversion             14      6 / 7 / 1
  Other_cases_property_and_out_of_scope           9      property questions, processes with no model
                                                         (tidal, transient cooling) and one invalid input

Reading a model CSV
-------------------
The columns run left to right in this order:

  1. Case details: Case ID, Source (website example / test-case document / additional scenario),
     Source reference (E05, 2.1, ...), case name, how it was entered (model form or description)
     and the description text where there is one.
  2. Inputs: the values the engine used, in SI units. Temperatures are in K even when the
     description gave °C. Units are written in each column heading.
  3. Verdict.
  4. Results: the model's calculated values, e.g. efficiency and Carnot efficiency, W_max, COP and
     Carnot COP, S_gen, the output ceiling. Where a result has the same name as an input
     (refrigerator Q_C, W_in, Q_H), the heading ends in "- result".
  5. First-Law residual, the main reason for the verdict, and anything the tester asked for.

An empty cell means the value was not given or does not apply to that case.

Verdicts
--------
  FEASIBLE / NOT REJECTED               passes every check of the model
  NOT FEASIBLE ...                      a balance fails or the claim is above the model limit
  SECOND-LAW ADMISSIBLE / NOT REJECTED  heat exchanger that passes every check
  LIMIT CALCULATED - NO CLAIM TO TEST   only the maximum work was asked for
  INSUFFICIENT DATA / MODEL REQUIRED    a check could not be completed, or no model fits
  NEEDS MORE INFORMATION                the description is missing a value (see the last column)
  PROPERTY CALCULATION ONLY             a property question; numbers but no verdict
  INPUT ERROR                           invalid input, e.g. a temperature below absolute zero

Other files
-----------
Validation_against_literature.csv
    Code results next to literature values for every model (manuscript Table 1 and Figure 2). The
    "Type of reference" column separates comparisons with published data (JANAF, NETL, Mady et al.,
    Li et al.) from checks against published closed-form results (Carnot, Bejan, Clausius, Betz,
    Petela). The Li et al. row is an inequality: the measured 2.4 W must lie below the 4.4 W ceiling,
    so no percentage difference is given.
    Regenerate on its own: python data_export/validation_against_literature.py

Test_logs.txt
    Output of the engine self-test (27 checks), the Python tests (83) and the website tests (10).
    All pass.
