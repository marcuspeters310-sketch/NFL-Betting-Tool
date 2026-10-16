/*
 * Player props relay (PropLine, free plan) -- imported by odds.js.
 *
 * The board is public, so the PropLine key lives here as a secret
 * (PROPLINE_API_KEY), never in the page. Both the board's Props button and
 * the scheduled GitHub refresh call these routes:
 *
 *   GET /props/events      upcoming NFL games: [{id, home, away, commence}]
 *   GET /props/event/{id}  one game's player props, one line per player and
 *                          market: DraftKings' main line, else the line most
 *                          books are dealing (ties: nearest the median).
 *
 * Budget (free plan: 1,000 PropLine requests a day; Cloudflare free KV:
 * 1,000 writes a day):
 *   - The game list is reused for 60 minutes.
 *   - A game's props are reused for 15 minutes when it kicks off within 48
 *     hours, 3 hours when it's further out. So even nonstop tapping costs a
 *     few hundred requests and writes a day.
 *   - Below RESERVE requests left today (PropLine's X-Daily-Remaining), it
 *     stops calling and serves the last pull with a note.
 * One game per request keeps each run well inside the free plan's CPU limit.
 */

const PL = "https://api.prop-line.com/v1/sports/football_nfl";
// Over/Under markets with a real stat behind them, plus anytime TD (Yes only).
export const PROP_MARKETS = [
  "player_pass_yds", "player_pass_tds", "player_pass_completions", "player_pass_attempts",
  "player_pass_interceptions", "player_rush_yds", "player_rush_attempts", "player_receptions",
  "player_reception_yds", "player_rush_reception_yds", "player_pass_rush_yds", "player_anytime_td",
];
const BOOKS = ["draftkings", "fanduel", "betmgm", "betrivers", "fanatics", "hardrock", "bovada"];
const PREF = "draftkings";
const LIST_TTL_MIN = 60, NEAR_TTL_MIN = 15, FAR_TTL_MIN = 180, NEAR_HOURS = 48;
const RESERVE = 150;
const AHEAD_DAYS = 9;

const implied = a => a == null ? null : a < 0 ? -a / (-a + 100) : 100 / (a + 100);

// Most common value; ties go to the one nearest the median (same rule as the board's game lines).
export function consensus(vals) {
  const v = vals.filter(x => typeof x === "number").sort((a, b) => a - b);
  if (!v.length) return null;
  const med = v[Math.floor((v.length - 1) / 2)] / 2 + v[Math.ceil((v.length - 1) / 2)] / 2;
  const count = new Map(); v.forEach(x => count.set(x, (count.get(x) || 0) + 1));
  return [...count].sort((a, b) => (b[1] - a[1]) || (Math.abs(a[0] - med) - Math.abs(b[0] - med)))[0][0];
}

/*
 * PropLine event -> [{m, n, pid, pt, o, u, bk, nb}]
 *   m market key, n player name, pid PropLine player id ("espn:4374302"),
 *   pt line, o / u Over / Under prices (u null for anytime TD), bk book
 *   title, nb how many books post that player+market.
 * Each book's MAIN line is the one priced closest to even on both sides,
 * across every block the book sends for that market (books split a market
 * into several blocks, one per alternate ladder); "300+ Passing Yards"-style
 * ladder rungs are ignored. A player is keyed by PropLine's player id when
 * there is one, so "Bijan Robinson" and "Bijan Robinson (ATL)" are one player.
 */
const cleanName = s => s.replace(/\s*\([A-Z]{2,3}\)\s*$/, "").trim();
export function trimProps(ev) {
  const lines = new Map();       // `${m}|${player key}|${book}` -> Map(pt -> {o, u})
  const meta = new Map();        // `${m}|${player key}` -> {names: Map(name -> count), pid}
  const idOf = new Map();        // cleaned lower-case name -> player id, from any book that sends one
  for (const b of ev.bookmakers || []) for (const mk of b.markets || []) for (const o of mk.outcomes || []) {
    const who = cleanName(o.description || "").toLowerCase();
    if (who && o.player_id && !idOf.has(who)) idOf.set(who, o.player_id);
  }
  for (const b of ev.bookmakers || []) {
    for (const mk of b.markets || []) {
      if (!PROP_MARKETS.includes(mk.key)) continue;
      const anytime = mk.key === "player_anytime_td";
      for (const o of mk.outcomes || []) {
        const who = cleanName(o.description || "");
        if (!who || typeof o.price !== "number") continue;
        const side = o.name === "Over" || (anytime && o.name === "Yes") ? "o" : o.name === "Under" || (anytime && o.name === "No") ? "u" : null;
        if (!side) continue;
        const pt = anytime ? 0.5 : o.point;
        if (typeof pt !== "number") continue;
        const pid = o.player_id || idOf.get(who.toLowerCase()) || null;
        const mkey = `${mk.key}|${pid || who.toLowerCase()}`;
        if (!meta.has(mkey)) meta.set(mkey, { m: mk.key, names: new Map(), pid });
        const mt = meta.get(mkey);
        mt.names.set(who, (mt.names.get(who) || 0) + 1);
        const lk = `${mkey}|${b.key}`;
        if (!lines.has(lk)) lines.set(lk, new Map());
        const row = lines.get(lk).get(pt) || { o: null, u: null };
        row[side] = o.price;
        lines.get(lk).set(pt, row);
      }
    }
  }
  const perPlayer = new Map();   // `${m}|${player key}` -> [{book, pt, o, u}], one main line per book
  for (const [lk, byPt] of lines) {
    const book = lk.slice(lk.lastIndexOf("|") + 1), mkey = lk.slice(0, lk.lastIndexOf("|"));
    const anytime = mkey.startsWith("player_anytime_td|");
    let best = null;
    for (const [pt, r] of byPt) {
      if (r.o == null || (!anytime && r.u == null)) continue;
      const gap = anytime ? 0 : Math.abs(implied(r.o) - implied(r.u));
      if (!best || gap < best.gap) best = { book, pt, o: r.o, u: anytime ? null : r.u, gap };
    }
    if (!best) continue;
    if (!perPlayer.has(mkey)) perPlayer.set(mkey, []);
    perPlayer.get(mkey).push(best);
  }
  const out = [];
  for (const [mkey, rows] of perPlayer) {
    const mt = meta.get(mkey);
    let pick = rows.find(r => r.book === PREF);
    if (!pick) {
      const pt = consensus(rows.map(r => r.pt));
      pick = rows.filter(r => r.pt === pt).sort((a, b) => BOOKS.indexOf(a.book) - BOOKS.indexOf(b.book))[0];
    }
    const n = [...mt.names].sort((a, b) => b[1] - a[1] || a[0].length - b[0].length)[0][0];
    out.push({ m: mt.m, n, pid: mt.pid, pt: pick.pt, o: pick.o, u: pick.u,
               bk: pick.book === PREF ? "DraftKings" : "most books", nb: rows.length });
  }
  return out.sort((a, b) => a.n.localeCompare(b.n) || PROP_MARKETS.indexOf(a.m) - PROP_MARKETS.indexOf(b.m));
}

async function pl(env, path, params = {}) {
  const u = new URL(PL + path);
  u.search = new URLSearchParams(params);
  const resp = await fetch(u, { headers: { "X-API-Key": env.PROPLINE_API_KEY } });
  const left = Number(resp.headers.get("X-Daily-Remaining"));
  return { resp, remaining: Number.isFinite(left) && resp.headers.get("X-Daily-Remaining") !== null ? left : null };
}

async function kvGet(env, k) { try { return await env.ODDS.get(k, "json"); } catch { return null; } }
async function kvPut(env, k, v) { try { await env.ODDS.put(k, JSON.stringify(v)); } catch { /* daily write cap: serve without saving */ } }
const ageMin = (iso, now) => iso ? (now - new Date(iso)) / 60e3 : Infinity;

// Returns [body, status]; the caller adds CORS.
export async function handleProps(path, env, now = new Date()) {
  if (!env.PROPLINE_API_KEY) return [{ error: "PropLine key not set up yet (npx wrangler secret put PROPLINE_API_KEY)." }, 503];

  if (path === "/props/events") {
    const cached = await kvGet(env, "props_events");
    if (cached && ageMin(cached.fetched_at, now) < LIST_TTL_MIN) return [{ ...cached, cached: true }, 200];
    if (cached && cached.remaining != null && cached.remaining < RESERVE) return [{ ...cached, cached: true, note: "Daily PropLine requests nearly used up; showing the last game list." }, 200];
    let r;
    try { r = await pl(env, "/events"); } catch { r = null; }
    if (!r || !r.resp.ok) return cached ? [{ ...cached, cached: true, note: "PropLine didn't answer; showing the last game list." }, 200]
                                        : [{ error: `PropLine answered ${r ? r.resp.status : "nothing"}` }, 502];
    const horizon = now.getTime() + AHEAD_DAYS * 864e5;
    const events = (await r.resp.json())
      .filter(e => !e.completed && !e.is_outright && new Date(e.commence_time).getTime() < horizon)
      .map(e => ({ id: String(e.id), home: e.home_team, away: e.away_team, commence: e.commence_time }));
    const body = { fetched_at: now.toISOString(), remaining: r.remaining, events };
    await kvPut(env, "props_events", body);
    return [{ ...body, cached: false }, 200];
  }

  const m = /^\/props\/event\/([A-Za-z0-9_-]{1,40})$/.exec(path);
  if (!m) return [{ error: "not found" }, 404];
  const id = m[1], key = `props_ev_${id}`;
  const cached = await kvGet(env, key);
  const list = await kvGet(env, "props_events");
  const ev = list?.events?.find(e => e.id === id);
  const near = !ev || new Date(ev.commence).getTime() - now.getTime() < NEAR_HOURS * 36e5;
  if (cached && ageMin(cached.fetched_at, now) < (near ? NEAR_TTL_MIN : FAR_TTL_MIN)) return [{ ...cached, cached: true }, 200];
  const lastLeft = [cached?.remaining, list?.remaining].filter(x => x != null).sort((a, b) => a - b)[0];
  if (cached && lastLeft != null && lastLeft < RESERVE) return [{ ...cached, cached: true, note: "Daily PropLine requests nearly used up; showing the last pull." }, 200];
  let r;
  try { r = await pl(env, `/events/${id}/odds`, { markets: PROP_MARKETS.join(","), bookmakers: BOOKS.join(",") }); } catch { r = null; }
  if (!r || !r.resp.ok) {
    if (cached) return [{ ...cached, cached: true, note: `PropLine answered ${r ? r.resp.status : "nothing"}; showing the last pull.` }, 200];
    return [{ error: `PropLine answered ${r ? r.resp.status : "nothing"}` }, r?.resp.status === 404 ? 404 : 502];
  }
  const data = await r.resp.json();
  const body = { id: String(data.id ?? id), home: data.home_team, away: data.away_team, commence: data.commence_time,
                 fetched_at: now.toISOString(), remaining: r.remaining, props: trimProps(data) };
  await kvPut(env, key, body);
  return [{ ...body, cached: false }, 200];
}
