// Offline test of odds.js (run: node worker/test_odds.mjs): fake KV + fake Odds API, real handler. No key, no credits.
import worker from "./odds.js";
const kv = new Map();
const env = { ODDS_API_KEY: "test-key", ODDS: {
  get: async (k, t) => kv.has(k) ? (t === "json" ? JSON.parse(kv.get(k)) : kv.get(k)) : null,
  put: async (k, v) => { kv.set(k, v); } } };
let apiCalls = 0, remaining = 300;
const sample = [{ id: "e1", commence_time: "2026-10-16T00:15:00Z", home_team: "Denver Broncos", away_team: "Seattle Seahawks",
  bookmakers: [
    { title: "DraftKings", last_update: "2026-10-15T01:00:00Z", markets: [
      { key: "spreads", outcomes: [{ name: "Denver Broncos", price: -110, point: 3 }, { name: "Seattle Seahawks", price: -110, point: -3 }] },
      { key: "totals", outcomes: [{ name: "Over", price: -108, point: 41 }, { name: "Under", price: -112, point: 41 }] }] },
    { title: "FanDuel", last_update: "2026-10-15T01:00:00Z", markets: [
      { key: "spreads", outcomes: [{ name: "Denver Broncos", price: -105, point: 2.5 }, { name: "Seattle Seahawks", price: -115, point: -2.5 }] }] }] }];
globalThis.fetch = async (url) => { apiCalls++; remaining -= 2;
  const u = new URL(url); if (u.searchParams.get("apiKey") !== "test-key" || u.searchParams.get("markets") !== "spreads,totals") throw new Error("bad query");
  return new Response(JSON.stringify(sample), { status: 200, headers: { "x-requests-remaining": String(remaining) } }); };
const req = (origin = "https://marcuspeters310-sketch.github.io", path = "/odds") => new Request("https://w.example" + path, { headers: { Origin: origin } });
const ok = (name, cond, extra = "") => console.log(`${cond ? "[PASS]" : "[FAIL]"} ${name} ${extra}`);

let r = await worker.fetch(req(), env); let b = await r.json();
ok("first tap pulls live odds (2 credits) and trims books", r.status === 200 && apiCalls === 1 && b.cached === false && b.events[0].books.length === 2, JSON.stringify(b.events[0].books[0]));
ok("spread kept as the HOME team's own number (+3 = Denver getting 3)", b.events[0].books[0].home_pt === 3 && b.events[0].books[1].total === undefined);
ok("CORS allows the board's origin", r.headers.get("Access-Control-Allow-Origin") === "https://marcuspeters310-sketch.github.io");
r = await worker.fetch(req("https://evil.example"), env);
ok("CORS header withheld for other sites", r.headers.get("Access-Control-Allow-Origin") === null);
ok("taps within 10 minutes reuse the pull (no extra credits)", apiCalls === 1 && (await r.json()).cached === true);
// age the cache and exhaust the daily cap
for (let i = 0; i < 20; i++) { const c = JSON.parse(kv.get("latest")); c.fetched_at = new Date(Date.now() - 11 * 60e3).toISOString(); kv.set("latest", JSON.stringify(c)); await worker.fetch(req(), env); }
ok("never more than 12 pulls per day", apiCalls === 12, `apiCalls=${apiCalls}`);
b = await (await worker.fetch(req(), env)).json();
ok("after the daily cap it serves the last pull with a note", b.cached === true && /Daily limit/.test(b.note));
kv.set("budget", JSON.stringify({ day: "1999-01-01", pulls: 0 }));
const c = JSON.parse(kv.get("latest")); c.remaining = 10; c.fetched_at = new Date(Date.now() - 60 * 60e3).toISOString(); kv.set("latest", JSON.stringify(c));
const before = apiCalls; b = await (await worker.fetch(req(), env)).json();
ok("below the 40-credit reserve it stops pulling for the month", apiCalls === before && /nearly used up/.test(b.note));
ok("unknown paths 404", (await worker.fetch(req(undefined, "/"), env)).status === 404);
