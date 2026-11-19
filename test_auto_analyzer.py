"""Tests for the automatic plain-English analyzer (auto_analyzer.py)."""

import math
import unittest

from auto_analyzer import analyze_scenario, scan_quantities
from website.dist.solver import solve


def verdict(text):
    response = analyze_scenario(text)
    result = response.get("result")
    return response, (result["verdict"]["status"] if result else response["status"])


class ScannerTests(unittest.TestCase):
    def test_units_are_converted_to_engine_units(self):
        found = {q.quote: (q.kind, q.value) for q in scan_quantities(
            "1200 kJ/min, 27 °C, 450 °F, 2 MW, 0.5 g/s, 4.18 kJ/(kg·K), 10 m3/s, 1000 W/m², 120 steps per minute")}
        self.assertAlmostEqual(found["1200 kJ/min"][1], 20.0)
        self.assertAlmostEqual(found["27 °C"][1], 300.15)
        self.assertAlmostEqual(found["450 °F"][1], 505.372, places=3)
        self.assertEqual(found["2 MW"], ("power", 2000.0))
        self.assertEqual(found["0.5 g/s"][0], "mass_flow")
        self.assertEqual(found["4.18 kJ/(kg·K)"], ("cp", 4.18))
        self.assertEqual(found["10 m3/s"][0], "vol_flow")
        self.assertEqual(found["1000 W/m²"][0], "irradiance")
        self.assertAlmostEqual(found["120 steps per minute"][1], 2.0)

    def test_from_to_pair_inherits_unit(self):
        quantities = scan_quantities("cools from 600 to 400 K")
        self.assertEqual([q.value for q in quantities], [600.0, 400.0])
        self.assertEqual({q.pair for q in quantities}, {"from"})


class M3ValidationCases(unittest.TestCase):
    """The five validation cases listed in the M3 plan, written as prose."""

    def test_1_two_reservoir_baseline(self):
        response, status = verdict("A heat engine takes 100 kW of heat from a source at 600 K, rejects heat to a "
                                   "sink at 300 K and produces 30 kW of work.")
        calc = response["result"]["calculations"]
        self.assertEqual(status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(calc["carnot_efficiency_percent"], 50.0)
        self.assertAlmostEqual(calc["Q_cold_rejected_kw"], 70.0)
        # S_gen = Q_C/T_C - Q_H/T_H = 70/300 - 100/600 (the M3 plan table lists 0.2333, which is Q_C/T_C only)
        self.assertAlmostEqual(calc["S_gen_dot_kw_per_k"], 70 / 300 - 100 / 600)

    def test_2_finite_streams_infer_cold_outlet(self):
        response, status = verdict("Hot water at 1 kg/s cools from 600 K to 400 K with Cp = 4.18 kJ/(kg K). Cold "
                                   "water at 2 kg/s enters at 300 K, Cp 4.18 kJ/(kg K). The device claims 186 kW.")
        self.assertEqual(status, "FEASIBLE / NOT REJECTED")
        # The engine derives the cold outlet from the First Law (Q_cold = Q_hot - W).
        self.assertAlmostEqual(response["result"]["calculations"]["cold_outlet_from_first_law_K"], 377.751, places=2)
        self.assertTrue(response["result"]["checks"]["first_law"]["passed"])

    def test_3_variable_cp_and_phase_change(self):
        response, status = verdict("A source stream with Cp(T) = 1 + 0.0005T kJ/kg K cools from 500 K to 350 K. "
                                   "Heat is rejected to a wax that melts with latent heat 250 kJ/kg and entropy "
                                   "of fusion 0.80 kJ/kg K. The engine claims 25% efficiency.")
        calc = response["result"]["calculations"]
        case = response["cases"]["fixed_reservoir_engine"]
        self.assertAlmostEqual(case["T_hot_K"], 421.32, places=2)
        self.assertAlmostEqual(case["T_cold_K"], 312.5)
        self.assertAlmostEqual(calc["carnot_efficiency_percent"], 25.83, places=2)
        self.assertEqual(status, "FEASIBLE / NOT REJECTED")

    def test_4_impossible_claim(self):
        response, status = verdict("An inventor claims his engine takes 100 kW of heat from a source at 600 K, "
                                   "rejects heat to surroundings at 300 K and produces 60 kW of work.")
        self.assertEqual(status, "NOT FEASIBLE")
        self.assertLess(response["result"]["calculations"]["S_gen_dot_kw_per_k"], 0)

    def test_5_incomplete_nonthermal(self):
        response, status = verdict("Can I generate electricity by walking?")
        self.assertEqual(status, "needs_values")
        self.assertTrue(response["analysis"]["missing"])
        self.assertIsNone(response["result"])


class DomainTests(unittest.TestCase):
    CASES = [
        ("A heat engine receiving heat at a rate of 1200 kJ/min from a furnace at 800 K rejects heat to a river "
         "at 290 K. The engine produces 12 kW.", "FEASIBLE / NOT REJECTED", "fixed_reservoir_engine"),
        ("A heat engine operates between 327°C and 27°C. It receives 500 kJ/s of heat and delivers 200 kW of "
         "power.", "FEASIBLE / NOT REJECTED", "fixed_reservoir_engine"),
        ("An engine claims 35 percent efficiency while running between 120 °C and 30 °C.", "NOT FEASIBLE",
         "fixed_reservoir_engine"),
        ("An engine receives 1000 kJ of heat from a reservoir at 500 K, rejects 400 kJ to a sink at 300 K, and "
         "produces 600 kJ of work.", "NOT FEASIBLE", "fixed_reservoir_engine"),
        ("A device draws 50 kW of heat from the ocean at 290 K and converts it entirely into work.",
         "NOT FEASIBLE", "closed_system"),
        ("A heat engine takes in heat from a reservoir at 400 K and rejects heat to a reservoir at 500 K while "
         "producing 10 kW of work from 50 kW of heat.", "NOT FEASIBLE", "closed_system"),
        ("Waste heat from flue gases at 3 kg/s cooling from 450 °C to 120 °C is used to generate 250 kW of "
         "electricity. The surroundings are at 25 °C.", "FEASIBLE / NOT REJECTED", "fixed_reservoir_engine"),
        ("A geothermal power plant uses geothermal water extracted at 160°C at a rate of 440 kg/s as the heat "
         "source and produces 22 MW of net power. The environment temperature is 25°C.",
         "FEASIBLE / NOT REJECTED", "fixed_reservoir_engine"),
        ("A shoe-mounted generator recovers 4 J per step at 120 steps per minute and claims to generate 5 W of "
         "electricity.", "FEASIBLE / NOT REJECTED", "mechanical_power"),
        ("A person pedals a bicycle generator with 150 W of mechanical power and claims 160 W of electricity.",
         "NOT FEASIBLE", "mechanical_power"),
        ("A machine produces 10 kW without any energy input.", "NOT FEASIBLE", "mechanical_power"),
        ("A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW. Air density 1.2 kg/m3.",
         "NOT FEASIBLE", "mechanical_power"),
        ("Water falls 20 m at 50 L/s through a micro-hydro turbine; the generator claims 9 kW.",
         "FEASIBLE / NOT REJECTED", "mechanical_power"),
        ("A PV panel of 1.6 m² under 800 W/m² of sunlight produces 350 W.", "FEASIBLE / NOT REJECTED",
         "solar_blackbody"),
        ("A methanol fuel cell uses 0.5 g/s of methanol and delivers 8 kW.", "FEASIBLE / NOT REJECTED",
         "reaction_gibbs"),
        ("A fuel cell running on hydrogen at 2 g/s claims 300 kW.", "NOT FEASIBLE", "reaction_gibbs"),
    ]

    def test_domains_and_verdicts(self):
        for text, expected, model in self.CASES:
            with self.subTest(text=text[:60]):
                response, status = verdict(text)
                self.assertEqual(status, expected)
                self.assertEqual(response["analysis"]["model"], model)

    def test_wind_betz_value(self):
        response, _ = verdict("A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW. "
                              "Air density 1.2 kg/m3.")
        limit = response["result"]["calculations"]["reversible_power_limit_kw"]
        import math
        self.assertAlmostEqual(limit, 16 / 27 * 0.5 * 1.2 * math.pi * 20 ** 2 * 1000 / 1000, places=6)

    def test_assumptions_are_reported(self):
        response, _ = verdict("Waste heat from flue gases at 3 kg/s cooling from 450 °C to 120 °C is used to "
                              "generate 250 kW of electricity. The surroundings are at 25 °C.")
        self.assertTrue(any("Cp" in item for item in response["analysis"]["assumptions"]))
        self.assertIn("450 °C", [item["quote"] for item in response["analysis"]["extracted"]])

    def test_ambiguous_claim_is_not_judged(self):
        response, status = verdict("A heat engine has hot reservoir 600 K and cold reservoir 300 K, receives "
                                   "100 kW of heat, and claims to generate 50 to 60 kW.")
        self.assertEqual(status, "needs_values")

    def test_refrigerator_is_evaluated_with_carnot_cop(self):
        # Refrigerators are now a supported model (Model 5): COP 5 is below the Carnot COP 6.5.
        response, status = verdict("A refrigerator removes 5 kW from a cold space at 260 K and rejects heat to a room "
                                   "at 300 K using 1 kW of work.")
        self.assertEqual(status, "FEASIBLE / NOT REJECTED")
        self.assertAlmostEqual(response["result"]["calculations"]["carnot_COP_refrigerator"], 6.5)


class WebsiteIntegrationTests(unittest.TestCase):
    def test_solver_returns_analysis_and_analyzer_code(self):
        response = solve({"scenario": "A hydro plant with a 50 m head and 10 m3/s flow claims 5 MW."})
        self.assertEqual(response["status"], "evaluated")
        self.assertEqual(response["result"]["verdict"]["status"], "NOT FEASIBLE")
        self.assertEqual(response["analysis"]["domain"], "hydro")
        names = [item["name"] for item in response["calculation_code"]]
        self.assertIn("auto_analyzer.ScenarioAnalyzer.route_hydro", names)
        self.assertIn("analyse_mechanical_power", names)

    def test_missing_information_is_listed(self):
        response = solve({"scenario": "A wind turbine claims 2 MW."})
        self.assertEqual(response["status"], "needs_values")
        self.assertTrue(any("Wind speed" in item for item in response["analysis"]["missing"]))

    def test_website_no_longer_runs_the_course_assignments(self):
        # The CL 202 assignments were removed from the website; run_mock_assignments.py is kept for the terminal.
        response = solve({"scenario": "Run CL 202 Assignment 1 Base for suffix 100."})
        self.assertNotEqual(response.get("route"), "assignment_base")
        self.assertNotIn("assignment_base", response.get("models", {}))


if __name__ == "__main__":
    unittest.main()


class HeatExchangerTests(unittest.TestCase):
    TEXT = ("An industrial heat exchanger is used to cool hot oil from 450 K to 350 K while heating cold water from "
            "300 K to 380 K. The hot oil has a mass flow rate of 2 kg/s and a constant specific heat capacity of 2.2 "
            "kJ/(kg·K). The cold water has a mass flow rate of 1.2 kg/s and a constant specific heat capacity of "
            "4.5833 kJ/(kg·K). Assume the heat exchanger operates at steady state, is adiabatic to the surroundings, "
            "and has no shaft work output. All heat released by the hot oil is absorbed by the cold water.")

    def test_rounded_textbook_exchanger(self):
        response, status = verdict(self.TEXT)
        calc = response["result"]["calculations"]
        self.assertEqual(status, "SECOND-LAW ADMISSIBLE / NOT REJECTED")
        self.assertEqual(response["analysis"]["model"], "finite_stream_heat_recovery")
        hot = response["cases"]["finite_stream_heat_recovery"]["hot_stream"]
        self.assertEqual((hot["mass_flow_kg_s"], hot["cp_kj_kg_k"], hot["T_in_K"]), (2.0, 2.2, 450.0))
        self.assertAlmostEqual(calc["heat_released_by_hot_stream_kw"], 440.0)
        self.assertAlmostEqual(calc["S_gen_dot_kw_per_k"], 4.4 * math.log(350 / 450) + 1.2 * 4.5833 * math.log(380 / 300))
        self.assertAlmostEqual(calc["exergy_destroyed_kw"], 298.15 * calc["S_gen_dot_kw_per_k"])

    def test_cool_hot_oil_is_the_hot_stream(self):
        response, _ = verdict(self.TEXT)
        names = [item["quantity"] for item in response["analysis"]["extracted"]]
        self.assertIn("Hot oil flow rate", names)
        self.assertNotIn("Cold oil inlet temperature", names)

    def test_missing_outlet_is_inferred_and_t0_read(self):
        response, status = verdict("A heat exchanger cools hot oil (2 kg/s, Cp 2.2 kJ/kg K) from 450 K to 350 K. Cold "
                                   "water at 1.2 kg/s with Cp 4.18 kJ/kg K enters at 300 K. No shaft work. "
                                   "Surroundings are at 25 °C.")
        case = response["cases"]["finite_stream_heat_recovery"]
        self.assertEqual(status, "SECOND-LAW ADMISSIBLE / NOT REJECTED")
        # The engine (not the analyzer) derives the missing outlet from the energy balance.
        self.assertNotIn("T_out_K", case["cold_stream"])
        self.assertAlmostEqual(response["result"]["calculations"]["cold_outlet_from_first_law_K"], 300 + 440 / (1.2 * 4.18))
        self.assertAlmostEqual(case["T0_K"], 298.15)

    def test_temperature_cross_is_rejected(self):
        _, status = verdict("A heat exchanger cools hot oil from 360 K to 340 K while heating cold water from 300 K "
                            "to 370 K. Hot oil: 2 kg/s, Cp 2.2 kJ/(kg K). Cold water: 0.30075 kg/s, Cp 4.18 kJ/(kg K). "
                            "No shaft work.")
        self.assertTrue(status.startswith("NOT FEASIBLE"), status)

    def test_real_energy_mismatch_is_rejected(self):
        _, status = verdict("A heat exchanger cools hot oil from 450 K to 350 K while heating cold water from 300 K "
                            "to 380 K. Hot oil: 2 kg/s, Cp 2.2 kJ/(kg K). Cold water: 1.0 kg/s, Cp 4.18 kJ/(kg K). "
                            "No shaft work.")
        self.assertTrue(status.startswith("NOT FEASIBLE"), status)


class TwoWaterStreamsTests(unittest.TestCase):
    """'A hot water stream ... A cold water stream ...' must be two streams (Model 2), not stream + reservoir."""
    TEXT = ("A hot water stream flows at 1 kg/s and cools from 90°C to 60°C. A cold water stream flows at 1 kg/s "
            "and enters at 20°C. The system claims to produce {w} kW of shaft work. Assume both streams have a "
            "specific heat capacity of 4.18 kJ/kg K. Check feasibility.")

    def run_claim(self, w):
        response = analyze_scenario(self.TEXT.format(w=w))
        self.assertEqual(response["route"], "finite_stream_engine")
        return response["result"]

    def test_claim_above_heat_released_is_rejected_by_model_2(self):
        result = self.run_claim(150)
        self.assertEqual(result["verdict"]["status"], "NOT FEASIBLE")
        self.assertAlmostEqual(result["calculations"]["strict_reversible_work_limit_kw"], 15.056, places=2)

    def test_reversible_limit_separates_feasible_and_infeasible_claims(self):
        self.assertEqual(self.run_claim(15)["verdict"]["status"], "FEASIBLE / NOT REJECTED")
        self.assertEqual(self.run_claim(16)["verdict"]["status"], "NOT FEASIBLE")


class ModelMappingTests(unittest.TestCase):
    """Text → model number mapping (same numbers as the terminal program)."""
    CASES = [
        ("Steam enters an adiabatic turbine with h = 3231.7 kJ/kg and s = 6.9235 kJ/kg K and leaves with "
         "h = 2675.0 kJ/kg and s = 7.3589 kJ/kg K. The mass flow rate is 1 kg/s. It produces 556.7 kW.", 3,
         "FEASIBLE / NOT REJECTED"),   # all balances complete and closed -> admissible
        ("Steam enters a turbine with h = 3231.7 kJ/kg and s = 6.9235 kJ/kg K and leaves with h = 2675.0 kJ/kg and "
         "s = 6.80 kJ/kg K. Mass flow 1 kg/s. It is adiabatic and produces 556.7 kW.", 3, "NOT FEASIBLE"),
        ("A gas in a piston-cylinder device receives 15 kJ of heat from a reservoir at 300 K. Its internal energy "
         "increases by 10 kJ and its entropy increases by 0.04 kJ/K. The gas does 5 kJ of work.", 4, "NOT FEASIBLE"),
        ("A refrigerator removes 5 kW from a cold space at 260 K and rejects heat to a room at 300 K using 1 kW of "
         "work.", 5, "FEASIBLE / NOT REJECTED"),
        (HeatExchangerTests.TEXT, 6, "SECOND-LAW ADMISSIBLE / NOT REJECTED"),
        ("A wind turbine with 40 m rotor diameter in 10 m/s wind claims 800 kW.", 7, "NOT FEASIBLE"),
        ("A PV panel of 1.6 m² under 800 W/m² of sunlight produces 350 W.", 8, "FEASIBLE / NOT REJECTED"),
        ("A fuel cell running on hydrogen at 2 g/s claims 300 kW.", 9, "NOT FEASIBLE"),
        ("An inventor claims his engine takes 100 kW of heat from a source at 600 K, rejects heat to surroundings "
         "at 300 K and produces 60 kW of work.", 1, "NOT FEASIBLE"),
    ]

    def test_text_maps_to_numbered_model(self):
        for text, number, expected in self.CASES:
            with self.subTest(model=number):
                response = solve({"scenario": text})
                self.assertTrue(response["model_label"].startswith(f"Model {number} "), response["model_label"])
                self.assertEqual(response["result"]["verdict"]["status"], expected)

    def test_property_values(self):
        # The property calculator is no longer a numbered model, but text questions still reach it.
        response = solve({"scenario": "Water is heated from 300 K to 350 K with Cp = 4.18 kJ/kg K. Calculate the "
                                      "enthalpy change, entropy change and effective temperature."})
        self.assertEqual(response["result"]["verdict"]["status"], "PROPERTY CALCULATION ONLY")
        calc = response["result"]["calculations"]
        self.assertAlmostEqual(calc["delta_h_kj_kg"], 209.0)
        self.assertAlmostEqual(calc["effective_temperature_K"], 50 / math.log(350 / 300))

    def test_model2_exact_limit_from_text(self):
        response = solve({"scenario": "Hot water at 1 kg/s cools from 600 K to 400 K with Cp = 4.18 kJ/(kg K). Cold "
                                      "water at 2 kg/s enters at 300 K, Cp 4.18 kJ/(kg K). The device claims 186 kW."})
        calc = response["result"]["calculations"]
        self.assertTrue(response["model_label"].startswith("Model 2 "))
        self.assertAlmostEqual(calc["strict_reversible_work_limit_kw"], 272.34, places=1)
