#!/usr/bin/env python3
"""AUTOMATIC SCENARIO ANALYZER — plain-English problem to feasibility verdict.

Pipeline
--------
1. Scan    : find every number with a unit and convert it to engine units
             (K, kW, kJ, kg/s, kJ/(kg K), ...). The original phrase is kept.
2. Roles   : read the words around each quantity and decide what it means
             (heat input, rejected heat, claimed work, hot/cold temperature,
             stream inlet/outlet, wind speed, head, ...).
3. Map     : identify the energy SOURCE and SINK. Hot/cold streams are
             grouped, reservoirs are separated from streams, and the domain
             (thermal, mechanical, wind, hydro, solar, chemical) is chosen.
4. Infer   : fill at most one gap from the First Law (for example the cold
             stream outlet) and use clearly labelled textbook defaults
             (Cp of water, air density, sun temperature, ambient 298.15 K).
5. Solve   : build the standard engine case and run the ORIGINAL engine
             (thermodynamic_feasibility_tester_MASTER.run_case).

Every extracted value, inferred value and assumption is reported, so the
mapping can be checked and defended. The analyzer never invents a claim:
if the claimed output or a required quantity is missing or ambiguous, it
returns "needs_values" with a specific list of what to add.

Pure standard-library Python (runs locally and in the browser via Pyodide).

Usage
-----
    python auto_analyzer.py "An engine receives 100 kW from a source at 600 K, ..."
    python auto_analyzer.py --json "..."
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import thermodynamic_feasibility_tester_MASTER as thermo

# ============================================================================
# 0. Constants and labelled defaults
# ============================================================================

T_AMBIENT_DEFAULT_K = 298.15          # standard dead state for thermal sinks
SOLAR_T_SUN_K = 5778.0                # effective blackbody temperature of the sun
SOLAR_T_AMBIENT_K = 300.0             # flowchart value for the solar route
SOLAR_IRRADIANCE_W_M2 = 1000.0        # standard test-condition irradiance
AIR_DENSITY_KG_M3 = 1.225             # sea-level standard air
WATER_DENSITY_KG_M3 = 1000.0
GRAVITY_M_S2 = 9.81
BETZ = 16.0 / 27.0

# Typical constant heat capacities, kJ/(kg K). Used only when the text names
# the substance but gives no Cp; always reported as an assumption.
CP_DEFAULTS = {
    "water": (4.18, "liquid water"),
    "seawater": (3.99, "seawater"),
    "air": (1.005, "air"),
    "exhaust gas": (1.10, "combustion/exhaust gas (typical average)"),
    "nitrogen": (1.04, "nitrogen"),
    "helium": (5.193, "helium"),
    "carbon dioxide": (0.846, "carbon dioxide"),
}

# Standard Gibbs energies at 298.15 K, 1 bar, per mole of fuel, liquid water
# product (from standard Gibbs energies of formation). Used only when the
# text names the fuel but gives no Delta G; always reported as an assumption.
FUEL_GIBBS = {
    "hydrogen": (-237.1, 2.016, "H2 + 1/2 O2 -> H2O(l)", r"\b(?:hydrogen|H2)\b"),
    "methane": (-818.0, 16.04, "CH4 + 2 O2 -> CO2 + 2 H2O(l)", r"\b(?:methane|CH4|natural gas)\b"),
    "methanol": (-702.4, 32.04, "CH3OH(l) + 3/2 O2 -> CO2 + 2 H2O(l)", r"\b(?:methanol|CH3OH)\b"),
    "ethanol": (-1325.4, 46.07, "C2H5OH(l) + 3 O2 -> 2 CO2 + 3 H2O(l)", r"\b(?:ethanol|C2H5OH)\b"),
}

# ============================================================================
# 1. Quantity scanner
# ============================================================================

NUM = (r"(?<![\w.])(?P<sign>[-−–])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d*)?|\.\d+)"
       r"(?P<exp>\s*(?:[eE][-+−]?\d+|[×x*]\s*10\s*\^?\s*\(?[-+−]?\d+\)?))?")
NUM_RE = re.compile(NUM)
NUM_PLAIN = r"[-−]?(?:\d+(?:\.\d*)?|\.\d+)(?:\s*(?:[eE][-+−]?\d+|[×x*]\s*10\s*\^?\s*\(?[-+−]?\d+\)?))?"
SUP2 = r"(?:2|²|\^2)"
SUP3 = r"(?:3|³|\^3)"
KGK = r"\(?\s*kg\s*(?:[·.\-*/ ]|\s)?\s*(?:K|°\s*C|ºC|C)(?![A-Za-z])\s*\)?"
PER = r"\s*(?:/|per|each|a)\s*"


def _c_to_k(value: float) -> float:
    return value + 273.15


def _f_to_k(value: float) -> float:
    return (value - 32.0) * 5.0 / 9.0 + 273.15


# (regex, kind, conversion factor or function, case-sensitive?, unit label)
# Ordered so that longer / compound units are tried first.
UNIT_SPECS: List[Tuple[str, str, Any, bool, str]] = [
    # specific heat capacity / specific entropy  kJ/(kg K)
    (rf"kJ\s*/\s*{KGK}", "cp", 1.0, False, "kJ/(kg K)"),
    (rf"kJ\s*/\s*kg\s*/\s*(?:K|°C)", "cp", 1.0, False, "kJ/(kg K)"),
    (rf"kJ\s+per\s+kg\s+(?:per\s+)?(?:K|kelvin|°C|degree)", "cp", 1.0, False, "kJ/(kg K)"),
    (rf"J\s*/\s*{KGK}", "cp", 0.001, False, "kJ/(kg K)"),
    (rf"J\s*/\s*kg\s*/\s*(?:K|°C)", "cp", 0.001, False, "kJ/(kg K)"),
    (r"kJ\s*/\s*K\b", "entropy", 1.0, True, "kJ/K"),
    (r"kW\s*/\s*K\b", "entropy_rate", 1.0, True, "kW/K"),
    # molar
    (r"kJ\s*/\s*mol(?:e)?(?:\s+(?:of\s+)?reaction)?\b", "molar_energy", 1.0, False, "kJ/mol"),
    (r"kJ\s+per\s+mol(?:e)?\b", "molar_energy", 1.0, False, "kJ/mol"),
    (r"J\s*/\s*mol(?:e)?\b", "molar_energy", 0.001, False, "kJ/mol"),
    (r"mmol\s*/\s*s\b", "molar_rate", 0.001, False, "mol/s"),
    (r"mol(?:e?s)?\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "molar_rate", 1.0, False, "mol/s"),
    (r"mol(?:e?s)?\s*(?:/|per)\s*min(?:ute)?\b", "molar_rate", 1 / 60, False, "mol/s"),
    (r"mol(?:e?s)?\s*(?:/|per)\s*h(?:r|our)?\b", "molar_rate", 1 / 3600, False, "mol/s"),
    # specific energy kJ/kg (after cp so that kJ/kg K is not split)
    (r"MJ\s*/\s*kg\b", "specific_energy", 1000.0, False, "kJ/kg"),
    (r"kJ\s*/\s*kg\b", "specific_energy", 1.0, False, "kJ/kg"),
    (r"kJ\s+per\s+kg\b", "specific_energy", 1.0, False, "kJ/kg"),
    (r"J\s*/\s*kg\b", "specific_energy", 0.001, False, "kJ/kg"),
    # irradiance
    (rf"kW\s*/\s*m{SUP2}", "irradiance", 1000.0, False, "W/m²"),
    (rf"W\s*/\s*m{SUP2}", "irradiance", 1.0, False, "W/m²"),
    (r"W\s+per\s+square\s+met(?:er|re)", "irradiance", 1.0, False, "W/m²"),
    # density
    (rf"kg\s*/\s*m{SUP3}", "density", 1.0, False, "kg/m³"),
    # mass flow
    (r"kg\s*(?:/|per)\s*s(?:ec(?:ond)?s?)?\b", "mass_flow", 1.0, False, "kg/s"),
    (r"kg\s*(?:/|per)\s*min(?:ute)?\b", "mass_flow", 1 / 60, False, "kg/s"),
    (r"kg\s*(?:/|per)\s*h(?:r|our)?\b", "mass_flow", 1 / 3600, False, "kg/s"),
    (r"g\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "mass_flow", 0.001, False, "kg/s"),
    (r"g\s*(?:/|per)\s*min(?:ute)?\b", "mass_flow", 0.001 / 60, False, "kg/s"),
    (r"(?:t|tonnes?|tons?)\s*(?:/|per)\s*h(?:r|our)?\b", "mass_flow", 1000 / 3600, False, "kg/s"),
    # volumetric flow
    (rf"m{SUP3}\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "vol_flow", 1.0, False, "m³/s"),
    (rf"m{SUP3}\s*(?:/|per)\s*min(?:ute)?\b", "vol_flow", 1 / 60, False, "m³/s"),
    (rf"m{SUP3}\s*(?:/|per)\s*h(?:r|our)?\b", "vol_flow", 1 / 3600, False, "m³/s"),
    (r"(?:L|l|litres?|liters?)\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "vol_flow", 0.001, False, "m³/s"),
    (r"(?:L|l|litres?|liters?)\s*(?:/|per)\s*min(?:ute)?\b", "vol_flow", 0.001 / 60, False, "m³/s"),
    (r"(?:L|l|litres?|liters?)\s*(?:/|per)\s*h(?:r|our)?\b", "vol_flow", 0.001 / 3600, False, "m³/s"),
    # rotation and force (mechanical harvesting): torque before force, rad/s and rpm before velocity
    (r"N\s*[·.\-*]?\s*m\b|Nm\b|newton[- ]met(?:er|re)s?\b", "torque", 1.0, False, "N·m"),
    (r"rad\s*/\s*s\b|rad(?:ians)?\s+per\s+second\b", "angular_velocity", 1.0, False, "rad/s"),
    (r"rpm\b|rev(?:olution)?s?\s*(?:/|per)\s*min(?:ute)?\b", "angular_velocity", 2 * math.pi / 60, False, "rad/s"),
    (r"kN(?![A-Za-z0-9])", "force", 1000.0, True, "N"),
    (r"N(?![A-Za-z0-9·.])|newtons?\b", "force", 1.0, True, "N"),
    (r"m\s*(?:/|per)\s*(?:step|stride|footfall|press|event|stroke)\b", "length", 1.0, False, "m"),
    (r"cm\s*(?:/|per)\s*(?:step|stride|footfall|press|event|stroke)\b", "length", 0.01, False, "m"),
    # velocity
    (r"m\s*/\s*s\b", "velocity", 1.0, True, "m/s"),
    (r"met(?:er|re)s?\s+per\s+second\b", "velocity", 1.0, False, "m/s"),
    (r"km\s*/\s*h(?:r)?\b|kmph\b|km\s+per\s+hour\b", "velocity", 1 / 3.6, False, "m/s"),
    # per-step energy and step rate (mechanical harvesting)
    (rf"(?:kJ|J|joules?)(?:\s+of\s+energy)?{PER}(?:foot)?(?:step|stride|footfall|press|stomp|event)\b",
     "event_energy", None, False, "J/step"),
    (rf"(?:foot)?(?:steps?|strides?|footfalls?|presses|events?){PER}min(?:ute)?\b", "event_rate", 1 / 60, False, "1/s"),
    (rf"(?:foot)?(?:steps?|strides?|footfalls?|presses|events?){PER}s(?:ec(?:ond)?)?\b", "event_rate", 1.0, False, "1/s"),
    (rf"(?:foot)?(?:steps?|strides?|footfalls?|presses|events?){PER}h(?:our)?\b", "event_rate", 1 / 3600, False, "1/s"),
    # power, including energy-per-time forms
    (r"kJ\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "power", 1.0, False, "kW"),
    (r"kJ\s*(?:/|per)\s*min(?:ute)?\b", "power", 1 / 60, False, "kW"),
    (r"kJ\s*(?:/|per)\s*h(?:r|our)?\b", "power", 1 / 3600, False, "kW"),
    (r"MJ\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "power", 1000.0, False, "kW"),
    (r"MJ\s*(?:/|per)\s*h(?:r|our)?\b", "power", 1000 / 3600, False, "kW"),
    (r"MJ\s*(?:/|per)\s*min(?:ute)?\b", "power", 1000 / 60, False, "kW"),
    (r"J\s*(?:/|per)\s*s(?:ec(?:ond)?)?\b", "power", 0.001, False, "kW"),
    (r"(?:Btu|BTU)\s*(?:/|per)\s*h(?:r|our)?\b", "power", 0.000293071, False, "kW"),
    (r"GW(?![A-Za-z])", "power", 1.0e6, True, "kW"),
    (r"MW(?![A-Za-z])", "power", 1000.0, True, "kW"),
    (r"kW(?![A-Za-z])", "power", 1.0, True, "kW"),
    (r"KW(?![A-Za-z])", "power", 1.0, True, "kW"),
    (r"mW(?![A-Za-z])", "power", 1.0e-6, True, "kW"),
    (r"W(?![A-Za-z])", "power", 0.001, True, "kW"),
    (r"gigawatts?\b", "power", 1.0e6, False, "kW"),
    (r"megawatts?\b", "power", 1000.0, False, "kW"),
    (r"kilowatts?\b", "power", 1.0, False, "kW"),
    (r"milliwatts?\b", "power", 1.0e-6, False, "kW"),
    (r"watts?\b", "power", 0.001, False, "kW"),
    (r"(?:hp|horsepower)\b", "power", 0.7457, False, "kW"),
    # energy (amount): per-cycle or with a duration
    (r"kWh\b|kW\s*h\b|kilowatt[- ]hours?\b", "energy", 3600.0, False, "kJ"),
    (r"Wh\b", "energy", 3.6, True, "kJ"),
    (r"GJ(?![A-Za-z/])", "energy", 1.0e6, True, "kJ"),
    (r"MJ(?![A-Za-z/])", "energy", 1000.0, True, "kJ"),
    (r"kJ(?![A-Za-z/])", "energy", 1.0, True, "kJ"),
    (r"J(?![A-Za-z/])", "energy", 0.001, True, "kJ"),
    (r"(?:kilo)?joules?\b", "energy", None, False, "kJ"),
    (r"(?:kcal|kilocalories?)\b", "energy", 4.184, False, "kJ"),
    # temperature
    (r"°\s*F\b|ºF\b|deg(?:rees?)?\s*F(?:ahrenheit)?\b|Fahrenheit\b", "temperature", _f_to_k, False, "K"),
    (r"°\s*C\b|ºC\b|℃|deg(?:rees?)?\s*C(?:elsius)?\b|Celsius\b|centigrade\b", "temperature", _c_to_k, False, "K"),
    (r"C(?![A-Za-z0-9])", "temperature", _c_to_k, True, "K"),
    (r"K(?![A-Za-z0-9])|kelvins?\b|°\s*K\b", "temperature", 1.0, True, "K"),
    (r"kelvins?\b", "temperature", 1.0, False, "K"),
    # mass (an amount, not a flow: kg/s etc. are matched above)
    (r"kg(?![A-Za-z/·.])|kilograms?\b", "mass", 1.0, False, "kg"),
    # geometry
    (rf"cm{SUP2}", "area", 1.0e-4, False, "m²"),
    (rf"m{SUP2}(?![A-Za-z0-9])", "area", 1.0, False, "m²"),
    (r"square\s+met(?:er|re)s?\b|sq\.?\s*m\b", "area", 1.0, False, "m²"),
    (r"km(?![A-Za-z0-9/])", "length", 1000.0, True, "m"),
    (r"cm(?![A-Za-z0-9/])", "length", 0.01, True, "m"),
    (r"mm(?![A-Za-z0-9/])", "length", 0.001, True, "m"),
    (r"m(?![A-Za-z0-9/²³^])", "length", 1.0, True, "m"),
    (r"met(?:er|re)s?\b", "length", 1.0, False, "m"),
    (r"(?:ft|feet|foot)\b", "length", 0.3048, False, "m"),
    # electrical, time, fraction
    (r"mA(?![A-Za-z])", "current", 0.001, True, "A"),
    (r"A(?![A-Za-z0-9])|amps?\b|amperes?\b", "current", 1.0, True, "A"),
    (r"amps?\b|amperes?\b", "current", 1.0, False, "A"),
    (r"V(?![A-Za-z0-9])|volts?\b", "voltage", 1.0, True, "V"),
    (r"%|percent\b|per\s*cent\b", "fraction", 0.01, False, ""),
    (r"Hz\b", "event_rate", 1.0, True, "1/s"),
]
UNIT_RES = [(re.compile(r"\s*(?:" + pattern + r")", 0 if case else re.IGNORECASE), kind, conv, label)
            for pattern, kind, conv, case, label in UNIT_SPECS]


@dataclass
class Quantity:
    value: float              # converted to engine units
    kind: str                 # dimension, e.g. "temperature", "power"
    unit: str                 # engine unit label
    quote: str                # text as written, e.g. "57 °C"
    start: int
    end: int
    raw: float
    sentence: int = 0
    role: Optional[str] = None
    owner: Optional[str] = None       # stream key
    pair: Optional[str] = None        # "from"/"between"/"range" partner info
    partner: Optional[int] = None
    note: str = ""


def _parse_number(match: re.Match) -> float:
    value = float(match["num"].replace(",", ""))
    exp = match["exp"]
    if exp:
        digits = re.search(r"[-+−]?\d+\)?\s*$", exp.strip())
        power = int(digits.group(0).replace("−", "-").rstrip(")").strip()) if digits else 0
        value *= 10.0 ** power
    if match["sign"]:
        value = -value
    return value


def _convert(raw: float, conv: Any, kind: str, unit_text: str) -> float:
    if callable(conv):
        return conv(raw)
    if conv is None:
        if kind in ("event_energy",):
            return raw * (1000.0 if re.match(r"\s*k", unit_text, re.IGNORECASE) else 1.0)
        if kind == "energy":
            return raw * (1.0 if re.match(r"\s*kilo", unit_text, re.IGNORECASE) else 0.001)
    return raw * conv


def sentence_spans(text: str) -> List[Tuple[int, int]]:
    spans, start = [], 0
    for match in re.finditer(r"(?<=[.!?;])\s+(?=\S)|\n+", text):
        spans.append((start, match.start()))
        start = match.end()
    spans.append((start, len(text)))
    return spans


def scan_quantities(text: str) -> List[Quantity]:
    """Find every number+unit, including 'from 600 to 400 K' style pairs."""
    sentences = sentence_spans(text)
    found: List[Quantity] = []
    pending: List[Tuple[re.Match, float]] = []   # numbers without their own unit
    for match in NUM_RE.finditer(text):
        raw = _parse_number(match)
        unit_match, spec = None, None
        for regex, kind, conv, label in UNIT_RES:
            unit_match = regex.match(text, match.end())
            if unit_match:
                spec = (kind, conv, label)
                break
        if not unit_match:
            pending.append((match, raw))
            continue
        kind, conv, label = spec
        unit_text = unit_match.group(0)
        if kind == "temperature" and conv == _c_to_k and match["sign"] is None:
            pass
        value = _convert(raw, conv, kind, unit_text)
        quote = text[match.start():unit_match.end()].strip()
        q = Quantity(value, kind, label, quote, match.start(), unit_match.end(), raw)
        # A bare number directly before this one ("from 600 to 400 K",
        # "between 600 and 300 K", "50 to 60 kW") inherits the same unit.
        if pending:
            prev_match, prev_raw = pending[-1]
            gap = text[prev_match.end():match.start()]
            joiner = re.fullmatch(r"\s*(to|and|-|–|—|or)\s*", gap, re.IGNORECASE)
            if joiner:
                before = text[max(0, prev_match.start() - 40):prev_match.start()].lower()
                if re.search(r"\bfrom\s+(?:an?\s+|the\s+)?(?:initial\s+)?(?:temperature\s+of\s+)?$", before) \
                        or re.search(r"\bfrom\b[^.;]*$", before[-25:]):
                    pair = "from"
                elif re.search(r"\bbetween\b[^.;]*$", before[-30:]):
                    pair = "between"
                else:
                    pair = "range"
                first = Quantity(_convert(prev_raw, conv, kind, unit_text), kind, label,
                                 text[prev_match.start():prev_match.end()] + " " + label.strip(),
                                 prev_match.start(), prev_match.end(), prev_raw)
                first.pair, q.pair = pair, pair
                found.append(first)
                first.partner = len(found)      # index of q after append
                q.partner = len(found) - 1
                pending.clear()
        found.append(q)
    # explicit with-units pairs: "from 500 °C to 150 °C", "between 600 K and 300 K"
    for i in range(len(found) - 1):
        a, b = found[i], found[i + 1]
        if a.pair or b.pair or a.kind != b.kind:
            continue
        gap = text[a.end:b.start]
        if re.fullmatch(r"\s*(?:to|down to|up to|and|-|–)\s*", gap, re.IGNORECASE):
            before = text[max(0, a.start - 30):a.start].lower()
            if re.search(r"\bfrom\s*$|\bfrom\s+\w+\s*$", before):
                a.pair = b.pair = "from"
            elif re.search(r"\bbetween\s*$|\bbetween\s+\w+\s*$", before):
                a.pair = b.pair = "between"
            elif re.fullmatch(r"\s*(?:to|-|–)\s*", gap) and a.kind != "temperature":
                a.pair = b.pair = "range"
            else:
                continue
            a.partner, b.partner = i + 1, i
    for q in found:
        for index, (s, e) in enumerate(sentences):
            if s <= q.start <= e:
                q.sentence = index
                break
    return found


# ============================================================================
# 2. Context-based role scoring
# ============================================================================

def _left_context(text: str, quantities: List[Quantity], index: int, sentences) -> str:
    q = quantities[index]
    s_start = sentences[q.sentence][0]
    prev_end = s_start
    for other in quantities[:index]:
        if other.sentence == q.sentence and other.end <= q.start and other.partner != index:
            prev_end = max(prev_end, other.end)
    if q.pair == "from" and q.partner is not None and q.partner < index:
        # second member of a pair: use the context before the pair
        prev_end = s_start
        for other in quantities[:q.partner]:
            if other.sentence == q.sentence:
                prev_end = max(prev_end, other.end)
        return text[prev_end:quantities[q.partner].start]
    return text[prev_end:q.start]


def _right_context(text: str, quantities: List[Quantity], index: int, sentences) -> str:
    q = quantities[index]
    s_end = sentences[q.sentence][1]
    nxt = s_end
    for other in quantities[index + 1:]:
        if other.sentence == q.sentence and other.start >= q.end:
            nxt = min(nxt, other.start)
            break
    right = text[q.end:nxt]
    # A qualifier that follows a number belongs to the same clause only.
    cut = re.search(r"[,;:()]|\b(?:and|while|but|which|whereas|then|so|if)\b", right, re.IGNORECASE)
    if cut:
        right = right[:cut.start()]
    words = re.findall(r"\S+", right)
    return " " + " ".join(words[:6])


def _score(left: str, right: str, rules: Dict[str, List[Tuple[str, float]]]) -> Dict[str, float]:
    scores = {role: 0.0 for role in rules}
    left_words = len(re.findall(r"\w+", left))
    for role, patterns in rules.items():
        for pattern, weight in patterns:
            for m in re.finditer(pattern, left, re.IGNORECASE):
                distance = left_words - len(re.findall(r"\w+", left[:m.end()]))
                scores[role] += weight / (1.0 + 0.35 * max(distance, 0))
            for m in re.finditer(pattern, right, re.IGNORECASE):
                distance = len(re.findall(r"\w+", right[:m.start()]))
                scores[role] += 0.85 * weight / (1.0 + 0.35 * distance)
    return scores


ENERGY_RULES = {
    "work_out": [(r"\bproduc\w*", 3), (r"\bgenerat\w*", 3), (r"\bdeliver\w*", 3), (r"\boutputs?\b", 2.5), (r"\bsupplies\b", 1.5),
                 (r"\byield\w*", 2.5), (r"\bdevelop\w*", 2.5), (r"\belectric\w*", 2.5), (r"\bwork\b", 2),
                 (r"\bpower\b", 1.0), (r"\bclaim\w*", 1.0), (r"\bnet\b", 1.5), (r"\bconvert\w*", 1.5),
                 (r"\bsupplies\s+(?:a\s+)?(?:load|grid)", 2.5), (r"\bdrives?\b", 1.5),
                 (r"\bdoes\b|\bperforms\b", 2.0)],
    "heat_in": [(r"\breceiv\w*", 3), (r"\babsorb\w*", 3), (r"\bsuppl(?:y|ied|ies)\b", 2.5), (r"\binput\b", 2.5),
                (r"\btak(?:es|ing|en)?\b", 2), (r"\bextract\w*", 2), (r"\bconsum\w*", 2), (r"\bheat\b", 1.2),
                (r"\bfrom\b", 0.8), (r"\bburn\w*", 2), (r"\bfuel\b", 1.5), (r"\bfed\b", 2), (r"\bdraws?\b", 2),
                (r"\bacquir\w*", 2), (r"\bgets?\b", 1.5)],
    "heat_out": [(r"\breject\w*", 3.5), (r"\bdischarg\w*", 3), (r"\bdump\w*", 3), (r"\brelease\w*", 2.5),
                 (r"\blos(?:e|es|t|s|ing)\b", 2.5), (r"\bto\s+(?:the|a)\b", 0.8), (r"\bwaste\b", 1.2),
                 (r"\bcondenser\b", 2), (r"\bsink\b", 1.5),
                 (r"\b(?:surroundings|environment|atmosphere|river|lake|sea|ocean)\b", 1.2),
                 (r"\bexhaust(?:s|ed)\b", 2.5), (r"\bgives?\s+(?:out|off|up)\b", 2.5)],
    "mech_in": [(r"\bmechanical\b", 3), (r"\bshaft\s+(?:power|work)\s+(?:input|supplied)", 3),
                (r"\binput\s+power\b|\bpower\s+input\b", 2), (r"\bpedal\w*", 2.5), (r"\bhuman\b", 1.5),
                (r"\bsupplied\s+to\s+(?:it|the\s+generator|the\s+device)", 2.5), (r"\bkinetic\b", 1.5),
                (r"\bavailable\b", 1.2), (r"\brecoverable\b", 2.5)],
}

TEMP_RULES = {
    "hot": [(r"\bhot\w*", 2.5), (r"\bsource\b", 3), (r"\bfurnace\b", 3), (r"\bboiler\b", 2.5),
            (r"\bcombustion\b", 2), (r"\bflame\b", 2), (r"\bgeothermal\b", 2), (r"\bhigh[- ]temperature\b", 3),
            (r"\bhigher\b", 1.5), (r"\bsuppl\w*\s+at\b", 2), (r"\breceiv\w*", 1.5), (r"\bfrom\b", 0.9),
            (r"\bupper\b", 1.5), (r"\bsun\b", 2), (r"\bcondens(?:ing|es)\b", 2), (r"\bsolidif\w*|\bfreez\w*", 1.5),
            (r"\bheat\s+(?:is\s+)?(?:taken|extracted|added)\b", 1.5), (r"\bwaste\s+heat\b", 1.5),
            (r"\bexhaust\b|\bflue\b", 2.5), (r"\bbrine\b|\bgeothermal\s+(?:fluid|water)\b", 1.5),
            (r"\bheater\b|\bburner\b|\bengine\s+block\b|\bcylinder\b", 2.0)],
    "cold": [(r"\bcold\w*", 2.5), (r"\bsink\b", 3), (r"\bsurroundings\b", 3), (r"\bambient\b", 3),
             (r"\benvironment\b", 3), (r"\batmosphere\b", 2.5), (r"\briver\b|\blake\b|\bsea\b|\bocean\b", 2.5),
             (r"\bcooling\b", 1.5), (r"\breject\w*", 2), (r"\blow[- ]temperature\b", 3), (r"\blower\b", 1.5),
             (r"\bdump\w*|\bdischarg\w*", 1.5), (r"\boutside\b|\broom\b", 1.5), (r"\bcooler\b|\bradiator\b|\bheat\s+sink\b", 2.5), (r"\bmelt\w*", 2),
             (r"\bboil\w*|\bevaporat\w*|\bvaporiz\w*", 1.0), (r"\bto\b", 0.6), (r"\bdead\s+state\b", 2.5)],
    "in": [(r"\benter\w*", 3), (r"\binlet\b", 3), (r"\binitial\w*", 2.5), (r"\bincoming\b", 2), (r"\bfrom\b", 1.5),
           (r"\bsupplied\s+at\b", 1.5), (r"\bstarts?\b", 1.5), (r"\barriv\w*", 2)],
    "out": [(r"\bleav\w*", 3), (r"\bexit\w*", 3), (r"\boutlet\b", 3), (r"\bfinal\w*", 2.5),
            (r"\b(?:cooled|heated|warmed|raised|reduced|brought)\s+(?:down\s+|up\s+)?to\b", 3),
            (r"\b(?:down|up)\s+to\b", 2), (r"\bdischarged\s+at\b", 2.5), (r"\breturn\w*", 2), (r"\bto\b", 1.0)],
}

RESERVOIR_WORDS = (r"\b(?:reservoir|surroundings|ambient|environment|atmosphere|sink|source|furnace|river|lake|sea|ocean|"
                   r"sun|flame|boiler|room|outside|air\s+outside|dead\s+state|ground|earth|wax|ice|condenser)\b")
PHASE_WORDS = r"\b(?:melt\w*|boil\w*|condens\w*|evaporat\w*|vaporiz\w*|freez\w*|solidif\w*|phase\s+change)\b"

STREAM_ADJ = r"(?:hot|cold|cool|cooling|warm|waste|exhaust|flue|chilled|heated|incoming|process|combustion|geothermal)"
STREAM_SUBST = (r"(?:thermal\s+oil|hot\s+oil|oil|sea\s*water|water|brine|air|exhaust\s+gas(?:es)?|flue\s+gas(?:es)?|"
                r"combustion\s+gas(?:es)?|gas(?:es)?|nitrogen|helium|carbon\s+dioxide|CO2|glycol|coolant|"
                r"fluid|stream|steam|exhaust)")
# The adjective nearest the substance decides the side: "cool hot oil" is the HOT oil, "cooling water" is cold.
STREAM_RE = re.compile(rf"\b(?:{STREAM_ADJ}\s+(?={STREAM_ADJ}\s))?(?:(?P<adj>{STREAM_ADJ})\s+)?(?P<subst>{STREAM_SUBST})\b", re.IGNORECASE)
EXCHANGER_RE = (r"\b(?:heat\s+)?exchangers?\b|\brecuperat\w*|\beconomi[sz]er\b|\bpreheat\w*|\bheat\s+recovery\b|"
                r"\bno\b[^.;]{0,30}\bshaft\s+work\b|\bcounter-?flow\b|\bparallel[- ]flow\b|"
                r"\bno\s+(?:shaft\s+)?work\b|\bwithout\s+(?:any\s+)?(?:shaft\s+)?work\b|\bzero\s+(?:shaft\s+)?work\b|"
                r"\ball\s+(?:the\s+)?heat\s+(?:released|lost|given\s+up|rejected)\b[^.]*\babsorbed\b")
EXCHANGER_DATA_TOLERANCE = 1.0e-3   # 0.1 % of the heat duty, for rounded textbook data


def _subst_key(subst: str) -> str:
    s = re.sub(r"\s+", " ", subst.lower())
    if "exhaust" in s or "flue" in s or "combustion" in s:
        return "exhaust gas"
    if "oil" in s:
        return "oil"
    if "sea" in s:
        return "seawater"
    if s in ("gases",):
        return "gas"
    if s in ("co2", "carbon dioxide"):
        return "carbon dioxide"
    if s in ("fluid", "stream", "coolant"):
        return "fluid"
    return s


@dataclass
class Stream:
    key: str
    substance: str
    adjective: str
    first_pos: int
    side: Optional[str] = None
    quantities: List[int] = field(default_factory=list)
    cp_linear: Optional[Tuple[float, float, str]] = None


# ============================================================================
# 3. The analysis object
# ============================================================================

@dataclass
class Analysis:
    text: str
    domain: str = "unknown"
    model: Optional[str] = None
    title: str = ""
    extracted: List[Dict[str, Any]] = field(default_factory=list)
    mapping: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    inferred: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    cases: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    quotes: Dict[str, str] = field(default_factory=dict)
    reason: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"domain": self.domain, "model": self.model, "title": self.title, "reason": self.reason,
                "extracted": self.extracted, "mapping": self.mapping,
                "assumptions": self.assumptions, "inferred": self.inferred,
                "missing": self.missing, "issues": self.issues}


DOMAIN_LABELS = {
    "thermal": "Thermal (heat engine / source–sink)",
    "mechanical": "Mechanical (recoverable work)",
    "wind": "Wind (kinetic energy, Betz limit)",
    "hydro": "Hydro (potential energy of water)",
    "solar": "Solar / radiative (Petela–Landsberg limit)",
    "chemical": "Chemical / electrochemical (Gibbs energy)",
    "work_consuming": "Refrigerator / heat pump (consumes work)",
    "unsupported": "Outside the implemented models",
    "no_input": "No energy input (perpetual-motion claim)",
}

DOMAIN_RULES = {
    "thermal": [(r"\bheat\s+engines?\b", 4), (r"\bengine\b", 1.5), (r"\breservoirs?\b", 2.5), (r"\bcarnot\b", 3),
                (r"\bfurnace\b|\bboiler\b", 2), (r"\bwaste\s+heat\b", 3), (r"\bexhaust\b|\bflue\b", 1.5),
                (r"\bgeothermal\b|\bORC\b|\brankine\b|\bbrayton\b|\botto\b|\bstirling\b|\bthermoelectric\b|\bTEG\b|\bOTEC\b", 3),
                (r"\bsource\b|\bsink\b", 1.5), (r"\bpower\s+plant\b|\bpower\s+cycle\b|\bthermal\s+energy\b", 1.5), (r"\bsteam\b", 1.0),
                (r"\bturbines?\b|\bcompressors?\b|\bpumps?\b|\bnozzles?\b|\bpiston\b|\bcylinder\b|\bclosed\s+system\b|"
                 r"\binternal\s+energy\b|\benthalpy\b|\bentropy\b|\bexchangers?\b", 2.0),
                (r"\bheat\s+exchanger\b|\bstreams?\b", 1.5), (r"\bhot\b|\bcold\b", 1.0), (r"\bheat\b", 1.0)],
    "mechanical": [(r"\bwalk\w*\b|\bfoot\w*\b|\bsteps?\b|\bshoes?\b|\bstride\w*\b", 3), (r"\bpedal\w*|\bcycling\b|\bbicycle\b", 3),
                   (r"\bpiezo\w*", 3), (r"\bmechanical\b", 2), (r"\bflywheel\b|\bspring\b|\bvibration\w*|\btreadmill\b|\bhand[- ]crank\w*", 2.5),
                   (r"\bhuman\b", 1.5), (r"\bkinetic\s+energy\s+harvest\w*", 2)],
    "wind": [(r"\bwind\b", 4), (r"\bwindmill\b", 4), (r"\brotor\b|\bblades?\b", 2), (r"\bswept\s+area\b", 3)],
    "hydro": [(r"\bhydro\w*\b", 4), (r"\bdam\b", 3), (r"\bhead\b", 2), (r"\bwaterfall\b|\bpenstock\b|\bfalls?\b", 2.5),
              (r"\bmicro[- ]?hydro\b", 3), (r"\bwater\s+wheel\b", 3), (r"\bheight\b|\belevation\b", 1.0)],
    "solar": [(r"\bsolar\b", 4), (r"\bsun\w*\b", 3), (r"\bphotovoltaic\w*|\bPV\b", 4), (r"\birradian\w*|\binsolation\b", 3),
              (r"\bradiation\b|\bblack\s*body\b", 2)],
    "chemical": [(r"\bfuel\s+cells?\b", 5), (r"\bgibbs\b|ΔG|\bdelta\s*G\b", 4), (r"\breaction\b", 2), (r"\bbattery\b|\bbatteries\b", 2),
                 (r"\belectrochemical\b|\belectrolyte\b", 2), (r"\bhydrogen\b|\bmethanol\b|\bethanol\b", 1.5)],
    "work_consuming": [(r"\brefrigerat\w*|\bfreezer\b|\bheat\s+pumps?\b|\bair[- ]condition\w*|\bCOP\b|\bcoefficient\s+of\s+performance\b|\bchiller\b", 5)],
}
# Processes with no validated model here: they get MODEL REQUIRED instead of a forced, misleading verdict.
UNSUPPORTED_RE = (r"\btransient\b|\btime[- ]dependent\b|\bchanges?\s+with\s+time\b|\bsuddenly\s+(?:exposed|immersed|placed|dropped)\b|"
                  r"\bhow\s+long\b|\bcooling\s+time\b|\blumped\s+capacitance\b|\btidal\b|\btides?\b|\bwave\s+energy\b|"
                  r"\bnuclear\s+(?:fission|fusion|reactor)\b|\bmagnet\w*\s+motor\b")
KIND_DOMAIN = {"velocity": ("wind", 2.5), "event_energy": ("mechanical", 4), "event_rate": ("mechanical", 2),
               "molar_energy": ("chemical", 4), "molar_rate": ("chemical", 2), "irradiance": ("solar", 4),
               "vol_flow": ("hydro", 1.5), "cp": ("thermal", 3), "specific_energy": ("thermal", 1),
               "temperature": ("thermal", 1.2)}


NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "twelve": 12, "twenty": 20, "fifty": 50, "hundred": 100}


def _digits_for_words(text: str) -> str:
    """'Two kg' -> '2 kg' (only when a unit follows, so ordinary words are untouched)."""
    return re.sub(r"\b(" + "|".join(NUMBER_WORDS) + r")\b(?=\s+(?:kg|kilograms?|kJ|kW|W|K|°C|m|L|litres?|liters?|mol)\b)",
                  lambda m: str(NUMBER_WORDS[m.group(1).lower()]), text, flags=re.IGNORECASE)


class ScenarioAnalyzer:
    def __init__(self, text: str):
        self.text = _digits_for_words(text.strip())
        self.sentences = sentence_spans(self.text)
        self.q = scan_quantities(self.text)
        self.a = Analysis(self.text)
        self.streams: Dict[str, Stream] = {}

    # ------------------------------------------------------------------ helpers
    def ctx(self, index: int) -> Tuple[str, str]:
        return (_left_context(self.text, self.q, index, self.sentences),
                _right_context(self.text, self.q, index, self.sentences))

    def wide_left(self, index: int, chars: int = 120) -> str:
        q = self.q[index]
        s_start = self.sentences[q.sentence][0]
        return self.text[max(s_start, q.start - chars):q.start]

    def record(self, index: int, label: str, value: Optional[float] = None, unit: Optional[str] = None,
               field_name: Optional[str] = None) -> None:
        q = self.q[index]
        entry = {"quantity": label, "value": q.value if value is None else value,
                 "unit": q.unit if unit is None else unit, "quote": q.quote}
        if entry not in self.a.extracted:
            self.a.extracted.append(entry)
        if field_name:
            self.a.quotes[field_name] = q.quote

    def of_kind(self, *kinds: str) -> List[int]:
        return [i for i, q in enumerate(self.q) if q.kind in kinds]

    def has(self, pattern: str) -> bool:
        return re.search(pattern, self.text, re.IGNORECASE) is not None

    # ------------------------------------------------------------------ domain
    def detect_domain(self) -> str:
        scores = {d: 0.0 for d in DOMAIN_RULES}
        for domain, rules in DOMAIN_RULES.items():
            for pattern, weight in rules:
                scores[domain] += weight * len(re.findall(pattern, self.text, re.IGNORECASE))
        for q in self.q:
            if q.kind in KIND_DOMAIN:
                domain, weight = KIND_DOMAIN[q.kind]
                scores[domain] += weight
        if any(q.kind == "length" for q in self.q) and any(q.kind in ("vol_flow", "mass_flow") for q in self.q) \
                and self.has(r"\bhead\b|\bheight\b|\bfalls?\b|\bdam\b|\bdrop\b"):
            scores["hydro"] += 4
        if self.has(r"\bwind\b") and any(q.kind == "velocity" for q in self.q):
            scores["wind"] += 3
        if scores["work_consuming"] > 0 and not self.has(r"\bheat\s+engine\b"):
            return "work_consuming"
        best = max(scores, key=scores.get)
        if scores[best] <= 0:
            return "unknown"
        return best

    # ------------------------------------------------------------------ roles
    def assign_energy_roles(self) -> None:
        for i, q in enumerate(self.q):
            if q.kind not in ("power", "energy"):
                continue
            left, right = self.ctx(i)
            scores = _score(left, right, ENERGY_RULES)
            # "of heat" right after the number is a strong heat marker
            if re.match(r"\s*(?:of\s+)?heat\b", right, re.IGNORECASE) and scores["heat_out"] < 2.5:
                scores["heat_in"] += 2.0
            if re.match(r"\s*(?:of\s+)?(?:useful\s+|net\s+|shaft\s+|mechanical\s+)?(?:work|power|electric\w*)\b",
                        right, re.IGNORECASE) and not re.search(r"\bmechanical\b", right[:25], re.IGNORECASE):
                scores["work_out"] += 2.0
            best = max(scores, key=scores.get)
            q.role = best if scores[best] > 0.6 else None

    def assign_temperature_roles(self) -> None:
        for i, q in enumerate(self.q):
            if q.kind != "temperature":
                continue
            left, right = self.ctx(i)
            q.note = ""
            phase_ctx = left + right
            # "600 K reservoir" qualifies; "600 K with ambient air" / "800 K rejects" do not.
            cut = re.search(r"\b(?:with|from|to|at|in|on|by|into|of|for|is|are|was|rejects?|receives?|"
                            r"produces?|and|while|whereas)\b", right, re.IGNORECASE)
            right = right[:cut.start()] if cut else right
            scores = _score(left, right, TEMP_RULES)
            if q.pair == "from" and q.partner is not None:
                q.role = "in" if q.partner > i else "out"
                continue
            q.role = max(scores, key=scores.get) if max(scores.values()) > 0.5 else None
            q.scores = scores  # type: ignore[attr-defined]
            if re.search(PHASE_WORDS, phase_ctx, re.IGNORECASE):
                q.note = "phase"

    # ------------------------------------------------------------------ streams
    def build_streams(self) -> None:
        mentions = []
        for m in STREAM_RE.finditer(self.text):
            subst = _subst_key(m.group(0) if (m["adj"] or "").lower() in ("exhaust", "flue", "combustion")
                               and re.search(r"gas", m["subst"], re.I) else m["subst"])
            adj = (m["adj"] or "").lower()
            # "heat engine", "water" in "water wheel" etc. are not streams
            after = self.text[m.end():m.end() + 12].lower()
            if subst == "steam" and re.match(r"\s*(?:engine|turbine|power)", after):
                continue
            mentions.append((m.start(), m.end(), subst, adj))
        if not mentions:
            return
        # Merge mentions into streams.
        order: List[str] = []
        mention_keys = []
        for start, end, subst, adj in mentions:
            side_hint = "hot" if subst in ("exhaust gas",) or adj in ("hot", "warm", "waste", "exhaust", "flue", "heated", "combustion", "geothermal") \
                else "cold" if adj in ("cold", "cool", "cooling", "chilled") else ""
            key = None
            if side_hint:
                key = f"{side_hint} {subst}"
            else:
                same = [k for k in order if self.streams[k].substance == subst]
                if subst == "fluid" and not same:
                    same = [k for k in order if self.streams[k].substance != "fluid"]
                if len(same) == 1:
                    key = same[0]
                elif len(same) > 1:
                    key = None  # resolved by proximity below
                else:
                    key = subst
            if key is not None and key not in self.streams:
                self.streams[key] = Stream(key, subst, side_hint, start, side=side_hint or None)
                order.append(key)
            mention_keys.append((start, end, key, subst))
        # Resolve ambiguous mentions to the most recently named stream of that substance.
        resolved = []
        last_by_subst: Dict[str, str] = {}
        for start, end, key, subst in mention_keys:
            if key is None and subst == "fluid" and resolved:
                # A generic "stream"/"fluid" belongs to the stream named just before it
                # ("A cold water stream ..." is the cold water, not the earlier hot water).
                key = resolved[-1][2]
            if key is None:
                key = last_by_subst.get(subst) or next((k for k in order if self.streams[k].substance == subst), None)
            if key is None:
                continue
            last_by_subst[subst] = key
            resolved.append((start, end, key))
        self._mentions = resolved

        # Attach stream-type quantities to the nearest stream mention.
        for i, q in enumerate(self.q):
            if q.kind not in ("temperature", "mass_flow", "vol_flow", "cp"):
                continue
            owner = self._owner_for(i)
            if owner is None:
                continue
            if q.kind == "temperature":
                left, right = self.ctx(i)
                clause = left[-60:]
                res = [m.end() for m in re.finditer(RESERVOIR_WORDS, clause, re.IGNORECASE)]
                strm = [m.end() for m in STREAM_RE.finditer(clause)]
                if res and (not strm or max(res) > max(strm)) and q.pair != "from":
                    continue  # reservoir temperature, not a stream state
                if q.note == "phase":
                    continue
            q.owner = owner
            self.streams[owner].quantities.append(i)
        # Linear Cp expressions "Cp = a + bT" in each stream's scope.
        for m in re.finditer(r"\bc_?p\s*(?:\(\s*T\s*\))?\s*=\s*(" + NUM_PLAIN + r")\s*([+\-−])\s*(" + NUM_PLAIN
                             + r")\s*[*×·]?\s*T\b", self.text, re.IGNORECASE):
            a_val = _parse_number(NUM_RE.search(m.group(1)))
            b_val = abs(_parse_number(NUM_RE.search(m.group(3))))
            if m.group(2) in "-−":
                b_val = -b_val
            owner = self._owner_at(m.start())
            if owner is None and len(self.streams) == 1:
                owner = next(iter(self.streams))
            if owner is not None:
                self.streams[owner].cp_linear = (a_val, b_val, m.group(0))
                self.a.extracted.append({"quantity": f"{owner} Cp(T)", "value": f"{a_val:g} + {b_val:g}·T",
                                         "unit": "kJ/(kg K)", "quote": m.group(0)})
            # cp-expression numbers must not be read as other quantities
            self.q = [q for q in self.q if not (m.start() <= q.start < m.end())]

    def _owner_at(self, pos: int) -> Optional[str]:
        sentence = next((k for k, (s, e) in enumerate(self.sentences) if s <= pos <= e), 0)
        s_start, s_end = self.sentences[sentence]
        before = [(st, key) for st, en, key in self._mentions if s_start <= st < pos]
        if before:
            return max(before)[1]
        after = [(st, key) for st, en, key in self._mentions if pos < st <= s_end]
        if after:
            return min(after)[1]
        prev = [(st, key) for st, en, key in self._mentions if st < s_start]
        if prev and re.match(r"\s*(?:it|its|this|the\s+stream|the\s+same|which|they|their)\b",
                             self.text[s_start:s_end], re.IGNORECASE):
            return max(prev)[1]
        return None

    def _owner_for(self, index: int) -> Optional[str]:
        q = self.q[index]
        follow = re.match(r"\s*of\s+(?:the\s+)?", self.text[q.end:])
        if follow:
            pos = q.end + follow.end()
            direct = [key for st, en, key in self._mentions if st == pos]
            if direct:
                return direct[0]
        return self._owner_at(q.start)

    # ------------------------------------------------------------------ claim
    def claim(self, domain_inputs: Tuple[str, ...] = ("heat_in",)) -> Tuple[Optional[float], Optional[int], Optional[float], Optional[int]]:
        """Return (claimed work kW, quantity index, claimed efficiency fraction, index)."""
        work = [i for i, q in enumerate(self.q) if q.kind in ("power", "energy") and q.role == "work_out"]
        if any(self.q[i].pair == "range" for i in work):
            self.a.issues.append("The claimed output is given as a range. Give a single claimed value "
                                 "(or test the upper value) to obtain a verdict.")
            return None, None, None, None
        values = {round(self.q[i].value, 12) for i in work}
        if len(values) > 1:
            self.a.issues.append("Several different output values were found: "
                                 + ", ".join(self.q[i].quote for i in work) + ". State which one is claimed.")
            return None, None, None, None
        eff = None
        eff_index = None
        for i, q in enumerate(self.q):
            left = self.wide_left(i, 60)
            if q.kind == "fraction" and re.search(r"\befficien\w*|\bconvert\w*|\bconversion\b", left + self.ctx(i)[1][:30],
                                                  re.IGNORECASE):
                eff, eff_index = q.value, i
        # efficiency written as a bare decimal: "efficiency of 0.42"
        if eff is None:
            m = re.search(r"\befficiency\s+(?:of|is|=|:)?\s*(0?\.\d+|1\.0+)\b(?!\s*[%A-Za-z])", self.text, re.IGNORECASE)
            if m:
                eff = float(m.group(1))
                self.a.extracted.append({"quantity": "Claimed efficiency", "value": eff, "unit": "fraction",
                                         "quote": m.group(0)})
        if eff_index is not None:
            self.record(eff_index, "Claimed efficiency", eff * 100, "%", "claimed_efficiency")
        if work:
            return self.q[work[0]].value, work[0], eff, eff_index
        return None, None, eff, eff_index

    def single_power(self, exclude_roles=("heat_in", "heat_out", "mech_in")) -> Optional[int]:
        cands = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role not in exclude_roles]
        if len(cands) == 1:
            return cands[0]
        return None

    # ================================================================== routes
    def analyse(self) -> Analysis:
        a = self.a
        if not self.q and not self.text:
            a.missing.append("A description of the process with its numbers.")
            return a
        for q in self.q:                     # a temperature at or below 0 K is a typing error, not physics
            if q.kind == "temperature" and q.value <= 0:
                raise thermo.ThermoInputError(f"{q.quote} is at or below absolute zero (0 K = −273.15 °C); "
                                              "check the temperature and its unit.")
        domain = self.detect_domain()
        if re.search(r"\bperpetual\s+motion\b|\bwithout\s+(?:any\s+)?(?:energy|fuel|power|heat)(?:\s+input)?\b|"
                     r"\bno\s+(?:energy|fuel|power|heat)\s+input\b|\bfrom\s+nothing\b|\bfree\s+energy\b|"
                     r"\bwith\s+zero\s+(?:energy|fuel|power)\s+input\b|"
                     r"\b(?:has|with|needs|uses|takes)\s+no\s+(?:[\w-]+,?\s+){0,7}(?:or\s+(?:any\s+)?other\s+)?(?:energy\s+)?inputs?\b",
                     self.text, re.IGNORECASE):
            domain = "no_input"
        elif re.search(UNSUPPORTED_RE, self.text, re.IGNORECASE) and not (
                domain == "hydro" and self.of_kind("length") and self.of_kind("vol_flow", "mass_flow")):
            domain = "unsupported"
        a.domain = domain
        self.assign_energy_roles()
        self.assign_temperature_roles()
        if domain in ("thermal", "unknown"):
            self.build_streams()
        handler = {
            "thermal": self.route_thermal, "mechanical": self.route_mechanical, "wind": self.route_wind,
            "hydro": self.route_hydro, "solar": self.route_solar, "chemical": self.route_chemical,
            "work_consuming": self.route_work_consuming, "no_input": self.route_no_input,
            "unsupported": self.route_unsupported,
        }.get(domain, self.route_unknown)
        handler()
        return a

    # ---------------------------------------------------------------- thermal
    def route_thermal(self) -> None:
        a = self.a
        # Models 3, 4 and 5 are chosen only on strong cues; otherwise the source/sink routes below apply.
        if self.try_steady_cv() or self.try_closed_system() or self.try_property_request() or self.try_exchanger_duties():
            return
        hot_streams = [s for s in self.streams.values() if s.quantities or s.cp_linear]
        # Decide stream sides from their temperatures and adjectives.
        complete_streams = []
        for s in hot_streams:
            temps = [i for i in s.quantities if self.q[i].kind == "temperature"]
            flows = [i for i in s.quantities if self.q[i].kind in ("mass_flow", "vol_flow")]
            if not temps:
                continue
            if len(temps) == 1 and not flows and s.cp_linear is None and \
                    not any(self.q[i].kind == "cp" for i in s.quantities):
                # a single temperature with no flow is a reservoir-like source/sink
                q = self.q[temps[0]]
                q.owner = None
                if s.side and q.role not in ("hot", "cold"):
                    q.role = s.side
                if s.side == "hot" or q.role == "hot":
                    self.a.assumptions.append(
                        f"Only one temperature is given for the {s.key} ({q.quote}); it is treated as an isothermal "
                        "source at that temperature. A stream that cools while giving up heat has a lower effective "
                        "temperature, so this gives the most generous (upper-bound) ceiling.")
                continue
            complete_streams.append(s)
        stream_info = {s.key: self.stream_state(s) for s in complete_streams}
        reservoirs = [i for i, q in enumerate(self.q) if q.kind == "temperature" and q.owner is None]
        hot_res, cold_res = self.classify_reservoirs(reservoirs)

        hot_s = [k for k, st in stream_info.items() if st["side"] == "hot"]
        cold_s = [k for k, st in stream_info.items() if st["side"] == "cold"]
        if len(hot_s) > 1 or len(cold_s) > 1:
            a.issues.append("More than one hot or cold stream was found; describe one source stream and one sink stream.")
            a.missing.append("A single source (hot) stream and a single sink (cold) stream.")
            return
        latent = self.latent_reservoir()
        if latent is not None:
            cold_res = [latent]
        if hot_s and cold_s:
            return self.thermal_two_streams(stream_info[hot_s[0]], stream_info[cold_s[0]])
        bare_cold = [st for st in self.streams.values() if not st.quantities and not st.cp_linear
                     and (st.side == "cold" or re.search(r"\bcold|cool", st.key)) and st.key not in stream_info]
        if hot_s and bare_cold and not cold_res:
            a.model, a.title = "finite_stream_engine", "Finite hot stream → device → finite cold stream"
            self._record_stream(dict(stream_info[hot_s[0]], side="hot"), stream_info[hot_s[0]]["key"].capitalize())
            a.missing += [f"Inlet temperature of the {bare_cold[0].key}.", f"Mass flow rate of the {bare_cold[0].key}.",
                          f"Heat capacity Cp of the {bare_cold[0].key} (if it is not water)."]
            return
        if hot_s:
            return self.thermal_stream_reservoir(stream_info[hot_s[0]], None, cold_res, "hot")
        if cold_s:
            return self.thermal_stream_reservoir(stream_info[cold_s[0]], hot_res, None, "cold")
        return self.thermal_reservoirs(hot_res, cold_res)

    # ---------------------------------------------------------------- Models 3, 4, 5 from text
    IN_WORDS = r"\benter\w*|\binlet\b|\binitial\w*|\bstate\s*1\b|\bsupplied\b|\bat\s+the\s+inlet\b"
    OUT_WORDS = r"\bleav\w*|\bexit\w*|\boutlet\b|\bdischarg\w*|\bstate\s*2\b|\bfinal\w*"

    def _in_or_out(self, index: int, order_position: int) -> str:
        left = self.wide_left(index, 140)
        last_in = max((m.end() for m in re.finditer(self.IN_WORDS, left, re.IGNORECASE)), default=-1)
        last_out = max((m.end() for m in re.finditer(self.OUT_WORDS, left, re.IGNORECASE)), default=-1)
        if last_in < 0 and last_out < 0:
            return "in" if order_position == 0 else "out"
        return "in" if last_in > last_out else "out"

    DEVICE_RE = (r"\bturbines?\b|\bcompressors?\b|\bnozzles?\b|\bdiffusers?\b|\bpumps?\b|\bthrottl\w*|\bfans?\b|"
                 r"\bblowers?\b|\bcontrol\s+volume\b|\bsteady[- ]flow\b|\bsteady\s+(?:open\s+)?(?:device|system)\b")
    CONSUMES_WORK = r"\bconsum\w*|\brequir\w*|\bneeds?\b|\binput\b|\bdriven\b|\babsorbs?\b|\buses?\b|\bsupplied\s+to\b|\bon\s+the\b"

    def try_steady_cv(self) -> bool:
        """Model 3: steady open device. Uses whatever is stated (mass flows, h, s, heat, work); a balance that cannot
        be evaluated is reported by the engine, a missing enthalpy is asked for."""
        enters = self.has(r"\benter\w*|\binlet\b|\breceiv\w*") and self.has(r"\bexit\w*|\bleav\w*|\boutlet\b|\bdischarg\w*")
        hs = [i for i, q in enumerate(self.q) if q.kind == "specific_energy"
              and not re.search(r"latent|fusion|vapori|heating\s+value|LHV|HHV", self.wide_left(i, 40), re.I)]
        ss = [i for i, q in enumerate(self.q) if q.kind == "cp"
              and re.search(r"\bs\s*\d?\s*[=:]|\bentropy\b", self.wide_left(i, 40), re.I)]
        flows = self.of_kind("mass_flow")
        device = self.has(self.DEVICE_RE)
        if not ((device or enters) and (hs or (len(flows) >= 2 and enters and not self.of_kind("temperature")))):
            return False
        if not hs and not device and not self.has(r"\bdevice\b|\bsteady\b|\baccumulat\w*"):
            return False
        a = self.a
        a.model, a.title = "steady_control_volume", "Steady control volume (open device)"
        if not hs:                                     # mass flows only: the mass balance is the test
            ins = [i for i in flows if self._in_or_out(i, 0 if i == flows[0] else 1) == "in"
                   or re.search(r"receiv\w*|enter\w*|inlet", self.wide_left(i, 40), re.I)]
            outs = [i for i in flows if i not in ins]
            if not ins or not outs:
                ins, outs = flows[:1], flows[1:]
                a.assumptions.append("The first mass flow is taken as the inlet.")
            for i in ins:
                self.record(i, "Inlet mass flow", field_name="inlet_mass_flow_kg_s")
            for i in outs:
                self.record(i, "Outlet mass flow", field_name="outlet_mass_flow_kg_s")
            a.mapping += ["Boundary: steady open device (Model 3); steady state means no mass accumulation.",
                          "Test: Σṁ_in − Σṁ_out = 0. Energy and entropy need h and s for each stream."]
            a.cases["steady_control_volume"] = {"model": "steady_control_volume", "title": a.title, "description": self.text,
                "inlets": [{"mass_flow_kg_s": self.q[i].value} for i in ins],
                "outlets": [{"mass_flow_kg_s": self.q[i].value} for i in outs]}
            return True
        h = {self._in_or_out(i, n): i for n, i in enumerate(hs[:2])}
        sv = {self._in_or_out(i, n): i for n, i in enumerate(ss[:2])}
        if len(hs) >= 2 and set(h) != {"in", "out"}:
            h = {"in": hs[0], "out": hs[1]}
            a.assumptions.append("Inlet/outlet wording was unclear: the first h is taken as the inlet state.")
        if len(ss) >= 2 and set(sv) != {"in", "out"}:
            sv = {"in": ss[0], "out": ss[1]}
        for side, label in (("in", "Inlet"), ("out", "Outlet")):
            if side in h:
                self.record(h[side], f"{label} specific enthalpy h", field_name=f"h_{side}")
            else:
                a.missing.append(f"{label} state: specific enthalpy h (kJ/kg), and s for the Second Law.")
            if side in sv:
                self.record(sv[side], f"{label} specific entropy s", field_name=f"s_{side}")
        if not flows:
            a.missing.append("Mass flow rate through the device (kg/s).")
        if a.missing:
            return True
        mdot = self.q[flows[0]].value
        self.record(flows[0], "Mass flow rate", field_name="mass_flow_kg_s")
        heat = []
        heats = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role in ("heat_in", "heat_out")
                 and re.search(r"\bheat\b|\blos\w*", self.wide_left(i, 30) + self.text[self.q[i].end:self.q[i].end + 12], re.I)]
        if heats and not self.has(r"\badiabatic\b|\binsulated\b"):
            i = heats[0]
            temps = [j for j, q in enumerate(self.q) if q.kind == "temperature" and q.sentence == self.q[i].sentence and j > i]
            sign = -1.0 if self.q[i].role == "heat_out" else 1.0
            item = {"heat_into_cv_kw": sign * self.q[i].value}
            self.record(i, "Heat transfer (+ into the device)", sign * self.q[i].value, "kW", "heat_into_cv_kw")
            if temps:
                item["boundary_temperature_K"] = self.q[temps[0]].value
                self.record(temps[0], "Boundary temperature for that heat", field_name="boundary_temperature_K")
            heat = [item]
        works = [i for i, q in enumerate(self.q) if q.kind == "power" and i not in heats]
        case = {"model": "steady_control_volume", "title": a.title, "description": self.text,
                "inlets": [{"mass_flow_kg_s": mdot, "specific_enthalpy_kj_kg": self.q[h["in"]].value}],
                "outlets": [{"mass_flow_kg_s": mdot, "specific_enthalpy_kj_kg": self.q[h["out"]].value}], "heat": heat}
        if len(sv) == 2:
            case["inlets"][0]["specific_entropy_kj_kg_k"] = self.q[sv["in"]].value
            case["outlets"][0]["specific_entropy_kj_kg_k"] = self.q[sv["out"]].value
        else:
            a.issues.append("Specific entropies were not given, so only the mass and energy balances can be checked.")
        if len(works) > 1:
            a.issues.append("Several power values were found; the first one not identified as heat is taken as the work.")
        if works:
            i = works[0]
            clause = re.split(r"[,;]|\band\b", self.wide_left(i, 60))[-1]
            consumed = re.search(self.CONSUMES_WORK, clause, re.I) or (
                self.has(r"\bcompressors?\b|\bpumps?\b|\bfans?\b|\bblowers?\b")
                and not re.search(r"produc\w*|generat\w*|deliver\w*|output", clause, re.I))
            case["W_out_kw"] = -self.q[i].value if consumed else self.q[i].value
            self.record(i, "Shaft work (+ out of the device)" if not consumed else "Shaft work input (entered as W_out < 0)",
                        case["W_out_kw"], "kW", "W_out_kw")
            if consumed:
                a.inferred.append(f"Work is consumed, so W_out = −{self.q[i].value:g} kW (sign convention: work out positive).")
        else:
            a.issues.append("No work value was found: the work is derived from the First Law and only the Second "
                            "Law (S_gen ≥ 0) is tested.")
        a.assumptions.append("Kinetic and potential energy changes are neglected; h and s are taken as given (from tables).")
        a.mapping += ["Boundary: steady open device with one inlet and one outlet (Model 3).",
                      "Tests: mass in = out; Q̇ − Ẇ + ṁ(h_in − h_out) = 0; S_gen = ṁ(s_out − s_in) − ΣQ̇/T_b ≥ 0."]
        a.cases["steady_control_volume"] = case
        return True

    def try_closed_system(self) -> bool:
        """Model 4: fixed mass. First Law Q − W = ΔE always; S_gen = ΔS − ΣQ/T_b when ΔS is stated (or for a cycle)."""
        if not self.has(r"\bpiston\b|\bcylinder\b|\brigid\s+tank\b|\bclosed\s+system\b|\bsealed\b|\bclosed\s+tank\b|"
                        r"\binternal\s+energy\b|\bΔU\b|\bdelta\s*U\b"):
            return False
        a = self.a
        energies = [i for i, q in enumerate(self.q) if q.kind == "energy"]
        u_left = lambda i: self.wide_left(i, 60)
        u1 = [i for i in energies if re.search(r"\binitial\w*\s+internal\s+energy|\bU\s*1\b|\bU_1\b", u_left(i), re.I)]
        u2 = [i for i in energies if re.search(r"\bfinal\w*\s+internal\s+energy|\bU\s*2\b|\bU_2\b", u_left(i), re.I)]
        de = [i for i in energies if i not in u1 + u2 and re.search(
              r"internal\s+energy|energy\s+of\s+the\s+(?:system|gas|fluid)|ΔU|ΔE|delta\s*[UE]", u_left(i), re.I)]
        heats = [i for i in energies if i not in u1 + u2 + de and self.q[i].role in ("heat_in", "heat_out")
                 and not re.search(r"\bwork\b", self.text[self.q[i].end:self.q[i].end + 20], re.I)]
        works = [i for i in energies if i not in u1 + u2 + de + heats]
        cycle = self.has(r"\bcycle\b|\bcyclic\b")
        if not (heats or works or de):
            return False
        a.model, a.title = "closed_system", "Closed system (energy and entropy balance)"
        sign = lambda i: -1.0 if re.search(r"decreas|fall|drop|reduc|lower|loses", u_left(i)[-40:], re.I) else 1.0
        case: Dict[str, Any] = {"model": "closed_system", "title": a.title, "description": self.text, "heat": []}
        if de:
            case["delta_E_kj"] = sign(de[0]) * self.q[de[0]].value
            self.record(de[0], "Change in internal energy ΔU", case["delta_E_kj"], "kJ", "delta_E_kj")
        elif cycle:
            case["delta_E_kj"] = 0.0
            a.inferred.append("Complete cycle: the system returns to its initial state, so ΔE = 0 and ΔS = 0.")
        for key, idx, label in (("U1_kj", u1, "Initial internal energy U₁"), ("U2_kj", u2, "Final internal energy U₂")):
            if idx:
                case[key] = self.q[idx[0]].value
                self.record(idx[0], label, field_name=key)
        entropy = self.of_kind("entropy")
        if entropy:
            case["delta_S_system_kj_K"] = sign(entropy[0]) * self.q[entropy[0]].value
            self.record(entropy[0], "Change in system entropy ΔS", case["delta_S_system_kj_K"], "kJ/K", "delta_S_system_kj_K")
        elif cycle:
            case["delta_S_system_kj_K"] = 0.0
        for i in heats:
            q = self.q[i]
            value = (-1.0 if q.role == "heat_out" else 1.0) * q.value
            item = {"heat_into_system_kj": value}
            temps = [j for j, t in enumerate(self.q) if t.kind == "temperature" and t.sentence == q.sentence and j > i
                     and not any(k in heats for k in range(i + 1, j))]
            if temps:
                item["boundary_temperature_K"] = self.q[temps[0]].value
                self.record(temps[0], "Boundary temperature for that heat")
            case["heat"].append(item)
            self.record(i, "Heat transfer (+ into the system)", value, "kJ")
        if works:
            i = works[0]
            clause = re.split(r"[,;]|\band\b", u_left(i))[-1]
            on = re.search(r"\bon\s+the\s+(?:gas|system|fluid)|\bconsum\w*|\breceiv\w*\s*$|\binput\b|\bcompress\w*", clause, re.I)
            case["W_out_kj"] = -self.q[i].value if on else self.q[i].value
            self.record(i, "Work output (+ by the system)", case["W_out_kj"], "kJ", "W_out_kj")
        known = sum(x is not None for x in (case.get("delta_E_kj"), case.get("W_out_kj"), case["heat"] or None))
        if known < 2 and not (u1 and u2):
            a.missing.append("Two of: heat transfer, work and change in internal energy (kJ).")
            return True
        a.mapping += ["Boundary: closed system — no mass crosses it (Model 4).",
                      "Tests: ΣQ − W − ΔE = 0, and S_gen = ΔS_system − ΣQ/T_b ≥ 0 when ΔS and boundary temperatures are known."]
        a.cases["closed_system"] = case
        return True

    def try_property_request(self) -> bool:
        """Property utility: the question asks only for property changes (Δh, Δs, T_eff, latent heat), no device claim."""
        if not self.has(r"\b(?:calculate|find|determine|compute|what\s+(?:is|are))\b[^.?]*\b(?:enthalpy|entropy)(?:\s+and\s+entropy)?\s+changes?|"
                        r"\b(?:calculate|find|determine|compute)\b[^.?]*\b(?:effective|log[- ]mean)\s+temperature|"
                        r"\b(?:calculate|find|determine|compute)\b[^.?]*\bproperty\s+changes?"):
            return False
        if any(q.role == "work_out" for q in self.q if q.kind in ("power", "energy")):
            return False
        temps = [i for i, q in enumerate(self.q) if q.kind == "temperature"]
        latent = [i for i, q in enumerate(self.q) if q.kind == "specific_energy"
                  and re.search(r"latent|vapori|evapor|fusion|melt|boil|condens", self.wide_left(i, 60), re.I)]
        if len(temps) < 2 and not (latent and temps):
            return False
        a = self.a
        a.model, a.title = "property_change", "Property change of a material"
        case: Dict[str, Any] = {"model": "property_change", "title": a.title, "description": self.text}
        masses = self.of_kind("mass")
        if masses:
            case["mass_kg"] = self.q[masses[0]].value
            self.record(masses[0], "Mass", field_name="mass_kg")
        if latent:
            case.update(latent_heat_kj_kg=self.q[latent[0]].value, phase_change_temperature_K=self.q[temps[0]].value)
            self.record(latent[0], "Latent heat L", field_name="latent_heat_kj_kg")
            self.record(temps[0], "Phase-change temperature", field_name="phase_change_temperature_K")
            a.mapping += ["Property calculation (utility, no verdict): phase change at constant T, Δh = L, Δs = L/T."]
            a.cases["property_change"] = case
            return True
        t1, t2 = temps[0], temps[1]
        self.record(t1, "Initial temperature T1", field_name="T1_K")
        self.record(t2, "Final temperature T2", field_name="T2_K")
        case.update(T1_K=self.q[t1].value, T2_K=self.q[t2].value)
        linear = next((st.cp_linear for st in self.streams.values() if st.cp_linear), None)
        cps = [i for i, q in enumerate(self.q) if q.kind == "cp"
               and not re.search(r"entropy", self.wide_left(i, 40), re.I)]
        if linear:
            case.update(cp_model="linear", a=linear[0], b=linear[1])
        elif cps:
            case.update(cp_model="constant", cp_kj_kg_k=self.q[cps[0]].value)
            self.record(cps[0], "Heat capacity Cp", field_name="cp_kj_kg_k")
        else:
            subst = next((st.substance for st in self.streams.values() if st.substance in CP_DEFAULTS), None)
            if subst is None:
                a.missing.append("Heat capacity Cp of the material (kJ/kg K).")
                return True
            case.update(cp_model="constant", cp_kj_kg_k=CP_DEFAULTS[subst][0])
            a.assumptions.append(f"Cp of {CP_DEFAULTS[subst][1]} not given; {CP_DEFAULTS[subst][0]} kJ/(kg K) used.")
        a.mapping += ["Property calculation (utility, no verdict): Δh = ∫Cp dT, Δs = ∫Cp/T dT, T_eff = |Δh|/|Δs|"
                      + (", totals = mass × specific values." if masses else "."),
                      "No device is assessed, so no feasibility verdict is given."]
        a.cases["property_change"] = case
        return True

    def try_exchanger_duties(self) -> bool:
        """Model 6 from heat duties alone ('hot loses 50 kW, cold gains 50 kW'): First Law with W_shaft = 0."""
        if not self.has(EXCHANGER_RE) or self.of_kind("temperature"):
            return False
        powers = self.of_kind("power")
        hot = [i for i in powers if re.search(r"\bhot\w*[^,;.]{0,25}(?:los\w*|releas\w*|gives?|rejects?)\s*$|"
                                              r"(?:los\w*|releas\w*)\s*$", self.wide_left(i, 40), re.I)]
        cold = [i for i in powers if re.search(r"\bcold\w*[^,;.]{0,25}(?:gains?|absorb\w*|receiv\w*|picks?\s+up)\s*$|"
                                               r"(?:gains?|absorb\w*)\s*$", self.wide_left(i, 40), re.I)]
        if not hot or not cold:
            return False
        a = self.a
        a.model, a.title = "finite_stream_heat_recovery", "Heat exchanger (no shaft work) — duties only"
        self.record(hot[0], "Heat released by the hot stream", field_name="hot_duty_kw")
        self.record(cold[0], "Heat absorbed by the cold stream", field_name="cold_duty_kw")
        a.mapping += ["Device: heat exchanger with no shaft work (W = 0), insulated.",
                      "Test: Q̇_released = Q̇_absorbed. S_gen needs the stream temperatures and flows."]
        a.cases["finite_stream_heat_recovery"] = {"model": "finite_stream_heat_recovery", "title": a.title,
            "description": self.text, "hot_duty_kw": self.q[hot[0]].value, "cold_duty_kw": self.q[cold[0]].value}
        return True

    def latent_reservoir(self) -> Optional[Any]:
        """Sink defined by phase-change enthalpy and entropy: T = Δh/Δs."""
        dh = [i for i, q in enumerate(self.q) if q.kind == "specific_energy"
              and re.search(r"latent|melt|fusion|vapori|phase|evapor", self.wide_left(i, 80), re.IGNORECASE)]
        ds = [i for i, q in enumerate(self.q) if q.kind == "cp"
              and re.search(r"entropy", self.wide_left(i, 60), re.IGNORECASE) and q.owner is None]
        if len(dh) == 1 and len(ds) == 1:
            t = thermo.phase_change_temperature(self.q[dh[0]].value, self.q[ds[0]].value)
            self.record(dh[0], "Phase-change enthalpy Δh", field_name="phase_delta_h_kj_kg")
            self.record(ds[0], "Phase-change entropy Δs", field_name="phase_delta_s_kj_kg_k")
            self.a.mapping.append(f"Phase-change sink: T = Δh/Δs = {self.q[dh[0]].value:g}/{self.q[ds[0]].value:g} "
                                  f"= {t:.2f} K (isothermal).")
            return ("latent", t)
        return None

    def _heat_side(self, i: int) -> Optional[str]:
        """Source or sink from the heat flow named just before a temperature in the same sentence:
        'receives 500 kJ from a 300 K reservoir' -> source, 'rejects 300 kJ to a 600 K reservoir' -> sink."""
        q = self.q[i]
        for j in range(i - 1, -1, -1):
            p = self.q[j]
            if p.sentence != q.sentence or p.kind == "temperature":
                return None
            if p.kind in ("power", "energy") and p.role in ("heat_in", "heat_out"):
                return "hot" if p.role == "heat_in" else "cold"
        return None

    def classify_reservoirs(self, idx: List[int]) -> Tuple[List[Any], List[Any]]:
        hot, cold = [], []
        unlabeled = []
        for i in idx:
            q = self.q[i]
            side = self._heat_side(i)
            if side and q.role not in ("hot", "cold"):
                q.role = side          # the stated direction of heat flow, not the size of the number, decides
            if q.pair == "between" and q.partner is not None:
                unlabeled.append(i)
                continue
            if q.role == "hot":
                hot.append(i)
            elif q.role == "cold":
                cold.append(i)
            else:
                unlabeled.append(i)
        if (len(hot) >= 2 and not cold) or (len(cold) >= 2 and not hot):
            both = sorted(hot + cold, key=lambda i: self.q[i].value, reverse=True)
            hot, cold = [both[0]], [both[-1]]
            self.a.assumptions.append("The source/sink wording was ambiguous: the higher temperature is taken as the "
                                      "source and the lower as the sink.")
        if unlabeled:
            if len(unlabeled) == 2 and not hot and not cold:
                hi, lo = sorted(unlabeled, key=lambda i: self.q[i].value, reverse=True)
                hot.append(hi)
                cold.append(lo)
                self.a.assumptions.append("Two reservoir temperatures were given without labels: the higher is "
                                          "taken as the source and the lower as the sink.")
            elif len(unlabeled) == 1:
                i = unlabeled[0]
                if hot and not cold:
                    cold.append(i)
                elif cold and not hot:
                    hot.append(i)
                else:
                    hot.append(i)
            else:
                for i in unlabeled:
                    (hot if not hot else cold).append(i)
        return hot, cold

    def stream_state(self, s: Stream) -> Dict[str, Any]:
        temps = [i for i in s.quantities if self.q[i].kind == "temperature"]
        tin = tout = None
        for i in temps:
            if self.q[i].role == "in" and tin is None:
                tin = i
            elif self.q[i].role == "out" and tout is None:
                tout = i
        rest = [i for i in temps if i not in (tin, tout)]
        for i in rest:
            if tin is None:
                tin = i
            elif tout is None:
                tout = i
        side = s.side
        if tin is not None and tout is not None:
            side = "hot" if self.q[tin].value > self.q[tout].value else "cold"
            if s.side and s.side != side:
                self.a.issues.append(f"The {s.key} is called {s.side} but its temperatures show it "
                                     f"{'cooling' if side == 'hot' else 'warming'}; it is treated as the {side} side.")
        if side is None:
            ctx = self.text[max(0, s.first_pos - 10):s.first_pos + 160].lower()
            side = "hot" if re.search(r"cool(?:s|ed|ing)?\b|releas|gives\s+up", ctx) else \
                "cold" if re.search(r"heat(?:s|ed)\b|warm|absorb", ctx) else None
        flows = [i for i in s.quantities if self.q[i].kind in ("mass_flow", "vol_flow")]
        cps = [i for i in s.quantities if self.q[i].kind == "cp"]
        mflow = None
        if flows:
            q = self.q[flows[0]]
            if q.kind == "mass_flow":
                mflow = q.value
            else:
                rho = 1025.0 if s.substance == "seawater" else WATER_DENSITY_KG_M3 if s.substance == "water" else None
                if rho is not None:
                    mflow = q.value * rho
                    self.a.assumptions.append(f"Density of {s.substance} taken as {rho:g} kg/m³ to convert "
                                              f"{q.quote} to {mflow:g} kg/s.")
                else:
                    self.a.issues.append(f"Volumetric flow {q.quote} given for {s.key}; give its density or mass flow.")
        cp = None
        cp_assumed = False
        if cps:
            cp = ("constant", self.q[cps[0]].value)
        elif s.cp_linear:
            cp = ("linear", s.cp_linear[0], s.cp_linear[1])
        elif len(shared := [i for i, q in enumerate(self.q) if q.kind == "cp" and q.owner is None]) == 1:
            # one Cp stated for all streams, e.g. "both streams have a specific heat of 4.18 kJ/kg K"
            cps = shared
            cp = ("constant", self.q[shared[0]].value)
        elif s.substance in CP_DEFAULTS:
            val, name = CP_DEFAULTS[s.substance]
            cp = ("constant", val)
            cp_assumed = True
            self.a.assumptions.append(f"Cp of {name} not given; typical constant value {val:g} kJ/(kg K) used for the {s.key}.")
        return {"key": s.key, "name": s.key, "side": side, "tin": tin, "tout": tout, "flow": flows[0] if flows else None,
                "m": mflow, "cp": cp, "cp_index": cps[0] if cps else None, "substance": s.substance,
                "cp_assumed": cp_assumed}

    def _dh_ds(self, cp, t1, t2) -> Tuple[float, float]:
        if cp[0] == "constant":
            return thermo.properties_constant_cp(t1, t2, cp[1])
        return thermo.properties_linear_cp(t1, t2, cp[1], cp[2])

    def _solve_outlet(self, cp, t_in, dh_target) -> float:
        if cp[0] == "constant":
            return t_in + dh_target / cp[1]
        a_, b_ = cp[1], cp[2]
        lo, hi = (1.0, t_in) if dh_target < 0 else (t_in, t_in + 5000.0)
        return thermo.solve_temperature_bisection(dh_target, t_in, lo, hi, lambda T: a_ + b_ * T)

    def _stream_case(self, st) -> Dict[str, Any]:
        """Engine stream; unknown values are left out (the engine derives or reports them)."""
        out = {"name": st["name"], "mass_flow_kg_s": st["m"], "T_in_K": st["Tin"], "T_out_K": st["Tout"]}
        out = {k: v for k, v in out.items() if v is not None}
        if st["cp"] is None:
            out["cp_model"] = "constant"
        elif st["cp"][0] == "constant":
            out.update(cp_model="constant", cp_kj_kg_k=st["cp"][1])
        else:
            out.update(cp_model="linear", a=st["cp"][1], b=st["cp"][2])
        return out

    def _record_stream(self, st, label) -> None:
        for key, name, field_ in (("tin", "inlet temperature", "T_in_K"), ("tout", "outlet temperature", "T_out_K"),
                                  ("flow", "flow rate", "mass_flow_kg_s"), ("cp_index", "Cp", "cp_kj_kg_k")):
            if st.get(key) is not None:
                self.record(st[key], f"{label} {name}", field_name=f"{st['side']}_stream.{field_}")

    def thermal_two_streams(self, hot, cold) -> None:
        a = self.a
        a.model = "finite_stream_engine"
        a.title = "Finite hot stream → device → finite cold stream"
        w, wi, eff, _ = self.claim()
        exchanger = w is None and eff is None and self.has(EXCHANGER_RE)
        if exchanger:
            a.model = "finite_stream_heat_recovery"
            a.title = "Insulated heat exchanger (no shaft work)"
            w = 0.0
        for st in (hot, cold):
            st["Tin"] = self.q[st["tin"]].value if st["tin"] is not None else None
            st["Tout"] = self.q[st["tout"]].value if st["tout"] is not None else None
            self._record_stream(st, f"{st['key'].capitalize()}")
            if st["cp"] is None and not exchanger:
                a.missing.append(f"Heat capacity Cp of the {st['key']}.")
        if wi is not None:
            self.record(wi, "Claimed work/power output", field_name="W_out_kw")
        if a.missing:
            return
        unknowns = [(st, f) for st in (hot, cold) for f in ("Tin", "Tout", "m") if st[f] is None]
        if exchanger:                 # the engine derives one missing outlet, or checks what the data allow
            return self._exchanger_case(hot, cold)
        if unknowns == [(cold, "Tout")] and eff is None:
            # The engine fixes the cold outlet from the First Law when a claim is given; with no claim it
            # reports the exact reversible limit (the "maximum work" question).
            case = {"model": "finite_stream_engine", "title": a.title, "description": self.text,
                    "hot_stream": self._stream_case(hot), "cold_stream": self._stream_case(cold),
                    "apply_strict_reversible_limit": True}
            if w is not None:
                case["W_out_kw"] = w
                case["apply_effective_temperature_limit"] = False
            a.mapping += [f"SOURCE: {hot['key']} cooling {hot['Tin']:.2f} → {hot['Tout']:.2f} K.",
                          f"SINK: {cold['key']} entering at {cold['Tin']:.2f} K (outlet not stated).",
                          "Boundary: both streams + device, steady and adiabatic to the surroundings.",
                          ("The cold outlet follows from the First Law (Q̇_cold = Q̇_hot − Ẇ); then S_gen ≥ 0 and "
                           "W ≤ W_max are checked." if w is not None else
                           "No claim: the engine calculates the maximum reversible work W_max (cold outlet chosen so that S_gen = 0).")]
            a.cases["finite_stream_engine"] = case
            return
        if w is None and eff is None:
            pass  # engine will infer work; verdict rests on S_gen only
        if eff is not None and w is None and hot["Tin"] and hot["Tout"] and hot["m"]:
            q_h = -hot["m"] * self._dh_ds(hot["cp"], hot["Tin"], hot["Tout"])[0]
            w = eff * q_h
            a.inferred.append(f"Claimed work = efficiency × heat released by hot stream = {eff:.4g} × {q_h:.4g} kW = {w:.4g} kW.")
        if len(unknowns) > 1 or (unknowns and w is None):
            for st, f in unknowns:
                a.missing.append({"Tin": f"Inlet temperature of the {st['key']}.", "Tout": f"Outlet temperature of the {st['key']}.",
                                  "m": f"Mass flow rate of the {st['key']}."}[f])
            if w is None and unknowns:
                a.missing.append("The claimed work or power output.")
            return
        if unknowns:
            st, f = unknowns[0]
            other = cold if st is hot else hot
            od = other["m"] * self._dh_ds(other["cp"], other["Tin"], other["Tout"])[0]  # signed
            # energy balance: -dH_hot = dH_cold + W
            if st is hot:
                need = -(od + w)                     # signed ΔḢ of hot stream
            else:
                need = -od - w                       # signed ΔḢ of cold stream
            if f == "m":
                dh = self._dh_ds(st["cp"], st["Tin"], st["Tout"])[0]
                st["m"] = need / dh
                if st["m"] <= 0:
                    raise thermo.ThermoInputError("The stated temperatures and claimed work leave no positive flow "
                                                  f"for the {st['key']}: the claim exceeds the heat available.")
                a.inferred.append(f"{st['key'].capitalize()} mass flow from the First Law: {st['m']:.5g} kg/s.")
            elif f == "Tout":
                st["Tout"] = self._solve_outlet(st["cp"], st["Tin"], need / st["m"])
                a.inferred.append(f"{st['key'].capitalize()} outlet temperature from the First Law "
                                  f"(Q̇_hot = Ẇ + Q̇_cold): {st['Tout']:.5g} K ({st['Tout'] - 273.15:.4g} °C).")
            else:
                raise thermo.ThermoInputError(f"Give the inlet temperature of the {st['key']}.")
            if unknowns:
                a.assumptions.append("One missing stream quantity was calculated from the energy balance, so the "
                                     "First Law closes by construction; the verdict rests on the Second Law.")
        case = {"model": "finite_stream_engine", "title": a.title, "description": self.text,
                "hot_stream": self._stream_case(hot), "cold_stream": self._stream_case(cold),
                "apply_effective_temperature_limit": True, "apply_strict_reversible_limit": True}
        if w is not None:
            case["W_out_kw"] = w
        a.mapping += [f"SOURCE: {hot['key']} cooling {hot['Tin']:.2f} → {hot['Tout']:.2f} K.",
                      f"SINK: {cold['key']} warming {cold['Tin']:.2f} → {cold['Tout']:.2f} K.",
                      "Boundary: both streams + device, steady and adiabatic to the surroundings.",
                      "Tests: First Law, S_gen = ṁ_hΔs_h + ṁ_cΔs_c ≥ 0, and the exact reversible limit W_max = Q̇_hot − "
                      "Q̇_cold,rev (cold outlet chosen so that S_gen = 0). The T_eff ratio is shown for information only."]
        if w is None:
            a.issues.append("No claimed output was found; work is inferred from the stream energy balance and only "
                            "the Second Law can be tested.")
        a.cases["finite_stream_engine"] = case

    def _exchanger_case(self, hot, cold) -> None:
        """Two streams exchanging heat with no work: energy balance, S_gen and exergy destruction."""
        a = self.a
        t0 = None
        for i, q in enumerate(self.q):
            if q.kind == "temperature" and q.owner is None and \
                    re.search(r"surroundings|ambient|environment|atmosphere|dead\s+state|T0|T_0",
                              self.wide_left(i, 60), re.IGNORECASE):
                t0 = q.value
                self.record(i, "Environment temperature T0", field_name="T0_K")
                break
        if t0 is None:
            t0 = T_AMBIENT_DEFAULT_K
            a.assumptions.append(f"Environment temperature T0 not given; {T_AMBIENT_DEFAULT_K} K (25 °C) is used "
                                 "only for exergy destruction. It does not affect the verdict.")
        a.assumptions.append(f"Stated data may be rounded: an energy mismatch up to {EXCHANGER_DATA_TOLERANCE:.1%} of the "
                             "heat duty is accepted as rounding. The exact mismatch is always reported.")
        complete = all(st[k] is not None for st in (hot, cold) for k in ("m", "Tin", "Tout", "cp"))
        if complete:
            q_hot = -hot["m"] * self._dh_ds(hot["cp"], hot["Tin"], hot["Tout"])[0]
            q_cold = cold["m"] * self._dh_ds(cold["cp"], cold["Tin"], cold["Tout"])[0]
            a.inferred += [f"Heat released by the {hot['key']}: Q̇ = ṁ·Δh = {q_hot:.6g} kW.",
                           f"Heat absorbed by the {cold['key']}: Q̇ = ṁ·Δh = {q_cold:.6g} kW.",
                           f"Energy mismatch = {q_hot - q_cold:.4g} kW ({100 * abs(q_hot - q_cold) / max(q_hot, q_cold):.4g} %)."]
        fmt = lambda st: " → ".join(f"{st[k]:.2f} K" if st[k] is not None else "?" for k in ("Tin", "Tout"))
        a.mapping += [f"SOURCE: {hot['key']} cooling {fmt(hot)}.",
                      f"SINK: {cold['key']} warming {fmt(cold)}.",
                      "Device: insulated heat exchanger, no shaft work (W = 0). Boundary encloses both streams.",
                      "Tests: Q̇_released = Q̇_absorbed, S_gen = ṁ_hΔs_h + ṁ_cΔs_c ≥ 0, no temperature cross; "
                      "exergy destroyed = T0·S_gen."]
        a.cases["finite_stream_heat_recovery"] = {
            "model": "finite_stream_heat_recovery", "title": a.title, "description": self.text,
            "hot_stream": self._stream_case(hot), "cold_stream": self._stream_case(cold),
            "T0_K": t0, "energy_relative_tolerance": EXCHANGER_DATA_TOLERANCE,
            "arrangement": "parallel" if self.has(r"\bparallel[- ]flow\b|\bco-?current\b") else "counterflow"}

    def thermal_stream_reservoir(self, st, hot_res, cold_res, side) -> None:
        """Finite stream on one side, isothermal reservoir on the other → T_eff mapping."""
        a = self.a
        st["Tin"] = self.q[st["tin"]].value if st["tin"] is not None else None
        st["Tout"] = self.q[st["tout"]].value if st["tout"] is not None else None
        self._record_stream(st, st["key"].capitalize())
        w, wi, eff, _ = self.claim()
        if wi is not None:
            self.record(wi, "Claimed work/power output", field_name="W_out_kw")
        if st["cp"] is None:
            a.missing.append(f"Heat capacity Cp of the {st['key']}.")
        # reservoir temperature on the other side
        res_list = cold_res if side == "hot" else hot_res
        t_res = None
        if res_list:
            r = res_list[0]
            if isinstance(r, tuple):
                t_res = r[1]
            else:
                t_res = self.q[r].value
                self.record(r, "Sink temperature" if side == "hot" else "Source temperature",
                            field_name="T_cold_K" if side == "hot" else "T_hot_K")
        elif side == "hot":
            t_res = T_AMBIENT_DEFAULT_K
            a.assumptions.append("No sink temperature given: heat is assumed rejected to the surroundings at "
                                 f"{T_AMBIENT_DEFAULT_K} K (25 °C).")
        else:
            a.missing.append("Temperature of the heat source.")
        if st["Tin"] is None:
            a.missing.append(f"Inlet temperature of the {st['key']}.")
        if st["Tout"] is None and side == "hot" and t_res is not None and st["Tin"] is not None:
            st["Tout"] = t_res
            a.assumptions.append(f"Outlet of the {st['key']} not given: it is taken as cooled to the sink temperature "
                                 f"({t_res:.2f} K), the maximum heat it can supply. This gives an upper bound.")
        q_heat = None
        if st["m"] is None:
            powers = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role in ("heat_in", "heat_out")]
            if powers and st["Tin"] and st["Tout"]:
                q_heat = self.q[powers[0]].value
                self.record(powers[0], "Heat exchanged by the stream", field_name="Q_kw")
            elif eff is not None and w is None:
                st["m"] = 1.0
                a.assumptions.append("No flow rate given; a 1 kg/s basis is used because only an efficiency is claimed.")
            else:
                a.missing.append(f"Mass flow rate of the {st['key']}.")
        if w is None and eff is None:
            a.missing.append("The claimed work/power output or efficiency.")
        if st["Tout"] is None:
            if side == "cold" and st["m"] is not None and w is not None:
                pass  # solved below from the energy balance
            else:
                a.missing.append(f"Outlet temperature of the {st['key']}.")
        if a.missing:
            return

        if side == "hot":
            dh, ds = self._dh_ds(st["cp"], st["Tin"], st["Tout"])
            m = st["m"] if st["m"] is not None else -q_heat / dh
            q_h = -m * dh
            t_eff = thermo.effective_temperature(dh, ds)
            if eff is not None and w is None:
                w = eff * q_h
                a.inferred.append(f"Claimed work = {eff:.4g} × Q̇_H = {w:.5g} kW.")
            a.inferred.append(f"Hot stream: Δh = {dh:.5g} kJ/kg, Δs = {ds:.5g} kJ/(kg K), Q̇_H = ṁ|Δh| = {q_h:.5g} kW.")
            a.inferred.append(f"Effective source temperature T_H,eff = |Δh|/|Δs| = {t_eff:.5g} K.")
            t_hot, t_cold = t_eff, t_res
            q_c_checked = None
            a.mapping += [f"SOURCE: {st['key']} cooling {st['Tin']:.2f} → {st['Tout']:.2f} K, represented by "
                          f"T_H,eff = {t_eff:.2f} K.",
                          f"SINK: isothermal reservoir at {t_res:.2f} K."]
        else:
            # hot reservoir supplies Q_H; cold stream absorbs Q_C = Q_H - W
            heat_in = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role == "heat_in"]
            t_hot = t_res
            if st["Tout"] is None:
                if not heat_in:
                    a.missing.append("Heat input rate from the source (or the sink outlet temperature).")
                    return
                q_h = self.q[heat_in[0]].value
                self.record(heat_in[0], "Heat input", field_name="Q_hot_kw")
                if eff is not None and w is None:
                    w = eff * q_h
                st["Tout"] = self._solve_outlet(st["cp"], st["Tin"], (q_h - w) / st["m"])
                a.inferred.append(f"Sink outlet temperature from the First Law Q̇_C = Q̇_H − Ẇ: {st['Tout']:.5g} K.")
            dh, ds = self._dh_ds(st["cp"], st["Tin"], st["Tout"])
            q_c = st["m"] * dh
            t_eff = thermo.effective_temperature(dh, ds)
            if heat_in and "Heat input" not in [e["quantity"] for e in a.extracted]:
                # Heat input was stated independently: keep it, so the engine checks the First Law.
                q_h = self.q[heat_in[0]].value
                self.record(heat_in[0], "Heat input", field_name="Q_hot_kw")
                if eff is not None and w is None:
                    w = eff * q_h
                if st.get("cp_assumed"):
                    q_c_checked = None
                    a.inferred.append(f"Stated Q̇_H = {q_h:.5g} kW. The sink stream's Cp was assumed, so its heat "
                                      f"({q_c:.5g} kW) is not used for a strict First Law check; Q̇_C = Q̇_H − Ẇ.")
                else:
                    q_c_checked = q_c
                    a.inferred.append(f"Cold stream absorbs Q̇_C = ṁΔh = {q_c:.5g} kW; stated Q̇_H = {q_h:.5g} kW "
                                      "(the engine checks Q̇_H − Ẇ − Q̇_C = 0).")
            else:
                q_c_checked = None
                q_h = q_c + (w or 0.0)
                if eff is not None and w is None:
                    q_h = q_c / (1 - eff)
                    w = eff * q_h
                a.inferred.append(f"Cold stream: Q̇_C = ṁΔh = {q_c:.5g} kW; Q̇_H = Q̇_C + Ẇ = {q_h:.5g} kW.")
            a.inferred.append(f"Effective sink temperature T_C,eff = Δh/Δs = {t_eff:.5g} K.")
            t_cold = t_eff
            a.mapping += [f"SOURCE: isothermal reservoir at {t_res:.2f} K.",
                          f"SINK: {st['key']} warming {st['Tin']:.2f} → {st['Tout']:.2f} K, represented by "
                          f"T_C,eff = {t_eff:.2f} K."]
        a.mapping.append("For a finite stream exchanging heat with a reservoir, η_rev = 1 − T_C/T_H with the "
                         "effective temperature is exact: Ẇ_max = ΔḢ − T_res ΔṠ.")
        self._fixed_engine(t_hot, t_cold, q_h, w, q_c_checked,
                           "Finite stream ↔ reservoir engine (effective-temperature mapping)")

    def thermal_reservoirs(self, hot_res, cold_res) -> None:
        a = self.a
        heat_in = [i for i, q in enumerate(self.q) if q.kind in ("power", "energy") and q.role == "heat_in"]
        heat_out = [i for i, q in enumerate(self.q) if q.kind in ("power", "energy") and q.role == "heat_out"]
        w, wi, eff, _ = self.claim()
        if a.issues and w is None and eff is None:
            a.missing.append("A single claimed work/power output.")
            return
        t_hot = t_cold = None
        if hot_res:
            r = hot_res[0]
            t_hot = self.q[r].value
            self.record(r, "Source (hot) temperature", field_name="T_hot_K")
        if cold_res:
            r = cold_res[0]
            if isinstance(r, tuple):
                t_cold = r[1]
            else:
                t_cold = self.q[r].value
                self.record(r, "Sink (cold) temperature", field_name="T_cold_K")
        if len(hot_res) > 1 or len(cold_res) > 1:
            a.issues.append("More than one source or sink temperature was found; the first of each is used.")
        q_h = self.q[heat_in[0]].value if heat_in else None
        q_c = self.q[heat_out[0]].value if heat_out else None
        if heat_in:
            self.record(heat_in[0], "Heat input Q_H", field_name="Q_hot_kw")
        if heat_out:
            self.record(heat_out[0], "Rejected heat Q_C", field_name="Q_cold_rejected_kw")
        if wi is not None:
            self.record(wi, "Claimed work output W", field_name="W_out_kw")
        energy_units = {self.q[i].kind for i in heat_in + heat_out + ([wi] if wi is not None else [])}
        if energy_units == {"energy"}:
            a.assumptions.append("Quantities are energies (kJ) rather than rates; they are treated as amounts per "
                                 "cycle. Feasibility depends only on their ratios, so 'kW' in the output reads as 'kJ per cycle'.")
        # unlabeled powers: if only one power and no claim, decide by context
        if w is None and eff is None and q_h is not None and q_c is not None:
            w = q_h - q_c
            a.inferred.append(f"Work output not stated: from the First Law W = Q_H − Q_C = {w:.5g}.")
            q_c = None
            a.assumptions.append("Rejected heat was used to infer work, so the First Law closes by construction.")
        if q_h is None and w is not None and q_c is not None:
            q_h = w + q_c
            a.inferred.append(f"Heat input not stated: Q_H = W + Q_C = {q_h:.5g}.")
            q_c = None
        if eff is not None:
            if q_h is None and w is None:
                q_h = 100.0
                w = eff * q_h
                a.assumptions.append("Only an efficiency is claimed: a 100 kW heat-input basis is used "
                                     f"(W = {w:.4g} kW); the verdict does not depend on this basis.")
            elif q_h is None:
                q_h = w / eff
                a.inferred.append(f"Heat input from the claimed efficiency: Q_H = W/η = {q_h:.5g}.")
            elif w is None:
                w = eff * q_h
                a.inferred.append(f"Claimed work from efficiency: W = η·Q_H = {w:.5g}.")
            elif abs(w / q_h - eff) > 0.01:
                a.issues.append(f"Stated efficiency {eff:.1%} differs from W/Q_H = {w / q_h:.1%}; W and Q_H are used.")
        # single reservoir (Kelvin–Planck) or missing sink
        single = self.has(r"\bsingle\s+(?:heat\s+)?reservoir\b|\bone\s+(?:heat\s+)?reservoir\b|\bonly\s+one\s+reservoir\b|"
                          r"\bentirely\s+into\s+work\b|\bcompletely\s+into\s+work\b|\bwithout\s+rejecting\b|"
                          r"\bno\s+heat\s+(?:is\s+)?rejected\b|\brejects\s+no\s+heat\b")
        if single and w is None and eff is None and q_h is not None:
            w = q_h
            a.inferred.append("'Converts it entirely into work' means W = Q_H (100 % conversion).")
        if t_hot is None and t_cold is not None and single:
            t_hot, t_cold = t_cold, None
        if t_hot is not None and t_cold is None and (single or (q_h is not None and w is not None and w >= q_h)):
            return self._kelvin_planck(t_hot, q_h, w)
        if t_hot is not None and t_cold is None:
            if self.has(r"\b(?:surroundings|environment|atmosphere|ambient)\b"):
                t_cold = T_AMBIENT_DEFAULT_K
                a.assumptions.append(f"Sink temperature not given; surroundings taken as {T_AMBIENT_DEFAULT_K} K (25 °C).")
            else:
                a.missing.append("Temperature of the sink (where heat is rejected).")
        if t_hot is None:
            a.missing.append("Temperature of the heat source.")
        if q_h is None:
            a.missing.append("Heat input rate Q_H (or the efficiency claimed).")
        if w is None:
            a.missing.append("The claimed work/power output (or efficiency).")
        if a.missing:
            return
        a.mapping += [f"SOURCE: isothermal reservoir at {t_hot:.2f} K supplying Q_H = {q_h:.5g}.",
                      f"SINK: isothermal reservoir at {t_cold:.2f} K.",
                      "Device: cyclic heat engine. Tests: Carnot limit η ≤ 1 − T_C/T_H, First Law, "
                      "S_gen = Q_C/T_C − Q_H/T_H ≥ 0."]
        if t_hot <= t_cold and w > 0:
            return self._cycle_entropy(t_hot, t_cold, q_h, w, q_c)
        self._fixed_engine(t_hot, t_cold, q_h, w, q_c, "Cyclic heat engine between two reservoirs")

    def _fixed_engine(self, t_hot, t_cold, q_h, w, q_c, title) -> None:
        a = self.a
        if t_hot <= t_cold and w and w > 0:
            return self._cycle_entropy(t_hot, t_cold, q_h, w, q_c)
        a.model = "fixed_reservoir_engine"
        a.title = title
        case = {"model": "fixed_reservoir_engine", "title": title, "description": self.text,
                "T_hot_K": t_hot, "T_cold_K": t_cold, "Q_hot_kw": q_h, "W_out_kw": w}
        if q_c is not None:
            case["Q_cold_rejected_kw"] = q_c
        a.cases["fixed_reservoir_engine"] = case

    def _kelvin_planck(self, t_hot, q_h, w) -> None:
        a = self.a
        if q_h is None and w is not None:
            q_h = w
            a.inferred.append("Heat drawn from the single reservoir equals the work produced (Q = W).")
        if q_h is None or w is None:
            a.missing.append("Heat drawn and work produced.")
            return
        a.model = "closed_system"
        a.title = "Cyclic device exchanging heat with a single reservoir (Kelvin–Planck test)"
        a.mapping += [f"SOURCE: single reservoir at {t_hot:.2f} K supplying Q = {q_h:.5g}.",
                      "SINK: none — no heat is rejected to a colder body.",
                      "Over one cycle ΔE = 0 and ΔS = 0, so S_gen = −Q/T_H and the First Law requires W = Q."]
        a.cases["closed_system"] = {
            "model": "closed_system", "title": a.title, "description": self.text,
            "delta_E_kj": 0.0, "delta_S_system_kj_K": 0.0, "W_out_kj": w,
            "heat": [{"heat_into_system_kj": q_h, "boundary_temperature_K": t_hot}]}

    def _cycle_entropy(self, t_hot, t_cold, q_h, w, q_c) -> None:
        a = self.a
        if q_c is None:
            q_c = q_h - w
        a.model = "closed_system"
        a.title = "Cyclic device: heat drawn at a temperature not above the rejection temperature"
        a.issues.append("The stated source is not hotter than the sink, so a Carnot efficiency cannot be defined; "
                        "the cycle entropy balance is used instead.")
        a.cases["closed_system"] = {
            "model": "closed_system", "title": a.title, "description": self.text,
            "delta_E_kj": 0.0, "delta_S_system_kj_K": 0.0, "W_out_kj": w,
            "heat": [{"heat_into_system_kj": q_h, "boundary_temperature_K": t_hot},
                     {"heat_into_system_kj": -q_c, "boundary_temperature_K": t_cold}]}

    # ---------------------------------------------------------------- mechanical
    def route_mechanical(self) -> None:
        a = self.a
        a.model = "mechanical_power"
        a.title = "Mechanical-to-electrical converter"
        case: Dict[str, Any] = {"model": "mechanical_power", "title": a.title, "description": self.text}
        ev = self.of_kind("event_energy")
        rate = self.of_kind("event_rate")
        energies = [i for i in self.of_kind("energy")
                    if re.search(r"\bper\s+(?:foot)?step|\beach\s+step|\bevery\s+step|\bper\s+stride", self.ctx(i)[1][:40], re.I)]
        ev = ev or energies
        mech = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role == "mech_in"]
        heat = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role == "heat_out"]
        w, wi, eff, _ = self.claim()
        if w is None:
            outs = [i for i, q in enumerate(self.q) if q.kind == "power" and q.role not in ("mech_in", "heat_out", "heat_in")]
            if len(outs) == 1:
                w, wi = self.q[outs[0]].value, outs[0]
        forces, torques, omegas = self.of_kind("force"), self.of_kind("torque"), self.of_kind("angular_velocity")
        displacements = [i for i in self.of_kind("length") if re.search(r"displac|stroke|compress|step|press|distance",
                                                                         self.wide_left(i, 40) + self.q[i].quote, re.I)]
        if torques and omegas:                           # P = torque x angular velocity
            case.update(torque_Nm=self.q[torques[0]].value, angular_velocity_rad_s=self.q[omegas[0]].value)
            self.record(torques[0], "Torque", field_name="torque_Nm")
            self.record(omegas[0], "Angular velocity", field_name="angular_velocity_rad_s")
            src = f"P = τω = {self.q[torques[0]].value:g} N·m × {self.q[omegas[0]].value:.4g} rad/s"
            a.inferred.append(f"Mechanical input P = τ·ω = {self.q[torques[0]].value * self.q[omegas[0]].value:.5g} W.")
        elif forces and displacements and rate:          # energy per event = force x displacement
            case.update(force_N=self.q[forces[0]].value, displacement_m=self.q[displacements[0]].value,
                        events_per_s=self.q[rate[0]].value)
            self.record(forces[0], "Average force", field_name="force_N")
            self.record(displacements[0], "Displacement per step", field_name="displacement_m")
            self.record(rate[0], "Step rate", field_name="events_per_s")
            e = self.q[forces[0]].value * self.q[displacements[0]].value
            src = f"F·d = {e:.4g} J/step × {self.q[rate[0]].value:.4g} steps/s"
            a.inferred.append(f"Work per step W = F·d = {e:.5g} J; mechanical input = {e * self.q[rate[0]].value:.5g} W.")
        elif forces and self.of_kind("velocity"):        # P = force x velocity
            v = self.of_kind("velocity")[0]
            case.update(force_N=self.q[forces[0]].value, velocity_m_s=self.q[v].value)
            self.record(forces[0], "Force", field_name="force_N")
            self.record(v, "Velocity", field_name="velocity_m_s")
            src = "P = F·v"
            a.inferred.append(f"Mechanical input P = F·v = {self.q[forces[0]].value * self.q[v].value:.5g} W.")
        elif ev and rate:
            case["recoverable_energy_per_event_j"] = self.q[ev[0]].value if self.q[ev[0]].kind == "event_energy" \
                else self.q[ev[0]].value * 1000.0
            case["events_per_s"] = self.q[rate[0]].value
            self.record(ev[0], "Recoverable energy per step", case["recoverable_energy_per_event_j"], "J",
                        "recoverable_energy_per_event_j")
            self.record(rate[0], "Step rate", field_name="events_per_s")
            src = (f"{case['recoverable_energy_per_event_j']:g} J/step × {case['events_per_s']:.4g} steps/s = "
                   f"{case['recoverable_energy_per_event_j'] * case['events_per_s']:.4g} W")
        elif mech:
            case["recoverable_mechanical_power_kw"] = self.q[mech[0]].value
            self.record(mech[0], "Mechanical input power", field_name="recoverable_mechanical_power_kw")
            src = f"{self.q[mech[0]].quote} of mechanical power"
        else:
            src = None
            if ev and not rate:
                a.missing.append("How often the energy is recovered (e.g. steps per minute).")
            elif rate and not ev:
                a.missing.append("Recoverable mechanical energy per step (J).")
            else:
                a.missing.append("Recoverable mechanical input: power (W) or energy per step (J) and step rate.")
        if w is None and eff is not None and src is not None and ("recoverable_mechanical_power_kw" in case
                                                                  or "recoverable_energy_per_event_j" in case):
            p_in = case.get("recoverable_mechanical_power_kw") or \
                case["recoverable_energy_per_event_j"] * case["events_per_s"] / 1000
            w = eff * p_in
            a.inferred.append(f"Claimed output = {eff:.3g} × {p_in * 1000:.4g} W = {w * 1000:.4g} W.")
        if w is None:
            a.missing.append("The claimed electrical output (W).")
        else:
            case["W_out_kw"] = w
            if wi is not None:
                self.record(wi, "Claimed electrical output", field_name="W_out_kw")
        if heat:
            case["heat_rejected_kw"] = self.q[heat[0]].value
            self.record(heat[0], "Measured heat loss", field_name="heat_rejected_kw")
        temps = self.of_kind("temperature")
        if temps:
            case["heat_rejection_temperature_K"] = self.q[temps[0]].value
            self.record(temps[0], "Heat-rejection temperature", field_name="heat_rejection_temperature_K")
        if a.missing:
            return
        a.mapping += [f"SOURCE: recoverable mechanical work, {src}.",
                      "SINK: losses leave as heat (no Carnot temperature is invented for a mechanical source).",
                      "Limit: electrical output ≤ mechanical input (100 % ideal conversion)."]
        a.cases["mechanical_power"] = case

    # ---------------------------------------------------------------- wind
    def route_wind(self) -> None:
        a = self.a
        vel = self.of_kind("velocity")
        lengths = self.of_kind("length")
        areas = self.of_kind("area")
        dens = self.of_kind("density")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        if not vel:
            a.missing.append("Wind speed (m/s).")
        area = None
        if areas:
            area = self.q[areas[0]].value
            self.record(areas[0], "Swept area", field_name="swept_area_m2")
        elif lengths:
            i = lengths[0]
            left = self.wide_left(i, 50).lower()
            d = self.q[i].value
            if re.search(r"radius|blade\s+length|blades?\s+(?:of|are|is)|long\s+blades?", left + self.ctx(i)[1][:20].lower()):
                area = math.pi * d ** 2
                self.record(i, "Rotor radius / blade length", field_name="rotor_radius_m")
                a.inferred.append(f"Swept area A = πR² = {area:.5g} m².")
            else:
                area = math.pi * d ** 2 / 4
                self.record(i, "Rotor diameter", field_name="rotor_diameter_m")
                a.inferred.append(f"Swept area A = πD²/4 = {area:.5g} m².")
        else:
            a.missing.append("Rotor diameter, blade length or swept area.")
        rho = AIR_DENSITY_KG_M3
        if dens:
            rho = self.q[dens[0]].value
            self.record(dens[0], "Air density", field_name="air_density_kg_m3")
        else:
            a.assumptions.append(f"Air density not given; standard {AIR_DENSITY_KG_M3} kg/m³ used.")
        if a.missing:
            if w is None and eff is None:
                a.missing.append("The claimed power output.")
            return
        v = self.q[vel[0]].value
        self.record(vel[0], "Wind speed", field_name="wind_speed_m_s")
        p_wind, p_betz = thermo.wind_power_kw(rho, area, v)
        if w is None and eff is not None:
            w = eff * p_wind
            a.inferred.append(f"Claimed output = {eff:.3g} × kinetic power {p_wind:.5g} kW = {w:.5g} kW "
                              "(efficiency taken relative to the wind's kinetic power).")
        if w is None:
            a.missing.append("The claimed power output.")
            return
        if wi is not None:
            self.record(wi, "Claimed power output", field_name="W_out_kw")
        a.inferred += [f"Kinetic power of the wind ½ρAv³ = {p_wind:.5g} kW.",
                       f"Betz limit (16/27 of it) = {p_betz:.5g} kW — the maximum extractable mechanical power."]
        a.mapping += ["SOURCE: kinetic energy of the air stream through the rotor area.",
                      "SINK: slowed air leaving the rotor. No Carnot temperature applies.",
                      "Limit: Betz, P_max = (16/27)·½ρAv³ (calculated by the engine from ρ, A and v)."]
        a.model = "mechanical_power"
        a.title = "Wind turbine (Betz limit)"
        a.cases["mechanical_power"] = {"model": "mechanical_power", "title": a.title, "description": self.text,
                                       "air_density_kg_m3": rho, "swept_area_m2": area, "wind_speed_m_s": v, "W_out_kw": w}

    # ---------------------------------------------------------------- hydro
    def route_hydro(self) -> None:
        a = self.a
        lengths = self.of_kind("length")
        flows = self.of_kind("vol_flow", "mass_flow")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        head = None
        for i in lengths:
            head = self.q[i].value
            self.record(i, "Head (height of fall)", field_name="head_m")
            break
        if head is None:
            a.missing.append("Head / height the water falls (m).")
        if not flows:
            a.missing.append("Water flow rate (m³/s, L/s or kg/s).")
        if a.missing:
            if w is None and eff is None:
                a.missing.append("The claimed power output.")
            return
        f = self.q[flows[0]]
        dens = self.of_kind("density")
        rho = self.q[dens[0]].value if dens else WATER_DENSITY_KG_M3
        mdot = f.value * rho if f.kind == "vol_flow" else f.value
        self.record(flows[0], "Water flow rate", field_name="flow")
        if f.kind == "vol_flow" and dens:
            self.record(dens[0], "Water density", field_name="water_density_kg_m3")
        elif f.kind == "vol_flow":
            a.assumptions.append(f"Water density not given; {WATER_DENSITY_KG_M3:g} kg/m³ used.")
        p_max = thermo.hydraulic_power_kw(mdot, head, GRAVITY_M_S2)
        if w is None and eff is not None:
            w = eff * p_max
            a.inferred.append(f"Claimed output = {eff:.3g} × {p_max:.5g} kW = {w:.5g} kW.")
        if w is None:
            a.missing.append("The claimed power output.")
            return
        if wi is not None:
            self.record(wi, "Claimed power output", field_name="W_out_kw")
        a.inferred.append(f"Ideal hydraulic power ṁgH = {mdot:.5g} × {GRAVITY_M_S2} × {head:.5g} = {p_max:.5g} kW.")
        a.assumptions.append(f"g = {GRAVITY_M_S2} m/s²; head is the gross head with no friction losses (ideal limit).")
        a.mapping += ["SOURCE: gravitational potential energy of water falling through the head.",
                      "SINK: water leaving at the lower level. No Carnot temperature applies.",
                      "Limit: P_max = ρgQH (calculated by the engine from the flow and head)."]
        a.model = "mechanical_power"
        a.title = "Hydroelectric generator (ρgQH limit)"
        a.cases["mechanical_power"] = {"model": "mechanical_power", "title": a.title, "description": self.text,
                                       "head_m": head, "water_mass_flow_kg_s": mdot, "g_m_s2": GRAVITY_M_S2, "W_out_kw": w}

    # ---------------------------------------------------------------- solar
    def route_solar(self) -> None:
        a = self.a
        temps = self.of_kind("temperature")
        t_src = t_amb = None
        for i in temps:
            left = self.wide_left(i, 60).lower()
            if re.search(r"\bsun\b|source|surface|black\s*body|radiat", left) and t_src is None and self.q[i].value > 1000:
                t_src = self.q[i].value
                self.record(i, "Radiation source (sun) temperature", field_name="T_source_K")
            elif t_amb is None:
                t_amb = self.q[i].value
                self.record(i, "Ambient temperature", field_name="T_ambient_K")
        if t_src is None:
            t_src = SOLAR_T_SUN_K
            a.assumptions.append(f"Sun treated as a blackbody at {SOLAR_T_SUN_K} K.")
        if t_amb is None:
            t_amb = SOLAR_T_AMBIENT_K
            a.assumptions.append(f"Ambient temperature taken as {SOLAR_T_AMBIENT_K} K.")
        irr = self.of_kind("irradiance")
        areas = self.of_kind("area")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        g = None
        zero = re.search(r"\b(?:irradiance|insolation|sunlight|solar\s+radiation)\s+(?:is|=|of|equals)\s+(?:zero|0|nil|none)\b|"
                         r"\bno\s+(?:sunlight|sun|irradiance|solar\s+radiation)\b|\bzero\s+(?:irradiance|sunlight)\b|"
                         r"\bat\s+night\b|\bin\s+(?:complete\s+)?darkness\b", self.text, re.IGNORECASE)
        if irr:
            g = self.q[irr[0]].value
            self.record(irr[0], "Irradiance", field_name="irradiance_W_m2")
        elif zero:
            g = 0.0
            a.extracted.append({"quantity": "Irradiance", "value": 0.0, "unit": "W/m²", "quote": zero.group(0)})
            a.quotes["irradiance_W_m2"] = zero.group(0)
        area = None
        if areas:
            area = self.q[areas[0]].value
            self.record(areas[0], "Collection area", field_name="collection_area_m2")
        if area is None and w is None and eff is not None:
            area, g = 1.0, g or SOLAR_IRRADIANCE_W_M2
            a.assumptions.append("Only an efficiency is claimed: a 1 m² basis is used; the verdict does not depend on it.")
        if g is None:
            g = SOLAR_IRRADIANCE_W_M2
            a.assumptions.append(f"Irradiance not given; standard {SOLAR_IRRADIANCE_W_M2:g} W/m² used.")
        if area is None:
            a.missing.append("Collection area (m²).")
        if w is None and eff is not None and area is not None:
            w = eff * g * area / 1000.0
            a.inferred.append(f"Claimed output = {eff:.3g} × {g:g} W/m² × {area:g} m² = {w:.5g} kW.")
        if w is None:
            a.missing.append("The claimed electrical output or efficiency.")
        if a.missing:
            return
        if wi is not None:
            self.record(wi, "Claimed electrical output", field_name="W_out_kw")
        a.assumptions.append("The Petela–Landsberg bound for undiluted blackbody radiation is used. Real sunlight at "
                             "Earth is diluted and has less exergy, so this is a generous (conservative) upper bound.")
        a.mapping += [f"SOURCE: blackbody radiation from the sun at {t_src:g} K.",
                      f"SINK: environment at {t_amb:g} K.",
                      "Limit: η_L = 1 − (4/3)(T₀/T_s) + (1/3)(T₀/T_s)⁴ applied to the incident radiation."]
        a.model = "solar_blackbody"
        a.title = "Solar converter (Petela–Landsberg limit)"
        a.cases["solar_blackbody"] = {"model": "solar_blackbody", "title": a.title, "description": self.text,
                                      "T_source_K": t_src, "T_ambient_K": t_amb, "irradiance_W_m2": g,
                                      "collection_area_m2": area, "W_out_kw": w}

    # ---------------------------------------------------------------- chemical
    def route_chemical(self) -> None:
        a = self.a
        dg = self.of_kind("molar_energy")
        rates = self.of_kind("molar_rate")
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power()
            if i is not None:
                w, wi = self.q[i].value, i
        fuel = None
        for name, (g0, mm, eqn, pattern) in FUEL_GIBBS.items():
            if re.search(pattern, self.text, re.IGNORECASE):
                fuel = name
                break
        delta_g = None
        direction = None
        m_dir = re.search(r"reaction(?:\s+direction)?\s*[:=]\s*([^;\n]+?)(?:;|\n|$|\.\s)", self.text, re.IGNORECASE)
        if m_dir:
            direction = m_dir.group(1).strip()
        if dg:
            # prefer the value labelled Gibbs / ΔG over enthalpy
            pick = next((i for i in dg if re.search(r"gibbs|ΔG|delta\s*G|free\s+energy", self.wide_left(i, 40), re.I)), None)
            if pick is None and len(dg) == 1 and not re.search(r"enthalpy|ΔH|delta\s*H|heating\s+value",
                                                               self.wide_left(dg[0], 40), re.I):
                pick = dg[0]
            if pick is not None:
                delta_g = self.q[pick].value
                if delta_g > 0 and not re.search(r"[-−+]\s*$", self.text[:self.q[pick].start]) and \
                        re.search(r"releas|liberat|availab|produc", self.wide_left(pick, 60), re.I):
                    delta_g = -delta_g
                    a.inferred.append("Gibbs energy described as released: sign taken as negative (ΔG < 0).")
                self.record(pick, "Reaction Gibbs energy ΔG", delta_g, "kJ/mol", "delta_G_kj_per_mol_rxn")
        if delta_g is None and fuel is not None:
            delta_g = FUEL_GIBBS[fuel][0]
            a.assumptions.append(f"ΔG not given: standard value for {FUEL_GIBBS[fuel][2]} at 298 K, "
                                 f"{delta_g} kJ/mol of fuel, is used.")
        if direction is None and fuel is not None:
            direction = FUEL_GIBBS[fuel][2]
        rate = None
        if rates:
            rate = self.q[rates[0]].value
            self.record(rates[0], "Reaction (fuel) rate", field_name="reaction_rate_mol_rxn_s")
        elif fuel is not None:
            mflows = self.of_kind("mass_flow")
            if mflows:
                rate = self.q[mflows[0]].value * 1000.0 / FUEL_GIBBS[fuel][1]
                self.record(mflows[0], f"{fuel.capitalize()} mass flow", field_name="fuel_mass_flow")
                a.inferred.append(f"Molar rate = mass flow / M = {self.q[mflows[0]].value * 1000:.5g} g/s ÷ "
                                  f"{FUEL_GIBBS[fuel][1]} g/mol = {rate:.5g} mol/s.")
        if delta_g is None:
            a.missing.append("Gibbs energy of reaction ΔG (kJ/mol) or the fuel name.")
        if rate is None:
            a.missing.append("Reaction or fuel consumption rate (mol/s or g/s).")
        if direction is None:
            direction = "as stated in the description"
        if w is None and eff is not None and delta_g is not None and rate is not None:
            w = eff * (-delta_g) * rate
            a.inferred.append("Efficiency taken relative to −ΔG: W = η(−ΔG)ṅ.")
        if w is None:
            a.missing.append("The claimed electrical output.")
        if a.missing:
            return
        if wi is not None:
            self.record(wi, "Claimed electrical output", field_name="W_out_kw")
        a.mapping += [f"SOURCE: chemical reaction {direction}.",
                      "Limit: maximum non-expansion work = −ΔG × reaction rate (isothermal, isobaric). "
                      "No Carnot temperature is used for an electrochemical device."]
        a.model = "reaction_gibbs"
        a.title = "Electrochemical converter (Gibbs energy limit)"
        a.cases["reaction_gibbs"] = {"model": "reaction_gibbs", "title": a.title, "description": self.text,
                                     "reaction_direction": direction, "delta_G_kj_per_mol_rxn": delta_g,
                                     "reaction_rate_mol_rxn_s": rate, "W_out_kw": w}

    # ---------------------------------------------------------------- other
    def route_no_input(self) -> None:
        """Claimed output with zero energy input: a First Law (perpetual-motion) test."""
        a = self.a
        self.assign_energy_roles()
        w, wi, eff, _ = self.claim()
        if w is None:
            i = self.single_power(exclude_roles=())
            if i is not None:
                w, wi = self.q[i].value, i
        if w is None:
            a.missing.append("The claimed output power.")
            return
        if wi is not None:
            self.record(wi, "Claimed output", field_name="W_out_kw")
        a.model = "mechanical_power"
        a.title = "Claimed output with no energy input (perpetual-motion test)"
        a.mapping += ["SOURCE: none — the description states that no energy enters the device.",
                      "Test: First Law. With zero input, the maximum steady output is zero."]
        a.cases["mechanical_power"] = {"model": "mechanical_power", "title": a.title, "description": self.text,
                                       "recoverable_mechanical_power_kw": 0.0, "W_out_kw": w}

    def route_work_consuming(self) -> None:
        """Model 5: refrigerator / heat pump. Roles come from the verbs: heat extracted from the cold side,
        heat rejected/delivered to the hot side, and work (or electricity) consumed."""
        a = self.a
        heat_pump = self.has(r"\bheat\s+pumps?\b") and not self.has(r"\brefrigerat\w*|\bfreezer\b|\bair[- ]condition\w*|\bchiller\b")
        a.model = "refrigeration_cycle"
        a.title = "Heat pump" if heat_pump else "Refrigerator / cooling device"
        roles = {"Q_cold": [], "Q_hot": [], "W_in": []}
        for i, q in enumerate(self.q):
            if q.kind not in ("power", "energy"):
                continue
            left, right = self.wide_left(i, 70).lower(), self.text[q.end:q.end + 45].lower()
            clause = re.split(r"[,;]|\band\b|\bwhile\b", left)[-1]
            near_right = " ".join(re.findall(r"\S+", right)[:4])
            if re.search(r"\bwork\b|\belectric\w*|\bcompressor\b|\bpower\s+input\b|\binput\s+power\b", near_right) or \
                    re.search(r"\bconsum\w*|\brequir\w*|\bneeds?\b|\bdraws?\b|\buses?\b|\busing\b|\bwork\b|"
                              r"\belectric\w*|\bpower\s+input\b|\binput\b", clause) and not re.search(r"\bheat\b", near_right):
                roles["W_in"].append(i)
            elif re.search(r"\bextract\w*|\bremov\w*|\babsorb\w*|\btak\w*|\bcooling\s+(?:capacity|load|effect)|"
                           r"\brefrigeration\s+(?:capacity|effect)|\bfrom\s+the\s+(?:cold|outside|ambient|surround)", clause):
                roles["Q_cold"].append(i)
            elif re.search(r"\breject\w*|\bdeliver\w*|\bsuppl\w*|\bprovid\w*|\bdischarg\w*|\bdump\w*|\bgives?\b|"
                           r"\bheating\s+(?:capacity|load|output)|\bto\s+the\s+(?:room|house|hot|space|building|kitchen)", clause):
                roles["Q_hot"].append(i)
        case: Dict[str, Any] = {"model": "refrigeration_cycle", "title": a.title, "description": self.text,
                                "mode": "heat_pump" if heat_pump else "refrigerator"}
        labels = {"Q_cold": "Heat extracted from the cold side Q_C", "Q_hot": "Heat rejected/delivered to the hot side Q_H",
                  "W_in": "Work input W_in"}
        kinds = set()
        for key, idx in roles.items():
            if len(idx) > 1:
                a.issues.append(f"Several values look like the {labels[key].lower()}: "
                                + ", ".join(self.q[i].quote for i in idx) + "; the first is used.")
            if idx:
                case[key] = self.q[idx[0]].value
                kinds.add(self.q[idx[0]].kind)
                self.record(idx[0], labels[key], field_name=key)
        if len(kinds) > 1:
            raise thermo.ThermoInputError("Mixed energy amounts (kJ) and rates (kW): give all heats and the work on the same basis.")
        case["energy_unit"] = "kJ" if kinds == {"energy"} else "kW"
        cop = re.search(r"\b(?:COP|coefficient\s+of\s+performance)\b\D{0,20}?(\d+(?:\.\d+)?)", self.text, re.IGNORECASE)
        known = [k for k in roles if k in case]
        if cop and len(known) == 1:
            value = float(cop.group(1))
            a.extracted.append({"quantity": "Claimed COP", "value": value, "unit": "", "quote": cop.group(0)})
            base = known[0]
            if base == "W_in":
                case["Q_hot" if heat_pump else "Q_cold"] = value * case["W_in"]
            elif base == ("Q_hot" if heat_pump else "Q_cold"):
                case["W_in"] = case[base] / value
            a.inferred.append(f"From the claimed COP = {value:g}: " + ", ".join(
                f"{k} = {case[k]:.5g} {case['energy_unit']}" for k in ("Q_cold", "Q_hot", "W_in") if k in case and k != base))
        temps = [i for i in self.of_kind("temperature")]
        cold_t = hot_t = None
        for i in temps:
            left = self.wide_left(i, 80).lower()
            last = max(((m.end(), side) for side, pat in (
                ("cold", r"extract\w*|remov\w*|absorb\w*|cold|freezer|inside|outside\s+air|ambient\s+air|evaporat\w*|from"),
                ("hot", r"reject\w*|deliver\w*|room|house|hot|kitchen|warm|condens\w*|to"))
                for m in re.finditer(pat, left)), default=(None, None))[1]
            if last == "cold" and cold_t is None:
                cold_t = i
            elif last == "hot" and hot_t is None:
                hot_t = i
        if len(temps) == 2 and (cold_t is None or hot_t is None or cold_t == hot_t):
            cold_t, hot_t = sorted(temps, key=lambda i: self.q[i].value)
            a.assumptions.append("Temperature roles were unclear: the lower is taken as the cold side and the higher as the hot side.")
        for key, idx, label in (("T_cold_K", cold_t, "Cold-side temperature T_C"), ("T_hot_K", hot_t, "Hot-side temperature T_H")):
            if idx is None:
                a.missing.append(f"{label} (K or °C).")
            else:
                case[key] = self.q[idx].value
                self.record(idx, label, field_name=key)
        if sum(k in case for k in ("Q_cold", "Q_hot", "W_in")) < 2:
            a.missing.append("Two of: heat removed from the cold side, heat delivered to the hot side, work input (or a COP).")
        if a.missing:
            return
        a.mapping += [f"Device: {'heat pump' if heat_pump else 'refrigerator'} — a reversed cycle that CONSUMES work.",
                      "COLD side: heat Q_C is extracted at T_C.   HOT side: heat Q_H = Q_C + W_in is rejected at T_H.",
                      "Tests: First Law, Carnot COP limit, and S_gen = Q_H/T_H − Q_C/T_C ≥ 0 (Clausius)."]
        a.cases["refrigeration_cycle"] = case

    def route_unsupported(self) -> None:
        """No validated model (transient conduction, tidal, ...): MODEL REQUIRED rather than a forced verdict."""
        a = self.a
        kind = ("transient heat transfer" if self.has(r"transient|with\s+time|time[- ]dependent|suddenly|how\s+long|"
                                                       r"cooling\s+time|lumped") else
                "tidal / wave energy" if self.has(r"tid|wave\s+energy") else "this process")
        a.model, a.title = "unsupported_domain", f"Model required: {kind}"
        reason = {"transient heat transfer": "Transient heat transfer needs a time-dependent model (lumped capacitance or "
                                            "transient conduction with a heat-transfer coefficient), which is not implemented. "
                                            "The steady and cyclic models would give a misleading answer.",
                  "tidal / wave energy": "A tidal/wave limit needs the tidal range, basin area and period; no validated "
                                        "tidal model is implemented. (A hydro case with a stated head and flow can use Model 7.)"
                  }.get(kind, "No validated thermodynamic model is implemented for this process.")
        a.mapping.append(reason)
        a.cases["unsupported_domain"] = {"model": "unsupported_domain", "title": a.title, "description": self.text,
                                         "domain": kind, "reason": reason}

    def route_unknown(self) -> None:
        a = self.a
        if self.try_steady_cv() or self.try_closed_system():
            a.domain = "thermal"
            return
        if self.of_kind("temperature"):
            a.domain = "thermal"
            self.build_streams()
            return self.route_thermal()
        a.missing.append("Describe the energy source (heat at a temperature, mechanical motion, wind, falling water, "
                         "sunlight or a chemical reaction) and the claimed output with units.")


# ============================================================================
# 4. Public entry point
# ============================================================================

# Why a model was chosen: the physical reason plus the words in the text that pointed to it.
MODEL_WHY = {
    "fixed_reservoir_engine": ("Heat is taken from a source at a fixed temperature and rejected at a fixed lower "
                               "temperature while work is produced: a cyclic heat engine between two reservoirs.",
                               r"heat\s+engine|cyclic|cycle|engine|reservoirs?|carnot|source|sink|surroundings|power\s+(?:plant|cycle)"),
    "finite_stream_engine": ("Two flowing streams change temperature (finite heat capacity) and work is produced or asked "
                             "for, so they cannot be treated as fixed-temperature reservoirs.",
                             r"hot\s+\w+|cold\s+\w+|streams?|flows?|cools?|work|maximum|waste\s+heat"),
    "steady_control_volume": ("Mass flows steadily through an open device with stated inlet and outlet states, so mass, "
                              "energy and entropy are balanced on a control volume.",
                              r"turbines?|compressors?|pumps?|nozzles?|enters?|exits?|inlet|outlet|steady[- ]flow|h\s*\d|kg/s"),
    "closed_system": ("A fixed mass exchanges heat and work with no flow across its boundary, so the closed-system "
                      "First Law (Q − W = ΔE) and entropy balance apply.",
                      r"closed\s+system|piston|cylinder|tank|internal\s+energy|cycle"),
    "refrigeration_cycle": ("The device consumes work to move heat from a cold side to a hot side: a reversed cycle, "
                            "judged against the Carnot COP rather than the Carnot efficiency.",
                            r"refrigerat\w*|freezer|heat\s+pump|air[- ]condition\w*|chiller|COP|consum\w*|extracts?|removes?"),
    "finite_stream_heat_recovery": ("Heat passes from one stream to another with no shaft work: a heat exchanger, "
                                    "so W = 0 and only energy closure, entropy generation and temperature cross apply.",
                                    r"exchangers?|no\s+shaft\s+work|counter-?flow|heat\s+recovery|absorbed|preheat"),
    "mechanical_power": ("The input is mechanical (motion, wind or falling water), so the output is limited by the "
                         "available mechanical power — no Carnot temperature applies.",
                         r"walk\w*|steps?|pedal\w*|torque|force|wind|rotor|Betz|hydro\w*|head|dam|no\s+\w+\s+input"),
    "solar_blackbody": ("The input is solar radiation, so the limit is the radiation exergy (Petela–Landsberg).",
                        r"solar|sun\w*|irradian\w*|photovoltaic|PV|radiation"),
    "reaction_gibbs": ("Chemical energy is converted directly to electricity, so the limit is −ΔG × reaction rate.",
                       r"fuel\s+cells?|Gibbs|ΔG|reaction|hydrogen|methan\w*|electrochemical"),
    "property_change": ("Only property changes are asked for (no device and no claim), so the property calculator "
                        "answers without a feasibility verdict.", r"enthalpy|entropy|effective\s+temperature|latent|heated|cooled"),
    "unsupported_domain": ("No validated thermodynamic model exists here for this process, so MODEL REQUIRED is "
                           "returned instead of a misleading verdict.", r"transient|with\s+time|suddenly|tidal|tides?|wave"),
}


def selection_reason(analysis: Analysis) -> List[str]:
    """One sentence on the physics that decided the model, and the words in the text that pointed to it."""
    if analysis.model not in MODEL_WHY:
        return []
    sentence, cues = MODEL_WHY[analysis.model]
    found = []
    for m in re.finditer(r"\b(?:" + cues + r")\b", analysis.text, re.IGNORECASE):
        word = m.group(0).lower()
        if word not in found:
            found.append(word)
    reason = [sentence]
    if found:
        reason.append("Words that pointed here: " + ", ".join(f"“{w}”" for w in found[:6]) + ".")
    return reason


def analyze_scenario(text: str) -> Dict[str, Any]:
    """Return a JSON-ready dictionary with the interpretation and engine result."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter a scenario description.")
    analyzer = ScenarioAnalyzer(text)
    try:
        analysis = analyzer.analyse()
        analysis.reason = selection_reason(analysis)
    except thermo.ThermoInputError as error:
        analysis = analyzer.a
        analysis.issues.append(str(error))
        return {"status": "error", "route": analysis.model, "analysis": analysis.as_dict(),
                "source_quotes": analysis.quotes, "cases": {}, "result": None, "error": str(error)}
    if analysis.missing or not analysis.cases:
        if not analysis.missing:
            analysis.missing.append("A complete description of the source, sink and claimed output.")
        return {"status": "needs_values", "route": analysis.model or _domain_route(analysis.domain),
                "analysis": analysis.as_dict(), "source_quotes": analysis.quotes, "cases": {}, "result": None,
                "issues": analysis.issues}
    case = list(analysis.cases.values())[-1]
    try:
        result = thermo.run_case(case)
    except thermo.ThermoInputError as error:
        return {"status": "error", "route": analysis.model, "analysis": analysis.as_dict(),
                "source_quotes": analysis.quotes, "cases": analysis.cases, "result": None,
                "error": f"The engine rejected the mapped inputs: {error}"}
    result_dict = result.as_dict()
    if analysis.assumptions:
        result_dict["verdict"]["reasons"].append(
            "This verdict uses the labelled assumptions listed under 'How the scenario was interpreted'.")
    return {"status": "evaluated", "route": result.model, "analysis": analysis.as_dict(),
            "source_quotes": analysis.quotes, "cases": analysis.cases, "result": result_dict, "questions": []}


def _domain_route(domain: str) -> Optional[str]:
    return {"mechanical": "mechanical_power", "wind": "mechanical_power", "hydro": "mechanical_power",
            "solar": "solar_blackbody", "chemical": "reaction_gibbs", "thermal": "fixed_reservoir_engine"}.get(domain)


def format_report(response: Dict[str, Any]) -> str:
    lines = ["=" * 78, "AUTOMATIC FEASIBILITY ANALYSIS", "=" * 78]
    an = response.get("analysis", {})
    lines.append(f"Domain : {DOMAIN_LABELS.get(an.get('domain'), an.get('domain'))}")
    lines.append(f"Model  : {an.get('title') or an.get('model')}")
    if an.get("extracted"):
        lines.append("\nEXTRACTED FROM THE TEXT")
        for e in an["extracted"]:
            value = e["value"]
            shown = f"{value:.6g}" if isinstance(value, float) else str(value)
            lines.append(f"  - {e['quantity']}: {shown} {e['unit']}   ← \"{e['quote']}\"")
    for title, key in (("SOURCE / SINK MAPPING", "mapping"), ("CALCULATED / INFERRED", "inferred"),
                       ("ASSUMPTIONS", "assumptions"), ("NOTES", "issues"), ("STILL NEEDED", "missing")):
        if an.get(key):
            lines.append(f"\n{title}")
            lines += [f"  - {item}" for item in an[key]]
    if response.get("error"):
        lines.append(f"\nERROR: {response['error']}")
    result = response.get("result")
    if result:
        calc = result.get("calculations", {})
        lines.append("\nKEY RESULTS")
        for key in ("carnot_efficiency_percent", "actual_efficiency_percent", "reversible_work_limit_kw",
                    "reversible_power_limit_kw", "effective_temperature_work_reference_kw",
                    "hot_effective_temperature_K", "cold_effective_temperature_K", "S_gen_dot_kw_per_k", "S_gen_kj_per_k"):
            if calc.get(key) is not None:
                lines.append(f"  {key}: {calc[key]:.6g}")
        lines.append("\nVERDICT: " + result["verdict"]["status"])
        lines += [f"  - {reason}" for reason in result["verdict"]["reasons"]]
    else:
        lines.append(f"\nSTATUS: {response['status']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze a plain-English energy claim automatically.")
    parser.add_argument("scenario", nargs="*", help="Scenario text (reads stdin if omitted).")
    parser.add_argument("--json", action="store_true", help="Print the full JSON response.")
    args = parser.parse_args(argv)
    text = " ".join(args.scenario) if args.scenario else sys.stdin.read()
    response = analyze_scenario(text)
    print(json.dumps(response, indent=2, default=str) if args.json else format_report(response))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
