const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

class Element {
  constructor() {
    this.children = [];
    this.handlers = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.style = {};
    this.textContent = "";
    this.innerHTML = "";
  }
  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  focus() {}
  scrollIntoView() {}
  querySelectorAll() { return this.children.flatMap((child) => child.children); }
  fire(name) { return this.handlers[name]({ preventDefault() {}, target: this }); }
}

test("local scenario opens empty numeric fields and submits engine JSON", async () => {
  const html = fs.readFileSync(__dirname + "/dist/index.html", "utf8");
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  elements.model.value = "mechanical_power";
  const submitted = [];
  const submits = [new Element(), new Element()];
  const response = (value) => ({
    ok: true, headers: {get: () => "application/json"}, json: async () => value,
  });
  const context = vm.createContext({
    document: {getElementById: (id) => elements[id], createElement: () => new Element(),
      querySelectorAll: () => submits},
    location: {protocol: "http:"}, Intl, setTimeout, clearTimeout, AbortSignal,
    FormData: class {
      get(name) {
        const inputs = elements.fields.children.flatMap((label) => label.children);
        return inputs.find((input) => input.name === name)?.value ?? null;
      }
    },
    fetch: async (url, options) => {
      assert.equal(url, "./api/solve");
      const request = JSON.parse(options.body);
      submitted.push(request);
      if (request.scenario) return response({
        status: "needs_values", route: "mechanical_power", cases: {}, result: null,
      });
      return response({
        status: "evaluated", route: "mechanical_power", cases: {mechanical_power: request.case},
        result: {title: "Walking", verdict: {status: "FEASIBLE / NOT REJECTED"},
          calculations: {claimed_electrical_output_kw: .005, reversible_power_limit_kw: .008}},
      });
    },
  });
  vm.runInContext(fs.readFileSync(__dirname + "/dist/app.js", "utf8"), context);
  elements.scenario.value = "Generating electricity by walking";
  elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(elements.status.textContent, "Enter values");
  assert.equal(elements["model-form"].hidden, false);
  const inputs = elements.fields.children.flatMap((label) => label.children);
  assert.ok(inputs.every((input) => input.value === ""));
  inputs.find((input) => input.name === "recoverable_mechanical_power_kw").value = "8";
  inputs.find((input) => input.name === "W_out_kw").value = "5";
  elements["model-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(submitted.length, 2);
  assert.deepEqual(submitted[1].case, {model: "mechanical_power",
    recoverable_mechanical_power_kw: .008, W_out_kw: .005});
  assert.equal(elements.status.textContent, "Feasible");
  assert.equal(submits[0].disabled, false);
  vm.runInContext('openModel("mechanical_steps")', context);
  elements["model-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(submitted[2].case.events_per_s, 2);
  assert.equal(submitted[2].case.W_out_kw, .005);
  const optionalInputs = elements.fields.children.flatMap((label) => label.children);
  optionalInputs.find((input) => input.name === "heat_rejected_kw").value = "3";
  optionalInputs.find((input) => input.name === "heat_rejection_temperature_K").value = "26.85";
  elements["model-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(submitted[3].case.heat_rejected_kw, .003);
  assert.equal(submitted[3].case.heat_rejection_temperature_K, 300);
  context.fetch = async () => response({
    status: "evaluated", route: "mechanical_power",
    cases: {mechanical_power: {model: "mechanical_power", recoverable_mechanical_power_kw: .008, W_out_kw: .005}},
    source_quotes: {W_out_kw: "5 W", recoverable_mechanical_power_kw: "8 W"},
    result: {title: "Automatic walking result", verdict: {status: "FEASIBLE / NOT REJECTED"},
      calculations: {claimed_electrical_output_kw: .005, reversible_power_limit_kw: .008}},
    calculation_code: [{name: "analyse_mechanical_power", source: "def analyse_mechanical_power(case):"}],
  });
  elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(elements.status.textContent, "Feasible");
  const populated = elements.fields.querySelectorAll();
  assert.equal(populated.find((input) => input.name === "W_out_kw").value, 5);
  const textOf = (el) => el.textContent + el.children.map(textOf).join(" ");
  assert.match(textOf(elements.result), /View calculation code/);
  assert.match(textOf(elements.result), /def analyse_mechanical_power/);
});

test("automatic analysis shows the interpretation and asks only for missing data", async () => {
  const html = fs.readFileSync(__dirname + "/dist/index.html", "utf8");
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  elements.model.value = "mechanical_power";
  const submits = [new Element(), new Element()];
  const response = (value) => ({ok: true, headers: {get: () => "application/json"}, json: async () => value});
  const analysis = {
    domain: "wind", model: "mechanical_power", title: "Wind turbine (Betz limit)",
    extracted: [{quantity: "Wind speed", value: 10, unit: "m/s", quote: "10 m/s"}],
    mapping: ["SOURCE: kinetic energy of the air stream through the rotor area."],
    inferred: ["Betz limit (16/27 of it) = 446.8 kW"], assumptions: ["Air density not given; standard 1.225 kg/m³ used."],
    missing: [], issues: [],
  };
  let reply = {status: "needs_values", route: "mechanical_power", cases: {}, result: null,
    analysis: {...analysis, missing: ["Rotor diameter, blade length or swept area."]}};
  const context = vm.createContext({
    document: {getElementById: (id) => elements[id], createElement: () => new Element(), querySelectorAll: () => submits},
    location: {protocol: "http:"}, Intl, setTimeout, clearTimeout, AbortSignal, FormData: class {},
    fetch: async () => response(reply),
  });
  vm.runInContext(fs.readFileSync(__dirname + "/dist/app.js", "utf8"), context);
  const textOf = (el) => el.textContent + " " + el.children.map(textOf).join(" ");
  elements.scenario.value = "A wind turbine in 10 m/s wind claims 800 kW.";
  elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(elements.status.textContent, "Needs info");
  assert.equal(elements["text-form"].hidden, false);
  assert.match(textOf(elements.result), /Rotor diameter, blade length or swept area/);
  assert.match(textOf(elements.result), /How the scenario was interpreted/);
  reply = {status: "evaluated", route: "mechanical_power", analysis, source_quotes: {W_out_kw: "800 kW"},
    cases: {mechanical_power: {model: "mechanical_power", recoverable_mechanical_power_kw: 446.8, W_out_kw: 800}},
    result: {title: "Wind turbine (Betz limit)", verdict: {status: "NOT FEASIBLE", reasons: ["Claimed useful work exceeds the limit."]},
      calculations: {claimed_electrical_output_kw: 800, reversible_power_limit_kw: 446.8}}};
  elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(elements.status.textContent, "Not feasible");
  const shown = textOf(elements.result);
  assert.match(shown, /Extracted from your description/);
  assert.match(shown, /“10 m\/s”/);
  assert.match(shown, /Assumptions made/);
  assert.match(shown, /SOURCE: kinetic energy/);
});

function loadApp(fetchImpl, extra = {}) {
  const html = fs.readFileSync(__dirname + "/dist/index.html", "utf8");
  const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((match) => [match[1], new Element()]));
  elements.model.value = "fixed_reservoir_engine";
  const submits = [new Element(), new Element()];
  const context = vm.createContext({
    document: {getElementById: (id) => elements[id], createElement: (tag) => Object.assign(new Element(), {tag}),
      querySelectorAll: () => submits},
    location: {protocol: "http:"}, Intl, setTimeout, clearTimeout, AbortSignal,
    FormData: class {
      get(name) {
        const inputs = elements.fields.children.flatMap((label) => label.children);
        return inputs.find((input) => input.name === name)?.value ?? null;
      }
    },
    fetch: fetchImpl,
  });
  Object.assign(context.document, extra.document || {});
  for (const [key, value] of Object.entries(extra)) if (key !== "document") context[key] = value;
  vm.runInContext(fs.readFileSync(__dirname + "/dist/app.js", "utf8"), context);
  return {elements, context};
}
const reply = (value) => ({ok: true, headers: {get: () => "application/json"}, json: async () => value});
const textOf = (el) => (el.textContent || "") + " " + el.children.map(textOf).join(" ");

test("model list is numbered 1-9 in order and the property utility is not selectable", () => {
  const {elements} = loadApp(async () => reply({}));
  const options = elements.model.children.flatMap((group) => group.children);
  const numbers = options.map((o) => /^Model (\d+) ·/.exec(o.textContent)?.[1]).filter(Boolean).map(Number);
  assert.deepEqual([...new Set(numbers)], [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.ok(options.every((o) => o.value !== "property_change" && o.value !== "unsupported_domain"));
  assert.ok(options.some((o) => o.value === "refrigeration_cycle"));
  assert.equal(elements.model.children.length, 1, "one option group: the nine models");
  assert.ok(options.every((o) => !o.value.startsWith("assignment_")), "the CL 202 assignments are not on the website");
});

const findAll = (el, pred) => [...(pred(el) ? [el] : []), ...el.children.flatMap((child) => findAll(child, pred))];

test("example library: categories, representative examples first, show more, filters", () => {
  const {elements, context} = loadApp(async () => reply({}));
  const examples = JSON.parse(JSON.stringify(vm.runInContext("examples", context)));
  const order = examples.map((e) => e.model);
  assert.deepEqual(order, [...order].sort((a, b) => a - b), "the registry stays in model order");
  for (let model = 1; model <= 9; model++) assert.ok(examples.filter((e) => e.model === model).length >= 4, `model ${model}`);
  // the full library stays closed until it is opened
  assert.equal(elements.library.hidden, true);
  vm.runInContext("openLibrary()", context);
  assert.equal(elements.library.hidden, false);
  const groups = elements["example-list"].children;
  assert.equal(groups.length, 8);
  const cards = findAll(elements["example-list"], (el) => el.className === "example");
  assert.equal(cards.length, examples.filter((e) => e.featured).length, "only representative examples at first");
  for (const group of groups) {
    const shown = findAll(group, (el) => el.className === "example").length;
    assert.ok(shown >= 1 && shown <= 2, "one or two per category");
  }
  const more = findAll(groups[0], (el) => el.className === "quiet more")[0];
  assert.match(more.textContent, /^Show \d+ more$/);
  more.fire("click");
  const heatEngines = examples.filter((e) => e.model <= 2).length;
  assert.equal(findAll(elements["example-list"].children[0], (el) => el.className === "example").length, heatEngines);
  const refrigeration = findAll(elements["library-filters"], (el) => el.textContent === "Refrigerators and heat pumps")[0];
  refrigeration.fire("click");
  assert.equal(elements["example-list"].children.length, 1);
  assert.match(textOf(elements["example-list"]), /Normal refrigerator/);
});

test("home page: exactly nine example cards, one per model, that fill the workbench without running", async () => {
  const requests = [];
  const {elements, context} = loadApp(async (url, options) => { requests.push(JSON.parse(options.body)); return reply({}); });
  const cards = elements["model-examples"].children;
  assert.equal(cards.length, 9);
  const wanted = ["Carnot heat engine", "Waste-heat recovery", "Steam turbine", "Piston-cylinder", "Refrigerator",
    "Water–water heat exchanger", "Wind turbine", "Solar panel", "Hydrogen fuel cell"];
  assert.deepEqual(cards.map((card) => card.children[1].textContent), wanted);
  cards.forEach((card, i) => assert.match(card.children[0].textContent, new RegExp(`^MODEL ${i + 1} · `)));
  const info = JSON.parse(vm.runInContext("JSON.stringify(modelInfo)", context));
  const examples = JSON.parse(JSON.stringify(vm.runInContext("examples", context)));
  const fieldValue = (name) => elements.fields.children.flatMap((label) => label.children).find((input) => input.name === name)?.value;
  // load the heat exchanger first, then the engine: no exchanger field may survive
  for (const i of [5, 0, 4, 2, 8, 1, 3, 6, 7]) {
    const tryButton = cards[i].children.at(-1);
    assert.equal(tryButton.textContent, "Try example");
    tryButton.fire("click");
    const example = examples.find((item) => item.title === info[i + 1].example.title);
    assert.equal(elements.model.value, example.form);
    assert.equal(elements["model-form"].hidden, false, "the form is shown for editing");
    const names = elements.fields.children.flatMap((label) => label.children).map((input) => input.name);
    const fields = JSON.parse(vm.runInContext(`JSON.stringify(models[${JSON.stringify(example.form)}].fields.map((f) => f[0]))`, context));
    assert.deepEqual(names, fields, "only this model's fields");
    assert.ok(names.some((name) => fieldValue(name) !== "" && fieldValue(name) !== undefined), "values are filled in");
  }
  assert.equal(fieldValue("T_hot_K"), undefined);
  assert.equal(elements.model.value, "solar_blackbody");
  assert.equal(fieldValue("irradiance_W_m2"), 1000);
  assert.equal(requests.length, 0, "Try example does not run the calculation");
  // the user's edit is what gets calculated
  elements.fields.children.flatMap((label) => label.children).find((input) => input.name === "W_out_kw").value = "0.7";
  elements["model-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(requests.length, 1);
  assert.equal(requests[0].case.W_out_kw, 0.7);
});

test("model reference table lists all nine models with their technologies and checks", () => {
  const {elements} = loadApp(async () => reply({}));
  const [head, ...rows] = elements["models-table"].children;
  assert.deepEqual(head.children.map((th) => th.textContent), ["#", "Model", "Technologies and examples", "What it checks"]);
  assert.deepEqual(rows.map((row) => row.children[0].textContent), ["1", "2", "3", "4", "5", "6", "7", "8", "9"]);
  assert.ok(rows.every((row) => row.children[2].textContent.length > 20 && row.children[3].textContent.length > 10));
});

test("every example fills the right form, leaves no stale values, and gives its intended verdict in the real engine", async () => {
  const requests = [];
  const {elements, context} = loadApp(async (url, options) => {
    requests.push(JSON.parse(options.body));
    return reply({status: "needs_values", route: null, cases: {}, result: null});
  });
  const examples = JSON.parse(JSON.stringify(vm.runInContext("examples", context)));
  for (let i = 0; i < examples.length; i++) {
    await vm.runInContext(`loadExample(${i})`, context);
    const example = examples[i];
    if (!example.text) {
      assert.equal(elements.model.value, example.form, example.title);
      const names = elements.fields.children.flatMap((label) => label.children).map((input) => input.name);
      const fields = JSON.parse(vm.runInContext(`JSON.stringify(models[${JSON.stringify(example.form)}].fields.map((f) => f[0]))`, context));
      assert.deepEqual(names, fields, "only the selected model's fields are on the form: " + example.title);
    }
  }
  assert.equal(requests.length, examples.length);
  const { execFileSync } = require("node:child_process");
  const script = [
    "import json, sys",
    "sys.path[:0] = ['.', 'website/dist']",
    "from website.dist.solver import solve",
    "out = []",
    "for request in json.load(sys.stdin):",
    "    r = solve(request)",
    "    out.append((r.get('result') or {}).get('verdict', {}).get('status') or r.get('status') or r.get('error'))",
    "print(json.dumps(out))",
  ].join("\n");
  const statuses = JSON.parse(execFileSync("python3", ["-c", script], {cwd: __dirname + "/..", input: JSON.stringify(requests)}).toString());
  examples.forEach((example, i) => {
    assert.ok(String(statuses[i]).startsWith(example.expect), `${example.title}: expected ${example.expect}, got ${statuses[i]}`);
  });
});

test("balance and limit checks render as a table, never as raw JSON", async () => {
  const check = {residual: 0, passed: true, equation: "Qdot_hot_released - Qdot_cold_absorbed = 0 (insulated exchanger; no shaft work)", units: "kW"};
  const {elements} = loadApp(async () => reply({
    status: "evaluated", route: "finite_stream_heat_recovery", cases: {x: {}}, source_quotes: {},
    result: {title: "Heat exchanger", verdict: {status: "SECOND-LAW ADMISSIBLE / NOT REJECTED", reasons: ["ok"]},
      calculations: {heat_released_by_hot_stream_kw: 62.7, heat_absorbed_by_cold_stream_kw: 62.7,
        hot_stream_property_results: {delta_h_kj_kg: -125.4}, governing_equations: ["a", "b"]},
      checks: {first_law: check, second_law: "PASS", terminal_temperature_check: "PASS"},
      check_table: [{check: "Energy balance (First Law)", equation: check.equation, value: "Residual = 0 kW", criterion: "Residual ≈ 0", status: "PASS"},
        {check: "Temperature cross", equation: "", value: "PASS", criterion: "", status: "PASS"}]},
  }));
  elements.scenario.value = "exchanger";
  elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  const shown = textOf(elements.result);
  assert.equal(elements.status.textContent, "Feasible");
  assert.match(shown, /Balance and limit checks/);
  assert.match(shown, /Energy balance \(First Law\)/);
  assert.match(shown, /Residual = 0 kW/);
  assert.doesNotMatch(shown, /\{"residual"/);
  assert.doesNotMatch(shown, /"passed"/);
  // a result without an engine table (older summaries) is still shown as rows, not JSON
  const legacy = loadApp(async () => reply({status: "evaluated", route: "closed_system", cases: {x: {}}, source_quotes: {},
    result: {title: "Closed", verdict: {status: "NOT FEASIBLE", reasons: ["x"]}, calculations: {}, checks: {first_law: check}}}));
  legacy.elements.scenario.value = "x";
  legacy.elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  const legacyText = textOf(legacy.elements.result);
  assert.match(legacyText, /Residual = 0 kW/);
  assert.doesNotMatch(legacyText, /\{"residual"/);
});

test("results show the energy path, balance closure, entropy gauge, temperatures and a sensitivity sweep", async () => {
  const { execFileSync } = require("node:child_process");
  const script = ["import json, sys", "sys.path[:0] = ['.', 'website/dist']", "from website.dist.solver import solve",
    "r = solve({'scenario': 'A refrigerator removes 300 W from a freezer at -18 °C and rejects heat to a kitchen at 25 °C using 100 W of electricity.'})",
    "r.pop('calculation_code', None)", "print(json.dumps(r))"].join("\n");
  const response = JSON.parse(execFileSync("python3", ["-c", script], {cwd: __dirname + "/.."}).toString());
  response.result.checks.first_law.residual = 2.7756e-17;            // floating-point noise must print as 0
  const {elements} = loadApp(async () => reply(response));
  elements.scenario.value = "fridge";
  elements["text-form"].fire("submit");
  await new Promise((resolve) => setTimeout(resolve, 5));
  const visuals = findAll(elements.result, (el) => el.className === "analysis-visuals")[0];
  assert.ok(visuals, "visual summary is shown");
  const titles = visuals.children.map((card) => card.children[0].textContent);
  assert.deepEqual(titles, ["Energy path", "First-Law closure", "Entropy generation", "Temperature profile"]);
  const shown = textOf(visuals);
  assert.match(shown, /300 W/);                                       // Q_C
  assert.match(shown, /400 W/);                                       // Q_H = Q_C + W_in
  assert.match(shown, /Residual: 0 kW/);
  assert.doesNotMatch(shown, /e-17|0\.0000000/);
  assert.match(shown, /255\.15 K \/ -18 °C/);
  assert.match(textOf(elements.result), /Explore how an input changes the verdict/);
});

test("theme button switches between light and dark and remembers the choice", () => {
  const stored = {};
  const root = {dataset: {}};
  const {elements} = loadApp(async () => reply({}), {document: {documentElement: root},
    localStorage: {getItem: (k) => stored[k] ?? null, setItem: (k, v) => { stored[k] = v; }},
    matchMedia: () => ({matches: false})});
  const button = elements["theme-toggle"];
  assert.equal(root.dataset.theme, "light");
  assert.equal(button.textContent, "Dark");
  button.fire("click");
  assert.equal(root.dataset.theme, "dark");
  assert.equal(button.textContent, "Light");
  assert.equal(stored["thermolab-theme"], "dark");
});
