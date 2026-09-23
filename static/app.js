const API = "";

let allEmployees = [];
let activeEmployeeId = null;
let showAILogic = false;
let profileRequestId = 0;
let profileChat = null;
let hrChat = null;

// ── Navigation and appearance ───────────────────────────────────────

const sections = {
  profile: { short: "Навигатор", eyebrow: "МОЯ ТРАЕКТОРИЯ", title: "Карьера в фокусе", description: "Профиль, карьерная цель и следующий шаг — в одном месте." },
  hr: { short: "HR-аналитика", eyebrow: "ПАНЕЛЬ ДЛЯ HR", title: "Развитие команды", description: "Где есть разрывы в навыках и кому нужна помощь с выбором шага." },
  upload: { short: "Загрузка данных", eyebrow: "ДАННЫЕ ДЛЯ ПРОВЕРКИ", title: "Загрузка данных", description: "Добавьте проверочные профили и историю активностей." },
};

function closeSidebar(restoreFocus = false) {
  document.body.classList.remove("sidebar-open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "false");
  if (restoreFocus) document.getElementById("menu-toggle").focus();
}

function navigateTo(tab, pushHistory = true) {
  if (!sections[tab]) return;
  document.querySelectorAll(".tab-btn").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${tab}`));
  document.getElementById("topbar-section").textContent = sections[tab].short;
  document.getElementById("page-eyebrow").textContent = sections[tab].eyebrow;
  document.getElementById("page-title").textContent = sections[tab].title;
  document.getElementById("page-description").textContent = sections[tab].description;
  if (pushHistory && location.hash !== `#${tab}`) history.pushState(null, "", `#${tab}`);
  closeSidebar();
  if (tab === "hr") loadHR();
  syncAgentChatDock(tab);
  window.scrollTo({ top: 0, behavior: "auto" });
}

document.querySelectorAll(".tab-btn").forEach((button) => button.addEventListener("click", () => navigateTo(button.dataset.tab)));
window.addEventListener("popstate", () => navigateTo(location.hash.slice(1) || "profile", false));
window.addEventListener("hashchange", () => navigateTo(location.hash.slice(1) || "profile", false));
document.getElementById("menu-toggle").addEventListener("click", () => {
  document.body.classList.add("sidebar-open");
  document.getElementById("menu-toggle").setAttribute("aria-expanded", "true");
  document.getElementById("sidebar-close").focus();
});
document.getElementById("sidebar-close").addEventListener("click", () => closeSidebar(true));
document.getElementById("sidebar-backdrop").addEventListener("click", () => closeSidebar(true));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) closeSidebar(true);
});

const themeButton = document.getElementById("theme-toggle");
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const dark = theme === "dark";
  themeButton.querySelector(".theme-label").textContent = dark ? "Светлая тема" : "Тёмная тема";
  themeButton.setAttribute("aria-label", dark ? "Включить светлую тему" : "Включить тёмную тему");
  document.querySelector('meta[name="theme-color"]').setAttribute("content", dark ? "#111827" : "#f5f7fb");
  try { localStorage.setItem("careerQuestTheme", theme); } catch { /* storage may be disabled */ }
}
let savedTheme = "light";
try { savedTheme = localStorage.getItem("careerQuestTheme") === "dark" ? "dark" : "light"; } catch { /* use light */ }
setTheme(savedTheme);
themeButton.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

const employeePicker = document.getElementById("employee-picker");
const employeePickerButton = document.getElementById("employee-picker-toggle");
function closeEmployeePicker() {
  employeePicker.classList.remove("is-open");
  employeePickerButton.setAttribute("aria-expanded", "false");
}
employeePickerButton.addEventListener("click", () => {
  const open = employeePicker.classList.toggle("is-open");
  employeePickerButton.setAttribute("aria-expanded", String(open));
});

// ── Employee list ────────────────────────────────────────────────────

async function loadEmployees() {
  const res = await fetch(`${API}/api/employees`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  allEmployees = await res.json();
  document.getElementById("employee-count").textContent = allEmployees.length;
  renderEmployeeList(filterEmployees());
  if (!activeEmployeeId && allEmployees.length) selectEmployee(allEmployees[0].employee_id);
}

function renderEmployeeList(list) {
  const container = document.getElementById("employee-list");
  container.innerHTML = "";
  if (!list.length) {
    container.innerHTML = `<div class="people-empty">Сотрудники не найдены</div>`;
    return;
  }
  for (const e of list) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "employee-row" + (e.employee_id === activeEmployeeId ? " active" : "");
    row.setAttribute("aria-pressed", String(e.employee_id === activeEmployeeId));
    row.innerHTML = `
      <span class="row-avatar" aria-hidden="true">${escapeHtml(e.full_name.trim().slice(0, 1).toUpperCase())}</span>
      <span class="row-copy"><span class="row-name">${escapeHtml(e.full_name)}</span><span class="row-meta">${escapeHtml(e.employee_id)} · ${escapeHtml(e.role)} · ${escapeHtml(e.grade)}</span></span>
      <span class="row-arrow" aria-hidden="true">→</span>
    `;
    row.addEventListener("click", () => selectEmployee(e.employee_id));
    container.appendChild(row);
  }
}

function filterEmployees() {
  const query = document.getElementById("employee-search").value.trim().toLowerCase();
  if (!query) return allEmployees;
  return allEmployees.filter((e) => [e.employee_id, e.full_name, e.role, e.department]
    .some((value) => String(value).toLowerCase().includes(query)));
}
document.getElementById("employee-search").addEventListener("input", () => renderEmployeeList(filterEmployees()));

// ── Profile ──────────────────────────────────────────────────────────

async function selectEmployee(employeeId) {
  const requestId = ++profileRequestId;
  if (activeEmployeeId !== employeeId || !profileChat) {
    profileChat?.dispose();
    const employeeName = allEmployees.find((e) => e.employee_id === employeeId)?.full_name || employeeId;
    profileChat = createAgentChat("profile", employeeId, employeeName);
  }
  activeEmployeeId = employeeId;
  if (document.getElementById("tab-profile").classList.contains("active")) syncAgentChatDock("profile");
  document.getElementById("selected-employee-label").textContent = allEmployees.find((e) => e.employee_id === employeeId)?.full_name || employeeId;
  renderEmployeeList(filterEmployees());
  closeEmployeePicker();
  const main = document.getElementById("profile-main");
  main.innerHTML = `
    <div class="profile-loading" role="status" aria-live="polite">
      <span class="loading-shimmer loading-title" aria-hidden="true"></span>
      <span class="loading-shimmer loading-line" aria-hidden="true"></span>
      <span class="loading-shimmer loading-line short" aria-hidden="true"></span>
      <span class="loading-caption">Считаем траекторию и рекомендации...</span>
    </div>`;
  try {
    const res = await fetch(`${API}/api/employees/${employeeId}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const profile = await res.json();
    if (requestId !== profileRequestId) return;
    renderProfile(profile);
  } catch (err) {
    if (requestId !== profileRequestId) return;
    main.innerHTML = `<div class="empty-state">Ошибка загрузки профиля: ${escapeHtml(String(err))}</div>`;
  }
}

function renderProfile(p) {
  const main = document.getElementById("profile-main");
  main.innerHTML = "";

  const header = document.createElement("div");
  header.className = "card profile-header profile-card-enter";
  header.innerHTML = `
    <div class="profile-identity">
      <span class="section-kicker">ЛИЧНЫЙ ПРОФИЛЬ</span>
      <div class="name">${escapeHtml(p.full_name)}</div>
      <div class="meta">${escapeHtml(p.role)} · ${escapeHtml(p.department)}</div>
      <div class="profile-badges">
        <span class="badge">${escapeHtml(p.employee_id)}</span>
        <span class="badge">Стаж ${escapeHtml(p.tenure_months)} мес.</span>
        <span class="badge">${escapeHtml(p.work_format)}</span>
      </div>
    </div>
    <div class="profile-target">
      <span class="section-kicker">ТЕКУЩИЙ УРОВЕНЬ</span>
      <strong>${escapeHtml(p.grade)}</strong>
      <span>${p.career_goal ? `Цель: ${escapeHtml(p.career_goal.target_role)} · ${escapeHtml(p.career_goal.target_grade)}` : "Выберите следующий шаг развития"}</span>
    </div>
  `;
  main.appendChild(header);

  const trajCard = document.createElement("div");
  trajCard.className = "card profile-card-enter";
  trajCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">КАРЬЕРНЫЙ ПУТЬ</span><h2>Траектория развития</h2></div><span class="section-caption">Навыки и требования грейда</span></div>`;
  for (const t of p.trajectories) {
    const block = document.createElement("div");
    block.className = "trajectory-block";
    const label = t.label === "career_goal" ? "Карьерная цель" : "Следующий грейд";
    const readiness = Number.isFinite(Number(t.readiness_pct))
      ? Math.min(100, Math.max(0, Number(t.readiness_pct))) : 0;
    const ringLevel = readiness < 30 ? "low" : readiness <= 70 ? "mid" : "high";
    const ringDash = Math.round(2 * Math.PI * 35.5 * readiness / 100);
    block.innerHTML = `
      <div class="trajectory-title">
        <div class="trajectory-heading"><span>${label}</span><strong>${escapeHtml(t.role)} · ${escapeHtml(t.grade)}</strong></div>
        <div class="readiness-ring level-${ringLevel}" role="img" aria-label="Готовность: ${readiness}%">
          <svg viewBox="0 0 84 84" aria-hidden="true" focusable="false">
            <circle class="ring-track" cx="42" cy="42" r="35.5"></circle>
            <circle class="ring-progress" cx="42" cy="42" r="35.5"></circle>
          </svg>
          <span class="readiness-value" aria-hidden="true">${readiness}%</span>
        </div>
      </div>
      ${renderPromotionEstimate(t.promotion_estimate)}
    `;
    const skillsWrap = document.createElement("div");
    const fillsToAnimate = [];
    const ring = block.querySelector(".ring-progress");
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
      ring.style.strokeDasharray = `${ringDash} 223`;
    });
  }
  main.appendChild(trajCard);

  const recCard = document.createElement("div");
  recCard.className = "card profile-card-enter";
  recCard.innerHTML = `
    <div class="section-heading rec-card-header">
      <div><span class="section-kicker">ПЕРСОНАЛЬНЫЙ ПЛАН</span><h2>Следующие шаги</h2></div>
      <button class="btn btn-outline logic-toggle" id="ai-logic-toggle" type="button" aria-pressed="${showAILogic}">${showAILogic ? "Скрыть расчёт" : "Показать расчёт"}</button>
    </div>
  `;
  recCard.querySelector("#ai-logic-toggle").addEventListener("click", (ev) => {
    showAILogic = !showAILogic;
    ev.currentTarget.setAttribute("aria-pressed", String(showAILogic));
    ev.currentTarget.textContent = showAILogic ? "Скрыть расчёт" : "Показать расчёт";
    document.body.classList.toggle("show-ai-logic", showAILogic);
  });
  if (p.recommendations.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Нет активных рекомендаций — либо всё выполнено, либо нет подходящих активностей.";
    recCard.appendChild(empty);
  }
  for (const r of p.recommendations) {
    const card = document.createElement("div");
    const score = Number(r.score);
    const scoreLevel = score >= 2.5 ? "high" : score >= 1.5 ? "mid" : "low";
    card.className = `rec-card rec-score-${scoreLevel} rec-card-enter`;
    card.dataset.eventId = r.event_id;
    const sessions = r.upcoming_sessions.length ? r.upcoming_sessions.join(", ") : "доступно в любое время";
    const b = r.score_breakdown;
    card.innerHTML = `
      <div class="rec-topline"><span class="rec-label">РЕКОМЕНДОВАННЫЙ ШАГ</span><span class="rec-score">Оценка ${escapeHtml(r.score)}</span></div>
      <div class="rec-title"><span class="sr-only">Оценка рекомендации: ${scoreLevel === "high" ? "высокая" : scoreLevel === "mid" ? "средняя" : "низкая"}. </span>${escapeHtml(r.title)}</div>
      <div class="rec-meta">${escapeHtml(r.type)} · ${escapeHtml(r.format)} · ${escapeHtml(r.duration_hours)} ч · ${escapeHtml(sessions)}</div>
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
      <button class="btn complete-btn" type="button">✓ Завершить активность</button>
    `;
    card.querySelector("button").addEventListener("click", (ev) => completeActivity(p.employee_id, r.event_id, ev.target));
    recCard.appendChild(card);
  }
  main.appendChild(recCard);
  if (document.getElementById("tab-profile").classList.contains("active")) syncAgentChatDock("profile");

  const histCard = document.createElement("div");
  histCard.className = "card profile-card-enter";
  histCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">ЧТО УЖЕ СДЕЛАНО</span><h2>История активностей</h2></div></div>`;
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
  const tableScroll = document.createElement("div");
  tableScroll.className = "table-scroll";
  tableScroll.appendChild(table);
  histCard.appendChild(tableScroll);
  main.appendChild(histCard);

  document.body.classList.toggle("show-ai-logic", showAILogic);
}

function formatPromotionTime(months) {
  if (months == null || !Number.isFinite(months)) return "недостаточно данных для оценки темпа";
  if (months < 1) return "меньше месяца";
  if (months <= 24) return `${Math.round(months)} мес.`;
  const years = Math.round(months / 12);
  const lastTwo = years % 100;
  const last = years % 10;
  const unit = lastTwo >= 11 && lastTwo <= 14 ? "лет" : last === 1 ? "год" : last >= 2 && last <= 4 ? "года" : "лет";
  return `${years} ${unit} (это долгий путь)`;
}

function renderPromotionEstimate(estimate) {
  if (!estimate) return "";
  const complete = estimate.skills_remaining === 0;
  return `
    <div class="promotion-estimate">
      <div class="promotion-label">До повышения</div>
      ${complete
        ? `<div class="promotion-complete">✓ Все требования выполнены</div>`
        : `<div class="promotion-summary">Осталось ${escapeHtml(estimate.skills_remaining)} навыков (из них ${escapeHtml(estimate.critical_skills_remaining)} критичных)</div>
           <div class="promotion-time">${formatPromotionTime(estimate.estimated_months)}</div>`}
      <div class="promotion-basis">${escapeHtml(estimate.basis)}</div>
    </div>
  `;
}

async function completeActivity(employeeId, eventId, buttonEl) {
  if (!buttonEl || buttonEl.disabled) return;
  const requestId = profileRequestId;
  const card = buttonEl.closest(".rec-card");
  buttonEl.disabled = true;
  buttonEl.classList.add("complete-flash");
  await new Promise((resolve) => setTimeout(resolve, 200));
  if (requestId !== profileRequestId) return;
  buttonEl.classList.remove("complete-flash");
  buttonEl.textContent = "Обновляем...";
  card?.classList.add("rec-card-completing");
  try {
    const res = await fetch(`${API}/api/employees/${employeeId}/complete`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_id: eventId }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const profile = await res.json();
    if (requestId !== profileRequestId || activeEmployeeId !== employeeId) return;
    const disappears = !profile.recommendations.some((r) => r.event_id === eventId);
    if (disappears && card?.isConnected && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      card.classList.remove("rec-card-completing", "rec-card-enter");
      // Let the entry animation release its transform before the exit transition starts.
      card.getBoundingClientRect();
      card.classList.add("rec-card-leaving");
      await new Promise((resolve) => setTimeout(resolve, 260));
    }
    if (requestId !== profileRequestId || activeEmployeeId !== employeeId) return;
    renderProfile(profile);
    flashUpdated();
  } catch (err) {
    if (requestId !== profileRequestId || activeEmployeeId !== employeeId) return;
    card?.classList.remove("rec-card-completing", "rec-card-leaving");
    buttonEl.textContent = "✓ Завершить активность";
    buttonEl.disabled = false;
    let errorEl = card?.querySelector(".completion-error");
    if (!errorEl && card) {
      errorEl = document.createElement("div");
      errorEl.className = "completion-error";
      errorEl.setAttribute("role", "alert");
      card.appendChild(errorEl);
    }
    if (errorEl) errorEl.textContent = "Не удалось обновить прогресс. Попробуйте ещё раз.";
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

  const metrics = document.createElement("div");
  metrics.className = "metrics-grid";
  metrics.innerHTML = `
    <div class="metric-card"><span>Сотрудников</span><strong>${escapeHtml(d.total_employees)}</strong><small>В базе данных</small></div>
    <div class="metric-card"><span>Навыков с разрывами</span><strong>${escapeHtml(d.top_skill_gaps.length)}</strong><small>В фокусе HR</small></div>
    <div class="metric-card"><span>Без следующего шага</span><strong>${escapeHtml(d.employees_without_recommendation.length)}</strong><small>Нужна помощь</small></div>
    <div class="metric-card"><span>Без прогресса</span><strong>${escapeHtml(d.stalled_employees.length)}</strong><small>Больше 6 месяцев</small></div>`;
  container.appendChild(metrics);

  const grid = document.createElement("div");
  grid.className = "hr-grid";

  const gapsCard = document.createElement("div");
  gapsCard.className = "card";
  gapsCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">КОМПЕТЕНЦИИ</span><h2>Частые разрывы</h2></div></div>`;
  for (const g of d.top_skill_gaps) {
    const row = document.createElement("div");
    row.className = "gap-row";
    row.innerHTML = `<span>${escapeHtml(g.name)}</span><span>${g.employees_with_gap} чел. · ср. разрыв ${g.avg_gap}</span>`;
    gapsCard.appendChild(row);
  }
  grid.appendChild(gapsCard);

  const flagsCard = document.createElement("div");
  flagsCard.className = "card";
  flagsCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">ТРЕБУЕТ ВНИМАНИЯ</span><h2>Без следующего шага</h2></div><span class="count-badge">${d.employees_without_recommendation.length}</span></div>`;
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
  stalledCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">СИГНАЛЫ</span><h2>Нет недавнего прогресса</h2></div><span class="count-badge">${d.stalled_employees.length}</span></div>`;
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
  heatmapCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">ОТДЕЛЫ</span><h2>Карта разрывов</h2></div></div>`;
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
                  return `<td class="heat-cell" style="background: rgba(196, 77, 77, ${alpha}); color: ${Number(alpha) >= 0.55 ? "#fff" : "var(--text)"}" title="${escapeHtml(c.name)}: ${c.employees_with_gap} чел. (${Math.round(c.ratio * 100)}%)">${c.employees_with_gap}</td>`;
                })
                .join("")}
            </tr>`
          )
          .join("")}
      </tbody>
    `;
    const scroll = document.createElement("div");
    scroll.className = "table-scroll";
    scroll.appendChild(heatTable);
    heatmapCard.appendChild(scroll);
  }
  container.appendChild(heatmapCard);

  const partCard = document.createElement("div");
  partCard.className = "card";
  partCard.innerHTML = `<div class="section-heading"><div><span class="section-kicker">УЧАСТИЕ</span><h2>Явка по активностям</h2></div></div>`;
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
  const partScroll = document.createElement("div");
  partScroll.className = "table-scroll";
  partScroll.appendChild(table);
  partCard.appendChild(partScroll);
  container.appendChild(partCard);
}

// ── AI career agent ──────────────────────────────────────────────────

function createAgentChat(mode, employeeId, employeeName = "") {
  const isHR = mode === "hr";
  const prefix = `agent-${mode}`;
  const context = isHR ? "HR-режим · вся компания" : `Для сотрудника: ${employeeName}`;
  // Only user/assistant text goes back to the API; tool traces stay in the UI.
  const messages = [];
  let pending = false;
  let disposed = false;
  let controller = null;
  let failedTurn = null;

  const element = document.createElement("section");
  element.id = `${prefix}-chat`;
  element.className = "card agent-chat";
  element.setAttribute("aria-labelledby", `${prefix}-title`);
  element.innerHTML = `
    <button class="agent-chat-toggle" type="button" aria-expanded="false" aria-controls="${prefix}-body">
      <span class="agent-chat-icon" aria-hidden="true">AI</span>
      <span class="agent-chat-heading"><span id="${prefix}-title" class="agent-chat-title">Спросить AI-ассистента</span><span class="agent-chat-context"></span></span>
      <span class="agent-chat-chevron" aria-hidden="true">⌄</span>
    </button>
    <div class="agent-chat-body" id="${prefix}-body" hidden>
      <div class="agent-chat-log" role="log" aria-label="${isHR ? "Диалог с HR-ассистентом" : "Диалог с карьерным ассистентом"}" aria-live="polite" aria-relevant="additions text" tabindex="0">
        <p class="agent-chat-empty">${isHR ? "Обсудите развитие команды: найдите менторов или оцените эффект обучения для отдела." : "Обсудите разрывы в навыках, рекомендации и поиск ментора для следующего карьерного шага."}</p>
      </div>
      <div class="agent-chat-typing" role="status" hidden><span class="agent-typing-dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Ассистент готовит ответ…</span></div>
      <div class="agent-chat-error" role="alert" hidden></div>
      <form class="agent-chat-form">
        <label class="sr-only" for="${prefix}-input">${isHR ? "Вопрос HR-ассистенту" : "Вопрос карьерному ассистенту"}</label>
        <div class="agent-chat-compose">
          <textarea id="${prefix}-input" class="agent-chat-input" rows="2" placeholder="Задайте вопрос…" aria-describedby="${prefix}-hint" enterkeyhint="send"></textarea>
          <button class="btn agent-chat-send" type="submit" disabled>Отправить <span aria-hidden="true">↑</span></button>
        </div>
        <p class="agent-chat-hint" id="${prefix}-hint">Enter — отправить · Shift+Enter — новая строка</p>
      </form>
      ${isHR ? `<div class="agent-chat-examples"><span>Попробуйте спросить</span><button class="btn btn-outline agent-chat-example" type="button">Что если назначить System Design Fundamentals всему Backend-отделу?</button><button class="btn btn-outline agent-chat-example" type="button">Кто может быть ментором по SQL в Data &amp; Analytics?</button></div>` : ""}
    </div>
  `;

  const toggle = element.querySelector(".agent-chat-toggle");
  const body = element.querySelector(".agent-chat-body");
  const contextLabel = element.querySelector(".agent-chat-context");
  const log = element.querySelector(".agent-chat-log");
  const empty = element.querySelector(".agent-chat-empty");
  const typing = element.querySelector(".agent-chat-typing");
  const error = element.querySelector(".agent-chat-error");
  const form = element.querySelector(".agent-chat-form");
  const input = element.querySelector(".agent-chat-input");
  const send = element.querySelector(".agent-chat-send");
  const examples = element.querySelectorAll(".agent-chat-example");
  contextLabel.textContent = context;

  function scrollToLatest() {
    requestAnimationFrame(() => {
      if (!disposed && !body.hidden) log.scrollTop = log.scrollHeight;
    });
  }

  function updateControls() {
    input.readOnly = pending;
    send.disabled = pending || !input.value.trim();
    send.textContent = pending ? "Ждём ответ…" : failedTurn ? "Повторить" : "Отправить ↑";
    typing.hidden = !pending;
    form.setAttribute("aria-busy", String(pending));
    contextLabel.textContent = pending ? "Ассистент готовит ответ…" : context;
    examples.forEach((button) => { button.disabled = pending; });
  }

  function appendMessage(role, content, trace = []) {
    empty.remove();
    const message = document.createElement("div");
    message.className = `agent-message agent-message-${role}`;
    const author = document.createElement("span");
    author.className = "agent-message-author";
    author.textContent = role === "user" ? "Вы" : "AI-ассистент";
    const text = document.createElement("div");
    text.className = "agent-message-text";
    // Treat model replies and tool data as text, never as executable markup.
    text.textContent = content;
    message.append(author, text);
    if (trace.length) message.appendChild(renderAgentToolTrace(trace));
    log.appendChild(message);
    scrollToLatest();
    return text;
  }

  toggle.addEventListener("click", () => {
    const open = body.hidden;
    body.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      input.focus({ preventScroll: true });
      scrollToLatest();
    }
  });
  input.addEventListener("input", updateControls);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      if (!pending) form.requestSubmit();
    }
  });
  examples.forEach((button) => button.addEventListener("click", () => {
    input.value = button.textContent;
    updateControls();
    input.focus({ preventScroll: true });
  }));

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const content = input.value.trim();
    if (!content || pending || disposed) return;

    // A failed turn can be retried or edited without duplicating it in history.
    let turn = failedTurn;
    if (turn) {
      turn.message.content = content;
      turn.text.textContent = content;
    } else {
      const message = { role: "user", content };
      messages.push(message);
      turn = { message, text: appendMessage("user", content) };
    }
    failedTurn = null;
    pending = true;
    input.value = "";
    error.hidden = true;
    error.textContent = "";
    updateControls();
    scrollToLatest();
    controller = new AbortController();
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 180000);

    try {
      const res = await fetch(`${API}/api/agent/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages, employee_id: employeeId }),
        signal: controller.signal,
      });
      const data = await res.json().catch(() => null);
      if (disposed) return;
      if (!res.ok) throw new Error(`Ассистент сейчас недоступен (HTTP ${res.status}).`);
      if (typeof data?.reply !== "string" || !data.reply.trim()) {
        throw new Error("Не удалось прочитать ответ ассистента.");
      }
      messages.push({ role: "assistant", content: data.reply });
      appendMessage("assistant", data.reply, Array.isArray(data.tool_trace) ? data.tool_trace : []);
    } catch (err) {
      if (disposed) return;
      failedTurn = turn;
      input.value = content;
      error.textContent = `${timedOut ? "Ассистент не ответил вовремя." : err instanceof TypeError ? "Не удалось связаться с ассистентом. Проверьте соединение." : err.message || "Не удалось получить ответ."} Повторите отправку или измените вопрос.`;
      error.hidden = false;
    } finally {
      clearTimeout(timeout);
      controller = null;
      if (!disposed) {
        pending = false;
        updateControls();
      }
    }
  });

  return {
    element,
    employeeId,
    dispose() {
      disposed = true;
      controller?.abort();
      messages.length = 0;
      element.remove();
    },
  };
}

function renderAgentToolTrace(trace) {
  const details = document.createElement("details");
  details.className = "agent-tool-trace";
  const summary = document.createElement("summary");
  summary.textContent = `🔧 Использованы инструменты: ${trace.map((call) => `${call.tool}(…)`).join(", ")}`;
  details.appendChild(summary);
  for (const call of trace) {
    const item = document.createElement("div");
    item.className = "agent-tool-call";
    const name = document.createElement("strong");
    name.textContent = call.tool;
    item.appendChild(name);
    for (const [label, value] of [["Аргументы", call.arguments], ["Результат", call.result]]) {
      const caption = document.createElement("span");
      caption.className = "agent-tool-caption";
      caption.textContent = label;
      const data = document.createElement("pre");
      data.className = "agent-tool-data";
      data.textContent = JSON.stringify(value ?? {}, null, 2);
      item.append(caption, data);
    }
    details.appendChild(item);
  }
  return details;
}

function syncAgentChatDock(tab) {
  const dock = document.getElementById("agent-chat-dock");
  if (!dock) return;
  const chat = tab === "profile" ? profileChat : tab === "hr" ? hrChat : null;
  [profileChat, hrChat].filter(Boolean).forEach((item) => {
    if (item.element.parentElement !== dock) dock.appendChild(item.element);
    item.element.hidden = item !== chat;
  });
  if (!chat) {
    dock.hidden = true;
    return;
  }
  dock.hidden = false;
}

// ── Upload ───────────────────────────────────────────────────────────

setupUpload("employees", ".json", (data) =>
  `Загружено ${data.merged_employees} профилей. Всего в системе: ${data.total_employees}.`
);
setupUpload("activity", ".csv", (data) =>
  `Загружено ${data.merged_records} записей. Всего: ${data.total_records}.`
);

function isFileDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes("Files");
}

// A missed drop should not navigate away from the upload screen.
["dragover", "drop"].forEach((eventName) => {
  document.addEventListener(eventName, (event) => {
    if (document.getElementById("tab-upload").classList.contains("active") && isFileDrag(event)) {
      event.preventDefault();
    }
  });
});

function setupUpload(kind, extension, successMessage) {
  const input = document.getElementById(`file-${kind}`);
  const zone = document.getElementById(`dropzone-${kind}`);
  const button = document.getElementById(`btn-upload-${kind}`);
  const filenameEl = document.getElementById(`upload-${kind}-filename`);
  const resultEl = document.getElementById(`upload-${kind}-result`);
  const icon = zone.querySelector(".upload-icon");
  const idleButtonHtml = button.innerHTML;
  let selectedFile = null;
  let uploading = false;
  let dragDepth = 0;

  function resetDrag() {
    dragDepth = 0;
    zone.classList.remove("is-dragover");
  }

  function selectFile(files) {
    if (uploading || !files.length) return;
    // Keep the File independently so dropped files and picker files use the same request.
    // Clearing the native input also lets the same file be selected again after an error.
    input.value = "";
    selectedFile = null;
    button.disabled = true;
    zone.classList.remove("is-success");
    icon.textContent = "↥";
    filenameEl.textContent = "Файл не выбран";
    resultEl.className = "result-msg";
    resultEl.textContent = "";

    if (files.length !== 1 || !files[0].name.toLowerCase().endsWith(extension)) {
      resultEl.className = "result-msg err";
      resultEl.textContent = `Выберите один файл ${extension.toUpperCase()}.`;
      return;
    }
    selectedFile = files[0];
    filenameEl.textContent = selectedFile.name;
    button.disabled = false;
  }

  input.addEventListener("change", () => selectFile(Array.from(input.files)));
  zone.addEventListener("dragenter", (event) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    if (!uploading) {
      dragDepth += 1;
      zone.classList.add("is-dragover");
    }
  });
  zone.addEventListener("dragover", (event) => {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = uploading ? "none" : "copy";
    if (!uploading) zone.classList.add("is-dragover");
  });
  zone.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) resetDrag();
  });
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    resetDrag();
    selectFile(Array.from(event.dataTransfer?.files || []));
  });
  document.addEventListener("dragend", resetDrag);

  button.addEventListener("click", async () => {
    if (!selectedFile || uploading) return;
    uploading = true;
    button.disabled = true;
    input.disabled = true;
    button.textContent = "Загружаем...";
    zone.classList.add("is-uploading");
    zone.setAttribute("aria-busy", "true");
    resultEl.className = "result-msg";
    resultEl.textContent = "Отправляем файл...";
    resetDrag();
    const formData = new FormData();
    formData.append("file", selectedFile);
    try {
      const res = await fetch(`${API}/api/data/${kind}`, { method: "POST", body: formData });
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : `Ошибка загрузки (HTTP ${res.status}).`;
        throw new Error(detail);
      }
      if (!data) throw new Error("Сервер вернул некорректный ответ. Проверьте результат загрузки перед повтором.");
      selectedFile = null;
      zone.classList.add("is-success");
      icon.textContent = "✓";
      resultEl.className = "result-msg ok";
      resultEl.textContent = `✓ ${successMessage(data)}`;
      hrLoaded = false;
      flashUpdated("✓ Файл загружен");
      // An unsuccessful list refresh must not report that the upload itself failed.
      if (kind === "employees") {
        try {
          await loadEmployees();
        } catch {
          resultEl.textContent += " Обновите страницу, чтобы увидеть новые профили.";
        }
      }
    } catch (err) {
      resultEl.className = "result-msg err";
      resultEl.textContent = err instanceof Error ? err.message : String(err);
    } finally {
      uploading = false;
      input.disabled = false;
      button.disabled = !selectedFile;
      button.innerHTML = idleButtonHtml;
      zone.classList.remove("is-uploading");
      zone.removeAttribute("aria-busy");
    }
  });
}

// ── Toast ────────────────────────────────────────────────────────────

function flashUpdated(message = "✓ Прогресс обновлён") {
  let toast = document.getElementById("update-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "update-toast";
    toast.className = "toast";
    document.body.appendChild(toast);
  }
  toast.textContent = message;
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

hrChat = createAgentChat("hr", null);
navigateTo(location.hash.slice(1) in sections ? location.hash.slice(1) : "profile", false);
loadEmployees().catch((err) => {
  const list = document.getElementById("employee-list");
  list.innerHTML = `<div class="people-empty" role="alert">Не удалось загрузить список сотрудников: ${escapeHtml(err.message || String(err))}</div>`;
});
