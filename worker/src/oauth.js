/**
 * The second door (ADR-0021).
 *
 * Everything here exists to satisfy a handshake, not to model identity. There
 * is one person behind this record, the credential that authorises a grant is
 * the same `AUTH_TOKEN` the header door already uses, and the access token this
 * issues is a different string with a lifetime — nothing more.
 *
 * The split of responsibilities:
 *
 *   /            the header door, untouched (ADR-0007). Poke and Claude Code.
 *   /mcp         the same surface behind a grant. Claude web, ChatGPT, anything
 *                that speaks the MCP authorization spec.
 *   /authorize   the login and consent screen, gated on AUTH_TOKEN
 *   /oauth/token issued and refreshed by the provider
 *   /.well-known/oauth-authorization-server   RFC 8414, by the provider
 *   /.well-known/oauth-protected-resource/mcp RFC 9728, by the provider
 *
 * Two doors and two paths rather than two doors on one path, because RFC 9728
 * requires the `resource` field to match the URL the user typed exactly, and
 * the URL Poke has typed is the root. Giving the grant door its own path is
 * what lets the header door stay byte-for-byte what it was.
 */

import OAuthProvider from "@cloudflare/workers-oauth-provider";
import { grantDoor, headerDoor, tokenOk } from "./index.js";

const html = (body, status = 200) =>
  new Response(body, { status, headers: { "content-type": "text/html;charset=UTF-8" } });

/**
 * Guessing is the threat a login page has and an API token does not, so it gets
 * the control ADR-0007 refused for exfiltration. Ten attempts per IP per minute,
 * counted in the grant store because that is the only mutable thing here.
 *
 * Fails OPEN on a KV error, deliberately: this defends against a guesser, and a
 * store hiccup locking the owner out of their own record is the worse failure.
 */
async function overRate(env, ip) {
  const key = `rl:${ip}:${Math.floor(Date.now() / 60000)}`;
  try {
    const n = Number((await env.OAUTH_KV.get(key)) ?? 0) + 1;
    await env.OAUTH_KV.put(key, String(n), { expirationTtl: 120 });
    return n > 10;
  } catch {
    return false;
  }
}

const page = (inner) => `<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>exo</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.55 ui-serif, Georgia, serif; max-width: 30rem; margin: 12vh auto; padding: 0 1.5rem; }
  h1 { font-size: 1.15rem; letter-spacing: .01em; margin: 0 0 .25rem; }
  p { margin: .6rem 0; }
  .dim { opacity: .7; font-size: .9rem; }
  code { font: .85rem ui-monospace, SFMono-Regular, Menlo, monospace; word-break: break-all; }
  input[type=password] { width: 100%; padding: .55rem .6rem; font: inherit; box-sizing: border-box; }
  button { margin-top: .9rem; padding: .55rem 1.1rem; font: inherit; cursor: pointer; }
  .warn { border-left: 3px solid currentColor; padding-left: .8rem; opacity: .85; }
</style>
${inner}`;

/**
 * The consent screen. It names the client and — because the MCP authorization
 * spec requires it, and because a loopback redirect is impersonable by any
 * local process — the redirect host, plainly, before asking for the secret.
 */
function consentPage(clientName, redirectUri, state, error) {
  let host = redirectUri;
  let loopback = false;
  try {
    const u = new URL(redirectUri);
    host = u.host;
    loopback = u.hostname === "localhost" || u.hostname === "127.0.0.1";
  } catch {
    /* show it raw if it will not parse */
  }
  return page(`
<h1>Connect ${clientName ? escapeHtml(clientName) : "a client"} to this record</h1>
<p class="dim">It will be able to read what this instance publishes — notes, atoms,
ratings, drafts — under the same caps and the same call log as every other caller.
It will never be able to write.</p>
<p>Redirecting to <code>${escapeHtml(host)}</code> after you approve.</p>
${
  loopback
    ? `<p class="warn dim">That is an address on this machine. Any local process can
       claim it, so approve this only if you started the sign-in yourself, just now.</p>`
    : ""
}
${error ? `<p class="warn">${escapeHtml(error)}</p>` : ""}
<form method="POST">
  <input type="hidden" name="state" value="${escapeHtml(state)}">
  <label for="t">Token</label>
  <input id="t" name="token" type="password" autocomplete="current-password" autofocus required>
  <button type="submit">Approve</button>
</form>`);
}

const escapeHtml = (s) =>
  String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );

/**
 * Everything the provider does not claim. Two jobs: run the consent screen, and
 * hand every other path to the header door — which is what keeps the root URL
 * answering exactly as it did before this file existed.
 */
const consentHandler = {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    if (url.pathname !== "/authorize") return headerDoor.fetch(req, env, ctx);

    const ip = req.headers.get("cf-connecting-ip") ?? "?";

    let auth;
    try {
      auth = await env.OAUTH_PROVIDER.parseAuthRequest(req);
    } catch (err) {
      return html(page(`<h1>That sign-in link is not valid</h1>
        <p class="dim">${escapeHtml(err?.message ?? "unknown error")}</p>`), 400);
    }

    const client = await env.OAUTH_PROVIDER.lookupClient(auth.clientId).catch(() => null);
    const name = client?.clientName ?? client?.clientId ?? auth.clientId;
    const state = btoa(JSON.stringify(auth));

    if (req.method === "GET") return html(consentPage(name, auth.redirectUri, state));
    if (req.method !== "POST") return new Response("method not allowed", { status: 405 });

    if (await overRate(env, ip)) {
      return html(consentPage(name, auth.redirectUri, state, "Too many attempts. Wait a minute."), 429);
    }

    const form = await req.formData();
    const presented = String(form.get("token") ?? "").trim();

    // The same secret, compared the same way, as the header door (ADR-0021 §2).
    if (!env.AUTH_TOKEN || !tokenOk(presented, env.AUTH_TOKEN)) {
      // A refused login is exactly the event the caller log exists for, and it
      // is the one an attacker most wants unrecorded.
      ctx?.waitUntil?.(
        env.DB.prepare(
          `INSERT INTO wh_callers (day, ip, asn, org, country, colo, ua, outcome, n, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'denied:authorize', 1, ?, ?)
           ON CONFLICT (day, ip, asn, ua, outcome) DO UPDATE SET n = n + 1, last_seen = excluded.last_seen`
        )
          .bind(
            new Date().toISOString().slice(0, 10),
            ip,
            req.cf?.asn ?? null,
            req.cf?.asOrganization ?? null,
            req.cf?.country ?? null,
            req.cf?.colo ?? null,
            (req.headers.get("user-agent") ?? "").slice(0, 200),
            new Date().toISOString(),
            new Date().toISOString()
          )
          .run()
          .catch(() => {})
      );
      return html(consentPage(name, auth.redirectUri, state, "That is not the token."), 401);
    }

    const { redirectTo } = await env.OAUTH_PROVIDER.completeAuthorization({
      request: JSON.parse(atob(String(form.get("state") ?? state))),
      userId: env.OWNER_LABEL || "owner",
      metadata: { grantedAt: new Date().toISOString() },
      scope: auth.scope?.length ? auth.scope : ["exo:read"],
      // Read by nothing on the surface today. The door is recorded from the
      // request path, not from here, so this stays deliberately thin: props are
      // encrypted per-grant and the less that lives in them the better.
      props: { door: "oauth" },
    });
    return Response.redirect(redirectTo, 302);
  },
};

/**
 * Built per origin rather than once at module load, because RFC 9728 pins the
 * resource identifier to the exact URL the user typed and the worker only
 * learns that from the request. Cached on the origin, so this is one object per
 * hostname the instance answers on, not one per request.
 */
const built = new Map();

export function oauthProvider(req, env) {
  const origin = new URL(req.url).origin;
  const key = `${origin}|${env.OAUTH_ALLOW_DCR === "true"}`;
  const hit = built.get(key);
  if (hit) return hit;

  const provider = new OAuthProvider({
    apiRoute: "/mcp",
    apiHandler: grantDoor,
    defaultHandler: consentHandler,
    authorizeEndpoint: "/authorize",
    tokenEndpoint: "/oauth/token",

    // Claude prefers a Client ID Metadata Document when the metadata advertises
    // it, and the documentation is explicit that CIMD is preferable to dynamic
    // registration: DCR mints a fresh client on every connection and leaves them
    // all in the store. CIMD needs no credential pasted anywhere and no open
    // registration endpoint — which is the whole of ADR-0021 §6's objection.
    clientIdMetadataDocumentEnabled: true,

    // Off unless an instance asks for it, for the reason above: an open
    // /register is an unauthenticated write endpoint that exists to be found.
    ...(env.OAUTH_ALLOW_DCR === "true"
      ? { clientRegistrationEndpoint: "/oauth/register", clientRegistrationTTL: 60 * 60 * 24 * 30 }
      : {}),

    // `offline_access` is what makes Claude ask for a refresh token; without it
    // every grant dies at the first expiry and the owner logs in again.
    scopesSupported: ["exo:read", "offline_access"],

    resourceMetadata: {
      resource: `${origin}/mcp`,
      authorization_servers: [origin],
      scopes_supported: ["exo:read"],
      resource_name: "exo read surface",
    },
  });

  built.set(key, provider);
  return provider;
}
