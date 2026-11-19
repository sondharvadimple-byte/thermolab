"""Tests for the model-registry upgrade: renumbered models, refrigerator / heat pump, missing-value derivation,
heat-exchanger modes, routing from text, unit handling, invalid input and the structured check table."""

import unittest

import thermodynamic_feasibility_tester_MASTER as E
from auto_analyzer import analyze_scenario
from website.dist.solver import solve
from website_test_cases import CASES

C = 273.15


def water(m=None, tin=None, tout=None, cp=4.18):
    stream = {"name": "water", "cp_model": "constant", "cp_kj_kg_k": cp}
    stream.update({k: v for k, v in (("mass_flow_kg_s", m), ("T_in_K", tin), ("T_out_K", tout)) if v is not None})
    return stream


def status_of(response):
    return (response.get("result") or {}).get("verdict", {}).get("status") or response.get("status")


class RegistryTests(unittest.TestCase):
    def test_selectable_models_are_numbered_1_to_9(self):
        self.assertEqual([m["number"] for m in E.SELECTABLE_MODELS], list(range(1, 10)))
        keys = [m["key"] for m in E.SELECTABLE_MODELS]
        self.assertEqual(keys[4], "refrigeration_cycle")
        self.assertNotIn("property_change", keys)
        self.assertNotIn("unsupported_domain", keys)

    def test_stable_keys_still_run(self):
        # old saved cases keep working: the keys did not change, only the displayed numbers
        r = E.run_case({"model": "property_change", "T1_K": 300, "T2_K": 350, "cp_model": "constant", "cp_kj_kg_k": 4.18})
        self.assertEqual(r.verdict.status, "PROPERTY CALCULATION ONLY")
        self.assertEqual(E.model_label("finite_stream_heat_recovery"), "Model 6 · Heat exchanger (no shaft work)")


class RefrigerationTests(unittest.TestCase):
    def run_cycle(self, **kw):
        return E.run_case({"model": "refrigeration_cycle", **kw})

    def test_refrigerator_below_carnot_cop(self):
        r = self.run_cycle(mode="refrigerator", energy_unit="kJ", Q_cold=500, Q_hot=650, W_in=150, T_cold_K=270, T_hot_K=300)
        self.assertEqual(r.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(r.calculations["COP_refrigerator"], 500 / 150)
        self.assertAlmostEqual(r.calculations["carnot_COP_refrigerator"], 9.0)
        self.assertAlmostEqual(r.calculations["S_gen_kj_per_k"], 650 / 300 - 500 / 270)

    def test_refrigerator_above_carnot_cop_is_rejected(self):
        r = self.run_cycle(Q_cold=5, W_in=0.1, T_cold_K=260, T_hot_K=300)
        self.assertEqual(r.verdict.status, "NOT FEASIBLE")
        self.assertEqual(r.calculations["derived_from_first_law"], "Q_hot")

    def test_reversible_refrigerator_is_the_limit(self):
        w_min = 5 * (300 / 260 - 1)
        r = self.run_cycle(Q_cold=5, W_in=w_min, T_cold_K=260, T_hot_K=300)
        self.assertEqual(r.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(r.calculations["S_gen_dot_kw_per_k"], 0.0, places=9)

    def test_heat_pump_cop_and_derived_work(self):
        r = self.run_cycle(mode="heat_pump", Q_cold=5, Q_hot=7, T_cold_K=280, T_hot_K=300)
        self.assertAlmostEqual(r.calculations["W_in"], 2.0)
        self.assertAlmostEqual(r.calculations["COP_heat_pump"], 3.5)
        self.assertAlmostEqual(r.calculations["carnot_COP_heat_pump"], 15.0)
        self.assertEqual(r.input_status["W_in"], "DERIVED FROM FIRST LAW (Q_H = Q_C + W_in)")

    def test_first_law_violation(self):
        r = self.run_cycle(Q_cold=5, Q_hot=7, W_in=1, T_cold_K=260, T_hot_K=300)
        self.assertEqual(r.verdict.status, "NOT FEASIBLE")

    def test_missing_data_and_invalid_temperatures(self):
        self.assertEqual(self.run_cycle(Q_cold=5, T_cold_K=260, T_hot_K=300).verdict.status,
                         "INSUFFICIENT DATA / MODEL REQUIRED")
        with self.assertRaises(E.ThermoInputError):
            self.run_cycle(Q_cold=5, W_in=1, T_cold_K=300, T_hot_K=260)
        with self.assertRaises(E.ThermoInputError):
            self.run_cycle(Q_cold=-5, W_in=1, T_cold_K=260, T_hot_K=300)


class MissingValueTests(unittest.TestCase):
    def test_model2_cold_outlet_from_first_law_and_limit_only(self):
        hot, cold = water(1, 90 + C, 60 + C), water(1, 20 + C)
        limit = E.run_case({"model": "finite_stream_engine", "hot_stream": hot, "cold_stream": cold})
        self.assertEqual(limit.verdict.status, "LIMIT CALCULATED — NO CLAIM TO TEST")
        w_max = limit.calculations["strict_reversible_work_limit_kw"]
        self.assertAlmostEqual(w_max, 15.056, places=2)
        below = E.run_case({"model": "finite_stream_engine", "hot_stream": hot, "cold_stream": cold, "W_out_kw": w_max - 0.01})
        above = E.run_case({"model": "finite_stream_engine", "hot_stream": hot, "cold_stream": cold, "W_out_kw": w_max + 0.01})
        self.assertEqual(below.verdict.status, "FEASIBLE / NOT REJECTED")
        self.assertEqual(above.verdict.status, "NOT FEASIBLE")

    def test_steady_cv_work_derived_and_partial_data(self):
        cv = {"model": "steady_control_volume",
              "inlets": [{"mass_flow_kg_s": 2, "specific_enthalpy_kj_kg": 3200, "specific_entropy_kj_kg_k": 6.8}],
              "outlets": [{"mass_flow_kg_s": 2, "specific_enthalpy_kj_kg": 2600, "specific_entropy_kj_kg_k": 7.1}]}
        r = E.run_case(cv)
        self.assertAlmostEqual(r.inputs["W_out_kw"], 1200)
        self.assertEqual(r.verdict.status, "FEASIBLE / NOT REJECTED")
        mass_only = E.run_case({"model": "steady_control_volume", "inlets": [{"mass_flow_kg_s": 2}],
                                "outlets": [{"mass_flow_kg_s": 1.5}]})
        self.assertEqual(mass_only.verdict.status, "NOT FEASIBLE")

    def test_closed_system_derives_delta_e_and_final_energy(self):
        r = E.run_case({"model": "closed_system", "heat": [{"heat_into_system_kj": 100}], "W_out_kj": 60, "U1_kj": 500})
        self.assertAlmostEqual(r.calculations["U2_kj"], 540)
        self.assertEqual(r.verdict.status, "INSUFFICIENT DATA / MODEL REQUIRED")   # no ΔS: Second Law unknown
        self.assertIn("Second Law", r.verdict.reasons[0])

    def test_mechanical_inputs_from_force_torque_wind_hydro(self):
        calc = lambda **kw: E.run_case({"model": "mechanical_power", **kw}).calculations["reversible_power_limit_kw"]
        self.assertAlmostEqual(calc(force_N=700, displacement_m=.05, events_per_s=2, W_out_kw=.01), 0.070)
        self.assertAlmostEqual(calc(torque_Nm=20, rotational_speed_rpm=60, W_out_kw=.01), 20 * 2 * 3.141592653589793 / 1000)
        self.assertAlmostEqual(calc(air_density_kg_m3=1.225, swept_area_m2=10, wind_speed_m_s=8, W_out_kw=1), 16 / 27 * 3.136)
        self.assertAlmostEqual(calc(head_m=10, volumetric_flow_m3_s=.02, water_density_kg_m3=1000, W_out_kw=1), 1.962)

    def test_property_totals_and_latent_heat(self):
        r = E.run_case({"model": "property_change", "latent_heat_kj_kg": 2257, "phase_change_temperature_K": 373.15, "mass_kg": 2})
        self.assertAlmostEqual(r.calculations["delta_H_total_kj"], 4514)
        self.assertAlmostEqual(r.calculations["delta_S_total_kj_per_k"], 2 * 2257 / 373.15)


class HeatExchangerModeTests(unittest.TestCase):
    def run_hx(self, **kw):
        return E.run_case({"model": "finite_stream_heat_recovery", **kw})

    def test_shaft_work_is_zero_and_one_outlet_is_derived(self):
        r = self.run_hx(hot_stream=water(.5, 90 + C, 60 + C), cold_stream=water(1, 20 + C))
        self.assertEqual(r.calculations["W_shaft_kw"], 0.0)
        self.assertAlmostEqual(r.calculations["cold_outlet_from_first_law_K"], 35 + C)
        self.assertEqual(r.verdict.status, "SECOND-LAW ADMISSIBLE / NOT REJECTED")

    def test_temperature_cross_from_temperatures_only(self):
        r = self.run_hx(hot_stream={"name": "h", "T_in_K": 90 + C, "T_out_K": 40 + C},
                        cold_stream={"name": "c", "T_in_K": 20 + C, "T_out_K": 95 + C})
        self.assertEqual(r.verdict.status, "NOT FEASIBLE — TEMPERATURE CROSS")

    def test_duty_only_and_underdetermined(self):
        self.assertEqual(self.run_hx(hot_duty_kw=50, cold_duty_kw=50).checks["first_law"]["passed"], True)
        self.assertTrue(self.run_hx(hot_duty_kw=50, cold_duty_kw=40).verdict.status.startswith("NOT FEASIBLE"))
        both = self.run_hx(hot_stream=water(1, 90 + C), cold_stream=water(1, 20 + C))
        self.assertEqual(both.verdict.status, "INSUFFICIENT DATA / MODEL REQUIRED")

    def test_heat_loss_to_surroundings_is_not_internal_transfer(self):
        hot, cold = water(1, 90 + C, 50 + C), water(1, 20 + C, 40 + C)
        self.assertTrue(self.run_hx(hot_stream=hot, cold_stream=cold).verdict.status.startswith("NOT FEASIBLE"))
        lossy = self.run_hx(hot_stream=hot, cold_stream=cold, heat_loss_to_surroundings_kw=83.6, surroundings_temperature_K=298.15)
        self.assertEqual(lossy.verdict.status, "SECOND-LAW ADMISSIBLE / NOT REJECTED")


class CheckTableTests(unittest.TestCase):
    def test_every_model_returns_structured_rows(self):
        cases = [{"model": "fixed_reservoir_engine", "T_hot_K": 600, "T_cold_K": 300, "Q_hot_kw": 100, "W_out_kw": 40},
                 {"model": "refrigeration_cycle", "Q_cold": 5, "W_in": 1, "T_cold_K": 260, "T_hot_K": 300},
                 {"model": "finite_stream_heat_recovery", "hot_stream": water(.5, 363.15, 333.15), "cold_stream": water(1, 293.15, 308.15)},
                 {"model": "solar_blackbody", "T_source_K": 5778, "T_ambient_K": 300, "irradiance_W_m2": 1000,
                  "collection_area_m2": 2, "W_out_kw": .5}]
        for case in cases:
            rows = E.run_case(case).check_table
            self.assertTrue(rows, case["model"])
            for row in rows:
                self.assertEqual(set(row), {"check", "equation", "value", "criterion", "status"})
                self.assertIn(row["status"], {"PASS", "FAIL", "NOT EVALUATED", "NOT APPLICABLE"})
                self.assertNotIn("{", row["value"])


class RoutingTests(unittest.TestCase):
    """The prompt's routing table: each description must reach the right model."""
    ROUTES = [
        ("A heat engine receives 100 kW from a hot source at 600 K, rejects heat at 300 K and produces 40 kW.", "fixed_reservoir_engine"),
        ("The engine absorbs 100 kilowatts of thermal energy per second from a 700 K furnace, rejects heat at 300 K "
         "and delivers 45 kW.", "fixed_reservoir_engine"),
        ("Exhaust gas at 2 kg/s enters at 500 °C and leaves at 150 °C, cp = 1.1 kJ/kg K. Cooling water at 5 kg/s "
         "enters at 20 °C. The engine claims 300 kW.", "finite_stream_engine"),
        ("Steam enters a turbine at 3 kg/s with h = 3400 kJ/kg and s = 6.9 kJ/kg K and leaves with h = 2600 kJ/kg "
         "and s = 7.2 kJ/kg K. It produces 2400 kW.", "steady_control_volume"),
        ("Air enters a compressor at 0.5 kg/s with h1 = 300 kJ/kg and leaves with h2 = 450 kJ/kg. The compressor "
         "requires 75 kW of work.", "steady_control_volume"),
        ("Water enters a pump at 10 kg/s with h1 = 100 kJ/kg, s1 = 0.35 kJ/kg K and leaves with h2 = 103 kJ/kg, "
         "s2 = 0.351 kJ/kg K. The pump consumes 30 kW.", "steady_control_volume"),
        ("A piston-cylinder device holds gas that receives 40 kJ of heat while its internal energy increases by 25 kJ. "
         "It does 15 kJ of work.", "closed_system"),
        ("An air conditioner removes 3 kW from a room at 24 °C and rejects heat outdoors at 35 °C using 0.8 kW of "
         "electricity.", "refrigeration_cycle"),
        ("A heat pump delivers 10 kW to a house at 20 °C, taking heat from outside air at 0 °C. It consumes 2.5 kW.",
         "refrigeration_cycle"),
        ("A heat exchanger cools hot oil (2 kg/s, Cp 2.2 kJ/kg K) from 450 K to 350 K. Cold water at 1.2 kg/s with "
         "Cp 4.18 kJ/kg K enters at 300 K. No shaft work.", "finite_stream_heat_recovery"),
        ("A cyclist pedals with 25 N·m of torque at 60 rpm and the generator delivers 120 W.", "mechanical_power"),
        ("A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW.", "mechanical_power"),
        ("A hydro plant with a 50 m head and 10 m3/s flow claims 5 MW.", "mechanical_power"),
        ("A PV panel of 1.6 m² under 800 W/m² of sunlight produces 350 W.", "solar_blackbody"),
        ("A hydrogen fuel cell consumes 0.01 mol/s of H2 and produces 2 kW.", "reaction_gibbs"),
        ("A hot steel ball at 500 °C is dropped into oil at 30 °C. How long does it take to cool to 100 °C?",
         "unsupported_domain"),
    ]

    def test_routes(self):
        for text, route in self.ROUTES:
            with self.subTest(text=text[:50]):
                response = solve({"scenario": text})
                self.assertEqual(response["route"], route)
                self.assertTrue(response["analysis"]["reason"], "a reason for the choice is shown")

    def test_reversed_reservoirs_are_not_swapped(self):
        # heat taken at 300 K and rejected at 600 K: the stated direction, not the larger number, decides
        response = analyze_scenario("A complete cycle receives 500 kJ heat from a 300 K reservoir and rejects 300 kJ "
                                    "to a 600 K reservoir. It produces 200 kJ net work.")
        self.assertEqual(status_of(response), "NOT FEASIBLE")


class UnitAndValidationTests(unittest.TestCase):
    def test_celsius_watts_grams_litres(self):
        r = analyze_scenario("A methanol fuel cell uses 0.5 g/s of methanol and delivers 8 kW.")
        self.assertEqual(r["route"], "reaction_gibbs")
        hydro = analyze_scenario("A small hydro turbine has 20 L/s of water falling 10 m and claims 1500 W.")
        self.assertAlmostEqual(hydro["result"]["calculations"]["hydraulic_power_kw"], 0.02 * 1000 * 9.81 * 10 / 1000)
        fridge = analyze_scenario("A refrigerator removes 300 W from a freezer at -18 °C and rejects heat to a kitchen "
                                  "at 25 °C using 100 W of electricity.")
        self.assertAlmostEqual(fridge["result"]["inputs"]["T_cold_K"], 255.15)
        self.assertAlmostEqual(fridge["result"]["inputs"]["Q_cold"], 0.3)

    def test_below_absolute_zero_is_rejected(self):
        r = analyze_scenario("One kg liquid water is heated from 30°C to −400°C. Assume Cp = 4.18 kJ/kg K.")
        self.assertEqual(r["status"], "error")
        self.assertIn("absolute zero", r["error"])

    def test_mixed_kj_and_kw_is_rejected(self):
        r = analyze_scenario("A refrigerator removes 5 kJ from a cold space at 260 K and rejects heat at 300 K using 1 kW of work.")
        self.assertEqual(r["status"], "error")


class TestCasePdfSuite(unittest.TestCase):
    """The 63 cases of the team's website test-case PDF. Expected outcomes follow the physics; where it differs
    from the PDF's note the reason is given (2.1, 4.4, 6.1, 6.2, 7.4, 8.6, 8.8)."""
    EXPECTED = {
        "1.1": "FEASIBLE", "1.2": "NOT FEASIBLE", "1.3": "FEASIBLE", "1.4": "FEASIBLE", "1.5": "needs_values",
        "2.1": "NOT FEASIBLE",          # 20 kW claimed > W_max = 15.06 kW
        "2.2": "NOT FEASIBLE", "2.3": "LIMIT CALCULATED", "2.4": "LIMIT CALCULATED", "2.5": "LIMIT CALCULATED",
        "2.6": "needs_values",
        "3.1": "FEASIBLE", "3.2": "NOT FEASIBLE", "3.3": "FEASIBLE", "3.4": "FEASIBLE", "3.5": "needs_values",
        "3.6": "NOT FEASIBLE",
        "4.1": "FEASIBLE", "4.2": "NOT FEASIBLE", "4.3": "NOT FEASIBLE",
        "4.4": "INSUFFICIENT DATA",     # U2 = 540 kJ is calculated, but no entropy data: Second Law unknown
        "4.5": "INSUFFICIENT DATA",
        "5.1": "PROPERTY CALCULATION ONLY", "5.2": "PROPERTY CALCULATION ONLY", "5.3": "PROPERTY CALCULATION ONLY",
        "5.4": "PROPERTY CALCULATION ONLY", "5.5": "needs_values", "5.6": "error",
        "6.1": "FEASIBLE", "6.2": "FEASIBLE",   # refrigerator and heat pump are now implemented (Model 5)
        "6.3": "INSUFFICIENT DATA", "6.4": "INSUFFICIENT DATA", "6.5": "FEASIBLE",
        "7.1": "SECOND-LAW ADMISSIBLE", "7.2": "NOT FEASIBLE", "7.3": "NOT FEASIBLE",
        "7.4": "INSUFFICIENT DATA",     # duties close with W = 0; S_gen needs stream temperatures
        "7.5": "NOT FEASIBLE", "7.6": "INSUFFICIENT DATA",
        "8.1": "FEASIBLE", "8.2": "NOT FEASIBLE", "8.3": "FEASIBLE", "8.4": "FEASIBLE", "8.5": "FEASIBLE",
        "8.6": "needs_values", "8.8": "needs_values",   # "same conditions" refers to another test's text
        "8.7": "FEASIBLE", "8.9": "NOT FEASIBLE", "8.10": "needs_values",
        "9.1": "FEASIBLE", "9.2": "NOT FEASIBLE", "9.3": "NOT FEASIBLE", "9.4": "FEASIBLE", "9.5": "FEASIBLE",
        "9.6": "needs_values", "9.7": "NOT FEASIBLE",
        "10.1": "FEASIBLE", "10.2": "NOT FEASIBLE", "10.3": "FEASIBLE", "10.4": "FEASIBLE", "10.5": "NOT FEASIBLE",
        "10.6": "needs_values", "10.7": "NOT FEASIBLE",
    }
    ROUTE = {"1": "fixed_reservoir_engine", "2": "finite_stream_engine", "3": "steady_control_volume",
             "4": "closed_system", "5": "property_change", "7": "finite_stream_heat_recovery", "8": "mechanical_power",
             "9": "solar_blackbody", "10": "reaction_gibbs"}

    def test_all_63_cases(self):
        self.assertEqual(len(CASES), 63)
        for case_id, _, text in CASES:
            with self.subTest(case=case_id):
                response = solve({"scenario": text})
                self.assertTrue(str(status_of(response)).startswith(self.EXPECTED[case_id]),
                                f"{case_id}: {status_of(response)}")
                group = case_id.split(".")[0]
                if group in self.ROUTE and case_id != "5.6":
                    self.assertEqual(response["route"], self.ROUTE[group], case_id)

    def test_key_numbers(self):
        calc = lambda text: solve({"scenario": text})["result"]["calculations"]
        self.assertAlmostEqual(calc(CASES[8][2])["strict_reversible_work_limit_kw"], 36.09, places=2)    # 2.4
        self.assertAlmostEqual(calc(CASES[22][2])["delta_H_total_kj"], 501.6)                            # 5.1
        self.assertAlmostEqual(calc(CASES[25][2])["delta_s_kj_kg_k"], 2257 / 373.15)                     # 5.4
        self.assertAlmostEqual(calc(CASES[20][2])["U2_kj"], 540)                                          # 4.4


if __name__ == "__main__":
    unittest.main()
