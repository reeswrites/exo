/**
 * The second door, end to end (ADR-0021), against a running instance.
 *
 *   npx wrangler dev --local-protocol https --var AUTH_TOKEN:devtoken --var OAUTH_ALLOW_DCR:true
 *   node test/oauth-flow.mjs https://127.0.0.1:8788 devtoken
 *
 * Needs a real server because almost nothing here is our code: the value of the
 * run is that PKCE, single-use codes, refresh and audience binding behave as the
 * spec says against THIS configuration of the provider. A stub would only prove
 * the stub.
 *
 * Two things it will not do without help. The flow needs a registered client, so
 * it asks for dynamic registration — pass `--var OAUTH_ALLOW_DCR:true`, which is
 * off in production for the reason ADR-0021 §6 gives. And the provider refuses a
 * non-HTTPS issuer, so `--local-protocol https` is not optional; the certificate
 * is self-signed, which is why NODE_TLS_REJECT_UNAUTHORIZED is turned off below
 * for localhost only.
 *
 * ORDER MATTERS, and the comment at step 10 explains why. Reusing an
 * authorization code is theft as far as OAuth 2.1 is concerned: the provider
 * revokes the grant and every token minted from it. Checking replay early makes
 * every later assertion fail for a reason that has nothing to do with what it
 * was testing — which is exactly what happened when this file was first written.
 */

import { createHash, randomBytes } from "node:crypto";

const BASE = (process.argv[2] ?? "https://127.0.0.1:8788").replace(/\/$/, "");
const TOKEN = process.argv[3] ?? "devtoken";
const REDIRECT = "https://claude.ai/api/mcp/auth_callback";

if (new URL(BASE).hostname === "127.0.0.1" || new URL(BASE).hostname === "localhost") {
  process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
}

let failures = 0;
const check = (label, got, want) => {
  const good = JSON.stringify(got) === JSON.stringify(want);
  if (!good) failures++;
  console.log(`  ${good ? "ok  " : "FAIL"} ${label}: ${got}${good ? "" : `  (want ${want})`}`);
};

const post = (path, { form, json, headers } = {}) =>
  fetch(BASE + path, {
    method: "POST",
    redirect: "manual",
    headers: {
      ...(form ? { "content-type": "application/x-www-form-urlencoded" } : {}),
      ...(json ? { "content-type": "application/json" } : {}),
      ...headers,
    },
    body: form ? new URLSearchParams(form) : json ? JSON.stringify(json) : undefined,
  });

const b64url = (b) => b.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

// 1 — a client to be
const reg = await post("/oauth/register", {
  json: {
    client_name: "exo test",
    redirect_uris: [REDIRECT],
    grant_types: ["authorization_code", "refresh_token"],
    response_types: ["code"],
    token_endpoint_auth_method: "none",
  },
});
check("register", reg.status, 201);
const clientId = (await reg.json()).client_id;

// 2 — authorize, with PKCE the server is required to enforce
const verifier = b64url(randomBytes(48));
const challenge = b64url(createHash("sha256").update(verifier).digest());
const query = new URLSearchParams({
  response_type: "code",
  client_id: clientId,
  redirect_uri: REDIRECT,
  code_challenge: challenge,
  code_challenge_method: "S256",
  state: "test-state",
  scope: "exo:read offline_access",
  resource: `${BASE}/mcp`,
});
const consent = await fetch(`${BASE}/authorize?${query}`);
const page = await consent.text();
check("consent page", consent.status, 200);
check("consent names the redirect host", page.includes("claude.ai"), true);
const stateBlob = page.match(/name="state" value="([^"]+)"/)?.[1] ?? "";
check("consent carries state", stateBlob.length > 0, true);

// 3 — the gate
const wrong = await post(`/authorize?${query}`, { form: { state: stateBlob, token: "wrong" } });
check("wrong token at consent", wrong.status, 401);

const approved = await post(`/authorize?${query}`, { form: { state: stateBlob, token: TOKEN } });
check("approve redirects", approved.status, 302);
const back = new URL(approved.headers.get("location"));
check("redirect target", `${back.origin}${back.pathname}`, REDIRECT);
check("state echoed", back.searchParams.get("state"), "test-state");
const code = back.searchParams.get("code");
check("code issued", Boolean(code), true);

// 4 — exchange
const exchange = (extra) =>
  post("/oauth/token", {
    form: { grant_type: "authorization_code", code, client_id: clientId, redirect_uri: REDIRECT, ...extra },
  });

check("PKCE mismatch rejected", (await exchange({ code_verifier: "x".repeat(60) })).status, 400);

const issued = await exchange({ code_verifier: verifier });
check("token exchange", issued.status, 200);
const { access_token: access, refresh_token: refresh } = await issued.json();
check("access token", Boolean(access), true);
check("refresh token (offline_access)", Boolean(refresh), true);

// 5 — the grant door, and the header door it must not have disturbed
const init = {
  jsonrpc: "2.0",
  id: 1,
  method: "initialize",
  params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "test", version: "1" } },
};
const viaGrant = await post("/mcp", { json: init, headers: { authorization: `Bearer ${access}` } });
check("initialize through the grant door", viaGrant.status, 200);
if (viaGrant.status === 200) {
  check("version negotiated", (await viaGrant.json()).result.protocolVersion, "2025-06-18");
}
check(
  "forged access token",
  (await post("/mcp", { json: init, headers: { authorization: "Bearer forged" } })).status,
  401
);

const ping = { jsonrpc: "2.0", id: 1, method: "ping" };
check(
  "header door still open",
  (await post("/", { json: ping, headers: { authorization: `Bearer ${TOKEN}` } })).status,
  200
);
// The two credentials are not interchangeable, and this is the assertion that
// says so: a grant is not a header token, and the header door must not accept
// one just because it looks like a bearer.
check(
  "a grant is not a header credential",
  (await post("/", { json: ping, headers: { authorization: `Bearer ${access}` } })).status,
  401
);

// 6 — refresh
const refreshed = await post("/oauth/token", {
  form: { grant_type: "refresh_token", refresh_token: refresh, client_id: clientId },
});
check("refresh", refreshed.status, 200);
if (refreshed.status === 200) {
  check("refresh issues a new access token", (await refreshed.json()).access_token !== access, true);
}

// 7 — replay, LAST. See the header comment.
check("code replay rejected", (await exchange({ code_verifier: verifier })).status, 400);
check(
  "replay revoked the whole grant",
  (await post("/mcp", { json: init, headers: { authorization: `Bearer ${access}` } })).status,
  401
);

console.log(failures ? `\n${failures} failed` : "\nall pass");
process.exit(failures ? 1 : 0);
