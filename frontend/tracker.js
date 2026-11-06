/* Zweig Tracker — проекты, доска, список, команда и карточка задачи.
 *
 * Вся разметка страницы строится здесь, в #trk-root. Обработчики не пишутся
 * в разметку строками (onclick="f('…')"): текст задачи с кавычкой или
 * переносом ломал бы такой вызов. Вместо этого у элементов есть data-act, а
 * события ловит один делегирующий обработчик на корне.
 *
 * Снаружи нужны три глобальных имени — их зовёт app.js:
 *   navigateToTasks()      — кнопка «Трекер» в боковой панели и маршрут /tracker
 *   handleTaskWsEvent(d)   — события задач из WebSocket
 *   Tracker.openProject / Tracker.openByKey — маршруты /tracker/… и /t/ЛЕС-24
 */
(function () {
'use strict';

// ── Состояние ─────────────────────────────────────────────────────
const S = {
    projects: [],        // проекты верхнего уровня (список слева)
    project: null,       // открытый проект (с members, statuses, children)
    tasks: [],
    labels: [],
    view: lsGet('trk_view', 'board'),     // board | list | team
    task: null,          // открытая карточка (с subtasks, comments, attachments)
    filter: { q: '', who: '', late: false },
    loaded: false,
};
const ROLE_RU = { owner: 'Владелец', lead: 'Руководитель', member: 'Участник', viewer: 'Наблюдатель' };
const ROLE_HINT = {
    owner: 'Всё, включая удаление проекта и смену ролей',
    lead: 'Ставит задачи любому участнику, настраивает колонки и метки',
    member: 'Создаёт задачи, ведёт свои, комментирует',
    viewer: 'Видит доску и обсуждения, ничего не меняет',
};
const KIND_RU = { new: 'Не начата', work: 'В работе', review: 'На согласовании', done: 'Завершена' };
const PALETTE = ['#7FE0B0', '#E0A458', '#D97B4F', '#5FA3C4', '#9C86C9', '#D07A90', '#8FB8E8', '#C8A2E0', '#D9C79A', '#F07A6A', '#8A958F'];
const TEMPLATES = [
    { id: 'simple', name: 'Простой', cols: [['Нужно сделать', '#E0A458'], ['В работе', '#8FB8E8'], ['Готово', '#7FE0B0']] },
    { id: 'object', name: 'С проверкой', cols: [['Нужно сделать', '#E0A458'], ['В работе', '#8FB8E8'], ['На проверке', '#C8A2E0'], ['Готово', '#7FE0B0']] },
    { id: 'requests', name: 'Заявки', cols: [['Новая', '#F07A6A'], ['В работе', '#8FB8E8'], ['Ждёт ответа', '#E0A458'], ['Решена', '#7FE0B0']] },
];
const WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
const MON = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const ERR_RU = {
    'At least one status must close the task': 'Нужен хотя бы один статус с типом «Завершена»',
    'At least one status must be on the board': 'Хотя бы один статус должен быть на доске',
    'Status name required': 'У каждого статуса должно быть название',
    'Too many statuses': 'Слишком много статусов — не больше 20',
    'Duplicate status': 'Статусы повторяются',
    'Invalid status': 'Такого статуса нет в проекте',
    'Title required': 'Нужно название',
    'Project name required': 'Нужно название проекта',
    'Discussion is off for this task': 'Обсуждение в этой задаче выключено',
    'Not enough permissions': 'Недостаточно прав',
    'Owner role cannot be changed': 'Роль владельца поменять нельзя',
    'Owner cannot be removed': 'Владельца нельзя убрать из проекта',
    'Cannot remove yourself': 'Себя убрать нельзя',
    'User not found': 'Пользователь не найден',
};

function lsGet(k, d) { try { return localStorage.getItem(k) || d; } catch (_) { return d; } }
function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (_) {} }
const h = (s) => esc(s == null ? '' : String(s));
const ha = (s) => escAttr(s == null ? '' : String(s));
const $ = (sel) => document.querySelector(sel);

// ── Сеть ──────────────────────────────────────────────────────────
async function api(method, url, body) {
    const opts = { method };
    if (body !== undefined) opts.body = body;
    const res = await apiFetch('/tasks' + url, opts);
    if (!res) throw new Error('no-response');           // 401/403 — apiFetch уже показал причину
    if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch (_) {}
        const msg = ERR_RU[detail] || (typeof detail === 'string' && detail) || 'Не получилось — попробуйте ещё раз';
        showToast(msg, 'error');
        throw new Error(detail || String(res.status));
    }
    return res.status === 204 ? null : res.json();
}

// ── Даты ──────────────────────────────────────────────────────────
function todayIso() { const d = new Date(); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
function dayDiff(iso) {
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    const a = new Date(y, m - 1, d), n = new Date(); n.setHours(0, 0, 0, 0);
    return Math.round((a - n) / 86400000);
}
function fmtDate(iso) {
    const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
    const dt = new Date(y, m - 1, d);
    return WD[dt.getDay()] + ', ' + d + ' ' + MON[m - 1] + (y !== new Date().getFullYear() ? ' ' + y : '');
}
/** Срок → подпись и тон: late — просрочено, soon — сегодня/завтра. */
function due(task) {
    if (!task.due_date) return null;
    const diff = dayDiff(task.due_date);
    const done = isDone(task);
    if (done) return { text: fmtDate(task.due_date), tone: '' };
    if (diff < 0) return { text: 'просрочено · ' + fmtDate(task.due_date).replace(/^\S+ /, ''), tone: 'late' };
    if (diff === 0) return { text: 'сегодня', tone: 'soon' };
    if (diff === 1) return { text: 'завтра', tone: 'soon' };
    return { text: fmtDate(task.due_date), tone: '' };
}
function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    const diff = dayDiff(todayIso()) - dayDiff(iso.slice(0, 10));
    const hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    if (diff === 0) return 'сегодня, ' + hm;
    if (diff === 1) return 'вчера, ' + hm;
    return d.getDate() + ' ' + MON[d.getMonth()] + ', ' + hm;
}

// ── Проект: роли, статусы, люди ──────────────────────────────────
function myRole() { return (S.project && S.project.my_role) || 'viewer'; }
function isMember() { return !!(S.project && S.project.is_member); }
function canCreate() { return isMember() && ['owner', 'lead', 'member'].includes(myRole()); }
/** «Вся компания»: не участник может оставить заявку в первую колонку. */
function canSubmit() { return !!(S.project && !isMember() && S.project.visibility === 'public'); }
function canManage() { return isMember() && ['owner', 'lead'].includes(myRole()); }
function isOwner() { return isMember() && myRole() === 'owner'; }
function canEditTask(t) { return canManage() || (isMember() && t && (t.created_by === currentUser || t.assignee === currentUser)); }
function statuses() { return (S.project && S.project.statuses) || []; }
function statusOf(key) { return statuses().find(s => s.key === key) || { key, name: key, color: '#8A958F', kind: 'work' }; }
function isDone(t) { return statusOf(t.status).kind === 'done'; }
function members() { return (S.project && S.project.members) || []; }
function personName(username) {
    if (!username) return '';
    const m = members().find(x => x.username === username);
    if (m) return m.display_name || username;
    const u = (typeof allUsers !== 'undefined' ? allUsers : []).find(x => x.username === username);
    return (u && u.display_name) || username;
}
function personAvatar(username) {
    const m = members().find(x => x.username === username);
    if (m && m.avatar_path) return m.avatar_path;
    const u = (typeof allUsers !== 'undefined' ? allUsers : []).find(x => x.username === username);
    return (u && u.avatar_path) || '';
}
function initials(name) {
    const p = String(name || '?').trim().split(/\s+/);
    return ((p[0] || '?')[0] + (p[1] ? p[1][0] : '')).toUpperCase();
}
function hue(str) { let x = 0; for (const c of String(str)) x = (x * 31 + c.codePointAt(0)) >>> 0; return PALETTE[x % 8]; }
function avatar(username, size = 26) {
    if (!username) return `<span class="trk-av trk-av-empty" style="--s:${size}px" title="Без исполнителя"><span class="material-icons-round">person_outline</span></span>`;
    const name = personName(username), img = personAvatar(username);
    if (img) return `<span class="trk-av" style="--s:${size}px" title="${ha(name)}"><img src="${ha(srv(img))}" alt=""></span>`;
    return `<span class="trk-av" style="--s:${size}px;--c:${hue(username)}" title="${ha(name)}">${h(initials(name))}</span>`;
}
function projColor(p) { return (p && p.color) || hue(p ? p.name : ''); }
function mix(color, pct) { return `color-mix(in srgb, ${color} ${pct}%, transparent)`; }

// ── Ссылки на задачи ─────────────────────────────────────────────
function taskLink(key) { return (getServerBase() || location.origin) + '/t/' + encodeURIComponent(key); }
async function copyTaskLink(key) {
    const url = taskLink(key);
    try { await navigator.clipboard.writeText(url); }
    catch (_) {
        const ta = document.createElement('textarea'); ta.value = url; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); } catch (_) {} ta.remove();
    }
    showToast(`Ссылка скопирована — в чате покажется как ${key}`);
}

// ═════════════════════════════════════════════════════════════════
// Навигация
// ═════════════════════════════════════════════════════════════════
async function navigateToTasks() {
    if (typeof closeSettingsMenu === 'function') closeSettingsMenu();
    const sb = document.getElementById('sidebar');
    if (sb && sb.classList.contains('open')) toggleSidebar();
    showPage('tasks');
    await loadProjects();
    const last = lsGet('trk_last', '');
    const target = S.projects.find(p => p.slug === last) || S.projects[0];
    if (target) return openProject(target.slug, null, true);
    pushRoute('/tracker');
    S.project = null; S.tasks = []; S.task = null;
    render();
}

async function loadProjects() {
    try { S.projects = await api('GET', '/projects'); } catch (_) { S.projects = []; }
    S.loaded = true;
}

async function openProject(slug, taskRef, replaceRoute) {
    showPage('tasks');
    if (!S.loaded) await loadProjects();
    let project;
    try { project = await api('GET', `/projects/${encodeURIComponent(slug)}`); }
    catch (_) { S.project = null; render(); return; }
    const changed = !S.project || S.project.id !== project.id;
    S.project = project;
    lsSet('trk_last', project.slug);
    if (changed) { S.filter = { q: '', who: '', late: false }; S.task = null; }
    try {
        const [tasks, labels] = await Promise.all([
            api('GET', `/projects/${encodeURIComponent(project.slug)}/tasks`),
            api('GET', `/projects/${encodeURIComponent(project.slug)}/labels`),
        ]);
        S.tasks = tasks; S.labels = labels;
    } catch (_) { S.tasks = []; S.labels = []; }
    render();
    if (taskRef) {
        const t = S.tasks.find(x => x.id === taskRef || (x.task_key && x.task_key.toUpperCase() === String(taskRef).toUpperCase()));
        if (t) return openTask(t.id, true);
    }
    pushRoute('/tracker/' + encodeURIComponent(project.slug), !!replaceRoute);
}

async function openByKey(key) {
    showPage('tasks');
    let r;
    try { r = (await api('GET', '/keys?keys=' + encodeURIComponent(key)))[0]; } catch (_) { r = null; }
    if (!r || !r.found) { showToast(`Задача ${key} не найдена`, 'error'); return navigateToTasks(); }
    if (!r.access) { showToast(`Задача ${r.key} — в проекте, куда у вас нет доступа`, 'error'); return navigateToTasks(); }
    await openProject(r.project_slug, r.task_id);
}

async function openTask(id, replaceRoute) {
    if (!S.project) return;
    try { S.task = await api('GET', `/projects/${encodeURIComponent(S.project.slug)}/tasks/${id}`); }
    catch (_) { return; }
    if (S.view === 'team') { S.view = 'board'; renderMain(); }
    renderDrawer();
    markActiveCard();
    pushRoute('/tracker/' + encodeURIComponent(S.project.slug) + '/' + encodeURIComponent(S.task.task_key || S.task.id), !!replaceRoute);
}
function closeTask() {
    S.task = null;
    renderDrawer();
    markActiveCard();
    if (S.project) pushRoute('/tracker/' + encodeURIComponent(S.project.slug), true);
}
async function refreshTask() {
    if (!S.task || !S.project) return;
    try { S.task = await api('GET', `/projects/${encodeURIComponent(S.project.slug)}/tasks/${S.task.id}`); }
    catch (_) { S.task = null; }
    renderDrawer();
}
function upsertTask(t) {
    const i = S.tasks.findIndex(x => x.id === t.id);
    if (i >= 0) S.tasks[i] = Object.assign({}, S.tasks[i], t); else S.tasks.push(t);
}

// ═════════════════════════════════════════════════════════════════
// Отрисовка
// ═════════════════════════════════════════════════════════════════
function render() {
    const root = document.getElementById('trk-root');
    if (!root) return;
    root.innerHTML = `<aside class="trk-side" id="trk-side"></aside>
        <section class="trk-main" id="trk-main"></section>
        <aside class="trk-drawer" id="trk-drawer" aria-label="Карточка задачи"></aside>`;
    renderSide(); renderMain(); renderDrawer();
}

function renderSide() {
    const el = document.getElementById('trk-side');
    if (!el) return;
    const cur = S.project;
    const rows = S.projects.map(p => {
        const on = cur && (cur.id === p.id || (cur.breadcrumb || []).some(b => b.id === p.id));
        let kids = '';
        if (on && cur.children && cur.children.length && cur.id === p.id) {
            kids = cur.children.map(c => `<button class="trk-proj trk-proj-sub" data-act="project" data-slug="${ha(c.slug)}">
                <span class="trk-proj-name">${h(c.name)}</span><span class="trk-proj-n">${c.task_count || ''}</span></button>`).join('');
        }
        if (on && cur.id !== p.id) {
            kids = `<button class="trk-proj trk-proj-sub on" data-act="project" data-slug="${ha(cur.slug)}"><span class="trk-proj-name">${h(cur.name)}</span></button>`;
        }
        return `<button class="trk-proj${on && cur.id === p.id ? ' on' : ''}" data-act="project" data-slug="${ha(p.slug)}">
                <span class="trk-dot" style="--c:${projColor(p)}"></span>
                <span class="trk-proj-name">${h(p.name)}</span>
                <span class="trk-proj-n${p.overdue_count ? ' late' : ''}" title="${p.overdue_count ? 'Есть просроченные' : 'Открытых задач'}">${p.open_count || ''}</span>
            </button>${kids}`;
    }).join('');
    el.innerHTML = `
        <div class="trk-side-head">
            <button class="btn-icon topbar-menu-btn" data-act="sidebar" aria-label="Меню"><span class="material-icons-round">menu</span></button>
            <span class="trk-side-title">Трекер</span>
        </div>
        <div class="trk-label">// проекты</div>
        <nav class="trk-proj-list" aria-label="Проекты">${rows || '<div class="trk-side-empty">Пока ни одного проекта</div>'}</nav>
        <button class="trk-new-proj" data-act="new-project"><span class="material-icons-round">add</span>Новый проект</button>`;
}

function filteredTasks() {
    const f = S.filter, q = f.q.trim().toLowerCase();
    return S.tasks.filter(t => {
        if (q && !((t.title || '').toLowerCase().includes(q) || (t.task_key || '').toLowerCase().includes(q) || (t.description || '').toLowerCase().includes(q))) return false;
        if (f.who === '__me' && t.assignee !== currentUser) return false;
        if (f.who === '__none' && t.assignee) return false;
        if (f.who && !f.who.startsWith('__') && t.assignee !== f.who) return false;
        if (f.late) { const d = due(t); if (!d || d.tone !== 'late') return false; }
        return true;
    });
}
function lateCount() { return S.tasks.filter(t => { const d = due(t); return d && d.tone === 'late'; }).length; }

function renderMain() {
    const el = document.getElementById('trk-main');
    if (!el) return;
    if (!S.project) { el.innerHTML = emptyHome(); return; }
    const p = S.project;
    const crumbs = (p.breadcrumb || []).map(b => `<button class="trk-crumb" data-act="project" data-slug="${ha(b.slug)}">${h(b.name)}</button><span class="trk-crumb-sep">/</span>`).join('');
    const team = members().slice(0, 5).map(m => avatar(m.username, 32)).join('') + (members().length > 5 ? `<span class="trk-av trk-av-more" style="--s:32px">+${members().length - 5}</span>` : '');
    const open = S.tasks.filter(t => !isDone(t)).length;
    const tabs = [['board', 'Доска', 'view_kanban'], ['list', 'Список', 'view_list'], ['team', 'Команда', 'group']]
        .map(([id, name, icon]) => `<button role="tab" aria-selected="${S.view === id}" class="trk-tab${S.view === id ? ' on' : ''}" data-act="view" data-view="${id}"><span class="material-icons-round">${icon}</span>${name}</button>`).join('');
    const late = lateCount();
    const whoOpts = [['', 'Все исполнители'], ['__me', 'Мои задачи'], ['__none', 'Без исполнителя']]
        .concat(members().map(m => [m.username, m.display_name || m.username]))
        .map(([v, n]) => `<option value="${ha(v)}"${S.filter.who === v ? ' selected' : ''}>${h(n)}</option>`).join('');
    const filters = S.view === 'team' ? '' : `
        <div class="trk-filters">
            <label class="trk-search"><span class="material-icons-round">search</span>
                <input type="search" placeholder="Поиск по задачам" value="${ha(S.filter.q)}" data-act="filter-q" aria-label="Поиск по задачам"></label>
            <select class="trk-select" data-act="filter-who" aria-label="Исполнитель">${whoOpts}</select>
            ${late ? `<button class="trk-chip-btn late${S.filter.late ? ' on' : ''}" data-act="filter-late" aria-pressed="${S.filter.late}">Просрочено · ${late}</button>` : ''}
            ${canManage() ? `<button class="trk-chip-btn" data-act="settings"><span class="material-icons-round">tune</span>Колонки и статусы</button>` : ''}
        </div>`;
    el.innerHTML = `
        <header class="trk-head">
            <div class="trk-head-l">
                <button class="btn-icon topbar-menu-btn trk-menu-mobile" data-act="sidebar" aria-label="Меню"><span class="material-icons-round">menu</span></button>
                <div>
                    <div class="trk-meta">${crumbs}${p.prefix ? `<span class="trk-mono">${h(p.prefix)}</span> · ` : ''}${open} ${plural(open, 'открытая задача', 'открытые задачи', 'открытых задач')}${p.visibility === 'public' ? ' · видит вся компания' : ''}</div>
                    <h1 class="trk-title"><span class="trk-dot trk-dot-lg" style="--c:${projColor(p)}"></span>${h(p.name)}</h1>
                </div>
            </div>
            <div class="trk-head-r">
                <button class="trk-team" data-act="view" data-view="team" title="Команда проекта">${team}</button>
                ${canManage() ? `<button class="btn-icon trk-gear" data-act="settings" title="Настройки проекта" aria-label="Настройки проекта"><span class="material-icons-round">settings</span></button>` : ''}
                ${canCreate() || canSubmit() ? `<button class="trk-btn trk-btn-primary" data-act="new-task"><span class="material-icons-round">add</span>${canSubmit() ? 'Оставить заявку' : 'Новая задача'}</button>` : ''}
            </div>
        </header>
        <div class="trk-bar">
            <div class="trk-tabs" role="tablist" aria-label="Вид проекта">${tabs}</div>
            ${filters}
        </div>
        <div class="trk-body" id="trk-body">${S.view === 'list' ? listView() : S.view === 'team' ? teamView() : boardView()}</div>`;
    placeDrawer();
}

function plural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
}

function emptyHome() {
    return `<div class="trk-empty">
        <button class="btn-icon topbar-menu-btn trk-menu-mobile" data-act="sidebar" aria-label="Меню"><span class="material-icons-round">menu</span></button>
        <div class="trk-empty-card">
            <div class="trk-label">// zweig tracker</div>
            <h1 class="trk-empty-h">Задачи там,<br>где идёт работа</h1>
            <p>Создайте проект — объект, отдел или поток заявок. Задачи, сроки и файлы будут рядом с перепиской, на сервере вашей компании.</p>
            <button class="trk-btn trk-btn-primary trk-btn-lg" data-act="new-project"><span class="material-icons-round">add</span>Создать проект</button>
        </div>
    </div>`;
}

// ── Доска ─────────────────────────────────────────────────────────
function cardHtml(t) {
    const lab = (t.labels || [])[0];
    const d = due(t);
    const sel = S.task && S.task.id === t.id;
    const draggable = canCreate() ? ' draggable="true"' : '';
    return `<article class="trk-card${d && d.tone === 'late' ? ' late' : ''}${sel ? ' sel' : ''}${isDone(t) ? ' done' : ''}" data-act="task" data-id="${ha(t.id)}"${draggable} tabindex="0">
        ${lab || t.task_key ? `<div class="trk-card-top">${lab ? `<span class="trk-label-chip" style="--c:${ha(lab.color || '#8A958F')}">${h(lab.name)}</span>` : '<span></span>'}<span class="trk-key">${h(t.task_key || '')}</span></div>` : ''}
        <div class="trk-card-title">${h(t.title)}</div>
        <div class="trk-card-meta">
            <span class="trk-card-facts">
                ${d ? `<span class="trk-due ${d.tone}">${h(d.text)}</span>` : ''}
                ${t.subtask_total ? `<span class="trk-fact"><span class="material-icons-round">checklist</span>${t.subtask_done}/${t.subtask_total}</span>` : ''}
                ${t.attachment_count ? `<span class="trk-fact"><span class="material-icons-round">attach_file</span>${t.attachment_count}</span>` : ''}
                ${t.comment_count && t.discussion !== 0 ? `<span class="trk-fact"><span class="material-icons-round">chat_bubble_outline</span>${t.comment_count}</span>` : ''}
            </span>
            ${avatar(t.assignee, 26)}
        </div>
    </article>`;
}

function boardView() {
    const list = filteredTasks();
    const cols = statuses().filter(s => s.on_board);
    const folded = JSON.parse(lsGet('trk_fold_' + S.project.id, '{}'));
    const firstKey = (cols[0] || {}).key;
    const colHtml = cols.map(s => {
        const items = list.filter(t => t.status === s.key).sort((a, b) => (a.position || 0) - (b.position || 0));
        const isFold = folded[s.key] !== undefined ? folded[s.key] : s.kind === 'done';
        if (isFold) {
            return `<button class="trk-col-fold" data-act="fold" data-key="${ha(s.key)}" data-status="${ha(s.key)}" aria-label="${ha(s.name)}: ${items.length} — развернуть">
                <span class="trk-dot" style="--c:${ha(s.color)}"></span><span class="trk-col-fold-name">${h(s.name)}</span><span class="trk-mono">${items.length}</span></button>`;
        }
        return `<div class="trk-col" data-status="${ha(s.key)}">
            <div class="trk-col-head">
                <span class="trk-dot" style="--c:${ha(s.color)}"></span><span class="trk-col-name">${h(s.name)}</span><span class="trk-mono trk-col-n">${items.length}</span>
                <button class="btn-icon trk-col-fold-btn" data-act="fold" data-key="${ha(s.key)}" title="Свернуть колонку" aria-label="Свернуть колонку ${ha(s.name)}"><span class="material-icons-round">unfold_less</span></button>
            </div>
            <div class="trk-col-cards">${items.map(cardHtml).join('') || '<div class="trk-col-empty">Пусто</div>'}</div>
            ${canCreate() || (canSubmit() && s.key === firstKey) ? `<form class="trk-quick" data-act="quick" data-status="${ha(s.key)}">
                <input type="text" placeholder="${canSubmit() ? '+ Оставить заявку' : '+ Добавить задачу'}" aria-label="Новая задача в колонку ${ha(s.name)}" maxlength="300">
            </form>` : ''}
        </div>`;
    }).join('');
    const hidden = statuses().filter(s => !s.on_board).map(s => ({ s, n: list.filter(t => t.status === s.key).length })).filter(x => x.n);
    return `<div class="trk-board-wrap"><div class="trk-board">${colHtml}</div></div>
        ${hidden.length ? `<div class="trk-hidden-note">Не на доске: ${hidden.map(x => `${h(x.s.name)} · ${x.n}`).join(', ')} — <button class="trk-link" data-act="view" data-view="list">показать списком</button></div>` : ''}
        ${!S.tasks.length && canCreate() ? `<div class="trk-hint">Задачу можно создать кнопкой «Новая задача» или прямо в колонке — впишите название и нажмите Enter.</div>` : ''}`;
}

// ── Список ────────────────────────────────────────────────────────
function listView() {
    const list = filteredTasks();
    const now = Date.now();
    const doneWeek = S.tasks.filter(t => isDone(t) && t.updated_at && now - new Date(t.updated_at).getTime() < 7 * 86400000).length;
    const week = S.tasks.filter(t => { if (!t.due_date || isDone(t)) return false; const x = dayDiff(t.due_date); return x >= 0 && x <= 6; }).length;
    const stats = [
        ['Открыто', S.tasks.filter(t => !isDone(t)).length, '', 'по проекту'],
        ['Просрочено', lateCount(), 'late', 'срок уже прошёл'],
        ['Срок в ближайшие 7 дней', week, 'soon', 'не закрыты'],
        ['Закрыто за 7 дней', doneWeek, 'ok', 'перешли в «Завершена»'],
    ].map(([l, v, tone, hint]) => `<div class="trk-stat"><div class="trk-stat-l">${l}</div><div class="trk-stat-v ${tone}">${v}</div><div class="trk-stat-h">${hint}</div></div>`).join('');
    const groups = statuses().map(s => {
        const items = list.filter(t => t.status === s.key);
        if (!items.length) return '';
        return `<div class="trk-group">
            <div class="trk-group-head"><span class="trk-dot" style="--c:${ha(s.color)}"></span>${h(s.name)}<span class="trk-mono">${items.length}</span>${s.on_board ? '' : '<span class="trk-tag">не на доске</span>'}</div>
            ${items.map(t => {
                const d = due(t), lab = (t.labels || [])[0], done = s.kind === 'done';
                return `<div class="trk-row${d && d.tone === 'late' ? ' late' : ''}${done ? ' done' : ''}" data-act="task" data-id="${ha(t.id)}" tabindex="0" role="button">
                    <span class="trk-ring${done ? ' on' : ''}"></span>
                    <span class="trk-mono trk-row-key">${h(t.task_key || '')}</span>
                    <span class="trk-row-title">${h(t.title)}</span>
                    <span class="trk-row-who">${avatar(t.assignee, 24)}<span>${h(t.assignee ? personName(t.assignee) : '—')}</span></span>
                    <span class="trk-row-due ${d ? d.tone : ''}">${d ? h(d.text) : '—'}</span>
                    <span class="trk-row-lab">${lab ? `<span class="trk-label-chip" style="--c:${ha(lab.color || '#8A958F')}">${h(lab.name)}</span>` : ''}</span>
                </div>`;
            }).join('')}
        </div>`;
    }).join('');
    const load = {};
    S.tasks.filter(t => !isDone(t) && t.assignee).forEach(t => { load[t.assignee] = (load[t.assignee] || 0) + 1; });
    const max = Math.max(1, ...Object.values(load));
    const loadHtml = Object.entries(load).sort((a, b) => b[1] - a[1]).map(([u, n]) => `
        <div class="trk-load-row"><span class="trk-load-name">${avatar(u, 22)}${h(personName(u))}</span>
            <span class="trk-load-bar"><span style="width:${Math.round(n / max * 100)}%"></span></span><span class="trk-mono">${n}</span></div>`).join('');
    return `<div class="trk-stats">${stats}</div>
        <div class="trk-list-wrap">
            <section class="trk-table" aria-label="Задачи">
                <div class="trk-table-scroll">
                    <div class="trk-row trk-row-head"><span></span><span>№</span><span>Задача</span><span>Исполнитель</span><span>Срок</span><span>Метка</span></div>
                    ${groups || '<div class="trk-col-empty trk-pad">Задач не найдено</div>'}
                </div>
            </section>
            <section class="trk-panel" aria-label="Нагрузка">
                <h2 class="trk-panel-h">Нагрузка команды</h2>
                <div class="trk-panel-sub">Открытые задачи по исполнителям</div>
                ${loadHtml || '<div class="trk-col-empty">Пока никому ничего не назначено</div>'}
            </section>
        </div>`;
}

// ── Команда ───────────────────────────────────────────────────────
function teamView() {
    const owner = isOwner();
    const openBy = {};
    S.tasks.filter(t => !isDone(t) && t.assignee).forEach(t => { openBy[t.assignee] = (openBy[t.assignee] || 0) + 1; });
    const lateBy = {};
    S.tasks.forEach(t => { const d = due(t); if (d && d.tone === 'late' && t.assignee) lateBy[t.assignee] = (lateBy[t.assignee] || 0) + 1; });
    const order = { owner: 0, lead: 1, member: 2, viewer: 3 };
    const rows = members().slice().sort((a, b) => order[a.role] - order[b.role] || (a.display_name || '').localeCompare(b.display_name || '')).map(m => {
        const n = openBy[m.username] || 0, late = lateBy[m.username] || 0;
        const roleCtl = owner && m.role !== 'owner'
            ? `<select class="trk-select trk-select-sm" data-act="member-role" data-user="${ha(m.username)}" aria-label="Роль: ${ha(m.display_name || m.username)}">
                ${['lead', 'member', 'viewer'].map(r => `<option value="${r}"${m.role === r ? ' selected' : ''}>${ROLE_RU[r]}</option>`).join('')}</select>`
            : `<span class="trk-role trk-role-${m.role}">${ROLE_RU[m.role] || m.role}</span>`;
        return `<div class="trk-member">
            ${avatar(m.username, 38)}
            <span class="trk-member-name"><b>${h(m.display_name || m.username)}${m.username === currentUser ? ' <span class="trk-you">вы</span>' : ''}</b><span>@${h(m.username)}</span></span>
            <span class="trk-member-load">${n ? `${n} ${plural(n, 'задача', 'задачи', 'задач')}` : 'нет открытых задач'}${late ? ` · <span class="late">${late} просрочено</span>` : ''}</span>
            ${roleCtl}
            ${owner && m.role !== 'owner' ? `<button class="btn-icon" data-act="member-remove" data-user="${ha(m.username)}" title="Убрать из проекта" aria-label="Убрать из проекта: ${ha(m.display_name || m.username)}"><span class="material-icons-round">close</span></button>` : '<span></span>'}
        </div>`;
    }).join('');
    const memberSet = new Set(members().map(m => m.username));
    const candidates = (typeof allUsers !== 'undefined' ? allUsers : []).filter(u => !memberSet.has(u.username) && !u.home_server)
        .sort((a, b) => (a.display_name || a.username).localeCompare(b.display_name || b.username));
    const add = canManage() ? `<section class="trk-panel">
        <h2 class="trk-panel-h">Добавить в проект</h2>
        <form class="trk-add-member" data-act="member-add">
            <select class="trk-select" name="user" aria-label="Сотрудник">${candidates.length ? '<option value="">Выберите сотрудника…</option>' + candidates.map(u => `<option value="${ha(u.username)}">${h(u.display_name || u.username)} · @${h(u.username)}</option>`).join('') : '<option value="">Все сотрудники уже в проекте</option>'}</select>
            <div class="trk-add-row">
                <select class="trk-select" name="role" aria-label="Роль">${['member', 'lead', 'viewer'].map(r => `<option value="${r}">${ROLE_RU[r]}</option>`).join('')}</select>
                <button class="trk-btn trk-btn-primary" type="submit"${candidates.length ? '' : ' disabled'}>Добавить</button>
            </div>
        </form>
    </section>` : '';
    const roles = Object.keys(ROLE_RU).map(r => `<div class="trk-role-row"><span class="trk-role trk-role-${r}">${ROLE_RU[r]}</span><span>${ROLE_HINT[r]}</span></div>`).join('');
    return `<div class="trk-list-wrap">
        <section class="trk-table" aria-label="Участники проекта">
            <div class="trk-table-head"><h2 class="trk-panel-h">Участники · ${members().length}</h2>
                <div class="trk-panel-sub">${S.project.visibility === 'public' ? 'Проект видят все сотрудники, менять — только участники' : 'Проект видят только участники'}</div></div>
            ${rows}
        </section>
        <div class="trk-side-col">
            ${add}
            <section class="trk-panel"><h2 class="trk-panel-h">Что могут роли</h2>${roles}</section>
        </div>
    </div>`;
}

// ── Карточка задачи ───────────────────────────────────────────────
function fileIcon(name) {
    const ext = (String(name).split('.').pop() || '').toLowerCase();
    const map = { pdf: ['PDF', '#F07A6A'], doc: ['DOC', '#8FB8E8'], docx: ['DOC', '#8FB8E8'], xls: ['XLS', '#7FE0B0'], xlsx: ['XLS', '#7FE0B0'], csv: ['CSV', '#7FE0B0'],
        jpg: ['JPG', '#C8A2E0'], jpeg: ['JPG', '#C8A2E0'], png: ['PNG', '#C8A2E0'], heic: ['IMG', '#C8A2E0'], webp: ['IMG', '#C8A2E0'], zip: ['ZIP', '#D9C79A'], dwg: ['DWG', '#E0A458'], txt: ['TXT', '#8A958F'] };
    const [label, color] = map[ext] || [(ext || 'FILE').slice(0, 4).toUpperCase(), '#8A958F'];
    return `<span class="trk-file-ic" style="--c:${color}">${h(label)}</span>`;
}

function renderDrawer() {
    const el = document.getElementById('trk-drawer');
    if (!el) return;
    const t = S.task;
    document.getElementById('trk-root').classList.toggle('drawer-open', !!t);
    if (!t) { el.innerHTML = ''; return; }
    // Перерисовка не должна стирать то, что человек сейчас печатает.
    const keep = {};
    el.querySelectorAll('[data-keep]').forEach(x => { keep[x.dataset.keep] = x.value; });
    const focusKeep = document.activeElement && el.contains(document.activeElement) ? document.activeElement.dataset.keep : null;

    const edit = canEditTask(t);
    const st = statusOf(t.status);
    const d = due(t);
    const statusOpts = statuses().map(s => `<option value="${ha(s.key)}"${s.key === t.status ? ' selected' : ''}>${h(s.name)}</option>`).join('');
    const whoOpts = '<option value="">Без исполнителя</option>' + members().filter(m => m.role !== 'viewer' || m.username === t.assignee)
        .map(m => `<option value="${ha(m.username)}"${m.username === t.assignee ? ' selected' : ''}>${h(m.display_name || m.username)}</option>`).join('');
    const labIds = new Set((t.labels || []).map(l => l.id));
    const labels = S.labels.length
        ? S.labels.map(l => `<button class="trk-label-chip trk-label-toggle${labIds.has(l.id) ? ' on' : ''}" style="--c:${ha(l.color || '#8A958F')}" data-act="label-toggle" data-id="${ha(l.id)}"${edit ? '' : ' disabled'} aria-pressed="${labIds.has(l.id)}">${h(l.name)}</button>`).join('')
        : `<span class="trk-muted">${canManage() ? 'Меток пока нет — <button class="trk-link" data-act="settings" data-tab="labels">добавить</button>' : 'нет'}</span>`;
    const subs = (t.subtasks || []);
    const subDone = subs.filter(s => s.completed).length;
    const subHtml = subs.map(s => `<div class="trk-check${s.completed ? ' on' : ''}">
        <label><input type="checkbox" data-act="sub-toggle" data-id="${ha(s.id)}"${s.completed ? ' checked' : ''}${edit || isMember() ? '' : ' disabled'}><span>${h(s.title)}</span></label>
        ${edit ? `<button class="btn-icon" data-act="sub-del" data-id="${ha(s.id)}" aria-label="Удалить пункт"><span class="material-icons-round">close</span></button>` : ''}
    </div>`).join('');
    const atts = (t.attachments || []).map(a => `<div class="trk-file">
        ${fileIcon(a.filename)}
        <a class="trk-file-name" href="${ha(srv(a.filepath))}" target="_blank" rel="noopener" download="${ha(a.filename)}">${h(a.filename)}</a>
        <span class="trk-file-meta">${formatFileSize(a.filesize || 0)} · ${h(personName(a.uploaded_by))}</span>
        ${(a.uploaded_by === currentUser || canManage()) ? `<button class="btn-icon" data-act="att-del" data-id="${ha(a.id)}" aria-label="Удалить файл ${ha(a.filename)}"><span class="material-icons-round">delete_outline</span></button>` : ''}
    </div>`).join('');
    const discOn = t.discussion !== 0 && t.discussion !== false;
    const comments = (t.comments || []).map(c => `<div class="trk-comment">
        ${avatar(c.username, 28)}
        <div class="trk-comment-body">
            <div class="trk-comment-head"><b>${h(c.display_name || personName(c.username))}</b><span>${h(fmtTime(c.created_at))}</span>
                ${(c.username === currentUser || canManage()) ? `<button class="trk-link trk-comment-del" data-act="comment-del" data-id="${ha(c.id)}">удалить</button>` : ''}</div>
            <div class="trk-comment-text">${typeof formatMsgText === 'function' ? formatMsgText(c.text) : h(c.text)}</div>
        </div>
    </div>`).join('');

    el.innerHTML = `
        <div class="trk-d-head">
            <span class="trk-d-key">
                ${t.task_key ? `<span class="trk-mono">${h(t.task_key)}</span>
                <button class="trk-chip-btn" data-act="copy-link" title="Скопировать ссылку — в чате она покажется как ${ha(t.task_key)}"><span class="material-icons-round">link</span>Ссылка</button>` : ''}
            </span>
            <button class="btn-icon" data-act="close-task" aria-label="Закрыть карточку"><span class="material-icons-round">close</span></button>
        </div>
        <div class="trk-d-scroll">
            <textarea class="trk-d-title" rows="1" data-act="field" data-field="title" data-keep="title" aria-label="Название задачи" maxlength="300"${edit ? '' : ' readonly'}>${h(t.title)}</textarea>
            <div class="trk-d-grid">
                <span class="trk-d-l">Статус</span>
                <span><select class="trk-status-sel" style="--c:${ha(st.color)}" data-act="status" aria-label="Статус"${edit || isMember() && canCreate() ? '' : ' disabled'}>${statusOpts}</select></span>
                <span class="trk-d-l">Исполнитель</span>
                <span class="trk-d-who">${avatar(t.assignee, 24)}<select class="trk-select trk-select-plain" data-act="field" data-field="assignee" aria-label="Исполнитель"${edit ? '' : ' disabled'}>${whoOpts}</select></span>
                <span class="trk-d-l">Срок</span>
                <span class="trk-d-due"><input type="date" class="trk-date" value="${ha((t.due_date || '').slice(0, 10))}" data-act="field" data-field="due_date" aria-label="Срок"${edit ? '' : ' disabled'}>${d ? `<span class="trk-due ${d.tone}">${h(d.tone ? d.text : (dayDiff(t.due_date) > 1 ? 'через ' + dayDiff(t.due_date) + ' ' + plural(dayDiff(t.due_date), 'день', 'дня', 'дней') : ''))}</span>` : ''}</span>
                <span class="trk-d-l">Метки</span>
                <span class="trk-d-labels">${labels}</span>
            </div>

            <label class="trk-d-sec-h" for="trk-d-desc">Описание</label>
            <textarea id="trk-d-desc" class="trk-d-desc" rows="3" data-act="field" data-field="description" data-keep="desc" placeholder="${edit ? 'Подробности, ссылки, договорённости…' : 'Без описания'}"${edit ? '' : ' readonly'}>${h(t.description || '')}</textarea>

            <div class="trk-d-sec">
                <div class="trk-d-sec-h">Чек-лист ${subs.length ? `<span class="trk-muted">${subDone} из ${subs.length}</span>` : ''}</div>
                ${subHtml}
                ${edit ? `<form class="trk-d-add" data-act="sub-add"><input type="text" placeholder="+ Пункт чек-листа" data-keep="sub" maxlength="200" aria-label="Новый пункт чек-листа"></form>` : (subs.length ? '' : '<div class="trk-muted">Пунктов нет</div>')}
            </div>

            <div class="trk-d-sec trk-drop" data-drop="1">
                <div class="trk-d-sec-h">Файлы ${t.attachments && t.attachments.length ? `<span class="trk-muted">${t.attachments.length}</span>` : ''}
                    ${isMember() && canCreate() ? `<label class="trk-chip-btn trk-attach"><span class="material-icons-round">attach_file</span>Прикрепить<input type="file" multiple hidden data-act="upload"></label>` : ''}</div>
                ${atts}
                ${isMember() && canCreate() ? `<div class="trk-dropzone">Перетащите файлы сюда — документы, фото, сметы</div>` : (atts ? '' : '<div class="trk-muted">Файлов нет</div>')}
            </div>

            <div class="trk-d-sec">
                <div class="trk-d-sec-h">Обсуждение
                    ${edit ? `<label class="trk-switch-l">${discOn ? 'включено' : 'выключено'}<button type="button" role="switch" class="trk-switch${discOn ? ' on' : ''}" aria-checked="${discOn}" aria-label="Обсуждение в задаче" data-act="discussion"><span></span></button></label>` : ''}
                </div>
                ${discOn ? `${comments || '<div class="trk-muted">Комментариев пока нет</div>'}
                    ${isMember() && canCreate() ? `<form class="trk-d-add trk-d-comment" data-act="comment"><textarea rows="1" placeholder="Написать комментарий…" data-keep="comment" aria-label="Комментарий" maxlength="4000"></textarea><button class="btn-icon" type="submit" aria-label="Отправить"><span class="material-icons-round">send</span></button></form>` : ''}`
                    : `<div class="trk-d-off">У этой задачи нет ни чата, ни ветки комментариев.${edit ? ' Включить можно в любой момент.' : ''}</div>`}
            </div>

            <div class="trk-d-foot">
                <span>Создал(а) ${h(personName(t.created_by))} · ${h(fmtTime(t.created_at))}</span>
                ${canManage() ? `<button class="trk-link trk-danger" data-act="task-delete">Удалить задачу</button>` : ''}
            </div>
        </div>`;
    Object.entries(keep).forEach(([k, v]) => { const x = el.querySelector(`[data-keep="${k}"]`); if (x && k !== 'title' && k !== 'desc') x.value = v; });
    if (focusKeep) { const x = el.querySelector(`[data-keep="${focusKeep}"]`); if (x) { if (focusKeep !== 'title' && focusKeep !== 'desc') x.value = keep[focusKeep] || ''; x.focus(); } }
    autoGrow(el.querySelector('.trk-d-title'));
    placeDrawer();
}
/** Карточка выезжает под шапкой проекта, а не поверх неё: «Новая задача»,
 *  вкладки и фильтры остаются под рукой, пока карточка открыта. */
function placeDrawer() {
    const dr = document.getElementById('trk-drawer'), body = document.getElementById('trk-body');
    if (!dr) return;
    const overlay = window.innerWidth > 768 && window.innerWidth < 1500;
    dr.style.top = overlay && body ? body.offsetTop + 'px' : '';
}
function autoGrow(ta) { if (!ta) return; ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }
function markActiveCard() {
    document.querySelectorAll('#trk-body .trk-card').forEach(c => c.classList.toggle('sel', !!(S.task && c.dataset.id === S.task.id)));
}

// ═════════════════════════════════════════════════════════════════
// Действия
// ═════════════════════════════════════════════════════════════════
const slugP = () => encodeURIComponent(S.project.slug);

async function setTaskField(field, value) {
    const t = S.task; if (!t) return;
    if (field === 'title' && !value.trim()) { renderDrawer(); return showToast('Нужно название', 'error'); }
    try {
        const upd = await api('PUT', `/projects/${slugP()}/tasks/${t.id}`, { [field]: value });
        Object.assign(S.task, upd); upsertTask(upd);
        renderDrawer(); refreshBody();
    } catch (_) { renderDrawer(); }
}
async function setStatus(key) {
    const t = S.task; if (!t) return;
    try {
        await api('PATCH', `/projects/${slugP()}/tasks/${t.id}/status`, { status: key });
        t.status = key; upsertTask({ id: t.id, status: key, updated_at: new Date().toISOString() });
        renderDrawer(); refreshBody();
    } catch (_) { renderDrawer(); }
}
function refreshBody() {
    const body = document.getElementById('trk-body');
    if (!body) return renderMain();
    // Перестраиваем шапку целиком только когда меняются счётчики фильтров.
    renderMain();
}

async function moveTask(id, key) {
    const t = S.tasks.find(x => x.id === id);
    if (!t || t.status === key) return;
    const prev = t.status;
    t.status = key; t.updated_at = new Date().toISOString();
    renderMain();
    try { await api('PATCH', `/projects/${slugP()}/tasks/${id}/status`, { status: key }); }
    catch (_) { t.status = prev; renderMain(); return; }
    if (S.task && S.task.id === id) { S.task.status = key; renderDrawer(); }
}

async function quickAdd(status, title, openAfter) {
    title = title.trim(); if (!title) return;
    try {
        const t = canSubmit()
            ? await api('POST', `/projects/${slugP()}/submit-ticket`, { title })
            : await api('POST', `/projects/${slugP()}/tasks`, { title, status });
        if (canSubmit()) showToast('Заявка отправлена — команда проекта её увидит');
        upsertTask(t); renderMain();
        if (openAfter) return openTask(t.id);
        const inp = document.querySelector(`.trk-quick[data-status="${CSS.escape(status)}"] input`);
        if (inp) inp.focus();
    } catch (_) {}
}

/** «Новая задача» ведёт в поле быстрого добавления первой колонки: человек
 *  пишет название, жмёт Enter — и сразу попадает в карточку дописать срок и
 *  исполнителя. Пустых «Новая задача» на доске от случайного клика не бывает. */
function newTask() {
    if (S.view !== 'board') { S.view = 'board'; lsSet('trk_view', 'board'); renderMain(); }
    const first = statuses().find(s => s.on_board) || statuses()[0];
    if (!first) return;
    const f = JSON.parse(lsGet('trk_fold_' + S.project.id, '{}'));
    if (f[first.key] || (f[first.key] === undefined && first.kind === 'done')) { f[first.key] = false; lsSet('trk_fold_' + S.project.id, JSON.stringify(f)); renderMain(); }
    const inp = document.querySelector(`.trk-quick[data-status="${CSS.escape(first.key)}"] input`);
    if (!inp) return;
    S.openAfterQuick = true;
    inp.placeholder = 'Название задачи и Enter';
    inp.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    inp.focus();
}

async function uploadFiles(files) {
    if (!S.task || !files || !files.length) return;
    const id = S.task.id;
    for (const f of files) {
        const fd = new FormData(); fd.append('file', f);
        try { await api('POST', `/projects/${slugP()}/tasks/${id}/attachments`, fd); }
        catch (_) { break; }
    }
    showToast(files.length > 1 ? 'Файлы прикреплены' : 'Файл прикреплён');
    const t = S.tasks.find(x => x.id === id); if (t) t.attachment_count = (t.attachment_count || 0) + files.length;
    if (S.task && S.task.id === id) await refreshTask();
    refreshBody();
}

// ── Окно «Новый проект» ──────────────────────────────────────────
const NP = { name: '', prefix: '', prefixTouched: false, color: PALETTE[0], desc: '', tpl: 'object', parent: '', vis: 'private', disc: true, people: [] };
function autoPrefix(name) {
    const words = (name.match(/[0-9A-Za-zА-Яа-яЁё]+/g) || []).filter(w => w.length > 2);
    return ((words[words.length - 1] || name.trim() || 'ZW').slice(0, 3)).toUpperCase();
}
function openNewProject(parentId) {
    Object.assign(NP, { name: '', prefix: '', prefixTouched: false, color: PALETTE[S.projects.length % 8], desc: '', tpl: 'object', parent: parentId || '', vis: 'private', disc: true, people: [] });
    showModal('np', 'Новый проект', npBody(), `<button class="trk-btn" data-act="modal-close">Отмена</button><button class="trk-btn trk-btn-primary" data-act="np-create">Создать проект</button>`, 'wide');
    setTimeout(() => { const i = document.getElementById('np-name'); if (i) i.focus(); }, 30);
}
function npBody() {
    const users = (typeof allUsers !== 'undefined' ? allUsers : []).filter(u => u.username !== currentUser && !u.home_server && !NP.people.some(p => p.username === u.username))
        .sort((a, b) => (a.display_name || a.username).localeCompare(b.display_name || b.username));
    const parents = S.projects.filter(p => p.is_member);
    return `<div class="trk-np">
        <div class="trk-np-col">
            <div class="trk-np-row">
                <label class="trk-field trk-grow">Название проекта
                    <input type="text" id="np-name" value="${ha(NP.name)}" data-act="np" data-k="name" placeholder="Например, Запуск сайта" maxlength="80"></label>
                <label class="trk-field trk-np-prefix">Префикс задач
                    <input type="text" id="np-prefix" value="${ha(NP.prefix)}" data-act="np" data-k="prefix" maxlength="6" placeholder="САЙ"></label>
            </div>
            <div class="trk-muted" id="np-prefix-hint">${NP.prefix ? `Задачи будут нумероваться <span class="trk-mono">${h(NP.prefix)}-1, ${h(NP.prefix)}-2…</span> — так их номер будет выглядеть и в чате` : 'Префикс подставится из названия'}</div>
            <div class="trk-field">Цвет
                <div class="trk-swatches">${PALETTE.slice(0, 9).map(c => `<button type="button" class="trk-swatch${NP.color === c ? ' on' : ''}" style="--c:${c}" data-act="np-color" data-color="${c}" aria-label="Цвет ${c}" aria-pressed="${NP.color === c}"></button>`).join('')}</div></div>
            <label class="trk-field">Описание <span class="trk-muted">необязательно</span>
                <textarea rows="2" data-act="np" data-k="desc" placeholder="Что за проект, кто заказчик, сроки" maxlength="500">${h(NP.desc)}</textarea></label>
            <div class="trk-field">Колонки доски
                <div class="trk-tpls">${TEMPLATES.map(tp => `<button type="button" class="trk-tpl${NP.tpl === tp.id ? ' on' : ''}" data-act="np-tpl" data-tpl="${tp.id}" aria-pressed="${NP.tpl === tp.id}">
                    <b>${tp.name}</b>${tp.cols.map(([n, c]) => `<span><i class="trk-dot" style="--c:${c}"></i>${n}</span>`).join('')}</button>`).join('')}</div>
                <span class="trk-muted">Колонки можно переименовать и добавить позже, в настройках проекта.</span></div>
            ${parents.length ? `<label class="trk-field">Входит в проект
                <select class="trk-select" data-act="np" data-k="parent"><option value="">— самостоятельный проект —</option>${parents.map(p => `<option value="${ha(p.id)}"${NP.parent === p.id ? ' selected' : ''}>${h(p.name)}</option>`).join('')}</select></label>` : ''}
        </div>
        <div class="trk-np-col trk-np-aside">
            <div class="trk-field">Кто видит проект
                <div class="trk-seg">${[['private', 'Только участники'], ['public', 'Вся компания']].map(([v, n]) => `<button type="button" class="${NP.vis === v ? 'on' : ''}" data-act="np-vis" data-v="${v}" aria-pressed="${NP.vis === v}">${n}</button>`).join('')}</div>
                <span class="trk-muted">${NP.vis === 'public' ? 'Видят все сотрудники, менять могут только участники. Подходит для заявок в ИТ или АХО.' : 'Проект видят только добавленные люди.'}</span></div>
            <label class="trk-check-row"><span>Обсуждение внутри новых задач<span class="trk-muted">${NP.disc ? 'включено — у каждой задачи свой чат' : 'выключено — задачи без чата; включить можно в карточке'}</span></span>
                <input type="checkbox" data-act="np-disc"${NP.disc ? ' checked' : ''}></label>
            <div class="trk-field">Участники · ${NP.people.length + 1}
                <div class="trk-np-person">${avatar(currentUser, 30)}<span class="trk-grow"><b>${h(personName(currentUser))}</b> <span class="trk-muted">вы</span></span><span class="trk-role trk-role-owner">Владелец</span></div>
                ${NP.people.map((p, i) => `<div class="trk-np-person">${avatar(p.username, 30)}<span class="trk-grow"><b>${h(personName(p.username))}</b></span>
                    <select class="trk-select trk-select-sm" data-act="np-role" data-i="${i}" aria-label="Роль">${['lead', 'member', 'viewer'].map(r => `<option value="${r}"${p.role === r ? ' selected' : ''}>${ROLE_RU[r]}</option>`).join('')}</select>
                    <button type="button" class="btn-icon" data-act="np-rm" data-i="${i}" aria-label="Убрать"><span class="material-icons-round">close</span></button></div>`).join('')}
                ${users.length ? `<select class="trk-select" data-act="np-add" aria-label="Добавить участника"><option value="">+ Добавить участника…</option>${users.map(u => `<option value="${ha(u.username)}">${h(u.display_name || u.username)}</option>`).join('')}</select>` : ''}
            </div>
        </div>
    </div>`;
}
function npRerender() { const b = document.querySelector('#trk-modal .trk-modal-body'); if (b) { const sc = b.scrollTop; b.innerHTML = npBody(); b.scrollTop = sc; } }
async function npCreate() {
    if (!NP.name.trim()) { showToast('Нужно название проекта', 'error'); const i = document.getElementById('np-name'); if (i) i.focus(); return; }
    const body = {
        name: NP.name.trim(), prefix: (NP.prefix || autoPrefix(NP.name)).toUpperCase(), color: NP.color, description: NP.desc.trim(),
        template: NP.tpl, visibility: NP.vis, discussion_default: NP.disc, members: NP.people, parent_id: NP.parent || null,
    };
    try {
        const p = await api('POST', '/projects', body);
        closeModal();
        await loadProjects();
        if (p.prefix && p.prefix !== body.prefix) showToast(`Префикс ${body.prefix} уже занят — задачи будут ${p.prefix}-1, ${p.prefix}-2…`);
        else showToast('Проект создан');
        await openProject(p.slug);
    } catch (_) {}
}

// ── Окно настроек проекта ────────────────────────────────────────
const ST = { tab: 'statuses', list: [], proj: null };
function openSettings(tab) {
    ST.tab = tab || 'statuses';
    ST.list = statuses().map(s => Object.assign({}, s));
    const p = S.project;
    ST.proj = { name: p.name, prefix: p.prefix || '', color: projColor(p), description: p.description || '', visibility: p.visibility || 'private', discussion_default: p.discussion_default !== 0 };
    showModal('settings', 'Настройки проекта', stBody(), stFoot(), 'wide');
}
function stFoot() {
    if (ST.tab === 'labels') return `<button class="trk-btn" data-act="modal-close">Готово</button>`;
    return `<button class="trk-btn" data-act="modal-close">Отмена</button><button class="trk-btn trk-btn-primary" data-act="st-save">Сохранить</button>`;
}
function stBody() {
    const tabs = [['statuses', 'Статусы и колонки'], ['labels', 'Метки']].concat(isOwner() ? [['project', 'Проект']] : [])
        .map(([id, n]) => `<button class="trk-tab${ST.tab === id ? ' on' : ''}" data-act="st-tab" data-tab="${id}" role="tab" aria-selected="${ST.tab === id}">${n}</button>`).join('');
    let body = '';
    if (ST.tab === 'statuses') {
        body = `<p class="trk-muted trk-p">Каждый статус — колонка на доске. Тип подсказывает трекеру, когда задача закрыта: такие не попадают в просрочку. Если удалить статус, его задачи перейдут в первый статус списка.</p>
            <div class="trk-st-list">${ST.list.map((s, i) => `<div class="trk-st-row">
                <span class="trk-st-move">
                    <button class="btn-icon" data-act="st-up" data-i="${i}" aria-label="Выше"${i ? '' : ' disabled'}><span class="material-icons-round">keyboard_arrow_up</span></button>
                    <button class="btn-icon" data-act="st-down" data-i="${i}" aria-label="Ниже"${i < ST.list.length - 1 ? '' : ' disabled'}><span class="material-icons-round">keyboard_arrow_down</span></button>
                </span>
                <button class="trk-st-color" style="--c:${ha(s.color)}" data-act="st-color" data-i="${i}" aria-label="Сменить цвет «${ha(s.name)}»"><span></span></button>
                <input type="text" class="trk-st-name" value="${ha(s.name)}" data-act="st-name" data-i="${i}" maxlength="40" aria-label="Название статуса">
                <select class="trk-select trk-select-sm" data-act="st-kind" data-i="${i}" aria-label="Тип статуса">${Object.entries(KIND_RU).map(([k, n]) => `<option value="${k}"${s.kind === k ? ' selected' : ''}>${n}</option>`).join('')}</select>
                <label class="trk-st-board" title="Показывать колонкой на доске"><input type="checkbox" data-act="st-board" data-i="${i}"${s.on_board ? ' checked' : ''}><span>на доске</span></label>
                <button class="btn-icon" data-act="st-del" data-i="${i}" aria-label="Удалить статус «${ha(s.name)}»"${ST.list.length > 1 ? '' : ' disabled'}><span class="material-icons-round">delete_outline</span></button>
            </div>`).join('')}</div>
            <button class="trk-btn trk-btn-dashed" data-act="st-add"${ST.list.length >= 20 ? ' disabled' : ''}><span class="material-icons-round">add</span>Добавить статус</button>`;
    } else if (ST.tab === 'labels') {
        body = `<p class="trk-muted trk-p">Метки помогают отличать задачи на доске: «Материалы», «Документы», «Срочно».</p>
            <div class="trk-lab-list">${S.labels.map(l => `<div class="trk-lab-row"><span class="trk-label-chip" style="--c:${ha(l.color || '#8A958F')}">${h(l.name)}</span>
                <button class="btn-icon" data-act="lab-del" data-id="${ha(l.id)}" aria-label="Удалить метку ${ha(l.name)}"><span class="material-icons-round">delete_outline</span></button></div>`).join('') || '<div class="trk-muted">Меток пока нет</div>'}</div>
            <form class="trk-lab-add" data-act="lab-add">
                <input type="text" name="name" placeholder="Новая метка" maxlength="30" aria-label="Название метки">
                <div class="trk-swatches">${PALETTE.slice(0, 9).map((c, i) => `<label class="trk-swatch-r"><input type="radio" name="color" value="${c}"${i === 1 ? ' checked' : ''}><span class="trk-swatch" style="--c:${c}"></span></label>`).join('')}</div>
                <button class="trk-btn trk-btn-primary" type="submit">Добавить</button>
            </form>`;
    } else {
        const p = ST.proj;
        body = `<div class="trk-np-row">
                <label class="trk-field trk-grow">Название<input type="text" value="${ha(p.name)}" data-act="stp" data-k="name" maxlength="80"></label>
                <label class="trk-field trk-np-prefix">Префикс<input type="text" value="${ha(p.prefix)}" data-act="stp" data-k="prefix" maxlength="6"></label>
            </div>
            <div class="trk-muted">Новый префикс получат только новые задачи — у старых номера останутся прежними.</div>
            <div class="trk-field">Цвет<div class="trk-swatches">${PALETTE.slice(0, 9).map(c => `<button type="button" class="trk-swatch${p.color === c ? ' on' : ''}" style="--c:${c}" data-act="stp-color" data-color="${c}" aria-pressed="${p.color === c}" aria-label="Цвет ${c}"></button>`).join('')}</div></div>
            <label class="trk-field">Описание<textarea rows="2" data-act="stp" data-k="description" maxlength="500">${h(p.description)}</textarea></label>
            <div class="trk-field">Кто видит проект<div class="trk-seg">${[['private', 'Только участники'], ['public', 'Вся компания']].map(([v, n]) => `<button type="button" class="${p.visibility === v ? 'on' : ''}" data-act="stp-vis" data-v="${v}">${n}</button>`).join('')}</div></div>
            <label class="trk-check-row"><span>Обсуждение внутри новых задач<span class="trk-muted">уже созданных задач это не касается</span></span><input type="checkbox" data-act="stp-disc"${p.discussion_default ? ' checked' : ''}></label>
            <div class="trk-danger-zone"><div><b>Удалить проект</b><span class="trk-muted">Вместе со всеми задачами, файлами и комментариями. Отменить нельзя.</span></div>
                <button class="trk-btn trk-btn-danger" data-act="project-delete">Удалить</button></div>`;
    }
    return `<div class="trk-tabs trk-tabs-modal" role="tablist">${tabs}</div><div class="trk-st-body">${body}</div>`;
}
function stRerender() {
    const m = document.getElementById('trk-modal'); if (!m) return;
    m.querySelector('.trk-modal-body').innerHTML = stBody();
    m.querySelector('.modal-footer').innerHTML = stFoot();
}
async function stSave() {
    try {
        if (ST.tab === 'statuses') {
            if (ST.list.some(s => !s.name.trim())) return showToast('У каждого статуса должно быть название', 'error');
            const saved = await api('PUT', `/projects/${slugP()}/statuses`, { statuses: ST.list.map(s => ({ key: s.key || '', name: s.name.trim(), color: s.color, kind: s.kind, on_board: s.on_board })) });
            S.project.statuses = saved;
            const tasks = await api('GET', `/projects/${slugP()}/tasks`); S.tasks = tasks;
            showToast('Колонки сохранены');
        } else if (ST.tab === 'project') {
            const p = ST.proj;
            if (!p.name.trim()) return showToast('Нужно название проекта', 'error');
            const r = await api('PUT', `/projects/${slugP()}`, { name: p.name.trim(), prefix: p.prefix.trim(), color: p.color, description: p.description, visibility: p.visibility, discussion_default: p.discussion_default });
            Object.assign(S.project, { name: p.name.trim(), prefix: r.prefix, color: p.color, description: p.description, visibility: p.visibility, discussion_default: p.discussion_default ? 1 : 0 });
            await loadProjects();
            showToast(r.prefix && r.prefix !== p.prefix.trim().toUpperCase() && p.prefix.trim() ? `Префикс занят — новые задачи будут ${r.prefix}-…` : 'Проект сохранён');
        }
        closeModal();
        render();
    } catch (_) {}
}

// ── Модальное окно (общая обёртка) ───────────────────────────────
function showModal(kind, title, body, foot, size) {
    closeModal();
    const m = document.createElement('div');
    m.id = 'trk-modal'; m.className = 'modal trk-modal'; m.dataset.kind = kind;
    m.setAttribute('role', 'dialog'); m.setAttribute('aria-modal', 'true'); m.setAttribute('aria-label', title);
    m.innerHTML = `<div class="modal-backdrop" data-act="modal-close"></div>
        <div class="modal-content trk-modal-content${size === 'wide' ? ' wide' : ''}">
            <div class="modal-header"><span class="modal-title trk-modal-title">${h(title)}</span>
                <button class="btn-icon" data-act="modal-close" aria-label="Закрыть"><span class="material-icons-round">close</span></button></div>
            <div class="modal-body trk-modal-body">${body}</div>
            <div class="modal-footer">${foot}</div>
        </div>`;
    m.style.display = 'flex';
    document.body.appendChild(m);
    m.addEventListener('click', onClick);
    m.addEventListener('change', onChange);
    m.addEventListener('input', onInput);
    m.addEventListener('submit', onSubmit);
}
function closeModal() { const m = document.getElementById('trk-modal'); if (m) m.remove(); }

// ═════════════════════════════════════════════════════════════════
// Делегированные события
// ═════════════════════════════════════════════════════════════════
async function onClick(e) {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const act = el.dataset.act;
    const ds = el.dataset;
    switch (act) {
        case 'sidebar': toggleSidebar(); break;
        case 'project': e.preventDefault(); await openProject(ds.slug); break;
        case 'new-project': openNewProject(); break;
        case 'view':
            S.view = ds.view; lsSet('trk_view', S.view);
            if (S.view === 'team' && S.task) { S.task = null; renderDrawer(); }
            renderMain(); break;
        case 'filter-late': S.filter.late = !S.filter.late; renderMain(); break;
        case 'settings': openSettings(ds.tab); break;
        case 'new-task': newTask(); break;
        case 'task':
            if (e.target.closest('a, button:not([data-act="task"]), input, select')) return;
            await openTask(ds.id); break;
        case 'fold': {
            const k = 'trk_fold_' + S.project.id;
            const f = JSON.parse(lsGet(k, '{}'));
            const s = statusOf(ds.key);
            const cur = f[ds.key] !== undefined ? f[ds.key] : s.kind === 'done';
            f[ds.key] = !cur; lsSet(k, JSON.stringify(f)); renderMain(); break;
        }
        case 'close-task': closeTask(); break;
        case 'copy-link': if (S.task && S.task.task_key) copyTaskLink(S.task.task_key); break;
        case 'label-toggle': {
            if (!S.task) return;
            const ids = new Set((S.task.labels || []).map(l => l.id));
            ids.has(ds.id) ? ids.delete(ds.id) : ids.add(ds.id);
            await setTaskField('label_ids', [...ids]); break;
        }
        case 'sub-del':
            try { await api('DELETE', `/projects/${slugP()}/tasks/${S.task.id}/subtasks/${ds.id}`); await refreshTask(); patchCounts(); } catch (_) {}
            break;
        case 'att-del':
            if (!confirm('Удалить файл из задачи?')) return;
            try { await api('DELETE', `/projects/${slugP()}/tasks/${S.task.id}/attachments/${ds.id}`); await refreshTask(); patchCounts(); } catch (_) {}
            break;
        case 'discussion': {
            const on = !(S.task.discussion !== 0 && S.task.discussion !== false);
            await setTaskField('discussion', on); break;
        }
        case 'comment-del':
            try { await api('DELETE', `/projects/${slugP()}/tasks/${S.task.id}/comments/${ds.id}`); await refreshTask(); patchCounts(); } catch (_) {}
            break;
        case 'task-delete': {
            const t = S.task; if (!t) return;
            if (!confirm(`Удалить задачу «${t.title}»? Отменить нельзя.`)) return;
            try { await api('DELETE', `/projects/${slugP()}/tasks/${t.id}`); S.tasks = S.tasks.filter(x => x.id !== t.id); closeTask(); renderMain(); showToast('Задача удалена'); } catch (_) {}
            break;
        }
        case 'member-remove': {
            const name = personName(ds.user);
            if (!confirm(`Убрать ${name} из проекта?`)) return;
            try { await api('DELETE', `/projects/${slugP()}/members/${encodeURIComponent(ds.user)}`); await reloadProject(); } catch (_) {}
            break;
        }
        // окно «Новый проект»
        case 'modal-close': closeModal(); break;
        case 'np-color': NP.color = ds.color; npRerender(); break;
        case 'np-tpl': NP.tpl = ds.tpl; npRerender(); break;
        case 'np-vis': NP.vis = ds.v; npRerender(); break;
        case 'np-rm': NP.people.splice(+ds.i, 1); npRerender(); break;
        case 'np-create': await npCreate(); break;
        // окно настроек
        case 'st-tab': ST.tab = ds.tab; stRerender(); break;
        case 'st-up': case 'st-down': {
            const i = +ds.i, j = act === 'st-up' ? i - 1 : i + 1;
            if (j < 0 || j >= ST.list.length) return;
            [ST.list[i], ST.list[j]] = [ST.list[j], ST.list[i]]; stRerender(); break;
        }
        case 'st-color': { const s = ST.list[+ds.i]; const k = PALETTE.indexOf(s.color); s.color = PALETTE[(k + 1) % PALETTE.length]; stRerender(); break; }
        case 'st-del': {
            const s = ST.list[+ds.i];
            const n = S.tasks.filter(t => t.status === s.key).length;
            if (n && !confirm(`В статусе «${s.name}» ${n} ${plural(n, 'задача', 'задачи', 'задач')}. После сохранения они перейдут в «${(ST.list.find((x, j) => j !== +ds.i) || {}).name}». Удалить статус?`)) return;
            ST.list.splice(+ds.i, 1); stRerender(); break;
        }
        case 'st-add': ST.list.push({ key: '', name: 'Новый статус', color: PALETTE[(ST.list.length + 3) % PALETTE.length], kind: 'work', on_board: true }); stRerender();
            setTimeout(() => { const ins = document.querySelectorAll('.trk-st-name'); const x = ins[ins.length - 1]; if (x) { x.focus(); x.select(); } }, 20); break;
        case 'st-save': await stSave(); break;
        case 'stp-color': ST.proj.color = ds.color; stRerender(); break;
        case 'stp-vis': ST.proj.visibility = ds.v; stRerender(); break;
        case 'lab-del':
            try { await api('DELETE', `/projects/${slugP()}/labels/${ds.id}`); S.labels = S.labels.filter(l => l.id !== ds.id); S.tasks.forEach(t => { t.labels = (t.labels || []).filter(l => l.id !== ds.id); }); stRerender(); renderMain(); if (S.task) await refreshTask(); } catch (_) {}
            break;
        case 'project-delete': {
            if (!confirm(`Удалить проект «${S.project.name}» со всеми задачами? Отменить нельзя.`)) return;
            try { await api('DELETE', `/projects/${slugP()}`); closeModal(); lsSet('trk_last', ''); S.project = null; S.task = null; await navigateToTasks(); showToast('Проект удалён'); } catch (_) {}
            break;
        }
    }
}

async function onChange(e) {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const ds = el.dataset;
    switch (ds.act) {
        case 'filter-who': S.filter.who = el.value; renderMain(); break;
        case 'status': await setStatus(el.value); break;
        case 'field': {
            if (!S.task) return;
            let v = el.value;
            if (ds.field === 'title') v = v.replace(/\s+/g, ' ').trim();
            if ((S.task[ds.field] || '') === (v || '')) return;
            await setTaskField(ds.field, v); break;
        }
        case 'sub-toggle':
            try { await api('PUT', `/projects/${slugP()}/tasks/${S.task.id}/subtasks/${ds.id}`, { completed: el.checked }); await refreshTask(); patchCounts(); } catch (_) { el.checked = !el.checked; }
            break;
        case 'upload': await uploadFiles([...el.files]); el.value = ''; break;
        case 'member-role':
            try { await api('PUT', `/projects/${slugP()}/members/${encodeURIComponent(ds.user)}`, { role: el.value }); await reloadProject(); showToast('Роль изменена'); } catch (_) { await reloadProject(); }
            break;
        case 'np': if (ds.k === 'parent') NP.parent = el.value; break;
        case 'np-disc': NP.disc = el.checked; npRerender(); break;
        case 'np-add': if (el.value) { NP.people.push({ username: el.value, role: 'member' }); npRerender(); } break;
        case 'np-role': NP.people[+ds.i].role = el.value; break;
        case 'st-kind': ST.list[+ds.i].kind = el.value; break;
        case 'st-board': ST.list[+ds.i].on_board = el.checked; break;
        case 'stp-disc': ST.proj.discussion_default = el.checked; break;
    }
}

let _qTimer = null;
function onInput(e) {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const ds = el.dataset;
    switch (ds.act) {
        case 'filter-q':
            S.filter.q = el.value;
            clearTimeout(_qTimer);
            _qTimer = setTimeout(() => {
                const body = document.getElementById('trk-body');
                if (body) body.innerHTML = S.view === 'list' ? listView() : boardView();
            }, 120);
            break;
        case 'np':
            if (ds.k === 'name') {
                NP.name = el.value;
                if (!NP.prefixTouched) {
                    NP.prefix = el.value.trim() ? autoPrefix(el.value) : '';
                    const p = document.getElementById('np-prefix'); if (p) p.value = NP.prefix;
                }
            } else if (ds.k === 'prefix') {
                NP.prefixTouched = true; NP.prefix = el.value.toUpperCase().replace(/[^0-9A-ZА-ЯЁ]/g, '');
                if (el.value !== NP.prefix) el.value = NP.prefix;
            } else if (ds.k === 'desc') NP.desc = el.value;
            const hint = document.getElementById('np-prefix-hint');
            if (hint) hint.innerHTML = NP.prefix ? `Задачи будут нумероваться <span class="trk-mono">${h(NP.prefix)}-1, ${h(NP.prefix)}-2…</span> — так их номер будет выглядеть и в чате` : 'Префикс подставится из названия';
            break;
        case 'st-name': ST.list[+ds.i].name = el.value; break;
        case 'stp':
            ST.proj[ds.k] = ds.k === 'prefix' ? el.value.toUpperCase().replace(/[^0-9A-ZА-ЯЁ]/g, '') : el.value;
            if (ds.k === 'prefix' && el.value !== ST.proj.prefix) el.value = ST.proj.prefix;
            break;
        case 'field': if (ds.field === 'title') autoGrow(el); break;
    }
}

async function onSubmit(e) {
    const form = e.target.closest('form[data-act]');
    if (!form) return;
    e.preventDefault();
    const act = form.dataset.act;
    if (act === 'quick') {
        const inp = form.querySelector('input'); const v = inp.value; inp.value = '';
        const openAfter = !!S.openAfterQuick; S.openAfterQuick = false;
        await quickAdd(form.dataset.status, v, openAfter);
    } else if (act === 'sub-add') {
        const inp = form.querySelector('input'); const v = inp.value.trim(); if (!v) return;
        inp.value = '';
        try { await api('POST', `/projects/${slugP()}/tasks/${S.task.id}/subtasks`, { title: v }); await refreshTask(); patchCounts(); const x = document.querySelector('[data-keep="sub"]'); if (x) x.focus(); } catch (_) { inp.value = v; }
    } else if (act === 'comment') {
        const ta = form.querySelector('textarea'); const v = ta.value.trim(); if (!v) return;
        ta.value = '';
        try { await api('POST', `/projects/${slugP()}/tasks/${S.task.id}/comments`, { text: v }); await refreshTask(); patchCounts(); } catch (_) { ta.value = v; }
    } else if (act === 'member-add') {
        const user = form.user.value, role = form.role.value;
        if (!user) return showToast('Выберите сотрудника', 'error');
        try { await api('POST', `/projects/${slugP()}/members`, { username: user, role }); await reloadProject(); showToast(`${personName(user)} — в проекте`); } catch (_) {}
    } else if (act === 'lab-add') {
        const name = form.name.value.trim(); if (!name) return;
        const color = (form.querySelector('input[name="color"]:checked') || {}).value || PALETTE[1];
        try { const l = await api('POST', `/projects/${slugP()}/labels`, { name, color }); S.labels.push(l); stRerender(); if (S.task) renderDrawer(); } catch (_) {}
    }
}

function onKey(e) {
    const t = e.target;
    if (e.key === 'Enter' && t.closest && t.closest('[data-act="task"]') && t.matches('[data-act="task"]')) { openTask(t.dataset.id); return; }
    if (e.key === 'Enter' && !e.shiftKey && t.matches && t.matches('.trk-d-comment textarea')) { e.preventDefault(); t.form.requestSubmit(); return; }
    if (e.key === 'Enter' && t.matches && t.matches('.trk-d-title')) { e.preventDefault(); t.blur(); return; }
    if (e.key === 'Escape' && S.task && !document.getElementById('trk-modal') && currentPage === 'tasks') { closeTask(); }
}

/** Счётчики на карточке (чек-лист, файлы, комментарии) после правок в карточке. */
function patchCounts() {
    if (!S.task) return;
    const t = S.tasks.find(x => x.id === S.task.id); if (!t) return;
    const subs = S.task.subtasks || [];
    t.subtask_total = subs.length; t.subtask_done = subs.filter(s => s.completed).length;
    t.attachment_count = (S.task.attachments || []).length;
    t.comment_count = (S.task.comments || []).length;
    renderMain();
}
async function reloadProject() {
    if (!S.project) return;
    try { const p = await api('GET', `/projects/${slugP()}`); S.project = p; } catch (_) { return; }
    renderSide(); renderMain(); renderDrawer();
}

// ── Перетаскивание карточек и файлов ─────────────────────────────
let dragId = null;
function onDragStart(e) {
    const card = e.target.closest && e.target.closest('.trk-card[draggable="true"]');
    if (!card) return;
    dragId = card.dataset.id;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragId);
    requestAnimationFrame(() => card.classList.add('dragging'));
}
function onDragEnd() { dragId = null; document.querySelectorAll('.trk-card.dragging, .trk-drop-on').forEach(x => x.classList.remove('dragging', 'trk-drop-on')); }
function dropTarget(e) { return e.target.closest && e.target.closest('.trk-col[data-status], .trk-col-fold[data-status]'); }
// Перетаскивание не должно всплывать в #main-area: там висит загрузка файлов
// в открытый чат, и брошенный в карточку файл улетел бы ещё и в переписку.
function onDragOver(e) {
    e.stopPropagation();
    const files = e.dataTransfer && [...(e.dataTransfer.types || [])].includes('Files');
    const zone = e.target.closest && e.target.closest('.trk-drop');
    if (files && zone && S.task) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; zone.classList.add('trk-drop-on'); return; }
    if (!dragId) return;
    const col = dropTarget(e);
    if (!col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    document.querySelectorAll('.trk-drop-on').forEach(x => x !== col && x.classList.remove('trk-drop-on'));
    col.classList.add('trk-drop-on');
}
function onDragLeave(e) {
    e.stopPropagation();
    const z = e.target.closest && e.target.closest('.trk-drop-on');
    if (z && !z.contains(e.relatedTarget)) z.classList.remove('trk-drop-on');
}
async function onDrop(e) {
    e.stopPropagation();
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) e.preventDefault();
    const zone = e.target.closest && e.target.closest('.trk-drop');
    if (zone && e.dataTransfer.files && e.dataTransfer.files.length && S.task) {
        e.preventDefault(); zone.classList.remove('trk-drop-on');
        return uploadFiles([...e.dataTransfer.files]);
    }
    const col = dropTarget(e);
    if (!col || !dragId) return;
    e.preventDefault();
    const id = dragId; onDragEnd();
    await moveTask(id, col.dataset.status);
}

// ═════════════════════════════════════════════════════════════════
// События сервера
// ═════════════════════════════════════════════════════════════════
let _projTimer = null;
function reloadProjectsSoon() {
    clearTimeout(_projTimer);
    _projTimer = setTimeout(async () => { await loadProjects(); renderSide(); }, 300);
}
function handleTaskWsEvent(d) {
    TaskRefs.invalidate();
    if (!S.loaded) return;
    switch (d.event) {
        case 'project_created': case 'project_member_added': case 'project_member_removed':
            reloadProjectsSoon();
            if (S.project && d.project_id === S.project.id) {
                if (d.event === 'project_member_removed' && d.username === currentUser) { S.project = null; S.task = null; if (currentPage === 'tasks') navigateToTasks(); return; }
                reloadProject();
            }
            return;
        case 'project_deleted':
            reloadProjectsSoon();
            if (S.project && d.project_id === S.project.id) { S.project = null; S.task = null; if (currentPage === 'tasks') { showToast('Проект удалён'); navigateToTasks(); } }
            return;
        case 'project_updated':
            reloadProjectsSoon();
            if (S.project && d.project_id === S.project.id && d.project) { Object.assign(S.project, d.project); renderMain(); }
            return;
    }
    if (!S.project || d.project_id !== S.project.id) { if (d.event.startsWith('task_')) reloadProjectsSoon(); return; }
    switch (d.event) {
        case 'project_statuses_changed':
            S.project.statuses = d.statuses;
            api('GET', `/projects/${slugP()}/tasks`).then(ts => { S.tasks = ts; renderMain(); renderDrawer(); }).catch(() => {});
            break;
        case 'task_created': upsertTask(d.task); renderMain(); reloadProjectsSoon(); break;
        case 'task_updated':
            upsertTask(d.task); renderMain();
            if (S.task && S.task.id === d.task.id) refreshTask();
            break;
        case 'task_deleted':
            S.tasks = S.tasks.filter(t => t.id !== d.task_id); renderMain();
            if (S.task && S.task.id === d.task_id) { showToast('Задачу удалили'); closeTask(); }
            reloadProjectsSoon(); break;
        case 'task_status_changed': {
            const t = S.tasks.find(x => x.id === d.task_id);
            if (t) { t.status = d.new_status; t.updated_at = new Date().toISOString(); }
            renderMain();
            if (S.task && S.task.id === d.task_id) { S.task.status = d.new_status; renderDrawer(); }
            reloadProjectsSoon(); break;
        }
        case 'subtask_updated': case 'task_comment_added':
            if (S.task && S.task.id === d.task_id) refreshTask().then(patchCounts);
            else if (d.event === 'task_comment_added') { const t = S.tasks.find(x => x.id === d.task_id); if (t) { t.comment_count = (t.comment_count || 0) + 1; renderMain(); } }
            break;
    }
}

// ═════════════════════════════════════════════════════════════════
// Номера задач в чате: ЛЕС-24 → ссылка с точкой статуса
// ═════════════════════════════════════════════════════════════════
const KEY_RE = /(^|[^\p{L}\p{N}_\-\/])(\p{Lu}[\p{Lu}\p{N}]{1,5}-\d{1,6})(?![\p{L}\p{N}_])/gu;
const TaskRefs = {
    cache: new Map(),       // KEY → { data, at }
    pending: new Set(),
    timer: null,
    /** Текст (уже экранированный) → номера задач превращены в заготовки ссылок. */
    markText(html) {
        return html.replace(KEY_RE, (m, pre, key) => `${pre}<a class="task-ref" data-key="${key}" href="/t/${encodeURIComponent(key)}">${key}</a>`);
    },
    /** Ссылка /t/КЛЮЧ из чата → такая же заготовка. */
    fromUrl(url) {
        try {
            const u = new URL(url);
            if (!u.pathname.startsWith('/t/')) return null;
            const key = decodeURIComponent(u.pathname.slice(3)).replace(/\/$/, '');
            if (!/^[\p{L}\p{N}]{2,6}-\d{1,6}$/u.test(key)) return null;
            return `<a class="task-ref" data-key="${esc(key)}" href="/t/${encodeURIComponent(key)}">${esc(key)}</a>`;
        } catch (_) { return null; }
    },
    invalidate() { this.cache.clear(); document.querySelectorAll('.task-ref[data-res]').forEach(a => a.removeAttribute('data-res')); this.scan(); },
    scan() {
        clearTimeout(this.timer);
        this.timer = setTimeout(() => this._scan(), 120);
    },
    async _scan() {
        const els = [...document.querySelectorAll('.task-ref:not([data-res])')];
        if (!els.length || typeof token === 'undefined' || !token) return;
        const now = Date.now();
        const need = [...new Set(els.map(a => a.dataset.key.toUpperCase()))].filter(k => { const c = this.cache.get(k); return !(c && now - c.at < 60000) && !this.pending.has(k); });
        if (need.length) {
            need.forEach(k => this.pending.add(k));
            try {
                const res = await apiFetch('/tasks/keys?keys=' + encodeURIComponent(need.join(',')));
                const list = res && res.ok ? await res.json() : [];
                list.forEach(r => this.cache.set(String(r.key).toUpperCase(), { data: r, at: Date.now() }));
                need.forEach(k => { if (!this.cache.has(k)) this.cache.set(k, { data: { key: k, found: false }, at: Date.now() }); });
            } catch (_) {}
            need.forEach(k => this.pending.delete(k));
        }
        document.querySelectorAll('.task-ref:not([data-res])').forEach(a => this.paint(a));
    },
    paint(a) {
        const c = this.cache.get(a.dataset.key.toUpperCase());
        if (!c) return;
        const r = c.data;
        a.setAttribute('data-res', '1');
        if (!r.found) {                       // не номер задачи (COVID-19, ГОСТ-2) — обычный текст
            a.replaceWith(document.createTextNode(a.textContent));
            return;
        }
        a.textContent = r.key;
        a.classList.toggle('locked', !r.access);
        a.classList.toggle('closed', r.access && r.status_kind === 'done');
        a.style.setProperty('--c', r.access ? (r.status_color || '#8A958F') : '#5A6460');
        a.title = r.access ? `${r.key} · ${r.title} · ${r.status_name}` : `${r.key} — нет доступа к проекту`;
    },
    async open(a) {
        const key = a.dataset.key.toUpperCase();
        let c = this.cache.get(key);
        if (!c) { await this._scan(); c = this.cache.get(key); }
        const r = c && c.data;
        if (!r || !r.found) return;
        showPop(a, r);
    },
};
function showPop(anchor, r) {
    hidePop();
    const pop = document.createElement('div');
    pop.id = 'trk-pop'; pop.className = 'trk-pop'; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', 'Задача ' + r.key);
    if (!r.access) {
        pop.innerHTML = `<div class="trk-pop-key">${h(r.key)}</div><div class="trk-muted">Задача в проекте, куда у вас нет доступа.</div>`;
    } else {
        const d = r.due_date ? due({ due_date: r.due_date, status: '__x' }) : null;
        const dueTxt = r.due_date ? (r.status_kind === 'done' ? fmtDate(r.due_date) : d.text) : '';
        pop.innerHTML = `<div class="trk-pop-top"><span class="trk-mono">${h(r.key)} · ${h(r.project_name)}</span>
                <span class="trk-pop-st" style="--c:${ha(r.status_color || '#8A958F')}">${h(r.status_name)}</span></div>
            <div class="trk-pop-title">${h(r.title)}</div>
            <div class="trk-pop-meta">${r.assignee_name ? `<span><span class="material-icons-round">person</span>${h(r.assignee_name)}</span>` : '<span class="trk-muted">без исполнителя</span>'}
                ${dueTxt ? `<span class="${r.status_kind !== 'done' && d ? d.tone : ''}"><span class="material-icons-round">event</span>${h(dueTxt)}</span>` : ''}
                ${r.attachment_count ? `<span><span class="material-icons-round">attach_file</span>${r.attachment_count}</span>` : ''}</div>
            <div class="trk-pop-actions"><button class="trk-btn trk-btn-primary" data-pop="open">Открыть задачу</button><button class="trk-btn" data-pop="copy">Ссылка</button></div>`;
        pop.querySelector('[data-pop="open"]').onclick = () => { hidePop(); openProject(r.project_slug, r.task_id); };
        pop.querySelector('[data-pop="copy"]').onclick = () => { hidePop(); copyTaskLink(r.key); };
    }
    document.body.appendChild(pop);
    const b = anchor.getBoundingClientRect(), pw = pop.offsetWidth, ph = pop.offsetHeight;
    let left = Math.min(Math.max(8, b.left), window.innerWidth - pw - 8);
    let top = b.bottom + 6; if (top + ph > window.innerHeight - 8) top = Math.max(8, b.top - ph - 6);
    pop.style.left = left + 'px'; pop.style.top = top + 'px';
}
function hidePop() { const p = document.getElementById('trk-pop'); if (p) p.remove(); }

// ═════════════════════════════════════════════════════════════════
// Подключение
// ═════════════════════════════════════════════════════════════════
function init() {
    const root = document.getElementById('trk-root');
    if (root) {
        root.addEventListener('click', onClick);
        root.addEventListener('change', onChange);
        root.addEventListener('input', onInput);
        root.addEventListener('submit', onSubmit);
        root.addEventListener('keydown', onKey);
        root.addEventListener('dragstart', onDragStart);
        root.addEventListener('dragend', onDragEnd);
        root.addEventListener('dragover', onDragOver);
        root.addEventListener('dragleave', onDragLeave);
        root.addEventListener('drop', onDrop);
        root.addEventListener('focusout', e => { if (e.target.matches && e.target.matches('.trk-quick input')) { S.openAfterQuick = false; e.target.placeholder = canSubmit() ? '+ Оставить заявку' : '+ Добавить задачу'; } });
    }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') hidePop(); });
    document.addEventListener('click', e => {
        const ref = e.target.closest && e.target.closest('a.task-ref');
        if (ref) { e.preventDefault(); e.stopPropagation(); TaskRefs.open(ref); return; }
        if (!e.target.closest || !e.target.closest('#trk-pop')) hidePop();
    }, true);
    window.addEventListener('resize', () => { hidePop(); placeDrawer(); });
    document.addEventListener('scroll', hidePop, true);
    // Сообщения в ленту дорисовываются постоянно — ловим новые номера задач.
    new MutationObserver(muts => {
        for (const m of muts) for (const n of m.addedNodes) {
            if (n.nodeType === 1 && (n.matches('.task-ref') || n.querySelector('.task-ref:not([data-res])'))) { TaskRefs.scan(); return; }
        }
    }).observe(document.body, { childList: true, subtree: true });
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();

window.navigateToTasks = navigateToTasks;
window.handleTaskWsEvent = handleTaskWsEvent;
window.TaskRefs = TaskRefs;
window.Tracker = { openProject, openByKey, openTask, copyTaskLink };
})();
