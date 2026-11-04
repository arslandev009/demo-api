(function () {
  "use strict";
  var API = "/a3/demo";
  var cfg = {};
  var $ = function (s) { return document.querySelector(s); };
  var turnNo = 0, busy = false, ready = false, sessionPromise = null, widgetId = null, waiting = null;

  var TOOL_LABEL = {
    check_availability: "Checked free times",
    book_appointment: "Booked an appointment",
    cancel_appointment: "Requested a cancellation",
    lookup_appointment: "Looked up an appointment",
    get_clinic_info: "Read clinic information"
  };
  var GUARD_LABEL = {
    emergency_bypass: "Emergency wording: the AI was skipped and a fixed safety message was sent",
    cancel_not_requested: "Blocked a cancellation the patient had not asked for",
    time_not_confirmed: "Blocked a booking time the patient had not chosen",
    availability_not_checked: "Blocked a booking made before checking free times",
    repeated_failed_call: "Stopped a repeated failing call",
    bad_tool_arguments: "Rejected malformed tool arguments",
    max_steps: "Stopped after too many steps",
    pending_note_added: "Added the 'pending staff approval' note",
    judge_unavailable: "Safety checker was unavailable, so simpler rules were used",
    session_message_limit: "This demo chat reached its message limit"
  };
  function guardLabel(g) {
    if (GUARD_LABEL[g]) return GUARD_LABEL[g];
    if (g.indexOf("repair:") === 0) return "Asked the assistant to correct its reply (" + g.slice(7).replace(/_/g, " ") + ")";
    if (g.indexOf("blocked:") === 0) return "Replaced a reply that claimed something that did not happen";
    if (g.indexOf("judge_overruled_regex:") === 0) return "Safety checker overruled a keyword rule (" + g.split(":")[1].replace(/_/g, " ") + ")";
    if (g.indexOf("judge_caught:") === 0) return "Safety checker caught a wrong claim (" + g.split(":")[1].replace(/_/g, " ") + ")";
    if (g.indexOf("llm_retry:") === 0) return "Retried the model after a temporary error";
    return g.replace(/_/g, " ");
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  // Minimal, XSS-safe formatting for model replies: **bold** and "- " / "* " list lines. Everything else is plain text.
  function richInto(node, text) {
    text.split("\n").forEach(function (line, i) {
      if (i) node.appendChild(document.createTextNode("\n"));
      var m = line.match(/^\s*[-*•]\s+(.*)$/);
      var body = m ? m[1] : line;
      if (m) node.appendChild(document.createTextNode("• "));
      body.split(/(\*\*[^*]+\*\*)/).forEach(function (part) {
        if (/^\*\*[^*]+\*\*$/.test(part)) { var b = document.createElement("strong"); b.textContent = part.slice(2, -2); node.appendChild(b); }
        else if (part) node.appendChild(document.createTextNode(part));
      });
    });
  }
  function addMsg(kind, who, text, rich) {
    var empty = $("#empty"); if (empty) empty.remove();
    var m = el("div", "msg " + kind);
    if (who) m.appendChild(el("div", "who", who));
    var p = el("p");
    if (rich) richInto(p, text); else p.textContent = text;
    m.appendChild(p);
    $("#log").appendChild(m);
    $("#log").scrollTop = $("#log").scrollHeight;
    return m;
  }
  function setStatus(text) { $("#status").textContent = text; }
  function updateControls() { $("#sendBtn").disabled = busy || !ready; }
  function setBusy(b) {
    busy = b; updateControls();
    if (b) setStatus("The assistant is working on your message.");
  }

  // ---------- network + session (Turnstile only when the server has it switched on)
  async function api(path, options) {
    var res = await fetch(path, Object.assign({ credentials: "same-origin" }, options || {}));
    var data = null;
    try { data = await res.json(); } catch (e) { /* an HTML error page from the host */ }
    if (!res.ok) {
      var err = new Error(data && typeof data.detail === "string" ? data.detail : "Request failed (" + res.status + ")");
      err.status = res.status;
      throw err;
    }
    return data;
  }
  function loadTurnstileScript() {
    if (document.querySelector("script[data-turnstile]")) return;
    var s = document.createElement("script");
    s.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
    s.async = true; s.defer = true; s.dataset.turnstile = "1";
    document.head.appendChild(s);
  }
  function turnstileToken() {
    return new Promise(function (resolve, reject) {
      var started = Date.now();
      function go() {
        var ts = window.turnstile;
        if (!ts || typeof ts.render !== "function") {
          if (Date.now() - started > 15000) return reject(new Error("The verification widget did not load. Reload the page."));
          return setTimeout(go, 100);
        }
        try {
          waiting = { resolve: resolve, reject: reject };
          if (widgetId === null) {
            widgetId = ts.render("#turnstile", {
              sitekey: cfg.turnstileSiteKey, appearance: "interaction-only",
              callback: function (token) { if (waiting) { waiting.resolve(token); waiting = null; } },
              "error-callback": function () { if (waiting) { waiting.reject(new Error("Verification error. Reload the page.")); waiting = null; } }
            });
          } else {
            ts.reset(widgetId);
          }
        } catch (e) {
          waiting = null;
          reject(new Error("The verification widget failed to start. Reload the page."));
        }
      }
      go();
    });
  }
  function ensureSession() {
    if (!sessionPromise) {
      sessionPromise = (async function () {
        var token = await turnstileToken();
        await api("/session", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token: token }) });
      })().finally(function () { sessionPromise = null; });
    }
    return sessionPromise;
  }
  async function withSession(call) { // one retry after a fresh verification when the 30-minute session has expired
    try { return await call(); }
    catch (err) {
      if (err.status === 401 && cfg.turnstileSiteKey) { await ensureSession(); return call(); }
      throw err;
    }
  }
  function post(path, body) {
    return api(path, { method: "POST", headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
  }

  // ---------- behind the scenes
  function addTrace(debug) {
    $("#traceHint").hidden = true;
    turnNo += 1;
    var box = el("div", "turn");
    box.appendChild(el("h3", null, "Reply " + turnNo));
    var ul = el("ul");
    var any = false;
    (debug.tool_calls || []).forEach(function (c) {
      any = true;
      var li = el("li");
      li.appendChild(el("span", "tag " + (c.ok ? "ok" : "fail"), c.ok ? "done" : "refused"));
      li.appendChild(el("span", null, (TOOL_LABEL[c.name] || c.name) + (c.ok ? "" : " (the system said no)")));
      ul.appendChild(li);
    });
    (debug.guards || []).forEach(function (g) {
      any = true;
      var li = el("li");
      li.appendChild(el("span", "tag guard", "safety"));
      li.appendChild(el("span", null, guardLabel(g)));
      ul.appendChild(li);
    });
    if (!any) ul.appendChild(el("li", "none", "Answered directly, no tools needed."));
    box.appendChild(ul);
    var t = $("#trace");
    t.insertBefore(box, t.firstChild);
  }

  // ---------- chat
  async function send(text) {
    text = text.trim();
    if (!text || busy || !ready) return;
    addMsg("user", "You", text);
    $("#input").value = ""; autosize();
    setBusy(true);
    var working = addMsg("bot working", "Assistant", "");
    var dots = document.createElement("span"); dots.className = "dots";
    dots.appendChild(document.createElement("span")); dots.appendChild(document.createElement("span")); dots.appendChild(document.createElement("span"));
    working.querySelector("p").appendChild(dots);
    var left = null;
    try {
      var data = await withSession(function () { return post(API + "/chat", { message: text }); });
      working.remove();
      addMsg("bot", "Assistant", data.reply, true);
      if (typeof data.turns_left === "number") left = data.turns_left;
      if (data.debug) {
        addTrace(data.debug);
        var cancelled = (data.debug.tool_calls || []).some(function (c) { return c.name === "cancel_appointment" && c.ok; });
        if (cancelled) loadQueue(true);
      }
    } catch (e) {
      working.remove();
      var msg = e.status === 429 ? "Too many messages in a minute. Wait a moment, then send again."
        : e.status === 422 ? "That message could not be sent. Keep it between 1 and 1000 characters."
        : e.status === 503 ? e.message
        : e.status === 401 ? "Your verification expired. Reload the page."
        : e.status ? "The server hit a problem. Send the message again."
        : "Cannot reach the server. Check your connection and send the message again.";
      addMsg("note", null, msg);
    } finally {
      setBusy(false);
      setStatus(left === null ? "Press Enter to send, Shift+Enter for a new line." : left + " messages left in this chat. Enter sends, Shift+Enter adds a line.");
      $("#input").focus();
    }
  }

  // ---------- front desk (this visitor's own session)
  function setDeskMsg(t, err) { var m = $("#deskMsg"); m.textContent = t; m.className = "msgline" + (err ? " err" : ""); }
  function fmtWhen(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString("en-GB", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  }
  function setBadge(n) { var b = $("#badge"); b.textContent = n; b.hidden = !n; }
  async function loadQueue(quiet) {
    try {
      var data = await withSession(function () { return api(API + "/approvals"); });
      var items = data.pending || [];
      var box = $("#tickets"); box.textContent = "";
      setBadge(items.length);
      setDeskMsg(items.length ? items.length + (items.length === 1 ? " request is" : " requests are") + " waiting for a decision." : "No cancellation requests waiting.", false);
      items.forEach(function (it) { box.appendChild(ticket(it)); });
    } catch (e) { if (!quiet) setDeskMsg(e.message || "Could not load requests.", true); }
  }
  function ticket(it) {
    var t = el("div", "ticket");
    var stub = el("div", "stub"); stub.appendChild(el("b", null, "#" + it.appointment_id)); stub.appendChild(el("span", null, "booking"));
    var body = el("div", "body");
    body.appendChild(el("strong", null, it.patient_name));
    body.appendChild(el("span", "when", String(it.service).replace(/_/g, " ") + ", " + fmtWhen(it.start)));
    var act = el("div", "actions");
    var yes = el("button", "btn", "Approve cancellation"); yes.type = "button";
    var no = el("button", "btn warn", "Keep appointment"); no.type = "button";
    yes.addEventListener("click", function () { decide(it.appointment_id, "approve", yes, no); });
    no.addEventListener("click", function () { decide(it.appointment_id, "reject", yes, no); });
    act.appendChild(yes); act.appendChild(no); body.appendChild(act);
    t.appendChild(stub); t.appendChild(body);
    return t;
  }
  async function decide(id, what, yes, no) {
    yes.disabled = no.disabled = true;
    try {
      await withSession(function () { return post(API + "/approvals/" + id + "/" + what); });
      await loadQueue(true);
      setDeskMsg(what === "approve" ? "Cancellation approved. The time slot is free again." : "Appointment kept. The patient's booking stays in place.", false);
    } catch (e) { setDeskMsg(e.message || "Could not save the decision.", true); }
    yes.disabled = no.disabled = false;
  }

  // ---------- wiring
  function autosize() { var t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 140) + "px"; }
  $("#form").addEventListener("submit", function (e) { e.preventDefault(); send($("#input").value); });
  $("#input").addEventListener("input", autosize);
  $("#input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#input").value); }
  });
  $("#log").addEventListener("click", function (e) { if (e.target.closest("#starters") && e.target.tagName === "BUTTON") send(e.target.textContent); });
  $("#refresh").addEventListener("click", function () { loadQueue(false); });
  var emptyHTML = $("#log").innerHTML;
  $("#reset").addEventListener("click", async function () {
    if (busy) return;
    try { await withSession(function () { return post(API + "/reset"); }); } catch (e) { setStatus(e.message || "Could not reset. Reload the page."); return; }
    turnNo = 0;
    $("#log").innerHTML = emptyHTML;
    $("#trace").textContent = "";
    $("#tickets").textContent = "";
    setBadge(0);
    setDeskMsg("No cancellation requests waiting. One appears here after you ask the assistant to cancel an appointment.", false);
    $("#traceHint").hidden = false;
    $("#traceHint").textContent = "Send a message to see what the assistant did.";
    setStatus("New chat started with a fresh calendar.");
  });
  var tabs = document.querySelectorAll(".tabs button");
  function showTab(name) {
    tabs.forEach(function (b) { b.setAttribute("aria-selected", String(b.dataset.tab === name)); });
    document.querySelectorAll("[data-panel]").forEach(function (p) { p.classList.toggle("active", p.dataset.panel === name); });
  }
  tabs.forEach(function (b) { b.addEventListener("click", function () { showTab(b.dataset.tab); }); });
  showTab("chat");
  autosize();

  async function init() {
    try { cfg = await (await fetch("config.json", { credentials: "same-origin" })).json(); } catch (e) { cfg = {}; }
    if (cfg.repoUrl) $("#link-repo").href = cfg.repoUrl;
    if (cfg.readmeUrl) $("#link-readme").href = cfg.readmeUrl;
    if (cfg.videoUrl) { $("#link-video").href = cfg.videoUrl; $("#link-video").classList.remove("hidden"); }
    if (cfg.turnstileSiteKey) {
      loadTurnstileScript();
      setStatus("Verifying you are human…");
      try { await ensureSession(); } catch (e) { setStatus(e.message); return; }
    }
    try {
      var info = await withSession(function () { return api(API + "/info"); });
      var list = $("#samples");
      (info.samples || []).forEach(function (p) {
        list.appendChild(el("li", null, p.name + ", phone " + p.phone + ", " + p.service));
      });
      if ((info.samples || []).length) $("#sampleNote").classList.remove("hidden");
    } catch (e) { setStatus(e.message); return; }
    ready = true; updateControls();
    setStatus("Press Enter to send, Shift+Enter for a new line.");
  }
  init();
})();
