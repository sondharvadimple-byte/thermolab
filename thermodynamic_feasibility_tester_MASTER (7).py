"""THERMODYNAMIC FEASIBILITY TESTER — calculation engine (terminal program and website).

Idea: draw a boundary around the device, then check
  1. First Law  — does the energy balance close?
  2. Second Law — is entropy generation S_gen >= 0?
  3. Limit      — is the claimed work below a valid reversible limit (if one exists)?
and give one verdict: FEASIBLE / NOT FEASIBLE / INSUFFICIENT DATA (or a model-specific label).

Models (stable internal key -> number shown on the website and in the terminal menu)
   1 fixed_reservoir_engine        heat engine between two fixed temperatures (Carnot)
   2 finite_stream_engine          hot stream + cold stream, work produced
   3 steady_control_volume         open steady device (turbine, compressor, pump, nozzle ...)
   4 closed_system                 fixed mass (piston, tank, full cycle)
   5 refrigeration_cycle           refrigerator / heat pump (work consumed, Carnot COP)
   6 finite_stream_heat_recovery   heat exchanger, no shaft work (+ exergy destroyed)
   7 mechanical_power              walking, pedalling, wind (Betz), hydro (rho g Q H)
   8 solar_blackbody               Petela-Landsberg radiation limit
   9 reaction_gibbs                fuel cell: output <= -Delta G x reaction rate
   -  property_change              utility: Delta h, Delta s, T_eff (no verdict; not a selectable model)
   -  unsupported_domain           fallback: MODEL REQUIRED when no validated model exists

Units: K, kW (rates), kJ (totals), kg/s, kJ/kg, kJ/(kg K).
Signs: heat INTO the boundary and work OUT of it are positive; stream changes are outlet - inlet.
Standard library only (Python 3.9+). Demo cases and self-tests live in feasibility_examples.py.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ABS_TOL, REL_TOL, S_GEN_TOL = 1.0e-8, 1.0e-6, 1.0e-8

# ============================================================================
# 1. Model registry: number, name, role, guidance and governing equations
# ============================================================================
MODELS: Dict[str, Dict[str, Any]] = {
    "fixed_reservoir_engine": dict(number=1, role="core", name="Fixed-temperature reservoir cyclic heat engine",
        guidance="Use for a cyclic engine exchanging heat with fixed-temperature reservoirs.",
        equations=["First Law: Qdot_H - Wdot_out - Qdot_C = 0", "Carnot efficiency: eta_C = 1 - T_C/T_H",
                   "Second Law: Sdot_gen = Qdot_C/T_C - Qdot_H/T_H"]),
    "finite_stream_engine": dict(number=2, role="core", name="Finite hot stream + finite cold stream (work produced)",
        guidance="Use for two finite streams with specified inlet/outlet states; current model checks balances but does not claim a universal reversible-work limit.",
        equations=["Stream enthalpy rate: Delta Hdot = mdot * Delta h", "Stream entropy rate: Delta Sdot = mdot * Delta s",
                   "Adiabatic overall First Law: Qdot_hot - Wdot_out - Qdot_cold = 0",
                   "Adiabatic overall Second Law: Sdot_gen = Delta Sdot_hot + Delta Sdot_cold"]),
    "steady_control_volume": dict(number=3, role="core", name="General steady control volume",
        guidance="Use for steady open systems when all mass streams, heat interactions and shaft work are identified.",
        equations=["Mass: sum(mdot_in) - sum(mdot_out) = 0",
                   "Energy: Qdot - Wdot_out + sum_in[mdot(h+V^2/2+gz)] - sum_out[mdot(h+V^2/2+gz)] = 0",
                   "Entropy: Sdot_gen = sum_out(mdot*s) - sum_in(mdot*s) - sum_j(Qdot_j/T_b,j) >= 0"]),
    "closed_system": dict(number=4, role="core", name="Closed system",
        guidance="Use for a closed system with total energy/entropy changes and boundary heat interactions.",
        equations=["First Law: Q - W_out - Delta E = 0", "Entropy: S_gen = Delta S_system - sum_j(Q_j/T_b,j) >= 0"]),
    "property_change": dict(number=None, role="utility", name="Property calculation (utility, no verdict)",
        guidance="Use for state-property changes only; this is not a complete device feasibility test.",
        equations=["Delta h = integral(Cp(T) dT)", "Delta s = integral(Cp(T)/T dT)"]),
    "unsupported_domain": dict(number=None, role="fallback", name="Model required (no validated model)",
        guidance="A dedicated domain model is required; do not substitute a Carnot model without justification.",
        equations=[]),
    "refrigeration_cycle": dict(number=5, role="core", name="Refrigerator / heat pump (work-consuming cycle)",
        guidance="Use for a cyclic device that consumes work to move heat from a cold reservoir to a hot one (refrigerator, freezer, air conditioner, heat pump).",
        equations=["First Law: Qdot_H = Qdot_C + Wdot_in",
                   "COP_R = Qdot_C/Wdot_in;  COP_HP = Qdot_H/Wdot_in",
                   "Carnot limits: COP_R,max = T_C/(T_H - T_C);  COP_HP,max = T_H/(T_H - T_C)",
                   "Second Law: Sdot_gen = Qdot_H/T_H - Qdot_C/T_C >= 0"]),
    "finite_stream_heat_recovery": dict(number=6, role="core", name="Heat exchanger (no shaft work)",
        guidance="Use for an insulated two-stream heat exchanger with no shaft work; checks energy closure, entropy generation and optional exergy destruction.",
        equations=["Hot-side heat release: Qdot_hot = -mdot_hot * Delta h_hot",
                   "Cold-side heat absorption: Qdot_cold = mdot_cold * Delta h_cold",
                   "Insulated exchanger First Law: Qdot_hot - Qdot_cold = 0",
                   "Second Law: Sdot_gen = mdot_hot*Delta s_hot + mdot_cold*Delta s_cold >= 0",
                   "Exergy destruction: Edot_destroyed = T0*Sdot_gen"]),
    "mechanical_power": dict(number=7, role="extension", name="Mechanical energy harvesting",
        guidance="Use for mechanical-to-electrical conversion (walking, pedalling, wind via Betz, hydro via rho*g*Q*H); no Carnot temperature is used.",
        equations=["Recoverable input: P_mech = E_event * events_per_s (or supplied directly)",
                   "First Law limit: Wdot_electrical <= P_mech",
                   "Optional entropy check: Sdot_gen = Qdot_rejected / T_rejection >= 0"]),
    "solar_blackbody": dict(number=8, role="extension", name="Solar radiation conversion",
        guidance="Use for conversion of ideal blackbody (solar) radiation; the Petela-Landsberg bound is a generous upper limit for real sunlight.",
        equations=["Incident power: Qdot_rad = G * A",
                   "Petela-Landsberg fraction: eta_L = 1 - (4/3)(T0/Ts) + (1/3)(T0/Ts)^4",
                   "Limit: Wdot_out <= eta_L * Qdot_rad"]),
    "reaction_gibbs": dict(number=9, role="extension", name="Electrochemical conversion (Gibbs energy)",
        guidance="Use for fuel cells and other electrochemical converters; the limit is -Delta G times the reaction rate.",
        equations=["Reversible non-expansion work rate: Wdot_rev = -Delta G * ndot_reaction",
                   "Limit: Wdot_out <= Wdot_rev (requires Delta G < 0 for net output)"]),
}
MODEL_REGISTRY = [{"number": m["number"], "key": k, "name": m["name"], "role": m["role"]}
                  for k, m in sorted(MODELS.items(), key=lambda item: item[1]["number"] or 99)]
SELECTABLE_MODELS = [entry for entry in MODEL_REGISTRY if entry["number"] is not None]   # numbered 1..9
MODEL_NUMBER = {key: m["number"] for key, m in MODELS.items()}


def model_label(key: str) -> str:
    """'Model 6 · Heat exchanger (no shaft work)'; unnumbered utilities show their name only."""
    if key not in MODELS:
        return key
    number_ = MODELS[key]["number"]
    return f"Model {number_} · {MODELS[key]['name']}" if number_ else MODELS[key]["name"]


# ============================================================================
# 2. Input validation and result objects
# ============================================================================
class ThermoInputError(ValueError):
    """Missing, invalid, or physically inconsistent input data."""


def number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ThermoInputError(f"{label} must be numeric.") from exc
    if not math.isfinite(result):
        raise ThermoInputError(f"{label} must be finite.")
    return result


def positive(value: Any, label: str) -> float:
    if (result := number(value, label)) <= 0.0:
        raise ThermoInputError(f"{label} must be greater than zero.")
    return result


def positive_temperature(value: Any, label: str = "Temperature") -> float:
    if (result := number(value, label)) <= 0.0:
        raise ThermoInputError(f"{label} must be above 0 K. Enter absolute temperature in Kelvin.")
    return result


def is_close_to_zero(value: float, scale: float = 1.0) -> bool:
    return abs(value) <= ABS_TOL + REL_TOL * max(abs(scale), 1.0)


def within_limit(work: float, limit: float) -> bool:
    return work <= limit + ABS_TOL + REL_TOL * max(limit, 1.0)


def check_required(data: Dict[str, Any], fields: Sequence[str], context: str) -> None:
    missing = [name for name in fields if name not in data or data[name] is None]
    if missing:
        raise ThermoInputError(f"Missing {context} field(s): {', '.join(missing)}.")


@dataclass
class BalanceCheck:
    residual: float
    passed: bool
    equation: str
    units: str


def balance(residual: float, scale: float, equation: str, units: str) -> BalanceCheck:
    return BalanceCheck(residual, is_close_to_zero(residual, scale), equation, units)


@dataclass
class Verdict:
    status: str
    reasons: List[str]
    scope_note: str = ("Thermodynamic verdict within the stated model and supplied information; "
                       "this does not establish practical, economic, or engineering viability.")


@dataclass
class CaseResult:
    title: str
    model: str
    description: str
    inputs: Dict[str, Any]
    calculations: Dict[str, Any]
    checks: Dict[str, Any]
    verdict: Verdict
    assumptions: List[str]
    input_status: Dict[str, str] = field(default_factory=dict)
    check_table: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"title": self.title, "model": self.model, "description": self.description, "inputs": self.inputs,
                "calculations": self.calculations, "checks": self.checks, "verdict": asdict(self.verdict),
                "assumptions": self.assumptions, "input_status": self.input_status, "check_table": self.check_table}


def result(case: Dict[str, Any], model: str, default_title: str, inputs, calculations, checks, verdict,
           assumptions) -> CaseResult:
    return CaseResult(case.get("title", default_title), model, case.get("description", ""), inputs, calculations,
                      checks, verdict, assumptions)


# ============================================================================
# 3. Verdict logic shared by all models
# ============================================================================
def entropy_generation_is_negative(sgen: float) -> bool:
    return sgen < -(ABS_TOL + REL_TOL * max(abs(sgen), 1.0))


def entropy_generation_is_reversible(sgen: float) -> bool:
    return abs(sgen) <= ABS_TOL + REL_TOL * max(abs(sgen), 1.0)


def second_law(sgen: float) -> str:
    return "FAIL" if entropy_generation_is_negative(sgen) else "PASS"


def process_classification(sgen: Optional[float], first_law: Optional[BalanceCheck] = None,
                           mass_balance: Optional[BalanceCheck] = None,
                           performance_limit_passed: Optional[bool] = None) -> str:
    """Reversible, irreversible, or impossible (and why)."""
    if first_law is not None and not first_law.passed:
        return "IMPOSSIBLE UNDER THE STATED MODEL"
    if mass_balance is not None and not mass_balance.passed:
        return "IMPOSSIBLE UNDER THE STATED STEADY-STATE MODEL"
    if sgen is not None and entropy_generation_is_negative(sgen):
        return "IMPOSSIBLE — SECOND LAW VIOLATION"
    if performance_limit_passed is False:
        return "IMPOSSIBLE — REVERSIBLE LIMIT EXCEEDED"
    if sgen is None:
        return "NOT CLASSIFIED — ENTROPY BALANCE NOT AVAILABLE"
    return "REVERSIBLE (LIMITING CASE)" if entropy_generation_is_reversible(sgen) else "IRREVERSIBLE (SECOND-LAW ADMISSIBLE)"


def make_verdict(first_law: Optional[BalanceCheck] = None, sgen: Optional[float] = None,
                 work_out: Optional[float] = None, work_limit: Optional[float] = None,
                 model_limit_valid: bool = False, extra_reasons: Optional[List[str]] = None,
                 mass_balance: Optional[BalanceCheck] = None,
                 work_limit_tolerance: Optional[float] = None) -> Verdict:
    """Any violation -> NOT FEASIBLE; else FEASIBLE if the model has a valid limit, else INSUFFICIENT DATA."""
    reasons, violations = list(extra_reasons or []), []
    if first_law is not None and not first_law.passed:
        violations.append(f"First Law consistency check failed: residual = {first_law.residual:.6g} {first_law.units}.")
    if mass_balance is not None and not mass_balance.passed:
        violations.append(f"Steady-state mass balance failed: residual = {mass_balance.residual:.6g} {mass_balance.units}.")
    if sgen is not None and entropy_generation_is_negative(sgen):
        violations.append(f"Entropy generation is negative ({sgen:.6g}); this violates the Second Law for the stated boundary/model.")
    if work_out is not None and work_limit is not None:
        tolerance = (ABS_TOL + REL_TOL * max(abs(work_limit), 1.0)) if work_limit_tolerance is None else work_limit_tolerance
        if work_out - work_limit > tolerance:
            violations.append(f"Claimed useful work ({work_out:.6g}) exceeds the modelled reversible upper limit ({work_limit:.6g}).")
    if violations:
        return Verdict("NOT FEASIBLE", violations + reasons)
    if sgen is not None:
        reasons.append("S_gen is approximately zero: reversible limiting case within numerical tolerance."
                       if entropy_generation_is_reversible(sgen) else
                       "S_gen is positive: irreversible, but Second-Law admissible for the stated model.")
    if first_law is not None and first_law.passed:
        reasons.append("First Law balance is satisfied within numerical tolerance.")
    if model_limit_valid:
        if work_out is not None and work_limit is not None:
            reasons.append("Claimed work does not exceed the reversible limit used by this model.")
        return Verdict("FEASIBLE / NOT REJECTED", reasons)
    reasons.append("The available balance checks do not establish a defensible reversible performance limit.")
    return Verdict("INSUFFICIENT DATA / MODEL REQUIRED", reasons)


# ============================================================================
# 4. Property calculations and numerical methods
# ============================================================================
def properties_constant_cp(T1: float, T2: float, cp: float) -> Tuple[float, float]:
    """Signed (Delta h, Delta s) for constant Cp: Cp(T2-T1), Cp ln(T2/T1)."""
    T1, T2 = positive_temperature(T1, "T1"), positive_temperature(T2, "T2")
    cp = positive(cp, "Cp")
    return cp * (T2 - T1), cp * math.log(T2 / T1)


def properties_linear_cp(T1: float, T2: float, a: float, b: float) -> Tuple[float, float]:
    """Exact integrals for Cp(T) = a + bT (Cp must stay positive)."""
    T1, T2 = positive_temperature(T1, "T1"), positive_temperature(T2, "T2")
    a, b = number(a, "Cp coefficient a"), number(b, "Cp coefficient b")
    if min(a + b * T1, a + b * T2) <= 0.0:
        raise ThermoInputError("Cp(T)=a+bT must remain positive over the entire temperature interval.")
    return a * (T2 - T1) + 0.5 * b * (T2**2 - T1**2), a * math.log(T2 / T1) + b * (T2 - T1)


def adaptive_simpson(function: Callable[[float], float], lower: float, upper: float,
                     tolerance: float = 1.0e-8, max_depth: int = 18) -> float:
    """Adaptive Simpson integration: split each interval until the error estimate is small."""
    if lower == upper:
        return 0.0
    if upper < lower:
        return -adaptive_simpson(function, upper, lower, tolerance, max_depth)
    if tolerance <= 0 or max_depth < 1:
        raise ThermoInputError("Integration tolerance and maximum depth must be positive.")
    simpson = lambda a, b, fa, fm, fb: (b - a) * (fa + 4.0 * fm + fb) / 6.0

    def refine(a, b, fa, fm, fb, whole, tol, depth):
        mid = 0.5 * (a + b)
        flm, frm = function(0.5 * (a + mid)), function(0.5 * (mid + b))
        left, right = simpson(a, mid, fa, flm, fm), simpson(mid, b, fm, frm, fb)
        delta = left + right - whole
        if depth <= 0 or abs(delta) <= 15.0 * tol:
            return left + right + delta / 15.0
        return refine(a, mid, fa, flm, fm, left, tol / 2.0, depth - 1) + refine(mid, b, fm, frm, fb, right, tol / 2.0, depth - 1)

    f_lo, f_mid, f_hi = function(lower), function(0.5 * (lower + upper)), function(upper)
    return refine(lower, upper, f_lo, f_mid, f_hi, simpson(lower, upper, f_lo, f_mid, f_hi), tolerance, max_depth)


def properties_numerical(T1: float, T2: float, cp_function: Callable[[float], float]) -> Tuple[float, float]:
    """Delta h = integral Cp dT and Delta s = integral Cp/T dT for any Cp(T)."""
    T1, T2 = positive_temperature(T1, "T1"), positive_temperature(T2, "T2")
    cp = lambda T: positive(cp_function(T), f"Cp({T:g} K)")
    return adaptive_simpson(cp, T1, T2), adaptive_simpson(lambda T: cp(T) / T, T1, T2)


def effective_temperature(delta_h: float, delta_s: float) -> float:
    """T_eff = |Delta h| / |Delta s| — an equivalent temperature, not by itself a Carnot limit."""
    dh, ds = number(delta_h, "Delta h"), number(delta_s, "Delta s")
    if abs(dh) <= ABS_TOL or abs(ds) <= ABS_TOL:
        raise ThermoInputError("T_eff requires non-zero Delta h and Delta s.")
    return positive_temperature(abs(dh) / abs(ds), "Effective temperature")


def phase_change_temperature(delta_h: float, delta_s: float) -> float:
    """Isothermal phase change: T = Delta h / Delta s."""
    dh, ds = number(delta_h, "Phase-change Delta h"), number(delta_s, "Phase-change Delta s")
    if abs(ds) <= ABS_TOL:
        raise ThermoInputError("Phase-change Delta s must be non-zero.")
    return positive_temperature(dh / ds, "Phase-change temperature")


def solve_temperature_bisection(target_delta_h: float, reference_temperature: float, lower_temperature: float,
                                upper_temperature: float, cp_function: Callable[[float], float],
                                tolerance: float = 1.0e-7, max_iterations: int = 150) -> float:
    """Find T such that integral(T_ref -> T) Cp dT = target, by bracketed bisection."""
    target = number(target_delta_h, "Target Delta h")
    reference = positive_temperature(reference_temperature, "Reference temperature")
    low = positive_temperature(lower_temperature, "Lower temperature bound")
    high = positive_temperature(upper_temperature, "Upper temperature bound")
    if high <= low:
        raise ThermoInputError("Upper temperature bound must exceed lower bound.")
    residual = lambda T: properties_numerical(reference, T, cp_function)[0] - target
    f_low, f_high = residual(low), residual(high)
    if abs(f_low) <= tolerance:
        return low
    if abs(f_high) <= tolerance:
        return high
    if f_low * f_high > 0:
        raise ThermoInputError("Bisection interval does not bracket a root.")
    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        f_mid = residual(mid)
        if abs(f_mid) <= tolerance or high - low <= tolerance:
            return mid
        if f_low * f_mid <= 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    raise ThermoInputError("Bisection did not converge within the iteration limit.")


def stream_properties(stream: Dict[str, Any]) -> Dict[str, float]:
    """Signed Delta h, Delta s and their rates (x mass flow) for one constant/linear-Cp stream."""
    if not isinstance(stream, dict):
        raise ThermoInputError("Each stream must be a JSON object/dictionary.")
    check_required(stream, ["name", "mass_flow_kg_s", "T_in_K", "T_out_K", "cp_model"], "stream")
    name = str(stream["name"])
    mass_flow = number(stream["mass_flow_kg_s"], f"{name} mass flow")
    if mass_flow <= 0:
        raise ThermoInputError(f"{name} mass flow must be positive.")
    t_in = positive_temperature(stream["T_in_K"], f"{name} inlet temperature")
    t_out = positive_temperature(stream["T_out_K"], f"{name} outlet temperature")
    cp_model = str(stream["cp_model"]).lower()
    if cp_model == "constant":
        check_required(stream, ["cp_kj_kg_k"], f"{name} constant-Cp model")
        dh, ds = properties_constant_cp(t_in, t_out, stream["cp_kj_kg_k"])
    elif cp_model == "linear":
        check_required(stream, ["a", "b"], f"{name} linear-Cp model")
        dh, ds = properties_linear_cp(t_in, t_out, stream["a"], stream["b"])
    else:
        raise ThermoInputError(f"{name}: cp_model must be 'constant' or 'linear'.")
    return {"mass_flow_kg_s": mass_flow, "T_in_K": t_in, "T_out_K": t_out, "delta_h_kj_kg": dh,
            "delta_s_kj_kg_k": ds, "delta_H_dot_kw": mass_flow * dh, "delta_S_dot_kw_k": mass_flow * ds}


# ============================================================================
# 5. Balance equations
# ============================================================================
def carnot_efficiency(T_hot: float, T_cold: float) -> float:
    """eta_Carnot = 1 - T_C/T_H."""
    th = positive_temperature(T_hot, "Hot reservoir temperature")
    tc = positive_temperature(T_cold, "Cold reservoir temperature")
    if th <= tc:
        raise ThermoInputError("A heat engine needs T_hot > T_cold for positive work output.")
    return 1.0 - tc / th


def actual_efficiency(useful_output: float, defined_input: float) -> float:
    output, energy_in = number(useful_output, "Useful output"), positive(defined_input, "Defined primary energy input")
    if output < 0:
        raise ThermoInputError("Useful work output cannot be negative in this engine model.")
    return output / energy_in


def fixed_reservoir_entropy_generation(Q_hot: float, T_hot: float, Q_cold_rejected: float, T_cold: float) -> float:
    """Clausius for a two-reservoir cycle: S_gen = Q_C/T_C - Q_H/T_H."""
    qh, qc = number(Q_hot, "Hot-side heat input"), number(Q_cold_rejected, "Cold-side rejected heat")
    th = positive_temperature(T_hot, "Hot reservoir temperature")
    tc = positive_temperature(T_cold, "Cold reservoir temperature")
    if qh < 0 or qc < 0:
        raise ThermoInputError("Heat magnitudes must be non-negative.")
    return qc / tc - qh / th


def _flow_sum(streams, label: str, prop: Optional[Callable[[Dict[str, Any]], float]] = None,
              need: Sequence[str] = ()) -> float:
    """Sum of mdot * prop(stream) over CV streams (prop=None sums mdot); mass flows must be >= 0."""
    total = 0.0
    for stream in streams:
        check_required(stream, ["mass_flow_kg_s", *need], label)
        mdot = number(stream["mass_flow_kg_s"], f"{label} mass flow")
        value = prop(stream) if prop else 1.0
        if mdot < 0:
            raise ThermoInputError(f"{label} mass flow cannot be negative.")
        total += mdot * value
    return total


def _heat_sum(heat, key: str, context: str, label: str, divide_by_T: bool) -> float:
    """sum(Q) or sum(Q/T_boundary) over the heat interactions."""
    total = 0.0
    for item in heat:
        check_required(item, [key, "boundary_temperature_K"] if divide_by_T else [key], context)
        q = number(item[key], label)
        if divide_by_T:
            q /= positive_temperature(item["boundary_temperature_K"], "Heat boundary temperature")
        total += q
    return total


def steady_cv_mass_balance(inlets: Sequence[Dict[str, float]], outlets: Sequence[Dict[str, float]]) -> BalanceCheck:
    """sum(mdot_in) - sum(mdot_out) = 0."""
    m_in, m_out = _flow_sum(inlets, "Inlet stream"), _flow_sum(outlets, "Outlet stream")
    return balance(m_in - m_out, max(abs(m_in), abs(m_out), 1.0), "sum(mdot_in) - sum(mdot_out) = 0", "kg/s")


def steady_cv_energy_balance(inlets, outlets, heat, work_out_kw: float, include_kinetic_potential: bool = False) -> BalanceCheck:
    """Q_in - W_out + sum mdot(h + V^2/2 + gz)_in - sum mdot(...)_out = 0."""
    def energy(label):
        def e(stream):                                    # specific energy h + V^2/2 + g z  (kJ/kg)
            h = number(stream["specific_enthalpy_kj_kg"], f"{label} specific enthalpy")
            if not include_kinetic_potential:
                return h
            v = number(stream.get("velocity_m_s", 0.0), f"{label} velocity")
            z = number(stream.get("elevation_m", 0.0), f"{label} elevation")
            g = number(stream.get("g_m_s2", 9.80665), f"{label} gravity")
            return h + v**2 / 2000.0 + g * z / 1000.0
        return e
    need = ["specific_enthalpy_kj_kg"]
    e_in = _flow_sum(inlets, "Inlet stream", energy("Inlet stream"), need)
    e_out = _flow_sum(outlets, "Outlet stream", energy("Outlet stream"), need)
    q_in = _heat_sum(heat, "heat_into_cv_kw", "CV heat interaction", "Heat into CV", False)
    work = number(work_out_kw, "Work output")
    return balance(q_in - work + e_in - e_out, max(abs(q_in), abs(work), abs(e_in), abs(e_out), 1.0),
                   "Qdot_in - Wdot_out + sum(mdot*e_in) - sum(mdot*e_out) = 0", "kW")


def steady_cv_entropy_generation(inlets, outlets, heat) -> float:
    """S_gen = sum_out(mdot s) - sum_in(mdot s) - sum(Q/T_b)."""
    s = lambda label: lambda stream: number(stream["specific_entropy_kj_kg_k"], f"{label} specific entropy")
    need = ["specific_entropy_kj_kg_k"]
    s_in = _flow_sum(inlets, "Inlet stream", s("Inlet stream"), need)
    s_out = _flow_sum(outlets, "Outlet stream", s("Outlet stream"), need)
    return s_out - s_in - _heat_sum(heat, "heat_into_cv_kw", "CV heat interaction", "Heat into CV", True)


def closed_system_energy_balance(delta_energy_kj: float, heat, work_out_kj: float) -> BalanceCheck:
    """Q - W - Delta E = 0."""
    q = 0.0
    for item in heat:
        check_required(item, ["heat_into_system_kj"], "closed-system heat interaction")
        q += number(item["heat_into_system_kj"], "Heat into system")
        if item.get("boundary_temperature_K") is not None:
            positive_temperature(item["boundary_temperature_K"], "Heat boundary temperature")
    delta_e, work = number(delta_energy_kj, "Delta E"), number(work_out_kj, "Work out")
    return balance(q - work - delta_e, max(abs(q), abs(work), abs(delta_e), 1.0), "Q_into - W_out - Delta E = 0", "kJ")


def closed_system_entropy_generation(delta_entropy_system_kj_k: float, heat) -> float:
    """S_gen = Delta S_system - sum(Q/T_b)."""
    delta_s = number(delta_entropy_system_kj_k, "System Delta S")
    return delta_s - _heat_sum(heat, "heat_into_system_kj", "closed-system heat interaction", "Heat into system", True)


# ============================================================================
# 6. The ten models
# ============================================================================
def analyse_fixed_reservoir_engine(case: Dict[str, Any]) -> CaseResult:
    """Model 1: Carnot limit, First Law and S_gen = Q_C/T_C - Q_H/T_H."""
    check_required(case, ["T_hot_K", "T_cold_K", "Q_hot_kw", "W_out_kw"], "fixed-reservoir engine")
    th = positive_temperature(case["T_hot_K"], "Hot reservoir temperature")
    tc = positive_temperature(case["T_cold_K"], "Cold reservoir temperature")
    qh = positive(case["Q_hot_kw"], "Heat input")
    work = number(case["W_out_kw"], "Work output")
    if work < 0:
        raise ThermoInputError("Work output cannot be negative for the heat-engine model.")
    qc = None if case.get("Q_cold_rejected_kw") is None else number(case["Q_cold_rejected_kw"], "Rejected heat")
    if qc is not None and qc < 0:
        raise ThermoInputError("Rejected heat cannot be negative for this engine model.")
    qc_first_law, scale = qh - work, max(qh, work, qc or 0.0, 1.0)
    if qc is None and qc_first_law < 0 and not is_close_to_zero(qc_first_law, scale):
        energy = BalanceCheck(qc_first_law, False, "Qdot_H - Wdot_out >= 0 for Qdot_C >= 0", "kW")
        note, sgen = "Claimed work exceeds all supplied heat. No nonnegative rejected heat can close the balance.", None
    else:
        if qc is None:
            qc, note = max(0.0, qc_first_law), "Rejected heat was inferred from the First Law: Q_C = Q_H - W_out."
        else:
            note = "Rejected heat was supplied and independently checked against the First Law."
        energy = balance(qc_first_law - qc, scale, "Qdot_H - Wdot_out - Qdot_C = 0", "kW")
        sgen = fixed_reservoir_entropy_generation(qh, th, qc, tc)
    eta, eta_carnot = actual_efficiency(work, qh), carnot_efficiency(th, tc)
    limit = qh * eta_carnot
    return result(case, "fixed_reservoir_engine", "Fixed-reservoir cyclic heat engine",
        {"T_hot_K": th, "T_cold_K": tc, "Q_hot_kw": qh, "W_out_kw": work, "Q_cold_rejected_kw": qc},
        {"Q_cold_rejected_kw": qc, "actual_efficiency_fraction": eta, "actual_efficiency_percent": 100.0 * eta,
         "carnot_efficiency_fraction": eta_carnot, "carnot_efficiency_percent": 100.0 * eta_carnot,
         "efficiency_margin_below_carnot_fraction": eta_carnot - eta, "reversible_work_limit_kw": limit,
         "S_gen_dot_kw_per_k": sgen},
        {"first_law": asdict(energy), "energy_input_note": note,
         "second_law": "NOT EVALUATED" if sgen is None else second_law(sgen),
         "performance_limit": "PASS" if within_limit(work, limit) else "FAIL",
         "process_classification": process_classification(sgen, energy, performance_limit_passed=within_limit(work, limit))},
        make_verdict(energy, sgen, work, limit, True),
        ["Cyclic device; working-fluid state returns to its initial state over each cycle.",
         "Only two heat reservoirs are included in the model.",
         "Q_H and Q_C are positive heat-rate magnitudes; W_out is positive work rate.",
         "Kinetic and potential energy changes are neglected."])


def _two_streams(case: Dict[str, Any], context: str, hot_msg: str, cold_msg: str):
    """Shared by Models 2 and 7: hot stream must cool, cold stream must warm."""
    check_required(case, ["hot_stream", "cold_stream"], context)
    hot, cold = stream_properties(case["hot_stream"]), stream_properties(case["cold_stream"])
    if hot["delta_h_kj_kg"] >= 0:
        raise ThermoInputError(hot_msg)
    if cold["delta_h_kj_kg"] <= 0:
        raise ThermoInputError(cold_msg)
    return hot, cold, -hot["delta_H_dot_kw"], cold["delta_H_dot_kw"]


def reversible_cold_outlet_temperature(cold_stream: Dict[str, Any], entropy_to_absorb_kw_k: float) -> float:
    """Cold outlet at which the cold stream gains exactly this entropy rate (makes S_gen = 0)."""
    mass = positive(cold_stream["mass_flow_kg_s"], "Cold stream mass flow rate")
    t_in = positive_temperature(cold_stream["T_in_K"], "Cold stream inlet temperature")
    target = entropy_to_absorb_kw_k / mass
    if cold_stream.get("cp_model", "constant").lower() == "constant":
        return t_in * math.exp(target / positive(cold_stream["cp_kj_kg_k"], "Cold stream Cp"))
    entropy = lambda t: stream_properties(dict(cold_stream, T_out_K=t, mass_flow_kg_s=1.0))["delta_s_kj_kg_k"]
    lo, hi = t_in, t_in * 2.0
    while entropy(hi) < target:
        hi *= 2.0
        if hi > 1.0e6:
            raise ThermoInputError("Could not bracket the reversible cold-outlet temperature.")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if entropy(mid) < target else (lo, mid)
    return 0.5 * (lo + hi)


def outlet_temperature_for_heat(stream: Dict[str, Any], heat_kw: float) -> float:
    """Outlet temperature at which mdot * Delta h = heat_kw (positive heats the stream), for any Cp model."""
    mass = positive(stream["mass_flow_kg_s"], f"{stream.get('name', 'Stream')} mass flow")
    t_in = positive_temperature(stream["T_in_K"], f"{stream.get('name', 'Stream')} inlet temperature")
    target = heat_kw / mass
    if str(stream.get("cp_model", "constant")).lower() == "constant":
        return positive_temperature(t_in + target / positive(stream["cp_kj_kg_k"], "Cp"), "Calculated outlet temperature")
    dh = lambda t: stream_properties(dict(stream, T_out_K=t, mass_flow_kg_s=1.0))["delta_h_kj_kg"] - target
    lo, hi = (t_in, t_in * 2.0) if target > 0 else (t_in * 0.5, t_in)
    while dh(lo) * dh(hi) > 0:
        lo, hi = (lo, hi * 2.0) if target > 0 else (lo * 0.5, hi)
        if hi > 1.0e6 or lo < 1.0e-6:
            raise ThermoInputError("Could not find an outlet temperature for the stated heat duty.")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if dh(lo) * dh(mid) > 0 else (lo, mid)
    return 0.5 * (lo + hi)


def _finite_stream_open_outlet(case: Dict[str, Any]) -> Optional[CaseResult]:
    """Model 2 with the cold-stream outlet unknown. With a claim, the First Law fixes the outlet (then the normal
    model runs); with no claim, only the exact reversible limit is reported. Returns None if the outlet is given."""
    check_required(case, ["hot_stream", "cold_stream"], "finite-stream thermal engine")
    cold_in = case["cold_stream"]
    if not isinstance(cold_in, dict) or cold_in.get("T_out_K") is not None:
        return None
    hot = stream_properties(case["hot_stream"])
    if hot["delta_h_kj_kg"] >= 0:
        raise ThermoInputError("The designated hot/source stream must cool: its outlet enthalpy must be lower than its inlet enthalpy.")
    q_hot = -hot["delta_H_dot_kw"]
    t_rev = reversible_cold_outlet_temperature(cold_in, -hot["delta_S_dot_kw_k"])
    w_max = q_hot - stream_properties(dict(cold_in, T_out_K=t_rev))["delta_H_dot_kw"]
    claimed = None if case.get("W_out_kw") is None else number(case["W_out_kw"], "Claimed work output")
    if claimed is not None and claimed < 0:
        raise ThermoInputError("Claimed work output cannot be negative in this engine model.")
    if claimed is not None and claimed < q_hot - ABS_TOL:
        t_out = outlet_temperature_for_heat(cold_in, q_hot - claimed)
        res = analyse_finite_stream_engine(dict(case, cold_stream=dict(cold_in, T_out_K=t_out), apply_strict_reversible_limit=True))
        res.calculations["cold_outlet_from_first_law_K"] = t_out
        res.verdict.reasons.append(f"The cold-stream outlet was not given; the First Law fixes it at {t_out:.5g} K "
                                   f"(Q_cold = Q_hot - W = {q_hot - claimed:.6g} kW).")
        return res
    calcs = {"heat_released_by_hot_stream_kw": q_hot, "reversible_cold_outlet_temperature_K": t_rev,
             "strict_reversible_work_limit_kw": w_max, "S_gen_dot_kw_per_k": None}
    common = ["W_max = Q_hot - Q_cold,rev, where the cold outlet T_rev makes S_gen = 0 for the stated hot stream "
              f"and the stated cold-stream flow and inlet: T_rev = {t_rev:.5g} K, W_max = {w_max:.6g} kW."]
    if claimed is None:
        verdict = Verdict("LIMIT CALCULATED — NO CLAIM TO TEST",
                          common + ["No work claim was given, so there is nothing to accept or reject; any claim up to W_max is admissible."])
        checks = {"performance_limit": "NOT APPLICABLE — no claim", "first_law": "NOT APPLICABLE — no claim"}
    else:
        calcs["work_used_for_assessment_kw"] = claimed
        verdict = Verdict("NOT FEASIBLE", [f"Claimed work {claimed:.6g} kW is not less than the {q_hot:.6g} kW released by the "
                                           "hot stream: no cold-stream outlet can satisfy the First Law."] + common)
        checks = {"first_law": "FAIL — claim exceeds the heat released by the hot stream", "performance_limit": "FAIL"}
    return result(case, "finite_stream_engine", "Finite hot-stream / cold-stream thermal device",
                  {"hot_stream": dict(case["hot_stream"]), "cold_stream": dict(cold_in), "claimed_W_out_kw": claimed},
                  calcs, checks, verdict,
                  ["Steady operation; the complete boundary encloses the device and both streams and is adiabatic.",
                   "The cold-stream outlet temperature is unknown; the limit uses the best possible (reversible) outlet."])


def analyse_finite_stream_engine(case: Dict[str, Any]) -> CaseResult:
    """Model 2: two finite streams and a work-producing device inside one adiabatic boundary."""
    open_outlet = _finite_stream_open_outlet(case)
    if open_outlet is not None:
        return open_outlet
    hot, cold, q_hot, q_cold = _two_streams(case, "finite-stream thermal engine",
        "The designated hot/source stream must cool: its outlet enthalpy must be lower than its inlet enthalpy.",
        "The designated cold/sink stream must warm: its outlet enthalpy must be higher than its inlet enthalpy.")
    work_balance = q_hot - q_cold
    if q_hot <= 0:
        raise ThermoInputError("The hot source must release a positive heat rate.")
    if work_balance < -ABS_TOL:
        raise ThermoInputError("The specified stream changes require net work input, not work production.")
    work_balance = 0.0 if abs(work_balance) <= ABS_TOL else work_balance
    claimed = case.get("W_out_kw")
    work = work_balance if claimed is None else number(claimed, "Claimed work output")
    if work < 0:
        raise ThermoInputError("Claimed work output cannot be negative in this engine model.")
    energy = balance(q_hot - work - q_cold, max(q_hot, work, q_cold, 1.0),
                     "Qdot_hot released - Wdot_out - Qdot_cold absorbed = 0 (adiabatic overall boundary)", "kW")
    sgen = hot["delta_S_dot_kw_k"] + cold["delta_S_dot_kw_k"]
    th_eff = effective_temperature(hot["delta_H_dot_kw"], hot["delta_S_dot_kw_k"])
    tc_eff = effective_temperature(cold["delta_H_dot_kw"], cold["delta_S_dot_kw_k"])
    eta = actual_efficiency(work, q_hot)
    # T_eff reference: shown for comparison only, never used for the verdict.
    use_teff, eta_ref, teff_ref = bool(case.get("apply_effective_temperature_limit", False)), None, None
    if th_eff > tc_eff:
        eta_ref = 1.0 - tc_eff / th_eff
        teff_ref = q_hot * eta_ref
        teff_note = ("Calculated as an effective-temperature reversible reference. It is a model assumption for this "
                     "thermal mapping, not a universal Carnot limit for all finite-stream processes.")
    else:
        teff_note = "No positive work-producing effective-temperature ordering: T_hot,eff must exceed T_cold,eff."
        if use_teff:
            raise ThermoInputError(teff_note)
    # Optional exact limit: the cold outlet that makes S_gen = 0 gives W_max = Q_hot - Q_cold,rev.
    use_strict, t_rev, strict_limit = bool(case.get("apply_strict_reversible_limit", False)), None, None
    if use_strict:
        t_rev = reversible_cold_outlet_temperature(case["cold_stream"], -hot["delta_S_dot_kw_k"])
        strict_limit = q_hot - stream_properties(dict(case["cold_stream"], T_out_K=t_rev))["delta_H_dot_kw"]
    notes = ["No independent work-output claim was supplied; the displayed work is inferred from stream enthalpy changes."] if claimed is None else []
    if use_strict:
        notes.append("Exact reversible limit applied: W_max = Q_hot - Q_cold,rev, where the cold outlet makes S_gen = 0 "
                     "for the stated hot stream and the stated cold-stream flow and inlet.")
    if use_teff and teff_ref is not None:
        notes.append("The optional effective-temperature reference is displayed for comparison only; it is not used as a universal reversible-work limit.")
    elif not use_strict:
        notes.append("No generally valid reversible-work limit is implemented for this finite-stream arrangement.")
    return result(case, "finite_stream_engine", "Finite hot-stream / cold-stream thermal device",
        {"hot_stream": dict(case["hot_stream"]), "cold_stream": dict(case["cold_stream"]), "claimed_W_out_kw": claimed,
         "apply_effective_temperature_limit": use_teff, "apply_strict_reversible_limit": use_strict},
        {"hot_stream_property_results": hot, "cold_stream_property_results": cold,
         "heat_released_by_hot_stream_kw": q_hot, "heat_absorbed_by_cold_stream_kw": q_cold,
         "work_from_stream_energy_balance_kw": work_balance, "work_used_for_assessment_kw": work,
         "actual_efficiency_relative_to_hot_stream_heat_fraction": eta,
         "actual_efficiency_relative_to_hot_stream_heat_percent": 100.0 * eta,
         "hot_effective_temperature_K": th_eff, "cold_effective_temperature_K": tc_eff,
         "effective_temperature_efficiency_reference": eta_ref, "effective_temperature_work_reference_kw": teff_ref,
         "S_gen_dot_kw_per_k": sgen, "reversible_cold_outlet_temperature_K": t_rev,
         "strict_reversible_work_limit_kw": strict_limit},
        {"first_law": asdict(energy), "second_law": second_law(sgen),
         "process_classification": process_classification(sgen, energy, performance_limit_passed=(
             None if not use_teff or teff_ref is None else within_limit(work, teff_ref))),
         "effective_temperature_reference_comparison": ("NOT AVAILABLE" if teff_ref is None else
             "BELOW REFERENCE" if within_limit(work, teff_ref) else "ABOVE REFERENCE"),
         "effective_temperature_limit_note": teff_note,
         "energy_balance_note": ("Work output was inferred from the two stream enthalpy changes." if claimed is None
                                 else "Claimed work was checked against the stream energy balance.")},
        make_verdict(energy, sgen, None if claimed is None else work, strict_limit, use_strict and claimed is not None, notes),
        ["Steady operation; both streams have the same inlet and outlet mass flow rates.",
         "The complete boundary encloses the device and both streams.",
         "The complete boundary is adiabatic to the surroundings; no external heat interaction is specified.",
         "Only sensible enthalpy changes represented by the selected Cp models are included.",
         "Kinetic and potential energy changes are neglected.",
         "T_eff=|Delta Hdot|/|Delta Sdot| is an equivalent stream descriptor.",
         ("The effective-temperature reference is displayed as an informational comparison only; it is not treated as a generally valid reversible-work limit."
          if use_teff else "The effective-temperature ratio is not used as a universal performance limit.")])


def _balance_verdict(energy: Optional[BalanceCheck], sgen: Optional[float], missing: List[str],
                     mass: Optional[BalanceCheck] = None) -> Verdict:
    """Models 3 and 4: any failed balance -> NOT FEASIBLE; all balances complete and closed -> FEASIBLE;
    otherwise INSUFFICIENT DATA naming what is missing (a passed First Law alone proves nothing about S_gen)."""
    verdict = make_verdict(energy, sgen, model_limit_valid=not missing, mass_balance=mass)
    if missing:
        verdict.reasons.insert(0 if verdict.status != "NOT FEASIBLE" else len(verdict.reasons),
                               f"Not evaluated: {', '.join(missing)}.")
        verdict.reasons = [r for r in verdict.reasons if not r.startswith("The available balance checks")]
    elif verdict.status != "NOT FEASIBLE":
        verdict.reasons.insert(0, "Every balance is complete and closes with S_gen >= 0, so the stated process "
                                  "is thermodynamically admissible.")
    return verdict


def analyse_steady_control_volume(case: Dict[str, Any]) -> CaseResult:
    """Model 3: mass, energy and entropy balances for a steady open device (turbine, compressor, pump, nozzle).
    Each balance is evaluated when its data are present; missing ones are reported, never invented."""
    check_required(case, ["inlets", "outlets"], "steady control volume")
    inlets, outlets, heat = case["inlets"], case["outlets"], case.get("heat") or []
    if not all(isinstance(x, list) for x in (inlets, outlets, heat)):
        raise ThermoInputError("inlets, outlets and heat must each be lists in the JSON input.")
    if not all(isinstance(item, dict) for item in inlets + outlets + heat):
        raise ThermoInputError("Every inlet, outlet and heat interaction must be a JSON object.")
    if not inlets and not outlets:
        raise ThermoInputError("At least one mass inlet or outlet is required for a control-volume model.")
    kepe = bool(case.get("include_kinetic_potential", False))
    work = None if case.get("W_out_kw") is None else number(case["W_out_kw"], "Work output")
    streams = inlets + outlets
    has = lambda key: all(stream.get(key) is not None for stream in streams)
    heat_T_known = all(item.get("boundary_temperature_K") is not None for item in heat)
    missing = []
    mass = steady_cv_mass_balance(inlets, outlets)
    energy = sgen = None
    if not has("specific_enthalpy_kj_kg"):
        missing.append("First Law (specific enthalpy of every inlet and outlet)")
    elif work is None:
        missing.append("First Law (shaft work)")
    else:
        energy = steady_cv_energy_balance(inlets, outlets, heat, work, kepe)
    if not has("specific_entropy_kj_kg_k"):
        missing.append("Second Law (specific entropy of every inlet and outlet)")
    elif not heat_T_known:
        missing.append("Second Law (boundary temperature of each heat interaction)")
    else:
        sgen = steady_cv_entropy_generation(inlets, outlets, heat)
    return result(case, "steady_control_volume", "Steady control-volume balance",
        {"inlets": inlets, "outlets": outlets, "heat": heat, "W_out_kw": work, "include_kinetic_potential": kepe},
        {"S_gen_dot_kw_per_k": sgen},
        {"mass_balance": asdict(mass), "first_law": asdict(energy) if energy else "NOT EVALUATED",
         "second_law": second_law(sgen) if sgen is not None else "NOT EVALUATED",
         "process_classification": process_classification(sgen, energy, mass)},
        _balance_verdict(energy, sgen, missing, mass),
        ["Steady state: accumulation of energy and entropy within the control volume is zero.",
         "All relevant mass streams and heat interactions crossing the selected boundary must be listed.",
         "Heat is positive into the control volume; shaft work is positive out (a compressor or pump has negative W_out).",
         "Kinetic/potential energy terms are included only when include_kinetic_potential is true."])


def analyse_closed_system(case: Dict[str, Any]) -> CaseResult:
    """Model 4: Q - W = Delta E and S_gen = Delta S - sum Q/T_b (a cycle gives the Clausius inequality).
    Delta S (and boundary temperatures) may be absent: the First Law is still checked."""
    check_required(case, ["heat"], "closed system")
    heat = case["heat"]
    if not isinstance(heat, list) or not all(isinstance(item, dict) for item in heat):
        raise ThermoInputError("heat must be a list of JSON objects in the input.")
    missing, energy, sgen = [], None, None
    delta_e = None if case.get("delta_E_kj") is None else number(case["delta_E_kj"], "Delta E")
    work = None if case.get("W_out_kj") is None else number(case["W_out_kj"], "Work out")
    delta_s = None if case.get("delta_S_system_kj_K") is None else number(case["delta_S_system_kj_K"], "System Delta S")
    calcs: Dict[str, Any] = {"S_gen_kj_per_k": None}
    if delta_e is None or work is None:
        missing.append("First Law (two of heat, work and Delta E are needed)")
    else:
        energy = closed_system_energy_balance(delta_e, heat, work)
    if case.get("U1_kj") is not None and delta_e is not None:
        calcs["U1_kj"] = number(case["U1_kj"], "Initial internal energy")
        calcs["U2_kj"] = calcs["U1_kj"] + delta_e
    if delta_s is None:
        missing.append("Second Law (system entropy change Delta S)")
    elif any(item.get("boundary_temperature_K") is None for item in heat):
        missing.append("Second Law (boundary temperature of each heat interaction)")
    else:
        sgen = calcs["S_gen_kj_per_k"] = closed_system_entropy_generation(delta_s, heat)
    return result(case, "closed_system", "Closed-system energy and entropy balance",
        {"delta_E_kj": delta_e, "delta_S_system_kj_K": delta_s, "heat": heat, "W_out_kj": work},
        calcs,
        {"first_law": asdict(energy) if energy else "NOT EVALUATED",
         "second_law": second_law(sgen) if sgen is not None else "NOT EVALUATED",
         "process_classification": process_classification(sgen, energy)},
        _balance_verdict(energy, sgen, missing),
        ["Closed system: no mass crosses the selected boundary.",
         "Delta E includes the energy changes retained in the supplied model (e.g. internal, kinetic and potential).",
         "Heat is positive into the system; work is positive out.",
         "Each heat interaction is represented at a constant boundary temperature."])


REFRIGERATION_ENERGY = {"Q_cold": "heat extracted from the cold space/reservoir",
                        "Q_hot": "heat rejected/delivered to the hot side", "W_in": "work (or electrical power) input"}


def analyse_refrigeration_cycle(case: Dict[str, Any]) -> CaseResult:
    """Model 5: refrigerator or heat pump. Q_H = Q_C + W_in; S_gen = Q_H/T_H - Q_C/T_C >= 0 (Carnot COP)."""
    mode = str(case.get("mode", "refrigerator")).lower().replace(" ", "_").replace("-", "_")
    if mode not in ("refrigerator", "heat_pump"):
        raise ThermoInputError("mode must be 'refrigerator' or 'heat_pump'.")
    unit = str(case.get("energy_unit", "kW"))
    if unit not in ("kW", "kJ"):
        raise ThermoInputError("energy_unit must be 'kW' (rates) or 'kJ' (amounts per cycle or period).")
    values = {}
    for key, label in REFRIGERATION_ENERGY.items():
        if case.get(key) is not None:
            values[key] = number(case[key], label)
            if values[key] < 0:
                raise ThermoInputError(f"Enter the {label} as a positive magnitude.")
    tc = None if case.get("T_cold_K") is None else positive_temperature(case["T_cold_K"], "Cold-side temperature")
    th = None if case.get("T_hot_K") is None else positive_temperature(case["T_hot_K"], "Hot-side temperature")
    if tc is not None and th is not None and th <= tc:
        raise ThermoInputError("A refrigerator or heat pump moves heat from a colder side to a hotter side: T_hot must exceed T_cold.")
    title = "Refrigerator" if mode == "refrigerator" else "Heat pump"
    inputs = {"mode": mode, "energy_unit": unit, **{k: values.get(k) for k in REFRIGERATION_ENERGY},
              "T_cold_K": tc, "T_hot_K": th}
    assumptions = ["Cyclic device: no net change of the working fluid over a cycle (or steady operation for rates).",
                   "Only two thermal reservoirs: the cold space at T_C and the hot side at T_H.",
                   "Heat and work are entered as positive magnitudes; W_in is the work (electricity) consumed."]
    missing = [REFRIGERATION_ENERGY[k] for k in REFRIGERATION_ENERGY if k not in values]
    if len(missing) > 1:
        return result(case, "refrigeration_cycle", title, inputs, {}, {"first_law": "NOT EVALUATED"},
                      Verdict("INSUFFICIENT DATA / MODEL REQUIRED",
                              [f"Two of the three energy quantities are needed; missing: {', '.join(missing)}."]), assumptions)
    derived = None
    if len(values) == 2:                                       # the First Law fixes the third quantity uniquely
        derived = next(k for k in REFRIGERATION_ENERGY if k not in values)
        values[derived] = {"Q_hot": lambda v: v["Q_cold"] + v["W_in"], "Q_cold": lambda v: v["Q_hot"] - v["W_in"],
                           "W_in": lambda v: v["Q_hot"] - v["Q_cold"]}[derived](values)
        if values[derived] < 0:
            raise ThermoInputError(f"The First Law gives a negative {REFRIGERATION_ENERGY[derived]}; check the supplied values.")
    qc, qh, w = values["Q_cold"], values["Q_hot"], values["W_in"]
    energy = balance(qh - qc - w, max(qh, qc, w, 1.0), "Q_H - Q_C - W_in = 0", unit)
    cop_r = qc / w if w > 0 else None
    cop_hp = qh / w if w > 0 else None
    calcs: Dict[str, Any] = {"Q_cold": qc, "Q_hot": qh, "W_in": w, "derived_from_first_law": derived,
                             "COP_refrigerator": cop_r, "COP_heat_pump": cop_hp}
    checks: Dict[str, Any] = {"first_law": asdict(energy)}
    sgen = None
    reasons = [f"{REFRIGERATION_ENERGY[derived].capitalize()} was calculated from the First Law: "
               f"{values[derived]:.6g} {unit}."] if derived else []
    if tc is None or th is None:
        checks["second_law"] = "NOT EVALUATED — both reservoir temperatures are needed"
        verdict = make_verdict(energy, None, model_limit_valid=False, extra_reasons=reasons + [
            "Supply the cold-side and hot-side temperatures to check the Carnot COP limit."])
    else:
        cop_r_max, cop_hp_max = tc / (th - tc), th / (th - tc)
        sgen = qh / th - qc / tc
        w_min = qc * (th / tc - 1.0) if mode == "refrigerator" else qh * (1.0 - tc / th)
        cop, cop_max, name = (cop_r, cop_r_max, "COP_R") if mode == "refrigerator" else (cop_hp, cop_hp_max, "COP_HP")
        calcs.update({"carnot_COP_refrigerator": cop_r_max, "carnot_COP_heat_pump": cop_hp_max,
                      "minimum_work_input": w_min, "S_gen_kj_per_k" if unit == "kJ" else "S_gen_dot_kw_per_k": sgen})
        checks["second_law"] = second_law(sgen)
        checks["performance_limit"] = ("FAIL" if entropy_generation_is_negative(sgen) else "PASS")
        checks["process_classification"] = process_classification(sgen, energy)
        if cop is not None:
            reasons.append(f"{name} = {cop:.4g} vs Carnot {name},max = {cop_max:.4g} "
                           f"({'exceeds' if entropy_generation_is_negative(sgen) else 'does not exceed'} the reversible limit).")
        else:
            reasons.append("No work input: heat cannot flow from the cold side to the hot side by itself (Clausius statement).")
        verdict = make_verdict(energy, sgen, model_limit_valid=True, extra_reasons=reasons)
    return result(case, "refrigeration_cycle", title, inputs, calcs, checks, verdict, assumptions)


def analyse_property_change(case: Dict[str, Any]) -> CaseResult:
    """Property utility (not a feasibility model): sensible Delta h, Delta s and T_eff, and/or a latent
    (phase-change) step at constant temperature. Totals are reported when the mass is given."""
    sensible = any(case.get(k) is not None for k in ("T1_K", "T2_K", "cp_kj_kg_k", "a", "b"))
    latent = case.get("latent_heat_kj_kg") is not None
    if not sensible and not latent and "phase_delta_h_kj_kg" not in case:
        check_required(case, ["T1_K", "T2_K", "cp_model"], "property calculation")
    dh = ds = 0.0
    calcs: Dict[str, Any] = {}
    if sensible:
        check_required(case, ["T1_K", "T2_K"], "property calculation")
        cp_model = str(case.get("cp_model", "constant")).lower()
        if cp_model == "constant":
            check_required(case, ["cp_kj_kg_k"], "constant-Cp property calculation")
            dh, ds = properties_constant_cp(case["T1_K"], case["T2_K"], case["cp_kj_kg_k"])
        elif cp_model == "linear":
            check_required(case, ["a", "b"], "linear-Cp property calculation")
            dh, ds = properties_linear_cp(case["T1_K"], case["T2_K"], case["a"], case["b"])
        else:
            raise ThermoInputError("Property cp_model must be 'constant' or 'linear'.")
    if latent:                                                      # isothermal phase change: Delta s = L / T
        check_required(case, ["phase_change_temperature_K"], "latent-heat property calculation")
        latent_h = number(case["latent_heat_kj_kg"], "Latent heat")
        t_phase = positive_temperature(case["phase_change_temperature_K"], "Phase-change temperature")
        calcs.update(latent_delta_h_kj_kg=latent_h, latent_delta_s_kj_kg_k=latent_h / t_phase)
        dh, ds = dh + latent_h, ds + latent_h / t_phase
    try:
        teff, teff_note = effective_temperature(dh, ds), "Equivalent temperature descriptor only; not by itself a reversible work limit."
    except ThermoInputError:
        teff, teff_note = None, "Not defined for zero Delta h or zero Delta s."
    phase_t = None
    if "phase_delta_h_kj_kg" in case or "phase_delta_s_kj_kg_k" in case:
        check_required(case, ["phase_delta_h_kj_kg", "phase_delta_s_kj_kg_k"], "phase-change property calculation")
        phase_t = phase_change_temperature(case["phase_delta_h_kj_kg"], case["phase_delta_s_kj_kg_k"])
    calcs = {"delta_h_kj_kg": dh, "delta_s_kj_kg_k": ds, **calcs, "effective_temperature_K": teff,
             "effective_temperature_note": teff_note, "phase_change_temperature_K": phase_t}
    if case.get("mass_kg") is not None:
        mass = positive(case["mass_kg"], "Mass")
        calcs.update(mass_kg=mass, delta_H_total_kj=mass * dh, delta_S_total_kj_per_k=mass * ds)
    return result(case, "property_change", "Thermal property-change calculation",
        {k: v for k, v in case.items() if k not in {"model", "title", "description"}}, calcs,
        {"property_model": "PASS"},
        Verdict("PROPERTY CALCULATION ONLY",
                ["This calculates state-property changes; it does not assess a complete device or establish S_gen."]),
        ["The selected Cp relation represents the material over the entire stated temperature interval."]
        + (["The phase change happens at constant temperature and pressure, so Delta s = L/T."] if latent else []))


def analyse_unsupported_domain(case: Dict[str, Any]) -> CaseResult:
    """Fallback (MODEL REQUIRED): no validated model — say so instead of inventing a Carnot temperature."""
    return result(case, "unsupported_domain", "Unsupported or incomplete thermodynamic model",
        {"domain": str(case.get("domain", "nonthermal/unspecified"))}, {},
        {"model_support": "NO APPLICABLE MODEL SELECTED"},
        Verdict("INSUFFICIENT DATA / MODEL REQUIRED",
                [str(case.get("reason", "A domain-specific reversible model and complete required data have not been provided."))]),
        ["No Carnot temperatures are invented for a nonthermal process.",
         "The assessment requires a supported route and its physical input data."])


STREAM_FIELDS = {"mass_flow_kg_s": "mass flow rate", "T_in_K": "inlet temperature", "T_out_K": "outlet temperature"}


def _stream_gaps(stream: Dict[str, Any]) -> List[str]:
    """Names of the data a stream still lacks (Cp counts once)."""
    gaps = [label for key, label in STREAM_FIELDS.items() if stream.get(key) is None]
    cp_model = str(stream.get("cp_model", "constant")).lower()
    if (cp_model == "constant" and stream.get("cp_kj_kg_k") is None) or (
            cp_model == "linear" and (stream.get("a") is None or stream.get("b") is None)):
        gaps.append("heat capacity Cp")
    return gaps


def _heat_loss(case: Dict[str, Any]) -> Tuple[float, Optional[float]]:
    """Heat lost from the exchanger to the surroundings (0 for an insulated exchanger) and its temperature."""
    loss = number(case.get("heat_loss_to_surroundings_kw") or 0.0, "Heat loss to surroundings")
    if loss < 0:
        raise ThermoInputError("Enter heat lost to the surroundings as a positive number.")
    t = case.get("surroundings_temperature_K")
    if loss > 0 and t is None:
        raise ThermoInputError("Give the surroundings temperature for the heat lost by the exchanger.")
    return loss, None if t is None else positive_temperature(t, "Surroundings temperature")


def _exchanger_cross(hs: Dict[str, Any], cs: Dict[str, Any], arrangement: str) -> List[str]:
    """Terminal-temperature violations: heat cannot flow from a colder to a hotter fluid."""
    t = lambda s, k: None if s.get(k) is None else number(s[k], k)
    hi, ho, ci, co = t(hs, "T_in_K"), t(hs, "T_out_K"), t(cs, "T_in_K"), t(cs, "T_out_K")
    rules = [("cold outlet is hotter than the hot inlet", co is not None and hi is not None and co > hi),
             ("cold inlet is not colder than the hot inlet", ci is not None and hi is not None and ci >= hi)]
    if arrangement == "parallel":
        rules.append(("cold outlet is hotter than the hot outlet (impossible in parallel flow)",
                      co is not None and ho is not None and co > ho))
    return [text for text, bad in rules if bad]


def analyse_finite_stream_heat_recovery(case: Dict[str, Any]) -> CaseResult:
    """Model 6: heat exchanger with W_shaft = 0. Works with whatever the data allow: a full stream analysis,
    one outlet derived from the energy balance, a duty-only energy check, or a temperature-cross check."""
    arrangement = str(case.get("arrangement", "counterflow")).lower()
    loss, t_surr = _heat_loss(case)
    hs, cs = case.get("hot_stream"), case.get("cold_stream")
    title = "Heat exchanger (no shaft work)"
    base_assumptions = ["Shaft work is zero by definition for a heat exchanger.",
                        "Steady operation with no mass accumulation in either stream.",
                        "The exchanger is insulated (no heat to the surroundings) unless a heat loss is given."]
    if not isinstance(hs, dict) or not isinstance(cs, dict):           # duty-only: First Law check, W = 0
        if case.get("hot_duty_kw") is None or case.get("cold_duty_kw") is None:
            raise ThermoInputError("Give both streams (flow, temperatures, Cp) or the heat released and absorbed (kW).")
        q_hot, q_cold = positive(case["hot_duty_kw"], "Heat released by the hot stream"), positive(case["cold_duty_kw"], "Heat absorbed by the cold stream")
        energy = balance(q_hot - q_cold - loss, max(q_hot, q_cold, 1.0), "Q_hot released - Q_cold absorbed - Q_loss = 0 (W_shaft = 0)", "kW")
        verdict = (Verdict("NOT FEASIBLE UNDER THE STATED EXCHANGER MODEL", [f"Energy mismatch is {energy.residual:.6g} kW."])
                   if not energy.passed else
                   Verdict("INSUFFICIENT DATA / MODEL REQUIRED", ["The energy balance closes with W_shaft = 0.",
                           "Entropy generation needs each stream's flow, inlet/outlet temperatures and Cp."]))
        return result(case, "finite_stream_heat_recovery", title, {"hot_duty_kw": q_hot, "cold_duty_kw": q_cold,
                      "heat_loss_to_surroundings_kw": loss}, {"heat_released_by_hot_stream_kw": q_hot,
                      "heat_absorbed_by_cold_stream_kw": q_cold, "energy_balance_residual_kw": energy.residual, "W_shaft_kw": 0.0},
                      {"first_law": asdict(energy), "second_law": "NOT EVALUATED — stream temperatures and flows needed"},
                      verdict, base_assumptions)
    gaps = {"hot stream": _stream_gaps(hs), "cold stream": _stream_gaps(cs)}
    outlet_gaps = [(name, stream) for name, stream in (("hot stream", hs), ("cold stream", cs))
                   if gaps[name] == ["outlet temperature"]]
    if len(outlet_gaps) == 1 and not gaps["hot stream" if outlet_gaps[0][0] == "cold stream" else "cold stream"]:
        name, stream = outlet_gaps[0]                                   # one outlet fixed by Q_hot = Q_cold + Q_loss
        other = cs if stream is hs else hs
        q_other = stream_properties(other)["delta_H_dot_kw"]
        heat = (-q_other - loss) if stream is cs else -(q_other + loss)
        t_out = outlet_temperature_for_heat(stream, heat)
        fixed = dict(case, **{("hot_stream" if stream is hs else "cold_stream"): dict(stream, T_out_K=t_out)})
        res = _exchanger_full(fixed, arrangement, loss, t_surr)
        res.calculations[f"{name.split()[0]}_outlet_from_first_law_K"] = t_out
        res.verdict.reasons.insert(0, f"The {name} outlet was not given; the energy balance fixes it at {t_out:.5g} K "
                                      f"({t_out - 273.15:.4g} °C), so the First Law closes by construction.")
        return res
    if any(gaps.values()):
        cross = _exchanger_cross(hs, cs, arrangement)
        missing = [f"{name} {', '.join(g)}" for name, g in gaps.items() if g]
        if cross:
            verdict = Verdict("NOT FEASIBLE — TEMPERATURE CROSS", ["Temperature cross: " + " and ".join(cross) +
                              ". Heat cannot flow from a colder to a hotter fluid, whatever the flow rates."])
        else:
            verdict = Verdict("INSUFFICIENT DATA / MODEL REQUIRED", [f"Missing: {'; '.join(missing)}.",
                              "With both outlets unknown, add the heat duty, one outlet temperature or the exchanger effectiveness."])
        return result(case, "finite_stream_heat_recovery", title, {"hot_stream": dict(hs), "cold_stream": dict(cs)},
                      {"W_shaft_kw": 0.0}, {"terminal_temperature_check": "PASS" if not cross else "FAIL: " + "; ".join(cross),
                      "first_law": "NOT EVALUATED — " + "; ".join(missing)}, verdict, base_assumptions)
    return _exchanger_full(case, arrangement, loss, t_surr)


def _exchanger_full(case: Dict[str, Any], arrangement: str, loss: float, t_surr: Optional[float]) -> CaseResult:
    """Complete two-stream exchanger: energy closure, S_gen >= 0, no temperature cross, exergy destroyed."""
    hot, cold, q_hot, q_cold = _two_streams(case, "finite-stream heat-recovery exchanger",
        "Hot-side stream must cool (outlet enthalpy below inlet enthalpy).",
        "Cold-side stream must warm (outlet enthalpy above inlet enthalpy).")
    residual, duty = q_hot - q_cold - loss, max(q_hot, q_cold, 1.0)
    insulated = loss == 0
    data_tol = case.get("energy_relative_tolerance")          # optional allowance for rounded textbook data
    if data_tol is not None:
        data_tol = number(data_tol, "Energy relative tolerance")
        if not 0.0 <= data_tol < 0.05:
            raise ThermoInputError("energy_relative_tolerance must be between 0 and 0.05 (5 %).")
    strict_pass = is_close_to_zero(residual, duty)
    rounding_pass = data_tol is not None and abs(residual) <= data_tol * duty
    energy = BalanceCheck(residual, strict_pass or rounding_pass,
                          "Qdot_hot released - Qdot_cold absorbed = 0 (insulated exchanger; no shaft work)" if insulated else
                          "Qdot_hot released - Qdot_cold absorbed - Qdot_loss = 0 (no shaft work)", "kW")
    hs, cs = case["hot_stream"], case["cold_stream"]
    cross = _exchanger_cross(hs, cs, arrangement)
    sgen = hot["delta_S_dot_kw_k"] + cold["delta_S_dot_kw_k"] + (loss / t_surr if loss else 0.0)
    t0 = case.get("T0_K")
    exergy = None if t0 is None else positive_temperature(t0, "Reference environment temperature") * sgen
    if not energy.passed:
        verdict = Verdict("NOT FEASIBLE UNDER THE STATED INSULATED-EXCHANGER MODEL" if insulated else
                          "NOT FEASIBLE UNDER THE STATED EXCHANGER MODEL", [
            f"Energy mismatch is {residual:.6g} kW. An external heat/work interaction or corrected stream data is required."])
    elif entropy_generation_is_negative(sgen):
        verdict = Verdict("NOT FEASIBLE — SECOND LAW VIOLATION", [
            f"The complete exchanger boundary gives negative entropy generation ({sgen:.6g} kW/K)."])
    elif cross:
        verdict = Verdict("NOT FEASIBLE — TEMPERATURE CROSS", [
            "Temperature cross: " + " and ".join(cross) + ". Heat cannot flow from a colder to a hotter "
            "fluid in any exchanger arrangement, even though the overall S_gen is non-negative."])
    else:
        verdict = Verdict("SECOND-LAW ADMISSIBLE / NOT REJECTED", ([
            f"Energy mismatch {residual:.4g} kW ({100 * abs(residual) / duty:.4g} % of the duty) is within the "
            f"{100 * data_tol:g} % allowance for rounded input data."] if rounding_pass and not strict_pass else []) + [
            ("The insulated-exchanger energy balance closes within tolerance." if insulated else
             f"The energy balance closes within tolerance, including {loss:.6g} kW lost to the surroundings."),
            "Entropy generation is non-negative for the complete two-stream boundary.",
            "This establishes thermodynamic admissibility for the stated exchanger model, not practical, economic, or optimal performance."])
    classification = ("IMPOSSIBLE UNDER THE STATED MODEL — ENERGY MISMATCH" if not energy.passed else
                      "IMPOSSIBLE — SECOND LAW VIOLATION" if entropy_generation_is_negative(sgen) else
                      "IMPOSSIBLE — TEMPERATURE CROSS" if cross else
                      "IRREVERSIBLE / SECOND-LAW ADMISSIBLE" if sgen > S_GEN_TOL else "REVERSIBLE LIMITING CASE")
    return result(case, "finite_stream_heat_recovery", "Finite-stream heat-recovery exchanger",
        {"hot_stream": dict(hs), "cold_stream": dict(cs), "T0_K": t0, "energy_relative_tolerance": data_tol,
         "heat_loss_to_surroundings_kw": loss, "arrangement": arrangement},
        {"heat_released_by_hot_stream_kw": q_hot, "heat_absorbed_by_cold_stream_kw": q_cold, "W_shaft_kw": 0.0,
         "energy_balance_residual_kw": residual,
         "hot_stream_entropy_rate_change_kw_per_k": hot["delta_S_dot_kw_k"],
         "cold_stream_entropy_rate_change_kw_per_k": cold["delta_S_dot_kw_k"],
         "S_gen_dot_kw_per_k": sgen, "exergy_destroyed_kw": exergy,
         "exergy_destruction_note": ("T0*S_gen; available only when the reference environment temperature T0 is supplied."
                                     if t0 is not None else "Not calculated: supply T0_K to calculate exergy destruction.")},
        {"first_law": asdict(energy), "second_law": second_law(sgen),
         "terminal_temperature_check": "PASS" if not cross else "FAIL: " + "; ".join(cross),
         "process_classification": classification,
         "performance_limit": "Not assessed: this is a heat-recovery exchanger, not a work-producing engine."},
        verdict,
        ["Steady operation with no mass accumulation in either stream.",
         "The selected boundary encloses both streams and the heat exchanger.",
         ("The exchanger is insulated from the surroundings and has no shaft-work interaction." if insulated else
          f"No shaft work; {loss:.6g} kW is lost to surroundings at {t_surr:.6g} K (its entropy is counted in S_gen)."),
         "The supplied Cp model represents each stream over its full temperature interval.",
         "Kinetic and potential energy changes and pressure-drop effects are neglected.",
         "The model checks thermodynamic admissibility; it does not predict heat-transfer area, pressure drop, cost, fouling or exchanger effectiveness."])


# --- Models 7-9: claimed output compared with a resource limit -------------
def resource_result(case, inputs, calculations, assumptions, missing: Sequence[str] = (),
                    rejection: Optional[str] = None) -> CaseResult:
    """Common result for Models 7-9: missing data, a direct rejection, or limit comparison."""
    if missing:
        verdict, comparison = Verdict("INSUFFICIENT DATA / MODEL REQUIRED", [f"Provide {', '.join(missing)} to evaluate this claim."]), "NOT EVALUATED"
    elif rejection:
        verdict, comparison = Verdict("NOT FEASIBLE", [rejection]), "FAIL"
    else:
        verdict = make_verdict(work_out=calculations["claimed_electrical_output_kw"],
                               work_limit=calculations["reversible_power_limit_kw"], model_limit_valid=True,
                               work_limit_tolerance=1e-9 * calculations["reversible_power_limit_kw"])
        comparison = "PASS" if verdict.status == "FEASIBLE / NOT REJECTED" else "FAIL"
    return CaseResult(case.get("title", case["model"].replace("_", " ").capitalize()), case["model"].lower(),
                      case.get("description", ""), inputs, calculations,
                      {"performance_limit": comparison,
                       "first_law_balance": "NOT EVALUATED — complete energy flows unavailable",
                       "entropy_balance": "NOT EVALUATED — entropy generation unavailable"}, verdict, assumptions)


def resource_number(case: Dict[str, Any], name: str, minimum: Optional[float] = 0.0) -> float:
    if isinstance(case[name], bool):
        raise ThermoInputError(f"{name} must be numeric.")
    value = number(case[name], name)
    if minimum is not None and value < minimum:
        raise ThermoInputError(f"{name} must be at least {minimum:g}.")
    return value


def _present(case, fields):
    inputs = {name: case[name] for name in fields if case.get(name) is not None}
    return inputs, [name for name in fields if name not in inputs]


BETZ_FRACTION = 16.0 / 27.0


def wind_power_kw(air_density_kg_m3: float, swept_area_m2: float, wind_speed_m_s: float) -> Tuple[float, float]:
    """Kinetic power of the wind 1/2 rho A v^3 and its Betz limit (16/27 of it), both in kW."""
    p = 0.5 * positive(air_density_kg_m3, "Air density") * positive(swept_area_m2, "Swept area") \
        * number(wind_speed_m_s, "Wind speed") ** 3 / 1000.0
    return p, BETZ_FRACTION * p


def hydraulic_power_kw(mass_flow_kg_s: float, head_m: float, g_m_s2: float = 9.81) -> float:
    """Ideal hydraulic power mdot g H in kW (mdot = rho Q for a volumetric flow)."""
    return positive(mass_flow_kg_s, "Water mass flow") * positive(g_m_s2, "g") * positive(head_m, "Head") / 1000.0


def _mechanical_input(case: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
    """Turn force/velocity, torque/speed, force x distance per event, wind or hydro data into the recoverable
    mechanical input the model compares with. Returns (fields for the model, calculations, assumptions)."""
    g = lambda k: case.get(k) is not None
    if g("torque_Nm") and (g("angular_velocity_rad_s") or g("rotational_speed_rpm")):
        omega = (number(case["angular_velocity_rad_s"], "Angular velocity") if g("angular_velocity_rad_s")
                 else number(case["rotational_speed_rpm"], "Rotational speed") * 2 * math.pi / 60)
        p = number(case["torque_Nm"], "Torque") * omega / 1000.0
        return {"recoverable_mechanical_power_kw": p}, {"mechanical_input_basis": "P = torque x angular velocity",
                                                        "angular_velocity_rad_s": omega, "mechanical_input_kw": p}, []
    if g("force_N") and g("velocity_m_s"):
        p = number(case["force_N"], "Force") * number(case["velocity_m_s"], "Velocity") / 1000.0
        return {"recoverable_mechanical_power_kw": p}, {"mechanical_input_basis": "P = force x velocity",
                                                        "mechanical_input_kw": p}, []
    if g("force_N") and g("displacement_m"):
        e = number(case["force_N"], "Force") * number(case["displacement_m"], "Displacement per event")
        return {"recoverable_energy_per_event_j": e}, {"mechanical_input_basis": "E per event = force x displacement",
                                                       "energy_per_event_j": e}, []
    if g("wind_speed_m_s") or g("swept_area_m2"):
        missing = [k for k in ("air_density_kg_m3", "swept_area_m2", "wind_speed_m_s") if not g(k)]
        if missing:
            return {"_missing": missing}, {}, []
        p, betz = wind_power_kw(case["air_density_kg_m3"], case["swept_area_m2"], case["wind_speed_m_s"])
        return {"recoverable_mechanical_power_kw": betz}, {"mechanical_input_basis": "Betz limit = (16/27) x 1/2 rho A v^3",
                "wind_kinetic_power_kw": p, "betz_limit_kw": betz}, ["Wind: at most 16/27 of the kinetic power can be extracted (Betz)."]
    if g("head_m"):
        if g("water_mass_flow_kg_s"):
            mdot = number(case["water_mass_flow_kg_s"], "Water mass flow")
        elif g("volumetric_flow_m3_s") and g("water_density_kg_m3"):
            mdot = number(case["volumetric_flow_m3_s"], "Volumetric flow") * number(case["water_density_kg_m3"], "Water density")
        else:
            return {"_missing": ["water_mass_flow_kg_s (or volumetric_flow_m3_s and water_density_kg_m3)"]}, {}, []
        gravity = number(case.get("g_m_s2", 9.81), "g")
        p = hydraulic_power_kw(mdot, case["head_m"], gravity)
        return {"recoverable_mechanical_power_kw": p}, {"mechanical_input_basis": "P = mdot g H (= rho g Q H)",
                "water_mass_flow_kg_s": mdot, "hydraulic_power_kw": p}, (
                [] if g("g_m_s2") else ["g = 9.81 m/s^2."]) + ["Gross head, no friction losses (ideal hydraulic limit)."]
    return {}, {}, []


def analyse_mechanical_power(case: Dict[str, Any]) -> CaseResult:
    """Model 7: electrical output <= recoverable mechanical input (+ optional heat and S_gen checks).
    The input may be given directly, per event, or as force/torque/wind/hydro data (see _mechanical_input)."""
    fields, basis_calcs, basis_assumptions = _mechanical_input(case)
    if "_missing" in fields:
        return resource_result(case, {}, {}, ["The supplied resource is recoverable mechanical work at the converter input."],
                               fields["_missing"])
    if fields and (case.get("recoverable_mechanical_power_kw") is not None or case.get("recoverable_energy_per_event_j") is not None):
        raise ThermoInputError("Supply the mechanical input on one basis only (power, energy per event, force, torque, wind or hydro).")
    if fields:
        res = analyse_mechanical_power({k: v for k, v in dict(case, **fields).items() if k in (
            "model", "title", "description", "W_out_kw", "recoverable_mechanical_power_kw", "recoverable_energy_per_event_j",
            "events_per_s", "heat_rejected_kw", "heat_rejection_temperature_K")})
        res.calculations = {**basis_calcs, **res.calculations}
        res.inputs = {k: v for k, v in case.items() if k not in ("model", "title", "description")}
        res.assumptions += basis_assumptions
        return res
    power_field, event_fields = "recoverable_mechanical_power_kw", ("recoverable_energy_per_event_j", "events_per_s")
    direct, events = case.get(power_field) is not None, any(case.get(f) is not None for f in event_fields)
    if direct and events:
        raise ThermoInputError("Supply mechanical power or event energy and rate, using one input basis.")
    required = ("W_out_kw", *event_fields) if events else ("W_out_kw", power_field)
    assumptions = ["The supplied resource is recoverable mechanical work at the converter input.",
                   "Average input and output use the same time interval. Ideal conversion is capped at 100%."]
    inputs, missing = _present(case, required)
    if not direct and not events:
        missing[-1] = f"{power_field} (or both {event_fields[0]} and {event_fields[1]})"
    if missing:
        return resource_result(case, inputs, {}, assumptions, missing)
    inputs = {name: resource_number(case, name) for name in required}
    power = (number(inputs[event_fields[0]] * (inputs[event_fields[1]] / 1000), "Recoverable mechanical power (kW)")
             if events else inputs[power_field])
    res = resource_result(case, inputs, {"claimed_electrical_output_kw": inputs["W_out_kw"],
                                         "reversible_power_limit_kw": power}, assumptions)
    remainder, tolerance = power - inputs["W_out_kw"], 1e-9 * power
    ok = remainder >= -tolerance
    res.calculations["power_remaining_after_electrical_output_kw"] = remainder
    res.checks.update(input_energy_limit="PASS" if ok else "FAIL",
                      first_law_balance=("Energy limit satisfied. Remaining power must leave as heat or other output."
                                         if ok else "FAIL — electrical output exceeds mechanical input."),
                      entropy_balance="Add a heat-rejection temperature to calculate entropy generation.")
    heat = energy = sgen = None
    if case.get("heat_rejected_kw") is not None:                      # measured heat loss: check the energy balance
        heat = res.inputs["heat_rejected_kw"] = resource_number(case, "heat_rejected_kw")
        energy = BalanceCheck(remainder - heat, abs(remainder - heat) <= 1e-9 * max(power, heat),
                              "Mechanical input - electrical output - rejected heat = 0", "kW")
        res.calculations["first_law_residual_kw"] = remainder - heat
        res.checks["first_law_balance"] = "PASS" if energy.passed else "FAIL"
    elif ok:
        heat = max(0.0, remainder)
        res.checks["heat_rejection_basis"] = "Inferred from the energy balance."
    if heat is not None:
        res.calculations["heat_rejected_kw"] = heat
    if case.get("heat_rejection_temperature_K") is not None:           # S_gen = Q_rejected / T_rejection
        temperature = positive_temperature(resource_number(case, "heat_rejection_temperature_K"), "Heat rejection temperature")
        res.inputs["heat_rejection_temperature_K"] = temperature
        if heat is not None and ok and (energy is None or energy.passed):
            sgen = res.calculations["S_gen_dot_kw_per_k"] = heat / temperature
            res.checks.update(entropy_balance="PASS", process_classification=process_classification(sgen, energy))
            res.assumptions.append("Steady converter with no entropy accumulation or material flow. "
                                   "All remaining input leaves as heat at the single supplied boundary temperature.")
        else:
            res.checks["entropy_balance"] = "Resolve the energy-balance failure before evaluating entropy."
    res.verdict = make_verdict(first_law=energy, sgen=sgen, work_out=inputs["W_out_kw"], work_limit=power,
                               model_limit_valid=True, work_limit_tolerance=tolerance)
    return res


def analyse_solar_blackbody(case: Dict[str, Any]) -> CaseResult:
    """Model 8: output <= Petela-Landsberg fraction x incident radiation."""
    fields = ("T_source_K", "T_ambient_K", "irradiance_W_m2", "collection_area_m2", "W_out_kw")
    assumptions = ["Ideal undiluted blackbody radiation with a blackbody reference environment.",
                   "Irradiance and effective collection area describe the same incident beam at the aperture.",
                   "The bound describes radiative exergy and applies under the ideal radiation model."]
    inputs, missing = _present(case, fields)
    if missing:
        return resource_result(case, inputs, {}, assumptions, missing)
    inputs = {name: resource_number(case, name) for name in fields}
    source = positive_temperature(inputs["T_source_K"], "Radiation source temperature")
    ambient = positive_temperature(inputs["T_ambient_K"], "Ambient temperature")
    if source < ambient:
        raise ThermoInputError("This solar model requires a source at least as warm as the environment.")
    incident = number((inputs["irradiance_W_m2"] / 1000) * positive(inputs["collection_area_m2"], "Collection area"),
                      "Incident power (kW)")
    r = ambient / source
    fraction = (1 - r)**2 * (r**2 + 2 * r + 3) / 3     # = 1 - (4/3)r + (1/3)r^4, factored for accuracy
    return resource_result(case, inputs, {"incident_radiation_kw": incident, "petela_landsberg_exergy_fraction": fraction,
                                          "claimed_electrical_output_kw": inputs["W_out_kw"],
                                          "reversible_power_limit_kw": incident * fraction}, assumptions)


def analyse_reaction_gibbs(case: Dict[str, Any]) -> CaseResult:
    """Model 9: output <= -Delta G x reaction rate."""
    fields = ("reaction_direction", "delta_G_kj_per_mol_rxn", "reaction_rate_mol_rxn_s", "W_out_kw")
    assumptions = ["Reaction Gibbs energy applies to the stated direction at the operating temperature, pressure and composition.",
                   "Both molar quantities use moles of reaction extent for the same stoichiometry.",
                   "The reaction is the sole work source. Reversible non-expansion work per mole is minus Delta G."]
    inputs, missing = _present(case, fields)
    if missing:
        return resource_result(case, inputs, {}, assumptions, missing)
    if not isinstance(inputs["reaction_direction"], str) or not inputs["reaction_direction"].strip():
        raise ThermoInputError("reaction_direction must describe the direction used for Delta G.")
    inputs["reaction_direction"] = inputs["reaction_direction"].strip()
    delta_g = inputs["delta_G_kj_per_mol_rxn"] = resource_number(case, fields[1], minimum=None)
    rate = inputs["reaction_rate_mol_rxn_s"] = positive(resource_number(case, fields[2]), "Reaction rate")
    claimed = inputs["W_out_kw"] = resource_number(case, "W_out_kw")
    signed = number(-delta_g * rate, "Signed reversible work rate (kW)")
    return resource_result(case, inputs, {"claimed_electrical_output_kw": claimed,
                                          "signed_reversible_nonexpansion_work_kw": signed,
                                          "minimum_external_work_input_kw": max(0.0, -signed),
                                          "reversible_power_limit_kw": max(0.0, signed)}, assumptions,
                           rejection="The specified forward reaction requires external work input at the stated conditions."
                           if delta_g > 0 else None)


# ============================================================================
# 7. Entry point: tag inputs (given / derived / missing), run the model, add the equation trail
# ============================================================================
ANALYSERS = {"fixed_reservoir_engine": analyse_fixed_reservoir_engine,
             "finite_stream_engine": analyse_finite_stream_engine,
             "finite_stream_heat_recovery": analyse_finite_stream_heat_recovery,
             "steady_control_volume": analyse_steady_control_volume, "closed_system": analyse_closed_system,
             "property_change": analyse_property_change, "refrigeration_cycle": analyse_refrigeration_cycle, "unsupported_domain": analyse_unsupported_domain,
             "mechanical_power": analyse_mechanical_power, "solar_blackbody": analyse_solar_blackbody,
             "reaction_gibbs": analyse_reaction_gibbs}
REQUIRED = {"fixed_reservoir_engine": ["T_hot_K", "T_cold_K", "Q_hot_kw", "W_out_kw"],
            "finite_stream_engine": ["hot_stream", "cold_stream"], "finite_stream_heat_recovery": [],
            "steady_control_volume": ["inlets", "outlets"], "closed_system": ["heat"],
            "refrigeration_cycle": ["T_cold_K", "T_hot_K"],
            "property_change": [], "unsupported_domain": [], "mechanical_power": ["W_out_kw"],
            "solar_blackbody": ["T_source_K", "T_ambient_K", "irradiance_W_m2", "collection_area_m2", "W_out_kw"],
            "reaction_gibbs": ["reaction_direction", "delta_G_kj_per_mol_rxn", "reaction_rate_mol_rxn_s", "W_out_kw"]}


def _resolve_case_inputs(case: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Derive only values the First Law fixes uniquely, and label every input."""
    resolved, model = dict(case), str(case.get("model", "")).lower()
    status = {k: "GIVEN" if v is not None else "NOT PROVIDED" for k, v in case.items() if k not in {"model", "title", "description"}}
    checked = "GIVEN — CHECKED AGAINST FIRST LAW"
    if model == "fixed_reservoir_engine":
        if case.get("Q_cold_rejected_kw") is None and all(case.get(k) is not None for k in ("Q_hot_kw", "W_out_kw")):
            q_c = number(case["Q_hot_kw"], "Heat input") - number(case["W_out_kw"], "Work output")
            if q_c >= 0:
                resolved["Q_cold_rejected_kw"], status["Q_cold_rejected_kw"] = q_c, "DERIVED FROM FIRST LAW"
            else:
                status["Q_cold_rejected_kw"] = "CANNOT BE DERIVED — CLAIMED WORK EXCEEDS HEAT INPUT"
        elif case.get("Q_cold_rejected_kw") is not None:
            status["Q_cold_rejected_kw"] = checked
    elif model == "finite_stream_engine":
        if isinstance(case.get("cold_stream"), dict) and case["cold_stream"].get("T_out_K") is None:
            status["cold_stream.T_out_K"] = ("DERIVED FROM FIRST LAW (Q_cold = Q_hot - W)" if case.get("W_out_kw") is not None
                                             else "NOT PROVIDED — REVERSIBLE VALUE USED FOR THE LIMIT")
            if case.get("W_out_kw") is None:
                status["W_out_kw"] = "NOT PROVIDED — ONLY THE REVERSIBLE LIMIT IS CALCULATED"
        elif case.get("W_out_kw") is None and isinstance(case.get("hot_stream"), dict) and isinstance(case.get("cold_stream"), dict):
            status["W_out_kw"] = "DERIVED FROM STREAM ENERGY BALANCE (NOT A CLAIM)"
        elif case.get("W_out_kw") is not None:
            status["W_out_kw"] = "GIVEN — CHECKED AGAINST STREAM ENERGY BALANCE"
    elif model == "steady_control_volume":
        lists = all(isinstance(case.get(k, []), list) for k in ("inlets", "outlets", "heat")) and "inlets" in case
        enthalpies = lists and all(isinstance(x, dict) and x.get("specific_enthalpy_kj_kg") is not None
                                   for x in case.get("inlets", []) + case.get("outlets", []))
        if case.get("W_out_kw") is None and enthalpies:
            kepe = bool(case.get("include_kinetic_potential", False))
            def rate(s):  # mdot (h + V^2/2 + g z)
                e = number(s.get("specific_enthalpy_kj_kg"), "Specific enthalpy")
                if kepe:
                    e += (number(s.get("velocity_m_s", 0.0), "Stream velocity") ** 2 / 2000.0
                          + 9.80665 * number(s.get("elevation_m", 0.0), "Stream elevation") / 1000.0)
                return positive(s.get("mass_flow_kg_s"), "Stream mass flow") * e
            resolved["W_out_kw"] = (sum(number(x.get("heat_into_cv_kw"), "Heat rate") for x in case.get("heat") or [])
                                    + sum(map(rate, case["inlets"])) - sum(map(rate, case["outlets"])))
            status["W_out_kw"] = "DERIVED FROM STEADY-FLOW FIRST LAW"
        elif case.get("W_out_kw") is not None:
            status["W_out_kw"] = checked
    elif model == "refrigeration_cycle":
        known = [k for k in REFRIGERATION_ENERGY if case.get(k) is not None]
        if len(known) == 2:
            status[next(k for k in REFRIGERATION_ENERGY if k not in known)] = "DERIVED FROM FIRST LAW (Q_H = Q_C + W_in)"
        elif len(known) == 3:
            status.update({k: checked for k in known})
    elif model == "closed_system":
        if case.get("delta_E_kj") is None and case.get("U1_kj") is not None and case.get("U2_kj") is not None:
            resolved["delta_E_kj"] = number(case["U2_kj"], "Final internal energy") - number(case["U1_kj"], "Initial internal energy")
            status["delta_E_kj"] = "DERIVED FROM U2 - U1"
        q_known = isinstance(case.get("heat"), list) and all(isinstance(x, dict) and x.get("heat_into_system_kj") is not None
                                                            for x in case["heat"])
        if resolved.get("delta_E_kj") is None and case.get("W_out_kj") is not None and q_known:
            resolved["delta_E_kj"] = (sum(number(x["heat_into_system_kj"], "Heat transfer") for x in case["heat"])
                                      - number(case["W_out_kj"], "Work out"))
            status["delta_E_kj"] = "DERIVED FROM CLOSED-SYSTEM FIRST LAW"
            status["W_out_kj"] = "GIVEN"
        elif case.get("W_out_kj") is None and resolved.get("delta_E_kj") is not None and q_known:
            resolved["W_out_kj"] = (sum(number(x.get("heat_into_system_kj"), "Heat transfer") for x in case["heat"])
                                    - number(resolved["delta_E_kj"], "Delta E"))
            status["W_out_kj"] = "DERIVED FROM CLOSED-SYSTEM FIRST LAW"
        elif case.get("W_out_kj") is not None:
            status["W_out_kj"] = checked
    for key in REQUIRED.get(model, []):
        if resolved.get(key) is None:
            status[key] = "REQUIRED — MISSING"
    if model == "finite_stream_heat_recovery":
        status["W_out_kw"] = "NOT APPLICABLE — NO SHAFT-WORK INTERACTION IN THIS MODEL"
    if model == "fixed_reservoir_engine" and case.get("Q_cold_rejected_kw") is None and "Q_cold_rejected_kw" not in status:
        status["Q_cold_rejected_kw"] = ("DERIVED FROM FIRST LAW" if resolved.get("Q_cold_rejected_kw") is not None
                                        else "REQUIRED — CANNOT DERIVE WITHOUT Q_H AND W_OUT")
    if model == "finite_stream_engine" and "apply_effective_temperature_limit" not in status:
        status["apply_effective_temperature_limit"] = "NOT APPLICABLE — OPTIONAL COMPARISON DISABLED"
    return resolved, status


def _exchanger_steps(case: Dict[str, Any], c: Dict[str, Any]) -> List[str]:
    """Numbered calculation steps printed for Model 6."""
    hs, cs = case["hot_stream"], case["cold_stream"]
    cp = lambda s: (f"Cp={number(s['cp_kj_kg_k'], 'Cp'):.6g}" if s.get("cp_model", "constant").lower() == "constant"
                    else f"Cp(T)={number(s['a'], 'a'):.6g}+{number(s['b'], 'b'):.6g}T")
    side = lambda name, s: (f"{name} side: mdot={number(s['mass_flow_kg_s'], name.lower() + ' mass flow'):.6g} kg/s; "
                            f"Tin={number(s['T_in_K'], name.lower() + ' Tin'):.6g} K; Tout={number(s['T_out_K'], name.lower() + ' Tout'):.6g} K; {cp(s)} kJ/(kg K).")
    method = ("For constant Cp, each stream uses mdot*Cp*ln(Tout/Tin)."
              if all(s.get("cp_model", "constant").lower() == "constant" for s in (hs, cs))
              else "Each stream entropy change is integrated using its selected Cp(T)/T relation.")
    return [side("Hot", hs), side("Cold", cs),
            f"Hot-side heat release = mdot*Delta h = {c['heat_released_by_hot_stream_kw']:.6g} kW.",
            f"Cold-side heat absorption = mdot*Delta h = {c['heat_absorbed_by_cold_stream_kw']:.6g} kW.",
            f"First-Law residual = Qdot_hot - Qdot_cold = {c['energy_balance_residual_kw']:.6g} kW.",
            f"Entropy generation = sum of the two stream entropy-rate changes. {method} Result = {c['S_gen_dot_kw_per_k']:.8g} kW/K.",
            (f"Exergy destruction = T0*Sdot_gen = {c['exergy_destroyed_kw']:.8g} kW." if c['exergy_destroyed_kw'] is not None
             else "Exergy destruction not evaluated because T0_K was not supplied.")]


def _fmt(value: Optional[float], unit: str = "") -> str:
    if value is None:
        return "—"
    value = 0.0 if abs(value) < 1e-9 else value          # floating-point noise reads as 0
    return f"{value:.5g}{(' ' + unit) if unit else ''}"


def _status(text: Any) -> str:
    """PASS / FAIL / NOT EVALUATED / NOT APPLICABLE from an engine check value."""
    if isinstance(text, dict):
        return "PASS" if text.get("passed") else "FAIL"
    text = str(text or "").upper()
    for word in ("NOT APPLICABLE", "NOT EVALUATED", "FAIL", "PASS"):
        if text.startswith(word):
            return word
    return "NOT EVALUATED" if text.startswith("NOT") else "PASS" if "BELOW" in text else "NOT EVALUATED"


def build_check_table(res: CaseResult) -> List[Dict[str, Any]]:
    """Rows for the website's 'Balance and limit checks' table, built only from engine results:
    check, equation, calculated value, criterion and status (PASS / FAIL / NOT EVALUATED / NOT APPLICABLE)."""
    c, k, model, rows = res.calculations, res.checks, res.model, []
    eqs = MODELS.get(model, {}).get("equations", [])
    find_eq = lambda *words: next((e for e in eqs if all(w in e for w in words)), "")

    def add(check, equation, value, criterion, status):
        rows.append({"check": check, "equation": equation, "value": value, "criterion": criterion, "status": status})

    if isinstance(k.get("mass_balance"), dict):
        m = k["mass_balance"]
        add("Mass balance", m["equation"], f"Residual = {_fmt(m['residual'], m['units'])}", "Residual ≈ 0", _status(m))
    first = k.get("first_law")
    if isinstance(first, dict):
        add("Energy balance (First Law)", first["equation"], f"Residual = {_fmt(first['residual'], first['units'])}",
            "Residual ≈ 0", _status(first))
    elif first is not None or model in ("property_change", "unsupported_domain"):
        add("Energy balance (First Law)", find_eq("First Law") or "—", "—", "Residual ≈ 0",
            "NOT APPLICABLE" if model in ("property_change", "unsupported_domain") else _status(first))
    sgen_key = next((key for key in c if key.startswith("S_gen")), None)
    sgen = c.get(sgen_key) if sgen_key else None
    unit = "kJ/K" if sgen_key and "kj_per_k" in sgen_key else "kW/K"
    if model in ("mechanical_power", "solar_blackbody", "reaction_gibbs") and sgen is None:
        add("Entropy generation (Second Law)", "—", "—", "S_gen ≥ 0", "NOT APPLICABLE")
    elif model not in ("property_change", "unsupported_domain"):
        add("Entropy generation (Second Law)", find_eq("gen") or "S_gen ≥ 0", f"S_gen = {_fmt(sgen, unit)}",
            "S_gen ≥ 0 (= 0 only for a reversible process)",
            ("FAIL" if entropy_generation_is_negative(sgen) else "PASS") if sgen is not None else "NOT EVALUATED")
    if model == "fixed_reservoir_engine":
        add("Carnot limit", "eta <= eta_Carnot = 1 - T_C/T_H",
            f"eta = {_fmt(100 * c['actual_efficiency_fraction'], '%')} vs eta_Carnot = {_fmt(100 * c['carnot_efficiency_fraction'], '%')}",
            "Claimed work ≤ Q_H·eta_Carnot", _status(k.get("performance_limit")))
    elif model == "finite_stream_engine":
        limit, w = c.get("strict_reversible_work_limit_kw"), c.get("work_used_for_assessment_kw")
        add("Finite-stream reversible work limit", "W_max = Q_hot - Q_cold,rev (cold outlet where S_gen = 0)",
            f"W = {_fmt(w, 'kW')} vs W_max = {_fmt(limit, 'kW')}", "W ≤ W_max",
            "NOT EVALUATED" if limit is None or w is None else ("PASS" if within_limit(w, limit) else "FAIL"))
    elif model == "refrigeration_cycle":
        mode = res.inputs.get("mode", "refrigerator")
        cop, cmax = ((c.get("COP_refrigerator"), c.get("carnot_COP_refrigerator")) if mode == "refrigerator"
                     else (c.get("COP_heat_pump"), c.get("carnot_COP_heat_pump")))
        add("Carnot COP limit", "COP_R,max = T_C/(T_H - T_C)" if mode == "refrigerator" else "COP_HP,max = T_H/(T_H - T_C)",
            f"COP = {_fmt(cop)} vs COP_max = {_fmt(cmax)}", "COP ≤ Carnot COP",
            "NOT EVALUATED" if cmax is None or cop is None else _status(k.get("performance_limit")))
    elif model in ("mechanical_power", "solar_blackbody", "reaction_gibbs"):
        name = {"mechanical_power": "Mechanical input limit", "solar_blackbody": "Petela–Landsberg radiation limit",
                "reaction_gibbs": "Gibbs energy limit"}[model]
        eq = {"mechanical_power": c.get("mechanical_input_basis") or "Wdot_out <= recoverable mechanical input",
              "solar_blackbody": "Wdot_out <= eta_L * G * A", "reaction_gibbs": "Wdot_out <= -Delta G * reaction rate"}[model]
        add(name, eq, f"Output = {_fmt(c.get('claimed_electrical_output_kw'), 'kW')} vs limit = "
            f"{_fmt(c.get('reversible_power_limit_kw'), 'kW')}", "Output ≤ limit", _status(k.get("performance_limit")))
    elif model in ("steady_control_volume", "closed_system"):
        add("Reversible work limit", "—", "—", "The balances themselves decide feasibility", "NOT APPLICABLE")
    elif model == "finite_stream_heat_recovery":
        add("Shaft work", "W_shaft = 0 for a heat exchanger", "W = 0 kW", "No work produced or consumed", "NOT APPLICABLE")
    if "terminal_temperature_check" in k:
        add("Temperature cross", "Cold outlet ≤ hot inlet; cold inlet < hot inlet",
            str(k["terminal_temperature_check"]).replace("FAIL: ", ""), "Heat flows from hot to cold only",
            _status(k["terminal_temperature_check"]))
    if c.get("exergy_destroyed_kw") is not None:
        add("Exergy destroyed", "Edot_destroyed = T0 * S_gen", _fmt(c["exergy_destroyed_kw"], "kW"), "≥ 0", "PASS")
    return rows


def analyse_case(case: Dict[str, Any]) -> CaseResult:
    """Validate the model name, tag the inputs, run the model and attach its equations."""
    if not isinstance(case, dict):
        raise ThermoInputError("A case must be supplied as a JSON object/dictionary.")
    check_required(case, ["model"], "case")
    model = str(case["model"]).lower()
    if model not in ANALYSERS:
        raise ThermoInputError(f"Unknown model '{model}'. Supported model keys: {', '.join(ANALYSERS)}.")
    resolved, status = _resolve_case_inputs(case)
    res = ANALYSERS[model](resolved)
    res.input_status = status
    res.checks["model_selection_guidance"] = MODELS[model]["guidance"]
    res.checks["model_number"] = model_label(model)
    res.calculations["governing_equations"] = MODELS[model]["equations"]
    res.check_table = build_check_table(res)
    if model == "finite_stream_heat_recovery" and res.calculations.get("S_gen_dot_kw_per_k") is not None:
        res.calculations["calculation_steps"] = _exchanger_steps(res.inputs, res.calculations)
    return res


run_case = analyse_case


# ============================================================================
# 8. Terminal program: interactive input, report, JSON files
# ============================================================================
def ask_text(prompt: str, default: Optional[str] = None) -> str:
    value = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
    return value if value else (default if default is not None else "")


def _ask_number(prompt, default, cast, error):
    while True:
        raw = input(f"{prompt}{f' [{default}]' if default is not None else ''}: ").strip()
        if not raw and default is not None:
            return cast(default)
        try:
            return cast(raw)
        except ValueError:
            print(error)


def ask_float(prompt: str, default: Optional[float] = None) -> float:
    return _ask_number(prompt, default, float, "Please enter a numeric value.")


def ask_int(prompt: str, default: Optional[int] = None) -> int:
    return _ask_number(prompt, default, int, "Please enter a whole number.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    raw = input(f"{prompt} ({'Y/n' if default else 'y/N'}): ").strip().lower()
    return default if not raw else raw in {"y", "yes", "true", "1"}


def ask_count(prompt: str, default: int, what: str) -> int:
    count = ask_int(prompt, default)
    if count < 0:
        raise ThermoInputError(f"The number of {what} cannot be negative.")
    return count


def ask_fields(spec) -> Dict[str, Any]:
    """spec = [(key, prompt, default)]; text if the default is a string, otherwise a number."""
    return {key: ask_text(p, d) if isinstance(d, str) else ask_float(p, d) for key, p, d in spec}


def ask_cp(target: Dict[str, Any]) -> Dict[str, Any]:
    cp_model = target["cp_model"] = ask_text("Cp model: constant or linear", "constant").lower()
    if cp_model == "constant":
        target["cp_kj_kg_k"] = ask_float("Constant Cp (kJ/kg K)")
    elif cp_model == "linear":
        target.update(a=ask_float("Cp coefficient a"), b=ask_float("Cp coefficient b"))
    return target


def ask_stream(label: str, default_name: str, cp_choice: bool = True) -> Dict[str, Any]:
    print(f"\n{label} stream")
    stream = ask_fields([("name", "Stream name", default_name), ("mass_flow_kg_s", "Mass flow rate (kg/s)", None),
                         ("T_in_K", "Inlet temperature (K)", None), ("T_out_K", "Outlet temperature (K)", None)])
    if not cp_choice:
        return dict(stream, cp_model="constant", cp_kj_kg_k=ask_float("Constant Cp (kJ/kg K)"))
    stream = ask_cp(stream)
    if stream["cp_model"] not in ("constant", "linear"):
        print("Unknown Cp model; validation will report the issue.")
    return stream


def ask_heat(count_default: int, key: str, unit: str, into: str) -> List[Dict[str, float]]:
    heat = []
    for i in range(ask_count("Number of heat interactions", count_default, "heat interactions")):
        print(f"Heat interaction {i+1} (positive into {into})")
        heat.append({key: ask_float(f"Heat {'rate' if unit == 'kW' else 'transfer'} ({unit})"),
                     "boundary_temperature_K": ask_float("Boundary temperature (K)")})
    return heat


MENU = ["Fixed-temperature reservoir cyclic heat engine", "Finite hot stream + finite cold stream (work produced)",
        "General steady control volume (turbine, compressor, pump, nozzle)", "Closed system (energy + entropy balances)",
        "Refrigerator / heat pump (work consumed, Carnot COP)", "Heat exchanger (no shaft work)",
        "Mechanical energy harvesting (walking, pedalling, wind, hydro)", "Solar radiation conversion (ideal blackbody)",
        "Electrochemical conversion (fuel cell, Gibbs energy)",
        "Property calculator only (Delta h, Delta s, T_eff; no verdict)", "Record an unsupported domain (MODEL REQUIRED)"]
# menu number -> branch below (the branches keep their historical letters so saved scripts are unaffected)
MENU_BRANCH = {"1": "1", "2": "2", "3": "3", "4": "4", "5": "R", "6": "7", "7": "8", "8": "9", "9": "10",
               "10": "5", "11": "6"}


def interactive_case() -> Dict[str, Any]:
    """Ask for the model number, then only the inputs that model needs."""
    print("\nTHERMODYNAMIC FEASIBILITY TESTER — INPUT MODE")
    print("Enter data in SI/thermodynamic units shown. Temperatures must be in K.")
    print("This M3 version needs you to extract the data from the written problem first.")
    print("\nChoose the model:")
    for i, name in enumerate(MENU, 1):
        if i == 10:
            print("  -- utilities --")
        print(f"{i:3d}. {name}")
    choice = MENU_BRANCH.get(ask_text("Model number").strip(), "?")
    case: Dict[str, Any] = {"title": ask_text("Short case title", "User-supplied case"),
                            "description": ask_text("Brief process description", "")}
    if choice == "1":
        case.update(model="fixed_reservoir_engine", **ask_fields([
            ("T_hot_K", "Hot reservoir temperature (K)", None), ("T_cold_K", "Cold reservoir temperature (K)", None),
            ("Q_hot_kw", "Heat supplied from hot reservoir (kW)", None), ("W_out_kw", "Claimed work output (kW)", None)]))
        if ask_yes_no("Do you have a separately specified rejected-heat rate?", False):
            case["Q_cold_rejected_kw"] = ask_float("Rejected heat rate (kW)")
    elif choice == "2":
        case.update(model="finite_stream_engine", hot_stream=ask_stream("Hot/source", "Hot source"),
                    cold_stream=ask_stream("Cold/sink", "Cold sink"))
        if ask_yes_no("Do you have a claimed work output to test?", True):
            case["W_out_kw"] = ask_float("Claimed work output (kW)")
        case["apply_effective_temperature_limit"] = ask_yes_no(
            "Use the effective-temperature ratio as a model-specific reversible reference? (not universal)", False)
    elif choice == "R":
        case.update(model="refrigeration_cycle", mode=ask_text("Device: refrigerator or heat_pump", "refrigerator"),
                    energy_unit=ask_text("Energy unit: kW (rates) or kJ (amounts)", "kW"),
                    **ask_fields([("T_cold_K", "Cold-side temperature (K)", None), ("T_hot_K", "Hot-side temperature (K)", None)]))
        print("Enter the energy quantities you know (leave one blank to derive it from the First Law).")
        for key, label in REFRIGERATION_ENERGY.items():
            raw = ask_text(label.capitalize(), "")
            if raw:
                case[key] = float(raw)
    elif choice == "3":
        print("\nFor each stream, provide mass flow, specific h and specific s.")
        case["model"] = "steady_control_volume"
        for label in ("inlet", "outlet"):
            streams = case[label + "s"] = []
            for i in range(ask_count(f"Number of {label} streams", 1, "streams")):
                print(f"{label.title()} stream {i+1}")
                item = ask_fields([("mass_flow_kg_s", "Mass flow (kg/s)", None),
                                   ("specific_enthalpy_kj_kg", "Specific enthalpy (kJ/kg)", None),
                                   ("specific_entropy_kj_kg_k", "Specific entropy (kJ/kg K)", None)])
                if ask_yes_no("Include velocity/elevation for this stream?", False):
                    item.update(ask_fields([("velocity_m_s", "Velocity (m/s)", None), ("elevation_m", "Elevation (m)", None)]))
                streams.append(item)
        case.update(heat=ask_heat(0, "heat_into_cv_kw", "kW", "control volume"), W_out_kw=ask_float("Shaft work output (kW)"),
                    include_kinetic_potential=ask_yes_no("Include kinetic/potential energy terms?", False))
    elif choice == "4":
        case.update(model="closed_system", **ask_fields([("delta_E_kj", "System total energy change Delta E (kJ)", None),
                                                         ("delta_S_system_kj_K", "System entropy change Delta S (kJ/K)", None)]))
        case.update(heat=ask_heat(1, "heat_into_system_kj", "kJ", "system"), W_out_kj=ask_float("Work output (kJ)"))
    elif choice == "5":
        case.update(model="property_change", **ask_fields([("T1_K", "Initial temperature T1 (K)", None),
                                                           ("T2_K", "Final temperature T2 (K)", None)]))
        ask_cp(case)
        if ask_yes_no("Include phase-change Delta h and Delta s?", False):
            case.update(ask_fields([("phase_delta_h_kj_kg", "Phase-change Delta h (kJ/kg)", None),
                                    ("phase_delta_s_kj_kg_k", "Phase-change Delta s (kJ/kg K)", None)]))
    elif choice == "6":
        case.update(model="unsupported_domain", **ask_fields([
            ("domain", "Domain (e.g. chemical, radiative, mechanical)", "unspecified"),
            ("reason", "What required model/data is missing?", "A domain-specific reversible/exergy model has not been supplied.")]))
    elif choice == "7":
        case.update(model="finite_stream_heat_recovery", hot_stream=ask_stream("Hot/source", "Server coolant", False),
                    cold_stream=ask_stream("Cold/process", "Process water", False),
                    T0_K=ask_float("Reference environment temperature T0 (K)"))
    elif choice == "8":
        resource = (ask_fields([("recoverable_mechanical_power_kw", "Recoverable mechanical power (kW)", None)])
                    if ask_yes_no("Is recoverable mechanical power already known?", True) else
                    ask_fields([("recoverable_energy_per_event_j", "Recoverable energy per event, e.g. per step (J)", None),
                                ("events_per_s", "Events per second", None)]))
        case.update(model="mechanical_power", W_out_kw=ask_float("Claimed electrical output (kW)"), **resource)
    elif choice == "9":
        case.update(model="solar_blackbody", **ask_fields([
            ("T_source_K", "Blackbody source temperature (K)", 5778.0), ("T_ambient_K", "Ambient temperature (K)", 300.0),
            ("irradiance_W_m2", "Incident irradiance (W/m^2)", 1000.0),
            ("collection_area_m2", "Effective collection area (m^2)", None), ("W_out_kw", "Claimed electrical output (kW)", None)]))
    elif choice == "10":
        case.update(model="reaction_gibbs", **ask_fields([
            ("reaction_direction", "Reaction direction used for Delta G", "H2 + 1/2 O2 -> H2O(l)"),
            ("delta_G_kj_per_mol_rxn", "Signed Delta G at operating conditions (kJ/mol reaction)", -237.1),
            ("reaction_rate_mol_rxn_s", "Forward reaction rate (mol reaction/s)", None),
            ("W_out_kw", "Claimed electrical output (kW)", None)]))
    else:
        raise ThermoInputError("Choose a model number from 1 to 11.")
    return case


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.8g}"
    return json.dumps(value, indent=2, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)


def print_report(result: CaseResult) -> None:
    line = "=" * 78
    print(f"\n{line}\nTHERMODYNAMIC FEASIBILITY REPORT\n{line}")
    print(f"Case       : {result.title}\nModel      : {result.model}")
    if result.description:
        print(f"Description: {result.description}")
    print(f"\nQUICK VERDICT: {result.verdict.status}")
    if "process_classification" in result.checks:
        print(f"PROCESS CLASSIFICATION: {result.checks['process_classification']}")
    sections = [("INPUTS", result.inputs, None),
                ("INPUT DATA CLASSIFICATION", result.input_status, "No separate input provenance recorded."),
                ("CALCULATIONS / GOVERNING EQUATIONS", result.calculations, "No calculations available for this model."),
                ("CHECKS", result.checks, None)]
    for heading, items, empty in sections:
        print(f"\n{heading}")
        for key, value in items.items():
            print(f"  {key}: {value if heading == 'INPUT DATA CLASSIFICATION' else _format_value(value)}")
        if not items and empty:
            print(f"  {empty}")
    print("\nASSUMPTIONS / MODEL BOUNDARY")
    for item in result.assumptions:
        print(f"  - {item}")
    print(f"\nVERDICT\n  {result.verdict.status}")
    for reason in result.verdict.reasons:
        print(f"  - {reason}")
    print(f"  Note: {result.verdict.scope_note}\n{line}")


def load_case(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise ThermoInputError(f"Input JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ThermoInputError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ThermoInputError("The JSON root must be an object describing one case.")
    return data


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Thermodynamic Feasibility Tester: structured input, model selection, balances and verdicts.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--input", metavar="FILE.json", help="Analyse a structured JSON case file.")
    group.add_argument("--demo", action="store_true", help="Run built-in validation demonstrations.")
    group.add_argument("--prof-demo", action="store_true", help="Run the curated data-centre waste-heat recovery demonstration.")
    group.add_argument("--self-test", action="store_true", help="Run internal formula/logic tests only.")
    group.add_argument("--make-templates", action="store_true", help="Write editable JSON input templates.")
    args = parser.parse_args(argv)
    try:
        if args.self_test or args.demo or args.prof_demo or args.make_templates:
            import feasibility_examples as examples      # demo cases, templates and self-tests
            if args.self_test:
                tests = examples.self_test()
                print(f"SELF-TEST PASSED: {len(tests)} checks")
                for item in tests:
                    print(f"  PASS — {item}")
            elif args.demo:
                examples.run_demonstrations()
            elif args.prof_demo:
                print_report(run_case(examples.demonstration_cases()[0]))
            else:
                print(f"Editable input templates written to: {examples.write_templates().resolve()}")
            return 0
        print_report(run_case(load_case(args.input) if args.input else interactive_case()))
        return 0
    except (ThermoInputError, OSError) as error:
        print(f"\nINPUT / MODEL ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
