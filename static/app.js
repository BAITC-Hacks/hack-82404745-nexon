const API = "";

let allEmployees = [];
let activeEmployeeId = null;
let showAILogic = false;

// ── Tabs ─────────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "hr") loadHR();
  });
});

// ── Employee list ────────────────────────────────────────────────────

async function loadEmployees() {
  const res = await fetch(`${API}/api/employees`);
  allEmployees = await res.json();
  renderEmployeeList(allEmployees);
}

function renderEmployeeList(list) {
  const container = document.getElementById("employee-list");
  container.innerHTML = "";
  for (const e of list) {
    const row = document.createElement("div");
    row.className = "employee-row" + (e.employee_id === activeEmployeeId ? " active" : "");
    row.innerHTML = `
      <div class="row-name">${escapeHtml(e.full_name)}</div>
      <div class="row-meta">${e.employee_id} · ${escapeHtml(e.role)} · ${e.grade}</div>
    `;
    row.addEventListener("click", () => selectEmployee(e.employee_id));
    container.appendChild(row);
  }
}

document.getElementById("employee-search").addEventListener("input", (ev) => {
  const q = ev.target.value.trim().toLowerCase();
  const filtered = allEmployees.filter(
    (e) =>
      e.employee_id.toLowerCase().includes(q) ||
      e.full_name.toLowerCase().includes(q) ||
      e.role.toLowerCase().includes(q) ||
      e.department.toLowerCase().includes(q)
  );
  renderEmployeeList(filtered);
});

// ── Profile ──────────────────────────────────────────────────────────

async function selectEmployee(employeeId) {
  activeEmployeeId = employeeId;
  renderEmployeeList(
    allEmployees.filter((e) => document.getElementById("employee-search").value.trim() === "" ||
      e.full_name.toLowerCase().includes(document.getElementById("employee-search").value.trim().toLowerCase()) ||
      e.employee_id.toLowerCase().includes(document.getElementById("employee-search").value.trim().toLowerCase()))
  );
  const main = document.getElementById("profile-main");
  main.innerHTML = `<div class="loading">Считаем траекторию и рекомендации...</div>`;
  try {
    const res = await fetch(`${API}/api/employees/${employeeId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const profile = await res.json();
    renderProfile(profile);
  } catch (err) {
    main.innerHTML = `<div class="empty-state">Ошибка загрузки профиля: ${escapeHtml(String(err))}</div>`;
  }
}

function renderProfile(p) {
  const main = document.getElementById("profile-main");
  main.innerHTML = "";

  const header = document.createElement("div");
  header.className = "card profile-header";
  header.innerHTML = `
    <div>
      <div class="name">${escapeHtml(p.full_name)}</div>
      <div class="meta">${escapeHtml(p.department)} · ${escapeHtml(p.role)} · ${p.grade} · стаж ${p.tenure_months} мес.</div>
      <div style="margin-top:8px">
        <span class="badge">${p.employee_id}</span>
        <span class="badge">${p.work_format}</span>
        <span class="badge">язык: ${p.preferred_language}</span>
        ${p.career_goal ? `<span class="badge">цель: ${escapeHtml(p.career_goal.target_role)} ${p.career_goal.target_grade}</span>` : ""}
      </div>
    </div>
  `;
  main.appendChild(header);

  const trajCard = document.createElement("div");
  trajCard.className = "card";
  trajCard.innerHTML = `<h3>Траектория и разрывы по навыкам</h3>`;
  for (const t of p.trajectories) {
    const block = document.createElement("div");
    block.className = "trajectory-block";
    const label = t.label === "career_goal" ? "Карьерная цель" : "Следующий грейд";
    block.innerHTML = `
      <div class="trajectory-title">
        <div>${label}: <strong>${escapeHtml(t.role)} ${t.grade}</strong></div>
        <div class="readiness-pill">${t.readiness_pct}%</div>
      </div>
    `;
    const skillsWrap = document.createElement("div");
    const fillsToAnimate = [];
    for (const s of t.skills) {
      const pct = s.required_level ? Math.min(100, Math.round((s.current_level / s.required_level) * 100)) : 100;
      const row = document.createElement("div");
      row.className = "skill-bar-row";
      row.innerHTML = `
        <div class="skill-name${s.critical ? " critical" : ""}">${escapeHtml(s.name)}</div>
        <div class="skill-bar-track"><div class="skill-bar-fill${s.gap > 0 ? " gap" : ""}" style="width:0%"></div></div>
        <div class="skill-level">${s.current_level}/${s.required_level}</div>
      `;
      skillsWrap.appendChild(row);
      fillsToAnimate.push([row.querySelector(".skill-bar-fill"), pct]);
    }
    block.appendChild(skillsWrap);
    trajCard.appendChild(block);
    // animate fill on next frame so the CSS width transition actually plays
    requestAnimationFrame(() => {
      for (const [el, pct] of fillsToAnimate) el.style.width = `${pct}%`;
    });
  }
  main.appendChild(trajCard);

  const recCard = document.createElement("div");
  recCard.className = "card";
  recCard.innerHTML = `
    <div class="rec-card-header">
      <h3>Рекомендованные шаги</h3>
      <label class="ai-logic-toggle">
        <input type="checkbox" id="ai-logic-checkbox" ${showAILogic ? "checked" : ""} />
        <span>🔍 Показать логику AI</span>
      </label>
    </div>
  `;
  recCard.querySelector("#ai-logic-checkbox").addEventListener("change", (ev) => {
    showAILogic = ev.target.checked;
    document.body.classList.toggle("show-ai-logic", showAILogic);
  });
  if (p.recommendations.length === 0) {
    recCard.innerHTML += `<div class="empty-state">Нет активных рекомендаций — либо всё выполнено, либо нет подходящих активностей.</div>`;
  }
  for (const r of p.recommendations) {
    const card = document.createElement("div");
    card.className = "rec-card";
    const sessions = r.upcoming_sessions.length ? r.upcoming_sessions.join(", ") : "доступно в любое время";
    const b = r.score_breakdown;
    card.innerHTML = `
      <div class="rec-title">${escapeHtml(r.title)}</div>
      <div class="rec-meta">${r.type} · ${r.format} · ${r.duration_hours} ч · сессии: ${sessions} · score ${r.score}</div>
      <div class="rec-explanation">${escapeHtml(r.explanation)}</div>
      <div class="logic-panel">
        <div class="logic-row"><span>gap_score (вклад закрытия разрыва)</span><span>${b.gap_score}</span></div>
        <div class="logic-row"><span>critical_weight_applied (навык критичен → ×2)</span><span>${b.critical_weight_applied}</span></div>
        <div class="logic-row"><span>history_risk_ratio (доля пропусков в этом формате)</span><span>${b.history_risk_ratio}</span></div>
        <div class="logic-row"><span>risk_multiplier (штраф за историю)</span><span>${b.risk_multiplier}</span></div>
        <div class="logic-row highlight"><span>final_score = gap_score × risk_multiplier</span><span>${b.final_score}</span></div>
      </div>
      <ul class="rec-factors">
        ${r.factors.map((f) => `<li>${escapeHtml(f.detail)}</li>`).join("")}
      </ul>
      <button class="btn" data-event="${r.event_id}">✓ Отметить выполненным</button>
    `;
    card.querySelector("button").addEventListener("click", (ev) => completeActivity(p.employee_id, r.event_id, ev.target));
    recCard.appendChild(card);
  }
  main.appendChild(recCard);

  const histCard = document.createElement("div");
  histCard.className = "card";
  histCard.innerHTML = `<h3>История активностей</h3>`;
  const table = document.createElement("table");
  table.className = "activity-table";
  table.innerHTML = `
    <thead><tr><th>Дата</th><th>Активность</th><th>Статус</th><th>%</th></tr></thead>
    <tbody>
      ${p.recent_activity
        .map(
          (a) => `<tr>
            <td>${a.date}</td>
            <td>${escapeHtml(a.event_title)}</td>
            <td class="status-${a.status}">${a.status}</td>
            <td>${a.completion_pct}</td>
          </tr>`
        )
        .join("")}
    </tbody>
  `;
  histCard.appendChild(table);
  main.appendChild(histCard);

  document.body.classList.toggle("show-ai-logic", showAILogic);
}

async function completeActivity(employeeId, eventId, buttonEl) {
  if (buttonEl) {
    buttonEl.disabled = true;
    buttonEl.textContent = "Обновляем...";
    buttonEl.closest(".rec-card")?.classList.add("rec-card-completing");
  }
  const res = await fetch(`${API}/api/employees/${employeeId}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_id: eventId }),
  });
  if (res.ok) {
    const profile = await res.json();
    renderProfile(profile);
    flashUpdated();
  } else {
    alert("Не удалось обновить прогресс");
  }
}

// ── HR overview ──────────────────────────────────────────────────────

let hrLoaded = false;

async function loadHR() {
  if (hrLoaded) return;
  hrLoaded = true;
  const container = document.getElementById("hr-content");
  container.innerHTML = `<div class="loading">Считаем срез по компании...</div>`;
  try {
    const res = await fetch(`${API}/api/hr/overview`);
    const data = await res.json();
    renderHR(data);
  } catch (err) {
    container.innerHTML = `<div class="empty-state">Ошибка: ${escapeHtml(String(err))}</div>`;
    hrLoaded = false;
  }
}

function renderHR(d) {
  const container = document.getElementById("hr-content");
  container.innerHTML = "";

  const summary = document.createElement("div");
  summary.className = "card";
  summary.innerHTML = `<h2>HR-обзор</h2><div class="meta">Всего сотрудников: ${d.total_employees}</div>`;
  container.appendChild(summary);

  const grid = document.createElement("div");
  grid.className = "hr-grid";

  const gapsCard = document.createElement("div");
  gapsCard.className = "card";
  gapsCard.innerHTML = `<h3>Чаще всего проседающие навыки</h3>`;
  for (const g of d.top_skill_gaps) {
    const row = document.createElement("div");
    row.className = "gap-row";
    row.innerHTML = `<span>${escapeHtml(g.name)}</span><span>${g.employees_with_gap} чел. · ср. разрыв ${g.avg_gap}</span>`;
    gapsCard.appendChild(row);
  }
  grid.appendChild(gapsCard);

  const flagsCard = document.createElement("div");
  flagsCard.className = "card";
  flagsCard.innerHTML = `<h3>Выпадают из развития (${d.employees_without_recommendation.length})</h3>`;
  for (const f of d.employees_without_recommendation) {
    const row = document.createElement("div");
    row.className = "flag-row";
    row.innerHTML = `<div>${escapeHtml(f.full_name)} — ${escapeHtml(f.role)} ${f.grade}</div><div class="flag-reason">${escapeHtml(f.reason)}</div>`;
    flagsCard.appendChild(row);
  }
  if (d.employees_without_recommendation.length === 0) {
    flagsCard.innerHTML += `<div class="empty-state">Все сотрудники с разрывами имеют доступные рекомендации</div>`;
  }
  grid.appendChild(flagsCard);

  const stalledCard = document.createElement("div");
  stalledCard.className = "card";
  stalledCard.innerHTML = `<h3>Застряли в развитии (${d.stalled_employees.length})</h3>`;
  for (const f of d.stalled_employees) {
    const row = document.createElement("div");
    row.className = "flag-row";
    row.innerHTML = `<div>${escapeHtml(f.full_name)} — ${escapeHtml(f.role)} ${f.grade}</div><div class="flag-reason">${escapeHtml(f.reason)}</div>`;
    stalledCard.appendChild(row);
  }
  if (d.stalled_employees.length === 0) {
    stalledCard.innerHTML += `<div class="empty-state">Нет сотрудников без прогресса дольше 6 месяцев</div>`;
  }
  grid.appendChild(stalledCard);

  container.appendChild(grid);

  const heatmapCard = document.createElement("div");
  heatmapCard.className = "card";
  heatmapCard.innerHTML = `<h3>Тепловая карта разрывов по отделам</h3>`;
  if (d.department_gaps.length > 0) {
    const skillNames = d.department_gaps[0].cells.map((c) => c.name);
    const heatTable = document.createElement("table");
    heatTable.className = "heatmap-table";
    heatTable.innerHTML = `
      <thead><tr><th>Отдел</th>${skillNames.map((n) => `<th>${escapeHtml(n)}</th>`).join("")}</tr></thead>
      <tbody>
        ${d.department_gaps
          .map(
            (dep) => `<tr>
              <td class="dept-name">${escapeHtml(dep.department)}<br/><span class="dept-count">${dep.employee_count} чел.</span></td>
              ${dep.cells
                .map((c) => {
                  const alpha = Math.min(0.9, c.ratio * 1.1).toFixed(2);
                  return `<td class="heat-cell" style="background: rgba(239, 95, 95, ${alpha})" title="${escapeHtml(c.name)}: ${c.employees_with_gap} чел. (${Math.round(c.ratio * 100)}%)">${c.employees_with_gap}</td>`;
                })
                .join("")}
            </tr>`
          )
          .join("")}
      </tbody>
    `;
    heatmapCard.appendChild(heatTable);
  }
  container.appendChild(heatmapCard);

  const partCard = document.createElement("div");
  partCard.className = "card";
  partCard.innerHTML = `<h3>Явка по активностям</h3>`;
  const table = document.createElement("table");
  table.className = "activity-table";
  table.innerHTML = `
    <thead><tr><th>Активность</th><th>Завершили</th><th>Пропустили/бросили</th><th>Всего</th></tr></thead>
    <tbody>
      ${d.event_participation
        .map(
          (p) => `<tr><td>${escapeHtml(p.title)}</td><td class="status-completed">${p.completed}</td><td class="status-no_show">${p.negative}</td><td>${p.total}</td></tr>`
        )
        .join("")}
    </tbody>
  `;
  partCard.appendChild(table);
  container.appendChild(partCard);
}

// ── Upload ───────────────────────────────────────────────────────────

document.getElementById("btn-upload-employees").addEventListener("click", async () => {
  const input = document.getElementById("file-employees");
  const resultEl = document.getElementById("upload-employees-result");
  if (!input.files.length) return;
  const formData = new FormData();
  formData.append("file", input.files[0]);
  try {
    const res = await fetch(`${API}/api/data/employees`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "ошибка");
    resultEl.className = "result-msg ok";
    resultEl.textContent = `Загружено ${data.merged_employees} профилей. Всего в системе: ${data.total_employees}.`;
    hrLoaded = false;
    await loadEmployees();
  } catch (err) {
    resultEl.className = "result-msg err";
    resultEl.textContent = String(err);
  }
});

document.getElementById("btn-upload-activity").addEventListener("click", async () => {
  const input = document.getElementById("file-activity");
  const resultEl = document.getElementById("upload-activity-result");
  if (!input.files.length) return;
  const formData = new FormData();
  formData.append("file", input.files[0]);
  try {
    const res = await fetch(`${API}/api/data/activity`, { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "ошибка");
    resultEl.className = "result-msg ok";
    resultEl.textContent = `Загружено ${data.merged_records} записей. Всего: ${data.total_records}.`;
    hrLoaded = false;
  } catch (err) {
    resultEl.className = "result-msg err";
    resultEl.textContent = String(err);
  }
});

// ── Toast ────────────────────────────────────────────────────────────

function flashUpdated() {
  let toast = document.getElementById("update-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "update-toast";
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = "✓ Прогресс обновлён";
  toast.classList.add("visible");
  clearTimeout(toast._hideTimer);
  toast._hideTimer = setTimeout(() => toast.classList.remove("visible"), 2200);
}

// ── Utils ────────────────────────────────────────────────────────────

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ── Init ─────────────────────────────────────────────────────────────

loadEmployees();
