/* Zweig — «Важное» (колокольчик на рельсе) и модули сервера.
 *
 * Лента хранится на сервере, одна для всех устройств: назначили задачу,
 * срок, комментарий в моей задаче, мою задачу закрыли, меня упомянули.
 * Прочитали здесь — гаснет и в телефоне (событие notifications_read).
 *
 * Модули: сервер говорит, включён ли трекер (GET /api/features и событие
 * features_changed). Выключен — пропадает кнопка «Трекер», ссылки на задачи
 * ведут на заглушку, номер вида ЛЕС-24 в чате остаётся простым текстом.
 */
(function () {
  'use strict';

  const L = {
    ru: {
      title: 'Важное', readAll: 'Прочитать все', all: 'Все', tasks: 'Задачи', notices: 'Уведомления',
      empty: 'Здесь будут задачи, сроки и упоминания — всё, что касается лично вас.',
      emptyTasks: 'Событий по задачам пока нет.', emptyNotices: 'Здесь будут упоминания и пропущенные звонки.', missed: 'Пропущенный звонок', callBack: 'Перезвонить',
      assigned: 'назначил(а) вам задачу', comment: 'Новый комментарий в задаче', done: 'Задача закрыта',
      due: 'Срок сегодня', overdue: 'Срок прошёл', mention: 'упомянул(а) вас', inDm: 'в личных сообщениях',
      review: 'ждёт вашего решения:', returned: 'вернул(а) на доработку', approved: 'принял(а) задачу',
      approve: 'Принять', reject: 'Вернуть', rejectPh: 'Что поправить', send: 'Отправить', cancel: 'Отмена',
      decided: 'Готово', failed: 'Не получилось',
      moduleTitle: 'Трекер не подключён', moduleText: 'Задачи на этом сервере выключены. Проекты и задачи сохранены — они вернутся, когда администратор снова включит трекер.',
      moduleBack: 'К чатам', now: 'сейчас', min: 'мин', h: 'ч', yesterday: 'вчера', loading: 'Загрузка…',
    },
    en: {
      title: 'Inbox', readAll: 'Mark all read', all: 'All', tasks: 'Tasks', notices: 'Notifications',
      empty: 'Tasks, deadlines and mentions that concern you will show up here.',
      emptyTasks: 'No task events yet.', emptyNotices: 'Mentions and missed calls will show up here.', missed: 'Missed call', callBack: 'Call back',
      assigned: 'assigned you a task', comment: 'New comment on', done: 'Task closed',
      due: 'Due today', overdue: 'Overdue', mention: 'mentioned you', inDm: 'in a direct message',
      review: 'is waiting for your decision:', returned: 'sent back', approved: 'approved',
      approve: 'Approve', reject: 'Send back', rejectPh: 'What to fix', send: 'Send', cancel: 'Cancel',
      decided: 'Done', failed: 'Something went wrong',
      moduleTitle: 'Tracker is off', moduleText: 'Tasks are switched off on this server. Projects and tasks are kept and will come back when the administrator turns the tracker on again.',
      moduleBack: 'Back to chats', now: 'now', min: 'min', h: 'h', yesterday: 'yesterday', loading: 'Loading…',
    },
  };
  const tr = (k) => (L[((typeof currentLang !== 'undefined' && currentLang) || 'ru') === 'en' ? 'en' : 'ru'][k] || L.ru[k] || k);
  const $ = (id) => document.getElementById(id);
  const escH = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n) => `<span class="material-icons-round">${n}</span>`;

  let items = [];
  let unread = 0;
  let filter = 'all';
  let panel = null;
  let loading = false;

  // ── Модули ────────────────────────────────────────────────────
  window.zwFeatures = { tracker: { enabled: true } };
  const trackerOn = () => !(window.zwFeatures.tracker && window.zwFeatures.tracker.enabled === false);
  window.trackerEnabled = trackerOn;

  function applyFeatures() {
    const on = trackerOn();
    const btn = $('rail-tasks');
    if (btn) btn.style.display = on ? '' : 'none';
    if (!on && typeof currentPage !== 'undefined' && currentPage === 'tasks') showModuleOff();
    if (on && typeof currentPage !== 'undefined' && currentPage === 'module-off') origNavigateToTasks && origNavigateToTasks();
    // Уже показанные номера задач в переписке: выключен — гасим ссылки.
    document.querySelectorAll('a.task-ref').forEach((el) => el.classList.toggle('task-ref-off', !on));
    if (panel && panel.style.display !== 'none') renderPanel();
  }

  function showModuleOff() {
    let page = $('page-module-off');
    if (!page) {
      page = document.createElement('div');
      page.id = 'page-module-off';
      page.className = 'page';
      page.style.display = 'none';
      const tasks = $('page-tasks');
      (tasks ? tasks.parentNode : document.getElementById('main-area')).appendChild(page);
    }
    page.innerHTML = `<div class="mod-off"><div class="mod-off-ic">${icon('view_kanban')}</div>
      <h2>${escH(tr('moduleTitle'))}</h2><p>${escH(tr('moduleText'))}</p>
      <button class="btn-action btn-accent" type="button" onclick="selectFolder(typeof activeFolder!=='undefined'?activeFolder:'all')">${escH(tr('moduleBack'))}</button></div>`;
    showPage('module-off');
    const ti = $('topbar-channel-icon'); if (ti) ti.textContent = '';
    const tn = $('topbar-channel-name'); if (tn) tn.textContent = tr('moduleTitle');
    const td = $('topbar-channel-desc'); if (td) td.textContent = '';
  }

  let origNavigateToTasks = null;
  function wrapTracker() {
    if (origNavigateToTasks || typeof window.navigateToTasks !== 'function') return;
    origNavigateToTasks = window.navigateToTasks;
    window.navigateToTasks = function () {
      if (!trackerOn()) { showModuleOff(); return; }
      return origNavigateToTasks.apply(this, arguments);
    };
    if (window.Tracker) {
      ['openProject', 'openByKey', 'openTask'].forEach((k) => {
        const f = window.Tracker[k];
        window.Tracker[k] = function () {
          if (!trackerOn()) { showModuleOff(); return; }
          return f.apply(this, arguments);
        };
      });
    }
  }

  async function loadFeatures() {
    try {
      const res = await apiFetch('/features');
      if (res && res.ok) {
        const d = await res.json();
        window.zwFeatures = d.features || window.zwFeatures;
      }
    } catch (e) { /* старый сервер без модулей — трекер включён, как раньше */ }
    applyFeatures();
  }

  // ── Счётчик ───────────────────────────────────────────────────
  function setUnread(n) {
    unread = Math.max(0, n | 0);
    window.notifUnread = unread;
    const pip = $('rail-bell-pip');
    if (pip) {
      pip.style.display = unread ? '' : 'none';
      pip.textContent = unread > 99 ? '99+' : String(unread);
    }
    const bell = $('rail-bell');
    if (bell) bell.setAttribute('aria-label', unread ? `${tr('title')}: ${unread}` : tr('title'));
  }

  async function loadUnread() {
    try {
      const res = await apiFetch('/notifications/unread');
      if (res && res.ok) setUnread((await res.json()).unread);
    } catch (e) { /* сервер без ленты — колокольчик просто без цифры */ }
  }

  async function loadItems() {
    loading = true;
    try {
      const res = await apiFetch(`/notifications?kind=${filter}&limit=60`);
      if (res && res.ok) {
        const d = await res.json();
        items = d.items || [];
        setUnread(d.unread);
      }
    } finally { loading = false; }
    renderPanel();
  }

  // ── Панель ────────────────────────────────────────────────────
  function ago(iso) {
    const d = new Date(iso);
    const s = (Date.now() - d.getTime()) / 1000;
    if (s < 60) return tr('now');
    if (s < 3600) return `${Math.floor(s / 60)} ${tr('min')}`;
    if (s < 86400 && new Date().getDate() === d.getDate()) return `${Math.floor(s / 3600)} ${tr('h')}`;
    if (s < 172800) return tr('yesterday');
    return d.toLocaleDateString(currentLang === 'en' ? 'en-GB' : 'ru-RU', { day: 'numeric', month: 'short' });
  }

  const KIND = {
    task_assigned: { ic: 'assignment_ind', cls: 'mint' },
    task_comment: { ic: 'chat_bubble_outline', cls: 'mint' },
    task_done: { ic: 'task_alt', cls: 'gray' },
    task_due: { ic: 'schedule', cls: 'amber' },
    task_overdue: { ic: 'alarm', cls: 'coral' },
    task_review: { ic: 'rule', cls: 'amber' },
    task_returned: { ic: 'undo', cls: 'coral' },
    task_approved: { ic: 'verified', cls: 'mint' },
    mention: { ic: 'alternate_email', cls: 'blue' },
    missed_call: { ic: 'phone_missed', cls: 'coral' },
    module_expiring: { ic: 'hourglass_bottom', cls: 'amber' },
  };

  function itemHtml(n) {
    const k = KIND[n.kind] || { ic: 'notifications', cls: 'gray' };
    const who = `<b>${escH(n.actor_name || n.actor || '')}</b>`;
    const key = n.task_key ? `<span class="inb-key">${escH(n.task_key)}</span> ` : '';
    let text = '';
    let sub = '';
    switch (n.kind) {
      case 'task_assigned': text = `${who} ${escH(tr('assigned'))} ${key}${escH(n.title)}`; sub = n.body; break;
      case 'task_comment': text = `${escH(tr('comment'))} ${key}${escH(n.title)}`; sub = n.body; break;
      case 'task_done': text = `${escH(tr('done'))}: ${key}${escH(n.title)}`; sub = n.actor_name || ''; break;
      case 'task_due': text = `${escH(tr('due'))}: ${key}${escH(n.title)}`; sub = n.body; break;
      case 'task_overdue': text = `${escH(tr('overdue'))}: ${key}${escH(n.title)}`; sub = n.body; break;
      case 'task_review': text = `${key}${escH(n.title)} ${escH(tr('review'))} ${who}`; sub = n.body; break;
      case 'task_returned': text = `${who} ${escH(tr('returned'))} ${key}${escH(n.title)}`; sub = n.body; break;
      case 'task_approved': text = `${who} ${escH(tr('approved'))} ${key}${escH(n.title)}`; sub = n.body; break;
      case 'missed_call': text = `${escH(tr('missed'))}: ${who}`; sub = ''; break;
      case 'mention': text = `${who} ${escH(tr('mention'))} ${n.title ? `в <b># ${escH(n.title)}</b>` : escH(tr('inDm'))}`; sub = n.body; break;
      default: text = `<b>${escH(n.title)}</b>`; sub = n.body;
    }
    const acts = n.kind === 'task_review' && !n.read && n.project_slug
      ? `<div class="inb-acts" data-for="${escH(n.id)}">
          <button type="button" class="inb-btn ok" data-act="approve">${escH(tr('approve'))}</button>
          <button type="button" class="inb-btn" data-act="reject">${escH(tr('reject'))}</button></div>` : '';
    return `<div class="inb-row">${itemBtn(n, k, text, sub)}${acts}</div>`;
  }

  function itemBtn(n, k, text, sub) {
    return `<button type="button" class="inb-item${n.read ? '' : ' new'}" data-id="${escH(n.id)}">
      <span class="inb-ic ${k.cls}">${icon(k.ic)}</span>
      <span class="inb-txt"><span class="inb-t">${text}</span>${sub ? `<span class="inb-sub">${escH(sub)}</span>` : ''}<span class="inb-time">${escH(ago(n.created_at))}</span></span>
      ${n.read ? '' : '<span class="inb-dot"></span>'}
    </button>`;
  }

  function buildPanel() {
    panel = document.createElement('section');
    panel.id = 'inbox-panel';
    panel.className = 'inb';
    panel.setAttribute('aria-label', tr('title'));
    panel.style.display = 'none';
    document.body.appendChild(panel);
    panel.addEventListener('click', onPanelClick);
    document.addEventListener('mousedown', (e) => {
      if (!panel || panel.style.display === 'none') return;
      if (panel.contains(e.target) || ($('rail-bell') && $('rail-bell').contains(e.target))) return;
      closePanel();
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && panel && panel.style.display !== 'none') closePanel(); });
  }

  function renderPanel() {
    if (!panel) return;
    const tabs = [['all', tr('all')], ...(trackerOn() ? [['tasks', tr('tasks')]] : []), ['notices', tr('notices')]];
    if (!trackerOn() && filter === 'tasks') filter = 'all';
    const emptyText = filter === 'tasks' ? tr('emptyTasks') : filter === 'notices' ? tr('emptyNotices') : tr('empty');
    panel.innerHTML = `<div class="inb-head"><h3>${escH(tr('title'))}</h3>
        <button type="button" class="inb-readall" data-act="readall"${unread ? '' : ' disabled'}>${escH(tr('readAll'))}</button></div>
      <div class="inb-tabs">${tabs.map(([id, n]) => `<button type="button" class="inb-tab${filter === id ? ' on' : ''}" data-tab="${id}">${escH(n)}${id === 'all' && unread ? ` · ${unread}` : ''}</button>`).join('')}</div>
      <div class="inb-list">${items.length ? items.map(itemHtml).join('') : `<div class="inb-empty">${icon('notifications_none')}<p>${escH(loading ? tr('loading') : emptyText)}</p></div>`}</div>`;
  }

  function placePanel() {
    const bell = $('rail-bell');
    if (!bell || window.innerWidth <= 768) { panel.style.left = ''; panel.style.top = ''; panel.style.bottom = ''; return; }
    const r = bell.getBoundingClientRect();
    panel.style.left = (r.right + 12) + 'px';
    panel.style.top = '';
    panel.style.bottom = Math.max(12, window.innerHeight - r.bottom - 8) + 'px';
  }

  function openPanel() {
    if (!panel) buildPanel();
    panel.style.display = 'flex';
    placePanel();
    const bell = $('rail-bell'); if (bell) bell.classList.add('on');
    renderPanel();
    loadItems();
  }
  function closePanel() {
    if (panel) panel.style.display = 'none';
    const bell = $('rail-bell'); if (bell) bell.classList.remove('on');
  }
  window.toggleNotifPanel = function () {
    if (panel && panel.style.display !== 'none') closePanel(); else openPanel();
  };

  async function markRead(body) {
    try {
      const res = await apiFetch('/notifications/read', { method: 'POST', body });
      if (res && res.ok) setUnread((await res.json()).unread);
    } catch (e) { /* не страшно — отметится при следующем открытии */ }
  }

  async function onPanelClick(e) {
    const tab = e.target.closest('[data-tab]');
    if (tab) { filter = tab.dataset.tab; items = []; renderPanel(); loadItems(); return; }
    if (e.target.closest('[data-act="readall"]')) {
      items.forEach((n) => { n.read = true; });
      renderPanel();
      await markRead({ all: true });
      renderPanel();
      return;
    }
    const act = e.target.closest('.inb-acts [data-act]');
    if (act) { await onDecision(act); return; }
    const it = e.target.closest('.inb-item');
    if (!it) return;
    const n = items.find((x) => x.id === it.dataset.id);
    if (!n) return;
    if (!n.read) { n.read = true; markRead({ ids: [n.id] }); }
    closePanel();
    if (n.task_id && n.project_slug && window.Tracker) {
      showPage('tasks');
      window.Tracker.openProject(n.project_slug, n.task_id);
    } else if (n.channel_slug) {
      await openChannel(n.channel_slug);
      if (n.message_id && typeof scrollToMessage === 'function') setTimeout(() => scrollToMessage(n.message_id), 400);
    } else if (n.kind === 'module_expiring' && typeof navigateToAdmin === 'function') {
      navigateToAdmin();
    }
  }

  // ── Решение прямо из ленты: принять или вернуть с комментарием ──
  async function onDecision(btn) {
    const box = btn.closest('.inb-acts');
    const n = items.find((x) => x.id === box.dataset.for);
    if (!n) return;
    const what = btn.dataset.act;
    if (what === 'reject') {
      box.innerHTML = `<textarea class="inb-note" rows="2" placeholder="${escH(tr('rejectPh'))}"></textarea>
        <div class="inb-acts-row"><button type="button" class="inb-btn ok" data-act="reject-send">${escH(tr('send'))}</button>
        <button type="button" class="inb-btn" data-act="cancel">${escH(tr('cancel'))}</button></div>`;
      box.querySelector('textarea').focus();
      return;
    }
    if (what === 'cancel') { renderPanel(); return; }
    let body = {};
    if (what === 'reject-send') {
      const note = (box.querySelector('textarea').value || '').trim();
      if (!note) { box.querySelector('textarea').focus(); return; }
      body = { comment: note };
    }
    const path = what === 'approve' ? 'approve' : 'reject';
    box.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    try {
      const r = await apiFetch(`/tasks/projects/${encodeURIComponent(n.project_slug)}/tasks/${encodeURIComponent(n.task_id)}/${path}`, { method: 'POST', body });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.status);
      n.read = true;
      box.innerHTML = `<span class="inb-done">${icon('check')} ${escH(tr('decided'))}</span>`;
      setTimeout(renderPanel, 1200);
    } catch (err) {
      box.innerHTML = `<span class="inb-done err">${escH(String(err.message || tr('failed')))}</span>`;
      setTimeout(renderPanel, 2500);
    }
  }

  // ── События с сервера ─────────────────────────────────────────
  window.handleInboxWsEvent = function (data) {
    if (data.event === 'notification') {
      setUnread(data.unread);
      if (panel && panel.style.display !== 'none') {
        const k = data.item && data.item.kind;
        const fits = filter === 'all' || (filter === 'notices' && (k === 'mention' || k === 'missed_call')) || (filter === 'tasks' && k && k.startsWith('task_'));
        if (fits) { items.unshift(data.item); renderPanel(); }
      }
    } else if (data.event === 'notifications_read') {
      setUnread(data.unread);
      if (panel && panel.style.display !== 'none') loadItems();
    } else if (data.event === 'features_changed') {
      window.zwFeatures = data.features || window.zwFeatures;
      applyFeatures();
      if (trackerOn() && window.TaskRefs && window.TaskRefs.invalidate) window.TaskRefs.invalidate();
    }
  };

  // Открыли чат — упоминания в нём прочитаны. Открыли задачу — её события тоже.
  function wrapOpeners() {
    if (typeof window.openChannel === 'function' && !window.openChannel.__inb) {
      const orig = window.openChannel;
      window.openChannel = async function (slug) {
        const r = await orig.apply(this, arguments);
        if (unread) markRead({ channel_slug: slug });
        return r;
      };
      window.openChannel.__inb = true;
    }
    if (window.Tracker && window.Tracker.openTask && !window.Tracker.openTask.__inb) {
      const o = window.Tracker.openTask;
      window.Tracker.openTask = function (id) {
        if (unread && id) markRead({ task_id: id });
        return o.apply(this, arguments);
      };
      window.Tracker.openTask.__inb = true;
    }
  }

  window.initInbox = function () {
    wrapTracker();
    wrapOpeners();
    loadFeatures();
    loadUnread();
  };
  window.addEventListener('resize', () => { if (panel && panel.style.display !== 'none') placePanel(); });
})();
