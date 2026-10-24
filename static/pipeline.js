"use strict";
/* Pipeline monitor. All data comes from the Flask backend (/api/pipeline); no GitHub
   credentials ever reach the browser. Helpers $, el, api, fmtDur, setErr, renderSys live in index.html. */
let lastPipe = null;
const pFilters = { status: "", branch: "", size: 20 };
const STATE_LABEL = { success: "Success", failure: "Failed", in_progress: "In progress", queued: "Queued",
  cancelled: "Cancelled", skipped: "Skipped", other: "Other", unknown: "Unknown" };
const STATE_CLS = { success: "s-success", failure: "s-failure", in_progress: "s-progress", queued: "s-queued" };
const REPO_RE = /^[A-Za-z0-9][A-Za-z0-9-]*\/[A-Za-z0-9_.-]+$/;

function stOf(r) { return r.state || "unknown"; }
function when(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !isNaN(d) ? d : null;
}
function fmtWhen(iso) { const d = when(iso); return d ? d.toLocaleString() : "–"; }
function ago(iso) {
  const d = when(iso); if (!d) return "–";
  const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
}
function badge(state) { return el("span", "st " + (STATE_CLS[state] || "s-other"), STATE_LABEL[state] || state); }
function runLink(r, text) {
  if (typeof r.url !== "string" || !r.url.startsWith("https://github.com/")) return null;
  const a = el("a", null, text); a.href = r.url; a.target = "_blank"; a.rel = "noopener";
  a.setAttribute("aria-label", "Open workflow run on GitHub: " + (r.name || r.id)); return a;
}
function setText(id, v) { $(id).textContent = v; }
const dash = v => (v === null || v === undefined ? "–" : String(v));

async function loadPipeline(force) {
  const btn = $("pRefresh"); btn.disabled = true; btn.textContent = "Refreshing…";
  try {
    lastPipe = await api("/api/pipeline?per_page=" + pFilters.size + (force ? "&refresh=1" : ""));
  } catch (e) {
    lastPipe = { source: "unavailable", repo: "", runs: [], stats: null, config_warnings: [],
      error: { code: "backend", message: "Could not reach the dashboard backend: " + e.message } };
  }
  renderPipeline();
  btn.disabled = false; btn.textContent = "Refresh pipeline";
}

function renderPipeline() {
  const p = lastPipe, src = p.source, runs = p.runs || [], st = p.stats;
  const live = src === "live", demo = src === "demo", down = src === "unavailable";

  const pill = $("src");
  pill.textContent = live ? "Live · GitHub data" : demo ? "Demo data" : "Live data unavailable";
  pill.className = "pill " + (live ? "live" : demo ? "demo" : "down");
  setText("pstate", live ? "connected" : demo ? "demo mode" : "not connected");

  const link = $("repoLink");
  if (p.repo && REPO_RE.test(p.repo)) {
    link.textContent = p.repo; link.href = "https://github.com/" + p.repo; link.hidden = false; $("repoNone").hidden = true;
  } else {
    link.hidden = true; $("repoNone").hidden = false;
    setText("repoNone", p.repo ? "invalid GITHUB_REPO" : "not configured (set GITHUB_REPO)");
  }

  const banner = $("pbanner");
  banner.hidden = live && !(p.config_warnings || []).length;
  banner.className = "pbanner " + (down ? "bad" : demo ? "warn" : "info");
  const parts = [];
  if (down) parts.push("Live GitHub data is unavailable: " + (p.error ? p.error.message : "unknown error") +
    " No sample data is shown and no success rate is calculated. Tasks and the Kanban board are unaffected.");
  if (demo) parts.push("Demo mode is enabled (DEMO_MODE=true). The runs below are SAMPLE data, not real builds.");
  (p.config_warnings || []).forEach(w => parts.push("Configuration: " + w));
  setText("pbannerText", parts.join(" "));
  $("pbannerRetry").hidden = !down;

  const hasStats = !down && st;
  setText("c-runs", hasStats ? st.total_runs : "–");
  setText("h-runs", hasStats ? (p.total_count != null ? "latest " + st.total_runs + " of " + p.total_count + " in repo" : "retrieved runs") : "no data");
  setText("c-rate", hasStats && st.success_rate !== null ? st.success_rate + "%" : "–");
  setText("h-rate", hasStats ? (st.eligible_runs ? "of " + st.eligible_runs + " completed success/failure runs" : "n/a – no completed runs yet") : "n/a – no data");
  setText("c-success", hasStats ? st.successful : "–");
  setText("c-failed", hasStats ? st.failed : "–");
  setText("c-inprog", hasStats ? st.in_progress : "–");
  setText("h-inprog", hasStats ? st.queued + " queued" : "");

  renderLatest(p, runs, st);
  renderBranchFilter(runs);
  renderRunsTable(p, runs);
  renderAnalytics(p, runs);

  const gh = $("ghLink");
  gh.hidden = !(live && p.actions_url && p.actions_url.startsWith("https://github.com/"));
  if (!gh.hidden) gh.href = p.actions_url;
  setText("runsNote", live ? "Fetched " + ago(p.fetched_at) + " from the GitHub Actions API. Filters affect this table only; the cards above cover all retrieved runs."
    : demo ? "Demo data – not real pipeline runs." : "");
  if (typeof renderSys === "function") renderSys(!$("hdot").classList.contains("bad"), null, tasksOk);
}

function renderLatest(p, runs, st) {
  const box = $("latest"); box.replaceChildren();
  if (p.source === "unavailable") { box.appendChild(el("div", "empty", "No data – GitHub Actions is not reachable.")); setText("latestNote", ""); return; }
  if (!runs.length) { box.appendChild(el("div", "empty", "No workflow runs yet. Push a commit or start the workflow manually on GitHub.")); setText("latestNote", ""); return; }
  const r = runs[0], s = stOf(r);
  setText("latestNote", p.source === "demo" ? "Demo data" : "Updated " + ago(r.created));
  const top = el("div", "lt");
  top.appendChild(el("strong", null, r.name || "(no title)")); top.appendChild(badge(s));
  box.appendChild(top);
  const bits = [r.workflow, r.branch && "branch " + r.branch, r.sha && "commit " + r.sha.slice(0, 7), r.event && "on " + r.event,
    r.run_number != null && "run #" + r.run_number, "started " + fmtWhen(r.started || r.created), r.duration != null && "took " + fmtDur(r.duration)].filter(Boolean);
  box.appendChild(el("div", "sub", bits.join(" · ")));
  if (r.commit_message) box.appendChild(el("div", "sub", "Commit: " + r.commit_message));
  const running = st ? st.in_progress + st.queued : 0;
  if (running) box.appendChild(el("div", "sub", running + " run(s) currently in progress or queued."));
  const a = runLink(r, "Open this run on GitHub ↗");
  if (a) box.appendChild(a);
}

function renderBranchFilter(runs) {
  const sel = $("fBranch"), branches = [...new Set(runs.map(r => r.branch).filter(Boolean))].sort();
  if (pFilters.branch && !branches.includes(pFilters.branch)) pFilters.branch = "";
  sel.replaceChildren(new Option("All branches", ""));
  branches.forEach(b => sel.appendChild(new Option(b, b)));
  sel.value = pFilters.branch;
}

function renderRunsTable(p, runs) {
  const tb = $("runs"); tb.replaceChildren();
  const msg = text => { const tr = el("tr"), td = el("td", "empty", text); td.colSpan = 7; tr.appendChild(td); tb.appendChild(tr); };
  if (p.source === "unavailable") return msg("Pipeline data is unavailable – see the warning above, then use Retry.");
  if (!runs.length) return msg("No workflow runs found for this repository yet.");
  const shown = runs.filter(r => (!pFilters.status || stOf(r) === pFilters.status) && (!pFilters.branch || r.branch === pFilters.branch));
  if (!shown.length) return msg("No runs match the current filters.");
  shown.forEach(r => {
    const tr = el("tr"), name = el("td", "name");
    name.appendChild(el("div", null, r.name || "(no title)"));
    const sub = [r.workflow, r.run_number != null && "#" + r.run_number].filter(Boolean).join(" ");
    if (sub) name.appendChild(el("div", "subtle", sub));
    tr.appendChild(name);
    tr.appendChild(el("td", null, r.branch || "–"));
    tr.appendChild(el("td", "mono", r.sha ? r.sha.slice(0, 7) : "–"));
    const s = stOf(r), stc = el("td"); stc.appendChild(badge(s));
    if (s === "other" && r.conclusion) stc.appendChild(el("span", "subtle", " " + r.conclusion.replace(/_/g, " ")));
    tr.appendChild(stc);
    tr.appendChild(el("td", null, fmtDur(r.duration)));
    const started = el("td", null, fmtWhen(r.created)); started.title = ago(r.created); tr.appendChild(started);
    const lk = el("td"), a = runLink(r, "View run ↗");
    lk.appendChild(a || el("span", null, p.source === "demo" ? "demo" : "–"));
    tr.appendChild(lk); tb.appendChild(tr);
  });
}

function renderAnalytics(p, runs) {
  const sb = $("sbar"), lg = $("legend"), d = $("dur");
  sb.replaceChildren(); lg.replaceChildren(); d.replaceChildren();
  setText("anNote", p.source === "live" ? "Live data" : p.source === "demo" ? "Demo data" : "No data");
  if (p.source === "unavailable" || !runs.length) {
    sb.setAttribute("aria-label", "No pipeline data");
    setText("durCap", p.source === "unavailable" ? "Pipeline data unavailable." : "No runs to chart yet."); return;
  }
  const n = { success: 0, failure: 0, other: 0 };
  runs.forEach(r => { const s = stOf(r); n[s === "success" ? "success" : s === "failure" ? "failure" : "other"]++; });
  [["success", "Success", "var(--ok)"], ["failure", "Failed", "var(--bad)"], ["other", "Other / running", "var(--muted)"]].forEach(([k, l, c]) => {
    if (n[k]) { const i = el("i"); i.style.width = (100 * n[k] / runs.length) + "%"; i.style.background = c; sb.appendChild(i); }
    const s = el("span"), sw = el("span", "sw"); sw.style.background = c; s.appendChild(sw);
    s.appendChild(document.createTextNode(l + " ")); s.appendChild(el("b", null, String(n[k]))); lg.appendChild(s);
  });
  sb.setAttribute("aria-label", "Success " + n.success + ", failed " + n.failure + ", other " + n.other);
  const timed = runs.filter(r => r.duration != null).slice().reverse();
  const max = Math.max(1, ...timed.map(r => r.duration));
  timed.forEach(r => {
    const s = stOf(r), b = el("div", s === "failure" ? "f" : s === "success" ? "" : "o");
    b.style.height = Math.max(6, 100 * r.duration / max) + "%"; b.title = (r.name || "run") + ": " + fmtDur(r.duration) + " (" + s + ")"; d.appendChild(b);
  });
  setText("durCap", timed.length ? "Average " + fmtDur(Math.round(timed.reduce((a, r) => a + r.duration, 0) / timed.length)) +
    " · longest " + fmtDur(max) + " · " + timed.length + " completed runs" : "No duration data available.");
}

function initPipelineControls() {
  $("fStatus").onchange = e => { pFilters.status = e.target.value; if (lastPipe) renderRunsTable(lastPipe, lastPipe.runs || []); };
  $("fBranch").onchange = e => { pFilters.branch = e.target.value; if (lastPipe) renderRunsTable(lastPipe, lastPipe.runs || []); };
  $("fSize").onchange = e => { pFilters.size = +e.target.value; loadPipeline(false); };
  $("pRefresh").onclick = () => loadPipeline(true);
  $("pbannerRetry").onclick = () => loadPipeline(true);
}
