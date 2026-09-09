// MWI Market Analyzer — frontend
// HTMX: loads index.json + per-item history JSON (no swap; parsed in afterRequest).
// Chart.js: renders selected items as price-over-time lines (no animations, no clutter).
//
// Data contract:
//   data/index.json            -> [{ slug, name }, ...]
//   data/history/<slug>.json   -> { name, grades: { "<grade>": [[ts,ask,bid,price,vol], ...] } }
//   Each row: ts(unix s), ask, bid, price, volume. Null = absent/-1 in the API.

const STATE = {
  items: [],          // [{slug,name}] sorted
  bySlug: new Map(),  // slug -> {slug,name}
  history: new Map(), // slug -> history json
  selected: [],       // slugs currently drawn, in selection order
};

const el = (id) => document.getElementById(id);

/* ---------- index loading (HTMX afterRequest) ---------- */

function onHtmxResponse(xhr) {
  if (!xhr || !xhr.responseText) return;
  const url = xhr.responseURL || "";
  try {
    if (url.endsWith("/index.json")) {
      STATE.items = JSON.parse(xhr.responseText);
      STATE.items.forEach((it) => STATE.bySlug.set(it.slug, it));
      populateDatalist();
      const st = el("status");
      st.textContent = `Loaded ${STATE.items.length} items. Select one to plot.`;
    } else if (/\/history\/.+\.json$/.test(url)) {
      const slug = decodeURIComponent(url.replace(/^.*\/history\/(.+)\.json$/, "$1"));
      const data = JSON.parse(xhr.responseText);
      STATE.history.set(slug, data);
      renderChips();
      renderChart();
    }
  } catch (e) {
    console.error("parse failed for", url, e);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.body.addEventListener("htmx:afterRequest", (e) => onHtmxResponse(e.detail?.xhr));
  el("q").addEventListener("change", onInputChange);
});

function populateDatalist() {
  const dl = el("items");
  dl.replaceChildren();
  for (const it of STATE.items) {
    const opt = document.createElement("option");
    opt.value = it.name;
    opt.dataset.slug = it.slug;
    dl.appendChild(opt);
  }
}

/* ---------- selection ---------- */

function onInputChange() {
  const input = el("q");
  const name = input.value.trim();
  if (!name) return;
  const match = STATE.items.find((it) => it.name.toLowerCase() === name.toLowerCase());
  if (!match) {
    input.value = "";
    return;
  }
  selectItem(match.slug);
  input.value = "";
}

function selectItem(slug) {
  if (STATE.selected.includes(slug)) return;
  STATE.selected.push(slug);
  renderChips();
  renderChart();
  if (!STATE.history.has(slug)) {
    fetchHistory(slug);
  }
}

function fetchHistory(slug) {
  const url = `data/history/${encodeURIComponent(slug)}.json`;
  htmx.ajax("GET", url, { swap: "none", target: "#chart-wrap" });
}

function removeItem(slug) {
  STATE.selected = STATE.selected.filter((s) => s !== slug);
  STATE.history.delete(slug);
  renderChips();
  renderChart();
}

function renderChips() {
  const box = el("selected");
  box.replaceChildren();
  STATE.selected.forEach((slug, i) => {
    const name = STATE.history.get(slug)?.name || STATE.bySlug.get(slug)?.name || slug;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = name;
    const rm = document.createElement("button");
    rm.type = "button";
    rm.textContent = "×";
    rm.title = "remove";
    rm.onclick = () => removeItem(slug);
    chip.appendChild(rm);
    box.appendChild(chip);
  });
}

/* ---------- chart ---------- */

const PALETTE = [
  "#2563eb", "#dc2626", "#059669", "#7c3aed", "#b45309",
  "#be123c", "#0891b2", "#65a30d", "#991b1b", "#3730a3",
];

function color(i) {
  return PALETTE[i % PALETTE.length];
}

function fmt(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US").format(Math.round(n));
}

// Pick one price series per item: grade 0 when present, else the smallest grade.
function pricePoints(history) {
  const grades = history.grades || {};
  const keys = Object.keys(grades).map((g) => Number(g)).sort((a, b) => a - b);
  if (keys.length === 0) return [];
  const chosen = String(keys.includes(0) ? 0 : keys[0]);
  const rows = grades[chosen] || [];
  const out = [];
  for (const row of rows) {
    const ts = row[0], a = row[1], b = row[2], p = row[3];
    let price = p;
    if (price == null && a != null && b != null) price = (a + b) / 2;
    else if (price == null && a != null) price = a;
    else if (price == null && b != null) price = b;
    else if (price == null) continue;
    out.push({ x: ts * 1000 /* ms */, y: price });
  }
  return out;
}

function renderChart() {
  const ctx = el("chart").getContext("2d");
  const datasets = STATE.selected.map((slug, i) => {
    const hist = STATE.history.get(slug);
    return {
      label: hist?.name || STATE.bySlug.get(slug)?.name || slug,
      data: hist ? pricePoints(hist) : [],
      borderColor: color(i),
      backgroundColor: "transparent",
      borderJoinStyle: "round",
      borderCapStyle: "round",
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0,
      fill: false,
    };
  });
  const emptyNote = el("empty");
  emptyNote.style.display = STATE.selected.length ? "none" : "";

  if (window.CHART) {
    window.CHART.data.datasets = datasets;
    window.CHART.update("none"); // no re-render animation
  } else {
    window.CHART = new Chart(ctx, {
      type: "line",
      data: { datasets },
      options: {
        animation: false,
        parsing: false,
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { position: "bottom", labels: { usePointStyle: true } },
          tooltip: {
            mode: "index",
            callbacks: {
              title: (ctx) => new Date(ctx[0].raw.x).toLocaleString(),
              label: (c) => `${c.dataset.label}: ${fmt(c.raw.y)}`,
            },
          },
        },
        scales: {
          x: {
            type: "linear",
            grid: { display: false },
            ticks: {
              maxRotation: 0,
              autoSkip: true,
              maxTicksLimit: 8,
              callback: (v) => new Date(v).toLocaleDateString(undefined, {
                month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
              }),
            },
          },
          y: {
            grid: { color: "rgba(0,0,0,0.05)" },
            ticks: { callback: (v) => fmt(v) },
          },
        },
      },
    });
  }
}
