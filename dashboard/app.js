/**
 * AURA Dashboard – Application Logic
 * =====================================
 * Responsibilities:
 *  - Poll backend /density-summary every 3s
 *  - Render stadium heatmap (color-coded zones)
 *  - Populate KPI + analytics table
 *  - Manual override: POST /trigger-nudge
 *  - Live event log stream
 *  - CSV export
 *
 * WHY POLLING not WebSocket:
 *   Dashboard runs standalone (no build pipeline).
 *   Polling is simpler and equally effective at 3s intervals.
 *   WebSocket can be added later as a drop-in enhancement.
 */

"use strict";

// ── Config ────────────────────────────────────────────────────
// WHY "/api": Dashboard is served by nginx which proxies /api/ → backend_core:8000.
// Using a relative path works both in Docker and when opened via localhost during dev
// (simply set BACKEND_URL to "http://localhost:8000" here for local dev without Docker).
const BACKEND_URL  = "/api";
const POLL_INTERVAL_MS = 3000;
const SECTIONS = ["A","B","C","D","E","F"];

// Section positions on the 100×100 normalized map grid
const ZONE_POSITIONS = {
  A: { left:"3%",  top:"36%", width:"22%", height:"28%" },
  B: { left:"28%", top:"4%",  width:"42%", height:"22%" },
  C: { left:"28%", top:"72%", width:"42%", height:"22%" },
  D: { left:"74%", top:"36%", width:"22%", height:"28%" },
  E: { left:"60%", top:"4%",  width:"36%", height:"22%" },
  F: { left:"60%", top:"72%", width:"36%", height:"22%" },
};

// ── State ─────────────────────────────────────────────────────
let state = {
  densities:    {},           // { sectionId: 0.0-1.0 }
  predictions:  {},           // { sectionId: 0.0-1.0 }
  ledStates:    {},           // { sectionId: "RED"|"GREEN"|"AMBER"|"WHITE" }
  activeGreenPaths: [],       // sections in current green path
  nudgeCount:   0,
  greenPathCount: 0,
  lastNudges:   {},           // sectionId → last nudge type
  connected:    false,
};

// ── DOM refs ──────────────────────────────────────────────────
const $map            = document.getElementById("stadiumMap");
const $tableBody      = document.getElementById("tableBody");
const $logStream      = document.getElementById("logStream");
const $responseBox    = document.getElementById("responseContent");
const $kpiHighRisk    = document.getElementById("kpiHighRisk");
const $kpiNudges      = document.getElementById("kpiNudges");
const $kpiGreenPaths  = document.getElementById("kpiGreenPaths");
const $kpiAvgDensity  = document.getElementById("kpiAvgDensity");
const $heatmapTs      = document.getElementById("heatmapTimestamp");
const $connectionDot  = document.getElementById("connectionDot");
const $connectionStat = document.getElementById("connectionStatus");
const $logCount       = document.getElementById("logCount");
const $densityRange   = document.getElementById("overrideDensity");
const $densityVal     = document.getElementById("overrideDensityVal");
const $triggerBtn     = document.getElementById("triggerNudgeBtn");
const $clearBtn       = document.getElementById("clearAlertsBtn");
const $exportBtn      = document.getElementById("exportBtn");
const $clock          = document.getElementById("clock");

// ── Initialization ─────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  buildMapZones();
  attachListeners();
  startClock();
  seedMockState();       // Start with realistic initial data
  poll();
  setInterval(poll, POLL_INTERVAL_MS);
});

/** Build DOM zones for each section (done once) */
function buildMapZones() {
  SECTIONS.forEach(id => {
    const zone = document.createElement("div");
    zone.className = "zone clear";
    zone.id = `zone-${id}`;
    Object.assign(zone.style, ZONE_POSITIONS[id]);

    // Accessibility: zones are interactive – expose as buttons for keyboard/screen-reader users
    zone.setAttribute("role", "button");
    zone.setAttribute("tabindex", "0");
    zone.setAttribute("aria-label", `Section ${id} – density unknown`);
    zone.title = `Section ${id} – click for details`;

    zone.innerHTML = `
      <div class="zone-label" aria-hidden="true">${id}</div>
      <div class="zone-density" id="density-${id}" aria-hidden="true">—%</div>
    `;

    // Keyboard activation (Enter / Space) mirrors click behaviour
    zone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        zone.click();
      }
    });

    $map.appendChild(zone);
  });
}

/** Seed realistic initial mock data so the dashboard isn't empty */
function seedMockState() {
  const bases = { A: 0.62, B: 0.45, C: 0.88, D: 0.31, E: 0.55, F: 0.73 };
  state.densities   = { ...bases };
  state.predictions = Object.fromEntries(
    Object.entries(bases).map(([k,v]) => [k, Math.min(1, v + 0.05)])
  );
  state.ledStates = {};
  SECTIONS.forEach(s => {
    const d = bases[s];
    state.ledStates[s] = d >= 0.70 ? "RED" : d >= 0.55 ? "AMBER" : "WHITE";
  });
  state.activeGreenPaths = ["D"];   // D is relief target for C
  renderAll(new Date().toLocaleTimeString());
}

// ── Polling ────────────────────────────────────────────────────
async function poll() {
  try {
    const [summaryResp] = await Promise.all([
      fetch(`${BACKEND_URL}/density-summary`, { signal: AbortSignal.timeout(4000) }),
    ]);

    if (summaryResp.ok) {
      const data = await summaryResp.json();
      updateStateFromSummary(data);
      setConnected(true);
    }
  } catch (err) {
    // Backend offline: animate densities as mock simulation
    console.debug("[AURA] Backend offline, simulating:", err?.message);
    simulateDensityTick();
    setConnected(false);
  }

  renderAll(new Date().toLocaleTimeString());
}

/** Update state from backend /density-summary response */
function updateStateFromSummary(data) {
  state.densities = data.densities || state.densities;
  const highRisk = data.high_risk || [];

  SECTIONS.forEach(s => {
    const d = state.densities[s] || 0;
    state.ledStates[s] = d >= 0.70 ? "RED" : d >= 0.55 ? "AMBER" : "WHITE";
  });

  // Trigger nudge log entry for newly congested sections
  highRisk.forEach(s => {
    if (!state.lastNudges[s]) {
      addLogEntry("🎯", s, `Section ${s} density ≥70% – nudge auto-evaluating`);
      state.nudgeCount++;
      updateKPIs();
    }
    state.lastNudges[s] = "auto";
  });

  $heatmapTs.textContent = `Updated ${new Date().toLocaleTimeString()}`;
}

/** Simulate density fluctuation when backend is offline.
 * WHY upward bias (0.42 vs 0.5): models the natural tendency of crowds
 * to build up toward exits before dispersing at halftime/end of event.
 */
function simulateDensityTick() {
  SECTIONS.forEach(s => {
    const prev = state.densities[s] || 0.4;
    const delta = (Math.random() - 0.42) * 0.08;   // slight upward bias
    state.densities[s] = Math.max(0.1, Math.min(1.0, prev + delta));
    const d = state.densities[s];
    state.ledStates[s] = d >= 0.70 ? "RED" : d >= 0.55 ? "AMBER" : "WHITE";

    // Trigger auto-nudge logic
    if (d >= 0.70 && !state.lastNudges[s]) {
      state.nudgeCount++;
      state.lastNudges[s] = "auto";
      addLogEntry("⚡", s, `Auto nudge triggered → redirect to ${getReliefSection(s)}`);
    } else if (d < 0.55) {
      delete state.lastNudges[s];   // Reset when section clears
    }
  });
}

const RELIEF = { A:"D", B:"E", C:"D", D:"B", E:"F", F:"D" };
const getReliefSection = s => RELIEF[s] || "D";

// ── Render ─────────────────────────────────────────────────────
function renderAll(ts) {
  renderZones();
  renderTable();
  updateKPIs();
}

function renderZones() {
  SECTIONS.forEach(s => {
    const zone     = document.getElementById(`zone-${s}`);
    const label    = document.getElementById(`density-${s}`);
    const density  = state.densities[s] || 0;
    const isGreen  = state.activeGreenPaths.includes(s);
    const pct      = (density * 100).toFixed(0);

    label.textContent = `${pct}%`;

    zone.className = "zone " + (
      isGreen             ? "green-path" :
      density >= 0.70     ? "red" :
      density >= 0.55     ? "amber" : "clear"
    );

    // Keep aria-label in sync so screen readers announce current density
    const statusText = isGreen ? "green path active" :
      density >= 0.70 ? "congested" :
      density >= 0.55 ? "moderate" : "clear";
    zone.setAttribute("aria-label", `Section ${s} – ${pct}% density, ${statusText}`);
  });
}

function renderTable() {
  $tableBody.innerHTML = "";
  SECTIONS.forEach(s => {
    const density = state.densities[s] || 0;
    const pred    = state.predictions[s] ?? density + 0.04;
    const led     = state.ledStates[s] || "WHITE";
    const status  = density >= 0.70 ? "Congested" : density >= 0.55 ? "Moderate" : "Clear";
    const badgeClass = density >= 0.70 ? "badge-red" : density >= 0.55 ? "badge-amber" : "badge-green";
    const ledBadge   = led === "RED" ? "badge-red" : led === "GREEN" ? "badge-green" :
                       led === "AMBER" ? "badge-amber" : "badge-white";
    const lastNudge  = state.lastNudges[s] || "—";

    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong>${s}</strong></td>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <div style="flex:1;height:6px;background:#1a1a3a;border-radius:3px;overflow:hidden">
            <div style="height:100%;width:${density*100}%;background:${
              density>=0.70?'var(--accent-red)':density>=0.55?'var(--accent-amber)':'var(--accent-blue)'
            };border-radius:3px;transition:width 0.4s ease"></div>
          </div>
          <span>${(density*100).toFixed(0)}%</span>
        </div>
      </td>
      <td>${(Math.min(pred,1)*100).toFixed(0)}%</td>
      <td><span class="badge ${ledBadge}">${led}</span></td>
      <td><span class="badge ${badgeClass}">${status}</span></td>
      <td style="color:var(--text-muted)">${lastNudge}</td>
    `;
    $tableBody.appendChild(row);
  });
}

function updateKPIs() {
  const densities = Object.values(state.densities);
  const highRiskCount = densities.filter(d => d >= 0.70).length;
  const avgDensity = densities.length
    ? densities.reduce((a, b) => a + b, 0) / densities.length : 0;

  $kpiHighRisk.textContent   = highRiskCount;
  $kpiNudges.textContent     = state.nudgeCount;
  $kpiGreenPaths.textContent = state.activeGreenPaths.length;
  $kpiAvgDensity.textContent = `${(avgDensity * 100).toFixed(0)}%`;
}

// ── Manual Override ────────────────────────────────────────────
function attachListeners() {
  // Density slider live feedback + ARIA attribute updates
  $densityRange.addEventListener("input", () => {
    const val = $densityRange.value;
    $densityVal.textContent = `${val}%`;
    $densityRange.setAttribute("aria-valuenow", val);
    $densityRange.setAttribute("aria-valuetext", `${val} percent`);
  });

  // Trigger nudge
  $triggerBtn.addEventListener("click", async () => {
    const section = document.getElementById("overrideSection").value;
    const density = parseInt($densityRange.value) / 100;
    const nudgeType = document.getElementById("nudgeType").value;

    $triggerBtn.disabled = true;
    $triggerBtn.textContent = "Firing…";

    try {
      const resp = await fetch(`${BACKEND_URL}/trigger-nudge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          section_id: section,
          density_score: density,
          raw_density: density,
          predicted_density_10min: Math.min(density + 0.06, 1.0),
        }),
        signal: AbortSignal.timeout(5000),
      });
      const data = await resp.json();
      $responseBox.textContent = JSON.stringify(data, null, 2);

      if (data.status === "nudge_triggered" && data.action) {
        const action = data.action;
        state.activeGreenPaths = [action.section_to];
        state.nudgeCount++;
        state.lastNudges[section] = nudgeType;
        addLogEntry("🎯", section,
          `Manual nudge: ${section}→${action.section_to} | ${action.nudge_type}`);
        renderAll();
      } else {
        addLogEntry("ℹ️", section, "No nudge action generated (density below threshold)");
      }
    } catch {
      // Use mock response when backend offline
      const mockAction = {
        action_id: `nudge-${section}-${Date.now()}`,
        section_from: section,
        section_to: getReliefSection(section),
        nudge_type: nudgeType,
        value: `Special offer at Section ${getReliefSection(section)}!`,
        reason: `Manual override: density ${($densityRange.value)}%`,
        rl_confidence: 0.75,
      };
      $responseBox.textContent = JSON.stringify({ status:"nudge_triggered", action: mockAction }, null, 2);
      state.activeGreenPaths = [mockAction.section_to];
      state.nudgeCount++;
      state.lastNudges[section] = nudgeType;
      addLogEntry("🎯", section,
        `[MOCK] Nudge: ${section}→${mockAction.section_to} | ${nudgeType}`);
      renderAll();
    } finally {
      $triggerBtn.disabled = false;
      $triggerBtn.textContent = "⚡ Trigger Nudge";
    }
  });

  // Clear alerts
  $clearBtn.addEventListener("click", () => {
    state.activeGreenPaths = [];
    state.lastNudges = {};
    $logStream.innerHTML = '<div class="log-placeholder">Log cleared.</div>';
    $logCount.textContent = "0 events";
    renderAll();
    addLogEntry("🔕", "ALL", "Alerts cleared by operator");
  });

  // Export CSV
  $exportBtn.addEventListener("click", exportCSV);
}

// ── Event Log ──────────────────────────────────────────────────
let logEntryCount = 0;

/**
 * Safely escape a string before inserting into HTML context.
 * Prevents XSS if any backend-sourced message contains special characters.
 */
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function addLogEntry(icon, section, message) {
  const placeholder = $logStream.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();

  logEntryCount++;
  $logCount.textContent = `${logEntryCount} events`;

  // Build DOM nodes explicitly to avoid innerHTML XSS on unsanitised message
  const entry = document.createElement("div");
  entry.className = "log-entry";

  const spanIcon    = document.createElement("span");
  spanIcon.className = "log-icon";
  spanIcon.textContent = icon;

  const spanTime    = document.createElement("span");
  spanTime.className = "log-time";
  spanTime.textContent = new Date().toLocaleTimeString();

  const spanSection = document.createElement("span");
  spanSection.className = "log-section";
  spanSection.textContent = `[${section}]`;

  const spanMsg     = document.createElement("span");
  spanMsg.className = "log-msg";
  spanMsg.textContent = message;   // textContent is XSS-safe

  entry.append(spanIcon, spanTime, spanSection, spanMsg);
  $logStream.prepend(entry);

  // Keep max 50 entries
  const entries = $logStream.querySelectorAll(".log-entry");
  if (entries.length > 50) entries[entries.length - 1].remove();
}

// ── CSV Export ─────────────────────────────────────────────────
function exportCSV() {
  const rows = [["Section","Density%","LED State","Status","Last Nudge"]];
  SECTIONS.forEach(s => {
    const d = state.densities[s] || 0;
    rows.push([
      s,
      (d * 100).toFixed(1),
      state.ledStates[s] || "WHITE",
      d >= 0.70 ? "Congested" : d >= 0.55 ? "Moderate" : "Clear",
      state.lastNudges[s] || "—",
    ]);
  });
  const csv = rows.map(r => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href = url; a.download = `aura_density_${Date.now()}.csv`; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 100);   // allow browser to initiate download first
}

// ── Clock ──────────────────────────────────────────────────────
function startClock() {
  const tick = () => {
    $clock.textContent = new Date().toLocaleTimeString();
  };
  tick(); setInterval(tick, 1000);
}

// ── Connection indicator ───────────────────────────────────────
function setConnected(connected) {
  state.connected = connected;
  $connectionDot.classList.toggle("connected", connected);
  const statusText = connected ? "Live (Backend)" : "Simulating";
  $connectionStat.textContent = statusText;
  // Update ARIA on the live region so screen readers announce transitions
  const bar = document.getElementById("connectionStatusBar");
  if (bar) bar.setAttribute("aria-label", `Connection status: ${statusText}`);
}
