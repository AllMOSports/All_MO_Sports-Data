/**
 * All MO Sports data server (Cloudflare Worker).
 *
 * Serves member-only JSON files from an R2 bucket, fast, from Cloudflare's network.
 *
 *   GET https://<worker>/school_reports/st-vincent.json?pass=<pass from WordPress>
 *
 * How access works
 *  - WordPress gives each logged-in visitor a short-lived "pass": their level (account, pro or
 *    program) and an expiry time, signed with a secret only WordPress and this Worker know.
 *  - This Worker checks the signature and the expiry, looks up which level the file needs in
 *    _access.json (uploaded to the bucket from access.json in the data repo), and only then
 *    returns the file.
 *
 * Settings (Cloudflare dashboard → the Worker → Settings):
 *  - R2 bucket binding named DATA
 *  - Secret PASS_SECRET (the same value as AMOS_PASS_SECRET in WordPress's wp-config.php)
 *  - Variable ALLOWED_ORIGINS, e.g. "https://allmosports.com,https://www.allmosports.com"
 *
 * The pass rides in the address (?pass=) rather than a header on purpose: a plain GET needs no
 * extra "preflight" round trip from the browser, which keeps every file to one request.
 */

const LEVELS = ["public", "account", "pro", "program"];
const ACCESS_TTL_MS = 5 * 60 * 1000; // re-read _access.json at most every 5 minutes
let accessCache = null;
let accessLoaded = 0;

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const allowed = (env.ALLOWED_ORIGINS || "").split(",").map((s) => s.trim()).filter(Boolean);
    const cors = {};
    if (allowed.includes(origin)) {
      cors["Access-Control-Allow-Origin"] = origin;
      cors["Vary"] = "Origin";
    }

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: { ...cors, "Access-Control-Allow-Methods": "GET", "Access-Control-Max-Age": "86400" } });
    }
    if (request.method !== "GET") {
      return reply(405, { error: "method" }, cors);
    }

    const url = new URL(request.url);
    const key = url.pathname.replace(/^\/+/, "");

    if (key === "" || key === "health") {
      return reply(200, { ok: true }, cors);
    }
    // Only plain file names like school_reports/st-vincent.json; never "..", never _access.json.
    if (!/^[a-z0-9_][a-z0-9_\-\/]*\.json$/.test(key) || key.includes("..") || key.includes("//")) {
      return reply(404, { error: "not_found" }, cors);
    }

    // 1. Which level does this file need?
    const access = await loadAccess(env);
    if (!access) {
      return reply(503, { error: "no_access_rules" }, cors);
    }
    const need = levelForFile(access, key);
    if (need === null) {
      return reply(404, { error: "not_found" }, cors); // not listed in access.json = not served
    }

    // 2. Does the visitor's pass cover it?
    if (need > 0) {
      const pass = await readPass(url.searchParams.get("pass") || "", env.PASS_SECRET);
      if (!pass.ok) {
        return reply(401, { error: pass.reason }, cors); // the page asks WordPress for a fresh pass and retries
      }
      if (pass.level < need) {
        return reply(403, { error: "level", need: LEVELS[need] }, cors);
      }
    }

    // 3. Serve it.
    const obj = await env.DATA.get(key);
    if (!obj) {
      return reply(404, { error: "not_found" }, cors);
    }
    return new Response(obj.body, {
      status: 200,
      headers: {
        ...cors,
        "Content-Type": "application/json; charset=utf-8",
        // Private: browsers may keep it 5 minutes, shared caches never.
        "Cache-Control": "private, max-age=300",
        "ETag": obj.httpEtag,
      },
    });
  },
};

function reply(status, body, cors) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

async function loadAccess(env) {
  const now = Date.now();
  if (accessCache) {
    if (now - accessLoaded < ACCESS_TTL_MS) {
      return accessCache;
    }
  }
  try {
    const obj = await env.DATA.get("_access.json");
    if (obj) {
      accessCache = await obj.json();
      accessLoaded = now;
    }
  } catch (e) {
    // keep the last good copy
  }
  return accessCache;
}

// The longest matching folder in access.json decides; returns a level number, or null if unlisted.
function levelForFile(access, key) {
  let best = null;
  let bestLen = -1;
  const files = access.files || {};
  for (const prefix of Object.keys(files)) {
    if (key.startsWith(prefix)) {
      if (prefix.length > bestLen) {
        best = files[prefix];
        bestLen = prefix.length;
      }
    }
  }
  if (best === null) {
    return null;
  }
  const featureLevel = (access.features || {})[best];
  const n = LEVELS.indexOf(featureLevel || "program"); // unknown feature: most restrictive
  return n < 0 ? LEVELS.length - 1 : n;
}

// Pass format: base64url(JSON {u, l, e}) + "." + base64url(HMAC-SHA256 of the first part)
async function readPass(pass, secret) {
  if (!secret) {
    return { ok: false, reason: "server_not_configured" };
  }
  const dot = pass.indexOf(".");
  if (dot < 1) {
    return { ok: false, reason: "no_pass" };
  }
  const body = pass.slice(0, dot);
  const sig = pass.slice(dot + 1);
  const keyObj = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["verify"]);
  let sigBytes;
  try {
    sigBytes = fromB64url(sig);
  } catch (e) {
    return { ok: false, reason: "bad_pass" };
  }
  const good = await crypto.subtle.verify("HMAC", keyObj, sigBytes, new TextEncoder().encode(body));
  if (!good) {
    return { ok: false, reason: "bad_pass" };
  }
  let data;
  try {
    data = JSON.parse(new TextDecoder().decode(fromB64url(body)));
  } catch (e) {
    return { ok: false, reason: "bad_pass" };
  }
  if (!data.e || data.e * 1000 < Date.now()) {
    return { ok: false, reason: "expired" };
  }
  const level = LEVELS.indexOf(data.l);
  return { ok: true, level: level < 0 ? 0 : level, user: data.u };
}

function fromB64url(s) {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    out[i] = bin.charCodeAt(i);
  }
  return out;
}
