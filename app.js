const $ = (id) => document.getElementById(id);
const number = new Intl.NumberFormat(undefined, { maximumSignificantDigits: 5 });
const C = 273.15;   // °C fields are converted to K before the engine sees them

// ---------------------------------------------------------------------------
// Model registry. Stable keys (= engine model keys / saved cases) are separate from the display number.
// Field: [key, label, default, min, max, scale, offset]; default "" = optional; an array default = a select.
// Values sent to the engine = typed value × scale + offset (so °C → K and W → kW happen before any calculation).
// ---------------------------------------------------------------------------
const models = {
  fixed_reservoir_engine: {
    number: 1, group: "model",
    name: "Heat engine — fixed hot and cold temperatures",
    fields: [["T_hot_K", "Hot temperature (K)", 600, .001], ["T_cold_K", "Cold temperature (K)", 300, .001],
      ["Q_hot_kw", "Heat supplied (kW)", 100, .001], ["W_out_kw", "Claimed power (kW)", 30, 0]],
    note: "The Carnot ceiling uses absolute temperatures. Rejected heat is calculated from the First Law.",
  },
  finite_stream_engine: {
    number: 2, group: "model",
    name: "Waste-heat engine — hot and cold streams with power output",
    fields: [
      ["hot_stream.mass_flow_kg_s", "Hot-stream flow (kg/s)", .3, .000001],
      ["hot_stream.T_in_K", "Hot-stream inlet (°C)", 57.746972594, -273.149, undefined, 1, C],
      ["hot_stream.T_out_K", "Hot-stream outlet (°C)", 45, -273.149, undefined, 1, C],
      ["hot_stream.cp_kj_kg_k", "Hot-stream heat capacity (kJ/kg K)", 4.184, .000001],
      ["cold_stream.mass_flow_kg_s", "Cold-stream flow (kg/s)", .6, .000001],
      ["cold_stream.T_in_K", "Cold-stream inlet (°C)", 25, -273.149, undefined, 1, C],
      ["cold_stream.T_out_K", "Cold-stream outlet (°C, optional — calculated if blank)", "", -273.149, undefined, 1, C],
      ["cold_stream.cp_kj_kg_k", "Cold-stream heat capacity (kJ/kg K)", 4.184, .000001],
      ["W_out_kw", "Claimed power output (kW, optional — blank gives the limit only)", "", 0],
    ],
    note: "Steady, adiabatic boundary around both streams and the device. Leave the cold outlet blank to have it calculated from the First Law; leave the claim blank to get only the exact reversible limit W_max (cold outlet chosen so that S_gen = 0).",
  },
  steady_control_volume: {
    number: 3, group: "model", review: false,
    name: "Steady control volume — turbine, compressor, pump, nozzle (one inlet, one outlet)",
    fields: [["cv_m", "Mass flow rate (kg/s)", 1, .000001],
      ["cv_h_in", "Inlet enthalpy h₁ (kJ/kg)", 3231.7], ["cv_s_in", "Inlet entropy s₁ (kJ/kg K, optional)", ""],
      ["cv_h_out", "Outlet enthalpy h₂ (kJ/kg)", 2675.0], ["cv_s_out", "Outlet entropy s₂ (kJ/kg K, optional)", ""],
      ["cv_q", "Heat into the device (kW, optional; negative = heat loss)", ""],
      ["cv_tb", "Boundary temperature of that heat (K, optional)", "", .001],
      ["W_out_kw", "Shaft work out (kW; negative for a compressor or pump; blank = from First Law)", ""]],
    build: (c) => {
      const stream = (h, s) => ({mass_flow_kg_s: c.cv_m, specific_enthalpy_kj_kg: h, ...(s === undefined ? {} : {specific_entropy_kj_kg_k: s})});
      return {model: c.model, inlets: [stream(c.cv_h_in, c.cv_s_in)], outlets: [stream(c.cv_h_out, c.cv_s_out)],
        heat: c.cv_q === undefined ? [] : [{heat_into_cv_kw: c.cv_q, ...(c.cv_tb === undefined ? {} : {boundary_temperature_K: c.cv_tb})}],
        ...(c.W_out_kw === undefined ? {} : {W_out_kw: c.W_out_kw})};
    },
    note: "Take h and s from property tables. Checks mass, energy and S_gen = ṁ(s₂ − s₁) − Q/T_b ≥ 0. Without s values only the mass and energy balances are checked.",
  },
  closed_system: {
    number: 4, group: "model", review: false,
    name: "Closed system — piston-cylinder, sealed tank, or a full cycle",
    fields: [["cs_q", "Heat into the system, Q₁ (kJ; negative = heat out)", 15], ["cs_tb", "Boundary temperature of Q₁ (K, optional)", "", .001],
      ["cs_q2", "Second heat interaction Q₂ (kJ, optional)", ""], ["cs_tb2", "Boundary temperature of Q₂ (K, optional)", "", .001],
      ["W_out_kj", "Work done by the system (kJ, optional if ΔE is given)", ""],
      ["delta_E_kj", "Change in system energy ΔE (kJ, optional if W is given; 0 for a cycle)", ""],
      ["delta_S_system_kj_K", "Change in system entropy ΔS (kJ/K, optional; 0 for a cycle)", ""],
      ["U1_kj", "Initial internal energy U₁ (kJ, optional)", ""]],
    build: (c) => {
      const heat = [[c.cs_q, c.cs_tb], [c.cs_q2, c.cs_tb2]].filter(([q]) => q !== undefined)
        .map(([q, t]) => ({heat_into_system_kj: q, ...(t === undefined ? {} : {boundary_temperature_K: t})}));
      const out = {model: c.model, heat};
      for (const key of ["W_out_kj", "delta_E_kj", "delta_S_system_kj_K", "U1_kj"]) if (c[key] !== undefined) out[key] = c[key];
      return out;
    },
    note: "First Law: Q − W = ΔE (the missing one of Q, W, ΔE is calculated). Second Law: S_gen = ΔS − ΣQ/T_b ≥ 0, checked when ΔS and the boundary temperatures are given. For a full cycle enter ΔE = 0 and ΔS = 0 (Clausius inequality).",
  },
  refrigeration_cycle: {
    number: 5, group: "model",
    name: "Refrigerator / heat pump — work consumed, Carnot COP",
    fields: [["mode", "Device", [["refrigerator", "Refrigerator / freezer / air conditioner"], ["heat_pump", "Heat pump"]]],
      ["energy_unit", "Energy basis", [["kW", "Rates (kW)"], ["kJ", "Amounts (kJ per cycle or period)"]]],
      ["T_cold_K", "Cold-side temperature (°C)", -18, -273.149, undefined, 1, C],
      ["T_hot_K", "Hot-side temperature (°C)", 25, -273.149, undefined, 1, C],
      ["Q_cold", "Heat removed from the cold side Q_C (kW or kJ)", ""],
      ["Q_hot", "Heat delivered to the hot side Q_H (kW or kJ)", ""],
      ["W_in", "Work / electricity input W_in (kW or kJ)", ""]],
    note: "Enter any two of Q_C, Q_H and W_in; the third comes from the First Law Q_H = Q_C + W_in. Compares the COP with the Carnot COP (T_C/(T_H − T_C) for a refrigerator, T_H/(T_H − T_C) for a heat pump).",
  },
  finite_stream_heat_recovery: {
    number: 6, group: "model",
    name: "Heat exchanger — hot stream heats a cold stream, no shaft work",
    fields: [
      ["hot_stream.mass_flow_kg_s", "Hot-stream flow (kg/s)", .5, .000001],
      ["hot_stream.T_in_K", "Hot-stream inlet (°C)", 80, -273.149, undefined, 1, C],
      ["hot_stream.T_out_K", "Hot-stream outlet (°C, optional)", "", -273.149, undefined, 1, C],
      ["hot_stream.cp_kj_kg_k", "Hot-stream heat capacity (kJ/kg K)", 4.18, .000001],
      ["cold_stream.mass_flow_kg_s", "Cold-stream flow (kg/s)", .5, .000001],
      ["cold_stream.T_in_K", "Cold-stream inlet (°C)", 25, -273.149, undefined, 1, C],
      ["cold_stream.T_out_K", "Cold-stream outlet (°C, optional)", "", -273.149, undefined, 1, C],
      ["cold_stream.cp_kj_kg_k", "Cold-stream heat capacity (kJ/kg K)", 4.18, .000001],
      ["T0_K", "Environment temperature T₀ (°C, optional — for exergy destroyed)", "", -273.149, undefined, 1, C],
      ["heat_loss_to_surroundings_kw", "Heat lost to surroundings (kW, optional; blank = insulated)", "", 0],
      ["surroundings_temperature_K", "Surroundings temperature for that loss (°C, optional)", "", -273.149, undefined, 1, C]],
    build: (c) => ({...c, energy_relative_tolerance: 0.001}),
    note: "W_shaft = 0 by definition. Checks Q released = Q absorbed (+ any heat lost, 0.1 % allowance for rounded data), S_gen ≥ 0 and no temperature cross. One missing outlet is calculated from the energy balance.",
  },
  mechanical_power: {
    number: 7, group: "model",
    name: "Mechanical harvesting — mechanical power available",
    fields: [["recoverable_mechanical_power_kw", "Mechanical power available (W)", 8, 0, undefined, .001],
      ["W_out_kw", "Claimed electrical output (W)", 5, 0, undefined, .001],
      ["heat_rejected_kw", "Measured heat loss (W, optional)", "", 0, undefined, .001],
      ["heat_rejection_temperature_K", "Heat rejection temperature (°C, optional)", "", -273.149, undefined, 1, C]],
    note: "Output ≤ mechanical input (no Carnot limit). Enter the heat-rejection temperature to calculate entropy generation, and a measured heat loss to check energy conservation.",
  },
  mechanical_steps: {
    number: 7, group: "model", engineModel: "mechanical_power",
    name: "Mechanical harvesting — energy per step and walking rate",
    fields: [["recoverable_energy_per_event_j", "Energy recovered per step (J)", 4, 0],
      ["events_per_s", "Walking rate (steps/min)", 120, 0, undefined, 1 / 60],
      ["W_out_kw", "Claimed electrical output (W)", 5, 0, undefined, .001],
      ["heat_rejected_kw", "Measured heat loss (W, optional)", "", 0, undefined, .001],
      ["heat_rejection_temperature_K", "Heat rejection temperature (°C, optional)", "", -273.149, undefined, 1, C]],
    note: "The input limit is recovered energy per step multiplied by the number of steps per second.",
  },
  mechanical_force: {
    number: 7, group: "model", engineModel: "mechanical_power",
    name: "Mechanical harvesting — force × displacement per step",
    fields: [["force_N", "Average force (N)", 700, 0], ["displacement_m", "Displacement per step (m)", .05, 0],
      ["events_per_s", "Steps per second", 2, 0], ["W_out_kw", "Claimed electrical output (W)", 20, 0, undefined, .001]],
    note: "Work per step = F·d; mechanical input = F·d × steps per second.",
  },
  mechanical_torque: {
    number: 7, group: "model", engineModel: "mechanical_power",
    name: "Mechanical harvesting — pedalling (torque × angular speed)",
    fields: [["torque_Nm", "Torque (N·m)", 20, 0], ["angular_velocity_rad_s", "Angular speed (rad/s)", 10, 0],
      ["W_out_kw", "Claimed electrical output (W)", 120, 0, undefined, .001]],
    note: "Mechanical input P = τ·ω.",
  },
  mechanical_wind: {
    number: 7, group: "model", engineModel: "mechanical_power",
    name: "Mechanical harvesting — wind turbine (Betz limit)",
    fields: [["air_density_kg_m3", "Air density (kg/m³)", 1.225, .000001], ["swept_area_m2", "Rotor swept area (m²)", 10, .000001],
      ["wind_speed_m_s", "Wind speed (m/s)", 8, 0], ["W_out_kw", "Claimed electrical output (W)", 1500, 0, undefined, .001]],
    note: "Wind power = ½ρAv³; at most 16/27 of it can be extracted (Betz limit).",
  },
  mechanical_hydro: {
    number: 7, group: "model", engineModel: "mechanical_power",
    name: "Mechanical harvesting — hydro turbine (ρgQH)",
    fields: [["head_m", "Head (m)", 10, .000001], ["volumetric_flow_m3_s", "Water flow (m³/s)", .02, .000001],
      ["water_density_kg_m3", "Water density (kg/m³)", 1000, .000001], ["g_m_s2", "g (m/s²)", 9.81, .000001],
      ["W_out_kw", "Claimed electrical output (W)", 1500, 0, undefined, .001]],
    note: "Ideal hydraulic power = ρ g Q H (gross head, no friction losses).",
  },
  solar_blackbody: {
    number: 8, group: "model",
    name: "Solar radiation conversion — ideal radiation limit",
    fields: [["T_source_K", "Source temperature (K)", 5778, .001], ["T_ambient_K", "Ambient temperature (K)", 300, .001],
      ["irradiance_W_m2", "Irradiance (W/m²)", 1000, 0], ["collection_area_m2", "Collection area (m²)", 2, .0001],
      ["W_out_kw", "Claimed power (kW)", .5, 0]],
    note: "Uses the Petela–Landsberg limit for ideal radiation at the stated conditions.",
  },
  reaction_gibbs: {
    number: 9, group: "model",
    name: "Electrochemical conversion — fuel cell (Gibbs energy)",
    fields: [["reaction_direction", "Reaction direction", "H₂ + ½O₂ → H₂O(l)"],
      ["delta_G_kj_per_mol_rxn", "ΔG (kJ/mol reaction, signed)", -237.141],
      ["reaction_rate_mol_rxn_s", "Forward rate (mol/s)", .01, .000001], ["W_out_kw", "Claimed power (kW)", 2, 0]],
    note: "Supply the signed Gibbs energy for the stated direction and conditions. W_max = −ΔG × rate; a positive ΔG gives no work.",
  },
};
// One frontend registry for the model descriptions: it drives the model reference table, the nine home-page example
// cards (example.title names an entry in the examples list below) and the Example Library groups.
// Numbers match the engine's MODEL_REGISTRY; form = workbench form.
const modelInfo = {
  1: {name: "Fixed-temperature reservoir engine", category: "heat-engines", form: "fixed_reservoir_engine",
    example: {title: "Normal feasible heat engine", label: "Carnot heat engine", description: "100 kW taken from a 600 K reservoir, heat rejected at 300 K, 40 kW claimed — checked against the 50 % Carnot limit."},
    tech: "Inventor’s engines, Stirling and power-plant cycles with given source and sink temperatures",
    checks: "η ≤ 1 − T_C/T_H · Q_H = W + Q_C · S_gen = Q_C/T_C − Q_H/T_H ≥ 0"},
  2: {name: "Finite hot + cold streams (work produced)", category: "heat-engines", form: "finite_stream_engine",
    example: {title: "Normal heat recovery", label: "Waste-heat recovery", description: "Hot water cooling from 90 to 60 °C drives an engine that rejects heat to cold water at 20 °C; 10 kW claimed."},
    tech: "Waste-heat engines, exhaust-gas recovery, data-centre waste heat",
    checks: "W ≤ W_max = Q̇_hot − Q̇_cold,rev (cold outlet where S_gen = 0) · First Law"},
  3: {name: "Steady control volume", category: "turbines", form: "steady_control_volume",
    example: {title: "Steam turbine", label: "Steam turbine", description: "2 kg/s of steam from h = 3200 to 2600 kJ/kg with entropies from tables; 1200 kW of shaft work."},
    tech: "Steam and gas turbines, compressors, pumps, nozzles",
    checks: "Σṁ_in = Σṁ_out · Q̇ − Ẇ = Σṁh_out − Σṁh_in · S_gen = Σṁs_out − Σṁs_in − ΣQ̇/T ≥ 0"},
  4: {name: "Closed system", category: "closed", form: "closed_system",
    example: {title: "Piston-cylinder", label: "Piston-cylinder", description: "Gas receives 15 kJ of heat at 300 K, its energy rises 10 kJ and its entropy 0.06 kJ/K; 5 kJ of work."},
    tech: "Piston-cylinder devices, sealed tanks, complete cycles",
    checks: "Q − W = ΔE · S_gen = ΔS − ΣQ/T ≥ 0 (Clausius for a cycle)"},
  5: {name: "Refrigerator / heat pump", category: "refrigeration", form: "refrigeration_cycle",
    example: {title: "Normal refrigerator", label: "Refrigerator", description: "A freezer at −18 °C in a 25 °C kitchen removes 0.3 kW using 0.1 kW of electricity."},
    tech: "Refrigerators, freezers, air conditioners, heat pumps",
    checks: "Q_H = Q_C + W_in · COP ≤ T_C/(T_H − T_C) or T_H/(T_H − T_C) · S_gen ≥ 0"},
  6: {name: "Heat exchanger (no shaft work)", category: "exchangers", form: "finite_stream_heat_recovery",
    example: {title: "Balanced water–water exchanger", label: "Water–water heat exchanger", description: "Hot water 0.5 kg/s cools 90 → 60 °C while cold water 1 kg/s warms 20 → 35 °C; no shaft work."},
    tech: "Water–water and oil–water exchangers, data-centre heat recovery",
    checks: "W = 0 · Q̇_released = Q̇_absorbed (+ loss) · S_gen ≥ 0 · no temperature cross"},
  7: {name: "Mechanical energy harvesting", category: "mechanical", form: "mechanical_power",
    example: {title: "Wind turbine", label: "Wind turbine", description: "A 10 m² rotor in 8 m/s wind (ρ = 1.225 kg/m³) claims 1.5 kW — checked against the Betz limit."},
    tech: "Walking and pedalling generators, wind turbines, hydro turbines, no-input claims",
    checks: "Output ≤ mechanical input (F·d, τω) · Betz (16/27)·½ρAv³ · ρgQH"},
  8: {name: "Solar radiation conversion", category: "solar", form: "solar_blackbody",
    example: {title: "Normal solar panel", label: "Solar panel", description: "2 m² under 1000 W/m² of sunlight claims 500 W — checked against the Petela–Landsberg limit."},
    tech: "Solar panels and radiation converters",
    checks: "Output ≤ [1 − (4/3)(T₀/T_s) + (1/3)(T₀/T_s)⁴]·G·A (Petela–Landsberg)"},
  9: {name: "Electrochemical conversion", category: "electrochemical", form: "reaction_gibbs",
    example: {title: "Hydrogen fuel cell", label: "Hydrogen fuel cell", description: "0.01 mol/s of H₂ (ΔG = −237.1 kJ/mol) claims 2 kW — checked against −ΔG × rate."},
    tech: "Hydrogen, methane and methanol fuel cells",
    checks: "Output ≤ −ΔG × reaction rate (ΔG < 0 required)"},
};
const categories = [
  {key: "heat-engines", name: "Heat engines", blurb: "Fixed-temperature reservoirs and finite hot and cold streams."},
  {key: "refrigeration", name: "Refrigerators and heat pumps", blurb: "Work-consuming cycles judged by the Carnot COP."},
  {key: "turbines", name: "Turbines and compressors", blurb: "Steady-flow devices from table values of h and s."},
  {key: "closed", name: "Closed systems and cycles", blurb: "Piston-cylinders, tanks and complete cycles."},
  {key: "exchangers", name: "Heat exchangers and heat recovery", blurb: "No shaft work: energy closure, S_gen and temperature cross."},
  {key: "mechanical", name: "Mechanical energy harvesting", blurb: "Walking, pedalling, wind (Betz) and hydro (ρgQH)."},
  {key: "solar", name: "Solar conversion", blurb: "The Petela–Landsberg radiation exergy limit."},
  {key: "electrochemical", name: "Electrochemical conversion", blurb: "Fuel cells limited by −ΔG × reaction rate."},
];
const categoryOf = (example) => modelInfo[example.model].category;

// ---------------------------------------------------------------------------
// Examples: grouped by model, in model order; the order inside each group is the order written here.
// form + values (engine units) fill the form; text examples go through the description analyzer.
// expect = the verdict the example is built to show (checked by website/test_ui.cjs against the real engine).
// ---------------------------------------------------------------------------
const water = (m, tin, tout) => ({mass_flow_kg_s: m, T_in_K: tin + C, ...(tout === undefined ? {} : {T_out_K: tout + C}), cp_kj_kg_k: 4.18});
const examples = [
  {model: 1, title: "Normal feasible heat engine", note: "40 kW from 100 kW between 600 K and 300 K.", form: "fixed_reservoir_engine",
    values: {T_hot_K: 600, T_cold_K: 300, Q_hot_kw: 100, W_out_kw: 40}, expect: "FEASIBLE"},
  {model: 1, title: "Carnot-limit case", note: "Exactly the Carnot work: S_gen = 0.", form: "fixed_reservoir_engine",
    values: {T_hot_K: 600, T_cold_K: 300, Q_hot_kw: 100, W_out_kw: 50}, expect: "FEASIBLE"},
  {model: 1, title: "Carnot violation", featured: true, note: "60 % claimed against a 50 % Carnot limit.", form: "fixed_reservoir_engine",
    values: {T_hot_K: 600, T_cold_K: 300, Q_hot_kw: 100, W_out_kw: 60}, expect: "NOT FEASIBLE"},
  {model: 1, title: "Missing-temperature case", note: "No reservoir temperatures: the tester asks for them.",
    text: "A heat engine receives 100 kW of heat and produces 40 kW of work. Determine feasibility.", expect: "needs_values"},
  {model: 2, title: "Normal heat recovery", note: "10 kW claimed; the cold outlet is calculated.", form: "finite_stream_engine",
    values: {hot_stream: water(1, 90, 60), cold_stream: water(1, 20), W_out_kw: 10}, expect: "FEASIBLE"},
  {model: 2, title: "Excessive work claim", featured: true, note: "150 kW claimed from 125.4 kW of heat.", form: "finite_stream_engine",
    values: {hot_stream: water(1, 90, 60), cold_stream: water(1, 20), W_out_kw: 150}, expect: "NOT FEASIBLE"},
  {model: 2, title: "Reversible limit", note: "No claim: the maximum reversible work W_max.", form: "finite_stream_engine",
    values: {hot_stream: water(1, 90, 60), cold_stream: water(1, 20)}, expect: "LIMIT CALCULATED"},
  {model: 2, title: "Exhaust-gas engine (described in words)", note: "Exhaust gas 500 → 150 °C, cooling water at 20 °C, 300 kW claimed.",
    text: "Exhaust gas at 2 kg/s enters at 500 °C and leaves at 150 °C, cp = 1.1 kJ/kg K. Cooling water at 5 kg/s enters at 20 °C. The engine claims 300 kW.",
    expect: "FEASIBLE"},
  {model: 3, title: "Steam turbine", featured: true, note: "h and s from tables; S_gen = 0.6 kW/K.", form: "steady_control_volume",
    values: {cv_m: 2, cv_h_in: 3200, cv_s_in: 6.8, cv_h_out: 2600, cv_s_out: 7.1, W_out_kw: 1200}, expect: "FEASIBLE"},
  {model: 3, title: "Compressor", featured: true, note: "80 kW consumed (W_out = −80 kW).", form: "steady_control_volume",
    values: {cv_m: 1, cv_h_in: 300, cv_s_in: 6.5, cv_h_out: 380, cv_s_out: 6.7, W_out_kw: -80}, expect: "FEASIBLE"},
  {model: 3, title: "Energy imbalance", note: "1500 kW claimed from a 1200 kW enthalpy drop.", form: "steady_control_volume",
    values: {cv_m: 2, cv_h_in: 3200, cv_h_out: 2600, W_out_kw: 1500}, expect: "NOT FEASIBLE"},
  {model: 3, title: "Missing outlet state", note: "Only the inlet enthalpy is known.",
    text: "Steam enters a turbine at 2 kg/s with inlet enthalpy 3200 kJ/kg. The turbine produces 1000 kW work. Determine feasibility.",
    expect: "needs_values"},
  {model: 4, title: "Complete cycle", featured: true, note: "500 kJ in at 600 K, 300 kJ out at 300 K, 200 kJ work.", form: "closed_system",
    values: {cs_q: 500, cs_tb: 600, cs_q2: -300, cs_tb2: 300, W_out_kj: 200, delta_E_kj: 0, delta_S_system_kj_K: 0}, expect: "FEASIBLE"},
  {model: 4, title: "Piston-cylinder", featured: true, note: "15 kJ heat at 300 K, ΔU = 10 kJ, ΔS = 0.06 kJ/K.", form: "closed_system",
    values: {cs_q: 15, cs_tb: 300, W_out_kj: 5, delta_E_kj: 10, delta_S_system_kj_K: .06}, expect: "FEASIBLE"},
  {model: 4, title: "First Law violation", note: "Q − W = 200 kJ but ΔE = 100 kJ; ΔS is not needed for this check.", form: "closed_system",
    values: {cs_q: 500, W_out_kj: 300, delta_E_kj: 100}, expect: "NOT FEASIBLE"},
  {model: 4, title: "Second Law violation", note: "Heat taken at 300 K, rejected at 600 K.", form: "closed_system",
    values: {cs_q: 500, cs_tb: 300, cs_q2: -300, cs_tb2: 600, W_out_kj: 200, delta_E_kj: 0, delta_S_system_kj_K: 0}, expect: "NOT FEASIBLE"},
  {model: 5, title: "Normal refrigerator", featured: true, note: "Freezer at −18 °C: COP 3 vs Carnot 5.9.", form: "refrigeration_cycle",
    values: {mode: "refrigerator", energy_unit: "kW", T_cold_K: -18 + C, T_hot_K: 25 + C, Q_cold: .3, W_in: .1}, expect: "FEASIBLE"},
  {model: 5, title: "Refrigerator exceeding Carnot COP", note: "COP 10 claimed; Carnot allows 5.9.", form: "refrigeration_cycle",
    values: {mode: "refrigerator", energy_unit: "kW", T_cold_K: -18 + C, T_hot_K: 25 + C, Q_cold: .3, W_in: .03}, expect: "NOT FEASIBLE"},
  {model: 5, title: "Normal heat pump", note: "10 kW to a 35 °C circuit from 0 °C air, COP 4.", form: "refrigeration_cycle",
    values: {mode: "heat_pump", energy_unit: "kW", T_cold_K: 0 + C, T_hot_K: 35 + C, Q_hot: 10, W_in: 2.5}, expect: "FEASIBLE"},
  {model: 5, title: "Heat pump exceeding Carnot COP", featured: true, note: "COP 10 claimed; Carnot allows 8.8.", form: "refrigeration_cycle",
    values: {mode: "heat_pump", energy_unit: "kW", T_cold_K: 0 + C, T_hot_K: 35 + C, Q_hot: 10, W_in: 1}, expect: "NOT FEASIBLE"},
  {model: 5, title: "Missing work input", note: "Only the cooling load is known.", form: "refrigeration_cycle",
    values: {mode: "refrigerator", energy_unit: "kW", T_cold_K: -18 + C, T_hot_K: 25 + C, Q_cold: .3}, expect: "INSUFFICIENT DATA"},
  {model: 6, title: "Balanced water–water exchanger", featured: true, note: "62.7 kW each side; S_gen > 0.", form: "finite_stream_heat_recovery",
    values: {hot_stream: water(.5, 90, 60), cold_stream: water(1, 20, 35)}, expect: "SECOND-LAW ADMISSIBLE"},
  {model: 6, title: "Oil–water exchanger", note: "Oil loses 200 kW, water gains 188.1 kW.", form: "finite_stream_heat_recovery",
    values: {hot_stream: {mass_flow_kg_s: 2, T_in_K: 150 + C, T_out_K: 100 + C, cp_kj_kg_k: 2.0}, cold_stream: water(1.5, 25, 55)},
    expect: "NOT FEASIBLE"},
  {model: 6, title: "Temperature cross", featured: true, note: "Cold water leaves at 95 °C, above the 90 °C hot inlet.", form: "finite_stream_heat_recovery",
    values: {hot_stream: water(1, 90, 40), cold_stream: water(2 / 3, 20, 95)}, expect: "NOT FEASIBLE"},
  {model: 6, title: "Energy imbalance", note: "167.2 kW released but only 83.6 kW absorbed.", form: "finite_stream_heat_recovery",
    values: {hot_stream: water(1, 90, 50), cold_stream: water(1, 20, 40)}, expect: "NOT FEASIBLE"},
  {model: 6, title: "Missing outlet temperature", note: "The cold outlet is calculated from the energy balance.", form: "finite_stream_heat_recovery",
    values: {hot_stream: water(.5, 90, 60), cold_stream: water(1, 20)}, expect: "SECOND-LAW ADMISSIBLE"},
  {model: 7, title: "Walking generator", featured: true, note: "4 J per step at 120 steps/min; 5 W claimed.", form: "mechanical_steps",
    values: {recoverable_energy_per_event_j: 4, events_per_s: 2, W_out_kw: .005}, expect: "FEASIBLE"},
  {model: 7, title: "Walking — force × displacement", note: "700 N × 0.05 m × 2 steps/s = 70 W.", form: "mechanical_force",
    values: {force_N: 700, displacement_m: .05, events_per_s: 2, W_out_kw: .02}, expect: "FEASIBLE"},
  {model: 7, title: "Pedalling generator", note: "20 N·m at 10 rad/s = 200 W; 120 W claimed.", form: "mechanical_torque",
    values: {torque_Nm: 20, angular_velocity_rad_s: 10, W_out_kw: .12}, expect: "FEASIBLE"},
  {model: 7, title: "Wind turbine", featured: true, note: "10 m², 8 m/s: Betz limit 1858 W.", form: "mechanical_wind",
    values: {air_density_kg_m3: 1.225, swept_area_m2: 10, wind_speed_m_s: 8, W_out_kw: 1.5}, expect: "FEASIBLE"},
  {model: 7, title: "Hydro turbine", note: "0.02 m³/s through 10 m: 1962 W ideal.", form: "mechanical_hydro",
    values: {head_m: 10, volumetric_flow_m3_s: .02, water_density_kg_m3: 1000, g_m_s2: 9.81, W_out_kw: 1.5}, expect: "FEASIBLE"},
  {model: 7, title: "No-input claim", note: "500 W with no energy input at all.", form: "mechanical_power",
    values: {recoverable_mechanical_power_kw: 0, W_out_kw: .5}, expect: "NOT FEASIBLE"},
  {model: 7, title: "Large wind turbine (described in words)", note: "40 m rotor in 10 m/s wind claims 800 kW.",
    text: "A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW.", expect: "NOT FEASIBLE"},
  {model: 8, title: "Normal solar panel", featured: true, note: "500 W from 2 m² at 1000 W/m².", form: "solar_blackbody",
    values: {T_source_K: 5778, T_ambient_K: 300, irradiance_W_m2: 1000, collection_area_m2: 2, W_out_kw: .5}, expect: "FEASIBLE"},
  {model: 8, title: "Output above the exergy limit", note: "1900 W claimed; limit ≈ 1862 W.", form: "solar_blackbody",
    values: {T_source_K: 5778, T_ambient_K: 300, irradiance_W_m2: 1000, collection_area_m2: 2, W_out_kw: 1.9}, expect: "NOT FEASIBLE"},
  {model: 8, title: "Different collector area", note: "5 m² at 800 W/m², 1 kW claimed.", form: "solar_blackbody",
    values: {T_source_K: 5778, T_ambient_K: 300, irradiance_W_m2: 800, collection_area_m2: 5, W_out_kw: 1}, expect: "FEASIBLE"},
  {model: 8, title: "Zero irradiance", featured: true, note: "No sunlight but 100 W claimed.", form: "solar_blackbody",
    values: {T_source_K: 5778, T_ambient_K: 300, irradiance_W_m2: 0, collection_area_m2: 2, W_out_kw: .1}, expect: "NOT FEASIBLE"},
  {model: 9, title: "Hydrogen fuel cell", featured: true, note: "0.01 mol/s: 2.37 kW limit, 2 kW claimed.", form: "reaction_gibbs",
    values: {reaction_direction: "H₂ + ½O₂ → H₂O(l)", delta_G_kj_per_mol_rxn: -237.141, reaction_rate_mol_rxn_s: .01, W_out_kw: 2}, expect: "FEASIBLE"},
  {model: 9, title: "Methanol fuel cell (described in words)", note: "0.5 g/s of methanol, 8 kW claimed.",
    text: "A methanol fuel cell uses 0.5 g/s of methanol and delivers 8 kW.", expect: "FEASIBLE"},
  {model: 9, title: "Methane fuel cell", note: "ΔG° = −817.9 kJ/mol, 0.002 mol/s: 1.64 kW limit.", form: "reaction_gibbs",
    values: {reaction_direction: "CH₄ + 2O₂ → CO₂ + 2H₂O(l)", delta_G_kj_per_mol_rxn: -817.9, reaction_rate_mol_rxn_s: .002, W_out_kw: 1.2},
    expect: "FEASIBLE"},
  {model: 9, title: "Methanol fuel cell", note: "ΔG° = −702.4 kJ/mol, 0.001 mol/s: 0.70 kW < 0.8 kW claimed.", form: "reaction_gibbs",
    values: {reaction_direction: "CH₃OH + 3/2O₂ → CO₂ + 2H₂O(l)", delta_G_kj_per_mol_rxn: -702.4, reaction_rate_mol_rxn_s: .001, W_out_kw: .8},
    expect: "NOT FEASIBLE"},
  {model: 9, title: "Reversible limit", note: "Exactly −ΔG × rate is claimed.", form: "reaction_gibbs",
    values: {reaction_direction: "H₂ + ½O₂ → H₂O(l)", delta_G_kj_per_mol_rxn: -237.141, reaction_rate_mol_rxn_s: .01, W_out_kw: 2.37141},
    expect: "FEASIBLE"},
  {model: 9, title: "Gibbs-energy sign error", featured: true, note: "ΔG > 0: the reaction needs work, it cannot give 1 kW.", form: "reaction_gibbs",
    values: {reaction_direction: "H₂ + ½O₂ → H₂O(l)", delta_G_kj_per_mol_rxn: 237.141, reaction_rate_mol_rxn_s: .01, W_out_kw: 1},
    expect: "NOT FEASIBLE"},
];
const engineLabels = {
  claimed_electrical_output_kw: "Claimed electrical power (kW)", reversible_power_limit_kw: "Ideal power limit (kW)",
  reversible_work_limit_kw: "Carnot work limit (kW)", S_gen_dot_kw_per_k: "Entropy generation (kW/K)", S_gen_kj_per_k: "Entropy generation (kJ/K)",
  petela_landsberg_exergy_fraction: "Radiation exergy fraction", incident_radiation_kw: "Incident radiation (kW)",
  strict_reversible_work_limit_kw: "Exact reversible work limit W_max (kW)", exergy_destroyed_kw: "Exergy destroyed (kW)",
  heat_released_by_hot_stream_kw: "Heat released by hot stream (kW)", heat_absorbed_by_cold_stream_kw: "Heat absorbed by cold stream (kW)",
  energy_balance_residual_kw: "Energy mismatch (kW)", governing_equations: "Governing equations", calculation_steps: "Calculation steps",
  COP_refrigerator: "COP (refrigerator)", COP_heat_pump: "COP (heat pump)", carnot_COP_refrigerator: "Carnot COP (refrigerator)",
  carnot_COP_heat_pump: "Carnot COP (heat pump)", minimum_work_input: "Minimum (reversible) work input",
  Q_cold: "Heat removed from cold side Q_C", Q_hot: "Heat delivered to hot side Q_H", W_in: "Work input W_in",
  cold_outlet_from_first_law_K: "Cold outlet from First Law (K)", reversible_cold_outlet_temperature_K: "Reversible cold outlet (K)",
  delta_H_total_kj: "Total ΔH (kJ)", delta_S_total_kj_per_k: "Total ΔS (kJ/K)", U2_kj: "Final internal energy U₂ (kJ)",
  W_shaft_kw: "Shaft work (kW)", mechanical_input_basis: "Mechanical input basis", betz_limit_kw: "Betz limit (kW)",
  wind_kinetic_power_kw: "Kinetic power of the wind (kW)", hydraulic_power_kw: "Ideal hydraulic power (kW)",
};

let revision = 0;
const fromFile = location.protocol === "file:";
const hosted = location.hostname?.endsWith(".github.io") === true;
let browserWorker;
let browserRequest = 0;

function element(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

function makeFields(blank = false) {
  const model = models[$("model").value];
  $("fields").replaceChildren(...model.fields.map(([key, label, value, min, max]) => {
    const group = element("label", label);
    if (Array.isArray(value)) {                     // a choice, e.g. refrigerator / heat pump
      const select = element("select");
      select.name = key;
      for (const [optionValue, optionLabel] of value) {
        const option = element("option", optionLabel);
        option.value = optionValue;
        select.append(option);
      }
      select.value = value[0][0];
      group.append(select);
      return group;
    }
    const input = element("input");
    input.name = key;
    input.value = blank ? "" : value;
    input.placeholder = value === "" ? "optional" : String(value);
    const optional = value === "";
    input.required = !optional;
    if (typeof value === "number" || optional) {
      input.type = "number";
      input.step = key === "suffix" ? "1" : "any";
      if (min !== undefined) input.min = min;
      if (max !== undefined) input.max = max;
    } else input.maxLength = 300;
    group.append(input);
    return group;
  }));
  $("model-note").textContent = model.note;
}

function setStatus(label, type = "") {
  $("status").textContent = label;
  $("status").className = "badge " + type;
}

function clearOutput() {
  revision++;
  setStatus("Ready");
  const empty = element("div", undefined, "empty");
  empty.append(element("span", "◎", "empty-icon"), element("h3", "Your results appear here"),
    element("p", "Run a scenario to compare claimed output with the ideal limit and see which checks apply."));
  $("result").replaceChildren(empty);
}

function setMode(isText, preserveResult = false) {
  $("text-form").hidden = !isText;
  $("model-form").hidden = isText;
  $("text-tab").setAttribute("aria-pressed", isText);
  $("model-tab").setAttribute("aria-pressed", !isText);
  if (!preserveResult) clearOutput();
}

const formKey = {cv_m: (v) => v.inlets?.[0]?.mass_flow_kg_s, cv_h_in: (v) => v.inlets?.[0]?.specific_enthalpy_kj_kg,
  cv_s_in: (v) => v.inlets?.[0]?.specific_entropy_kj_kg_k, cv_h_out: (v) => v.outlets?.[0]?.specific_enthalpy_kj_kg,
  cv_s_out: (v) => v.outlets?.[0]?.specific_entropy_kj_kg_k, cv_q: (v) => v.heat?.[0]?.heat_into_cv_kw,
  cv_tb: (v) => v.heat?.[0]?.boundary_temperature_K};

function fillValues(route, values) {
  if (!models[route]) return;
  $("model").value = route;
  makeFields(true);                                   // always start from empty fields: no stale values
  for (const [key, , , , , scale = 1, offset = 0] of models[route].fields) {
    let value = key in values ? values[key] : key.split(".").reduce((item, part) => item?.[part], values);
    if ((value === undefined || value === null) && formKey[key]) value = formKey[key](values);
    if (value === undefined || value === null) continue;
    const input = [...$("fields").querySelectorAll("input, select")].find((item) => item.name === key);
    if (input) input.value = typeof value === "number" ? Number(((value - offset) / scale).toPrecision(12)) : value;
  }
}

async function runEngine(request) {
  if (fromFile) throw new Error("Start the site with python website/serve.py, then open http://127.0.0.1:8000.");
  if (hosted) {
    browserWorker ??= new Worker("engine-worker.js?v=20261119-visuals");
    const worker = browserWorker;
    const id = ++browserRequest;
    return new Promise((resolve, reject) => {
      const finish = (action, value) => {
        clearTimeout(timer);
        worker.removeEventListener("message", receive);
        worker.removeEventListener("error", failed);
        action(value);
      };
      const receive = ({data}) => { if (data.id === id) finish(resolve, data.result); };
      const failed = () => {
        worker.terminate();
        if (browserWorker === worker) browserWorker = null;
        finish(reject, new Error("The browser Python engine did not start. Check your connection and retry."));
      };
      const timer = setTimeout(failed, 90000);
      worker.addEventListener("message", receive);
      worker.addEventListener("error", failed);
      worker.postMessage({id, request});
    });
  }
  const response = await fetch("./api/solve", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(request), signal: AbortSignal.timeout(10000)});
  if (!response.headers.get("content-type")?.includes("application/json")) {
    throw new Error("Open the site using python website/serve.py. This page needs the local calculation server.");
  }
  return response.json();
}

// ---------------------------------------------------------------------------
// Result visuals (energy path, balance closure, entropy-generation gauge, temperature profile) and the
// sensitivity sweep. They only display values the engine already calculated; nothing is recomputed here.
// ---------------------------------------------------------------------------
function outputValues(result, cases) {
  const calc = result.calculations || result;
  const input = Object.values(cases).at(-1);
  const claimed = calc["Claimed electrical output (kW)"] ?? calc.claimed_electrical_output_kw ?? input?.W_out_kw;
  const limit = calc["Strict reversible power ceiling (kW)"] ?? calc["Reversible power ceiling (kW)"]
    ?? calc.reversible_power_limit_kw ?? calc.reversible_work_limit_kw ?? calc.strict_reversible_work_limit_kw;
  return { calc, claimed, limit };
}

// Floating-point noise (e.g. 2.8e-17 kW) is shown as 0, as in the engine's checks table.
const clean = (value) => (Math.abs(value) < 1e-9 ? 0 : value);

function analysisVisualizations(result, cases, route) {
  const calc = result.calculations || result;
  const input = result.inputs || Object.values(cases || {}).at(-1) || {};
  const cards = [];
  const card = (title, description) => {
    const box = element("article", undefined, "viz-card");
    box.append(element("h4", title), element("p", description));
    return box;
  };
  const energyRows = [];
  const addEnergy = (label, value, kind = "") => {
    if (Number.isFinite(value)) energyRows.push({label, value: clean(value), kind});
  };

  if (route === "fixed_reservoir_engine") {
    addEnergy("Heat supplied", input.Q_hot_kw, "source");
    addEnergy("Claimed work", input.W_out_kw, "work");
    addEnergy("Heat rejected", calc.Q_cold_rejected_kw ?? input.Q_cold_rejected_kw, "sink");
  } else if (route === "finite_stream_engine") {
    addEnergy("Hot stream releases", calc.heat_released_by_hot_stream_kw, "source");
    addEnergy("Work assessed", calc.work_used_for_assessment_kw, "work");
    addEnergy("Cold stream absorbs", calc.heat_absorbed_by_cold_stream_kw, "sink");
  } else if (route === "finite_stream_heat_recovery") {
    addEnergy("Hot-side release", calc.heat_released_by_hot_stream_kw, "source");
    addEnergy("Cold-side gain", calc.heat_absorbed_by_cold_stream_kw, "sink");
    addEnergy("Balance residual", calc.energy_balance_residual_kw, "work");
  } else if (route === "mechanical_power") {
    addEnergy("Recoverable input / ceiling", calc.reversible_power_limit_kw, "source");
    addEnergy("Claimed electrical output", calc.claimed_electrical_output_kw, "work");
    addEnergy("Power remaining", calc.power_remaining_after_electrical_output_kw, "sink");
  } else if (route === "solar_blackbody") {
    addEnergy("Incident radiation", calc.incident_radiation_kw, "source");
    addEnergy("Ideal work ceiling", calc.reversible_power_limit_kw, "limit");
    addEnergy("Claimed electrical output", calc.claimed_electrical_output_kw, "work");
  } else if (route === "reaction_gibbs") {
    addEnergy("Reversible output ceiling", calc.reversible_power_limit_kw, "limit");
    addEnergy("Claimed electrical output", calc.claimed_electrical_output_kw, "work");
    addEnergy("External work required", calc.minimum_external_work_input_kw, "sink");
  } else if (route === "refrigeration_cycle") {
    addEnergy("Heat absorbed from cold side Q_C", calc.Q_cold, "source");
    addEnergy("Work supplied W_in", calc.W_in, "work");
    addEnergy("Heat delivered to hot side Q_H", calc.Q_hot, "sink");
  } else if (route === "steady_control_volume") {
    // Enthalpy carried in and out by the streams (ṁ·h of the given states), the shaft work and any heat.
    const enthalpyFlow = (streams) => (streams || []).reduce((sum, s) => sum + s.mass_flow_kg_s * s.specific_enthalpy_kj_kg, 0);
    addEnergy("Enthalpy drop ṁ(h_in − h_out)", enthalpyFlow(input.inlets) - enthalpyFlow(input.outlets), "source");
    addEnergy(input.W_out_kw < 0 ? "Shaft work in (negative = consumed)" : "Shaft work out", input.W_out_kw, "work");
    for (const [index, item] of (input.heat || []).entries()) addEnergy("Heat into the device " + (index + 1), item.heat_into_cv_kw, "sink");
  } else if (route === "closed_system") {
    addEnergy("System energy change", input.delta_E_kj, "source");
    addEnergy("Work out", input.W_out_kj, "work");
    for (const [index, interaction] of (input.heat || []).entries()) {
      addEnergy("Heat interaction " + (index + 1), interaction.heat_into_system_kj, "sink");
    }
  }
  if (!energyRows.length) {
    const output = outputValues(result, cases || {});
    addEnergy("Model work ceiling", output.limit, "limit");
    addEnergy("Claimed output", output.claimed, "work");
  }

  if (energyRows.length) {
    const units = route === "closed_system" ? "kJ" : "kW";
    const maximum = Math.max(...energyRows.map((row) => Math.abs(row.value)), Number.MIN_VALUE);
    const displayScale = units === "kW" && maximum < 1 ? 1000 : 1;
    const displayUnits = displayScale === 1000 ? "W" : units;
    const energyCard = card("Energy path", "Model outputs on a shared scale (" + displayUnits + "). Values come from the selected model's calculated terms.");
    for (const row of energyRows) {
      const line = element("div", undefined, "viz-row");
      const track = element("div", undefined, "viz-track");
      const bar = element("i", undefined, row.kind);
      bar.style.width = (100 * Math.abs(row.value) / maximum) + "%";
      track.append(bar);
      line.append(element("span", row.label, "viz-row-label"), track,
        element("strong", number.format(row.value * displayScale) + " " + displayUnits, "viz-row-value"));
      energyCard.append(line);
    }
    cards.push(energyCard);
  }

  const balanceVisual = (label, balance) => {
    if (!balance || typeof balance !== "object" || !Number.isFinite(balance.residual)) return;
    const checkCard = card(label + " closure", balance.equation || "Residual from the selected model's balance equation.");
    const status = element("strong", balance.passed ? "PASS" : "FAIL", "balance-viz-status " + (balance.passed ? "pass" : "fail"));
    const residual = element("div", "Residual: " + number.format(clean(balance.residual)) + (balance.units ? " " + balance.units : ""), "viz-row-value");
    checkCard.append(status, residual);
    cards.push(checkCard);
  };
  balanceVisual("First-Law", result.checks?.first_law);
  balanceVisual("Mass-balance", result.checks?.mass_balance);
  if (Number.isFinite(calc.first_law_residual_kw) && !result.checks?.first_law) {
    balanceVisual("First-Law", {residual: calc.first_law_residual_kw,
      units: "kW", passed: result.checks?.first_law_balance === "PASS",
      equation: "Mechanical input - electrical output - measured heat loss = 0"});
  }

  const entropyEntry = Object.entries(calc).find(([key, value]) => /^S_gen/.test(key) && typeof value === "number" && Number.isFinite(value));
  if (entropyEntry) {
    const [key, value] = entropyEntry;
    const units = key.includes("kw_per_k") ? "kW/K" : key.includes("kj_per_k") ? "kJ/K" : "model units";
    const entropyCard = card("Entropy generation", "Second Law check for the boundary and process represented by this model.");
    const gauge = element("div", undefined, "viz-gauge");
    const marker = element("i", undefined, value < 0 ? "negative" : "positive");
    const width = Math.min(48, Math.abs(value) / Math.max(Math.abs(value) * 1.2, 1e-12) * 48);
    marker.style.width = width + "%";
    marker.style.left = (value < 0 ? 50 - width : 50) + "%";
    gauge.append(marker);
    entropyCard.append(gauge);
    const status = value < -1e-8 ? "Negative: second-law violation" : value <= 1e-8 ? "Approximately reversible" : "Positive: irreversible, second-law admissible";
    entropyCard.append(element("div", "S_gen = " + number.format(clean(value)) + " " + units, "viz-row-value"),
      element("p", status, "sweep-note"), element("div", "Violation ← 0 → Admissible", "viz-scale"));
    cards.push(entropyCard);
  }

  const temperatures = [];
  const addTemp = (label, value) => { if (Number.isFinite(value) && value > 0) temperatures.push({label, value}); };
  if (route === "fixed_reservoir_engine") {
    addTemp("Hot reservoir", input.T_hot_K); addTemp("Cold reservoir", input.T_cold_K);
  } else if (route === "finite_stream_engine" || route === "finite_stream_heat_recovery") {
    for (const [side, label] of [["hot_stream", "Hot stream"], ["cold_stream", "Cold stream"]]) {
      const stream = input[side] || {};
      addTemp(label + " inlet", stream.T_in_K); addTemp(label + " outlet", stream.T_out_K);
    }
  } else if (route === "solar_blackbody") {
    addTemp("Radiation source", input.T_source_K); addTemp("Environment", input.T_ambient_K);
  } else if (route === "refrigeration_cycle") {
    addTemp("Cold side", input.T_cold_K); addTemp("Hot side", input.T_hot_K);
  } else if (route === "property_change") {
    addTemp("Initial state", input.T1_K); addTemp("Final state", input.T2_K);
  }
  if (temperatures.length > 1) {
    const maximum = Math.max(...temperatures.map((row) => row.value));
    const tempCard = card("Temperature profile", "Absolute temperatures from the supplied model inputs; labels also show °C for readability.");
    for (const row of temperatures) {
      const line = element("div", undefined, "viz-temp-row");
      const track = element("div", undefined, "viz-track");
      const bar = element("i", undefined, row.label.toLowerCase().includes("outlet") ? "sink" : "limit");
      bar.style.width = (100 * row.value / maximum) + "%";
      track.append(bar);
      line.append(element("span", row.label), track,
        element("strong", number.format(row.value) + " K / " + number.format(row.value - 273.15) + " °C"));
      tempCard.append(line);
    }
    cards.push(tempCard);
  }

  if (!cards.length) return null;
  const visuals = element("div", undefined, "analysis-visuals");
  visuals.setAttribute("aria-label", "Model result visualizations");
  visuals.append(...cards);
  return visuals;
}

function numericPaths(value, prefix = "", out = []) {
  for (const [key, item] of Object.entries(value || {})) {
    if (key === "model" || key === "suffix") continue;
    const path = prefix ? prefix + "." + key : key;
    if (typeof item === "number" && Number.isFinite(item)) out.push([path, item]);
    else if (item && typeof item === "object" && !Array.isArray(item)) numericPaths(item, path, out);
  }
  return out;
}

function getPath(value, path) { return path.split(".").reduce((item, key) => item?.[key], value); }
function setPath(value, path, next) {
  const keys = path.split(".");
  let item = value;
  for (const key of keys.slice(0, -1)) item = item[key];
  item[keys.at(-1)] = next;
}

function sensitivitySweep(baseCase) {
  const fields = numericPaths(baseCase);
  if (!fields.length) return null;
  const body = element("div");
  body.append(element("p", "Vary one numeric input and rerun the same thermodynamic model at each point. Values use the model's normalized engine units; this is a sensitivity check, not a new model." , "sweep-note"));
  const controls = element("div", undefined, "sweep-controls");
  const choose = element("select");
  for (const [path, value] of fields) {
    const option = element("option", path.replaceAll("_", " ") + " (current " + number.format(value) + ")");
    option.value = path; choose.append(option);
  }
  const input = (label, initial, min, max) => {
    const box = element("label", label);
    const field = element("input"); field.type = "number"; field.step = "any"; field.value = String(initial);
    if (min !== undefined) field.min = String(min);
    if (max !== undefined) field.max = String(max);
    box.append(field); return {box, field};
  };
  const parameterLabel = element("label", "Input to vary"); parameterLabel.append(choose);
  const low = input("Start", fields[0][1] * .8);
  const high = input("End", fields[0][1] * 1.2);
  const count = input("Points (3 to 7)", 5, 3, 7); count.field.step = "1";
  for (const control of [parameterLabel, low.box, high.box, count.box]) controls.append(control);
  body.append(controls);
  const run = element("button", "Run sensitivity sweep", "quiet"); run.type = "button";
  const result = element("div", undefined, "sweep-output");
  const updateRange = () => {
    const current = getPath(baseCase, choose.value);
    const delta = Math.max(Math.abs(current) * .2, current === 0 ? 1 : Number.MIN_VALUE);
    low.field.value = String(Number((current - delta).toPrecision(8)));
    high.field.value = String(Number((current + delta).toPrecision(8)));
  };
  choose.addEventListener("change", updateRange);
  run.addEventListener("click", async () => {
    const start = Number(low.field.value), end = Number(high.field.value);
    const points = Math.max(3, Math.min(7, Math.round(Number(count.field.value) || 5)));
    if (!Number.isFinite(start) || !Number.isFinite(end) || start === end) {
      result.replaceChildren(element("p", "Enter two different finite values for the sweep range.", "sweep-note")); return;
    }
    run.disabled = true; run.textContent = "Running model cases...";
    result.replaceChildren(element("p", "Recalculating the selected model at " + points + " input values...", "sweep-note"));
    const rows = [];
    try {
      for (let index = 0; index < points; index++) {
        const value = start + (end - start) * index / (points - 1);
        const testCase = structuredClone(baseCase); setPath(testCase, choose.value, value);
        const response = await runEngine({case: testCase});
        if (response.error) throw new Error(response.error);
        const code = response.result?.verdict?.status || response.result?.Verdict || "MODEL REQUIRED";
        const isRejected = String(code).startsWith("NOT FEASIBLE");
        const isFeasible = code === "FEASIBLE / NOT REJECTED" || code === "SECOND-LAW ADMISSIBLE / NOT REJECTED";
        const outputs = outputValues(response.result, response.cases);
        rows.push({value, code, isRejected, isFeasible, claimed: outputs.claimed, limit: outputs.limit});
      }
      const plotted = rows.filter((row) => Number.isFinite(row.claimed) && Number.isFinite(row.limit));
      const maxPower = Math.max(...plotted.flatMap((row) => [Math.abs(row.claimed), Math.abs(row.limit)]), Number.MIN_VALUE);
      const plot = element("div", undefined, "sweep-plot");
      for (const row of plotted) {
        const group = element("div", undefined, "sweep-plot-point");
        group.append(element("span", number.format(row.value), "sweep-plot-value"));
        for (const [label, amount, kind] of [["Claim", row.claimed, "claim"], ["Limit", row.limit, "limit"]]) {
          const barRow = element("div", undefined, "sweep-plot-bar-row");
          const track = element("div", undefined, "sweep-plot-track");
          const bar = element("i", undefined, kind);
          bar.style.width = (100 * Math.abs(amount) / maxPower) + "%";
          track.append(bar);
          barRow.append(element("span", label), track, element("span", number.format(amount)));
          group.append(barRow);
        }
        plot.append(group);
      }
      const table = element("table", undefined, "sweep-table");
      const thead = element("tr");
      for (const label of [choose.value.replaceAll("_", " ") + " (engine units)", "Verdict", "Claim (kW)", "Limit (kW)"]) thead.append(element("th", label));
      table.append(thead);
      for (const row of rows) {
        const tr = element("tr");
        tr.append(element("td", number.format(row.value)));
        const status = element("td", row.isRejected ? "Not feasible" : row.isFeasible ? "Feasible" : row.code, "sweep-status " + (row.isRejected ? "fail" : row.isFeasible ? "pass" : "wait"));
        tr.append(status, element("td", Number.isFinite(row.claimed) ? number.format(row.claimed) : "—"), element("td", Number.isFinite(row.limit) ? number.format(row.limit) : "—"));
        table.append(tr);
      }
      result.replaceChildren(...(plotted.length ? [plot] : []), table);
    } catch (error) {
      result.replaceChildren(element("p", "Sweep stopped: " + error.message, "sweep-note"));
    } finally { run.disabled = false; run.textContent = "Run sensitivity sweep"; }
  });
  body.append(run, result);
  return details("Explore how an input changes the verdict", body);
}

// The two numbers worth comparing at a glance, chosen per model.
function headline(result, cases, route) {
  const calc = result.calculations || {};
  if (route === "refrigeration_cycle") {
    const hp = result.inputs?.mode === "heat_pump";
    const cop = hp ? calc.COP_heat_pump : calc.COP_refrigerator;
    const max = hp ? calc.carnot_COP_heat_pump : calc.carnot_COP_refrigerator;
    return {labels: ["Claimed COP", "Carnot COP"], claimed: cop, limit: max, unit: ""};
  }
  if (route === "finite_stream_heat_recovery") {
    return {labels: ["Heat absorbed", "Heat released"], claimed: calc.heat_absorbed_by_cold_stream_kw,
      limit: calc.heat_released_by_hot_stream_kw, unit: "kW"};
  }
  const input = Object.values(cases || {}).at(-1);
  const claimed = calc["Claimed electrical output (kW)"] ?? calc.claimed_electrical_output_kw ?? calc.work_used_for_assessment_kw
    ?? (route === "fixed_reservoir_engine" ? input?.W_out_kw : undefined);
  const limit = calc["Strict reversible power ceiling (kW)"] ?? calc["Reversible power ceiling (kW)"]
    ?? calc.reversible_power_limit_kw ?? calc.strict_reversible_work_limit_kw ?? calc.reversible_work_limit_kw;
  return {labels: ["Claimed output", "Ideal ceiling"], claimed, limit, unit: "kW"};
}

function formatNumber(value) {
  return typeof value === "number" ? number.format(value) : value;
}

// Any engine value as readable elements (never a raw JSON string).
function renderValue(value) {
  if (value === null || value === undefined) return element("span", "—", "muted");
  if (Array.isArray(value)) {
    const list = element("ul", undefined, "value-list");
    for (const item of value) {
      const li = element("li");
      li.append(renderValue(item));
      list.append(li);
    }
    return list;
  }
  if (typeof value === "object") return dataList(value);
  if (typeof value === "boolean") return element("span", value ? "Yes" : "No");
  return element("span", String(formatNumber(value)));
}

function details(title, body) {
  const section = element("details");
  section.append(element("summary", title), body);
  return section;
}

function dataList(data) {
  const list = element("dl");
  for (const [name, value] of Object.entries(data)) {
    const dd = element("dd");
    dd.append(renderValue(value));
    list.append(element("dt", engineLabels[name] || name.replaceAll("_", " ")), dd);
  }
  return list;
}

const statusClass = {"PASS": "pass", "FAIL": "fail", "NOT EVALUATED": "wait", "NOT APPLICABLE": "na"};

// Rows for results that carry no engine check table (older result formats).
function rowsFromChecks(checks) {
  return Object.entries(checks || {}).filter(([key]) => !["model_selection_guidance", "model_number"].includes(key))
    .map(([key, value]) => {
      const label = key.replaceAll("_", " ");
      if (value && typeof value === "object" && "residual" in value) {
        return {check: label, equation: value.equation, value: `Residual = ${formatNumber(value.residual)} ${value.units}`,
          criterion: "Residual ≈ 0", status: value.passed ? "PASS" : "FAIL"};
      }
      const text = String(value);
      const status = ["PASS", "FAIL", "NOT EVALUATED", "NOT APPLICABLE"].find((s) => text.toUpperCase().startsWith(s)) || "";
      return {check: label, equation: "", value: status ? text.slice(status.length).replace(/^[\s:—-]+/, "") || "—" : text,
        criterion: "", status: status || "NOTE"};
    });
}

function checksTable(result) {
  const rows = result.check_table?.length ? result.check_table : rowsFromChecks(result.checks);
  const wrap = element("div", undefined, "table-wrap");
  const table = element("table", undefined, "checks");
  const head = element("tr");
  for (const label of ["Check", "Calculated result", "Criterion", "Status"]) head.append(element("th", label));
  table.append(head);
  for (const row of rows) {
    const tr = element("tr");
    const name = element("td");
    name.append(element("strong", row.check));
    if (row.equation && row.equation !== "—") name.append(element("code", row.equation, "equation"));
    const status = element("td");
    status.append(element("span", row.status, "pill " + (statusClass[row.status] || "na")));
    tr.append(name, element("td", row.value || "—"), element("td", row.criterion || "—"), status);
    table.append(tr);
  }
  wrap.append(table);
  return wrap;
}

const domainNames = {
  thermal: "Thermal — heat flows from a source to a sink", mechanical: "Mechanical — recoverable work",
  wind: "Wind — kinetic energy (Betz limit)", hydro: "Hydro — falling water (ρgQH limit)",
  solar: "Solar — radiation (Petela–Landsberg limit)", chemical: "Chemical — Gibbs energy limit",
  work_consuming: "Refrigerator / heat pump (consumes work)", no_input: "No energy input (perpetual-motion claim)",
  unsupported: "No validated model (model required)", unknown: "Not recognised yet",
};

function listBlock(title, items, className) {
  const box = element("div", undefined, "interp-block " + (className || ""));
  box.append(element("h4", title));
  const list = element("ul");
  for (const item of items) list.append(element("li", item));
  box.append(list);
  return box;
}

function interpretation(analysis, open = true) {
  const body = element("div", undefined, "interp");
  const head = element("p", undefined, "interp-model");
  head.append(element("strong", analysis.model_label || domainNames[analysis.domain] || analysis.domain));
  if (analysis.title) head.append(element("span", " · " + analysis.title));
  body.append(head);
  if (analysis.reason?.length) body.append(listBlock("Why this model", analysis.reason, "why"));
  if (analysis.extracted?.length) {
    const table = element("table", undefined, "extracted");
    const headRow = element("tr");
    for (const label of ["Quantity", "Value used", "Found in text"]) headRow.append(element("th", label));
    table.append(headRow);
    for (const item of analysis.extracted) {
      const row = element("tr");
      const value = typeof item.value === "number" ? number.format(item.value) + (item.unit ? " " + item.unit : "") : String(item.value);
      row.append(element("td", item.quantity), element("td", value), element("td", "“" + item.quote + "”"));
      table.append(row);
    }
    const box = element("div", undefined, "interp-block");
    const wrap = element("div", undefined, "table-wrap");
    wrap.append(table);
    box.append(element("h4", "1 · Extracted from your description"), wrap);
    body.append(box);
  }
  if (analysis.mapping?.length) body.append(listBlock("2 · Source, sink and test selected", analysis.mapping));
  if (analysis.inferred?.length) body.append(listBlock("3 · Calculated from the extracted values", analysis.inferred));
  if (analysis.assumptions?.length) body.append(listBlock("Assumptions made (check these)", analysis.assumptions, "assume"));
  if (analysis.issues?.length) body.append(listBlock("Notes", analysis.issues, "assume"));
  const section = details("How the scenario was interpreted", body);
  section.open = open;
  section.className = "interp-details";
  return section;
}

function verdictBadge(code, route) {
  if (typeof code !== "string") return ["Model required", "wait"];
  if (code.startsWith("NOT FEASIBLE")) return ["Not feasible", "fail"];
  if (code.startsWith("FEASIBLE") || code.startsWith("SECOND-LAW ADMISSIBLE")) return ["Feasible", "pass"];
  if (code.startsWith("LIMIT CALCULATED")) return ["Limit calculated", "info"];
  if (code === "PROPERTY CALCULATION ONLY") return ["Properties only", "info"];
  return route === "unsupported_domain" ? ["Model required", "wait"] : ["Insufficient data", "wait"];
}

function render(data) {
  const output = $("result");
  output.replaceChildren();
  if (data.error) {
    setStatus("Check input", "fail");
    const message = element("div", undefined, "verdict");
    message.append(element("h3", "Could not run the calculation"), element("p", data.error));
    output.append(message);
    if (data.analysis) output.append(interpretation(data.analysis));
    return;
  }
  if (data.status === "needs_values" && data.analysis) {
    setStatus("Needs info", "wait");
    const message = element("div", undefined, "verdict");
    if (data.model_label) message.append(element("p", data.model_label, "model-tag"));
    message.append(element("h3", "Almost there. Add the missing information"),
      element("p", "Add these details to your description in plain words, then click Analyze scenario again."));
    output.append(message);
    output.append(listBlock("Still needed", data.analysis.missing || [], "missing"));
    output.append(interpretation(data.analysis));
    const route = data.route;
    if (models[route]) {
      const manual = element("button", "Or enter the values in the form", "quiet");
      manual.type = "button";
      manual.addEventListener("click", () => {
        setMode(false, true);
        fillValues(route, data.prefill || {});
      });
      output.append(manual);
    }
    $("scenario").focus({preventScroll: true});
    return;
  }
  if (data.status === "needs_values") {
    const route = data.route;
    setMode(false);
    if (models[route]) $("model").value = route;
    makeFields(true);
    fillValues(route, data.prefill || {});
    setStatus("Enter values", "wait");
    const message = element("div", undefined, "verdict");
    message.append(element("h3", "Enter the numbers for your scenario"),
      element("p", models[route] ? "Suggested model: " + models[route].name + ". Review the model, fill in the labeled fields, and click Calculate result." : "Choose the model that matches your process, then enter its values."));
    output.replaceChildren(message);
    if (data.issues?.length) output.append(element("p", data.issues.join(" "), "form-note"));
    $("model").focus({preventScroll: true});
    return;
  }
  const { result, cases } = data;
  if (Object.keys(data.source_quotes || {}).length) {
    const populated = Object.values(cases).at(-1);
    let route = data.route;
    if (route === "mechanical_power" && populated?.events_per_s !== undefined) route = "mechanical_steps";
    fillValues(route, populated);
  }
  const code = result.verdict?.status || result.Verdict;
  const [badge, tone] = verdictBadge(code, data.route);
  setStatus(badge, tone);
  const verdict = element("div", undefined, "verdict");
  if (data.model_label) verdict.append(element("p", data.model_label, "model-tag"));
  verdict.append(element("h3", result.title || models[data.route]?.name || data.route.replaceAll("_", " ")));
  if (code) verdict.append(element("p", "Verdict: " + code, "verdict-code " + tone));
  const reason = result.verdict?.reasons?.[0] || result.Reason;
  if (reason) verdict.append(element("p", reason));
  const more = (result.verdict?.reasons || []).slice(1);
  if (more.length) {
    const reasons = element("ul", undefined, "reasons");
    for (const line of more) reasons.append(element("li", line));
    verdict.append(reasons);
  }
  output.append(verdict);
  if (data.analysis) output.append(interpretation(data.analysis));
  if (Object.keys(data.source_quotes || {}).length && models[data.route] && models[data.route].review !== false) {
    const review = element("button", "Review automatically filled values", "quiet");
    review.type = "button";
    review.addEventListener("click", () => setMode(false, true));
    output.append(review);
  }
  const { labels, claimed, limit, unit } = headline(result, cases, data.route);
  if (Number.isFinite(claimed) && Number.isFinite(limit)) {
    const watts = unit === "kW" && Math.max(Math.abs(claimed), Math.abs(limit)) < 1;
    const show = (value) => unit ? number.format(value * (watts ? 1000 : 1)) + (watts ? " W" : " " + unit) : number.format(value);
    const metrics = element("div", undefined, "metrics");
    for (const [label, value] of [[labels[0], claimed], [labels[1], limit]]) {
      const card = element("div", label, "metric");
      card.append(element("strong", show(value)));
      metrics.append(card);
    }
    output.append(metrics);
    const chart = element("div", undefined, "comparison");
    for (const [label, value] of [[labels[1], limit], [labels[0], claimed]]) {
      const row = element("div", undefined, "bar-row");
      const track = element("div", undefined, "track");
      const bar = element("i");
      bar.style.width = (100 * Math.max(0, value) / Math.max(claimed, limit, Number.MIN_VALUE)) + "%";
      track.append(bar);
      row.append(element("span", label), track, element("span", show(value)));
      chart.append(row);
    }
    output.append(chart);
  }
  const visualSummary = analysisVisualizations(result, cases, data.route);
  if (visualSummary) output.append(visualSummary);
  const sweepCase = Object.values(cases || {}).at(-1);
  if (sweepCase?.model && data.route !== "unsupported_domain") {
    const sweep = sensitivitySweep(sweepCase);
    if (sweep) output.append(sweep);
  }
  if (result.check_table?.length || result.checks) {
    const checks = details("Balance and limit checks", checksTable(result));
    checks.open = true;
    output.append(checks);
  }
  if (result.calculations) output.append(details("All calculated values", dataList(result.calculations)));
  if (result.input_status && Object.keys(result.input_status).length) {
    output.append(details("Inputs: given, calculated or missing", dataList(result.input_status)));
  }
  if (result.assumptions?.length) {
    const assumptions = element("ul");
    for (const assumption of result.assumptions) assumptions.append(element("li", assumption));
    output.append(details("Model assumptions", assumptions));
  }
  if (Object.keys(data.source_quotes || {}).length) output.append(details("What the tester extracted", dataList(data.source_quotes)));
  const jsonBody = element("div");
  const files = Object.entries(cases);
  jsonBody.append(element("p", "The exact input the engine received, for saving or re-running in the terminal program.", "form-note"),
    element("pre", JSON.stringify(files.length === 1 ? files[0][1] : cases, null, 2)));
  for (const [name, content] of files) {
    const download = element("button", files.length === 1 ? "Download case JSON" : "Download " + name.replaceAll("_", " "), "quiet");
    download.type = "button";
    download.addEventListener("click", () => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2)], {type: "application/json"}));
      const link = element("a");
      link.href = url;
      link.download = name + ".json";
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    jsonBody.append(download);
  }
  output.append(details("Engine input file (JSON)", jsonBody));
  if (data.calculation_code?.length) {
    const codeBody = element("div");
    codeBody.append(element("p", "These functions come from the Python analyzer and engine. The auto_analyzer functions read and map the description. The engine functions calculate the verdict."));
    for (const item of data.calculation_code) {
      codeBody.append(element("h4", item.name), element("pre", item.source));
    }
    for (const [file, label] of [["analyzer", "Download automatic scenario analyzer"], ["engine", "Download full calculation engine"],
      ["parser", "Download local scenario parser"]]) {
      const link = element("a", label, "quiet");
      const paths = {analyzer: "auto_analyzer.py", engine: "thermodynamic_feasibility_tester_MASTER.py",
        parser: "website/local_scenarios.py"};
      link.href = hosted ? "./python/" + paths[file] : "./api/source/" + file;
      link.download = "";
      codeBody.append(link);
    }
    output.append(details("View calculation code", codeBody));
  }
}

async function analyze(request) {
  const current = ++revision;
  const submits = document.querySelectorAll("form button[type=submit]");
  submits.forEach((button) => button.disabled = true);
  const originalLabels = [...submits].map((button) => button.innerHTML);
  submits.forEach((button) => button.textContent = "Analyzing…");
  setStatus("Calculating…", "wait");
  $("result").replaceChildren(element("p", hosted
    ? "Running Python in your browser. The first calculation downloads the engine and may take a moment."
    : "Running the thermodynamics engine…", "form-note"));
  try {
    const response = await runEngine(request);
    if (current === revision) render(response);
  } catch (error) {
    if (current === revision) {
      render({error: error.name === "TimeoutError" ? "The local server took too long to respond. Restart python website/serve.py and retry." : error.message});
    }
  } finally {
    submits.forEach((button, index) => { button.disabled = false; button.innerHTML = originalLabels[index]; });
  }
}

// Turn the model form into the engine case (unit conversion happens here, before any calculation).
function formCase() {
  const model = $("model").value;
  const spec = models[model];
  const inputs = new FormData($("model-form"));
  const caseData = {model: spec.engineModel || model};
  for (const [key, , value, , , scale = 1, offset = 0] of spec.fields) {
    const raw = inputs.get(key);
    if (value === "" && (raw === null || String(raw).trim() === "")) continue;
    if (raw === null || String(raw).trim() === "") return {error: "Fill in every labeled value before calculating."};
    const numeric = typeof value === "number" || value === "";
    const parsed = numeric ? Number(raw) * scale + offset : String(raw).trim();
    if (numeric && !Number.isFinite(parsed)) return {error: "Enter a number for “" + key + "”."};
    if (key.includes(".")) {
      const [group, field] = key.split(".");
      caseData[group] ??= {name: group.replace("_", " "), cp_model: "constant"};
      caseData[group][field] = parsed;
    } else caseData[key] = parsed;
  }
  if (model === "finite_stream_engine") {
    caseData.apply_effective_temperature_limit = caseData.W_out_kw !== undefined && caseData.cold_stream?.T_out_K !== undefined;
    caseData.apply_strict_reversible_limit = true;
  }
  return {case: spec.build ? spec.build(caseData) : caseData};
}

for (const [group, label] of [["model", "Thermodynamic models 1–9"]]) {
  const optgroup = element("optgroup");
  optgroup.label = label;
  const entries = Object.entries(models).filter(([, spec]) => spec.group === group)
    .sort(([, a], [, b]) => (a.number ?? 99) - (b.number ?? 99));
  for (const [id, spec] of entries) {
    const option = element("option", (spec.number ? `Model ${spec.number} · ` : "") + spec.name);
    option.value = id;
    optgroup.append(option);
  }
  $("model").append(optgroup);
}
if (!models[$("model").value]) $("model").value = "fixed_reservoir_engine";
makeFields(true);
function openModel(model) {
  setMode(false);
  $("model").value = model;
  makeFields();
  $("model").focus({ preventScroll: true });
  $("workbench").scrollIntoView();
}

// Load one example: switch to the right model, clear the form, fill it, and run it.
function loadExample(index) {
  const example = examples[index];
  goWorkbench();
  if (example.text) {
    setMode(true);
    $("scenario").value = example.text;
    $("workbench").scrollIntoView();
    return analyze({scenario: example.text});
  }
  setMode(false);
  fillValues(example.form, example.values);
  $("workbench").scrollIntoView();
  const built = formCase();
  return built.error ? render(built) : analyze(built);
}

// ---------------------------------------------------------------------------
// Example Library (modal): filter by technology; each category shows its representative examples first.
// ---------------------------------------------------------------------------
const library = {filter: "all", expanded: new Set()};

function exampleCard(example) {
  const index = examples.indexOf(example);
  const card = element("button", undefined, "example");
  card.type = "button";
  card.append(element("span", `MODEL ${example.model} · ${example.text ? "DESCRIBED IN WORDS" : "MODEL INPUTS"}`, "eyebrow"),
    element("strong", example.title), element("small", example.note));
  card.addEventListener("click", () => loadExample(index));
  return card;
}

function renderLibrary() {
  $("library-filters").replaceChildren(...[{key: "all", name: "All"}, ...categories].map((category) => {
    const chip = element("button", category.name, "chip" + (library.filter === category.key ? " active" : ""));
    chip.type = "button";
    chip.setAttribute("aria-pressed", library.filter === category.key);
    chip.addEventListener("click", () => { library.filter = category.key; renderLibrary(); });
    return chip;
  }));
  const shown = categories.filter((category) => library.filter === "all" || library.filter === category.key);
  $("example-list").replaceChildren(...shown.map((category) => {
    const all = examples.filter((example) => categoryOf(example) === category.key);
    const featured = all.filter((example) => example.featured);
    const open = library.expanded.has(category.key) || library.filter === category.key && all.length <= 2;
    const visible = open ? [...featured, ...all.filter((example) => !example.featured)] : featured;
    const group = element("section", undefined, "example-group");
    const models = [...new Set(all.map((example) => example.model))].map((n) => "Model " + n).join(" · ");
    const head = element("div", undefined, "group-head");
    head.append(element("h3", category.name), element("span", models, "group-models"));
    const list = element("div", undefined, "examples");
    list.append(...visible.map(exampleCard));
    group.append(head, list);
    if (all.length > featured.length) {
      const more = element("button", open ? "Show fewer" : `Show ${all.length - featured.length} more`, "quiet more");
      more.type = "button";
      more.setAttribute("aria-expanded", open);
      more.addEventListener("click", () => {
        library.expanded[open ? "delete" : "add"](category.key);
        renderLibrary();
      });
      group.append(more);
    }
    return group;
  }));
}

function openLibrary(category = "all") {
  library.filter = category;
  renderLibrary();
  $("library").hidden = false;
  $("library-close").focus?.();
}

function closeLibrary() {
  $("library").hidden = true;
}

function surprise() {
  const pool = examples.filter((example) => example.featured);
  return loadExample(examples.indexOf(pool[Math.floor(Math.random() * pool.length)]));
}

// Home page: exactly one representative example per model. "Try example" fills the form and stops there, so the
// values can be edited before calculating.
function tryExample(modelNumber) {
  const info = modelInfo[modelNumber];
  const example = examples.find((item) => item.title === info.example.title);
  goWorkbench();
  setMode(false);
  fillValues(example.form, example.values);       // clears the form first: no stale values from another model
  setStatus("Example loaded", "info");
  const note = element("div", undefined, "verdict");
  note.append(element("p", `Model ${modelNumber} · ${info.name}`, "model-tag"), element("h3", info.example.label),
    element("p", "The inputs are filled in on the left. Change any value, then click Calculate result."));
  $("result").replaceChildren(note);
  $("workbench").scrollIntoView();
  $("model").focus({preventScroll: true});
}

$("model-examples").replaceChildren(...Object.entries(modelInfo).map(([n, info]) => {
  const card = element("article", undefined, "model-example");
  const button = element("button", "Try example", "primary small");
  button.type = "button";
  button.addEventListener("click", () => tryExample(Number(n)));
  card.append(element("span", `MODEL ${n} · ${info.name.toUpperCase()}`, "eyebrow"), element("h3", info.example.label),
    element("p", info.example.description), button);
  return card;
}));

// Model reference table (documentation only — the runnable examples are the cards above and the library).
{
  const head = element("tr");
  for (const label of ["#", "Model", "Technologies and examples", "What it checks"]) head.append(element("th", label));
  const body = Object.entries(modelInfo).map(([n, info]) => {
    const row = element("tr");
    const name = element("td");
    name.append(element("strong", info.name));
    row.append(element("td", n, "num"), name, element("td", info.tech), element("td", info.checks, "eq"));
    return row;
  });
  $("models-table").replaceChildren(head, ...body);
}

function goWorkbench() {
  closeLibrary();
}

document.addEventListener?.("keydown", (event) => { if (event.key === "Escape" && !$("library").hidden) dismissLibrary(); });
function dismissLibrary() {
  closeLibrary();
}
$("library-close").addEventListener("click", dismissLibrary);
$("library-backdrop").addEventListener("click", dismissLibrary);
closeLibrary();                                       // the full library starts closed
$("open-library").addEventListener("click", () => openLibrary());
$("all-examples").addEventListener("click", () => openLibrary());
$("surprise").addEventListener("click", () => surprise());
$("text-tab").addEventListener("click", () => setMode(true));
$("model-tab").addEventListener("click", () => setMode(false));
$("model").addEventListener("change", () => { makeFields(true); clearOutput(); });
$("reset").addEventListener("click", () => { $("scenario").value = ""; clearOutput(); $("scenario").focus(); });
$("text-form").addEventListener("submit", (event) => {
  event.preventDefault();
  analyze({scenario: $("scenario").value.trim()});
});
$("model-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const built = formCase();
  if (built.error) return render(built);
  analyze(built);
});
$("interface-loading").hidden = true;
$("analyze-button").disabled = false;

// Light / dark theme: follows the system setting until the visitor chooses, then remembers the choice.
const themeToggle = $("theme-toggle");
const rootElement = document.documentElement;
if (themeToggle && rootElement) {
  let savedTheme = null;
  try { savedTheme = localStorage.getItem("thermolab-theme"); } catch (_) {}
  const prefersDark = globalThis.matchMedia?.("(prefers-color-scheme: dark)").matches;
  const applyTheme = (dark) => {
    rootElement.dataset.theme = dark ? "dark" : "light";
    themeToggle.textContent = dark ? "Light" : "Dark";
    themeToggle.setAttribute("aria-pressed", String(dark));
    themeToggle.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  };
  applyTheme(savedTheme ? savedTheme === "dark" : Boolean(prefersDark));
  themeToggle.addEventListener("click", () => {
    const dark = rootElement.dataset.theme !== "dark";
    applyTheme(dark);
    try { localStorage.setItem("thermolab-theme", dark ? "dark" : "light"); } catch (_) {}
  });
}
