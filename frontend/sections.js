/* Zweig — окна с разделами: «Аккаунт и настройки» и «Настройки канала».
 *
 * Одно окно вместо россыпи маленьких: слева разделы, справа содержимое.
 * Аватар в боковой панели открывает его на «Профиле», шестерёнка — на
 * «Оформлении». Настройки канала устроены так же.
 *
 * Содержимое старых окон (профиль, устройства, 2FA, настройки канала) не
 * переписано заново: их узлы переносятся в нужный раздел, поэтому вся
 * существующая логика (saveProfile, loadSessions, openTwofa…) работает как была.
 */
(function () {
  'use strict';

  const L = {
    ru: {
      account: 'аккаунт', app: 'приложение', security: 'безопасность', server: 'сервер', help: 'помощь',
      profile: 'Профиль', appearance: 'Оформление', notifications: 'Уведомления', devices: 'Устройства',
      secTitle: 'Безопасность', integrations: 'Интеграции', federation: 'Связи с серверами', admin: 'Администрирование',
      about: 'О приложении', logout: 'Выйти', save: 'Сохранить', password: 'Пароль', changePw: 'Сменить пароль', newPw: 'Новый пароль',
      pwHint: 'Новый пароль для входа в Zweig. Остальные устройства останутся в аккаунте.',
      twofa: 'Двухфакторная защита', delAcc: 'Удаление аккаунта',
      delHint: 'Аккаунт и личные данные удаляются навсегда. Ваши сообщения в общих каналах станут анонимными.',
      notifBrowser: 'Уведомления браузера', notifOn: 'включены', notifOff: 'выключены', notifAsk: 'Разрешить',
      notifBlocked: 'запрещены в настройках браузера — разрешите их для этого сайта в адресной строке',
      notifHint: 'Всплывающие уведомления о новых сообщениях и звонках, когда вкладка в фоне. Отдельный канал можно заглушить в его настройках.',
      intHint: 'Отправляйте сообщения в Zweig из CI, мониторинга и CRM или подключите своего бота.',
      intSoon: 'Скоро: вебхук в личку — уведомления лично себе, без канала.',
      intDocs: 'Документация API', intAdmin: 'Вебхуки и боты для каналов', open: 'Открыть',
      fedHint: 'Переписка и звонки с сотрудниками других организаций на своих серверах Zweig.',
      // канал
      chBasic: 'Основное', chMembers: 'Участники', chNotif: 'Уведомления', chInt: 'Интеграции', chDanger: 'Опасная зона',
      chChannel: 'канал', chAdd: 'Пригласить', chMute: 'Без звука', chMuteHint: 'Новые сообщения из этого канала не будут звенеть и всплывать.',
      chHookNew: 'новый вебхук', chHookName: 'Откуда, напр. GitLab CI', chHookBot: 'Общий бот «Вебхук»', chHookCreate: 'Создать адрес',
      chHookHint: 'Внешний сервис отправляет POST с {"text": "…"} — сообщение появляется в этом канале.',
      chHooks: 'подключены', chHooksNone: 'Вебхуков в этом канале пока нет', chHookDel: 'Удалить вебхук «{name}»?',
      chIntNoRights: 'Вебхуки и ботов для канала пока подключает администратор сервера. Скоро это сможет делать и владелец канала.',
      chHookUrl: 'Адрес вебхука', chHookOnce: 'Сохраните адрес: второй раз он не показывается. Адрес работает как пароль.',
      chLeave: 'Покинуть канал', chDelete: 'Удалить канал', chResync: 'Синхронизировать с серверами',
      chDangerHint: 'Удаление нельзя отменить: канал и вся переписка исчезнут у всех участников.',
    },
    en: {
      account: 'account', app: 'app', security: 'security', server: 'server', help: 'help',
      profile: 'Profile', appearance: 'Appearance', notifications: 'Notifications', devices: 'Devices',
      secTitle: 'Security', integrations: 'Integrations', federation: 'Linked servers', admin: 'Administration',
      about: 'About', logout: 'Sign out', save: 'Save', password: 'Password', changePw: 'Change password', newPw: 'New password',
      pwHint: 'A new password for signing in to Zweig. Your other devices stay signed in.',
      twofa: 'Two-factor authentication', delAcc: 'Delete account',
      delHint: 'Your account and personal data are deleted permanently. Your messages in shared channels become anonymous.',
      notifBrowser: 'Browser notifications', notifOn: 'on', notifOff: 'off', notifAsk: 'Allow',
      notifBlocked: 'blocked in browser settings — allow them for this site from the address bar',
      notifHint: 'Pop-up notifications about new messages and calls while the tab is in the background. Mute a single channel in its settings.',
      intHint: 'Post to Zweig from CI, monitoring and CRM, or connect your own bot.',
      intSoon: 'Coming soon: a webhook to your direct messages — notifications just for you.',
      intDocs: 'API documentation', intAdmin: 'Channel webhooks and bots', open: 'Open',
      fedHint: 'Messages and calls with people at other organizations on their own Zweig servers.',
      chBasic: 'General', chMembers: 'Members', chNotif: 'Notifications', chInt: 'Integrations', chDanger: 'Danger zone',
      chChannel: 'channel', chAdd: 'Invite', chMute: 'Mute', chMuteHint: 'New messages in this channel won’t ring or pop up.',
      chHookNew: 'new webhook', chHookName: 'Source, e.g. GitLab CI', chHookBot: 'Shared “Webhook” bot', chHookCreate: 'Create URL',
      chHookHint: 'An external service sends POST with {"text": "…"} and the message appears in this channel.',
      chHooks: 'connected', chHooksNone: 'No webhooks in this channel yet', chHookDel: 'Delete webhook “{name}”?',
      chIntNoRights: 'For now, channel webhooks and bots are set up by the server administrator. Channel owners will be able to do it soon.',
      chHookUrl: 'Webhook URL', chHookOnce: 'Save the URL: it is shown only once. It works like a password.',
      chLeave: 'Leave channel', chDelete: 'Delete channel', chResync: 'Sync with servers',
      chDangerHint: 'Deleting cannot be undone: the channel and its history disappear for every member.',
    },
  };
  const tr = (k) => (L[((typeof currentLang !== 'undefined' && currentLang) || 'ru') === 'en' ? 'en' : 'ru'][k] || L.ru[k] || k);
  const $ = (id) => document.getElementById(id);
  const escH = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (n) => `<span class="material-icons-round">${n}</span>`;

  /* ── Общий каркас окна с разделами ───────────────────────────── */
  function makeShell(id, onClose) {
    const root = document.createElement('div');
    root.id = id;
    root.className = 'modal secm';
    root.style.display = 'none';
    root.innerHTML = `<div class="modal-backdrop"></div>
      <div class="secm-box" role="dialog" aria-modal="true">
        <aside class="secm-nav"><div class="secm-head"></div><nav class="secm-items"></nav><div class="secm-foot"></div></aside>
        <section class="secm-main">
          <button class="btn-icon secm-close" type="button" aria-label="Закрыть">${icon('close')}</button>
          <button class="btn-icon secm-back" type="button" aria-label="Назад">${icon('arrow_back')}</button>
          <div class="secm-eyebrow"></div><h2 class="secm-title"></h2>
          <div class="secm-panes"></div>
        </section>
      </div>`;
    document.body.appendChild(root);
    root.querySelector('.modal-backdrop').onclick = onClose;
    root.querySelector('.secm-close').onclick = onClose;
    // узкий экран: сначала список разделов, раздел открывается поверх
    root.querySelector('.secm-back').onclick = () => root.classList.remove('secm-open');
    return root;
  }

  function renderNav(root, sections, active, onPick) {
    const items = root.querySelector('.secm-items');
    let html = '', group = null;
    sections.filter((s) => !s.cond || s.cond()).forEach((s) => {
      if (s.group !== group) { group = s.group; html += `<div class="secm-group">// ${escH(tr(s.group))}</div>`; }
      html += `<button type="button" class="secm-item${s.id === active ? ' active' : ''}${s.danger ? ' danger' : ''}" data-sec="${s.id}">
        ${icon(s.icon)}<span>${escH(tr(s.title))}</span>${s.badge ? `<small>${escH(s.badge())}</small>` : ''}</button>`;
    });
    items.innerHTML = html;
    items.querySelectorAll('.secm-item').forEach((b) => { b.onclick = () => onPick(b.dataset.sec); });
  }

  function showPane(root, sections, id) {
    const s = sections.find((x) => x.id === id);
    root.querySelector('.secm-eyebrow').textContent = '// ' + tr(s.group);
    root.querySelector('.secm-title').textContent = tr(s.title);
    root.querySelectorAll('.secm-pane').forEach((p) => { p.hidden = p.dataset.sec !== id; });
    root.querySelector('.secm-main').scrollTop = 0;
    root.classList.add('secm-open');
  }

  function pane(root, id) {
    let p = root.querySelector(`.secm-pane[data-sec="${id}"]`);
    if (!p) {
      p = document.createElement('div');
      p.className = 'secm-pane';
      p.dataset.sec = id;
      p.hidden = true;
      root.querySelector('.secm-panes').appendChild(p);
    }
    return p;
  }

  // Перенос узла в другое место. Узлы с id, обработчиками и состоянием
  // переезжают целиком — старые функции их найдут там, где они теперь.
  function relocate(node, parent) {
    if (node && parent && node.parentElement !== parent) parent.appendChild(node);
  }

  /* ── Аккаунт и настройки ─────────────────────────────────────── */
  let acct = null;
  let acctSec = 'profile';
  const ACCT = [
    { id: 'profile', group: 'account', icon: 'person', title: 'profile' },
    { id: 'appearance', group: 'app', icon: 'palette', title: 'appearance' },
    { id: 'notifications', group: 'app', icon: 'notifications', title: 'notifications' },
    { id: 'devices', group: 'security', icon: 'devices', title: 'devices' },
    { id: 'security', group: 'security', icon: 'shield', title: 'secTitle', badge: () => (twofaState && twofaState.enabled ? '2FA' : '') },
    { id: 'integrations', group: 'security', icon: 'webhook', title: 'integrations' },
    { id: 'federation', group: 'server', icon: 'hub', title: 'federation', cond: () => { const r = $('settings-federation-row'); return r && r.style.display !== 'none'; } },
    { id: 'admin', group: 'server', icon: 'admin_panel_settings', title: 'admin', cond: () => userRole === 'admin' },
    { id: 'about', group: 'help', icon: 'help_outline', title: 'about' },
  ];

  function closeAccount() { if (acct) acct.style.display = 'none'; }

  function buildAccount() {
    acct = makeShell('acct-modal', closeAccount);
    acct.querySelector('.secm-foot').innerHTML = `<button type="button" class="secm-item danger" id="acct-logout">${icon('logout')}<span>${escH(tr('logout'))}</span></button>`;
    $('acct-logout').onclick = () => { closeAccount(); window.logout(); };

    // Профиль: тело старого окна профиля + кнопка «Сохранить»
    const prof = pane(acct, 'profile');
    const pBody = document.querySelector('#profile-dialog .modal-body');
    relocate(pBody, prof);
    const pwGroup = $('profile-password') && $('profile-password').closest('.input-group');
    prof.insertAdjacentHTML('beforeend', `<div class="secm-actions"><button class="btn-action btn-accent" type="button" id="acct-save-profile">${escH(tr('save'))}</button></div>`);
    $('acct-save-profile').onclick = () => window.saveProfile();

    // Оформление: переключатели темы и языка из старого меню
    const app = pane(acct, 'appearance');
    app.innerHTML = '<div class="secm-card secm-pad" id="acct-appearance"></div>';
    document.querySelectorAll('#settings-menu .settings-field').forEach((f) => relocate(f, $('acct-appearance')));

    // Уведомления браузера
    pane(acct, 'notifications');

    // Устройства — тело старого окна переносится при открытии (см. openAccount)
    pane(acct, 'devices');

    // Безопасность: пароль, 2FA, удаление аккаунта
    const sec = pane(acct, 'security');
    sec.innerHTML = `<div class="secm-label">// ${escH(tr('password'))}</div>
      <div class="secm-card secm-pad" id="acct-pw"><p class="secm-hint">${escH(tr('pwHint'))}</p></div>
      <div class="secm-label">// ${escH(tr('twofa'))}</div><div class="secm-card secm-pad" id="acct-2fa"></div>
      <div class="secm-label">// ${escH(tr('delAcc'))}</div>
      <div class="secm-card secm-pad"><p class="secm-hint">${escH(tr('delHint'))}</p>
        <button class="btn-action btn-danger-outline" type="button" id="acct-del">${icon('delete_forever')}${escH(tr('delAcc'))}</button></div>`;
    if (pwGroup) {
      relocate(pwGroup, $('acct-pw'));
      const pw = $('profile-password');
      pw.removeAttribute('data-i18n-placeholder');
      pw.placeholder = tr('newPw');
      pw.autocomplete = 'new-password';
      $('acct-pw').insertAdjacentHTML('beforeend', `<button class="btn-action" type="button" id="acct-save-pw">${escH(tr('changePw'))}</button>`);
      $('acct-save-pw').onclick = () => window.saveProfile();
    }
    relocate($('twofa-body'), $('acct-2fa'));
    $('acct-del').onclick = () => window.openDeleteAccount();

    // Интеграции
    pane(acct, 'integrations').innerHTML = `<div class="secm-card secm-pad"><p class="secm-hint" style="margin-top:0">${escH(tr('intHint'))}</p>
      <div class="secm-row">${icon('description')}<span>${escH(tr('intDocs'))}</span><a class="btn-action" href="https://zweig.services/api-docs" target="_blank" rel="noopener">${escH(tr('open'))} ${icon('open_in_new')}</a></div>
      <div class="secm-row" id="acct-int-admin">${icon('webhook')}<span>${escH(tr('intAdmin'))}</span><button class="btn-action" type="button" id="acct-int-open">${escH(tr('open'))}</button></div>
      <p class="secm-soon">${escH(tr('intSoon'))}</p></div>`;
    $('acct-int-open').onclick = () => { closeAccount(); window.navigateToAdmin().then(() => window.switchAdminTab && window.switchAdminTab('bots')); };

    // Федерация
    pane(acct, 'federation').innerHTML = `<div class="secm-card secm-pad"><p class="secm-hint" style="margin-top:0">${escH(tr('fedHint'))}</p>
      <button class="btn-action btn-accent" type="button" id="acct-fed">${icon('hub')}${escH(tr('open'))}</button></div>`;
    $('acct-fed').onclick = () => window.openFederationDialog();

    // О приложении: ссылки на документы и поддержку
    const about = pane(acct, 'about');
    about.innerHTML = '<div class="secm-card" id="acct-about"></div>';
    document.querySelectorAll('#settings-menu a.settings-item').forEach((a) => relocate(a, $('acct-about')));
  }

  function renderNotifPane() {
    const p = pane(acct, 'notifications');
    const has = 'Notification' in window;
    const perm = has ? Notification.permission : 'denied';
    const state = perm === 'granted' ? tr('notifOn') : perm === 'denied' ? tr('notifBlocked') : tr('notifOff');
    p.innerHTML = `<div class="secm-card secm-pad">
      <div class="secm-row">${icon('notifications_active')}<span>${escH(tr('notifBrowser'))}<small>${escH(state)}</small></span>
      ${perm === 'default' ? `<button class="btn-action btn-accent" type="button" id="acct-notif">${escH(tr('notifAsk'))}</button>` : ''}</div>
      <p class="secm-hint">${escH(tr('notifHint'))}</p></div>`;
    const b = $('acct-notif');
    if (b) b.onclick = async () => { try { await Notification.requestPermission(); } catch (_) {} renderNotifPane(); };
  }

  function pickAccount(id) {
    if (id === 'admin') { closeAccount(); window.navigateToAdmin(); return; }
    acctSec = id;
    renderNav(acct, ACCT, id, pickAccount);
    if (id === 'profile') loadProfile();
    if (id === 'notifications') renderNotifPane();
    if (id === 'devices') {
      // старая функция настроит заголовки «свои устройства» и загрузит список
      origOpenSessions();
      const sm = $('sessions-modal');
      if (sm) sm.style.display = 'none';
      relocate(sm && sm.querySelector('.modal-body'), pane(acct, 'devices'));
    }
    if (id === 'security') {
      origOpenTwofa();
      const m = $('twofa-modal');
      if (m) m.style.display = 'none';
    }
    showPane(acct, ACCT, id);
  }

  async function loadProfile() {
    origShowProfile();
    const d = $('profile-dialog');
    if (d) d.style.display = 'none';
    // шапка навигации — кто вошёл
    const head = acct.querySelector('.secm-head');
    const name = displayName || currentUser || '';
    const av = currentUserAvatar ? `<img src="${escH(window.srv(currentUserAvatar))}" alt="">` : escH(name.charAt(0).toUpperCase());
    head.innerHTML = `<div class="secm-ava">${av}</div><div class="secm-who"><b>${escH(name)}</b><small>@${escH(currentUser || '')}</small></div>`;
  }

  function openAccount(id) {
    if (!acct) buildAccount();
    window.closeSettingsMenuOld();
    acct.style.display = 'flex';
    acct.classList.toggle('secm-open', window.innerWidth > 768);
    loadProfile();
    if (window.refreshTwofaState) window.refreshTwofaState();
    pickAccount(id || acctSec);
    if (window.innerWidth <= 768 && !id) acct.classList.remove('secm-open');
  }

  /* ── Настройки канала ────────────────────────────────────────── */
  let chm = null;
  const CH = [
    { id: 'basic', group: 'chChannel', icon: 'info', title: 'chBasic' },
    { id: 'members', group: 'chChannel', icon: 'group', title: 'chMembers', badge: () => String((currentChannel && (currentChannel.member_details || currentChannel.members || []).length) || '') },
    { id: 'notif', group: 'chChannel', icon: 'notifications', title: 'chNotif' },
    { id: 'integrations', group: 'chChannel', icon: 'webhook', title: 'chInt' },
    { id: 'danger', group: 'chChannel', icon: 'warning', title: 'chDanger', danger: true },
  ];

  function closeChannel() { if (chm) chm.style.display = 'none'; }

  function buildChannel() {
    chm = makeShell('chset-modal', closeChannel);
    const basic = pane(chm, 'basic');
    relocate(document.querySelector('#channel-settings-dialog .modal-body'), basic);
    basic.insertAdjacentHTML('beforeend', `<div class="secm-actions"><button class="btn-action btn-accent" type="button" id="chset-save">${escH(tr('save'))}</button></div>`);
    $('chset-save').onclick = () => window.saveChannelSettings();
    // Удалить/покинуть и пересинхронизация — в «Опасной зоне»
    const danger = pane(chm, 'danger');
    danger.innerHTML = `<div class="secm-card secm-pad" id="chset-danger"><p class="secm-hint" style="margin-top:0">${escH(tr('chDangerHint'))}</p><div class="secm-btns" id="chset-danger-btns"></div></div>`;
    ['ch-settings-leave-btn', 'ch-settings-delete-btn'].forEach((bid) => relocate($(bid), $('chset-danger-btns')));
    relocate($('ch-settings-resync-row'), $('chset-danger'));
    pane(chm, 'members'); pane(chm, 'notif'); pane(chm, 'integrations');
  }

  function chHead() {
    const ch = currentChannel;
    const head = chm.querySelector('.secm-head');
    const av = ch.avatar_path ? `<img src="${escH(window.srv(ch.avatar_path))}" alt="">` : icon(ch.type === 'private' ? 'lock' : 'tag');
    const n = (ch.member_details || ch.members || []).length;
    head.innerHTML = `<div class="secm-ava secm-ava-ch">${av}</div><div class="secm-who"><b>${escH(ch.name || ch.slug)}</b><small>${escH(ch.type === 'private' ? 'закрытый' : 'публичный')}${n ? ' · ' + n : ''}</small></div>`;
  }

  async function renderMembers() {
    const ch = currentChannel;
    const p = pane(chm, 'members');
    if (!ch.member_details || !ch.member_details.length) {
      const res = await window.apiFetch(`/channels/${ch.slug}/members`);
      if (res && res.ok) ch.member_details = await res.json();
    }
    const list = ch.member_details || [];
    const me = list.find((m) => m.username === currentUser);
    const myRole = me ? me.role : 'member';
    const canKick = myRole === 'owner' || myRole === 'admin' || userRole === 'admin';
    p.innerHTML = (canKick ? `<div class="secm-actions" style="margin-top:0;margin-bottom:12px"><button class="btn-action btn-accent" type="button" id="chset-add">${icon('person_add')}${escH(tr('chAdd'))}</button></div>` : '') +
      `<div class="secm-card">${list.map((m) => {
        const av = m.avatar_path ? `<img src="${escH(window.srv(m.avatar_path))}" alt="">` : escH((m.display_name || m.username).charAt(0).toUpperCase());
        const role = m.role === 'owner' || m.role === 'admin' ? `<small class="secm-tag">${m.role === 'owner' ? 'владелец' : 'админ'}</small>` : '';
        const kick = canKick && m.role !== 'owner' && m.username !== currentUser
          ? `<button class="btn-icon" type="button" data-kick="${escH(m.username)}" title="Исключить">${icon('person_remove')}</button>` : '';
        return `<div class="secm-member"><div class="secm-mava">${av}</div><div class="secm-mname"><b>${escH(m.display_name || m.username)}</b><small>@${escH(m.username)}${m.home_server ? ' · ' + escH(m.home_server) : ''}</small></div>${role}${kick}</div>`;
      }).join('')}</div>`;
    const add = $('chset-add');
    if (add) add.onclick = () => window.showAddMembersModal();
    p.querySelectorAll('[data-kick]').forEach((b) => {
      b.onclick = async () => { await window.kickMember(b.dataset.kick); ch.member_details = null; renderMembers(); };
    });
  }

  function renderNotif() {
    const ch = currentChannel;
    const muted = window.isChannelMuted(ch);
    pane(chm, 'notif').innerHTML = `<div class="secm-card secm-pad"><label class="secm-row secm-switch">${icon('notifications_off')}<span>${escH(tr('chMute'))}<small>${escH(tr('chMuteHint'))}</small></span>
      <input type="checkbox" id="chset-mute" ${muted ? 'checked' : ''}><i></i></label></div>`;
    $('chset-mute').onchange = async (e) => {
      if (e.target.checked) await window.muteChannel(ch.slug); else await window.unmuteChannel(ch.slug);
      const fresh = (channels || []).find((c) => c.slug === ch.slug);
      if (fresh) Object.assign(ch, { muted_until: fresh.muted_until });
    };
  }

  async function renderIntegrations() {
    const ch = currentChannel;
    const p = pane(chm, 'integrations');
    if (userRole !== 'admin') {
      p.innerHTML = `<div class="secm-card secm-pad"><p class="secm-hint" style="margin-top:0">${escH(tr('chIntNoRights'))}</p>
        <a class="btn-action" href="https://zweig.services/api-docs" target="_blank" rel="noopener">${escH(tr('intDocs'))} ${icon('open_in_new')}</a></div>`;
      return;
    }
    let hooks = [], bots = [];
    try {
      const [h, b] = await Promise.all([window.apiFetch('/admin/webhooks'), window.apiFetch('/admin/bots')]);
      if (h && h.ok) hooks = (await h.json()).filter((x) => x.channel_slug === ch.slug);
      if (b && b.ok) bots = await b.json();
    } catch (_) { /* покажем пустой список */ }
    p.innerHTML = `<div class="secm-label" style="margin-top:0">// ${escH(tr('chHookNew'))}</div>
      <div class="secm-card secm-pad"><div class="secm-form">
        <div class="input-group"><span class="material-icons-round input-icon">label</span><input type="text" id="chset-hook-name" placeholder="${escH(tr('chHookName'))}" autocomplete="off"></div>
        <select id="chset-hook-bot" class="admin-select"><option value="">${escH(tr('chHookBot'))}</option>${bots.filter((x) => !x.blocked).map((x) => `<option value="${escH(x.username)}">${escH(x.display_name)} (@${escH(x.username)})</option>`).join('')}</select>
        <button class="btn-action btn-accent" type="button" id="chset-hook-create">${icon('add_link')}${escH(tr('chHookCreate'))}</button></div>
        <p class="secm-hint">${escH(tr('chHookHint'))} <a href="https://zweig.services/api-docs" target="_blank" rel="noopener">${escH(tr('intDocs'))} →</a></p>
        <div id="chset-hook-secret"></div></div>
      <div class="secm-label">// ${escH(tr('chHooks'))} · ${hooks.length}</div>
      <div class="secm-card">${hooks.length ? hooks.map((x) => `<div class="secm-member"><div class="secm-mava secm-hook">${icon('webhook')}</div><div class="secm-mname"><b>${escH(x.name || 'Вебхук')}</b><small>@${escH(x.bot)} · ${escH(new Date(x.created_at).toLocaleDateString())}</small></div><button class="btn-icon" type="button" data-hook="${escH(x.id)}" data-name="${escH(x.name || '')}" title="Удалить">${icon('delete')}</button></div>`).join('') : `<div class="secm-empty">${escH(tr('chHooksNone'))}</div>`}</div>`;
    $('chset-hook-create').onclick = async () => {
      const name = $('chset-hook-name').value.trim();
      const bot = $('chset-hook-bot').value;
      const res = await window.apiFetch('/admin/webhooks', { method: 'POST', body: { channel_slug: ch.slug, bot, name } });
      if (!res) return;
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { window.showToast(data.detail || 'Не удалось создать вебхук', 'error'); return; }
      const url = (window.getServerBase() || location.origin) + data.path;
      await renderIntegrations();
      $('chset-hook-secret').innerHTML = `<div class="secm-secret"><b>${escH(tr('chHookUrl'))}</b><code>${escH(url)}</code><small>${escH(tr('chHookOnce'))}</small></div>`;
    };
    p.querySelectorAll('[data-hook]').forEach((b) => {
      b.onclick = async () => {
        if (!confirm(tr('chHookDel').replace('{name}', b.dataset.name || 'Вебхук'))) return;
        await window.apiFetch(`/admin/webhooks/${b.dataset.hook}`, { method: 'DELETE' });
        renderIntegrations();
      };
    });
  }

  let chSec = 'basic';
  function pickChannel(id) {
    chSec = id;
    renderNav(chm, CH, id, pickChannel);
    if (id === 'members') renderMembers();
    if (id === 'notif') renderNotif();
    if (id === 'integrations') renderIntegrations();
    showPane(chm, CH, id);
  }

  function openChannelSections(id) {
    const ch = currentChannel;
    if (!ch || ch.type === 'direct') return;
    if (!chm) buildChannel();
    origShowChannelSettings();           // заполнит поля и права кнопок
    const old = $('channel-settings-dialog');
    if (old) old.style.display = 'none';
    chHead();
    chm.style.display = 'flex';
    chm.classList.add('secm-open');
    pickChannel(id || 'basic');
  }

  /* ── Подмена точек входа ─────────────────────────────────────── */
  const origShowProfile = window.showProfileDialog;
  const origOpenTwofa = window.openTwofa;
  const origOpenSessions = window.openSessions;
  const origShowChannelSettings = window.showChannelSettingsDialog;
  const origCloseSettings = window.closeSettingsMenu;
  const origCloseChannel = window.closeChannelSettingsDialog;
  window.closeSettingsMenuOld = origCloseSettings;

  window.showSettingsMenu = () => openAccount('appearance');
  window.showProfileDialog = () => openAccount('profile');
  window.openTwofa = () => openAccount('security');
  window.openSessions = function (target, name) {
    if (target) {
      // админ смотрит чужие устройства — в отдельном окне, как раньше
      const body = document.querySelector('#acct-modal .secm-pane[data-sec="devices"] > .modal-body');
      const hdr = document.querySelector('#sessions-modal .modal-header');
      if (body && hdr) hdr.after(body);
      return origOpenSessions(target, name);
    }
    openAccount('devices');
  };
  // Старые функции закрывают меню настроек перед своим окном — новое окно
  // аккаунта при этом остаётся, а закрывается только при уходе в админку.
  window.closeSettingsMenu = function () { origCloseSettings(); };
  window.showChannelSettingsDialog = () => openChannelSections('basic');
  window.closeChannelSettingsDialog = function () { origCloseChannel(); closeChannel(); };
  window.openAccountSettings = openAccount;
  window.closeAccountSettings = closeAccount;
  window.openChannelSections = openChannelSections;

  // Сохранение профиля закрывало старое окно; новое оставляем открытым.
  // Уход в админку закрывает окно аккаунта.
  const origNavAdmin = window.navigateToAdmin;
  window.navigateToAdmin = function () { closeAccount(); return origNavAdmin.apply(this, arguments); };

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (chm && chm.style.display !== 'none') closeChannel();
    if (acct && acct.style.display !== 'none') closeAccount();
  });
})();
