"""Demonstration cases, JSON templates and self-tests for the feasibility tester.

Kept separate from the engine so the engine file contains only the physics.
Run:  python thermodynamic_feasibility_tester_MASTER.py --self-test   (or --demo, --make-templates)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from thermodynamic_feasibility_tester_MASTER import (
    MODEL_REGISTRY, analyse_case, closed_system_energy_balance, closed_system_entropy_generation, make_verdict,
    model_label, print_report, process_classification, properties_constant_cp, properties_linear_cp,
    properties_numerical, run_case, solve_temperature_bisection, steady_cv_energy_balance,
    steady_cv_entropy_generation, steady_cv_mass_balance)


def demonstration_cases() -> List[Dict[str, Any]]:
    """Return validation cases; these are demonstrations, not hidden user inputs."""
    return [
        {
            "title": "PROFESSOR DEMO — Data-centre waste-heat recovery",
            "model": "finite_stream_heat_recovery",
            "description": "An insulated exchanger cools server coolant from 80 C to 45 C while heating process water from 25 C to 60 C. Equal stream heat-capacity rates make the energy balance close exactly.",
            "hot_stream": {"name": "Server coolant", "mass_flow_kg_s": 0.5, "T_in_K": 353.15, "T_out_K": 318.15, "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "cold_stream": {"name": "Process water", "mass_flow_kg_s": 0.5, "T_in_K": 298.15, "T_out_K": 333.15, "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "T0_K": 298.15
        },
        {
            "title": "Baseline two-reservoir engine",
            "model": "fixed_reservoir_engine",
            "description": "600 K source, 300 K sink, 100 kW heat input and 30 kW work output.",
            "T_hot_K": 600.0, "T_cold_K": 300.0, "Q_hot_kw": 100.0, "W_out_kw": 30.0,
        },
        {
            "title": "Finite hot/cold stream example",
            "model": "finite_stream_engine",
            "description": "One hot stream cools and one cold stream warms; adiabatic complete boundary.",
            "hot_stream": {"name": "Hot source", "mass_flow_kg_s": 1.0,
                           "T_in_K": 600.0, "T_out_K": 400.0,
                           "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "cold_stream": {"name": "Cold sink", "mass_flow_kg_s": 2.0,
                            "T_in_K": 300.0, "T_out_K": 377.75,
                            "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "W_out_kw": 186.01,
            "apply_effective_temperature_limit": False,
        },
        {
            "title": "Finite-stream model-specific limit demonstration",
            "model": "finite_stream_engine",
            "description": "Same stream changes; explicitly enables the effective-temperature reference for comparison.",
            "hot_stream": {"name": "Hot source", "mass_flow_kg_s": 1.0,
                           "T_in_K": 600.0, "T_out_K": 400.0,
                           "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "cold_stream": {"name": "Cold sink", "mass_flow_kg_s": 2.0,
                            "T_in_K": 300.0, "T_out_K": 377.75,
                            "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "W_out_kw": 186.01,
            "apply_effective_temperature_limit": True,
        },
        {
            "title": "Impossible fixed-reservoir claim",
            "model": "fixed_reservoir_engine",
            "description": "60% claimed efficiency where the Carnot limit is 50%.",
            "T_hot_K": 600.0, "T_cold_K": 300.0, "Q_hot_kw": 100.0, "W_out_kw": 60.0,
        },
        {
            "title": "Variable Cp and phase-change property calculation",
            "model": "property_change",
            "description": "Property-only test using Cp(T)=1+0.0005T and a reversible phase-change ratio.",
            "T1_K": 500.0, "T2_K": 350.0, "cp_model": "linear", "a": 1.0, "b": 0.0005,
            "phase_delta_h_kj_kg": 250.0, "phase_delta_s_kj_kg_k": 0.80,
        },
        {
            "title": "Nonthermal case without an exergy model",
            "model": "unsupported_domain",
            "domain": "mechanical",
            "reason": "An input/output ratio alone does not supply a complete available-work or exergy model.",
        },
    ]


def resource_examples() -> List[Dict[str, Any]]:
    return [
        {
            "model": "mechanical_power", "title": "Walking energy harvester",
            "description": "Illustrative recoverable mechanical energy per step at the converter input.",
            "recoverable_energy_per_event_j": 4.0, "events_per_s": 2.0, "W_out_kw": 0.005,
        },
        {
            "model": "solar_blackbody", "title": "Ideal blackbody radiation example",
            "T_source_K": 5778.0, "T_ambient_K": 300.0, "irradiance_W_m2": 1000.0,
            "collection_area_m2": 2.0, "W_out_kw": 1.0,
        },
        {
            "model": "reaction_gibbs", "title": "Illustrative exergonic cell reaction",
            "reaction_direction": "Reactants to products as specified for Delta G",
            "delta_G_kj_per_mol_rxn": -200.0, "reaction_rate_mol_rxn_s": 0.01, "W_out_kw": 1.0,
        },
    ]


def self_test() -> List[str]:
    """Run deterministic internal checks; raise AssertionError on a failed test."""
    passed: List[str] = []

    # Constant Cp analytical formula and sign checks.
    dh, ds = properties_constant_cp(300.0, 500.0, 4.0)
    assert abs(dh - 800.0) < 1e-10 and ds > 0
    dh_cool, ds_cool = properties_constant_cp(500.0, 300.0, 4.0)
    assert dh_cool < 0 and ds_cool < 0
    passed.append("constant-Cp enthalpy/entropy formulas and sign convention")

    # Constant and linear Cp equivalence when b=0.
    dh_linear, ds_linear = properties_linear_cp(300.0, 500.0, 4.0, 0.0)
    assert abs(dh - dh_linear) < 1e-10 and abs(ds - ds_linear) < 1e-10
    passed.append("linear-Cp formula and constant-Cp special case")

    # Numerical integral agrees with analytical linear-Cp result.
    cp_func = lambda T: 1.0 + 0.0005 * T
    dh_exact, ds_exact = properties_linear_cp(350.0, 500.0, 1.0, 0.0005)
    dh_num, ds_num = properties_numerical(350.0, 500.0, cp_func)
    assert abs(dh_num - dh_exact) < 1e-7 and abs(ds_num - ds_exact) < 1e-7
    passed.append("adaptive Simpson integration vs analytical Cp(T)")

    # Bisection recovers the known endpoint.
    recovered = solve_temperature_bisection(dh_exact, 350.0, 400.0, 550.0, cp_func)
    assert abs(recovered - 500.0) < 1e-5
    passed.append("bracketed bisection temperature solver")

    # Fixed-reservoir reference case.
    baseline = analyse_case({"model": "fixed_reservoir_engine", "T_hot_K": 600,
                             "T_cold_K": 300, "Q_hot_kw": 100, "W_out_kw": 30})
    assert abs(baseline.calculations["Q_cold_rejected_kw"] - 70.0) < 1e-10
    assert abs(baseline.calculations["carnot_efficiency_fraction"] - 0.5) < 1e-10
    assert abs(baseline.calculations["actual_efficiency_fraction"] - 0.3) < 1e-10
    assert abs(baseline.calculations["S_gen_dot_kw_per_k"] - (70/300 - 100/600)) < 1e-10
    assert baseline.verdict.status == "FEASIBLE / NOT REJECTED"
    passed.append("fixed-reservoir First Law, Carnot, entropy and feasible verdict")

    # At the Carnot limit, the idealized engine is reversible.
    reversible = analyse_case({"model": "fixed_reservoir_engine", "T_hot_K": 600,
                               "T_cold_K": 300, "Q_hot_kw": 100, "W_out_kw": 50})
    assert reversible.verdict.status == "FEASIBLE / NOT REJECTED"
    assert process_classification(reversible.calculations["S_gen_dot_kw_per_k"],
                                  performance_limit_passed=True) == "REVERSIBLE (LIMITING CASE)"
    passed.append("reversible fixed-reservoir limiting case")

    # Impossible reservoir claim must fail both Carnot and entropy checks.
    impossible = analyse_case({"model": "fixed_reservoir_engine", "T_hot_K": 600,
                               "T_cold_K": 300, "Q_hot_kw": 100, "W_out_kw": 60})
    assert impossible.verdict.status == "NOT FEASIBLE"
    assert impossible.calculations["S_gen_dot_kw_per_k"] < 0
    passed.append("impossible fixed-reservoir claim rejected")

    # Finite streams: verify property rates and entropy generation.
    finite_case = demonstration_cases()[2]
    finite = analyse_case(finite_case)
    assert abs(finite.calculations["heat_released_by_hot_stream_kw"] - 836.0) < 1e-8
    assert finite.calculations["S_gen_dot_kw_per_k"] > 0
    assert finite.checks["first_law"]["passed"]
    assert finite.verdict.status == "INSUFFICIENT DATA / MODEL REQUIRED"
    passed.append("finite-stream properties, energy consistency and entropy generation")

    # Explicitly selected effective-temperature reference is checked but labelled.
    finite_limit_case = demonstration_cases()[3]
    finite_limit = analyse_case(finite_limit_case)
    assert finite_limit.calculations["effective_temperature_efficiency_reference"] is not None
    assert finite_limit.verdict.status == "INSUFFICIENT DATA / MODEL REQUIRED"
    assert finite_limit.checks["effective_temperature_reference_comparison"] == "BELOW REFERENCE"
    passed.append("effective-temperature reference is informational, not a universal feasibility limit")

    # Dedicated finite-stream heat-recovery exchanger case.
    recovery = analyse_case({
        "model": "finite_stream_heat_recovery",
        "hot_stream": {"name": "Server coolant", "mass_flow_kg_s": 0.5, "T_in_K": 353.15, "T_out_K": 318.15, "cp_model": "constant", "cp_kj_kg_k": 4.18},
        "cold_stream": {"name": "Process water", "mass_flow_kg_s": 0.5, "T_in_K": 298.15, "T_out_K": 333.15, "cp_model": "constant", "cp_kj_kg_k": 4.18},
        "T0_K": 298.15
    })
    assert recovery.checks["first_law"]["passed"]
    assert recovery.calculations["S_gen_dot_kw_per_k"] > 0
    assert recovery.calculations["exergy_destroyed_kw"] > 0
    assert recovery.verdict.status == "SECOND-LAW ADMISSIBLE / NOT REJECTED"
    passed.append("finite-stream heat-recovery energy, entropy and exergy-destruction model")
    mismatch = analyse_case({
        "model": "finite_stream_heat_recovery",
        "hot_stream": {"name": "Hot stream", "mass_flow_kg_s": 0.5, "T_in_K": 353.15, "T_out_K": 318.15, "cp_model": "constant", "cp_kj_kg_k": 4.18},
        "cold_stream": {"name": "Cold stream", "mass_flow_kg_s": 0.4, "T_in_K": 298.15, "T_out_K": 333.15, "cp_model": "constant", "cp_kj_kg_k": 4.18}
    })
    assert not mismatch.checks["first_law"]["passed"]
    assert mismatch.verdict.status == "NOT FEASIBLE UNDER THE STATED INSULATED-EXCHANGER MODEL"
    passed.append("heat-recovery model rejects an unaccounted energy mismatch")

    # Property / phase-change demonstration.
    prop = analyse_case(demonstration_cases()[5])
    assert prop.calculations["delta_h_kj_kg"] < 0
    assert prop.calculations["phase_change_temperature_K"] == 312.5
    passed.append("variable-Cp and phase-change property calculations")

    # General CV entropy balance. Adiabatic flow with entropy increase is positive.
    cv_in = [{"mass_flow_kg_s": 1.0, "specific_entropy_kj_kg_k": 1.0,
              "specific_enthalpy_kj_kg": 100.0}]
    cv_out = [{"mass_flow_kg_s": 1.0, "specific_entropy_kj_kg_k": 1.2,
               "specific_enthalpy_kj_kg": 80.0}]
    cv_sgen = steady_cv_entropy_generation(cv_in, cv_out, [])
    cv_mass = steady_cv_mass_balance(cv_in, cv_out)
    cv_energy = steady_cv_energy_balance(cv_in, cv_out, [], 20.0)
    assert abs(cv_sgen - 0.2) < 1e-12 and cv_mass.passed and cv_energy.passed
    passed.append("steady control-volume mass, energy and entropy balances")

    # Closed system entropy and energy balances.
    closed_energy = closed_system_energy_balance(10.0, [
        {"heat_into_system_kj": 15.0, "boundary_temperature_K": 300.0}], 5.0)
    closed_sgen = closed_system_entropy_generation(0.06, [
        {"heat_into_system_kj": 15.0, "boundary_temperature_K": 300.0}])
    assert closed_energy.passed and abs(closed_sgen - 0.01) < 1e-12
    passed.append("closed-system First Law and entropy balance")

    # A failed energy balance must be rejected.
    bad_energy_case = analyse_case({"model": "fixed_reservoir_engine", "T_hot_K": 600,
                                    "T_cold_K": 300, "Q_hot_kw": 100, "W_out_kw": 30,
                                    "Q_cold_rejected_kw": 60})
    assert bad_energy_case.verdict.status == "NOT FEASIBLE"
    passed.append("inconsistent First Law input correctly rejected")

    # A steady CV must satisfy mass conservation as well as its energy balance.
    bad_cv = analyse_case({
        "model": "steady_control_volume", "inlets": [
            {"mass_flow_kg_s": 1.0, "specific_enthalpy_kj_kg": 100.0,
             "specific_entropy_kj_kg_k": 1.0}],
        "outlets": [{"mass_flow_kg_s": 0.9, "specific_enthalpy_kj_kg": 80.0,
                     "specific_entropy_kj_kg_k": 1.2}],
        "heat": [], "W_out_kw": 28.0
    })
    assert not bad_cv.checks["mass_balance"]["passed"]
    assert bad_cv.verdict.status == "NOT FEASIBLE"
    passed.append("steady control-volume mass conservation check")

    # No independent work claim means no performance verdict, even if a
    # user elects to display the effective-temperature comparison.
    no_claim = dict(demonstration_cases()[3])
    no_claim.pop("W_out_kw")
    no_claim_result = analyse_case(no_claim)
    assert no_claim_result.verdict.status == "INSUFFICIENT DATA / MODEL REQUIRED"
    passed.append("missing independent work claim returns insufficient data")

    # Missing work outputs are derived only when the balance data determine them.
    derived_closed = analyse_case({"model": "closed_system", "delta_E_kj": 10.0,
        "delta_S_system_kj_K": 0.06, "heat": [{"heat_into_system_kj": 15.0, "boundary_temperature_K": 300.0}]})
    assert abs(derived_closed.inputs["W_out_kj"] - 5.0) < 1e-12
    assert derived_closed.input_status["W_out_kj"] == "DERIVED FROM CLOSED-SYSTEM FIRST LAW"
    derived_cv = analyse_case({"model": "steady_control_volume",
        "inlets": [{"mass_flow_kg_s": 1.0, "specific_enthalpy_kj_kg": 100.0, "specific_entropy_kj_kg_k": 1.0}],
        "outlets": [{"mass_flow_kg_s": 1.0, "specific_enthalpy_kj_kg": 80.0, "specific_entropy_kj_kg_k": 1.2}],
        "heat": []})
    assert abs(derived_cv.inputs["W_out_kw"] - 20.0) < 1e-12
    assert derived_cv.input_status["W_out_kw"] == "DERIVED FROM STEADY-FLOW FIRST LAW"
    passed.append("safe derivation and provenance tracking for closed-system and steady-CV work")

    # Negative entropy generation must be rejected.
    violation = make_verdict(sgen=-0.01, model_limit_valid=False)
    assert violation.status == "NOT FEASIBLE"
    assert process_classification(-0.01) == "IMPOSSIBLE — SECOND LAW VIOLATION"
    passed.append("negative entropy generation correctly rejected")

    # Model 6 (heat exchanger): rounded textbook data, temperature cross.
    rounded = {"model": "finite_stream_heat_recovery",
               "hot_stream": {"name": "Hot oil", "mass_flow_kg_s": 2.0, "T_in_K": 450.0, "T_out_K": 350.0,
                              "cp_model": "constant", "cp_kj_kg_k": 2.2},
               "cold_stream": {"name": "Cold water", "mass_flow_kg_s": 1.2, "T_in_K": 300.0, "T_out_K": 380.0,
                               "cp_model": "constant", "cp_kj_kg_k": 4.5833}, "T0_K": 298.15}
    assert analyse_case(rounded).verdict.status.startswith("NOT FEASIBLE")          # strict: 0.0032 kW mismatch
    assert analyse_case({**rounded, "energy_relative_tolerance": 1e-3}).verdict.status == "SECOND-LAW ADMISSIBLE / NOT REJECTED"
    crossed = {"model": "finite_stream_heat_recovery",
               "hot_stream": {"name": "H", "mass_flow_kg_s": 2.0, "T_in_K": 360.0, "T_out_K": 340.0,
                              "cp_model": "constant", "cp_kj_kg_k": 2.2},
               "cold_stream": {"name": "C", "mass_flow_kg_s": 88.0 / (4.18 * 70.0), "T_in_K": 300.0, "T_out_K": 370.0,
                               "cp_model": "constant", "cp_kj_kg_k": 4.18}}
    crossed_result = analyse_case(crossed)
    assert crossed_result.calculations["S_gen_dot_kw_per_k"] > 0
    assert crossed_result.verdict.status == "NOT FEASIBLE — TEMPERATURE CROSS"
    passed.append("Model 6 rounding allowance and temperature-cross rejection")

    # Model 7: walking harvester 4 J x 2 steps/s = 8 W.
    walking = analyse_case(resource_examples()[0])
    assert abs(walking.calculations["reversible_power_limit_kw"] - 0.008) < 1e-12
    assert walking.verdict.status == "FEASIBLE / NOT REJECTED"
    assert analyse_case({**resource_examples()[0], "W_out_kw": 0.010}).verdict.status == "NOT FEASIBLE"
    passed.append("Model 7 mechanical limit (8 W walking example; 10 W claim rejected)")

    # Model 8: Petela-Landsberg fraction for 5778 K / 300 K.
    r = 300.0 / 5778.0
    solar = analyse_case(resource_examples()[1])
    assert abs(solar.calculations["petela_landsberg_exergy_fraction"] - (1 - 4 * r / 3 + r ** 4 / 3)) < 1e-12
    assert solar.verdict.status == "FEASIBLE / NOT REJECTED"
    passed.append("Model 8 Petela-Landsberg fraction")

    # Model 9: -Delta G x rate = 200 kJ/mol x 0.01 mol/s = 2 kW.
    gibbs = analyse_case(resource_examples()[2])
    assert abs(gibbs.calculations["reversible_power_limit_kw"] - 2.0) < 1e-12
    assert analyse_case({**resource_examples()[2], "W_out_kw": 2.5}).verdict.status == "NOT FEASIBLE"
    passed.append("Model 9 Gibbs-energy limit (2 kW) and rejection of 2.5 kW claim")

    # Registry is complete and consistent with the dispatch table.
    numbered = [entry for entry in MODEL_REGISTRY if entry["number"] is not None]
    assert [entry["number"] for entry in numbered] == list(range(1, 10))
    for entry in numbered:
        assert model_label(entry["key"]).startswith(f"Model {entry['number']} ")
    assert {e["key"] for e in MODEL_REGISTRY if e["number"] is None} == {"property_change", "unsupported_domain"}
    passed.append("model registry: selectable models numbered 1-9; property utility and fallback unnumbered")

    # Model 5: refrigerator 500 kJ from 270 K, 650 kJ to 300 K, 150 kJ work -> COP 3.33 <= Carnot 9, S_gen > 0.
    fridge = analyse_case({"model": "refrigeration_cycle", "mode": "refrigerator", "energy_unit": "kJ",
                           "Q_cold": 500, "Q_hot": 650, "W_in": 150, "T_cold_K": 270, "T_hot_K": 300})
    assert abs(fridge.calculations["carnot_COP_refrigerator"] - 9.0) < 1e-12
    assert fridge.verdict.status == "FEASIBLE / NOT REJECTED"
    assert analyse_case({"model": "refrigeration_cycle", "Q_cold": 5, "W_in": 0.1, "T_cold_K": 260,
                         "T_hot_K": 300}).verdict.status == "NOT FEASIBLE"
    passed.append("Model 5 refrigerator Carnot COP (9) and rejection of COP 50")

    # Model 2 exact limit: hot 1 kg/s 600->400 K, cold 2 kg/s from 300 K, Cp 4.18 -> W_max = 272.34 kW.
    strict = dict(demonstration_cases()[2], apply_strict_reversible_limit=True)
    strict_result = analyse_case(strict)
    assert abs(strict_result.calculations["strict_reversible_work_limit_kw"] - 272.3399) < 1e-3
    assert strict_result.verdict.status == "FEASIBLE / NOT REJECTED"
    passed.append("Model 2 optional exact S_gen = 0 limit (272.34 kW reference case)")

    # Model 1: claimed work above heat input is a First Law verdict, not an input error.
    over = analyse_case({"model": "fixed_reservoir_engine", "T_hot_K": 600.0, "T_cold_K": 300.0,
                         "Q_hot_kw": 100.0, "W_out_kw": 110.0})
    assert over.verdict.status == "NOT FEASIBLE" and over.checks["second_law"] == "NOT EVALUATED"
    passed.append("Model 1 rejects work greater than heat input with a First Law verdict")

    return passed


def write_templates(folder: str = "thermo_case_templates") -> Path:
    """Write starter JSON files illustrating each supported case format."""
    target = Path(folder)
    target.mkdir(parents=True, exist_ok=True)
    templates = {
        "fixed_reservoir_engine.json": {
            "title": "My fixed-reservoir engine", "model": "fixed_reservoir_engine",
            "description": "Describe the process and relevant boundary.",
            "T_hot_K": 600.0, "T_cold_K": 300.0, "Q_hot_kw": 100.0, "W_out_kw": 30.0
        },
        "finite_stream_engine.json": {
            "title": "My finite-stream thermal process", "model": "finite_stream_engine",
            "description": "Manually identify the hot source and cold sink from the process.",
            "hot_stream": {"name": "Hot source", "mass_flow_kg_s": 1.0, "T_in_K": 600.0,
                           "T_out_K": 400.0, "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "cold_stream": {"name": "Cold sink", "mass_flow_kg_s": 2.0, "T_in_K": 300.0,
                            "T_out_K": 377.75, "cp_model": "constant", "cp_kj_kg_k": 4.18},
            "W_out_kw": 186.01,
            "apply_effective_temperature_limit": False
        },
        "refrigeration_cycle.json": {
            "title": "My refrigerator or heat pump", "model": "refrigeration_cycle", "mode": "refrigerator",
            "energy_unit": "kW", "T_cold_K": 255.15, "T_hot_K": 298.15, "Q_cold": 0.3, "W_in": 0.1,
            "description": "Give any two of Q_cold, Q_hot and W_in; the third comes from the First Law."},
        "steady_control_volume.json": {
            "title": "My steady control volume", "model": "steady_control_volume",
            "description": "List every mass and heat interaction crossing the chosen boundary.",
            "inlets": [{"mass_flow_kg_s": 1.0, "specific_enthalpy_kj_kg": 100.0,
                        "specific_entropy_kj_kg_k": 1.0}],
            "outlets": [{"mass_flow_kg_s": 1.0, "specific_enthalpy_kj_kg": 80.0,
                         "specific_entropy_kj_kg_k": 1.1}],
            "heat": [], "W_out_kw": 20.0, "include_kinetic_potential": False
        },
        "closed_system.json": {
            "title": "My closed-system process", "model": "closed_system",
            "description": "Specify total system changes and all heat transfers.",
            "delta_E_kj": 10.0, "delta_S_system_kj_K": 0.06,
            "heat": [{"heat_into_system_kj": 15.0, "boundary_temperature_K": 300.0}],
            "W_out_kj": 5.0
        },
        "property_change.json": {
            "title": "My property calculation", "model": "property_change",
            "description": "State 1 to state 2 property change.",
            "T1_K": 300.0, "T2_K": 500.0, "cp_model": "constant", "cp_kj_kg_k": 4.18
        }
    }
    templates.update({f"{example['model']}.json": example for example in resource_examples()})
    for filename, content in templates.items():
        with open(target / filename, "w", encoding="utf-8") as file:
            json.dump(content, file, indent=2)
    return target


def run_demonstrations() -> None:
    print("THERMODYNAMIC FEASIBILITY TESTER — VALIDATION DEMONSTRATIONS")
    passed = self_test()
    print(f"\nInternal self-tests: {len(passed)} passed")
    for i, test_name in enumerate(passed, 1):
        print(f"  {i}. PASS — {test_name}")
    for case in demonstration_cases() + resource_examples():
        print_report(run_case(case))
