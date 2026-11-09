/* Shelf Scout front end: capture -> upload -> ranked results. No framework. */
(() => {
  "use strict";

  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const money = (n) => (n == null || isNaN(n) ? "–" : `$${Number(n).toFixed(2)}`);
  const pct = (n) => `${Math.round((n || 0) * 100)}%`;
  const CH = { amazon_fba: "Amazon FBA", amazon_fbm: "Amazon (you ship)", ebay: "eBay" };
  const TIER = { pull: "Pull", check: "Look closer", skip: "Skip" };
  const FORMAT = {
    hardcover_dust_jacket: "hardcover with jacket", hardcover_no_jacket: "hardcover",
    trade_paperback: "paperback", mass_market_paperback: "mass-market paperback",
    library_binding: "library binding", leather_fine_binding: "fine binding", unknown: "",
  };
  const DEFAULTS = {
    cost_per_book: 1, min_profit: 5, min_roi: 1, sell_window_days: 90, condition: "good",
    channels: ["amazon_fba", "amazon_fbm", "ebay"],
  };

  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage full or blocked */ } },
  };

  let profile = { ...DEFAULTS, ...store.get("ss.profile", {}) };
  let password = store.get("ss.pw", "");
  let status = {};
  let last = null;           // last ScanResult
  let photoURL = "";
  let filter = "worth";

  // ---------- helpers ----------
  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid.nodeType ? kid : String(kid));
    return n;
  }

  function show(id) {
    for (const s of ["home", "busy", "results"]) $("#" + s).hidden = s !== id;
    $("#dock").hidden = id !== "results";
    window.scrollTo({ top: 0 });
  }

  function banner(msg, isErr = false) {
    const b = $("#banner");
    b.hidden = !msg;
    b.textContent = msg || "";
    b.classList.toggle("err", isErr);
  }

  async function api(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (password) headers["X-App-Password"] = password;
    const r = await fetch(path, { ...opts, headers });
    let body = null;
    try { body = await r.json(); } catch { /* non-JSON error page */ }
    if (r.status === 401) {
      $("#pw-field").hidden = false;
      openSettings();
      throw new Error("This server needs its app password. Enter it in Buying settings.");
    }
    if (!r.ok) throw new Error((body && body.detail) || `Server error ${r.status}`);
    return body;
  }

  // Shrink big photos before upload: faster on cellular, and the server tiles anyway.
  async function prepare(file, maxEdge = 4032) {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      const s = Math.min(1, maxEdge / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      bmp.close && bmp.close();
      return await new Promise((res) => c.toBlob((b) => res(b || file), "image/jpeg", 0.9));
    } catch {
      return file; // older browsers: send the original
    }
  }

  async function thumbnail(blob, edge = 1200) {
    try {
      const bmp = await createImageBitmap(blob);
      const s = Math.min(1, edge / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      return c.toDataURL("image/jpeg", 0.72);
    } catch { return ""; }
  }

  // ---------- scan ----------
  let timer = null;
  async function scanFile(file) {
    if (!file) return;
    banner("");
    const blob = await prepare(file);
    if (photoURL.startsWith("blob:")) URL.revokeObjectURL(photoURL);
    photoURL = URL.createObjectURL(blob);
    $("#busy-img").src = photoURL;
    show("busy");
    const t0 = Date.now();
    $("#busy-secs").textContent = "0";
    timer = setInterval(() => { $("#busy-secs").textContent = Math.round((Date.now() - t0) / 1000); }, 1000);

    const fd = new FormData();
    fd.append("image", blob, "shelf.jpg");
    fd.append("profile", JSON.stringify(profile));
    try {
      const res = await api("api/scan", { method: "POST", body: fd });
      last = res;
      if (location.hash) history.replaceState(null, "", location.pathname);
      render(res);
      // Mobile browsers often reload the tab after the camera app; keep the last scan.
      thumbnail(blob).then((t) => { store.set("ss.last", { res, thumb: t, at: Date.now() }); });
    } catch (e) {
      show("home");
      banner(e.message || "The scan failed. Check your connection and try again.", true);
    } finally {
      clearInterval(timer);
    }
  }

  // ---------- render ----------
  function render(res) {
    show("results");
    const srcBits = [];
    if (res.sources && res.sources.full_demo) srcBits.push("Demo mode: sample shelf with simulated prices.");
    banner(srcBits.join(" "));

    $("#shelf-img").src = photoURL;
    renderPins(res);

    const s = res.summary;
    const tally = $("#tally");
    tally.replaceChildren(
      el("span", { class: "t-pull" }, el("b", { class: "num", text: s.pull }), "to pull"),
      el("span", { class: "t-check" }, el("b", { class: "num", text: s.check }), "to look closer at"),
      el("span", {}, el("b", { class: "num", text: s.skip }), "to skip"),
      s.unreadable ? el("span", {}, el("b", { class: "num", text: s.unreadable }), "unreadable") : null,
    );
    renderList();

    const notes = [...(res.warnings || [])];
    if (res.image_notes) notes.unshift(res.image_notes);
    if (res.timings_ms && res.timings_ms.total_ms) notes.push(`Scan took ${(res.timings_ms.total_ms / 1000).toFixed(1)}s.`);
    $("#notes").hidden = !notes.length;
    $("#notes-list").replaceChildren(...notes.map((n) => el("li", { text: n })));
  }

  function rankLabel(res) {
    // number the books worth acting on; pins and list share these numbers
    const map = new Map(); let n = 0;
    for (const b of res.books) if (b.tier !== "skip") map.set(b.id, ++n);
    return map;
  }

  function renderPins(res) {
    const shelf = $("#shelf");
    $$(".pin", shelf).forEach((p) => p.remove());
    const ranks = rankLabel(res);
    for (const b of res.books) {
      if (!b.spine) continue;
      const r = ranks.get(b.id);
      const pin = el("button", {
        class: `pin ${b.tier}`, type: "button", "data-id": b.id,
        style: `left:${b.spine.x_center_pct}%;top:${b.spine.y_center_pct}%`,
        "aria-label": `${b.spine.title}: ${TIER[b.tier]}`,
        onclick: () => focusBook(b.id),
      }, r ? String(r) : "");
      shelf.append(pin);
    }
  }

  function focusBook(id) {
    if (filter === "worth" && last.books.find((b) => b.id === id)?.tier === "skip") {
      setFilter("all");
    }
    const row = $(`#book-${id}`);
    if (!row) return;
    $$(".pin").forEach((p) => p.classList.remove("active"));
    const det = $(".detail", row);
    if (det && det.hidden) $(".more", row).click();
    row.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  }

  function setFilter(f) {
    filter = f;
    $$("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === f)));
    renderList();
  }

  function renderList() {
    const list = $("#list");
    const ranks = rankLabel(last);
    const books = last.books.filter((b) => filter === "all" || b.tier !== "skip");
    if (!books.length) {
      list.replaceChildren(el("li", { class: "book skip" },
        el("div", {}, el("h3", { text: "Nothing worth pulling here" }),
          el("div", { class: "by", text: "Every readable book on this shelf sells for too little after fees. Try the next shelf, or tap Every book to see why." }))));
      return;
    }
    list.replaceChildren(...books.map((b) => bookRow(b, ranks.get(b.id))));
  }

  function titleOf(b) {
    if (b.spine) return b.spine.title;
    const c = b.candidates && b.candidates[0];
    return (c && c.title) || (b.market && b.market.title) || b.chosen_isbn;
  }

  function byline(b) {
    const sp = b.spine;
    const c = b.candidates && b.candidates[0];
    const author = (sp && sp.author) || (c && c.authors && c.authors.join(", "));
    const pub = (c && c.publisher) || (sp && sp.publisher_or_imprint);
    const fmt = ((c && c.physical_format) || (sp && FORMAT[sp.format_guess]) || "").toLowerCase();
    const ed = [pub, fmt].filter(Boolean).join(" ") + (c && c.year ? `, ${c.year}` : "");
    return [author ? `by ${author}` : "", ed.replace(/^, /, "")].filter(Boolean);
  }

  function sticker(b) {
    const best = b.best;
    if (b.tier === "skip") return el("div");
    if (!best || best.max_buy <= 0) {
      if (!b.spine) return el("div", { class: "sticker zero" }, el("small", { text: "Pay up to" }), el("strong", { class: "num", text: money(best ? best.max_buy : 0) }));
      return el("div", { class: "sticker outline" }, el("small", { text: "Pay up to" }), el("strong", { text: "ISBN?" }));
    }
    return el("div", { class: `sticker ${b.tier === "check" ? "outline" : ""}` },
      el("small", { text: "Pay up to" }), el("strong", { class: "num", text: money(best.max_buy) }));
  }

  function summaryLine(b) {
    return el("div", { class: "line" },
      el("span", { class: "verdict", text: TIER[b.tier] + ". " }),
      el("span", { class: b.tier === "skip" ? "muted" : "", text: b.headline || b.reasons[0] || "" }));
  }

  function channelTable(b) {
    const rows = b.channels.map((c) => el("tr", { class: b.best && c.channel === b.best.channel ? "best" : "" },
      el("td", { text: CH[c.channel] }),
      el("td", { class: "num", text: money(c.gross) }),
      el("td", { class: "num", text: money(Object.values(c.fees).reduce((a, x) => a + x, 0)) }),
      el("td", { class: "num", text: money(c.net_if_sold) }),
      el("td", { class: "num", text: pct(c.sell_prob) }),
      el("td", { class: "num", text: money(c.max_buy) }),
    ));
    return el("table", { class: "chan" },
      el("thead", {}, el("tr", {}, ...["Where", "Buyer pays", "Fees", "Net", "Sells", "Max pay"].map((h) => el("th", { scope: "col", text: h })))),
      el("tbody", {}, rows));
  }

  function feeLine(c) {
    const names = { referral: "referral", closing: "closing", fba_fulfillment: "FBA fulfillment", inbound_ship: "shipping to Amazon",
      prep: "prep", postage: "Media Mail", packaging: "mailer", final_value: "final value", order_fee: "order fee", promoted: "promoted" };
    return Object.entries(c.fees).map(([k, v]) => `${names[k] || k} ${money(v)}`).join(", ");
  }

  function detail(b) {
    const d = el("div", { class: "detail", hidden: true });
    for (const f of b.flags) d.append(el("p", { class: "flag", text: f }));
    if (b.reasons.length) d.append(el("ul", { class: "why" }, b.reasons.map((r) => el("li", { text: r }))));
    if (b.channels.length) {
      d.append(channelTable(b));
      if (b.best) d.append(el("p", { class: "basis", text: `${b.best.basis}. Fees: ${feeLine(b.best)}.` }));
    }
    if (b.candidates.length && b.spine) {
      d.append(el("ul", { class: "eds" }, b.candidates.slice(0, 3).map((c, i) => el("li", {},
        el("span", {},
          el("span", { text: `${i === 0 ? "Best match" : "Also possible"}: ${[c.publisher, c.year].filter(Boolean).join(" ") || c.title} ` }),
          el("span", { class: "isbn", text: c.isbn13 })),
        el("button", { class: "small-btn", type: "button", onclick: () => openIsbn(c.isbn13) }, "Check")))));
    }
    const L = { ...(b.market ? b.market.links : {}), ...b.search_links };
    const links = [["amazon", "Amazon"], ["ebay_sold", "eBay sold"], ["bookscouter", "BookScouter"], ["keepa", "Keepa"], ["abebooks", "AbeBooks"]]
      .filter(([k]) => L[k]).map(([k, label]) => el("a", { href: L[k], target: "_blank", rel: "noopener" }, label));
    if (b.spine) links.push(el("button", { class: "small-btn", type: "button", onclick: () => openIsbn("") }, "Enter the ISBN"));
    d.append(el("div", { class: "links" }, links));
    return d;
  }

  function bookRow(b, rank) {
    const det = detail(b);
    const more = el("button", { class: "more", type: "button", "aria-expanded": "false" }, "Details");
    more.addEventListener("click", () => {
      det.hidden = !det.hidden;
      more.setAttribute("aria-expanded", String(!det.hidden));
      more.textContent = det.hidden ? "Details" : "Hide details";
      $$(".pin").forEach((p) => p.classList.toggle("active", !det.hidden && p.dataset.id === String(b.id)));
    });
    return el("li", { class: `book ${b.tier}`, id: `book-${b.id}` },
      el("div", {},
        rank ? el("div", { class: "rank num", text: `#${rank}` }) : null,
        el("h3", { text: titleOf(b) }),
        byline(b).map((t) => el("div", { class: "by", text: t }))),
      sticker(b),
      summaryLine(b),
      more,
      det);
  }

  // ---------- ISBN ----------
  function openIsbn(isbn) {
    const dlg = $("#isbn");
    $("#isbn-input").value = isbn || "";
    $("#isbn-err").hidden = true;
    $("#isbn-out").replaceChildren();
    if (!dlg.open) dlg.showModal();
    if (isbn) $("#isbn-form").requestSubmit(); else $("#isbn-input").focus();
  }

  $("#isbn-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const raw = $("#isbn-input").value.trim();
    if (!raw) return;
    const go = $("#isbn-go");
    go.disabled = true; go.textContent = "Checking";
    $("#isbn-err").hidden = true;
    try {
      const res = await api("api/isbn", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isbn: raw, profile }),
      });
      const row = bookRow(res.book);
      $(".detail", row).hidden = false;
      $(".more", row).remove();
      const out = [row];
      if (res.warnings.length) out.push(el("p", { class: "basis", text: res.warnings.join(" ") }));
      $("#isbn-out").replaceChildren(...out);
    } catch (err) {
      $("#isbn-err").textContent = err.message;
      $("#isbn-err").hidden = false;
    } finally {
      go.disabled = false; go.textContent = "Check";
    }
  });
  $("[data-close]", $("#isbn")).addEventListener("click", () => $("#isbn").close());

  // ---------- settings ----------
  function openSettings() {
    const f = $("#settings-form");
    f.cost_per_book.value = profile.cost_per_book;
    f.min_profit.value = profile.min_profit;
    f.min_roi.value = String(profile.min_roi);
    f.sell_window_days.value = String(profile.sell_window_days);
    f.condition.value = profile.condition;
    $$("input[name=ch]", f).forEach((c) => { c.checked = profile.channels.includes(c.value); });
    $("#s-pw").value = password;
    if (!$("#settings").open) $("#settings").showModal();
  }

  $("#settings").addEventListener("close", () => {
    if ($("#settings").returnValue !== "save") return;
    const f = $("#settings-form");
    const num = (v, d) => (isFinite(parseFloat(v)) ? parseFloat(v) : d);
    const channels = $$("input[name=ch]:checked", f).map((c) => c.value);
    profile = {
      cost_per_book: num(f.cost_per_book.value, DEFAULTS.cost_per_book),
      min_profit: num(f.min_profit.value, DEFAULTS.min_profit),
      min_roi: num(f.min_roi.value, DEFAULTS.min_roi),
      sell_window_days: parseInt(f.sell_window_days.value, 10) || 90,
      condition: f.condition.value,
      channels: channels.length ? channels : DEFAULTS.channels,
    };
    password = $("#s-pw").value;
    store.set("ss.profile", profile);
    store.set("ss.pw", password);
    if (last) banner("Settings saved. They apply to your next scan.");
  });

  // ---------- wiring ----------
  $$("[data-pick]").forEach((b) => b.addEventListener("click", () => $(`#pick-${b.dataset.pick}`).click()));
  $$("[data-open=isbn]").forEach((b) => b.addEventListener("click", () => openIsbn("")));
  $("#open-isbn").addEventListener("click", () => openIsbn(""));
  $("#open-settings").addEventListener("click", openSettings);
  $$("[data-filter]").forEach((b) => b.addEventListener("click", () => setFilter(b.dataset.filter)));
  for (const id of ["#pick-camera", "#pick-library"]) {
    $(id).addEventListener("change", (e) => { const f = e.target.files[0]; e.target.value = ""; scanFile(f); });
  }

  (async () => {
    try {
      status = await (await fetch("api/status")).json();
      if (status.password_required) $("#pw-field").hidden = false;
      if (status.full_demo) banner("Demo mode: you'll see a sample shelf with simulated prices until API keys are added on the server.");
    } catch {
      banner("Can't reach the Shelf Scout server. Is it running?", true);
    }
    const saved = store.get("ss.last", null);
    const fresh = saved && saved.at && Date.now() - saved.at < 3 * 3600 * 1000;
    if (fresh && saved.res && location.hash !== "#new") {
      last = saved.res; photoURL = saved.thumb || "";
      render(last);
    }
  })();
})();
