document.addEventListener("DOMContentLoaded", () => {
    // State
    let allCases = [];
    let processedResults = [];
    let activeFilter = "ALL";
    let searchQuery = "";
    let eventSource = null;
    let glossaryData = {};

    // DOM Elements
    const uploadSection = document.getElementById("upload-section");
    const fileInput = document.getElementById("file-input");
    const btnLoadSample = document.getElementById("btn-load-sample");
    const fileInfoBanner = document.getElementById("file-info-banner");
    const activeFilename = document.getElementById("active-filename");
    const activeCaseCount = document.getElementById("active-case-count");
    const detectedColumns = document.getElementById("detected-columns");
    const btnStartScan = document.getElementById("btn-start-scan");
    const btnStopScan = document.getElementById("btn-stop-scan");
    const progressContainer = document.getElementById("progress-container");
    const progressBarFill = document.getElementById("progress-bar-fill");
    const progressPercent = document.getElementById("progress-percent");
    const progressCaseInfo = document.getElementById("progress-case-info");
    const tableBody = document.getElementById("results-table-body");
    const searchInput = document.getElementById("search-input");
    const filterPills = document.querySelectorAll(".filter-btn");
    const btnExportExcel = document.getElementById("btn-export-excel");
    const settingsPanel = document.getElementById("settings-panel");
    const btnToggleSettings = document.getElementById("btn-toggle-settings");
    const btnCloseSettings = document.getElementById("btn-close-settings");
    
    // API & Settings Elements
    const inputGeminiKey = document.getElementById("input-gemini-key");
    const btnTestGemini = document.getElementById("btn-test-gemini");
    const geminiStatusMsg = document.getElementById("gemini-status-msg");
    const inputApiToken = document.getElementById("input-api-token");
    const inputProxy = document.getElementById("input-proxy");
    const inputPmCookie = document.getElementById("input-pm-cookie");
    const chkDemoMode = document.getElementById("chk-demo-mode");
    const btnTestProxy = document.getElementById("btn-test-proxy");
    const proxyStatusMsg = document.getElementById("proxy-status-msg");

    // Glossary Modal Elements
    const btnOpenGlossary = document.getElementById("btn-open-glossary");
    const glossaryModal = document.getElementById("glossary-modal");
    const btnCloseGlossary = document.getElementById("btn-close-glossary");
    const btnDismissGlossary = document.getElementById("btn-dismiss-glossary");
    const glossaryContent = document.getElementById("glossary-content");
    const glossarySearch = document.getElementById("glossary-search");

    // KPI Elements
    const statTotal = document.getElementById("stat-total");
    const statProcessed = document.getElementById("stat-processed");
    const statSettled = document.getElementById("stat-settled");
    const statDismissed = document.getElementById("stat-dismissed");
    const statInvalidity = document.getElementById("stat-invalidity");
    const statGeminiVerified = document.getElementById("stat-gemini-verified");
    const statImported = document.getElementById("stat-imported");
    const importCaseId = document.getElementById("import-case-id");
    const importStatus = document.getElementById("docket-import-status");
    const importSourceUrl = document.getElementById("import-source-url");
    const btnImportDocket = document.getElementById("btn-import-docket");

    fetch('/static/js/capture-docket.js').then(response => response.text()).then(code => {
        const bookmark = document.getElementById('capture-docket-bookmark');
        bookmark.href = 'javascript:' + encodeURIComponent(code);
        bookmark.draggable = true;
        bookmark.addEventListener('click', event => {
            event.preventDefault();
            importStatus.textContent = 'Drag the capture link to your bookmarks bar. Use that bookmark on the PacerMonitor docket page.';
        });
    }).catch(() => { importStatus.textContent = 'Capture link unavailable. You can still save the loaded page as HTML in your browser.'; });

    importCaseId.addEventListener('change', () => {
        const selected = allCases.find(c => String(c.id) === importCaseId.value);
        importSourceUrl.value = selected?.pacermonitor_url || '';
    });

    document.getElementById('docket-import-form').addEventListener('submit', async event => {
        event.preventDefault();
        const file = document.getElementById('docket-html-file').files[0];
        if (!file || !importCaseId.value) {
            importStatus.textContent = 'Select a loaded case and a saved docket HTML file.';
            return;
        }
        const data = new FormData();
        data.append('case_id', importCaseId.value);
        data.append('source_url', importSourceUrl.value.trim());
        data.append('file', file);
        btnImportDocket.disabled = true;
        importStatus.textContent = 'Reading dated entries and checking the case number and parties...';
        try {
            const response = await fetch('/api/import-docket', {method: 'POST', body: data});
            const result = await response.json();
            if (!result.success) throw new Error(result.error || 'Docket import failed.');
            const selected = allCases.find(c => c.id === result.item.id);
            if (selected) selected.docket_import_id = result.docket_import_id;
            processedResults = processedResults.map(c => c.id === result.item.id ? result.item : c);
            chkDemoMode.checked = false;
            statImported.textContent = result.stats.imported || 0;
            statProcessed.textContent = result.stats.processed || 0;
            statGeminiVerified.textContent = result.stats.source_verified || 0;
            statSettled.textContent = result.stats.settled || 0;
            statDismissed.textContent = result.stats.dismissed || 0;
            statInvalidity.textContent = result.stats.invalidity_contentions || 0;
            importStatus.textContent = `Imported ${result.dated_entries} dated entries. Case identity matches the file; live source verification remains unavailable.`;
            renderTable();
        } catch (error) {
            importStatus.textContent = error.message;
        } finally {
            btnImportDocket.disabled = false;
        }
    });

    if (window.lucide) {
        window.lucide.createIcons();
    }

    // Load saved settings from localStorage
    if (localStorage.getItem("gemini_api_key")) {
        inputGeminiKey.value = localStorage.getItem("gemini_api_key");
    }
    if (localStorage.getItem("courtlistener_token")) {
        inputApiToken.value = localStorage.getItem("courtlistener_token");
    }
    if (localStorage.getItem("proxy_url")) {
        inputProxy.value = localStorage.getItem("proxy_url");
    }
    if (localStorage.getItem("pm_cookie")) {
        inputPmCookie.value = localStorage.getItem("pm_cookie");
    }

    // Save on change
    inputGeminiKey.addEventListener("change", () => {
        localStorage.setItem("gemini_api_key", inputGeminiKey.value.trim());
    });
    inputApiToken.addEventListener("change", () => {
        localStorage.setItem("courtlistener_token", inputApiToken.value.trim());
    });
    inputProxy.addEventListener("change", () => {
        localStorage.setItem("proxy_url", inputProxy.value.trim());
    });
    inputPmCookie.addEventListener("change", () => {
        localStorage.setItem("pm_cookie", inputPmCookie.value.trim());
    });

    // Toggle Settings Drawer
    btnToggleSettings.addEventListener("click", () => {
        settingsPanel.classList.toggle("hidden");
    });
    btnCloseSettings.addEventListener("click", () => {
        settingsPanel.classList.add("hidden");
    });

    // Test Gemini Connection
    btnTestGemini.addEventListener("click", async () => {
        const key = inputGeminiKey.value.trim();
        if (!key) {
            geminiStatusMsg.textContent = "Enter a Gemini key first.";
            geminiStatusMsg.className = "text-[11px] text-rose-500";
            return;
        }
        geminiStatusMsg.textContent = "Connecting to Gemini 2.5...";
        geminiStatusMsg.className = "text-[11px] text-purple-600 animate-pulse";

        try {
            const resp = await fetch("/api/test-gemini", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ api_key: key })
            });
            const data = await resp.json();
            if (data.success) {
                geminiStatusMsg.textContent = "âœ“ Gemini Connected!";
                geminiStatusMsg.className = "text-[11px] text-emerald-600 font-bold";
            } else {
                geminiStatusMsg.textContent = "Failed: " + data.error;
                geminiStatusMsg.className = "text-[11px] text-rose-500 font-medium";
            }
        } catch (e) {
            geminiStatusMsg.textContent = "Network error testing Gemini.";
            geminiStatusMsg.className = "text-[11px] text-rose-500";
        }
    });

    // Test Proxy
    btnTestProxy.addEventListener("click", async () => {
        const proxyVal = inputProxy.value.trim();
        if (!proxyVal) {
            proxyStatusMsg.textContent = "Please enter a proxy string first.";
            proxyStatusMsg.className = "text-[11px] text-rose-500";
            return;
        }
        proxyStatusMsg.textContent = "Testing proxy...";
        proxyStatusMsg.className = "text-[11px] text-indigo-500";

        try {
            const resp = await fetch("/api/test-proxy", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ proxy_string: proxyVal })
            });
            const data = await resp.json();
            if (data.success) {
                proxyStatusMsg.textContent = `Proxy verified! IP: ${data.ip}`;
                proxyStatusMsg.className = "text-[11px] text-emerald-600 font-semibold";
            } else {
                proxyStatusMsg.textContent = `Proxy failed: ${data.error}`;
                proxyStatusMsg.className = "text-[11px] text-rose-500";
            }
        } catch (e) {
            proxyStatusMsg.textContent = "Network error testing proxy.";
            proxyStatusMsg.className = "text-[11px] text-rose-500";
        }
    });

    // Load Glossary
    async function loadGlossary() {
        try {
            const resp = await fetch("/api/glossary");
            const data = await resp.json();
            if (data.success) {
                glossaryData = data.glossary;
                renderGlossary(glossaryData);
            }
        } catch (e) {
            console.error("Failed to load glossary", e);
        }
    }
    loadGlossary();

    function renderGlossary(grouped, filterText = "") {
        glossaryContent.innerHTML = "";
        const q = filterText.toLowerCase();

        for (const [phase, items] of Object.entries(grouped)) {
            const filteredItems = items.filter(item => 
                item.term.toLowerCase().includes(q) || item.definition.toLowerCase().includes(q)
            );

            if (filteredItems.length === 0) continue;

            const section = document.createElement("div");
            section.className = "space-y-3";

            const phaseBadgeColor = phase === "Patent Contentions" ? "bg-teal-100 text-teal-900 border-teal-300" : "bg-slate-100 text-slate-800 border-slate-200";

            section.innerHTML = `
                <div class="flex items-center space-x-2 border-b border-slate-200 pb-2">
                    <span class="px-2.5 py-0.5 rounded-full text-xs font-bold ${phaseBadgeColor} border">${phase}</span>
                    <span class="text-xs text-slate-400">(${filteredItems.length} terms)</span>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    ${filteredItems.map(item => `
                        <div class="p-3.5 rounded-xl border border-slate-200 bg-white hover:border-indigo-300 hover:shadow-sm transition space-y-1.5 ${item.term.includes('Invalidity') ? 'border-teal-300 bg-teal-50/20' : ''}">
                            <div class="flex items-center justify-between">
                                <span class="font-bold text-xs text-slate-900 flex items-center">
                                    ${item.term.includes('Invalidity') ? '<span class="mr-1 text-teal-600">ðŸŽ¯</span>' : ''}
                                    ${item.term}
                                </span>
                            </div>
                            <p class="text-[11px] text-slate-600 leading-relaxed">${item.definition}</p>
                        </div>
                    `).join("")}
                </div>
            `;
            glossaryContent.appendChild(section);
        }
    }

    glossarySearch.addEventListener("input", (e) => {
        renderGlossary(glossaryData, e.target.value.trim());
    });

    btnOpenGlossary.addEventListener("click", () => glossaryModal.classList.remove("hidden"));
    btnCloseGlossary.addEventListener("click", () => glossaryModal.classList.add("hidden"));
    btnDismissGlossary.addEventListener("click", () => glossaryModal.classList.add("hidden"));

    // Drag and Drop Upload
    uploadSection.addEventListener("click", () => fileInput.click());

    uploadSection.addEventListener("dragover", (e) => {
        e.preventDefault();
        uploadSection.classList.add("border-indigo-500", "bg-indigo-50/50");
    });

    uploadSection.addEventListener("dragleave", () => {
        uploadSection.classList.remove("border-indigo-500", "bg-indigo-50/50");
    });

    uploadSection.addEventListener("drop", (e) => {
        e.preventDefault();
        uploadSection.classList.remove("border-indigo-500", "bg-indigo-50/50");
        if (e.dataTransfer.files.length) {
            handleFileUpload(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files.length) {
            handleFileUpload(fileInput.files[0]);
        }
    });

    // Load Sample Cases
    btnLoadSample.addEventListener("click", async () => {
        try {
            btnLoadSample.disabled = true;
            btnLoadSample.innerHTML = `<i data-lucide="loader" class="w-3.5 h-3.5 mr-1.5 animate-spin"></i> Loading...`;
            if (window.lucide) window.lucide.createIcons();

            const resp = await fetch("/api/load-sample", { method: "POST" });
            const data = await resp.json();

            if (data.success) {
                displayLoadedCases(data);
            } else {
                alert("Error loading sample: " + data.error);
            }
        } catch (e) {
            alert("Network error loading sample cases");
        } finally {
            btnLoadSample.disabled = false;
            btnLoadSample.innerHTML = `<i data-lucide="sparkles" class="w-3.5 h-3.5 mr-1.5 text-indigo-400"></i> Load 100 Sample Cases`;
            if (window.lucide) window.lucide.createIcons();
        }
    });

    async function handleFileUpload(file) {
        const formData = new FormData();
        formData.append("file", file);

        try {
            const resp = await fetch("/api/upload", {
                method: "POST",
                body: formData
            });
            const data = await resp.json();
            if (data.success) {
                displayLoadedCases(data);
            } else {
                alert("Upload failed: " + data.error);
            }
        } catch (e) {
            alert("Error uploading file");
        }
    }

    function displayLoadedCases(data) {
        allCases = data.all_cases || [];
        importCaseId.replaceChildren(new Option('Select a case', ''));
        allCases.filter(c => c.case_number).forEach(c => {
            importCaseId.add(new Option(`${c.case_number}: ${c.plaintiff} v. ${c.defendants}`, String(c.id)));
        });
        importStatus.textContent = '';
        importSourceUrl.value = '';
        statImported.textContent = '0';
        processedResults = allCases.map(c => ({
            ...c,
            status_display: "Pending Scan",
            status_category: "PENDING",
            litigation_stage: "Not Scanned",
            has_invalidity_contentions: false,
            gemini_verified: false,
            client_summary: "",
            badge_color: "gray",
            date_terminated: "",
            matched_keywords: [],
            glossary_hits: {},
            trigger_entry: "",
            pacermonitor_url: `https://www.pacermonitor.com/search?querystring=${encodeURIComponent(c.case_number + ' ' + (c.court_code || c.jurisdiction_input || ''))}`,
            docket_url: "",
            search_url: `https://www.pacermonitor.com/search?querystring=${encodeURIComponent([c.case_number, c.plaintiff, c.defendants, c.court_code].filter(Boolean).join(' '))}&querytype=caseQuery`,
            source_used: "Not scanned"
        }));

        activeFilename.textContent = data.filename;
        activeCaseCount.textContent = `${data.total} Cases Loaded`;
        fileInfoBanner.classList.remove("hidden");

        detectedColumns.innerHTML = "";
        for (const [canonical, userCol] of Object.entries(data.column_mapping || {})) {
            const pill = document.createElement("span");
            pill.className = "px-2 py-0.5 rounded bg-slate-100 border border-slate-200 text-[10px] text-slate-700";
            pill.textContent = `${canonical}: ${userCol}`;
            detectedColumns.appendChild(pill);
        }

        statTotal.textContent = data.total;
        statProcessed.textContent = "0";
        statSettled.textContent = "0";
        statDismissed.textContent = "0";
        statInvalidity.textContent = "0";
        statGeminiVerified.textContent = "0";

        renderTable();
    }

    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    }

    function sourceLink(value) {
        try {
            const url = new URL(value);
            return url.protocol === "https:" && ["www.pacermonitor.com", "pacermonitor.com", "www.courtlistener.com"].includes(url.hostname)
                ? escapeHtml(url.href) : "#";
        } catch { return "#"; }
    }

    // Render Table
    function renderTable() {
        tableBody.innerHTML = "";

        const filtered = processedResults.filter(item => {
            if (activeFilter !== "ALL") {
                if (activeFilter === "INVALIDITY" && !item.has_invalidity_contentions) return false;
                if (activeFilter === "AI_VERIFIED" && !item.source_verified) return false;
                if (activeFilter === "SETTLED" && item.status_category !== "SETTLED") return false;
                if (activeFilter === "DISMISSED" && item.status_category !== "DISMISSED") return false;
                if (activeFilter === "ACTIVE" && item.status_category !== "ACTIVE") return false;
            }

            if (searchQuery) {
                const q = searchQuery.toLowerCase();
                const caseNum = (item.case_number || "").toLowerCase();
                const pl = (item.plaintiff || "").toLowerCase();
                const df = (item.defendants || "").toLowerCase();
                const court = (item.jurisdiction || "").toLowerCase();
                const pat = (item.patent_numbers || "").toLowerCase();
                const trig = (item.trigger_entry || "").toLowerCase();
                const summary = (item.client_summary || "").toLowerCase();
                const kw = (item.matched_keywords || []).join(" ").toLowerCase();
                return caseNum.includes(q) || pl.includes(q) || df.includes(q) || court.includes(q) || pat.includes(q) || trig.includes(q) || summary.includes(q) || kw.includes(q);
            }
            return true;
        });

        if (filtered.length === 0) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="10" class="py-12 text-center text-slate-400">
                        No cases match the current filter or search criteria.
                    </td>
                </tr>
            `;
            return;
        }

        filtered.forEach(rawItem => {
            const item = Object.fromEntries(Object.entries(rawItem).map(([key, value]) =>
                [key, typeof value === "string" ? escapeHtml(value) : value]));
            const verificationLabels = { SOURCE_VERIFIED: "Source matched", METADATA_ONLY: "Identity only",
                UNVERIFIED: "Unverified", DEMO: "DEMO - simulated", IMPORTED_MATCHED: "Imported - identity matched" };
            const verificationLabel = verificationLabels[rawItem.verification_status] || "Awaiting scan";
            const diagnosticHtml = (rawItem.source_diagnostics || []).map(d =>
                '<div>' + escapeHtml(d.source) + ': ' + escapeHtml(d.error || (d.dated_entries + ' dated entries')) + '</div>').join("");

            const tr = document.createElement("tr");
            tr.className = "case-row hover:bg-slate-50 border-b border-slate-100 transition";
            tr.id = `case-row-${item.id}`;

            let badgeClass = "badge-terminated";
            if (item.status_category === "SETTLED") badgeClass = "badge-settled";
            else if (item.status_category === "DISMISSED") badgeClass = "badge-dismissed";
            else if (item.status_category === "ACTIVE") badgeClass = "badge-active";
            else if (item.status_category === "MOTION_TO_DISMISS") badgeClass = "badge-mtd";

            // Keyword chips with tooltips
            const keywordsHtml = (item.matched_keywords || []).map(k => {
                const hit = Object.values(item.glossary_hits || {}).find(h => h.term === k);
                const def = hit ? escapeHtml(hit.definition) : '';
                return `<span class="px-1.5 py-0.5 rounded text-[10px] bg-indigo-50 text-indigo-700 border border-indigo-200 font-medium cursor-help" title="${def}">${escapeHtml(k)}</span>`;
            }).join(" ");

            const aiBadge = `<span class="block text-[10px] font-semibold mt-1" title="${item.verification_message || ''}">${verificationLabel} · ${item.source_used || 'No source'}</span>`;

            // Invalidity badge
            const invalidityBadge = item.has_invalidity_contentions ? `
                <span class="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-teal-100 text-teal-800 border border-teal-300" title="Patent Local Rule 3-3 disclosures served with prior art charts">
                    ðŸŽ¯ Served
                </span>
            ` : `<span class="text-slate-300 font-mono text-[11px]">-</span>`;

            // Client Executive Summary Card
            const clientSummaryHtml = item.client_summary ? `
                <div class="p-2.5 rounded-lg bg-slate-50 border border-slate-200 text-[11px] text-slate-800 leading-normal space-y-1">
                    <div class="flex items-center space-x-1.5 font-bold text-slate-900 text-[10px] uppercase tracking-wider">
                        <span class="text-indigo-600">ðŸ’¡</span>
                        <span>Client Call Summary</span>
                    </div>
                    <p class="text-slate-700 italic">"${item.client_summary}"</p>
                </div>
            ` : `<span class="text-slate-400 italic text-[11px]">No summary available</span>`;

            // Trigger Docket Entry
            const triggerSnippet = item.trigger_entry ? `
                <div class="group relative cursor-pointer" onclick="toggleSnippet(this)">
                    <p class="text-[10px] font-semibold text-slate-500">Docket date: ${item.trigger_date || "Unavailable"}</p>
                    <p class="line-clamp-2 text-slate-700 font-serif leading-relaxed text-[11px]">${item.trigger_entry}</p>
                    <span class="text-[10px] text-indigo-600 hover:underline">View full docket snippet &darr;</span>
                    <div class="hidden snippet-full p-2.5 mt-1.5 bg-slate-50 border border-slate-200 rounded-lg text-[11px] text-slate-800 leading-normal">
                        <span class="font-semibold text-slate-900 block mb-1">Entry #${item.trigger_entry_number || "—"} · ${item.trigger_date || "Date unavailable"} · ${item.trigger_source || item.source_used || ""}</span>
                        ${item.trigger_entry}
                    </div>
                </div>
            ` : `<span class="text-slate-400 italic">No triggering docket match</span>`;

            tr.innerHTML = `
                <td class="py-3 px-3 text-center text-slate-400 font-mono">${item.id}</td>
                <td class="py-3 px-3">
                    <span class="font-mono font-bold text-slate-900 block">${item.case_number}</span>
                    <span class="text-[10px] text-slate-400">${item.original_case_number || ""}</span>
                </td>
                <td class="py-3 px-3">
                    <span class="px-2 py-0.5 rounded font-mono font-semibold text-[10px] bg-slate-100 text-slate-800 border border-slate-200">${item.jurisdiction || "Fed Court"}</span>
                </td>
                <td class="py-3 px-4 max-w-[180px]">
                    <span class="font-semibold text-slate-800 block truncate" title="${item.plaintiff}">${item.plaintiff || "N/A"}</span>
                    <span class="text-slate-500 block truncate" title="${item.defendants}">v. ${item.defendants || "N/A"}</span>
                </td>
                <td class="py-3 px-3 max-w-[120px]">
                    <span class="font-semibold text-slate-700 block">${item.num_patents ? item.num_patents + ' Patents' : 'Patent Suit'}</span>
                    <span class="text-[10px] text-slate-500 truncate block" title="${item.patent_numbers}">${item.patent_numbers || ""}</span>
                </td>
                <td class="py-3 px-3">
                    <span class="px-2.5 py-1 rounded-full text-[11px] font-bold ${badgeClass} inline-block whitespace-nowrap">
                        ${item.status_display || "Pending"}
                    </span>
                    <span class="block text-[10px] text-slate-400 font-medium mt-0.5">${item.litigation_stage || ""}</span>
                    ${aiBadge}
                    ${keywordsHtml ? `<div class="mt-1 flex flex-wrap gap-1">${keywordsHtml}</div>` : ""}
                </td>
                <td class="py-3 px-3 text-center">
                    ${invalidityBadge}
                </td>
                <td class="py-3 px-4 max-w-[260px]">
                    ${clientSummaryHtml}
                </td>
                <td class="py-3 px-4 max-w-[240px]">
                    ${triggerSnippet}
                    <p class="text-[10px] text-slate-500 mt-2">Termination date (source): ${item.date_terminated || "Not available"}</p>
                </td>
                <td class="py-3 px-3 text-right whitespace-nowrap">
                    ${/^[0-9a-f]{32}$/.test(rawItem.docket_import_id || '') ? `<a href="/api/docket-imports/${rawItem.docket_import_id}/evidence" class="block text-indigo-700 underline text-[11px]">Download imported evidence</a>` : ''}
                    <button type="button" data-case-id="${item.id}" class="import-docket-for-case block text-indigo-700 underline text-[11px]">Import account docket</button>
                    <a href="${sourceLink(rawItem.trigger_source_url || rawItem.docket_url || rawItem.search_url || ('https://www.pacermonitor.com/search?querystring=' + encodeURIComponent(rawItem.case_number)))}" target="_blank" rel="noopener noreferrer" class="text-blue-700 underline text-[11px]">
                        ${item.docket_url ? 'Open ' + item.source_used : 'Search PacerMonitor'}
                    </a>
                    <details class="text-[10px] text-slate-500 mt-2 whitespace-normal max-w-[200px]">
                        <summary>Source details</summary>
                        <p>${item.verification_message || "Awaiting scan"}</p>
                        <p>Retrieved: ${item.retrieved_at || "Not retrieved"}</p>
                        ${item.imported ? `<p>Imported: ${item.imported_at} · ${item.import_filename}</p>` : ''}
                        <p>Source updated: ${item.source_last_updated || "Not provided"}</p>
                        <p>Latest visible entry: ${item.latest_entry_date || "Unavailable"}</p>
                        <p>${item.coverage || ""}</p>
                        <p>Date basis: ${item.trigger_date_basis || "Unavailable"}</p>
                        ${diagnosticHtml}
                    </details>
                </td>
            `;

            tableBody.appendChild(tr);
        });

        if (window.lucide) window.lucide.createIcons();
    }

    window.toggleSnippet = function(el) {
        const fullSnippet = el.querySelector(".snippet-full");
        if (fullSnippet) {
            fullSnippet.classList.toggle("hidden");
        }
    };

    tableBody.addEventListener('click', event => {
        const button = event.target.closest('.import-docket-for-case');
        if (!button) return;
        importCaseId.value = button.dataset.caseId;
        importCaseId.dispatchEvent(new Event('change'));
        document.getElementById('docket-import-panel').scrollIntoView({behavior: 'smooth'});
    });

    filterPills.forEach(btn => {
        btn.addEventListener("click", () => {
            filterPills.forEach(b => {
                b.classList.remove("bg-indigo-600", "text-white", "shadow-sm");
                b.classList.add("bg-slate-100", "text-slate-700");
            });
            btn.classList.add("bg-indigo-600", "text-white", "shadow-sm");
            btn.classList.remove("bg-slate-100", "text-slate-700");
            activeFilter = btn.getAttribute("data-filter");
            renderTable();
        });
    });

    searchInput.addEventListener("input", (e) => {
        searchQuery = e.target.value.trim();
        renderTable();
    });

    // Start Batch Scan
    btnStartScan.addEventListener("click", async () => {
        if (!allCases.length) {
            alert("Please load cases first.");
            return;
        }

        btnStartScan.classList.add("hidden");
        btnStopScan.classList.remove("hidden");
        progressContainer.classList.remove("hidden");

        const payload = {
            cases: allCases.map(c => {
                const selected = {...c};
                if (!document.getElementById('use-imported-dockets').checked) delete selected.docket_import_id;
                return selected;
            }),
            api_token: inputApiToken.value.trim(),
            gemini_api_key: inputGeminiKey.value.trim(),
            proxy_string: inputProxy.value.trim(),
            pm_cookie: inputPmCookie.value.trim(),
            demo_mode: chkDemoMode.checked,
            delay: chkDemoMode.checked ? 0.08 : 0.4
        };

        try {
            const startResp = await fetch("/api/start-scan", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const startData = await startResp.json();
            if (!startData.success) {
                alert("Failed to start scan: " + startData.error);
                btnStartScan.classList.remove("hidden");
                btnStopScan.classList.add("hidden");
                return;
            }

            if (eventSource) {
                eventSource.close();
            }

            eventSource = new EventSource("/api/progress-stream");

            eventSource.addEventListener("case_progress", (e) => {
                const ev = JSON.parse(e.data);
                const pct = Math.round((ev.index / ev.total) * 100);
                progressBarFill.style.width = `${pct}%`;
                progressPercent.textContent = `${pct}%`;
                progressCaseInfo.textContent = `Scanning Case ${ev.index} of ${ev.total}: [${ev.court}] ${ev.case_number}`;
            });

            eventSource.addEventListener("case_completed", (e) => {
                const ev = JSON.parse(e.data);
                const item = ev.item;
                const stats = ev.stats;

                const idx = processedResults.findIndex(c => c.id === item.id);
                if (idx !== -1) {
                    processedResults[idx] = item;
                }

                statProcessed.textContent = stats.processed;
                statSettled.textContent = stats.settled;
                statDismissed.textContent = stats.dismissed;
                statInvalidity.textContent = stats.invalidity_contentions || 0;
                statGeminiVerified.textContent = stats.source_verified || 0;
                statImported.textContent = stats.imported || 0;

                renderTable();
            });

            eventSource.addEventListener("batch_finished", (e) => {
                const ev = JSON.parse(e.data);
                eventSource.close();
                btnStartScan.classList.remove("hidden");
                btnStopScan.classList.add("hidden");
                progressBarFill.style.width = `100%`;
                progressPercent.textContent = `100%`;
                progressCaseInfo.textContent = `Multi-source scan complete! Processed ${ev.total_results} cases.`;
            });

            eventSource.addEventListener("batch_cancelled", (e) => {
                if (eventSource) eventSource.close();
                btnStartScan.classList.remove("hidden");
                btnStopScan.classList.add("hidden");
                progressCaseInfo.textContent = `Batch scan stopped by user.`;
            });

            eventSource.onerror = () => {
                if (eventSource) eventSource.close();
                btnStartScan.classList.remove("hidden");
                btnStopScan.classList.add("hidden");
            };

        } catch (e) {
            alert("Error communicating with scan server: " + e);
            btnStartScan.classList.remove("hidden");
            btnStopScan.classList.add("hidden");
        }
    });

    btnStopScan.addEventListener("click", async () => {
        try {
            await fetch("/api/stop-scan", { method: "POST" });
            if (eventSource) eventSource.close();
            btnStartScan.classList.remove("hidden");
            btnStopScan.classList.add("hidden");
        } catch (e) {
            console.error(e);
        }
    });

    btnExportExcel.addEventListener("click", () => {
        window.location.href = "/api/export/excel";
    });
});
