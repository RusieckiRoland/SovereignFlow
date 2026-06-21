const storageKeys = {
  verifier: "sovereignflow.pkce.verifier",
  state: "sovereignflow.oidc.state",
  tokens: "sovereignflow.oidc.tokens",
  activeConversation: "sovereignflow.activeConversation",
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
  newConversation: document.querySelector("#new-conversation"),
  deleteConversation: document.querySelector("#delete-conversation"),
  conversationList: document.querySelector("#conversation-list"),
  conversationTitle: document.querySelector("#conversation-title"),
  renameConversation: document.querySelector("#rename-conversation"),
  conversationStatus: document.querySelector("#conversation-status"),
  turns: document.querySelector("#turns"),
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
let conversations = [];
let activeConversationId = sessionStorage.getItem(storageKeys.activeConversation) || "";

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
  sessionStorage.removeItem(storageKeys.activeConversation);
  conversations = [];
  activeConversationId = "";
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
  setConversationControlsEnabled(enabled);
}

function setConversationControlsEnabled(enabled) {
  elements.newConversation.disabled = !enabled;
  elements.conversationList.disabled = !enabled || conversations.length === 0;
  elements.conversationTitle.disabled = !enabled || !activeConversationId;
  elements.renameConversation.disabled = !enabled || !activeConversationId;
  elements.deleteConversation.disabled = !enabled || !activeConversationId;
}

async function apiRequest(path, options = {}) {
  const accessToken = await currentAccessToken();
  const headers = {
    Authorization: `Bearer ${accessToken}`,
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    const message = payload.error && payload.error.message;
    throw new Error(message || `Request failed with HTTP ${response.status}.`);
  }
  return payload;
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
    claims.clearance_label ||
    (claims.classification_labels || []).join(", ") ||
    "Not provided";
  setQueryEnabled(false);
  await loadCatalog();
  await loadConversations();
  setQueryEnabled(capabilities.length > 0);
}

async function loadConversations() {
  const payload = await apiRequest("/v1/conversations?limit=50");
  conversations = payload.conversations || [];
  if (activeConversationId && !conversations.some(item => item.conversation_id === activeConversationId)) {
    activeConversationId = "";
    sessionStorage.removeItem(storageKeys.activeConversation);
  }
  if (!activeConversationId && conversations.length) {
    activeConversationId = conversations[0].conversation_id;
    sessionStorage.setItem(storageKeys.activeConversation, activeConversationId);
  }
  renderConversations();
  if (activeConversationId) {
    await loadTurns(activeConversationId);
  } else {
    renderTurns([]);
  }
}

function renderConversations() {
  elements.conversationList.replaceChildren(
    ...conversations.map(conversation => {
      const option = document.createElement("option");
      option.value = conversation.conversation_id;
      option.textContent = conversation.title;
      return option;
    }),
  );
  elements.conversationList.value = activeConversationId;
  const active = conversations.find(item => item.conversation_id === activeConversationId);
  elements.conversationTitle.value = active ? active.title : "";
  setConversationControlsEnabled(Boolean(tokens && tokens.access_token && capabilities.length));
}

async function loadTurns(conversationId) {
  const payload = await apiRequest(`/v1/conversations/${conversationId}/turns?limit=50`);
  renderTurns(payload.turns || []);
}

function renderTurns(turns) {
  elements.turns.replaceChildren();
  if (!turns.length) {
    const empty = document.createElement("p");
    empty.className = "empty-history";
    empty.textContent = activeConversationId ? "No turns in this conversation yet." : "No conversation selected.";
    elements.turns.append(empty);
    return;
  }
  for (const turn of turns) {
    const card = document.createElement("article");
    card.className = "turn";
    const meta = document.createElement("div");
    meta.className = "turn-meta";
    meta.textContent = `#${turn.sequence_number} · ${turn.status} · ${turn.turn_id}`;
    const question = document.createElement("div");
    question.className = "turn-question";
    question.textContent = `User: ${turn.question_text || turn.question || ""}`;
    const answer = document.createElement("div");
    answer.className = "turn-answer";
    answer.textContent = `Assistant: ${turn.answer_text || turn.answer || ""}`;
    card.append(meta, question, answer);
    elements.turns.append(card);
  }
}

async function createConversation() {
  const title = elements.query.value.trim().slice(0, 80) || "New conversation";
  const payload = await apiRequest("/v1/conversations", {
    method: "POST",
    body: JSON.stringify({
      session_id: crypto.randomUUID(),
      domain: selectedCapabilityDomain(),
      title,
    }),
  });
  activeConversationId = payload.conversation.conversation_id;
  sessionStorage.setItem(storageKeys.activeConversation, activeConversationId);
  await loadConversations();
  return activeConversationId;
}

async function renameConversation() {
  if (!activeConversationId) {
    return;
  }
  elements.conversationStatus.textContent = "Renaming conversation…";
  await apiRequest(`/v1/conversations/${activeConversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ title: elements.conversationTitle.value }),
  });
  await loadConversations();
  elements.conversationStatus.textContent = "Conversation renamed.";
}

async function deleteConversation() {
  if (!activeConversationId) {
    return;
  }
  elements.conversationStatus.textContent = "Deleting conversation…";
  await apiRequest(`/v1/conversations/${activeConversationId}`, { method: "DELETE" });
  activeConversationId = "";
  sessionStorage.removeItem(storageKeys.activeConversation);
  await loadConversations();
  elements.conversationStatus.textContent = "Conversation deleted.";
}

function selectedCapabilityDomain() {
  const selected = capabilities.find(capability => capability.capability_id === elements.domain.value);
  return selected ? selected.domain : "general";
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
    if (!activeConversationId) {
      await createConversation();
    }
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
        conversation_id: activeConversationId,
        filters: {},
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      const message = payload.error && payload.error.message;
      throw new Error(message || `Request failed with HTTP ${response.status}.`);
    }
    renderResult(payload);
    if (payload.conversation_id) {
      activeConversationId = payload.conversation_id;
      sessionStorage.setItem(storageKeys.activeConversation, activeConversationId);
      await loadConversations();
    }
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
elements.newConversation.addEventListener("click", async () => {
  try {
    elements.conversationStatus.textContent = "Creating conversation…";
    await createConversation();
    elements.conversationStatus.textContent = "Conversation created.";
  } catch (error) {
    elements.conversationStatus.textContent = error.message;
  }
});
elements.conversationList.addEventListener("change", async () => {
  activeConversationId = elements.conversationList.value;
  sessionStorage.setItem(storageKeys.activeConversation, activeConversationId);
  renderConversations();
  await loadTurns(activeConversationId);
});
elements.renameConversation.addEventListener("click", async () => {
  try {
    await renameConversation();
  } catch (error) {
    elements.conversationStatus.textContent = error.message;
  }
});
elements.deleteConversation.addEventListener("click", async () => {
  try {
    await deleteConversation();
  } catch (error) {
    elements.conversationStatus.textContent = error.message;
  }
});

initialize();
