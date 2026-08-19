/**
 * The Exo read surface — MCP over HTTP, for AI assistants.
 *
 * Reads only, permanently (ADR-0006). There is no write path here and adding one
 * is not a future feature: the moment an assistant can write, machine prose
 * enters ground truth, which the vault's whole authorship model exists to
 * prevent.
 *
 * What is reachable was decided upstream by `wh publish` (ADR-0005). Held
 * material is not filtered here — it is absent from this database entirely, so
 * no bug in this file and no injected instruction can reach it.
 *
 * JSON-RPC is hand-rolled rather than pulled from the MCP SDK: the protocol
 * surface used here is five methods, and a dependency-free Worker is one less
 * thing that can change under a corpus this personal.
 */
import { TOOLS } from "./tools.js";

const PROTOCOL_VERSION = "2024-11-05";

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

const rpcResult = (id, result) => json({ jsonrpc: "2.0", id, result });
const rpcError = (id, code, message) => json({ jsonrpc: "2.0", id, error: { code, message } });

/** Constant-time compare, so the token cannot be recovered by timing the endpoint. */
function tokenOk(presented, expected) {
  if (typeof presented !== "string" || typeof expected !== "string") return false;
  if (presented.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < presented.length; i++) diff |= presented.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

/**
 * What Cloudflare already knows about the caller. Free to collect — `req.cf` is
 * filled by the edge before the isolate runs — and it is the only thing that can
 * answer the question an allowlist needs answered first: which egress does a
 * legitimate client actually come from.
 *
 * Phase 1 of origin gating (ADR-0007). Observation only: nothing here rejects
 * anything. An allowlist written before the answer is known locks out the
 * client it was meant to protect.
 *
 * Note what is NOT here: `Origin`. Poke and every other assistant call this
 * server-to-server, so there is no Origin header and no preflight — CORS-shaped
 * gating would be unenforced decoration. IP and ASN are what exist.
 */
function callerFacts(req) {
  const cf = req.cf ?? {};
  return {
    ip: req.headers.get("cf-connecting-ip") ?? "",
    asn: cf.asn ?? 0,
    org: cf.asOrganization ?? "",
    country: cf.country ?? "",
    colo: cf.colo ?? "",
    // The client names itself here or nowhere. Bounded because it is attacker-set.
    ua: (req.headers.get("user-agent") ?? "").slice(0, 200),
  };
}

/**
 * One-per-minute-per-ip write suppression, for rejected callers only.
 *
 * Rolling up bounds ROWS. It does not bound WRITES, and the rejected path is
 * reachable by anyone who finds the URL — so an unthrottled log turns a probe
 * into a bill. Module scope, so it lives as long as the isolate and no longer,
 * which is the right lifetime: a cold isolate re-logging a prober is a cost of
 * one row, not a hole.
 */
const recentlyLogged = new Map();
function throttled(ip) {
  const key = `${ip}:${Math.floor(Date.now() / 60000)}`;
  if (recentlyLogged.has(key)) return true;
  if (recentlyLogged.size > 500) recentlyLogged.clear(); // ephemeral; a reset beats an LRU
  recentlyLogged.set(key, 1);
  return false;
}

/**
 * Where calls come from, rolled up by day. Both outcomes: `ok` is the allowlist
 * candidate, `denied` is someone holding a wrong token, and the second is the
 * one worth an alarm.
 *
 * Rolled up rather than appended because the question is "which distinct places
 * call", not "how did request 4,812 go" — wh_audit already holds per-call detail
 * and now carries the ip alongside it.
 *
 * Never fails the request: an unobserved answer beats an outage.
 */
async function recordCaller(env, req, outcome) {
  try {
    const f = callerFacts(req);
    if (outcome !== "ok" && throttled(f.ip)) return;
    await env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS wh_callers (
         day TEXT, ip TEXT, asn INTEGER, org TEXT, country TEXT, colo TEXT,
         ua TEXT, outcome TEXT, n INTEGER, first_seen TEXT, last_seen TEXT,
         PRIMARY KEY (day, ip, asn, ua, outcome)
       )`
    ).run();
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO wh_callers
         (day, ip, asn, org, country, colo, ua, outcome, n, first_seen, last_seen)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
       ON CONFLICT (day, ip, asn, ua, outcome) DO UPDATE SET
         n = n + 1,
         last_seen = excluded.last_seen,
         org = excluded.org,
         country = excluded.country,
         colo = excluded.colo`
    )
      .bind(now.slice(0, 10), f.ip, f.asn, f.org, f.country, f.colo, f.ua, outcome, now, now)
      .run();
  } catch (_) {
    /* observation must never take the surface down */
  }
}

/**
 * Append-only call log (ADR-0007). This is the only control here that DETECTS
 * rather than limits — the other three bound a leak, this one is how you find
 * out it happened. Never fails the request: an unlogged answer beats an outage.
 *
 * Carries the caller's ip and asn as well as the call, because with more than
 * one token holder (the owner's own machine, and whatever assistant is connected)
 * "what was asked" without "by whom" cannot tell a normal week from a compromise.
 *
 * Lives outside the published set, so `import.sh` protects `wh_` and will not
 * reconcile it away. The ip/asn columns require migrations/0001 to have run
 * against the live database BEFORE this version deploys — see the README.
 */
async function audit(env, req, tool, args, rows) {
  try {
    await env.DB.prepare(
      `CREATE TABLE IF NOT EXISTS wh_audit (
         ts TEXT, tool TEXT, args TEXT, rows INTEGER, ip TEXT, asn INTEGER
       )`
    ).run();
    const f = callerFacts(req);
    await env.DB.prepare(
      `INSERT INTO wh_audit (ts, tool, args, rows, ip, asn) VALUES (?, ?, ?, ?, ?, ?)`
    )
      .bind(new Date().toISOString(), tool, JSON.stringify(args ?? {}).slice(0, 500), rows, f.ip, f.asn)
      .run();
  } catch (_) {
    /* logging must never take the surface down */
  }
}

async function handleRpc(req, env, body) {
  const { id, method, params } = body;

  switch (method) {
    case "initialize":
      return rpcResult(id, {
        protocolVersion: PROTOCOL_VERSION,
        capabilities: { tools: {}, resources: {} },
        serverInfo: { name: "exo", version: "1.0.0" },
        instructions:
          `Personal context for ${env.OWNER_NAME || "the owner"}, from their own ` +
          "records. Read the exo://brief resource first — it says who they " +
          "are, what they are currently thinking about, and what you can ask for. " +
          "Quote their words as theirs; everything here is verbatim or counted, " +
          "never written about them.",
      });

    case "notifications/initialized":
      return new Response(null, { status: 202 });

    case "ping":
      return rpcResult(id, {});

    // The brief is the point of the whole surface: pushed, not pulled. A
    // pull-only surface cannot fix "I forget things exist" — it requires already
    // knowing what to ask for.
    case "resources/list":
      return rpcResult(id, {
        resources: [
          {
            uri: "exo://brief",
            name: "Standing context",
            description:
              "Who the owner is, what they are currently circling, how fresh each source is, and what this surface can answer. Load this before anything else.",
            mimeType: "text/markdown",
          },
        ],
      });

    case "resources/read": {
      // Both spellings. The URI is advertised in resources/list, but a client
      // that pinned the old one during the warehouse era must not break on the
      // day the name changes — renaming a published address is a migration,
      // not an edit.
      if (params?.uri !== "exo://brief" && params?.uri !== "warehouse://brief")
        return rpcError(id, -32602, "unknown resource");
      const obj = await env.VECTORS.get("brief.md");
      if (!obj) return rpcError(id, -32603, "brief not published");
      const text = await obj.text();
      await audit(env, req, "resources/read", { uri: params.uri }, 1);
      return rpcResult(id, {
        contents: [{ uri: params.uri, mimeType: "text/markdown", text }],
      });
    }

    case "tools/list":
      return rpcResult(id, {
        tools: Object.entries(TOOLS).map(([name, t]) => ({
          name,
          description: t.description,
          inputSchema: t.schema,
        })),
      });

    case "tools/call": {
      const tool = TOOLS[params?.name];
      if (!tool) return rpcError(id, -32602, `unknown tool: ${params?.name}`);
      try {
        const result = await tool.run(env, params.arguments ?? {});
        await audit(env, req, params.name, params.arguments, result.rows?.length ?? 0);
        return rpcResult(id, {
          content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
        });
      } catch (err) {
        await audit(env, req, params.name, params.arguments, -1);
        return rpcError(id, -32603, `tool failed: ${err.message}`);
      }
    }

    default:
      return rpcError(id, -32601, `unknown method: ${method}`);
  }
}

export default {
  async fetch(req, env, ctx) {
    if (req.method === "GET") {
      // Deliberately says nothing about what is inside.
      return new Response("exo read surface\n", { status: 200 });
    }
    if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

    // Token in a header, never the URL — URLs reach logs, referrers and history
    // (ADR-0007). This proves the CALLER is who they say; it cannot prove they
    // act on the owner's intent, which is why the caps and the log exist.
    //
    // Several shapes accepted because clients disagree about which header an
    // "API key" belongs in, and the failure mode is an opaque 401 with no way to
    // tell a wrong key from a wrong header. Same single secret either way.
    const candidates = [];
    const auth = req.headers.get("authorization") ?? "";
    if (auth.startsWith("Bearer ")) candidates.push(auth.slice(7));
    else if (auth) candidates.push(auth);
    for (const h of ["x-api-key", "api-key", "x-auth-token"]) {
      const v = req.headers.get(h);
      if (v) candidates.push(v);
    }

    if (!env.AUTH_TOKEN || !candidates.some((c) => tokenOk(c.trim(), env.AUTH_TOKEN))) {
      // Awaited, not deferred: the response is about to be returned and there is
      // no request left to hang the write off. A rejected caller is the one you
      // most want on the record.
      await recordCaller(env, req, "denied");
      return new Response("unauthorized", { status: 401 });
    }

    // Off the critical path — the caller waits for their answer, not for the
    // bookkeeping. Awaited when there is no ctx (the test harness), where
    // determinism matters more than latency.
    const observed = recordCaller(env, req, "ok");
    if (ctx?.waitUntil) ctx.waitUntil(observed);
    else await observed;

    let body;
    try {
      body = await req.json();
    } catch {
      return rpcError(null, -32700, "parse error");
    }

    if (Array.isArray(body)) {
      // Batches would let one authenticated request pull N caps' worth of data,
      // turning the per-call cap into no cap at all.
      return rpcError(null, -32600, "batch requests are not accepted");
    }
    return handleRpc(req, env, body);
  },
};
