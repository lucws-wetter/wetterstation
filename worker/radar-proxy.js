/**
 * Radar-Proxy für die Wetterseite (Cloudflare Worker).
 *
 * Hält die meteoCORE-Zugangsdaten geheim, holt sich ein Token (5 Minuten gültig) und reicht
 * ausschließlich Radar-Daten an die Wetterseite weiter. Kacheln mit festem Zeitpunkt werden
 * zwischengespeichert, damit die meteoCORE-API pro Kachel nur einmal gefragt wird.
 *
 * Geheimnisse (in Cloudflare unter Settings → Variables and Secrets, Typ "Secret"):
 *   MC_CLIENT_ID      Client-ID aus account.meteocore.com → API-Zugänge
 *   MC_CLIENT_SECRET  Client-Secret dazu
 * Optional (Variable): ALLOWED_ORIGINS  kommagetrennt, Standard: https://lucws-wetter.github.io
 */
const API = "https://api.meteocore.com";
const SOURCE = "[a-z0-9-]+";
const ALLOWED_PATHS = [
  new RegExp(`^/api/v1/radar$`),
  new RegExp(`^/api/v1/radar/${SOURCE}/(timestamps|legend|info|palettes)$`),
  new RegExp(`^/api/v1/radar/${SOURCE}/tiles/(latest|\\d{12})/\\d{3}/\\d{1,2}/\\d{1,6}/\\d{1,6}\\.png$`),
];
const ALLOWED_QUERY = ["palette"];

let token = null;
let tokenExpires = 0;

async function getToken(env) {
  if (token && Date.now() < tokenExpires - 30_000) return token;
  const res = await fetch(`${API}/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "client_credentials", client_id: env.MC_CLIENT_ID, client_secret: env.MC_CLIENT_SECRET }),
  });
  if (!res.ok) throw new Error(`Token-Abruf fehlgeschlagen (${res.status})`);
  const j = await res.json();
  token = j.access_token;
  tokenExpires = Date.now() + (j.expires_in || 300) * 1000;
  return token;
}

function origins(env) {
  return (env.ALLOWED_ORIGINS || "https://lucws-wetter.github.io").split(",").map(s => s.trim()).filter(Boolean);
}

function corsHeaders(req, env) {
  const list = origins(env);
  const o = req.headers.get("Origin");
  return { "Access-Control-Allow-Origin": o && list.includes(o) ? o : list[0], "Vary": "Origin" };
}

/** Anfragen von fremden Seiten abweisen (Bilder schicken einen Referer, fetch einen Origin). */
function fromAllowedSite(req, env) {
  const list = origins(env);
  const o = req.headers.get("Origin");
  if (o) return list.includes(o);
  const ref = req.headers.get("Referer");
  if (!ref) return true; // manche Browser lassen den Referer weg
  try { return list.includes(new URL(ref).origin); } catch { return false; }
}

export default {
  async fetch(req, env, ctx) {
    const cors = corsHeaders(req, env);
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...cors, "Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "86400" } });
    }
    if (req.method !== "GET") return new Response("Nur GET", { status: 405, headers: cors });
    if (!fromAllowedSite(req, env)) return new Response("Nicht erlaubt", { status: 403, headers: cors });

    const url = new URL(req.url);
    if (!ALLOWED_PATHS.some(re => re.test(url.pathname))) return new Response("Nicht gefunden", { status: 404, headers: cors });
    const q = new URLSearchParams();
    for (const k of ALLOWED_QUERY) if (url.searchParams.has(k)) q.set(k, url.searchParams.get(k).slice(0, 32));
    const upstream = `${API}${url.pathname}${q.toString() ? "?" + q : ""}`;

    const fixedTile = url.pathname.includes("/tiles/") && !url.pathname.includes("/tiles/latest/");
    const cacheKey = new Request(upstream, { method: "GET" });
    const cache = caches.default;
    let res = await cache.match(cacheKey);
    if (!res) {
      let up;
      try {
        up = await fetch(upstream, { headers: { Authorization: `Bearer ${await getToken(env)}` } });
        if (up.status === 401) { token = null; up = await fetch(upstream, { headers: { Authorization: `Bearer ${await getToken(env)}` } }); }
      } catch (e) {
        return new Response("meteoCORE nicht erreichbar", { status: 502, headers: cors });
      }
      res = new Response(up.body, up);
      res.headers.set("Cache-Control", up.ok ? (fixedTile ? "public, max-age=86400" : "public, max-age=60") : "no-store");
      res.headers.delete("Set-Cookie");
      if (up.ok) ctx.waitUntil(cache.put(cacheKey, res.clone()));
    }
    res = new Response(res.body, res);
    for (const [k, v] of Object.entries(cors)) res.headers.set(k, v);
    return res;
  },
};
