const storageKeys = {
  verifier: "sovereignflow.pkce.verifier",
  state: "sovereignflow.oidc.state",
  tokens: "sovereignflow.oidc.tokens",
};

const elements = {
  sessionStatus: document.querySelector("#session-status"),
  signIn: document.querySelector("#sign-in"),
  signOut: document.querySelector("#sign-out"),
  identityCard: document.querySelector("#identity-card"),
  userName: document.querySelector("#user-name"),
  tenantId: document.querySelector("#tenant-id"),
  aclLabels: document.querySelector("#acl-labels"),
  classificationLevel: document.querySelector("#classification-level"),
  form: document.querySelector("#query-form"),
  domain: document.querySelector("#domain"),
  query: document.querySelector("#query"),
  diagnostics: document.querySelector("#diagnostics"),
  submit: document.querySelector("#submit-query"),
  requestStatus: document.querySelector("#request-status"),
  requestId: document.querySelector("#request-id"),
  emptyResult: document.querySelector("#empty-result"),
  result: document.querySelector("#result"),
  answer: document.querySelector("#answer"),
  citations: document.querySelector("#citations"),
  pipelineTrace: document.querySelector("#pipeline-trace"),
  diagnosticsOutput: document.querySelector("#diagnostics-output"),
  rawResponse: document.querySelector("#raw-response"),
};

let configuration;
let tokens;
let capabilities = [];

function randomBase64Url(byteLength) {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64Url(bytes);
}

function base64Url(bytes) {
  return btoa(String.fromCharCode(...bytes))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

async function sha256Base64Url(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return base64Url(new Uint8Array(digest));
}

function callbackUrl() {
  return `${window.location.origin}/app/`;
}

function decodeJwtPayload(token) {
  const encoded = token.split(".")[1];
  if (!encoded) {
    throw new Error("The identity provider returned an invalid access token.");
  }
  const normalized = encoded.replaceAll("-", "+").replaceAll("_", "/");
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(padded), char => char.charCodeAt(0))));
}

function readTokens() {
  const raw = sessionStorage.getItem(storageKeys.tokens);
  return raw ? JSON.parse(raw) : null;
}

function saveTokens(value) {
  tokens = value;
  sessionStorage.setItem(storageKeys.tokens, JSON.stringify(value));
}

function clearSession() {
  tokens = null;
  sessionStorage.removeItem(storageKeys.tokens);
  sessionStorage.removeItem(storageKeys.verifier);
  sessionStorage.removeItem(storageKeys.state);
}

function tokenIsCurrent(accessToken) {
  const claims = decodeJwtPayload(accessToken);
  return Number(claims.exp || 0) * 1000 > Date.now() + 15000;
}

async function exchangeToken(parameters) {
  const response = await fetch(configuration.token_url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(parameters),
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error_description || payload.error || "Token exchange failed.");
  }
  saveTokens(payload);
}

async function completeLogin() {
  const parameters = new URLSearchParams(window.location.search);
  const code = parameters.get("code");
  const error = parameters.get("error");
  if (error) {
    throw new Error(parameters.get("error_description") || error);
  }
  if (!code) {
    return;
  }
  const expectedState = sessionStorage.getItem(storageKeys.state);
  const verifier = sessionStorage.getItem(storageKeys.verifier);
  if (!expectedState || parameters.get("state") !== expectedState || !verifier) {
    throw new Error("The login response could not be verified.");
  }
  await exchangeToken({
    grant_type: "authorization_code",
    client_id: configuration.client_id,
    code,
    code_verifier: verifier,
    redirect_uri: callbackUrl(),
  });
  sessionStorage.removeItem(storageKeys.verifier);
  sessionStorage.removeItem(storageKeys.state);
  history.replaceState({}, document.title, callbackUrl());
}

async function currentAccessToken() {
  if (!tokens || !tokens.access_token) {
    throw new Error("Sign in before running a query.");
  }
  if (tokenIsCurrent(tokens.access_token)) {
    return tokens.access_token;
  }
  if (!tokens.refresh_token) {
    clearSession();
    await renderSession();
    throw new Error("Your session expired. Sign in again.");
  }
  await exchangeToken({
    grant_type: "refresh_token",
    client_id: configuration.client_id,
    refresh_token: tokens.refresh_token,
  });
  await renderSession();
  return tokens.access_token;
}

async function startLogin() {
  const verifier = randomBase64Url(64);
  const state = randomBase64Url(32);
  sessionStorage.setItem(storageKeys.verifier, verifier);
  sessionStorage.setItem(storageKeys.state, state);
  const parameters = new URLSearchParams({
    client_id: configuration.client_id,
    redirect_uri: callbackUrl(),
    response_type: "code",
    scope: "openid profile email",
    state,
    code_challenge: await sha256Base64Url(verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${configuration.authorization_url}?${parameters}`);
}

function signOut() {
  const idToken = tokens && tokens.id_token;
  clearSession();
  const parameters = new URLSearchParams({
    client_id: configuration.client_id,
    post_logout_redirect_uri: callbackUrl(),
  });
  if (idToken) {
    parameters.set("id_token_hint", idToken);
  }
  window.location.assign(`${configuration.logout_url}?${parameters}`);
}

function setQueryEnabled(enabled) {
  elements.domain.disabled = !enabled;
  elements.query.disabled = !enabled;
  elements.diagnostics.disabled = !enabled;
  elements.submit.disabled = !enabled;
}

async function loadCatalog() {
  const accessToken = await currentAccessToken();
  const response = await fetch("/v1/catalog", {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload.error && payload.error.message;
    throw new Error(message || "Cannot load the capability catalog.");
  }
  capabilities = payload.capabilities;
  elements.domain.replaceChildren(
    ...capabilities.map(capability => {
      const option = document.createElement("option");
      option.value = capability.capability_id;
      option.textContent = `${capability.display_name} · ${capability.pipeline_name}`;
      return option;
    }),
  );
}

async function renderSession() {
  if (!tokens || !tokens.access_token) {
    elements.sessionStatus.textContent = "Not signed in";
    elements.signIn.hidden = false;
    elements.signOut.hidden = true;
    elements.identityCard.hidden = true;
    setQueryEnabled(false);
    return;
  }
  const claims = decodeJwtPayload(tokens.access_token);
  elements.sessionStatus.textContent = "Authenticated through OIDC";
  elements.signIn.hidden = true;
  elements.signOut.hidden = false;
  elements.identityCard.hidden = false;
  elements.userName.textContent =
    claims.name || claims.preferred_username || claims.sub || "Unknown user";
  elements.tenantId.textContent = claims.tenant_id || "Not provided";
  elements.aclLabels.textContent = (claims.acl_labels || []).join(", ") || "None";
  elements.classificationLevel.textContent =
    claims.max_classification_level === undefined
      ? "Not provided"
      : claims.max_classification_level;
  setQueryEnabled(false);
  await loadCatalog();
  setQueryEnabled(capabilities.length > 0);
}

function renderCitations(citations) {
  elements.citations.replaceChildren();
  if (!citations.length) {
    elements.citations.textContent = "No citations returned.";
    return;
  }
  for (const citation of citations) {
    const card = document.createElement("div");
    card.className = "citation";
    const title = document.createElement("strong");
    title.textContent = citation.source_id;
    const detail = document.createElement("span");
    detail.textContent =
      `${citation.chunk_id} · ${citation.score_type} · score ${Number(citation.score).toFixed(4)}`;
    card.append(title, detail);
    elements.citations.append(card);
  }
}

function renderResult(payload) {
  elements.emptyResult.hidden = true;
  elements.result.hidden = false;
  elements.requestId.textContent = payload.request_id || "";
  elements.answer.textContent = payload.answer || "";
  renderCitations(payload.citations || []);
  elements.pipelineTrace.textContent = JSON.stringify(payload.pipeline_trace || [], null, 2);
  elements.diagnosticsOutput.textContent = payload.diagnostics
    ? JSON.stringify(payload.diagnostics, null, 2)
    : "Diagnostics were not requested or are not permitted for this user.";
  elements.rawResponse.textContent = JSON.stringify(payload, null, 2);
}

async function runQuery(event) {
  event.preventDefault();
  elements.requestStatus.textContent = "Running query…";
  elements.submit.disabled = true;
  try {
    const accessToken = await currentAccessToken();
    const headers = {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
      "X-Request-ID": crypto.randomUUID(),
    };
    if (elements.diagnostics.checked) {
      headers["X-SovereignFlow-Diagnostics"] = "true";
    }
    const response = await fetch(configuration.api_url, {
      method: "POST",
      headers,
      body: JSON.stringify({
        capability_id: elements.domain.value,
        query: elements.query.value,
        session_id: crypto.randomUUID(),
        filters: {},
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const message = payload.error && payload.error.message;
      throw new Error(message || `Request failed with HTTP ${response.status}.`);
    }
    renderResult(payload);
    elements.requestStatus.textContent = "Query completed.";
  } catch (error) {
    elements.requestStatus.textContent = error.message;
  } finally {
    elements.submit.disabled =
      !tokens || !tokens.access_token || capabilities.length === 0;
  }
}

async function initialize() {
  try {
    const response = await fetch("/app/config.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Cannot load the web client configuration.");
    }
    configuration = await response.json();
    tokens = readTokens();
    await completeLogin();
    await renderSession();
  } catch (error) {
    clearSession();
    elements.sessionStatus.textContent = error.message;
    setQueryEnabled(false);
  }
}

elements.signIn.addEventListener("click", startLogin);
elements.signOut.addEventListener("click", signOut);
elements.form.addEventListener("submit", runQuery);

initialize();
