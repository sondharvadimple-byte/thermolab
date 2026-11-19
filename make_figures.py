"""Figures for the manuscript, drawn from the engine's own results (no hand-entered numbers).

Run from the project folder:  python manuscript_figures/make_figures.py <output folder>
Writes fig3_feasibility_maps.pdf/.png, fig2_validation.pdf/.png, fig4_verification.pdf/.png and
figure_data.json (the plotted values, also used for the manuscript tables).
"""

import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "website" / "dist"), str(ROOT / "data_export")]

import export_data  # noqa: E402
import validation_against_literature  # noqa: E402
from thermodynamic_feasibility_tester_MASTER import run_case  # noqa: E402
from website_test_cases import CASES  # noqa: E402

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Liberation Sans", "Arial", "DejaVu Sans"],
    "font.size": 7.5, "axes.labelsize": 7.5, "axes.titlesize": 8, "legend.fontsize": 6.5,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.linewidth": 0.6, "lines.linewidth": 1.0,
    "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.direction": "in", "ytick.direction": "in",
    "savefig.dpi": 600, "pdf.fonttype": 42, "axes.spines.top": True, "axes.spines.right": True,
})
FEAS, INFEAS, LIMIT, GREY = "#1b7a4b", "#c0392b", "#2f6f9f", "#7f8c8d"
SINGLE, DOUBLE = 3.33, 7.0


def verdict_kind(status):
    if status.startswith("NOT FEASIBLE"):
        return "not feasible"
    if status.startswith(("FEASIBLE", "SECOND-LAW")):
        return "feasible"
    if status.startswith("LIMIT"):
        return "limit"
    return "other"


def scatter(ax, xs, ys, kinds):
    for kind, color, marker, label in (("feasible", FEAS, "o", "Feasible"), ("not feasible", INFEAS, "x", "Not feasible"),
                                       ("limit", LIMIT, "s", "Limit only")):
        pts = [(x, y) for x, y, k in zip(xs, ys, kinds) if k == kind]
        if pts:
            ax.scatter(*zip(*pts), s=16 if marker != "x" else 20, marker=marker, color=color, label=label,
                       linewidths=1.0 if marker == "x" else 0.4, edgecolors="white" if marker != "x" else None, zorder=3)


def panel_label(ax, text):
    ax.text(0.03, 0.96, text, transform=ax.transAxes, fontweight="bold", va="top", ha="left", fontsize=8.5)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "figures")
    out.mkdir(parents=True, exist_ok=True)
    cases = export_data.collect()
    data = {}

    # ---------- Figure 2: feasibility maps ----------
    fig, axes = plt.subplots(2, 2, figsize=(DOUBLE, 5.0))
    # (a) Model 1: efficiency vs temperature ratio.
    ax = axes[0, 0]
    pts = []
    for c in cases:
        res = c["response"].get("result") or {}
        if c["route"] == "fixed_reservoir_engine" and res.get("calculations"):
            inp = res["inputs"]
            pts.append((inp["T_cold_K"] / inp["T_hot_K"], res["calculations"]["actual_efficiency_percent"],
                        verdict_kind(res["verdict"]["status"])))
    r = np.linspace(0, 1, 200)
    ax.plot(r, 100 * (1 - r), color="black", lw=1.1, label=r"Carnot limit $1-T_C/T_H$")
    ax.fill_between(r, 100 * (1 - r), 100, color=INFEAS, alpha=0.07, lw=0)
    scatter(ax, [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts])
    ax.set(xlim=(0.3, 0.7), ylim=(0, 80), xlabel=r"Temperature ratio $T_C/T_H$", ylabel="Efficiency (%)")
    ax.text(0.62, 62, "Second-Law\nviolation", color=INFEAS, fontsize=6.5, ha="center")
    ax.legend(loc="lower left", frameon=False)
    panel_label(ax, "a")
    data["fig2a"] = pts

    # (b) Model 5: claimed COP vs Carnot COP.
    ax = axes[0, 1]
    pts = []
    for c in cases:
        res = c["response"].get("result") or {}
        calc = res.get("calculations") or {}
        if c["route"] == "refrigeration_cycle" and "COP_refrigerator" in calc:
            hp = res["inputs"].get("mode") == "heat_pump"
            key = "heat_pump" if hp else "refrigerator"
            pts.append((calc[f"carnot_COP_{key}"], calc[f"COP_{key}"], verdict_kind(res["verdict"]["status"])))
    lim = max(max(p[0] for p in pts), max(p[1] for p in pts)) * 1.1
    ax.plot([0, lim], [0, lim], color="black", lw=1.1, label="COP = Carnot COP")
    ax.fill_between([0, lim], [0, lim], lim, color=INFEAS, alpha=0.07, lw=0)
    scatter(ax, [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts])
    ax.set(xlim=(0, lim), ylim=(0, lim), xlabel="Carnot COP", ylabel="Claimed COP")
    ax.legend(loc="upper left", bbox_to_anchor=(0.06, 0.99), frameon=False)
    panel_label(ax, "b")
    data["fig2b"] = pts

    # (c) Resource-limited models 2, 7, 8, 9: claim vs ceiling.
    ax = axes[1, 0]
    styles = {"finite_stream_engine": ("2", "D"), "mechanical_power": ("7", "o"), "solar_blackbody": ("8", "^"),
              "reaction_gibbs": ("9", "s")}
    pts = []
    for c in cases:
        res = c["response"].get("result") or {}
        calc = res.get("calculations") or {}
        if c["route"] in styles and calc:
            if c["route"] == "finite_stream_engine":
                claim, ceiling = calc.get("work_used_for_assessment_kw"), calc.get("strict_reversible_work_limit_kw")
                if res["verdict"]["status"].startswith("LIMIT"):
                    continue
            else:
                claim, ceiling = calc.get("claimed_electrical_output_kw"), calc.get("reversible_power_limit_kw")
            if isinstance(claim, (int, float)) and isinstance(ceiling, (int, float)) and claim > 0 and ceiling > 0:
                pts.append((c["route"], ceiling, claim, verdict_kind(res["verdict"]["status"])))
    lo, hi = 1e-3, 3e4
    ax.plot([lo, hi], [lo, hi], color="black", lw=1.1)
    ax.fill_between([lo, hi], [lo, hi], hi, color=INFEAS, alpha=0.07, lw=0)
    for route, (num, marker) in styles.items():
        for kind, color in (("feasible", FEAS), ("not feasible", INFEAS)):
            sel = [(p[1], p[2]) for p in pts if p[0] == route and p[3] == kind]
            if sel:
                ax.scatter(*zip(*sel), marker=marker, s=16, color=color, edgecolors="white", linewidths=0.4, zorder=3)
    for route, (num, marker) in styles.items():
        ax.scatter([], [], marker=marker, color=GREY, s=16, label=f"Model {num}")
    ax.set(xscale="log", yscale="log", xlim=(lo, hi), ylim=(lo, hi), xlabel="Model ceiling (kW)",
           ylabel="Claimed output (kW)")
    ax.text(2e-3, 5e2, "claim > ceiling:\nnot feasible", color=INFEAS, fontsize=6.5)
    ax.legend(loc="lower right", frameon=False, title="green: feasible, red: not", title_fontsize=6)
    panel_label(ax, "c")
    data["fig2c"] = pts

    # (d) Model 2 approaches the hot-stream exergy limit of Bejan and Alebrahim as the cold stream grows.
    ax = axes[1, 1]
    th, t0, cp = 363.15, 298.15, 4.18
    exergy = cp * ((th - t0) - t0 * np.log(th / t0))
    flows = np.logspace(0, 3, 22)          # cold stream at least as large as the hot stream (1 kg/s)
    wmax = []
    for m in flows:
        r = run_case({"model": "finite_stream_engine",
                      "hot_stream": {"name": "hot", "mass_flow_kg_s": 1, "T_in_K": th, "T_out_K": t0, "cp_model": "constant",
                                     "cp_kj_kg_k": cp},
                      "cold_stream": {"name": "cold", "mass_flow_kg_s": float(m), "T_in_K": t0, "cp_model": "constant",
                                      "cp_kj_kg_k": cp}, "apply_strict_reversible_limit": True})
        wmax.append(r.calculations["strict_reversible_work_limit_kw"])
    ax.plot(flows, wmax, color=LIMIT, marker="o", ms=2.5, label=r"Model 2 $\dot W_{max}$ (code)")
    ax.axhline(exergy, color="black", ls="--", lw=1.0, label="Hot-stream exergy (Bejan and Alebrahim)")
    ax.set(xscale="log", xlabel=r"Cold-stream flow (kg s$^{-1}$)", ylabel=r"$\dot W_{max}$ (kW)", ylim=(0, 30))
    ax.legend(loc="lower right", frameon=False)
    panel_label(ax, "d")
    data["fig2d"] = {"flows": flows.tolist(), "wmax": wmax, "exergy": exergy}
    for a in axes.flat:
        a.tick_params(top=True, right=True, which="both")
    fig.tight_layout(pad=0.4, h_pad=0.8, w_pad=1.2)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig3_feasibility_maps.{ext}")
    plt.close(fig)

    # ---------- Figure 3: validation against literature ----------
    rows = [r for r in validation_against_literature.rows() if r["kind"] != "Published data, inequality"]
    fig, ax = plt.subplots(figsize=(SINGLE, 3.1))
    labels = [f"M{r['model']}" if isinstance(r["model"], int) else "Prop." for r in rows]
    rel = [abs(r["relative_difference_percent"]) for r in rows]
    floor = 1e-6
    shown = [max(v, floor) for v in rel]
    y = np.arange(len(rows))[::-1]
    DATA = "#8a5a9e"
    colors = [DATA if r["kind"] == "Published data" else LIMIT for r in rows]
    ax.barh(y, [v - 1e-7 for v in shown], color=colors, height=0.62, left=1e-7)
    for yi, v, s in zip(y, rel, shown):
        ax.text(s * 1.6, yi, "exact" if v < floor else (f"{v:.2g} %" if v < 0.01 else f"{v:.3g} %"), va="center", fontsize=6)
    short = {1: "Carnot efficiency", 2: "Hot-stream exergy", 3: "Turbine S_gen", 4: "Clausius statement",
             5: "Refrigerator exergy eff.", 6: "Exchanger S_gen", 7: "Betz ceiling", 8: "Radiation ceiling",
             9: "Fuel-cell power"}
    ax.set_yticks(y, [f"{lab}  {short.get(r['model'], 'Water enthalpy')}" for lab, r in zip(labels, rows)], fontsize=6.5)
    ax.set_xscale("log")
    ax.set_xlim(1e-7, 300)
    ax.set_xlabel("|Code - reference| / reference (%)")
    ax.axvline(1, color=GREY, lw=0.6, ls=":")
    ax.bar(0, 0, color=LIMIT, label="vs published equation")
    ax.bar(0, 0, color=DATA, label="vs published data")
    ax.legend(loc="lower center", bbox_to_anchor=(0.32, 1.0), ncol=2, frameon=False, fontsize=6.3)
    fig.tight_layout(pad=0.3)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig2_validation.{ext}")
    plt.close(fig)
    data["validation"] = validation_against_literature.rows()

    # ---------- Figure 4: verification against the 63-case test PDF ----------
    expected = {cid: code for cid, code, _ in CASES}
    pdf_cases = [c for c in cases if c["source"] == "Test-case PDF"]
    group = {str(i): str(i) for i in range(1, 11)}
    order = [str(i) for i in range(1, 11)]
    counts = {g: Counter() for g in order}
    detail = []
    for c in pdf_cases:
        status = export_data.describe(c)[0]
        kind = ("Feasible" if status.startswith(("FEASIBLE", "SECOND-LAW")) else "Not feasible" if status.startswith("NOT FEASIBLE")
                else "Limit / property" if status.startswith(("LIMIT", "PROPERTY")) else "Needs data / model required"
                if status.startswith(("INSUFFICIENT", "NEEDS")) else "Input error")
        g = group[c["ref"].split(".")[0]]
        counts[g][kind] += 1
        detail.append({"case": c["ref"], "pdf": expected[c["ref"]], "code": status, "model": c["route"]})
    kinds = [("Feasible", FEAS), ("Not feasible", INFEAS), ("Limit / property", LIMIT),
             ("Needs data / model required", "#e3a33b"), ("Input error", GREY)]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.3))
    bottom = np.zeros(len(order))
    for kind, color in kinds:
        vals = np.array([counts[g][kind] for g in order])
        ax.bar(order, vals, bottom=bottom, color=color, label=kind, width=0.7, edgecolor="white", linewidth=0.4)
        bottom += vals
    ax.set_ylabel("Number of test cases")
    ax.set_ylim(0, 11)
    ax.set_xlabel("Section of the test-case document")
    ax.tick_params(axis="x", labelsize=6.5)
    ax.legend(frameon=False, fontsize=5.8, ncol=2, loc="upper center", bbox_to_anchor=(0.5, 1.28))
    fig.tight_layout(pad=0.3)
    for ext in ("pdf", "png"):
        fig.savefig(out / f"fig4_verification.{ext}")
    plt.close(fig)
    data["pdf_cases"] = detail
    (out / "figure_data.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    print("figures written to", out)


if __name__ == "__main__":
    main()
