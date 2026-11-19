"""Write the submission's Data folder: one input/output CSV and one readable results TXT per model.

Run from the project folder:
    python data_export/export_data.py <output folder>          (default: ../../Data)

Cases included (every case the code was run on for the project):
  * the 43 worked examples on the website (data_export/website_examples.json, extracted from
    website/dist/app.js by dump_examples.cjs),
  * the 63 scenarios of the team's test-case PDF (website_test_cases.py),
  * 20 further plain-English scenarios that check model selection and units (test_upgrade.py and below).
Cases that end in the property calculator, the model-required fallback or an input error are written to
Other_cases_*.  Validation against literature goes to Validation_against_literature.csv and the test output to Test_logs.txt.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "website" / "dist"), str(ROOT / "data_export")]

from test_upgrade import RoutingTests  # noqa: E402
from website.dist.solver import solve  # noqa: E402
from website_test_cases import CASES  # noqa: E402
import validation_against_literature  # noqa: E402

MODELS = {
    "fixed_reservoir_engine": (1, "Model_1_Fixed_reservoir_heat_engine"),
    "finite_stream_engine": (2, "Model_2_Finite_hot_and_cold_stream_engine"),
    "steady_control_volume": (3, "Model_3_Steady_control_volume"),
    "closed_system": (4, "Model_4_Closed_system"),
    "refrigeration_cycle": (5, "Model_5_Refrigerator_and_heat_pump"),
    "finite_stream_heat_recovery": (6, "Model_6_Heat_exchanger"),
    "mechanical_power": (7, "Model_7_Mechanical_energy_harvesting"),
    "solar_blackbody": (8, "Model_8_Solar_radiation_conversion"),
    "reaction_gibbs": (9, "Model_9_Electrochemical_conversion"),
}
OTHER = "Other_cases_property_and_out_of_scope"

EXTRA_SCENARIOS = [
    "A refrigerator removes 300 W from a freezer at -18 °C and rejects heat to a kitchen at 25 °C using 100 W of electricity.",
    "A freezer at -18 °C in a 25 °C kitchen claims a COP of 8 while removing 300 W.",
    "A small hydro turbine has 20 L/s of water falling 10 m and claims 1500 W.",
    "A refrigerator removes 5 kJ from a cold space at 260 K and rejects heat at 300 K using 1 kW of work.",
    "A heat exchanger cools hot oil (2 kg/s, Cp 2.2 kJ/kg K) from 450 K to 350 K. Cold water at 1.2 kg/s with Cp "
    "4.18 kJ/kg K enters at 300 K. No shaft work. Surroundings are at 25 °C.",
    "An industrial heat exchanger is used to cool hot oil from 450 K to 350 K while heating cold water from 300 K to "
    "380 K. The oil flows at 2 kg/s with cp 2.2 kJ/kg K and the water flows at 1.2 kg/s with cp 4.5833 kJ/kg K. "
    "All heat released by the hot oil is absorbed by the cold water.",
]

# Readable column names (with units) for engine input and output keys.
LABELS = {
    "T_hot_K": "Hot reservoir / hot-side temperature (K)", "T_cold_K": "Cold reservoir / cold-side temperature (K)",
    "Q_hot_kw": "Heat supplied Q_H (kW)", "W_out_kw": "Claimed work or power output (kW)",
    "W_out_kj": "Work done by the system W (kJ)", "U1_kj": "Initial internal energy U1 (kJ)",
    "delta_E_kj": "Change in system energy dE (kJ)", "delta_S_system_kj_K": "Change in system entropy dS (kJ/K)",
    "mass_flow_kg_s": "mass flow (kg/s)", "T_in_K": "inlet temperature (K)", "T_out_K": "outlet temperature (K)",
    "cp_kj_kg_k": "Cp (kJ/kg K)", "specific_enthalpy_kj_kg": "specific enthalpy (kJ/kg)",
    "specific_entropy_kj_kg_k": "specific entropy (kJ/kg K)", "heat_into_cv_kw": "heat into device (kW)",
    "heat_into_system_kj": "heat into system (kJ)", "boundary_temperature_K": "boundary temperature (K)",
    "Q_cold": "Heat removed from cold side Q_C (kW or kJ)", "Q_hot": "Heat delivered to hot side Q_H (kW or kJ)",
    "W_in": "Work input W_in (kW or kJ)", "mode": "Device", "energy_unit": "Energy basis",
    "T0_K": "Environment temperature T0 (K)", "hot_duty_kw": "Hot-side duty (kW)", "cold_duty_kw": "Cold-side duty (kW)",
    "arrangement": "Flow arrangement",
    "recoverable_mechanical_power_kw": "Mechanical input power (kW)", "recoverable_energy_per_event_j": "Energy per step (J)",
    "events_per_s": "Steps per second (1/s)", "force_N": "Force (N)", "displacement_m": "Displacement per step (m)",
    "torque_Nm": "Torque (N m)", "angular_velocity_rad_s": "Angular speed (rad/s)", "air_density_kg_m3": "Air density (kg/m3)",
    "swept_area_m2": "Rotor swept area (m2)", "wind_speed_m_s": "Wind speed (m/s)", "head_m": "Head (m)",
    "volumetric_flow_m3_s": "Water flow (m3/s)", "water_density_kg_m3": "Water density (kg/m3)", "g_m_s2": "g (m/s2)",
    "water_mass_flow_kg_s": "Water mass flow (kg/s)",
    "T_source_K": "Radiation source temperature (K)", "T_ambient_K": "Ambient temperature (K)",
    "irradiance_W_m2": "Irradiance (W/m2)", "collection_area_m2": "Collection area (m2)",
    "reaction_direction": "Reaction", "delta_G_kj_per_mol_rxn": "Gibbs energy of reaction dG (kJ/mol)",
    "reaction_rate_mol_rxn_s": "Reaction rate (mol/s)",
    "T1_K": "Initial temperature (K)", "T2_K": "Final temperature (K)", "mass_kg": "Mass (kg)",
    "latent_heat_kj_kg": "Latent heat (kJ/kg)", "phase_change_temperature_K": "Phase-change temperature (K)",
    # outputs
    "Q_cold_rejected_kw": "Heat rejected Q_C (kW)", "S_gen_dot_kw_per_k": "Entropy generation S_gen (kW/K)",
    "S_gen_kj_per_k": "Entropy generation S_gen (kJ/K)", "actual_efficiency_percent": "Claimed efficiency (%)",
    "carnot_efficiency_percent": "Carnot efficiency (%)", "reversible_work_limit_kw": "Reversible work limit (kW)",
    "heat_released_by_hot_stream_kw": "Heat released by hot stream (kW)",
    "heat_absorbed_by_cold_stream_kw": "Heat absorbed by cold stream (kW)",
    "strict_reversible_work_limit_kw": "Maximum reversible work W_max (kW)",
    "cold_outlet_from_first_law_K": "Cold outlet from First Law (K)",
    "reversible_cold_outlet_temperature_K": "Cold outlet at W_max (K)",
    "work_used_for_assessment_kw": "Work assessed (kW)",
    "actual_efficiency_relative_to_hot_stream_heat_percent": "Claimed efficiency vs hot-stream heat (%)",
    "COP_refrigerator": "COP (refrigerator)", "carnot_COP_refrigerator": "Carnot COP (refrigerator)",
    "COP_heat_pump": "COP (heat pump)", "carnot_COP_heat_pump": "Carnot COP (heat pump)",
    "minimum_work_input": "Minimum (reversible) work input (kW or kJ)",
    "energy_balance_residual_kw": "Energy balance residual (kW)", "W_shaft_kw": "Shaft work (kW)",
    "exergy_destroyed_kw": "Exergy destroyed T0 S_gen (kW)",
    "mechanical_input_kw": "Mechanical input (kW)", "wind_kinetic_power_kw": "Wind kinetic power (kW)",
    "betz_limit_kw": "Betz limit (kW)", "hydraulic_power_kw": "Hydraulic power rho g Q H (kW)",
    "energy_per_event_j": "Energy per step (J)", "reversible_power_limit_kw": "Output ceiling (kW)",
    "claimed_electrical_output_kw": "Claimed electrical output (kW)",
    "power_remaining_after_electrical_output_kw": "Margin below ceiling (kW)",
    "incident_radiation_kw": "Incident radiation (kW)", "petela_landsberg_exergy_fraction": "Petela-Landsberg exergy fraction (-)",
    "signed_reversible_nonexpansion_work_kw": "Reversible work -dG x rate (kW)",
    "minimum_external_work_input_kw": "External work needed (kW)",
    "delta_h_kj_kg": "Specific enthalpy change dh (kJ/kg)", "delta_s_kj_kg_k": "Specific entropy change ds (kJ/kg K)",
    "delta_H_total_kj": "Total enthalpy change dH (kJ)", "effective_temperature_K": "Effective temperature (K)",
}
PREFIX = {"hot_stream": "Hot stream", "cold_stream": "Cold stream", "inlets[0]": "Inlet", "outlets[0]": "Outlet",
          "heat[0]": "Heat interaction 1", "heat[1]": "Heat interaction 2"}
SKIP_INPUT = {"model", "title", "description", "name", "cp_model", "apply_strict_reversible_limit",
              "apply_effective_temperature_limit", "energy_relative_tolerance", "domain", "reason"}
OUTPUTS = {
    "fixed_reservoir_engine": ["actual_efficiency_percent", "carnot_efficiency_percent", "reversible_work_limit_kw",
                               "Q_cold_rejected_kw", "S_gen_dot_kw_per_k"],
    "finite_stream_engine": ["heat_released_by_hot_stream_kw", "work_used_for_assessment_kw", "strict_reversible_work_limit_kw",
                             "heat_absorbed_by_cold_stream_kw", "cold_outlet_from_first_law_K",
                             "reversible_cold_outlet_temperature_K", "S_gen_dot_kw_per_k"],
    "steady_control_volume": ["S_gen_dot_kw_per_k"],
    "closed_system": ["S_gen_kj_per_k"],
    "refrigeration_cycle": ["Q_cold", "W_in", "Q_hot", "COP_refrigerator", "carnot_COP_refrigerator", "COP_heat_pump",
                            "carnot_COP_heat_pump", "minimum_work_input", "S_gen_dot_kw_per_k"],
    "finite_stream_heat_recovery": ["heat_released_by_hot_stream_kw", "heat_absorbed_by_cold_stream_kw",
                                    "energy_balance_residual_kw", "cold_outlet_from_first_law_K", "S_gen_dot_kw_per_k",
                                    "exergy_destroyed_kw"],
    "mechanical_power": ["mechanical_input_kw", "wind_kinetic_power_kw", "betz_limit_kw", "hydraulic_power_kw",
                         "reversible_power_limit_kw", "claimed_electrical_output_kw", "power_remaining_after_electrical_output_kw"],
    "solar_blackbody": ["incident_radiation_kw", "petela_landsberg_exergy_fraction", "reversible_power_limit_kw",
                        "claimed_electrical_output_kw"],
    "reaction_gibbs": ["reversible_power_limit_kw", "claimed_electrical_output_kw", "minimum_external_work_input_kw"],
    "property_change": ["delta_h_kj_kg", "delta_s_kj_kg_k", "delta_H_total_kj", "effective_temperature_K"],
    "unsupported_domain": [],
}
VERDICT_WORDS = {"needs_values": "NEEDS MORE INFORMATION", "error": "INPUT ERROR"}


def label(path):
    parts = path.split(".")
    if len(parts) == 2 and parts[0] in PREFIX:
        return f"{PREFIX[parts[0]]} {LABELS.get(parts[1], parts[1])}"
    return LABELS.get(path, path.replace("_", " "))


def flatten(case, prefix=""):
    out = {}
    for key, value in case.items():
        if key in SKIP_INPUT:
            continue
        if isinstance(value, dict):
            out.update(flatten(value, prefix + key + "."))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    out.update(flatten(item, f"{prefix}{key}[{i}]."))
        else:
            out[prefix + key] = value
    return out


def tidy(value):
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        if abs(value) < 1e-9:
            return 0
        return float(f"{value:.6g}")
    return value


def run(request):
    try:
        return solve(request)
    except Exception as error:      # an input the engine rejects is itself a recorded outcome
        return {"status": "error", "error": str(error)}


def collect():
    dump = json.loads((ROOT / "data_export" / "website_examples.json").read_text(encoding="utf-8"))
    cases = []
    for i, example in enumerate(dump["examples"], 1):
        cases.append({"source": "Website example", "ref": f"E{i:02d}", "name": example["title"],
                      "entered": "Description" if example.get("text") else "Model form",
                      "text": example.get("text", ""), "note": example.get("note", ""), "request": example["request"]})
    for cid, _expected, text in CASES:
        cases.append({"source": "Test-case PDF", "ref": cid, "name": f"Test case {cid}", "entered": "Description",
                      "text": text, "note": "", "request": {"scenario": text}})
    seen = {c["text"] for c in cases}
    extra = [t for t, _ in RoutingTests.ROUTES] + EXTRA_SCENARIOS
    for n, text in enumerate([t for t in extra if t not in seen], 1):
        cases.append({"source": "Additional scenario", "ref": f"S{n:02d}", "name": f"Additional scenario {n}",
                      "entered": "Description", "text": text, "note": "", "request": {"scenario": text}})
    for case in cases:
        case["response"] = run(case["request"])
        response = case["response"]
        route = response.get("route") or (response.get("analysis") or {}).get("model")
        case["route"] = route if route in MODELS else None
    return cases


def describe(case):
    response = case["response"]
    result = response.get("result") or {}
    verdict = result.get("verdict") or {}
    status = verdict.get("status") or VERDICT_WORDS.get(response.get("status"), "") or ("INPUT ERROR" if response.get("error") else "")
    reasons = verdict.get("reasons") or []
    missing = (response.get("analysis") or {}).get("missing") or []
    engine_case = list((response.get("cases") or {}).values())[-1] if response.get("cases") else {}
    return status, reasons, missing, engine_case, result


def write_model_files(out, route, file_stem, cases):
    prefix = f"M{MODELS[route][0]}" if route else "X"
    rows, input_keys = [], []
    for case in cases:
        status, reasons, missing, engine_case, result = describe(case)
        inputs = flatten(engine_case) if engine_case else {}
        for key in inputs:
            if key not in input_keys:
                input_keys.append(key)
        calc = result.get("calculations") or {}
        fl = (result.get("checks") or {}).get("first_law")
        rows.append((case, status, reasons, missing, inputs, calc, fl))
    out_keys = [k for k in OUTPUTS.get(route, []) if any(isinstance(r[5].get(k), (int, float)) for r in rows)]
    header = (["Case ID", "Source", "Source reference", "Case name", "How entered", "Scenario text"] +
              [label(k) for k in input_keys] + ["Verdict"] +
              [label(k) + (" - result" if k in input_keys else "") for k in out_keys] +
              ["First-Law residual", "Main reason for the verdict", "Missing information"])
    with open(out / f"{file_stem}.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for n, (case, status, reasons, missing, inputs, calc, fl) in enumerate(rows, 1):
            residual = f"{tidy(fl['residual'])} {fl['units']}" if isinstance(fl, dict) and "residual" in fl else ""
            writer.writerow([f"{prefix}-{n:02d}",
                             case["source"], case["ref"], case["name"], case["entered"], case["text"]] +
                            [tidy(inputs.get(k, "")) for k in input_keys] + [status] +
                            [tidy(calc.get(k, "")) for k in out_keys] +
                            [residual, reasons[0] if reasons else "", " | ".join(missing)])

    with open(out / f"{file_stem}_results.txt", "w", encoding="utf-8") as handle:
        title = file_stem.replace("_", " ")
        handle.write(f"{title}\n{'=' * len(title)}\nFull results for every case in {file_stem}.csv (same Case IDs).\n\n")
        for n, (case, status, reasons, missing, inputs, calc, fl) in enumerate(rows, 1):
            cid = f"{prefix}-{n:02d}"
            handle.write(f"--- {cid}  {case['name']}  ({case['source']} {case['ref']}) ---\n")
            if case["text"]:
                handle.write(f"Scenario: {case['text']}\n")
            elif case["note"]:
                handle.write(f"Example: {case['note']}\n")
            if inputs:
                handle.write("Inputs given to the engine (SI units: K, kW, kJ, kg/s):\n")
                for k, v in inputs.items():
                    handle.write(f"    {label(k)} = {tidy(v)}\n")
            handle.write(f"Verdict: {status}\n")
            for line in reasons:
                handle.write(f"    - {line}\n")
            table = (case["response"].get("result") or {}).get("check_table") or []
            if table:
                handle.write("Balance and limit checks:\n")
                for row in table:
                    handle.write(f"    [{row.get('status', '')}] {row.get('check', '')}: {row.get('value', '')}"
                                 f"  (criterion: {row.get('criterion', '')})\n")
            if missing:
                handle.write("Missing information: " + "; ".join(missing) + "\n")
            if case["response"].get("error"):
                handle.write("Message: " + case["response"]["error"] + "\n")
            handle.write("\n")
    return len(rows)


def write_validation(out):
    data = validation_against_literature.rows()
    with open(out / "Validation_against_literature.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Model", "Type of reference", "Quantity", "Conditions", "Unit", "Reference value", "Code value",
                         "Difference (code - reference)", "Relative difference (%)", "Reference / source", "Note"])
        for r in data:
            rel = r["relative_difference_percent"]
            writer.writerow([r["model"], r["kind"], r["quantity"], r["conditions"], r["unit"], tidy(r["reference_value"]),
                             tidy(r["code_value"]), tidy(r["difference"]), "" if rel != rel else tidy(rel),
                             r["reference_source"], r["note"]])
    return len(data)


def write_logs(out):
    parts = []
    for title, cmd in (("Engine self-test", [sys.executable, "thermodynamic_feasibility_tester_MASTER.py", "--self-test"]),
                       ("Python unit tests", [sys.executable, "-m", "unittest", "-v"]),
                       ("Website tests", ["node", "--test", "website/test_ui.cjs"])):
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        parts.append(f"===== {title} =====\nCommand: {' '.join(c if c != sys.executable else 'python' for c in cmd)}\n\n"
                     f"{proc.stdout}{proc.stderr}\n")
    (out / "Test_logs.txt").write_text("\n".join(parts), encoding="utf-8")


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent.parent / "Data").resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases = collect()
    for route, (_, stem) in MODELS.items():
        n = write_model_files(out, route, stem, [c for c in cases if c["route"] == route])
        print(f"{stem}: {n} cases")
    n = write_model_files(out, None, OTHER, [c for c in cases if c["route"] is None])
    print(f"{OTHER}: {n} cases")
    print(f"Validation_against_literature: {write_validation(out)} comparisons")
    write_logs(out)


if __name__ == "__main__":
    main()
