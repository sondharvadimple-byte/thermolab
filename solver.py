"""Shared website interface to the original Python models."""

import json
import re
import inspect
import thermodynamic_feasibility_tester_MASTER as engine
import auto_analyzer
from website.local_scenarios import extract_case

from thermodynamic_feasibility_tester_MASTER import run_case


def calculation_code(route):
    names = {
        "mechanical_power": ["analyse_mechanical_power", "_mechanical_input", "wind_power_kw", "hydraulic_power_kw",
                             "resource_result", "make_verdict"],
        "fixed_reservoir_engine": ["analyse_fixed_reservoir_engine", "carnot_efficiency",
                                   "fixed_reservoir_entropy_generation", "make_verdict"],
        "finite_stream_engine": ["analyse_finite_stream_engine", "_finite_stream_open_outlet", "stream_properties",
                                 "reversible_cold_outlet_temperature",
                                 "properties_constant_cp", "properties_linear_cp", "effective_temperature", "make_verdict"],
        "steady_control_volume": ["analyse_steady_control_volume", "steady_cv_mass_balance", "steady_cv_energy_balance",
                                  "steady_cv_entropy_generation", "_balance_verdict", "make_verdict"],
        "closed_system": ["analyse_closed_system", "closed_system_energy_balance", "closed_system_entropy_generation",
                          "_balance_verdict", "make_verdict"],
        "property_change": ["analyse_property_change", "properties_constant_cp", "properties_linear_cp",
                            "effective_temperature", "phase_change_temperature"],
        "unsupported_domain": ["analyse_unsupported_domain"],
        "finite_stream_heat_recovery": ["analyse_finite_stream_heat_recovery", "_exchanger_full", "stream_properties",
                                        "outlet_temperature_for_heat", "properties_constant_cp", "properties_linear_cp"],
        "refrigeration_cycle": ["analyse_refrigeration_cycle", "make_verdict"],
        "solar_blackbody": ["analyse_solar_blackbody", "resource_result", "make_verdict"],
        "reaction_gibbs": ["analyse_reaction_gibbs", "resource_result", "make_verdict"],
    }
    code = [{"name": name, "source": inspect.getsource(getattr(engine, name))}
            for name in names.get(route, [])]
    return code


def analysis_code(analysis):
    """Source of the analyzer steps that produced this mapping (shown on the website)."""
    if not analysis:
        return []
    title, domain, model = analysis.get("title") or "", analysis.get("domain"), analysis.get("model")
    names = ["scan_quantities", "ScenarioAnalyzer.assign_energy_roles", "ScenarioAnalyzer.assign_temperature_roles"]
    if domain == "thermal":
        names.append("ScenarioAnalyzer.route_thermal")
        if model == "steady_control_volume":
            names.append("ScenarioAnalyzer.try_steady_cv")
        elif model == "finite_stream_heat_recovery" and "duties" in title:
            names.append("ScenarioAnalyzer.try_exchanger_duties")
        elif model == "closed_system" and title.startswith("Closed system"):
            names.append("ScenarioAnalyzer.try_closed_system")
        elif model == "property_change":
            names.append("ScenarioAnalyzer.try_property_request")
        elif model == "finite_stream_engine":
            names.append("ScenarioAnalyzer.thermal_two_streams")
        elif model == "finite_stream_heat_recovery":
            names += ["ScenarioAnalyzer.thermal_two_streams", "ScenarioAnalyzer._exchanger_case"]
        elif "stream" in title.lower():
            names.append("ScenarioAnalyzer.thermal_stream_reservoir")
        elif "single reservoir" in title.lower():
            names.append("ScenarioAnalyzer._kelvin_planck")
        elif model == "closed_system":
            names.append("ScenarioAnalyzer._cycle_entropy")
        else:
            names.append("ScenarioAnalyzer.thermal_reservoirs")
    else:
        route = {"mechanical": "route_mechanical", "wind": "route_wind", "hydro": "route_hydro",
                 "solar": "route_solar", "chemical": "route_chemical", "no_input": "route_no_input",
                 "work_consuming": "route_work_consuming", "unsupported": "route_unsupported"}.get(domain)
        if route:
            names.append("ScenarioAnalyzer." + route)
    code = []
    for name in names:
        target = auto_analyzer
        for part in name.split("."):
            target = getattr(target, part)
        code.append({"name": "auto_analyzer." + name, "source": inspect.getsource(target)})
    return code


# Mapping layer: website technologies and routes -> engine model numbers (same numbers as the terminal).
ROUTE_MODEL_LABELS = {}


def model_label(route):
    if not route:
        return None
    return ROUTE_MODEL_LABELS.get(route) or engine.model_label(route)


def solve(request):
    response = _solve(request)
    if isinstance(response, dict):
        response["model_label"] = model_label(response.get("route"))
        if response.get("analysis"):
            response["analysis"]["model_label"] = model_label(response["analysis"].get("model") or response.get("route"))
        response["models"] = engine.SELECTABLE_MODELS
    return response


def _solve(request):
    if not isinstance(request, dict):
        raise ValueError("Supply a scenario or a model case.")
    if "case" in request:
        case = request["case"]
        result = run_case(case)
        return {"status": "evaluated", "route": result.model, "questions": [],
                "source_quotes": {}, "cases": {result.model: case}, "result": result.as_dict(),
                "calculation_code": calculation_code(result.model)}

    scenario = request.get("scenario")
    if not isinstance(scenario, str) or not 0 < len(scenario.strip()) <= 3000:
        raise ValueError("Enter between 1 and 3,000 characters.")
    route = None

    # 1. Automatic analysis: extract, map source/sink, choose the model and run the engine.
    auto = None
    if route is None:
        auto = auto_analyzer.analyze_scenario(scenario)
        if auto["status"] == "evaluated":
            auto["calculation_code"] = analysis_code(auto["analysis"]) + calculation_code(auto["route"])
            return auto

    # 2. Earlier labelled-phrase extractor (fallback when the automatic analysis needs more data).
    if route is None:
        for candidate, pattern in (
            ("finite_stream_engine", r"\b(?:hot (?:thermal )?oil|hot water|hot stream|cold water|cooling water|cold tap water)\b"),
            ("solar_blackbody", r"\b(?:solar|sunlight|radiation)\b"),
            ("reaction_gibbs", r"\b(?:fuel cell|chemical|gibbs|reaction|battery)\b"),
            ("fixed_reservoir_engine", r"\b(?:heat engine|reservoir|carnot)\b"),
            ("mechanical_power", r"\b(?:walking|footsteps?|mechanical|pedal|pedaling)\b"),
        ):
            if re.search(pattern, scenario, re.IGNORECASE):
                route = candidate
                break
    case, quotes, values, issues = (extract_case(scenario, route) if route is not None else (None, {}, {}, []))
    if case is not None:
        response = solve({"case": case})
        response["source_quotes"] = quotes
        if auto is not None:
            response["analysis"] = auto["analysis"]
        return response
    if auto is not None:
        if auto["status"] == "error":
            return {"error": auto["error"], "analysis": auto["analysis"], "status": "error", "route": auto["route"]}
        route = auto["route"] or route
    return {"status": "needs_values", "route": route, "cases": {}, "result": None,
            "source_quotes": quotes or (auto or {}).get("source_quotes", {}), "prefill": values,
            "issues": issues + ((auto or {}).get("issues") or []),
            "analysis": (auto or {}).get("analysis")}


def solve_json(payload):
    try:
        return json.dumps(solve(json.loads(payload)), allow_nan=False)
    except (ValueError, OverflowError) as error:
        return json.dumps({"error": str(error)})
