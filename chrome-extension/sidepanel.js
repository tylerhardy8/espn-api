// FFA side panel: the whole analyzer docked beside ESPN's draft room.
// Tabs: Draft (live board, auction on-block card, Claude advice), Trades,
// Waivers; header switches between the app's connected leagues. Works as a
// plain page too (no chrome.*), which is how it's tested against a running app.

const DEFAULT_APP = "http://localhost:5050";
const POLL_MS = 10000;
const BLOCK_POLL_MS = 3000;
const AI_THROTTLE_MS = 90000;

let appUrl = DEFAULT_APP;
let myTeam = "";
let activeLeague = "";
let aiOk = false;
let tab = "draft";
let lastPickCount = -1;
let lastAiCall = 0;
let posFilter = "";
let board = [];
let isAuction = false;
let teams = [];
const marked = [];
let tradesLoaded = false;
let waiversLoaded = false;

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// ---------------------------------------------------------------- settings
async function loadSettings() {
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.sync) {
      const s = await chrome.storage.sync.get("appUrl");
      if (s.appUrl) appUrl = s.appUrl.replace(/\/$/, "");
    }
  } catch (e) { /* plain page */ }
  $("app-url").value = appUrl;
}

async function saveSettings() {
  appUrl = ($("app-url").value.trim() || DEFAULT_APP).replace(/\/$/, "");
  try {
    if (typeof chrome !== "undefined" && chrome.storage?.sync) {
      await chrome.storage.sync.set({ appUrl });
    }
  } catch (e) { /* plain page */ }
  resetLeagueState();
  await refresh();
}

async function api(path, options) {
  const resp = await fetch(appUrl + path, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || `${resp.status}`);
  return data;
}

function postJson(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------- identity
function resetLeagueState() {
  myTeam = "";
  lastPickCount = -1;
  board = [];
  teams = [];
  tradesLoaded = false;
  waiversLoaded = false;
  $("advice").textContent = "Press Advise now — or wait for the next pick with auto on.";
  $("trade-partners").innerHTML = "";
  $("trade-advice-card").hidden = true;
  $("waiver-advice-card").hidden = true;
  $("block-card").hidden = true;
  $("auction-intel").hidden = true;
  $("auction-row").hidden = true;
  $("slot-row").hidden = true;
}

async function ensureIdentity() {
  if (myTeam) return;
  const me = await api("/api/me");
  myTeam = me.team_name || "";
  activeLeague = me.league || "";
  aiOk = !!me.ai_available;
  $("team-label").textContent = myTeam || "no team set — see app Setup";
  $("team-label").title = `${myTeam} · ${activeLeague}`;
  for (const id of ["advise", "trades-ai", "waivers-ai"]) $(id).disabled = !aiOk;
  if (!aiOk) {
    $("advice").textContent = "Add your Anthropic API key on the app's Setup page to enable advice.";
  }
  await loadLeagues();
}

async function loadLeagues() {
  try {
    const d = await api("/api/leagues");
    const sel = $("league-select");
    sel.innerHTML = (d.leagues || []).map((l) =>
      `<option value="${esc(l.name)}" ${l.name === d.active ? "selected" : ""}>${esc(l.name)}</option>`
    ).join("");
    sel.hidden = (d.leagues || []).length < 2;
  } catch (e) { /* older server */ }
}

async function switchLeague(name) {
  if (!name || name === activeLeague) return;
  $("team-label").textContent = "switching…";
  try {
    await postJson("/api/league/switch", { name });
  } catch (e) {
    $("team-label").textContent = `switch failed: ${e.message}`;
    return;
  }
  resetLeagueState();
  await refresh();
  if (tab === "trades") loadTrades();
  if (tab === "waivers") loadWaivers();
}

// ---------------------------------------------------------------- tabs
function showTab(name) {
  tab = name;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  for (const t of ["draft", "trades", "waivers"]) $("tab-" + t).hidden = t !== name;
  if (name === "trades" && !tradesLoaded) loadTrades();
  if (name === "waivers" && !waiversLoaded) loadWaivers();
}

// ---------------------------------------------------------------- draft
function flags(e) {
  const out = [];
  const inj = (e.injury_status || "").toUpperCase();
  if (inj && inj !== "ACTIVE" && inj !== "NORMAL") {
    const bad = /OUT|IR|PUP|SUSP/.test(inj);
    out.push(`<span class="flag ${bad ? "out" : "inj"}">${esc(inj)}</span>`);
  }
  if (e.practice) out.push(`<span class="flag inj">${esc(e.practice)}</span>`);
  if (e.depth_chart) out.push(`<span class="flag">${esc(e.depth_chart)}</span>`);
  if (e.fp_ecr) out.push(`<span class="flag">ECR#${e.fp_ecr}</span>`);
  if (e.trending_adds) out.push(`<span class="flag trend">🔥${e.trending_adds}</span>`);
  if (e.availability != null && e.availability < 1) out.push(`<span class="flag out">avail ${Math.round(e.availability * 100)}%</span>`);
  if (e.bye) out.push(`<span class="flag">bye ${e.bye}</span>`);
  return out.join("");
}

function renderBoard() {
  const rows = board
    .filter((e) => !posFilter || e.position === posFilter)
    .slice(0, 18)
    .map((e) => `<tr>
      <td>
        <button class="x" data-id="${e.player_id}" data-name="${esc(e.name)}" title="Mark drafted">×</button>
        ${isAuction ? `<input class="price" type="number" min="1" placeholder="$" title="Sale price">` : ""}
        <span class="name">${esc(e.name)}</span><span class="pos">${esc(e.position)}</span>
        ${flags(e)}
      </td>
      <td class="val">${isAuction
        ? `$${Math.round(e.adjusted_value)}${e.market_price ? `<span class="mkt" title="Likely price in this league">$${Math.round(e.market_price)}</span>` : ""}`
        : "T" + e.tier + " · " + Math.round(e.projected_points)}</td>
    </tr>`);
  $("board").innerHTML = rows.join("") || `<tr><td class="meta">Nothing to show.</td></tr>`;
}

function renderTeams(budgets) {
  const names = (budgets || []).map((b) => b.team).sort();
  if (names.join("|") === teams.join("|")) return;
  teams = names;
  const sel = $("mark-team");
  const current = sel.value;
  sel.innerHTML = `<option value="">unknown team</option>` +
    names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  sel.value = names.includes(current) ? current : (names.includes(myTeam) ? myTeam : "");
}

function render(d) {
  const s = d.summary;
  $("picks").textContent = s.total_picks;
  isAuction = !!d.is_auction;
  if (d.mock !== undefined) {
    $("mock-badge").hidden = !d.mock;
    $("mock-mode").checked = !!d.mock;
  }

  if (d.my_slot) {
    $("slot-row").hidden = false;
    $("my-picks").textContent = d.my_slot.upcoming.map((p) => "#" + p).join(", ") || "done";
  }
  if (isAuction && d.budgets) {
    const mine = d.budgets.find((b) => b.team.toLowerCase() === myTeam.toLowerCase());
    if (mine) {
      $("auction-row").hidden = false;
      $("remaining").textContent = "$" + mine.remaining;
      $("max-bid").textContent = "$" + mine.max_bid;
      $("inflation").textContent = (d.inflation ?? 1).toFixed(2) + "×";
      $("board-legend").hidden = !d.market_inflation;
    }
    $("block-card").hidden = false;
    $("auction-intel").hidden = false;
    $("cash-rich").innerHTML = (d.cash_rich || []).map((b) =>
      `<div>${esc(b.team)}<span class="val">$${b.remaining} · max $${b.max_bid}</span></div>`
    ).join("") || `<div class="meta">–</div>`;
    $("nominate").innerHTML = (d.nominate_next || []).map((n) =>
      `<div>${esc(n.name)}<span class="pos">${esc(n.position)}</span><span class="val">$${Math.round(n.adjusted_value)}</span>
       <span class="why">${esc(n.why)}</span></div>`
    ).join("") || `<div class="meta">–</div>`;
  }
  renderTeams(d.budgets);

  const alert = $("run-alert");
  if (d.active_run) {
    alert.hidden = false;
    alert.textContent = `Run: ${d.active_run.count} of the last 5 picks were ${d.active_run.position}s`;
  } else {
    alert.hidden = true;
  }

  board = d.best_available || [];
  renderBoard();

  const src = d.sources || {};
  const parts = ["ESPN"];
  if (src.sleeper) parts.push("Sleeper");
  if (src.fantasypros) parts.push("FantasyPros");
  $("sources").textContent = parts.join(" · ") + (d.synthetic ? " · tracked" : "");

  const mine = (d.team_picks || {})[myTeam] || [];
  const spent = mine.reduce((a, p) => a + (p.price || 0), 0);
  $("roster-count").textContent = mine.length ? `${mine.length} players${isAuction ? " · $" + spent : ""}` : "";
  $("roster").innerHTML = mine.length
    ? mine.map((p) => `<div>${esc(p.player)}<span class="pos">${esc(p.position || "?")}</span>${isAuction ? `<span class="who"> $${p.price || 0}</span>` : ""}</div>`).join("")
    : `<div class="meta">No picks yet.</div>`;

  const recent = [...(d.recent || [])].reverse().slice(0, 8);
  $("recent").innerHTML = recent.length
    ? recent.map((p) => {
        const mineCls = myTeam && p.team_name.toLowerCase() === myTeam.toLowerCase() ? "mine" : "";
        return `<div><span class="${mineCls}">${esc(p.player_name)}</span><span class="pos">${esc(p.position || "?")}</span>
          <span class="who"> — ${esc(p.team_name)}${isAuction ? " $" + p.bid_amount : ""}</span></div>`;
      }).join("")
    : `<div class="meta">Waiting for picks…</div>`;

  // Auto-advice when new picks land (throttled); auto calls skip web search
  if (lastPickCount >= 0 && s.total_picks > lastPickCount &&
      $("auto-ai").checked && myTeam && aiOk &&
      Date.now() - lastAiCall > AI_THROTTLE_MS) {
    getAdvice(true);
  }
  lastPickCount = s.total_picks;
}

async function refresh() {
  try {
    await ensureIdentity();
    const d = await api("/api/draft-state?team=" + encodeURIComponent(myTeam));
    if (d.error) throw new Error(d.error);
    $("conn").className = "dot on";
    render(d);
  } catch (e) {
    $("conn").className = "dot off";
    $("team-label").textContent = `app unreachable at ${appUrl}`;
  }
}

function clockHtml(b) {
  if (b.clock_ms == null) return "";
  const secs = Math.max(0, Math.round(b.clock_ms / 1000));
  return `<span class="clock ${secs <= 5 ? "low" : ""}">${secs}s</span>`;
}

function renderBlock(b) {
  $("mock-badge").hidden = !(b && b.mock);
  if (b && b.mock !== undefined) $("mock-mode").checked = !!b.mock;
  if (!b || b.player_id == null) {
    const who = b && b.nominating
      ? `${b.nominating_is_me ? `<span class="me">YOU are nominating</span>` : `${esc(b.nominating)} is nominating`} ${clockHtml(b)}`
      : "Waiting for a nomination… (or type one below)";
    $("block-body").innerHTML = `<div class="meta">${who}</div>`;
    return;
  }
  if (!b.name) {
    $("block-body").innerHTML = `<div class="meta">Player #${b.player_id} on the block (not in pool) · high $${b.high_bid} ${clockHtml(b)}</div>`;
    return;
  }
  const verdict = b.verdict === "bid"
    ? `<span class="verdict bid">BID · up to $${b.suggested_max_bid}</span>`
    : b.verdict === "stretch"
      ? `<span class="verdict stretch">STRETCH · up to $${b.stretch_cap}</span>`
      : `<span class="verdict pass">PASS · worth $${b.suggested_max_bid}</span>`;
  $("block-body").innerHTML = `
    <div class="big">${esc(b.name)}<span class="pos">${esc(b.position)}</span>
      ${b.need ? "" : `<span class="flag">position filled</span>`}${b.already_drafted ? `<span class="flag out">already tracked</span>` : ""}</div>
    <div class="bids">
      <span>High bid <strong>$${b.high_bid}</strong>${b.high_bidder ? ` <span class="who ${b.high_bidder_is_me ? "me" : ""}">— ${b.high_bidder_is_me ? "YOU" : esc(b.high_bidder)}</span>` : ""} ${clockHtml(b)}</span>
      ${verdict}
    </div>
    ${b.reason ? `<div class="reason">${esc(b.reason)}</div>` : ""}
    <div class="meta">model $${b.adjusted_value}${b.market_price ? ` · market $${b.market_price}` : ""} · crowd $${b.espn_value ?? "–"} · my max $${b.my_max_bid}${b.scarcity != null ? ` · ${b.scarcity} comparable left` : ""}</div>
    ${historyHtml(b)}`;
}

function historyHtml(b) {
  if (!b.intel_ready) return `<div class="meta">league history loading…</div>`;
  const lines = [];
  if (b.sale_history && b.sale_history.length) {
    const hist = b.sale_history.map((h) =>
      `${h.year}: <strong>$${h.bid}</strong> <span class="who">(${esc(h.manager || h.team)}${h.keeper ? ", keeper" : ""})</span>`
    ).join(" · ");
    lines.push(`<div class="hist">Sold here ${hist}</div>`);
  } else if (b.intel_ready) {
    lines.push(`<div class="hist">Not drafted in this league's recent auctions</div>`);
  }
  if (b.league_price) {
    const rank = b.pos_rank ? `${esc(b.position)}${b.pos_rank}` : esc(b.position);
    const hot = b.league_price > b.suggested_max_bid;
    lines.push(`<div class="hist ${hot ? "hot" : ""}">This league pays ~<strong>$${b.league_price}</strong> for the ${rank}${hot ? " — above the model" : ""}</div>`);
  }
  if (b.rival && b.rival.tags && b.rival.tags.length) {
    lines.push(`<div class="hist ${b.rival.runs_hot ? "hot" : ""}">⚠ ${esc(b.rival.manager)} ${esc(b.rival.tags.join(", "))}${b.expected_price ? ` — expect $${b.expected_price}+` : ""}</div>`);
  } else if (b.expected_price && b.expected_price > b.high_bid) {
    lines.push(`<div class="hist">Likely to run to ~$${b.expected_price}</div>`);
  }
  return lines.join("");
}

async function pollBlock() {
  if (tab !== "draft" || !myTeam) return;
  if (!isAuction && $("block-card").hidden) return;
  try {
    const b = await api("/api/auction-live?team=" + encodeURIComponent(myTeam));
    renderBlock(b);
  } catch (e) { /* board poll shows connectivity */ }
}

async function setBlock() {
  const name = $("block-name").value.trim();
  const bid = parseInt($("block-bid").value, 10);
  if (!name && !bid) return;
  const body = {};
  if (name) body.name = name;
  if (bid > 0) body.high_bid = bid;
  try {
    renderBlock(await postJson("/api/auction-live?team=" + encodeURIComponent(myTeam), body));
    $("block-name").value = "";
  } catch (e) {
    $("block-body").innerHTML = `<div class="meta">${esc(e.message)}</div>`;
  }
}

async function getAdvice(isAuto = false) {
  if (!myTeam) return;
  const btn = $("advise");
  if (btn.disabled) return;
  lastAiCall = Date.now();
  btn.disabled = true;
  $("advice-meta").textContent = isAuto ? "Auto-advising…" : "Thinking…";
  try {
    const d = await postJson("/api/draft-recommendation", {
      team_name: myTeam,
      web_search: !isAuto && $("web-search").checked,
    });
    $("advice").textContent = d.recommendation;
    $("advice-meta").textContent =
      `${new Date().toLocaleTimeString()}${d.web_search ? " · checked live news" : ""}`;
  } catch (e) {
    $("advice").textContent = e.message || "Couldn't get advice — is the app running?";
    $("advice-meta").textContent = "";
  } finally {
    btn.disabled = false;
  }
}

async function markDrafted(playerId, name, undo = false, bid = null) {
  try {
    const body = { player_id: playerId, undo };
    if (bid > 0) body.bid_amount = bid;
    const teamName = $("mark-team").value;
    if (teamName && !undo) body.row = [name, teamName];  // server resolves the team by name
    if (body.row) {
      // Row path resolves both player and team; keep the id for validation
      delete body.player_id;
      if (bid > 0) body.row.push(`$${bid}`);
    }
    await postJson("/api/mark-drafted", body);
    if (!undo) marked.push({ playerId, name });
    await refresh();
  } catch (e) { /* refresh will show state */ }
}

// ---------------------------------------------------------------- trades
async function loadTrades() {
  if (!myTeam) return;
  tradesLoaded = true;
  $("trade-needs").innerHTML = `<span class="meta">Analyzing rosters…</span>`;
  $("trade-partners").innerHTML = "";
  try {
    const d = await api("/api/trades?team=" + encodeURIComponent(myTeam));
    $("trade-needs").innerHTML = (d.needs || []).map((n) =>
      `<span class="chip ${n.deficit > 0 ? "need" : "ok"}" title="${n.team_points} vs league ${n.league_avg}">${esc(n.position)} ${n.deficit > 0 ? "+" : ""}${Math.round(n.deficit)}</span>`
    ).join("") || `<span class="meta">No needs data.</span>`;
    const cards = (d.matches || []).map((m) => `
      <section class="card partner">
        <div class="card-head">
          <span>${esc(m.partner)}<span class="rec">${esc(m.record)} · fit ${m.fit_score}</span></span>
          <span class="meta">needs ${esc((m.their_needs || []).join(", ") || "–")}</span>
        </div>
        ${m.proposals.map((p) => `
          <div class="proposal">
            <span class="give">${esc(p.give_players.join(" + "))}</span> → <span class="get">${esc(p.receive_players.join(" + "))}</span>
            <div class="nums">me ${p.my_net >= 0 ? "+" : ""}${p.my_net} · them ${p.their_net >= 0 ? "+" : ""}${p.their_net} · market ${Math.round(p.market_ratio * 100)}%</div>
          </div>`).join("")}
      </section>`);
    $("trade-partners").innerHTML = cards.join("") ||
      `<section class="card"><div class="meta">No realistic trade partners found right now.</div></section>`;
  } catch (e) {
    $("trade-needs").innerHTML = `<span class="meta">${esc(e.message)}</span>`;
  }
}

async function tradesAi() {
  const btn = $("trades-ai");
  btn.disabled = true;
  $("trade-advice-card").hidden = false;
  $("trade-advice-meta").textContent = "Thinking (30–60s)…";
  $("trade-advice").textContent = "";
  try {
    const d = await postJson("/api/trades-ai", { team_name: myTeam });
    $("trade-advice").textContent = d.advice;
    $("trade-advice-meta").textContent = new Date().toLocaleTimeString();
  } catch (e) {
    $("trade-advice").textContent = e.message;
    $("trade-advice-meta").textContent = "";
  } finally {
    btn.disabled = !aiOk;
  }
}

// ---------------------------------------------------------------- waivers
function agentLine(a, extra = "") {
  return `<div class="item">${esc(a.name)}<span class="pos">${esc(a.position)}</span>
    <span class="sub"> ${esc(a.team)} · proj ${a.projected_points} · avg ${a.avg_points} · ${a.percent_owned}% owned${a.injury_status && a.injury_status !== "Active" ? " · " + esc(a.injury_status) : ""}</span>
    ${extra}</div>`;
}

function newsFor(news, name) {
  const items = (news || {})[name] || [];
  return items.slice(0, 1).map((n) => `<span class="news">📰 ${esc(n.title)}</span>`).join("");
}

async function loadWaivers() {
  if (!myTeam) return;
  waiversLoaded = true;
  $("waiver-meta").textContent = "Loading free agents…";
  try {
    const week = parseInt($("waiver-week").value, 10);
    const d = await api("/api/waivers?team=" + encodeURIComponent(myTeam) + (week ? "&week=" + week : ""));
    $("waiver-week").value = d.week;
    $("waiver-meta").textContent = `${d.recommendations.length} upgrades · ${d.top_agents.length} top agents`;
    $("waiver-recs").innerHTML = d.recommendations.map((r) =>
      agentLine(r, `<span class="sub">+${r.upgrade_per_week}/wk over ${esc(r.replaces)}</span>${newsFor(d.news, r.name)}`)
    ).join("") || `<div class="meta">No clear upgrades.</div>`;
    $("waiver-streamers").innerHTML = Object.entries(d.streamers || {}).map(([pos, lst]) =>
      `<div class="pos-head">${esc(pos)}</div>` + lst.map((a) => agentLine(a, `<span class="sub">score ${a.streamer_score}</span>`)).join("")
    ).join("") || `<div class="meta">–</div>`;
    $("waiver-top").innerHTML = d.top_agents.map((a) => agentLine(a, newsFor(d.news, a.name))).join("");
  } catch (e) {
    $("waiver-meta").textContent = e.message;
  }
}

async function waiversAi() {
  const btn = $("waivers-ai");
  btn.disabled = true;
  $("waiver-advice-card").hidden = false;
  $("waiver-advice-meta").textContent = "Thinking (30–60s)…";
  $("waiver-advice").textContent = "";
  try {
    const week = parseInt($("waiver-week").value, 10) || undefined;
    const d = await postJson("/api/waivers-ai", { team_name: myTeam, week });
    $("waiver-advice").textContent = d.advice;
    $("waiver-advice-meta").textContent = new Date().toLocaleTimeString();
  } catch (e) {
    $("waiver-advice").textContent = e.message;
    $("waiver-advice-meta").textContent = "";
  } finally {
    btn.disabled = !aiOk;
  }
}

// ---------------------------------------------------------------- wiring
document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  $("save-url").addEventListener("click", saveSettings);
  $("clear-marks").addEventListener("click", async () => {
    if (!confirm(`Clear every tracked pick for ${activeLeague || "this league"}?`)) return;
    try { await postJson("/api/marks/clear", {}); } catch (e) { /* ignore */ }
    lastPickCount = -1;
    await refresh();
  });
  $("league-select").addEventListener("change", (ev) => switchLeague(ev.target.value));
  $("mock-mode").addEventListener("change", async (ev) => {
    const enabled = ev.target.checked;
    // Mock marks live on their own board; switching just changes which board shows
    try { await postJson("/api/mock-mode", { enabled }); } catch (e) { /* ignore */ }
    $("mock-badge").hidden = !enabled;
    lastPickCount = -1;
    await refresh();
    pollBlock();
  });
  $("tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-tab]");
    if (b) showTab(b.dataset.tab);
  });
  $("advise").addEventListener("click", () => getAdvice(false));
  $("pos-filter").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-pos]");
    if (!b) return;
    posFilter = b.dataset.pos;
    document.querySelectorAll("#pos-filter button").forEach((x) => x.classList.toggle("on", x === b));
    renderBoard();
  });
  $("board").addEventListener("click", (ev) => {
    const b = ev.target.closest("button.x");
    if (!b) return;
    const priceInput = b.parentElement.querySelector("input.price");
    const bid = priceInput ? parseInt(priceInput.value, 10) : null;
    markDrafted(parseInt(b.dataset.id, 10), b.dataset.name, false, bid);
  });
  $("block-set").addEventListener("click", setBlock);
  $("block-name").addEventListener("keydown", (ev) => { if (ev.key === "Enter") setBlock(); });
  $("block-bid").addEventListener("keydown", (ev) => { if (ev.key === "Enter") setBlock(); });
  $("block-clear").addEventListener("click", async () => {
    try { renderBlock(await postJson("/api/auction-live", { clear: true })); } catch (e) { /* ignore */ }
  });
  $("trades-refresh").addEventListener("click", loadTrades);
  $("trades-ai").addEventListener("click", tradesAi);
  $("waivers-refresh").addEventListener("click", loadWaivers);
  $("waivers-ai").addEventListener("click", waiversAi);
  document.addEventListener("keydown", (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && ev.key === "z" && marked.length && tab === "draft") {
      const last = marked.pop();
      markDrafted(last.playerId, last.name, true);
    }
  });
  await refresh();
  setInterval(refresh, POLL_MS);
  setInterval(pollBlock, BLOCK_POLL_MS);
});
