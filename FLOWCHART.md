# How the tester reaches a verdict

GitHub renders the diagram below. Any Mermaid viewer also works.

```mermaid
flowchart TD
    In["Claim entered as a description or in a model form"] --> Read{"Written description?"}
    Read -->|Yes| Scan["auto_analyzer: find numbers and units, convert to SI, assign each a role"]
    Read -->|No, form| Case
    Scan --> Pick["Separate streams from reservoirs and choose the model (reason shown)"]
    Pick --> Enough{"Enough data for that model?"}
    Enough -->|"No, and the balances cannot fix it"| Ask["Insufficient data: name the missing value"]
    Enough -->|Yes| Case["Engine case in SI units"]

    Case --> Model{"Model"}
    Model -->|1 Reservoirs| M1["First Law; S_gen = Q_C/T_C - Q_H/T_H; Carnot efficiency"]
    Model -->|2 Two streams| M2["Hot-stream heat; cold outlet at S_gen = 0 gives W_max"]
    Model -->|3 Steady flow| M3["Mass, energy and entropy balances, each when its data exist"]
    Model -->|4 Closed system| M4["Q - W = dE; S_gen = dS - sum Q/T"]
    Model -->|5 Refrigerator / heat pump| M5["Q_H = Q_C + W_in; COP vs Carnot COP"]
    Model -->|6 Heat exchanger| M6["Energy closure with W = 0; S_gen; temperature cross"]
    Model -->|7 Mechanical| M7["Ceiling = step energy x rate, torque x speed, Betz or rho g Q H"]
    Model -->|8 Solar| M8["Ceiling = G A x Petela-Landsberg fraction"]
    Model -->|9 Fuel cell| M9["Ceiling = -dG x reaction rate"]
    Model -->|No validated model| MR["Model required"]

    M1 --> Derive["Missing values fixed by a balance are calculated and labelled"]
    M2 --> Derive
    M3 --> Derive
    M4 --> Derive
    M5 --> Derive
    M6 --> Derive
    M7 --> Check
    M8 --> Check
    M9 --> Check
    Derive --> Check{"Any balance fails, S_gen < 0, or claim above the limit?"}
    Check -->|Yes| NF["NOT FEASIBLE"]
    Check -->|"No, and every needed check was evaluated"| F["FEASIBLE / NOT REJECTED"]
    Check -->|"No, but a check could not be evaluated"| ID["INSUFFICIENT DATA"]
```

A few notes on the less obvious paths:

- When a calculated value closes the First Law (for example the rejected heat Q_C = Q_H - W), the energy balance passes
  by construction. The result marks that value as calculated, not given.
- If a Model 2 claim gives no work, the answer is the limit W_max on its own ("LIMIT CALCULATED").
- Property questions, such as the enthalpy change of heating water, go to the property calculator. They get numbers but
  no verdict, because no device is being judged.
- A claim exactly at the limit (S_gen = 0 within tolerance) is accepted as the reversible case.
