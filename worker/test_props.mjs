// Offline test of props.js (run: node worker/test_props.mjs): fake KV + fake PropLine, real handler. No key, no requests.
import worker from "./odds.js";
import { trimProps } from "./props.js";

const kv = new Map();
const kvEnv = { get: async (k, t) => kv.has(k) ? (t === "json" ? JSON.parse(kv.get(k)) : kv.get(k)) : null,
                put: async (k, v) => { kv.set(k, v); } };
const env = { ODDS_API_KEY: "x", PROPLINE_API_KEY: "pl-test", ODDS: kvEnv };
const ok = (name, cond, extra = "") => console.log(`${cond ? "[PASS]" : "[FAIL]"} ${name} ${extra}`);

const soon = new Date(Date.now() + 20 * 36e5).toISOString();      // kicks off in 20 h
const later = new Date(Date.now() + 5 * 864e5).toISOString();     // in 5 days
const book = (key, title, markets) => ({ key, title, markets });
const ou = (who, pt, o, u, pid) => [{ name: "Over", description: who, price: o, point: pt, player_id: pid },
                                     { name: "Under", description: who, price: u, point: pt, player_id: pid }];
const event = {
  id: "9001", home_team: "Denver Broncos", away_team: "Seattle Seahawks", commence_time: soon,
  bookmakers: [
    book("fanduel", "FanDuel", [
      { key: "player_reception_yds", outcomes: [...ou("Jaxon Smith-Njigba", 74.5, -115, -105, "espn:4430878")] },
      { key: "player_rush_yds", outcomes: [...ou("Bo Nix", 24.5, -110, -110, "espn:4426348")] }]),
    book("betmgm", "BetMGM", [
      { key: "player_rush_yds", outcomes: [...ou("Bo Nix", 25.5, -110, -110, null)] }]),
    book("draftkings", "DraftKings", [
      { key: "player_reception_yds", outcomes: [
        ...ou("Jaxon Smith-Njigba", 75.5, -112, -108, "espn:4430878"),
        ...ou("Jaxon Smith-Njigba", 90.5, +170, -230, "espn:4430878"),            // alt line: not the main one
        { name: "100+ Receiving Yards", description: "Jaxon Smith-Njigba", price: +260, point: 99.5 }] },   // ladder rung: ignored
      { key: "player_anytime_td", outcomes: [{ name: "Yes", description: "Courtland Sutton", price: +140, point: null }] },
      { key: "player_tackles_assists", outcomes: [...ou("Somebody", 4.5, -110, -110)] }]),   // not on the board's list
    book("bovada", "Bovada", [
      { key: "player_rush_yds", outcomes: [...ou("Bo Nix", 25.5, -115, -105, null)] }]),
  ],
};

const t = trimProps(event);
const get = (m, n) => t.find(r => r.m === m && r.n === n);
ok("DraftKings' line wins when DraftKings has one", get("player_reception_yds", "Jaxon Smith-Njigba")?.pt === 75.5
   && get("player_reception_yds", "Jaxon Smith-Njigba")?.bk === "DraftKings", JSON.stringify(get("player_reception_yds", "Jaxon Smith-Njigba")));
ok("a book's main line is the one priced closest to even (90.5 alt ignored)", get("player_reception_yds", "Jaxon Smith-Njigba")?.o === -112);
ok("without DraftKings, the line most books deal (25.5 from 2 of 3)", get("player_rush_yds", "Bo Nix")?.pt === 25.5
   && get("player_rush_yds", "Bo Nix")?.bk === "most books" && get("player_rush_yds", "Bo Nix")?.nb === 3);
ok("player id kept from any book that has it", get("player_rush_yds", "Bo Nix")?.pid === "espn:4426348");
ok("anytime TD: Yes price, line 0.5, no Under", get("player_anytime_td", "Courtland Sutton")?.o === 140
   && get("player_anytime_td", "Courtland Sutton")?.pt === 0.5 && get("player_anytime_td", "Courtland Sutton")?.u == null);
ok("ladder rungs and off-list markets dropped", t.length === 3, `${t.length} rows`);

// Real-feed shapes: a book splits one market into several blocks (main + alt ladder),
// and another spells the player "Name (TEAM)" without an id.
const split = trimProps({ bookmakers: [
  book("draftkings", "DraftKings", [
    { key: "player_rush_yds", outcomes: [...ou("Bijan Robinson", 110.5, +150, -190, "espn:4430807")] },     // alt block first
    { key: "player_rush_yds", outcomes: [...ou("Bijan Robinson", 93.5, -112, -112, "espn:4430807")] }]),    // main block
  book("fanduel", "FanDuel", [{ key: "player_rush_yds", outcomes: [...ou("Bijan Robinson (ATL)", 92.5, -114, -114, null)] }]),
  book("betmgm", "BetMGM", [{ key: "player_rush_yds", outcomes: [...ou("Bijan Robinson", 93.5, -110, -120, "espn:4430807")] }]),
] });
ok("a book's main line is found across all its blocks (93.5, not the 110.5 alt block)",
   split.length === 1 && split[0].pt === 93.5 && split[0].o === -112, JSON.stringify(split));
ok("'Name (TEAM)' and a missing id still join the same player; books counted once each",
   split.length === 1 && split[0].n === "Bijan Robinson" && split[0].nb === 3 && split[0].pid === "espn:4430807");

let calls = [], left = 900;
globalThis.fetch = async (url, init) => {
  const u = new URL(url); calls.push(u.pathname);
  if (init?.headers?.["X-API-Key"] !== "pl-test") throw new Error("key not sent as a header");
  if (u.searchParams.has("apiKey")) throw new Error("key leaked into the URL");
  left -= 1;
  const h = { "X-Daily-Remaining": String(left) };
  if (u.pathname.endsWith("/events")) return new Response(JSON.stringify([
    { id: "9001", home_team: "Denver Broncos", away_team: "Seattle Seahawks", commence_time: soon, completed: false },
    { id: "9002", home_team: "Chicago Bears", away_team: "Atlanta Falcons", commence_time: later, completed: false },
    { id: "8000", home_team: "Old", away_team: "Game", commence_time: soon, completed: true },
    { id: "7000", home_team: "Far", away_team: "Away", commence_time: new Date(Date.now() + 30 * 864e5).toISOString(), completed: false }]), { status: 200, headers: h });
  if (u.pathname.endsWith("/9001/odds")) {
    if (!u.searchParams.get("markets")?.includes("player_reception_yds")) throw new Error("markets not passed");
    return new Response(JSON.stringify(event), { status: 200, headers: h });
  }
  return new Response(JSON.stringify({ detail: "not found" }), { status: 404, headers: h });
};
const req = path => new Request("https://w.example" + path, { headers: { Origin: "https://marcuspeters310-sketch.github.io" } });

let r = await worker.fetch(req("/props/events"), env), b = await r.json();
ok("game list: upcoming games within 9 days only", r.status === 200 && b.events.map(e => e.id).join() === "9001,9002", JSON.stringify(b.events.map(e => e.id)));
ok("game list carries PropLine's requests-left count", b.remaining === 899);
ok("props routes send CORS for the board", r.headers.get("Access-Control-Allow-Origin") === "https://marcuspeters310-sketch.github.io");
await worker.fetch(req("/props/events"), env);
ok("game list reused within 60 minutes", calls.length === 1);

r = await worker.fetch(req("/props/event/9001"), env); b = await r.json();
ok("one game's props: trimmed rows + requests left", r.status === 200 && b.props.length === 3 && b.remaining === 898 && b.cached === false);
await worker.fetch(req("/props/event/9001"), env);
ok("a game within 48 h is reused for 15 minutes (no new request)", calls.length === 2);
const c = JSON.parse(kv.get("props_ev_9001")); c.fetched_at = new Date(Date.now() - 16 * 60e3).toISOString(); kv.set("props_ev_9001", JSON.stringify(c));
await worker.fetch(req("/props/event/9001"), env);
ok("...and pulled again after 15 minutes", calls.length === 3);

const c2 = JSON.parse(kv.get("props_ev_9001")); c2.fetched_at = new Date(Date.now() - 16 * 60e3).toISOString(); c2.remaining = 100; kv.set("props_ev_9001", JSON.stringify(c2));
const lst = JSON.parse(kv.get("props_events")); lst.remaining = 100; kv.set("props_events", JSON.stringify(lst));
b = await (await worker.fetch(req("/props/event/9001"), env)).json();
ok("below 150 requests left today it stops calling and says so", calls.length === 3 && /nearly used up/.test(b.note || ""));

r = await worker.fetch(req("/props/event/12345"), env);
ok("an unknown game id is a 404, not a crash", r.status === 404);
r = await worker.fetch(req("/props/event/../../x"), env);
ok("odd ids are refused before reaching PropLine", r.status === 404 && calls.length === 4);
r = await worker.fetch(req("/props/events"), { ...env, PROPLINE_API_KEY: undefined });
ok("without the secret set, it says how to set it up", r.status === 503 && /secret put PROPLINE_API_KEY/.test((await r.json()).error));
