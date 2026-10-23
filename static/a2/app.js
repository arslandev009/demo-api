(() => {
  "use strict";
  let cfg = {}; // loaded from config.json (Turnstile site key, links)
  const $ = (id) => document.getElementById(id);
  const statusEl = $("status"), samplesEl = $("samples"), runBtn = $("run"), resultEl = $("result");
  const previewEmpty = $("preview-empty"), previewFig = $("preview"), previewImg = $("preview-img"), previewPdf = $("preview-pdf");

  const FIELDS = [
    ["vendor_name", "Vendor"], ["invoice_number", "Invoice number"], ["po_number", "PO number"],
    ["invoice_date", "Invoice date"], ["due_date", "Due date"], ["currency", "Currency"],
    ["subtotal", "Subtotal"], ["discount", "Discount"], ["tax_rate", "Tax rate (%)"], ["tax_amount", "Tax amount"],
    ["other_charges", "Other charges"], ["total", "Total"], ["amount_due", "Amount due"],
  ];
  const ITEM_COLS = [
    ["description", "Description", false], ["quantity", "Qty", true], ["unit_price", "Unit price", true],
    ["amount", "Amount", true], ["discount_percent", "Disc. %", true],
  ];

  let samples = [], selected = null, ready = false, busy = false, sessionPromise = null, widgetId = null, waiting = null;

  // ---------- small helpers
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text; // never innerHTML: model output is untrusted text
    return node;
  }
  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = `pill pill-${kind}`;
  }
  function safePath(p) { // only same-origin sample files may become a link or an image source
    return typeof p === "string" && p.startsWith("/demo/a2/samples/") && !p.includes("..") ? p : "";
  }
  function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }
  function errorText(data, status) {
    if (data && typeof data.detail === "string") return data.detail;
    if (data && Array.isArray(data.detail)) return "The request was not valid.";
    return `Request failed (${status})`;
  }
  async function api(path, options = {}) {
    const res = await fetch(path, { credentials: "same-origin", ...options });
    let data = null;
    try { data = await res.json(); } catch { /* an HTML error page from the host */ }
    if (!res.ok) {
      const err = new Error(errorText(data, res.status));
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ---------- Turnstile + session (only when a site key is configured: otherwise no request goes to Cloudflare)
  function loadTurnstileScript() {
    if (document.querySelector("script[data-turnstile]")) return;
    const s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true; s.defer = true; s.dataset.turnstile = "1";
    document.head.appendChild(s);
  }
  function turnstileToken() {
    return new Promise((resolve, reject) => {
      const started = Date.now();
      const go = () => {
        const ts = window.turnstile;
        // The async script first creates window.turnstile without render(): wait until render() really is a function.
        if (!ts || typeof ts.render !== "function") {
          if (Date.now() - started > 15000) return reject(new Error("The verification widget did not load. Reload the page."));
          return setTimeout(go, 100);
        }
        try {
          waiting = { resolve, reject };
          if (widgetId === null) {
            widgetId = ts.render("#turnstile", {
              sitekey: cfg.turnstileSiteKey, appearance: "interaction-only",
              callback: (token) => { if (waiting) { waiting.resolve(token); waiting = null; } },
              "error-callback": () => { if (waiting) { waiting.reject(new Error("Verification error. Reload the page.")); waiting = null; } },
            });
          } else {
            ts.reset(widgetId);
          }
        } catch (err) {
          waiting = null;
          reject(new Error("The verification widget failed to start. Reload the page."));
        }
      };
      go();
    });
  }
  function ensureSession() {
    if (!sessionPromise) {
      sessionPromise = (async () => {
        const token = await turnstileToken();
        await api("/session", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token }) });
      })().finally(() => { sessionPromise = null; });
    }
    return sessionPromise;
  }
  async function withSession(call) { // one retry after a fresh verification when the 30-minute session has expired
    try {
      return await call();
    } catch (err) {
      if (err.status === 401 && cfg.turnstileSiteKey) { await ensureSession(); return call(); }
      throw err;
    }
  }

  // ---------- samples
  function updateControls() {
    runBtn.disabled = !ready || busy || !selected;
    samplesEl.querySelectorAll("button").forEach((b) => { b.disabled = !ready || busy; });
  }
  function selectSample(s) {
    selected = s;
    samplesEl.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.id === s.id ? "true" : "false"));
    const img = safePath(s.image), pdf = safePath(s.pdf);
    previewEmpty.classList.add("hidden");
    previewFig.classList.remove("hidden");
    previewImg.classList.remove("hidden");
    previewImg.alt = `Preview of the sample: ${s.title}`;
    previewImg.src = img;
    if (pdf) previewPdf.href = pdf;
    updateControls();
  }
  async function loadSamples() {
    samples = await withSession(() => api("/a2/demo/samples"));
    samplesEl.replaceChildren();
    if (!samples.length) {
      samplesEl.appendChild(el("p", "muted", "No samples are installed on this server."));
      return;
    }
    for (const s of samples) {
      const b = el("button", "sample");
      b.type = "button"; b.dataset.id = s.id; b.disabled = true;
      b.setAttribute("aria-pressed", "false");
      b.appendChild(el("strong", "", s.title));
      b.appendChild(el("span", "muted small", s.shows));
      if (s.expected_review) b.appendChild(el("span", "badge badge-warn", "expected: flagged for review"));
      b.addEventListener("click", () => selectSample(s));
      samplesEl.appendChild(b);
    }
  }

  // ---------- rendering
  function fmt(v) {
    if (v === null || v === undefined || v === "") return el("span", "missing", "not found");
    return document.createTextNode(String(v));
  }
  function fieldsTable(ext) {
    const wrap = el("div", "tbl-wrap"), table = el("table", "tbl tbl-fields"), body = el("tbody");
    for (const [key, label] of FIELDS) {
      const tr = el("tr");
      tr.appendChild(el("th", "", label));
      const td = el("td");
      td.appendChild(fmt(ext[key]));
      tr.appendChild(td);
      body.appendChild(tr);
    }
    table.appendChild(body); wrap.appendChild(table);
    return wrap;
  }
  function itemsTable(items) {
    const wrap = el("div", "tbl-wrap"), table = el("table", "tbl");
    const head = el("tr");
    for (const [, label, numeric] of ITEM_COLS) head.appendChild(el("th", numeric ? "num" : "", label));
    const thead = el("thead"); thead.appendChild(head); table.appendChild(thead);
    const body = el("tbody");
    for (const it of items) {
      const tr = el("tr");
      for (const [key, , numeric] of ITEM_COLS) {
        const td = el("td", numeric ? "num" : "");
        td.appendChild(fmt(it[key]));
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
    table.appendChild(body); wrap.appendChild(table);
    return wrap;
  }
  function issuesList(issues) {
    const ul = el("ul", "issues");
    for (const i of issues) {
      const li = el("li", "issue");
      li.appendChild(el("span", `pill ${i.severity === "error" ? "pill-err" : "pill-wait"}`, i.severity === "error" ? "error" : "warning"));
      li.appendChild(el("code", "", i.code));
      li.appendChild(el("span", "", i.field ? `${i.message} (${i.field})` : i.message));
      ul.appendChild(li);
    }
    return ul;
  }
  function showPending(s) {
    resultEl.classList.remove("hidden");
    resultEl.replaceChildren();
    const head = el("div", "panel-head");
    head.appendChild(el("h2", "", s.title));
    resultEl.appendChild(head);
    resultEl.appendChild(el("p", "muted", "Reading the PDF and asking the model. This takes a few seconds; the first request after a quiet period can take longer while the service wakes up."));
    if (resultEl.scrollIntoView) resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  function renderResult(data, sample) {
    const res = data.result || {}, ext = res.extraction;
    resultEl.classList.remove("hidden");
    resultEl.replaceChildren();

    const head = el("div", "panel-head");
    head.appendChild(el("h2", "", data.title || sample.title));
    head.appendChild(el("p", `badge ${res.needs_review ? "badge-warn" : "badge-ok"}`, res.needs_review ? "Needs human review" : "No issues found by the checks"));
    resultEl.appendChild(head);

    if (typeof sample.expected_review === "boolean" && sample.expected_review !== Boolean(res.needs_review)) {
      resultEl.appendChild(el("p", "note", "This differs from the outcome expected for this sample. Model output varies between runs, which is exactly why the checks exist."));
    }
    if (ext) {
      resultEl.appendChild(el("h3", "", "Extracted fields"));
      resultEl.appendChild(fieldsTable(ext));
      if (Array.isArray(ext.line_items) && ext.line_items.length) {
        resultEl.appendChild(el("h3", "", `Line items (${ext.line_items.length})`));
        resultEl.appendChild(itemsTable(ext.line_items));
      }
    } else {
      resultEl.appendChild(el("p", "", "No fields were extracted from this document."));
    }
    resultEl.appendChild(el("h3", "", "Checks"));
    if (Array.isArray(res.issues) && res.issues.length) resultEl.appendChild(issuesList(res.issues));
    else resultEl.appendChild(el("p", "muted", "No check reported a problem."));

    const parts = [plural(Number(res.pages || 0), "page"), `${Number(res.latency_ms || 0)} ms`];
    parts.push(data.llm_calls === 0 ? "no AI call" : plural(Number(data.llm_calls || 0), "AI call"));
    if (data.provider) parts.push(String(data.provider));
    if (data.cached) parts.push(`cached result, ${Number(data.age_s || 0)} s old (latency is from the original run)`);
    resultEl.appendChild(el("p", "meta", parts.join(" \u00b7 ")));

    const details = el("details");
    details.appendChild(el("summary", "", "Raw JSON"));
    details.appendChild(el("pre", "raw", JSON.stringify(res, null, 2)));
    resultEl.appendChild(details);
  }
  function renderError(text) {
    resultEl.classList.remove("hidden");
    resultEl.replaceChildren();
    resultEl.appendChild(el("p", "badge badge-err", "Something went wrong"));
    resultEl.appendChild(el("p", "", text));
  }

  // ---------- running
  async function run() {
    if (!selected || busy || !ready) return;
    const sample = selected;
    busy = true; updateControls();
    showPending(sample);
    try {
      const data = await withSession(() => api("/a2/demo/extract", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ sample: sample.id }),
      }));
      renderResult(data, sample);
    } catch (err) {
      renderError(err.message);
    } finally {
      busy = false; updateControls();
    }
  }

  // ---------- start up
  async function init() {
    try { cfg = await (await fetch("config.json", { credentials: "same-origin" })).json(); } catch { cfg = {}; }
    if (cfg.repoUrl) $("link-repo").href = cfg.repoUrl;
    if (cfg.readmeUrl) $("link-readme").href = cfg.readmeUrl;
    if (cfg.videoUrl) { $("link-video").href = cfg.videoUrl; $("link-video").classList.remove("hidden"); }
    runBtn.addEventListener("click", run);
    updateControls();

    if (cfg.turnstileSiteKey) {
      loadTurnstileScript();
      setStatus("Verifying you are human\u2026", "wait");
      try { await ensureSession(); } catch (err) { setStatus(err.message, "err"); return; }
    }
    try {
      await loadSamples();
    } catch (err) {
      setStatus(err.message, "err");
      return;
    }
    ready = true; updateControls();
    setStatus("Ready", "ok");
  }
  init();
})();
