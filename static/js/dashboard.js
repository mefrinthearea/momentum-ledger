/* ================================================================
   MOMENTUM LEDGER — dashboard.js
   ================================================================

   Reads main.py's actual output directly:

     GET /static/data/current_cycle.json   (mirrors current_cycle.json)
     GET /static/data/cycle_history.json   (mirrors cycle_history.json)

   Both are plain files on disk — no API layer needed. Any static
   server works (python -m http.server, Flask's default static
   route, etc). If main.py hasn't produced them yet, or the fetch
   fails for any reason, the dashboard falls back to a small demo
   snapshot so the page never renders blank or broken.

   Real schema, straight from main.py:

     current_cycle.json
       cycle_number, analysis_date, model_balance, baseline_balance,
       universe_snapshot [tickers], warming_up [tickers],
       stocks: {
         TICKER: { entry_price, score, momentum_10, momentum_30,
                   volatility_30, selected, weight }
       }

     cycle_history.json
       [ { cycle_number, start_date, end_date, model_return,
           baseline_return, model_edge, selected_average,
           rejected_average, starting_model_balance,
           ending_model_balance, starting_baseline_balance,
           ending_baseline_balance, stocks: [...] }, ... ]

   Nothing in this file invents fields main.py doesn't produce.
   The Top-5 list and the ranking table are both derived from the
   same `stocks` dict — one source of truth, no duplicate state.
   ================================================================ */

const DATA_URL_CURRENT = "/static/data/current_cycle.json";
const DATA_URL_HISTORY = "/static/data/cycle_history.json";

const STARTING_BALANCE = 10.00;
const CYCLE_LENGTH_DAYS = 10;

// Shown only if main.py hasn't produced real files yet, or the
// fetch fails (e.g. opened as a raw file instead of served).
const FALLBACK_CURRENT_CYCLE = {
  cycle_number: 1,
  analysis_date: new Date().toISOString().slice(0, 10),
  model_balance: STARTING_BALANCE,
  baseline_balance: STARTING_BALANCE,
  universe_snapshot: ["AAPL", "MSFT", "KO", "PEP", "XOM", "JNJ", "PG", "V", "DIS", "NVDA"],
  warming_up: [],
  stocks: {}
};

const FALLBACK_HISTORY = [];


// ----------------------------------------------------------------
// FETCH — real data first, quiet fallback second
// ----------------------------------------------------------------

async function loadJSON(url, fallback) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url} responded ${response.status}`);
    return await response.json();
  } catch (err) {
    console.warn(`Using fallback data for ${url}:`, err.message);
    return fallback;
  }
}


// ----------------------------------------------------------------
// FORMAT HELPERS
// ----------------------------------------------------------------

const fmtMoney    = v => `$${v.toFixed(2)}`;
const fmtPct      = v => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`;
const fmtPctTight = v => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;
const signClass   = v => (v >= 0 ? "cell-positive" : "cell-negative");

// Rough weekday count between two dates. Won't match a real
// trading calendar exactly (holidays aren't excluded), but it's
// close enough for a live progress indicator between cycles.
function weekdaysBetween(startISO, endDate) {
  const start = new Date(startISO + "T00:00:00");
  let count = 0;
  const cursor = new Date(start);
  while (cursor < endDate) {
    cursor.setDate(cursor.getDate() + 1);
    const day = cursor.getDay();
    if (day !== 0 && day !== 6) count++;
  }
  return count;
}


// ----------------------------------------------------------------
// DERIVE — build the ranking array + top-5 list from `stocks`
// ----------------------------------------------------------------

function deriveRanking(stocks) {
  return Object.entries(stocks)
    .map(([ticker, s]) => ({
      ticker,
      price: s.entry_price,
      momentum10: s.momentum_10,
      momentum30: s.momentum_30,
      volatility30: s.volatility_30,
      score: s.score,
      selected: s.selected,
      weight: s.weight
    }))
    .sort((a, b) => b.score - a.score);
}


// ----------------------------------------------------------------
// RENDER: header / stat strip
// ----------------------------------------------------------------

function renderHeader(cycle, daysElapsed) {
  document.getElementById("cycle-number").textContent = cycle.cycle_number;
  document.getElementById("cycle-start-date").textContent = cycle.analysis_date;

  const statusEl = document.getElementById("cycle-status");
  statusEl.innerHTML = "";
  const isLive = daysElapsed < CYCLE_LENGTH_DAYS;
  if (isLive) {
    const dot = document.createElement("span");
    dot.className = "live-dot";
    statusEl.appendChild(dot);
    statusEl.appendChild(document.createTextNode("Live"));
    statusEl.classList.remove("is-ready");
  } else {
    statusEl.appendChild(document.createTextNode("Ready to rebalance"));
    statusEl.classList.add("is-ready");
  }
}

function renderStats(cycle, history, daysElapsed) {
  const modelBalance = cycle.model_balance;
  const baselineBalance = cycle.baseline_balance;
  const edge = modelBalance / baselineBalance - 1;

  document.getElementById("model-balance").textContent = fmtMoney(modelBalance);
  document.getElementById("baseline-balance").textContent = fmtMoney(baselineBalance);
  document.getElementById("model-edge").textContent = fmtPct(edge);

  const cumulativeModel = modelBalance / STARTING_BALANCE - 1;
  const cumulativeBaseline = baselineBalance / STARTING_BALANCE - 1;

  const modelDelta = document.getElementById("model-delta");
  modelDelta.textContent = `${fmtPct(cumulativeModel)} since cycle 1`;
  modelDelta.className = `stat-delta ${signClass(cumulativeModel)}`;

  const baselineDelta = document.getElementById("baseline-delta");
  baselineDelta.textContent = `${fmtPct(cumulativeBaseline)} since cycle 1`;
  baselineDelta.className = `stat-delta ${signClass(cumulativeBaseline)}`;

  const edgeContext = document.getElementById("edge-context");
  if (history.length === 0) {
    edgeContext.textContent = "no completed cycles yet";
    edgeContext.className = "stat-delta";
  } else {
    const beatCount = history.filter(h => h.model_return > h.baseline_return).length;
    edgeContext.textContent = `beat baseline ${beatCount}/${history.length} cycles`;
    edgeContext.className = `stat-delta ${signClass(edge)}`;
  }

  const clamped = Math.min(daysElapsed, CYCLE_LENGTH_DAYS);
  const pct = Math.min(100, Math.round((clamped / CYCLE_LENGTH_DAYS) * 100));
  document.getElementById("cycle-progress").textContent = `${clamped} / ${CYCLE_LENGTH_DAYS} days`;
  document.getElementById("progress-fill").style.width = `${pct}%`;
}


// ----------------------------------------------------------------
// RENDER: top 5 portfolio (uses the backend's own weight — no
// re-deriving allocation math on the client)
// ----------------------------------------------------------------

function renderPortfolio(ranking, balance) {
  const container = document.getElementById("portfolio-list");
  container.innerHTML = "";

  const selected = ranking.filter(r => r.selected).sort((a, b) => b.weight - a.weight);

  if (selected.length === 0) {
    container.innerHTML = `<p class="empty-history-note">No portfolio yet — this fills in once cycle 1 runs.</p>`;
    return;
  }

  selected.forEach((stock, index) => {
    const amount = stock.weight * balance;
    const row = document.createElement("div");
    row.className = "portfolio-row";
    row.innerHTML = `
      <span class="portfolio-rank">${index + 1}</span>
      <div class="portfolio-main">
        <span class="portfolio-ticker">${stock.ticker}</span>
        <div class="portfolio-bar-track">
          <div class="portfolio-bar-fill" style="width:${(stock.weight * 100).toFixed(1)}%"></div>
        </div>
      </div>
      <span class="portfolio-weight">${(stock.weight * 100).toFixed(1)}%</span>
      <span class="portfolio-amount">${fmtMoney(amount)}</span>
    `;
    container.appendChild(row);
  });
}


// ----------------------------------------------------------------
// RENDER: full ranking table + warming-up tags
// ----------------------------------------------------------------

function renderRanking(ranking, warmingUp) {
  const body = document.getElementById("ranking-body");
  body.innerHTML = "";

  ranking.forEach((stock, index) => {
    const row = document.createElement("tr");
    if (stock.selected) row.classList.add("is-selected");
    row.innerHTML = `
      <td class="col-rank">${index + 1}</td>
      <td class="ticker-cell">${stock.ticker}</td>
      <td class="col-num">${fmtMoney(stock.price)}</td>
      <td class="col-num ${signClass(stock.momentum10)}">${fmtPctTight(stock.momentum10)}</td>
      <td class="col-num ${signClass(stock.momentum30)}">${fmtPctTight(stock.momentum30)}</td>
      <td class="col-num">${(stock.volatility30 * 100).toFixed(2)}%</td>
      <td class="col-num">${stock.score.toFixed(2)}</td>
    `;
    body.appendChild(row);
  });

  document.getElementById("ranking-count").textContent =
    ranking.length > 0 ? `${ranking.length} eligible` : "awaiting first cycle";

  const warmingContainer = document.getElementById("warming-up-list");
  warmingContainer.innerHTML = "";
  warmingUp.forEach(ticker => {
    const tag = document.createElement("span");
    tag.className = "warming-tag";
    tag.textContent = ticker;
    warmingContainer.appendChild(tag);
  });
}


// ----------------------------------------------------------------
// RENDER: cycle history table + summary note
// ----------------------------------------------------------------

function renderHistory(history) {
  const body = document.getElementById("cycle-history");
  body.innerHTML = "";

  if (history.length === 0) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="5" class="empty-history-note">No cycles completed yet — this fills in after cycle 1 finishes.</td>`;
    body.appendChild(row);
    document.getElementById("experiment-summary").textContent = "Not started";
    return;
  }

  history.forEach(cycle => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="col-rank">${cycle.cycle_number}</td>
      <td>${cycle.start_date} → ${cycle.end_date}</td>
      <td class="col-num ${signClass(cycle.model_return)}">${fmtPctTight(cycle.model_return)}</td>
      <td class="col-num ${signClass(cycle.baseline_return)}">${fmtPctTight(cycle.baseline_return)}</td>
      <td class="col-num ${signClass(cycle.model_edge)}">${fmtPctTight(cycle.model_edge)}</td>
    `;
    body.appendChild(row);
  });

  const beatCount = history.filter(h => h.model_return > h.baseline_return).length;
  const avgEdge = history.reduce((sum, h) => sum + h.model_edge, 0) / history.length;
  document.getElementById("experiment-summary").textContent =
    `${history.length} cycles · avg edge ${fmtPctTight(avgEdge)} · beat baseline ${beatCount}/${history.length}`;
}


// ----------------------------------------------------------------
// RENDER: equity curve (plain canvas, no chart library)
// ----------------------------------------------------------------

function renderChart(history) {
  const canvas = document.getElementById("performance-chart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  const pad = 28;

  ctx.clearRect(0, 0, w, h);

  const modelPoints = [STARTING_BALANCE, ...history.map(c => c.ending_model_balance)];
  const baselinePoints = [STARTING_BALANCE, ...history.map(c => c.ending_baseline_balance)];
  const allValues = [...modelPoints, ...baselinePoints];

  if (modelPoints.length < 2) {
    ctx.fillStyle = "#5a6272";
    ctx.font = "13px 'IBM Plex Mono', monospace";
    ctx.fillText("Chart fills in after cycle 1 completes", pad, h / 2);
    ctx.beginPath();
    ctx.arc(pad, h / 2 + 18, 3, 0, Math.PI * 2);
    ctx.fillStyle = "#c9a227";
    ctx.fill();
    return;
  }

  const min = Math.min(...allValues) * 0.98;
  const max = Math.max(...allValues) * 1.02;

  const xStep = (w - pad * 2) / (modelPoints.length - 1);
  const yFor = v => h - pad - ((v - min) / (max - min)) * (h - pad * 2);
  const xFor = i => pad + i * xStep;

  ctx.strokeStyle = "rgba(232,228,219,0.08)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const y = pad + ((h - pad * 2) / 3) * i;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(w - pad, y);
    ctx.stroke();
  }

  const drawLine = (points, color) => {
    ctx.beginPath();
    points.forEach((v, i) => {
      const x = xFor(i);
      const y = yFor(v);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.stroke();

    points.forEach((v, i) => {
      ctx.beginPath();
      ctx.arc(xFor(i), yFor(v), 2.5, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    });
  };

  drawLine(baselinePoints, "#6f93b8");
  drawLine(modelPoints, "#c9a227");
}


// ----------------------------------------------------------------
// RENDER: watchlist / universe
//
// Editing here is local to the browser tab only — main.py's real
// universe list lives in the Python file itself. There's no write
// path back to it yet, so edits reset on reload. That's stated
// plainly in the UI rather than pretending otherwise.
// ----------------------------------------------------------------

let localUniverse = [];

function renderUniverse(universe) {
  localUniverse = [...universe];
  const container = document.getElementById("universe-list");
  container.innerHTML = "";

  localUniverse.forEach(ticker => {
    const tag = document.createElement("span");
    tag.className = "universe-tag";
    tag.innerHTML = `${ticker} <button type="button" aria-label="Remove ${ticker}" data-ticker="${ticker}">×</button>`;
    container.appendChild(tag);
  });

  container.querySelectorAll("button[data-ticker]").forEach(btn => {
    btn.addEventListener("click", () => {
      const ticker = btn.getAttribute("data-ticker");
      renderUniverse(localUniverse.filter(t => t !== ticker));
    });
  });
}

function wireAddStockForm() {
  const form = document.getElementById("add-stock-form");
  const input = document.getElementById("ticker-input");

  form.addEventListener("submit", event => {
    event.preventDefault();
    const ticker = input.value.trim().toUpperCase();
    if (!ticker) return;

    if (!localUniverse.includes(ticker)) {
      renderUniverse([...localUniverse, ticker]);
    }
    input.value = "";
  });
}


// ----------------------------------------------------------------
// INIT
// ----------------------------------------------------------------

async function init() {
  const [cycle, history] = await Promise.all([
    loadJSON(DATA_URL_CURRENT, FALLBACK_CURRENT_CYCLE),
    loadJSON(DATA_URL_HISTORY, FALLBACK_HISTORY)
  ]);

  const ranking = deriveRanking(cycle.stocks || {});
  const daysElapsed = weekdaysBetween(cycle.analysis_date, new Date());

  renderHeader(cycle, daysElapsed);
  renderStats(cycle, history, daysElapsed);
  renderPortfolio(ranking, cycle.model_balance);
  renderRanking(ranking, cycle.warming_up || []);
  renderHistory(history);
  renderChart(history);
  renderUniverse(cycle.universe_snapshot || []);
  wireAddStockForm();
}

document.addEventListener("DOMContentLoaded", init);