/*
 * NFL Board odds relay -- a Cloudflare Worker (free plan).
 *
 * The board is a public static page, so it can't hold The Odds API key:
 * anyone could read it from the page source. This Worker holds the key as a
 * secret (ODDS_API_KEY), fetches NFL spreads and totals for US books, trims
 * them down, and hands them to the page's refresh button.
 *
 * Protecting the free 500 credits/month (one pull = 2 credits: 2 markets x
 * 1 region):
 *   - A pull is reused for MIN_AGE_MIN minutes. Any number of taps in that
 *     window costs nothing.
 *   - At most MAX_PULLS_PER_DAY pulls per Chicago day.
 *   - Below RESERVE remaining credits, no more pulls this month.
 * When a limit hits, the last good pull is served with a note saying why.
 * The free plan can't run up charges either way -- it just stops answering
 * once the month's credits are gone.
 *
 * Bindings (see wrangler.toml): KV namespace ODDS, secret ODDS_API_KEY.
 */

const ODDS_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds";
const MIN_AGE_MIN = 10;
const MAX_PULLS_PER_DAY = 12;
const RESERVE = 40;
const ALLOWED_ORIGINS = [
  "https://marcuspeters310-sketch.github.io",
  "http://127.0.0.1:8765",
  "http://localhost:8765",
];

const chicagoDay = d => d.toLocaleDateString("en-CA", { timeZone: "America/Chicago" });

function cors(origin) {
  return ALLOWED_ORIGINS.includes(origin)
    ? { "Access-Control-Allow-Origin": origin, "Vary": "Origin" }
    : {};
}

function json(body, status, origin) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...cors(origin) },
  });
}

// One event -> {id, commence, home, away, books: [{book, upd, home_pt, home_px, away_px, total, over_px, under_px}]}
function trim(ev) {
  const books = [];
  for (const b of ev.bookmakers || []) {
    const row = { book: b.title, upd: b.last_update };
    for (const m of b.markets || []) {
      if (m.key === "spreads") {
        const h = m.outcomes.find(o => o.name === ev.home_team);
        const a = m.outcomes.find(o => o.name === ev.away_team);
        if (h && typeof h.point === "number") Object.assign(row, { home_pt: h.point, home_px: h.price, away_px: a?.price });
      } else if (m.key === "totals") {
        const o = m.outcomes.find(x => x.name === "Over");
        const u = m.outcomes.find(x => x.name === "Under");
        if (o && typeof o.point === "number") Object.assign(row, { total: o.point, over_px: o.price, under_px: u?.price });
      }
    }
    if (row.home_pt != null || row.total != null) books.push(row);
  }
  return { id: ev.id, commence: ev.commence_time, home: ev.home_team, away: ev.away_team, books };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...cors(origin), "Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "86400" } });
    }
    const url = new URL(request.url);
    if (request.method !== "GET" || url.pathname !== "/odds") return json({ error: "not found" }, 404, origin);

    const now = new Date();
    const cached = await env.ODDS.get("latest", "json");
    const budget = (await env.ODDS.get("budget", "json")) || { day: "", pulls: 0 };
    if (budget.day !== chicagoDay(now)) Object.assign(budget, { day: chicagoDay(now), pulls: 0 });

    const serveCached = note => cached
      ? json({ ...cached, cached: true, note }, 200, origin)
      : json({ error: note }, 503, origin);

    if (cached && now - new Date(cached.fetched_at) < MIN_AGE_MIN * 60e3) return serveCached(null);
    if (cached && cached.remaining != null && cached.remaining < RESERVE) return serveCached("Monthly odds credits are nearly used up; showing the last pull.");
    if (budget.pulls >= MAX_PULLS_PER_DAY) return serveCached(`Daily limit of ${MAX_PULLS_PER_DAY} odds pulls reached; showing the last pull.`);

    const api = new URL(ODDS_URL);
    api.search = new URLSearchParams({
      apiKey: env.ODDS_API_KEY, regions: "us", markets: "spreads,totals",
      oddsFormat: "american", dateFormat: "iso",
    });
    let resp;
    try {
      resp = await fetch(api);
    } catch (e) {
      return serveCached("The odds service didn't respond; showing the last pull.");
    }
    budget.pulls += 1;
    await env.ODDS.put("budget", JSON.stringify(budget));
    if (!resp.ok) return serveCached(`The odds service answered ${resp.status}; showing the last pull.`);

    const events = (await resp.json()).map(trim);
    const remaining = Number(resp.headers.get("x-requests-remaining"));
    const body = {
      fetched_at: now.toISOString(),
      remaining: Number.isFinite(remaining) ? remaining : null,
      pulls_today: budget.pulls,
      events,
    };
    await env.ODDS.put("latest", JSON.stringify(body));
    return json({ ...body, cached: false, note: null }, 200, origin);
  },
};
