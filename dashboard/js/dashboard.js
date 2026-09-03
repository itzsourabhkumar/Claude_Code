/* Claude Code Token Usage - dashboard
 *
 * Vanilla JS, no dependencies. Charts are hand-drawn inline SVG so the page
 * works with no network access at all.
 *
 * All filtering, sorting and pagination happen server-side (SQLite), so the
 * browser never holds more than one page of rows regardless of how much history
 * has accumulated.
 */
(function () {
  "use strict";

  // ----------------------------------------------------------------- state
  var state = {
    filters: { range: "all", from: "", to: "", year: "all", month: "all",
               project: "all", model: "all", search: "" },
    projectSort: { key: "total_tokens", dir: "desc" },
    promptSort: { key: "timestamp", dir: "desc" },
    page: 1,
    perPage: 25,
    dailyMetric: "total_tokens",
    projectMetric: "total_tokens",
    auto: false,
    autoSeconds: 60,
    autoTimer: null,
    lastProjects: [],
    lastDaily: []
  };

  var MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];

  // ------------------------------------------------------------- utilities
  function $(id) { return document.getElementById(id); }
  function el(sel, root) { return (root || document).querySelector(sel); }
  function els(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /** Compact token count for cards and axes: 1234567 -> "1.2M". */
  function compact(value) {
    if (value == null) return "—";
    var n = Number(value);
    if (!isFinite(n)) return "—";
    var abs = Math.abs(n);
    if (abs >= 1e9) return trim(n / 1e9) + "B";
    if (abs >= 1e6) return trim(n / 1e6) + "M";
    if (abs >= 1e3) return trim(n / 1e3) + "K";
    return String(Math.round(n));
  }
  function trim(n) {
    var s = n.toFixed(1);
    return s.slice(-2) === ".0" ? s.slice(0, -2) : s;
  }

  /** Exact count with thousands separators, for tables and tooltips. */
  function exact(value) {
    if (value == null) return "—";
    var n = Number(value);
    return isFinite(n) ? n.toLocaleString() : "—";
  }

  function shortModel(name) {
    if (!name) return "—";
    return String(name).replace(/^claude-/, "").replace(/-\d{8}$/, "");
  }

  function formatStamp(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso).slice(0, 16).replace("T", " ");
    var day = String(d.getDate()).padStart(2, "0");
    var mon = MONTHS[d.getMonth()].slice(0, 3);
    var hh = String(d.getHours()).padStart(2, "0");
    var mm = String(d.getMinutes()).padStart(2, "0");
    return day + "-" + mon + "-" + d.getFullYear() + " " + hh + ":" + mm;
  }

  function formatDay(dateStr) {
    var parts = String(dateStr || "").split("-");
    if (parts.length !== 3) return dateStr || "";
    return parts[2] + " " + MONTHS[Number(parts[1]) - 1].slice(0, 3);
  }

  function queryString(extra) {
    var params = new URLSearchParams();
    var f = state.filters;
    if (f.range && f.range !== "all") params.set("range", f.range);
    if (f.from) params.set("from", f.from);
    if (f.to) params.set("to", f.to);
    if (f.year && f.year !== "all") params.set("year", f.year);
    if (f.month && f.month !== "all") params.set("month", f.month);
    if (f.project && f.project !== "all") params.set("project", f.project);
    if (f.model && f.model !== "all") params.set("model", f.model);
    if (f.search) params.set("search", f.search);
    Object.keys(extra || {}).forEach(function (key) {
      if (extra[key] != null && extra[key] !== "") params.set(key, extra[key]);
    });
    return params.toString();
  }

  function api(path, extra) {
    return fetch(path + "?" + queryString(extra), { headers: { Accept: "application/json" } })
      .then(function (response) {
        if (!response.ok) throw new Error(path + " -> HTTP " + response.status);
        return response.json();
      });
  }

  function banner(message) {
    var node = $("banner");
    if (!message) { node.classList.remove("on"); node.textContent = ""; return; }
    node.textContent = message;
    node.classList.add("on");
  }

  // -------------------------------------------------------------- tooltip
  var tip = null;
  function showTip(event, html) {
    if (!tip) tip = $("tooltip");
    tip.innerHTML = html;
    tip.classList.add("on");
    tip.setAttribute("aria-hidden", "false");
    var pad = 8;
    var box = tip.getBoundingClientRect();
    var x = Math.min(Math.max(event.clientX, box.width / 2 + pad),
                     window.innerWidth - box.width / 2 - pad);
    var y = event.clientY;
    if (y - box.height - 12 < pad) y = event.clientY + box.height + 24;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }
  function hideTip() {
    if (!tip) tip = $("tooltip");
    tip.classList.remove("on");
    tip.setAttribute("aria-hidden", "true");
  }

  function tipRows(title, rows) {
    var html = '<div class="t-title">' + esc(title) + "</div>";
    rows.forEach(function (row) {
      html += '<div class="t-row"><span>' + esc(row[0]) + "</span><b>" + esc(row[1]) + "</b></div>";
    });
    return html;
  }

  // ---------------------------------------------------------------- charts
  var SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  /** "Nice" axis maximum plus tick values, so labels land on round numbers. */
  function niceScale(max, ticks) {
    if (!(max > 0)) return { max: 1, ticks: [0, 1] };
    var step = Math.pow(10, Math.floor(Math.log10(max / ticks)));
    var candidates = [1, 2, 2.5, 5, 10];
    var chosen = step;
    for (var i = 0; i < candidates.length; i++) {
      chosen = step * candidates[i];
      if (max / chosen <= ticks) break;
    }
    var top = Math.ceil(max / chosen) * chosen;
    var values = [];
    for (var v = 0; v <= top + chosen / 2; v += chosen) values.push(v);
    return { max: top, ticks: values };
  }

  function emptyChart(host, message) {
    host.innerHTML = '<div class="empty"><strong>No data for this filter</strong>' +
                     esc(message || "Adjust the filters or record some usage.") + "</div>";
  }

  /* ---- daily: area + line with crosshair ---------------------------- */
  function renderDailyChart(rows) {
    var host = $("daily-chart");
    host.innerHTML = "";
    if (!rows || !rows.length) { emptyChart(host); return; }

    var metric = state.dailyMetric;
    var values = rows.map(function (r) { return Number(r[metric] || 0); });
    var W = Math.max(host.clientWidth || 560, 320);
    var H = 260;
    var m = { top: 12, right: 14, bottom: 26, left: 54 };
    var iw = W - m.left - m.right;
    var ih = H - m.top - m.bottom;

    var scale = niceScale(Math.max.apply(null, values), 5);
    var x = function (i) {
      return rows.length === 1 ? m.left + iw / 2 : m.left + (i / (rows.length - 1)) * iw;
    };
    var y = function (v) { return m.top + ih - (v / scale.max) * ih; };

    var svg = svgEl("svg", {
      class: "chart", viewBox: "0 0 " + W + " " + H,
      width: W, height: H, role: "img",
      "aria-label": "Daily " + metric.replace(/_/g, " ")
    });

    // Recessive gridlines and value axis.
    scale.ticks.forEach(function (value) {
      svg.appendChild(svgEl("line", {
        class: "grid-line", x1: m.left, x2: W - m.right, y1: y(value), y2: y(value)
      }));
      var label = svgEl("text", {
        class: "axis-text value", x: m.left - 8, y: y(value) + 3.5, "text-anchor": "end"
      });
      label.textContent = compact(value);
      svg.appendChild(label);
    });

    // Date axis: at most 7 labels so they never collide.
    var stride = Math.max(1, Math.ceil(rows.length / 7));
    rows.forEach(function (row, i) {
      if (i % stride !== 0 && i !== rows.length - 1) return;
      var label = svgEl("text", {
        class: "axis-text", x: x(i), y: H - 8, "text-anchor": "middle"
      });
      label.textContent = formatDay(row.date);
      svg.appendChild(label);
    });

    var line = "", area = "";
    values.forEach(function (value, i) {
      line += (i ? " L" : "M") + x(i).toFixed(1) + " " + y(value).toFixed(1);
    });
    area = line + " L" + x(values.length - 1).toFixed(1) + " " + (m.top + ih) +
           " L" + x(0).toFixed(1) + " " + (m.top + ih) + " Z";

    svg.appendChild(svgEl("path", { class: "series-area", d: area }));
    svg.appendChild(svgEl("path", { class: "series-line", d: line }));

    // Hover layer: crosshair + highlighted point + tooltip.
    var crosshair = svgEl("line", { class: "crosshair", y1: m.top, y2: m.top + ih, opacity: 0 });
    var marker = svgEl("circle", { class: "series-dot", r: 5, opacity: 0 });
    svg.appendChild(crosshair);
    svg.appendChild(marker);

    var hit = svgEl("rect", { class: "hit", x: m.left, y: m.top, width: iw, height: ih });
    svg.appendChild(hit);

    function nearest(event) {
      var box = svg.getBoundingClientRect();
      var px = (event.clientX - box.left) * (W / box.width);
      var i = rows.length === 1 ? 0
        : Math.round(((px - m.left) / iw) * (rows.length - 1));
      return Math.min(rows.length - 1, Math.max(0, i));
    }

    hit.addEventListener("mousemove", function (event) {
      var i = nearest(event);
      var row = rows[i];
      crosshair.setAttribute("x1", x(i));
      crosshair.setAttribute("x2", x(i));
      crosshair.setAttribute("opacity", 1);
      marker.setAttribute("cx", x(i));
      marker.setAttribute("cy", y(values[i]));
      marker.setAttribute("opacity", 1);
      showTip(event, tipRows(formatDay(row.date) + " " + String(row.date).slice(0, 4), [
        ["Prompts", exact(row.prompts)],
        ["Input", exact(row.input_tokens)],
        ["Output", exact(row.output_tokens)],
        ["Cache", exact(row.cache_tokens)],
        ["Total", exact(row.total_tokens)]
      ]));
    });
    hit.addEventListener("mouseleave", function () {
      crosshair.setAttribute("opacity", 0);
      marker.setAttribute("opacity", 0);
      hideTip();
    });

    host.appendChild(svg);
  }

  /* ---- projects: horizontal bars with direct labels ----------------- */
  function roundedRightBar(x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w, h / 2));
    if (w <= 0) return "";
    return "M" + x + " " + y +
           " H" + (x + w - r) +
           " Q" + (x + w) + " " + y + " " + (x + w) + " " + (y + r) +
           " V" + (y + h - r) +
           " Q" + (x + w) + " " + (y + h) + " " + (x + w - r) + " " + (y + h) +
           " H" + x + " Z";
  }

  function renderProjectChart(rows) {
    var host = $("project-chart");
    host.innerHTML = "";
    if (!rows || !rows.length) { emptyChart(host); return; }

    var metric = state.projectMetric;
    var data = rows.map(function (r) {
      return { name: r.project || "unknown", value: Number(r[metric] || 0), row: r };
    }).sort(function (a, b) { return b.value - a.value; });

    // Never generate extra hues: everything past the top 10 folds into "Other".
    var TOP = 10;
    if (data.length > TOP) {
      var rest = data.slice(TOP);
      var other = rest.reduce(function (sum, d) { return sum + d.value; }, 0);
      data = data.slice(0, TOP);
      data.push({ name: "Other (" + rest.length + " projects)", value: other, row: null });
    }

    var rowH = 26, gap = 6;
    var W = Math.max(host.clientWidth || 560, 320);
    var labelW = Math.min(190, Math.max(110, Math.round(W * 0.3)));
    var valueW = 68;
    var m = { top: 6, right: valueW, bottom: 6, left: labelW };
    var iw = Math.max(40, W - m.left - m.right - 10);
    var H = m.top + m.bottom + data.length * rowH + (data.length - 1) * gap;

    var max = Math.max.apply(null, data.map(function (d) { return d.value; })) || 1;
    var svg = svgEl("svg", {
      class: "chart", viewBox: "0 0 " + W + " " + H, width: W, height: H,
      role: "img", "aria-label": "Token usage by project"
    });

    data.forEach(function (d, i) {
      var y = m.top + i * (rowH + gap);
      var barH = rowH - 6;
      var w = Math.max(2, (d.value / max) * iw);

      var name = svgEl("text", {
        class: "bar-name", x: m.left - 10, y: y + barH / 2 + 4.5, "text-anchor": "end"
      });
      name.textContent = d.name.length > 26 ? d.name.slice(0, 24) + "…" : d.name;
      svg.appendChild(name);

      svg.appendChild(svgEl("rect", {
        class: "bar-track", x: m.left, y: y + 3, width: iw, height: barH, rx: 4
      }));
      var bar = svgEl("path", {
        class: "bar", d: roundedRightBar(m.left, y + 3, w, barH, 4)
      });
      svg.appendChild(bar);

      // Direct label - identity and magnitude never depend on colour alone.
      var value = svgEl("text", {
        class: "bar-label", x: m.left + iw + 8, y: y + barH / 2 + 4.5
      });
      value.textContent = compact(d.value);
      svg.appendChild(value);

      var hit = svgEl("rect", {
        class: "hit", x: 0, y: y, width: W, height: rowH
      });
      hit.addEventListener("mousemove", function (event) {
        var r = d.row;
        showTip(event, r ? tipRows(d.name, [
          ["Prompts", exact(r.prompts)],
          ["Input", exact(r.input_tokens)],
          ["Output", exact(r.output_tokens)],
          ["Cache", exact(r.cache_tokens)],
          ["Total", exact(r.total_tokens)],
          ["Share", (r.pct != null ? r.pct : 0) + "%"]
        ]) : tipRows(d.name, [[metric.replace(/_/g, " "), exact(d.value)]]));
      });
      hit.addEventListener("mouseleave", hideTip);
      if (d.row) {
        hit.style.cursor = "pointer";
        hit.addEventListener("click", function () {
          $("f-project").value = d.name;
          state.filters.project = d.name;
          state.page = 1;
          load();
        });
      }
      svg.appendChild(hit);
    });

    host.appendChild(svg);
  }

  // ---------------------------------------------------------------- render
  function renderCards(summary) {
    var prompts = summary.prompts || 0;
    var human = summary.human_prompts || 0;
    $("c-prompts").textContent = exact(prompts);
    $("c-prompts-foot").textContent = human === prompts
      ? "all user-initiated"
      : exact(human) + " user-initiated";

    $("c-input").textContent = compact(summary.input_tokens);
    $("c-input-foot").textContent = exact(summary.input_tokens) + " tokens";

    $("c-output").textContent = compact(summary.output_tokens);
    $("c-output-foot").textContent = exact(summary.output_tokens) + " tokens";

    $("c-cache").textContent = compact(summary.cache_tokens);
    $("c-cache-foot").textContent = compact(summary.cache_read_input_tokens) + " read · " +
                                    compact(summary.cache_creation_input_tokens) + " written";

    $("c-total").textContent = compact(summary.total_tokens);
    $("c-total-foot").textContent = exact(summary.total_tokens) + " tokens";

    $("c-projects").textContent = exact(summary.projects);
    $("c-projects-foot").textContent = summary.first_date
      ? summary.first_date + " → " + summary.last_date
      : "no data yet";
  }

  function sortRows(rows, sort) {
    var key = sort.key, dir = sort.dir === "asc" ? 1 : -1;
    return rows.slice().sort(function (a, b) {
      var av = a[key], bv = b[key];
      if (typeof av === "string" || typeof bv === "string") {
        return String(av || "").localeCompare(String(bv || "")) * dir;
      }
      return ((av || 0) - (bv || 0)) * dir;
    });
  }

  function renderProjectTable(rows) {
    var body = el("#project-table tbody");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">No projects match this filter.</td></tr>';
      return;
    }
    var sorted = sortRows(rows, state.projectSort);
    body.innerHTML = sorted.map(function (r) {
      var pct = r.pct != null ? r.pct : 0;
      return "<tr>" +
        '<td><a href="#" class="project-link" data-project="' + esc(r.project) + '">' +
          esc(r.project) + "</a></td>" +
        '<td class="num">' + exact(r.prompts) + "</td>" +
        '<td class="num">' + exact(r.input_tokens) + "</td>" +
        '<td class="num">' + exact(r.output_tokens) + "</td>" +
        '<td class="num">' + exact(r.cache_tokens) + "</td>" +
        '<td class="num">' + exact(r.total_tokens) + "</td>" +
        '<td class="num"><span class="share"><span class="track">' +
          '<span class="fill" style="width:' + Math.min(100, pct) + '%"></span></span>' +
          '<span class="pct">' + pct.toFixed(1) + "%</span></span></td>" +
      "</tr>";
    }).join("");

    els(".project-link", body).forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        var name = link.getAttribute("data-project");
        state.filters.project = name;
        $("f-project").value = name;
        state.page = 1;
        load();
      });
    });
  }

  function highlight(text, needle) {
    var safe = esc(text);
    if (!needle) return safe;
    try {
      var pattern = new RegExp("(" + needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
      return safe.replace(pattern, "<mark>$1</mark>");
    } catch (err) {
      return safe;
    }
  }

  function renderPromptTable(result) {
    var body = el("#prompt-table tbody");
    if (!result.rows.length) {
      body.innerHTML = '<tr><td colspan="9" class="empty">' +
        "<strong>No interactions match this filter</strong>" +
        "Try widening the date range or clearing the prompt search.</td></tr>";
    } else {
      var needle = state.filters.search;
      body.innerHTML = result.rows.map(function (r) {
        var prompt = r.prompt
          ? '<div class="text">' + highlight(r.prompt, needle) + "</div>"
          : '<div class="none">' + (r.prompt_length
              ? "prompt not stored (" + exact(r.prompt_length) + " chars)"
              : "no prompt text") + "</div>";
        return "<tr>" +
          '<td class="mono">' + esc(formatStamp(r.timestamp)) + "</td>" +
          "<td>" + esc(r.project || "—") + "</td>" +
          '<td><span class="pill">' + esc(r.git_branch || "—") + "</span></td>" +
          "<td>" + esc(shortModel(r.model)) + "</td>" +
          '<td class="prompt-cell">' + prompt + "</td>" +
          '<td class="num">' + exact(r.input_tokens) + "</td>" +
          '<td class="num">' + exact(r.output_tokens) + "</td>" +
          '<td class="num">' + exact(r.cache_tokens) + "</td>" +
          '<td class="num">' + exact(r.total_tokens) + "</td>" +
        "</tr>";
      }).join("");

      els(".prompt-cell .text", body).forEach(function (node) {
        node.addEventListener("click", function () {
          node.parentNode.classList.toggle("open");
        });
      });
    }

    var from = result.total ? result.offset + 1 : 0;
    var to = Math.min(result.offset + result.limit, result.total);
    $("page-count").textContent = exact(from) + "–" + exact(to) + " of " + exact(result.total);
    renderPager(result);
  }

  function renderPager(result) {
    var pages = result.pages || 1;
    var current = result.page || 1;
    $("btn-prev").disabled = current <= 1;
    $("btn-next").disabled = current >= pages;

    // A windowed pager: first, last, and a run around the current page.
    var wanted = new Set([1, pages, current]);
    for (var d = 1; d <= 2; d++) {
      if (current - d >= 1) wanted.add(current - d);
      if (current + d <= pages) wanted.add(current + d);
    }
    var list = Array.from(wanted).sort(function (a, b) { return a - b; });

    var host = $("page-buttons");
    host.innerHTML = "";
    var previous = 0;
    list.forEach(function (page) {
      if (previous && page - previous > 1) {
        var gap = document.createElement("span");
        gap.textContent = "…";
        gap.style.padding = "0 4px";
        gap.style.color = "var(--text-muted)";
        host.appendChild(gap);
      }
      var button = document.createElement("button");
      button.className = "btn small";
      button.type = "button";
      button.textContent = String(page);
      if (page === current) button.setAttribute("aria-current", "page");
      button.addEventListener("click", function () {
        state.page = page;
        loadPrompts();
      });
      host.appendChild(button);
      previous = page;
    });
  }

  function describeFilters() {
    var f = state.filters, parts = [];
    if (f.from || f.to) parts.push((f.from || "start") + " → " + (f.to || "today"));
    else if (f.range && f.range !== "all") {
      parts.push(el('#quick-ranges [data-range="' + f.range + '"]').textContent);
    }
    if (f.year !== "all") parts.push("Year " + f.year);
    if (f.month !== "all") parts.push(MONTHS[Number(f.month) - 1]);
    if (f.project !== "all") parts.push("Project: " + f.project);
    if (f.model !== "all") parts.push("Model: " + shortModel(f.model));
    if (f.search) parts.push('Search: "' + f.search + '"');
    $("filter-summary").textContent = parts.length ? parts.join(" · ") : "All time";
  }

  // ------------------------------------------------------------- data load
  var busy = false;

  function loadPrompts() {
    return api("/api/prompts", {
      page: state.page,
      per_page: state.perPage,
      sort: state.promptSort.key,
      dir: state.promptSort.dir
    }).then(renderPromptTable);
  }

  function load() {
    if (busy) return Promise.resolve();
    busy = true;
    document.body.classList.add("loading");
    describeFilters();

    return Promise.all([
      api("/api/summary"),
      api("/api/projects"),
      api("/api/usage"),
      loadPrompts()
    ]).then(function (results) {
      banner("");
      renderCards(results[0]);
      state.lastProjects = results[1].rows || [];
      state.lastDaily = results[2].rows || [];
      renderProjectChart(state.lastProjects);
      renderProjectTable(state.lastProjects);
      renderDailyChart(state.lastDaily);
      $("last-updated").textContent = formatStamp(results[0].generated_at);
      $("status-dot").classList.remove("stale");
    }).catch(function (error) {
      $("status-dot").classList.add("stale");
      banner("Could not load usage data: " + error.message +
             " — is the dashboard server still running?");
    }).finally(function () {
      busy = false;
      document.body.classList.remove("loading");
    });
  }

  function loadMeta() {
    return fetch("/api/meta").then(function (r) { return r.json(); }).then(function (meta) {
      var bits = [];
      if (meta.total_interactions) bits.push(exact(meta.total_interactions) + " interactions tracked");
      if (!meta.store_prompt_text) bits.push("prompt text storage disabled");
      $("meta-line").textContent = bits.length ? bits.join(" · ") : "Local usage tracker";
      $("footer-meta").textContent = "tracker v" + meta.tracker_version +
        (meta.last_date ? " · latest data " + meta.last_date : "");
      if (!meta.total_interactions) {
        banner("No usage recorded yet. Run: python -m tracker.cli backfill  " +
               "(imports existing Claude Code history), or start a Claude Code session.");
      }
    }).catch(function () { /* meta is cosmetic */ });
  }

  function loadFilterOptions() {
    return fetch("/api/filters").then(function (r) { return r.json(); }).then(function (data) {
      fillSelect($("f-project"), data.projects, "All Projects", state.filters.project);
      fillSelect($("f-model"), data.models, "All Models", state.filters.model,
                 function (value) { return shortModel(value); });
      fillSelect($("f-year"), (data.years || []).map(String), "All Years", state.filters.year);
    }).catch(function () { /* dropdowns stay as-is */ });
  }

  function fillSelect(select, values, allLabel, current, labeller) {
    var options = ['<option value="all">' + esc(allLabel) + "</option>"];
    (values || []).forEach(function (value) {
      options.push('<option value="' + esc(value) + '">' +
                   esc(labeller ? labeller(value) : value) + "</option>");
    });
    select.innerHTML = options.join("");
    select.value = current && values && values.indexOf(current) >= 0 ? current : "all";
  }

  // ----------------------------------------------------------------- wiring
  function readFilters() {
    state.filters.from = $("f-from").value;
    state.filters.to = $("f-to").value;
    state.filters.year = $("f-year").value;
    state.filters.month = $("f-month").value;
    state.filters.project = $("f-project").value;
    state.filters.model = $("f-model").value;
    state.filters.search = $("f-search").value.trim();
    if (state.filters.from || state.filters.to) {
      state.filters.range = "all";
      setQuickActive(null);
    }
    state.page = 1;
  }

  function setQuickActive(range) {
    els("#quick-ranges button").forEach(function (button) {
      button.setAttribute("aria-pressed", button.getAttribute("data-range") === range ? "true" : "false");
    });
  }

  function resetFilters() {
    state.filters = { range: "all", from: "", to: "", year: "all", month: "all",
                      project: "all", model: "all", search: "" };
    $("f-from").value = "";
    $("f-to").value = "";
    ["f-year", "f-month", "f-project", "f-model"].forEach(function (id) { $(id).value = "all"; });
    $("f-search").value = "";
    setQuickActive("all");
    state.page = 1;
    load();
  }

  function setAuto(on) {
    state.auto = on;
    var button = $("btn-auto");
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = "Auto Refresh: " + (on ? "ON" : "OFF");
    if (state.autoTimer) { clearInterval(state.autoTimer); state.autoTimer = null; }
    if (on) {
      state.autoTimer = setInterval(function () {
        // Ask the server to pick up newly written JSONL, then redraw.
        fetch("/api/refresh").catch(function () {}).then(load);
      }, state.autoSeconds * 1000);
    }
    try { localStorage.setItem("cctracker.auto", on ? "1" : "0"); } catch (e) {}
  }

  function bindSortable(tableId, sortState, onSort) {
    els("#" + tableId + " th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.getAttribute("data-sort");
        if (sortState.key === key) {
          sortState.dir = sortState.dir === "asc" ? "desc" : "asc";
        } else {
          sortState.key = key;
          sortState.dir = key === "project" || key === "model" ? "asc" : "desc";
        }
        els("#" + tableId + " th.sortable").forEach(function (other) {
          other.removeAttribute("data-dir");
        });
        th.setAttribute("data-dir", sortState.dir);
        onSort();
      });
    });
  }

  function applyTheme(theme) {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
    try { localStorage.setItem("cctracker.theme", theme || ""); } catch (e) {}
    // Chart colours come from CSS variables, so redraw after a theme change.
    if (state.lastDaily.length) renderDailyChart(state.lastDaily);
    if (state.lastProjects.length) renderProjectChart(state.lastProjects);
  }

  function init() {
    // Month dropdown is static; year comes from the data.
    $("f-month").innerHTML = '<option value="all">All Months</option>' +
      MONTHS.map(function (name, i) {
        return '<option value="' + (i + 1) + '">' + name + "</option>";
      }).join("");

    els("#quick-ranges button").forEach(function (button) {
      button.addEventListener("click", function () {
        var range = button.getAttribute("data-range");
        state.filters.range = range;
        state.filters.from = "";
        state.filters.to = "";
        $("f-from").value = "";
        $("f-to").value = "";
        setQuickActive(range);
        state.page = 1;
        load();
      });
    });

    $("btn-apply").addEventListener("click", function () { readFilters(); load(); });
    $("btn-reset").addEventListener("click", resetFilters);
    $("btn-refresh").addEventListener("click", function () {
      readFilters();
      fetch("/api/refresh").catch(function () {}).then(function () {
        return Promise.all([load(), loadMeta(), loadFilterOptions()]);
      });
    });

    $("f-search").addEventListener("keydown", function (event) {
      if (event.key === "Enter") { readFilters(); load(); }
    });
    ["f-year", "f-month", "f-project", "f-model"].forEach(function (id) {
      $(id).addEventListener("change", function () { readFilters(); load(); });
    });

    $("f-per-page").addEventListener("change", function () {
      state.perPage = Number($("f-per-page").value) || 25;
      state.page = 1;
      loadPrompts();
    });
    $("btn-prev").addEventListener("click", function () {
      if (state.page > 1) { state.page--; loadPrompts(); }
    });
    $("btn-next").addEventListener("click", function () {
      state.page++; loadPrompts();
    });

    els("#daily-metric button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.dailyMetric = button.getAttribute("data-metric");
        els("#daily-metric button").forEach(function (other) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        renderDailyChart(state.lastDaily);
      });
    });
    els("#project-metric button").forEach(function (button) {
      button.addEventListener("click", function () {
        state.projectMetric = button.getAttribute("data-metric");
        els("#project-metric button").forEach(function (other) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        });
        renderProjectChart(state.lastProjects);
      });
    });

    bindSortable("project-table", state.projectSort, function () {
      renderProjectTable(state.lastProjects);
    });
    bindSortable("prompt-table", state.promptSort, function () {
      state.page = 1;
      loadPrompts();
    });

    // Exports go through the API so they respect the active filters exactly.
    $("btn-csv").addEventListener("click", function () {
      readFilters();
      window.location.href = "/api/export.csv?" + queryString();
    });
    $("btn-json").addEventListener("click", function () {
      readFilters();
      window.location.href = "/api/export.json?" + queryString();
    });

    $("btn-auto").addEventListener("click", function () { setAuto(!state.auto); });
    $("f-interval").addEventListener("change", function () {
      state.autoSeconds = Number($("f-interval").value) || 60;
      if (state.auto) setAuto(true);
    });

    $("theme-toggle").addEventListener("click", function () {
      var current = document.documentElement.getAttribute("data-theme");
      var prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      var next = current === "dark" ? "light" : current === "light" ? "dark"
               : (prefersDark ? "light" : "dark");
      applyTheme(next);
    });

    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        if (state.lastDaily.length) renderDailyChart(state.lastDaily);
        if (state.lastProjects.length) renderProjectChart(state.lastProjects);
      }, 150);
    });

    try {
      var savedTheme = localStorage.getItem("cctracker.theme");
      if (savedTheme) document.documentElement.setAttribute("data-theme", savedTheme);
      if (localStorage.getItem("cctracker.auto") === "1") setAuto(true);
    } catch (e) { /* private browsing */ }

    loadMeta();
    loadFilterOptions().then(load);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
