"""Validation of the nine models against the literature.

Two kinds of comparison are made, and each row says which one it is:
  "Published data"      the reference is a number reported in the literature (JANAF tables, the NETL
                        handbook, Mady et al. 2020, Li et al. 2009);
  "Published equation"  the reference is computed here, without calling the engine, from a closed-form
                        result in the literature (Carnot, Bejan and Alebrahim, Clausius, Betz, Petela).
The Li et al. row is an inequality check: the measured output must lie below the code's ceiling.
Used for Data/Validation_against_literature.csv, Table 1 and Figure 2 of the manuscript.

Run from the project folder:  python data_export/validation_against_literature.py
"""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from thermodynamic_feasibility_tester_MASTER import run_case  # noqa: E402

FARADAY = 96485.33212        # C/mol
WATER_CP = 4.18               # kJ/(kg K)
RHO_AIR = 1.225               # kg/m3


def water(name, m, t_in, t_out=None):
    stream = {"name": name, "mass_flow_kg_s": m, "T_in_K": t_in, "cp_model": "constant", "cp_kj_kg_k": WATER_CP}
    if t_out is not None:
        stream["T_out_K"] = t_out
    return stream


def rows():
    out = []

    def add(model, quantity, conditions, unit, reference, computed, source, note="", kind="Published equation"):
        diff = computed - reference
        rel = 100 * diff / reference if reference and kind != "Published data, inequality" else float("nan")
        out.append({"model": model, "kind": kind, "quantity": quantity, "conditions": conditions, "unit": unit,
                    "reference_value": reference, "code_value": computed, "difference": diff,
                    "relative_difference_percent": rel, "reference_source": source, "note": note})

    # Model 1 - Carnot limit (Wang 2017; Lee et al. 2014).
    r = run_case({"model": "fixed_reservoir_engine", "T_hot_K": 600, "T_cold_K": 300, "Q_hot_kw": 100, "W_out_kw": 40})
    add(1, "Carnot efficiency", "T_H = 600 K, T_C = 300 K", "%", 100 * (1 - 300 / 600),
        r.calculations["carnot_efficiency_percent"], "Carnot limit, eta = 1 - T_C/T_H [Wang 2017; Lee et al. 2014]")

    # Model 2 - maximum work from a hot stream cooled to the environment (Bejan and Alebrahim 2001).
    # A very large cold stream at T0 behaves as the environment, so W_max tends to the stream exergy.
    th, t0, m = 363.15, 298.15, 1.0
    exergy = m * WATER_CP * ((th - t0) - t0 * math.log(th / t0))
    r = run_case({"model": "finite_stream_engine", "hot_stream": water("hot water", m, th, t0),
                  "cold_stream": water("environment", 1.0e5, t0), "apply_strict_reversible_limit": True})
    add(2, "Maximum work from a hot stream", "Water 1 kg/s cooled 90 -> 25 C; cold stream 1e5 kg/s at 25 C", "kW",
        exergy, r.calculations["strict_reversible_work_limit_kw"],
        "Stream exergy m cp[(T_H - T0) - T0 ln(T_H/T0)] [Bejan and Alebrahim 2001]",
        "Finite cold stream approaches the environment limit")

    # Model 3 - Gouy-Stodola lost work for adiabatic steam expansion (Pal 2017).
    r = run_case({"model": "steady_control_volume",
                  "inlets": [{"mass_flow_kg_s": 2, "specific_enthalpy_kj_kg": 3200, "specific_entropy_kj_kg_k": 6.8}],
                  "outlets": [{"mass_flow_kg_s": 2, "specific_enthalpy_kj_kg": 2600, "specific_entropy_kj_kg_k": 7.1}],
                  "heat": [], "W_out_kw": 1200})
    add(3, "Entropy generation, adiabatic turbine", "2 kg/s; s1 = 6.8, s2 = 7.1 kJ/kg K", "kW/K",
        2 * (7.1 - 6.8), r.calculations["S_gen_dot_kw_per_k"],
        "S_gen = m(s2 - s1); lost work T0 S_gen = 178.9 kW at 298.15 K [Pal 2017]")

    # Model 4 - Clausius statement: heat cannot flow from cold to hot unaided (Xue and Guo 2019).
    r = run_case({"model": "closed_system", "heat": [{"heat_into_system_kj": 100, "boundary_temperature_K": 300},
                                                     {"heat_into_system_kj": -100, "boundary_temperature_K": 600}],
                  "W_out_kj": 0, "delta_E_kj": 0, "delta_S_system_kj_K": 0})
    add(4, "Entropy generation, heat moved 300 K -> 600 K with no work", "Cyclic device, Q = 100 kJ", "kJ/K",
        100 / 600 - 100 / 300, r.calculations["S_gen_kj_per_k"],
        "Clausius statement: S_gen < 0, process impossible [Xue and Guo 2019]", "Verdict: " + r.verdict.status)

    # Model 5 - refrigeration COP and exergy efficiency (Mady et al. 2020, Table 2).
    q_c = 12000 * 0.29307107 / 1000            # 12 000 Btu/h in kW
    r = run_case({"model": "refrigeration_cycle", "mode": "refrigerator", "energy_unit": "kW",
                  "T_cold_K": 276.15, "T_hot_K": 298.15, "Q_cold": q_c, "W_in": q_c / 3.1})
    eta_ex = 100 * r.calculations["COP_refrigerator"] / r.calculations["carnot_COP_refrigerator"]
    add(5, "Exergy efficiency of a vapour-compression cycle", "T_evap = 3 C, T0 = 25 C, COP = 3.1", "%",
        27.3, eta_ex, "Mady et al. 2020, Table 2 (R410A, condenser outlet 45 C)",
        "Code uses the evaporating and ambient temperatures only; Mady's EES model includes the 45 C condenser",
        kind="Published data")

    # Model 6 - exergy destroyed in an exchanger, Gouy-Stodola (Pal 2017; Pal 2019).
    hot, cold = water("hot", 0.5, 363.15, 333.15), water("cold", 1.0, 293.15, 308.15)
    r = run_case({"model": "finite_stream_heat_recovery", "hot_stream": hot, "cold_stream": cold})
    s_gen_ref = 0.5 * WATER_CP * math.log(333.15 / 363.15) + 1.0 * WATER_CP * math.log(308.15 / 293.15)
    add(6, "Entropy generation, water-water exchanger", "Hot 0.5 kg/s 90 -> 60 C; cold 1 kg/s 20 -> 35 C", "kW/K",
        s_gen_ref, r.calculations["S_gen_dot_kw_per_k"],
        "S_gen = sum m cp ln(T_out/T_in); exergy destroyed T0 S_gen [Pal 2017; Pal 2019]")

    # Model 7 - Betz limit and the biomechanical harvester (Li et al. 2009).
    r = run_case({"model": "mechanical_power", "air_density_kg_m3": RHO_AIR, "swept_area_m2": 10,
                  "wind_speed_m_s": 8, "W_out_kw": 1.5})
    add(7, "Betz power ceiling", "A = 10 m2, v = 8 m/s, rho = 1.225 kg/m3", "W",
        16 / 27 * 0.5 * RHO_AIR * 10 * 8 ** 3, 1000 * r.calculations["reversible_power_limit_kw"],
        "Betz limit 16/27 of the wind kinetic power [Betz 1920]")
    r = run_case({"model": "mechanical_power", "recoverable_mechanical_power_kw": 0.0044, "W_out_kw": 0.0024})
    add(7, "Knee harvester: measured output vs ceiling", "Mechanical input 4.4 W, generative braking", "W",
        2.4, 1000 * r.calculations["reversible_power_limit_kw"],
        "Li et al. 2009, measured electrical output 2.4 W from 4.4 W mechanical input",
        "Code ceiling must not be below the measured output; verdict for the measured output: " + r.verdict.status,
        kind="Published data, inequality")

    # Model 8 - Petela-Landsberg radiation exergy (Petela 2003).
    r = run_case({"model": "solar_blackbody", "T_source_K": 6000, "T_ambient_K": 300, "irradiance_W_m2": 1000,
                  "collection_area_m2": 2, "W_out_kw": 1.8})
    ratio = 300 / 6000
    add(8, "Ideal radiation work ceiling", "G = 1000 W/m2, A = 2 m2, T_s = 6000 K, T0 = 300 K", "kW",
        2 * (1 - 4 / 3 * ratio + ratio ** 4 / 3), r.calculations["reversible_power_limit_kw"],
        "Petela 2003, psi = 1 - (4/3)(T0/Ts) + (1/3)(T0/Ts)^4")

    # Model 9 - hydrogen fuel cell: Gibbs energy (NIST-JANAF) vs the 1.229 V reversible voltage (NETL).
    r = run_case({"model": "reaction_gibbs", "reaction_direction": "H2 + 0.5 O2 -> H2O(l)",
                  "delta_G_kj_per_mol_rxn": -237.141, "reaction_rate_mol_rxn_s": 0.01, "W_out_kw": 2.0})
    add(9, "Reversible fuel-cell power", "H2 + 1/2 O2 -> H2O(l), 0.01 mol/s, 298.15 K", "kW",
        2 * FARADAY * 1.229 * 0.01 / 1000, r.calculations["reversible_power_limit_kw"],
        "W = nFE with E = 1.229 V [NETL Fuel Cell Handbook 2004]; code uses JANAF Gibbs energy [Chase 1998]",
        kind="Published data")

    # Property utility - sensible enthalpy of liquid water (NIST-JANAF).
    r = run_case({"model": "property_change", "T1_K": 298.15, "T2_K": 340, "cp_model": "constant", "cp_kj_kg_k": 4.184})
    add("Property utility", "Sensible enthalpy of liquid water", "298.15 -> 340 K, 1 bar", "kJ/kg",
        3.153 / 0.01801528, r.calculations["delta_h_kj_kg"], "NIST-JANAF Table H-065 [Chase 1998]",
        "Constant Cp = 4.184 kJ/kg K", kind="Published data")
    return out


if __name__ == "__main__":
    print(json.dumps(rows(), indent=1))
