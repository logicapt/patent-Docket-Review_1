"use strict";
const $ = id => document.getElementById(id);
const form = $("case-form");
let sources = [];
let controller = null;
let generation = 0;
let pastedHTML = "";
let pastedOriginalText = "";

function node(tag, text, cls) {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (cls) item.className = cls;
  return item;
}
function link(text, url, cls) {
  const item = node("a", text, cls);
  try { if (new URL(url).protocol === "https:") item.href = url; } catch (_) { /* no unsafe link */ }
  item.target = "_blank";
  item.rel = "noopener noreferrer";
  return item;
}
function identity() {
  return {case_number: $("case-number").value.trim(), plaintiff: $("plaintiff").value.trim(),
    defendants: $("defendants").value.trim(), court: $("court").value.trim(), proxy_string: $("proxy").value.trim()};
}
function selectedSources() {
  return [...$("sources").querySelectorAll("input:checked")].map(input => input.value);
}
function updateGoogle() {
  const data = identity();
  const terms = [data.case_number, data.plaintiff, data.defendants, data.court].filter(Boolean).map(value => '"' + value.replaceAll('"', " ") + '"');
  const selected = sources.filter(source => selectedSources().includes(source.id));
  if (selected.length) terms.push("(" + selected.map(source => "site:" + source.domain).join(" OR ") + ")");
  $("open-google").href = "https://www.google.com/search?" + new URLSearchParams({q: terms.join(" ")});
}
function busy(value) {
  document.querySelectorAll("#search, #review-direct, #review-paste, .candidate button").forEach(button => { button.disabled = value; });
}
function invalidate() {
  generation += 1;
  if (controller) controller.abort();
  controller = null;
  busy(false);
  $("review-section").hidden = true;
  for (const id of ["candidates", "source-searches", "search-status", "search-recovery", "review-content", "review-status"]) $(id).replaceChildren();
  updateGoogle();
}
function status(id, text, error = false) {
  $(id).textContent = text;
  $(id).classList.toggle("error", error);
}
async function post(url, data, signal) {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data), signal, cache: "no-store"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error + (result.details ? " " + result.details.map(item => item.message).join(" ") : ""));
  return result;
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (!selectedSources().length) { status("search-status", "Select at least one source.", true); return; }
  invalidate();
  const version = generation;
  const active = new AbortController(); controller = active;
  const caseData = identity();
  busy(true);
  status("search-status", "Searching Google in a temporary browser. This can take up to a minute…");
  try {
    const result = await post("/api/discover", {...caseData, sources: selectedSources()}, active.signal);
    if (version !== generation) return;
    status("search-status", result.message, !result.candidates.length);
    if (result.recovery_message) renderRecovery($("search-recovery"), result);
    $("open-google").href = result.google_url;
    for (const source of result.source_searches) $("source-searches").append(link(source.source + " ↗", source.url));
    for (const candidate of result.candidates) {
      const card = node("article", undefined, "candidate");
      card.append(node("span", candidate.source + " · Identity not checked", "source-name"), node("h3", candidate.title), link("Open source ↗", candidate.url));
      const button = node("button", "Review bot →", "secondary");
      button.type = "button";
      button.addEventListener("click", () => review(candidate.url));
      card.append(button); $("candidates").append(card);
    }
  } catch (error) {
    if (error.name !== "AbortError" && version === generation) status("search-status", error.message, true);
  } finally { if (version === generation) { controller = null; busy(false); } }
});

async function review(url, pasted = null) {
  if (!form.reportValidity()) return;
  if (!url) { status("search-status", "Paste a supported case link first.", true); return; }
  const mode = $("analysis-mode").value;
  const cloud = mode === "gemini";
  if (cloud && !$("gemini-key").value.trim()) {
    status("search-status", "Enter your Gemini API key above, then click Review bot or Analyze pasted docket.", true);
    $("gemini-key").focus();
    return;
  }
  const credentials = cloud ? {gemini_api_key: $("gemini-key").value.trim(), gemini_model: $("gemini-model").value.trim()} : {};
  if (cloud) $("gemini-key").value = "";
  generation += 1;
  if (controller) controller.abort();
  const version = generation;
  const active = new AbortController(); controller = active;
  busy(true);
  $("review-section").hidden = false;
  $("review-state").textContent = pasted ? "Reading pasted content" : "Reading source";
  $("review-content").replaceChildren();
  status("review-status", (pasted ? "Checking the case identity and docket rows in your pasted content…" : "Opening the case page, checking the parties, and reading the visible docket…") + (cloud ? " Gemini will then generate the case status and litigation recommendation report." : mode === "local_ai" ? " Local AI can take up to two additional minutes." : ""));
  $("review-section").scrollIntoView({behavior: "smooth", block: "start"});
  try {
    const result = await post(pasted ? "/api/analyze-paste" : "/api/analyze", {...identity(), url, entry_limit: Number($("entry-limit").value), analysis_mode: mode, ...credentials, ...(pasted || {})}, active.signal);
    if (version !== generation) return;
    $("review-state").textContent = result.status.replaceAll("_", " ");
    if (!result.success) {
      status("review-status", result.message, true);
      if (result.identity_match) {
        const observed = result.identity_match.observed;
        $("review-content").append(node("p", "Observed case: " + (observed.case_name || "Not shown") + " · " + (observed.case_number || "No case number"), "hint"));
      }
      if (result.source_url) $("review-content").append(link("Open this source ↗", result.source_url));
      if (result.recovery_message) renderRecovery($("review-content"), result);
      return;
    }
    if (result.case.evidence_origin === "user_paste") {
      $("review-state").textContent = "User-supplied · Identity matched";
      status("review-status", "The case identity matches your pasted content. Source authenticity and freshness have not been independently verified." + (!result.visible_entry_count ? " No supported dated rows were found. Copy the case header and docket table with normal Ctrl+C/Ctrl+V to preserve formatting; plain text needs tab-separated columns." : ""));
    } else {
      status("review-status", result.status === "metadata_only" ? "The case identity matches, but no supported dated docket rows were readable. The page may be restricted or use an unsupported layout." : "Case number and party roles match. Review is limited to the entries visible on this page.");
    }
    renderReview(result);
  } catch (error) {
    if (error.name !== "AbortError" && version === generation) { status("review-status", error.message, true); $("review-state").textContent = "Review unavailable"; }
  } finally { delete credentials.gemini_api_key; if (version === generation) { controller = null; busy(false); } }
}

function renderReview(result) {
  const item = result.case, analysis = result.analysis, root = $("review-content");
  root.append(node("h3", item.case_name, "case-heading"), link(item.source + " ↗", item.source_url));
  const metadata = node("dl", undefined, "metadata");
  const fields = [["Case number", item.case_number], ["Court", item.court], ["Reported citation", item.citation],
    [item.source_update_label || "Source last updated", item.source_last_updated], [item.evidence_origin === "user_paste" ? "Provided at (UTC)" : "Retrieved at (UTC)", item.provided_at || item.retrieved_at],
    ["Latest visible docket date", item.docket_entries[0]?.date], ["Termination date (source)", item.date_terminated],
    ["Dated entries visible / reviewed", result.visible_entry_count + " / " + analysis.entries_reviewed]];
  for (const [label, value] of fields) { const group = node("div"); group.append(node("dt", label), node("dd", value || "Not provided")); metadata.append(group); }
  root.append(metadata, node("p", item.coverage, "hint"));
  if (analysis.litigation_report) renderLitigationReport(root, analysis.litigation_report);
  const recommendation = node("section", undefined, "recommendation");
  recommendation.append(node("span", analysis.engine + " · Human review required", "engine"), node("h3", analysis.title), node("p", analysis.reason));
  const documents = node("div", undefined, "documents");
  for (const document of analysis.documents) {
    const card = node("article", undefined, "document"), outline = node("ol");
    for (const step of document.outline) outline.append(node("li", step));
    card.append(node("h4", document.title), outline); documents.append(card);
  }
  recommendation.append(documents);
  if (analysis.litigation_report?.status === "complete") {
    const comparison = node("details", undefined, "rule-comparison");
    comparison.append(node("summary", "Local rules comparison — Gemini report and status screening appear above"), recommendation);
    root.append(comparison);
  } else root.append(recommendation);
  if (analysis.ai_review) renderAIReview(root, analysis.ai_review);
  root.append(node("h3", "Latest visible entries (" + item.docket_entries.length + ")"));
  const scroll = node("div", undefined, "table-scroll"), table = node("table"), header = node("tr"), thead = node("thead"), tbody = node("tbody");
  for (const title of ["Docket no.", "Filed", "Entered", "Docket date", "Description"]) { const th = node("th", title); th.scope = "col"; header.append(th); }
  thead.append(header); table.append(thead);
  for (const entry of item.docket_entries) {
    const row = node("tr"), number = node("td"); number.append(link("#" + entry.entry_number, entry.source_url)); row.append(number);
    for (const key of ["date_filed", "date_entered", "docket_date"]) row.append(node("td", entry[key] || "—", "date"));
    const description = node("td", entry.description, "docket-description"); description.append(node("p", entry.date_basis, "hint")); row.append(description); tbody.append(row);
  }
  table.append(tbody); scroll.append(table); root.append(scroll);
  if (analysis.evidence.length) {
    root.append(node("h3", "Evidence behind the recommendation"));
    const list = node("ul", undefined, "evidence");
    for (const entry of analysis.evidence) { const li = node("li"); li.append(link("Dkt. " + entry.entry_number + " · " + entry.date, entry.source_url), node("span", " — " + entry.signal), node("blockquote", entry.description)); list.append(li); }
    root.append(list);
  }
  const limits = node("ul", undefined, "limits");
  for (const text of analysis.limitations) limits.append(node("li", text)); root.append(limits);
}

function renderRecovery(root, result) {
  root.append(node("p", result.recovery_message, "hint"));
  if (result.open_url) root.append(link(result.failed_stage === "google_search" ? "Open Google in my browser ↗" : "Open case in my browser ↗", result.open_url, "button secondary"));
  const button = node("button", "Paste page content", "secondary");
  button.type = "button";
  button.addEventListener("click", () => {
    if (result.source_url) $("paste-url").value = result.source_url;
    $("paste-panel").open = true;
    $("paste-panel").scrollIntoView({behavior: "smooth", block: "start"});
    $("paste-content").focus({preventScroll: true});
  });
  root.append(button);
}

function clearPaste() {
  pastedHTML = "";
  pastedOriginalText = "";
  $("paste-content").value = "";
}

$("paste-content").addEventListener("paste", event => {
  const plain = event.clipboardData?.getData("text/plain") || "";
  const html = event.clipboardData?.getData("text/html") || "";
  event.preventDefault();
  if (plain.length > 500000 || html.length > 1500000 || new TextEncoder().encode(JSON.stringify({pasted_text: plain, pasted_html: html})).length > 2000000) {
    clearPaste();
    $("paste-status").textContent = "The copied content is too large. Copy the case header and a smaller set of docket rows.";
    return;
  }
  pastedHTML = html;
  pastedOriginalText = plain;
  $("paste-content").value = plain;
  $("paste-status").textContent = html ? "Copied page formatting is available. Content will be sent only when you click Analyze pasted docket." : "Plain text pasted. Docket rows need tab-separated labelled columns; copying directly from the webpage usually preserves formatting.";
});
$("paste-content").addEventListener("input", () => {
  pastedHTML = ""; pastedOriginalText = "";
  $("paste-status").textContent = "Text edited; analysis will use the displayed plain text. Docket rows need tab-separated labelled columns.";
});
$("paste-form").addEventListener("submit", event => {
  event.preventDefault();
  if (!form.reportValidity() || !$("paste-form").reportValidity()) return;
  if ($("analysis-mode").value === "gemini" && !$("gemini-key").value.trim()) {
    status("paste-status", "Enter your Gemini API key above before submitting the copied docket.", true);
    $("gemini-key").focus();
    return;
  }
  const payload = {pasted_text: $("paste-content").value, pasted_html: $("paste-content").value === pastedOriginalText ? pastedHTML : ""};
  const url = $("paste-url").value.trim();
  clearPaste();
  $("paste-status").textContent = "Copied input cleared after submission. Results will be shown below.";
  review(url, payload);
});

function renderLitigationReport(root, report) {
  const panel = node("section", undefined, "litigation-report");
  panel.append(node("span", "Google Gemini · Draft for human review", "engine"), node("h3", "CASE STATUS & LITIGATION RECOMMENDATION REPORT"), node("p", report.message));
  root.append(panel);
  if (report.status !== "complete") return;
  const references = (parent, refs = []) => {
    if (!refs.length) return;
    const details = node("details", undefined, "report-citations");
    details.append(node("summary", "Supporting evidence (" + refs.length + ")"));
    for (const ref of refs) {
      const text = ref.entry_number ? "Dkt. " + ref.entry_number + " · " + ref.date : "Case page metadata";
      details.append(link(text + " ↗", ref.source_url), node("blockquote", ref.quote));
    }
    parent.append(details);
  };
  const field = (list, label, fact) => {
    const group = node("div"), value = typeof fact === "string" ? fact : fact?.value;
    group.append(node("dt", label), node("dd", value || "Not established by the supplied docket"));
    references(group, fact?.citations); list.append(group);
  };
  panel.append(node("p", "Case Status: " + report.case_status, "report-status"), node("p", "Strategic Action: " + report.strategic_action, "report-action"));
  panel.append(node("h4", "CONCLUSIVE SUMMARY"));
  for (const item of report.conclusive_summary) { const part = node("div"); part.append(node("p", item.text)); references(part, item.citations); panel.append(part); }
  panel.append(node("h4", "RECOMMENDED ACTION PLAN"));
  const actions = node("ol");
  for (const item of report.recommended_action_plan) { const li = node("li", item.text); references(li, item.citations); actions.append(li); }
  panel.append(actions, node("h4", "PARTIES"));
  const parties = node("dl", undefined, "report-facts");
  field(parties, "Plaintiff", report.parties.plaintiffs.join("; "));
  field(parties, "Plaintiff counsel", report.parties.plaintiff_counsel);
  field(parties, "Plaintiff type", report.parties.plaintiff_type);
  field(parties, "Defendant", report.parties.defendants.join("; "));
  field(parties, "Defendant counsel", report.parties.defendant_counsel);
  panel.append(parties, node("h4", "CASE FACTS"));
  const facts = node("dl", undefined, "report-facts"), data = report.case_facts;
  for (const [key, label] of [["case_number", "Case number"], ["court", "Court"], ["presiding_judge", "Presiding judge"], ["patents_in_suit", "Patents in suit"], ["filing_date", "Filing date"], ["disposition_date", "Disposition date"], ["current_stage", "Current stage"], ["contentions_status", "Contentions status"]]) field(facts, label, data[key]);
  if (data.recent_docket_event) {
    const e = data.recent_docket_event;
    field(facts, "Recent docket event", {value: "Dkt. " + e.entry_number + " · " + e.date + " · " + e.description,
      citations: [{entry_number: e.entry_number, date: e.date, source_url: e.source_url, quote: e.description}]});
  }
  panel.append(facts, node("h4", "STATUS SCREENING & COVERAGE"), node("p", report.status_screening.scope),
    node("p", report.status_screening.entries_screened + " visible entries screened for status; " + report.entries_reviewed + " recent entries reviewed for service focus."),
    node("p", report.coverage), node("p", "Generated: " + report.generated_at + " · Model: " + report.model, "hint"));
  for (const event of report.status_screening.events) { const part = node("div", event.case_status); references(part, [{...event, quote: event.text}]); panel.append(part); }
}

function updateGeminiSettings() {
  const selected = $("analysis-mode").value === "gemini";
  $("gemini-settings").hidden = !selected;
  $("gemini-model").disabled = !selected;
  if (!selected) $("gemini-key").value = "";
}
$("analysis-mode").addEventListener("change", updateGeminiSettings);
form.addEventListener("reset", () => queueMicrotask(updateGeminiSettings));

function renderAIReview(root, review) {
  const panel = node("section", undefined, "recommendation");
  panel.append(node("span", "On-device AI · Human review required", "engine"), node("h3", "AI-assisted review"), node("p", review.message));
  if (review.status === "complete") {
    const labels = {non_infringement: "Non-infringement strategy review", invalidation: "Invalidation services review", both: "Review both service options", insufficient_evidence: "Obtain more evidence", review_status: "Review case status first"};
    panel.append(node("h4", labels[review.suggested_focus]), node("p", review.summary));
    if (review.differs_from_rules) panel.append(node("p", "AI and rule-based suggestions differ. Resolve the difference before choosing a document."));
    const evidence = node("ul", undefined, "evidence");
    for (const entry of review.observations) {
      const item = node("li");
      item.append(link("Dkt. " + entry.entry_number + " · " + entry.date, entry.source_url), node("blockquote", entry.quote), node("p", entry.interpretation));
      evidence.append(item);
    }
    panel.append(evidence, node("h4", "Questions to resolve before outreach"));
    const questions = node("ul", undefined, "limits");
    for (const question of review.review_questions) questions.append(node("li", question));
    panel.append(questions);
  }
  root.append(panel);
}

$("review-direct").addEventListener("click", () => review($("case-url").value.trim()));
$("clear").addEventListener("click", () => { form.reset(); $("case-url").value = ""; $("paste-url").value = ""; clearPaste(); invalidate(); status("search-status", "Displayed results and input fields cleared. Any in-flight browser or AI request will stop when it finishes or reaches its time limit."); });
form.addEventListener("input", invalidate);
$("case-url").addEventListener("input", () => { generation += 1; if (controller) controller.abort(); controller = null; busy(false); $("review-section").hidden = true; $("review-content").replaceChildren(); });
window.addEventListener("pagehide", () => { form.reset(); $("case-url").value = ""; $("paste-url").value = ""; clearPaste(); invalidate(); });
fetch("/api/sources", {cache: "no-store"}).then(response => response.json()).then(result => {
  sources = result.sources;
  for (const source of sources) { const label = node("label"), input = node("input"); input.type = "checkbox"; input.value = source.id; input.checked = true; input.defaultChecked = true; label.append(input, node("span", source.name)); $("sources").append(label); }
  updateGoogle();
}).catch(() => status("search-status", "Could not load source settings. Refresh the page after starting the server.", true));
fetch("/api/analysis-config", {cache: "no-store"}).then(response => response.json()).then(result => {
  $("local-ai-option").disabled = !result.local_ai.available;
  $("local-ai-option").textContent = result.local_ai.available ? "Local AI + rules" : "Local AI + rules (setup required)";
  $("local-ai-status").textContent = result.local_ai.message;
}).catch(() => { $("local-ai-status").textContent = "Local AI setup could not be checked. Local rules are available."; });

const searchCSEBtn = $("search-cse");
if (searchCSEBtn) {
  searchCSEBtn.addEventListener("click", () => {
    const data = identity();
    const terms = [data.case_number, data.plaintiff, data.defendants, data.court].filter(Boolean).map(v => '"' + v.replaceAll('"', ' ') + '"');
    const selected = sources.filter(s => selectedSources().includes(s.id));
    if (selected.length) terms.push("(" + selected.map(s => "site:" + s.domain).join(" OR ") + ")");
    const query = terms.join(" ");

    if (window.google && window.google.search && window.google.search.cse) {
      const element = google.search.cse.element.getElement("case_search");
      if (element) {
        element.execute(query);
        status("search-status", "Executed search in Google CSE below. Pick a case link to analyze.");
        $("cse-panel").scrollIntoView({behavior: "smooth", block: "nearest"});
        return;
      }
    }
    status("search-status", "Google CSE is ready below. Query: " + (query || "Enter case details above"));
    $("cse-panel").scrollIntoView({behavior: "smooth", block: "nearest"});
  });
}

const csePanel = $("cse-panel");
if (csePanel) {
  csePanel.addEventListener("click", (e) => {
    const anchor = e.target.closest("a");
    if (anchor && anchor.href && anchor.href.startsWith("http")) {
      const url = anchor.href;
      $("case-url").value = url;
      status("search-status", "Captured link from Google CSE: " + url + "\nClick 'Review bot →' to analyze with Camoufox + Crawl4AI.");
      $("case-url").scrollIntoView({behavior: "smooth", block: "nearest"});
    }
  });
}

