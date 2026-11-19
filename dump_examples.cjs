// Load app.js in the same fake DOM as the tests and capture the engine request each example sends.
const fs = require("fs"), vm = require("vm");
class Element { constructor(){this.children=[];this.handlers={};this.hidden=false;this.value="";this.style={};this.textContent="";}
  append(...c){for(const x of c){this.children.push(x);}} replaceChildren(...c){this.children=[];this.append(...c);}
  setAttribute(n,v){this[n]=v;} addEventListener(n,h){this.handlers[n]=h;} focus(){} scrollIntoView(){}
  querySelectorAll(){return this.children.flatMap((c)=>c.children);} }
const dir = process.argv[2];
const html = fs.readFileSync(dir + "/index.html", "utf8");
const el = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map((m) => [m[1], new Element()]));
el.model.value = "fixed_reservoir_engine";
const requests = [];
const ctx = vm.createContext({document:{getElementById:(i)=>el[i],createElement:()=>new Element(),querySelectorAll:()=>[]},
  location:{protocol:"http:"},Intl,setTimeout,clearTimeout,AbortSignal,
  FormData: class { get(n){ return el.fields.children.flatMap((l)=>l.children).find((i)=>i.name===n)?.value ?? null; } },
  fetch: async (u,o)=>{requests.push(JSON.parse(o.body)); return {ok:true,headers:{get:()=>"application/json"},json:async()=>({})};}});
vm.runInContext(fs.readFileSync(dir + "/app.js", "utf8"), ctx);
(async () => {
  const ex = JSON.parse(vm.runInContext("JSON.stringify(examples)", ctx));
  const info = JSON.parse(vm.runInContext("JSON.stringify(modelInfo)", ctx));
  const out = [];
  for (let i = 0; i < ex.length; i++) {
    await vm.runInContext(`loadExample(${i})`, ctx);
    const fields = el.fields.children.flatMap((l) => l.children).filter((x) => x.value !== "" && x.value !== undefined)
      .map((x) => x.name + " = " + x.value);
    const labels = Object.fromEntries(el.fields.children.map((l) => [l.children[0]?.name, l.textContent]));
    out.push({...ex[i], request: requests.at(-1), shown: ex[i].text ? null : el.fields.children.flatMap((l) => l.children)
      .filter((x) => x.value !== "" && x.value !== undefined).map((x) => `${labels[x.name]}: ${x.value}`)});
  }
  fs.writeFileSync(process.argv[3] || "examples_dump.json", JSON.stringify({examples: out, info}, null, 1));
  console.log(out.length);
})();
