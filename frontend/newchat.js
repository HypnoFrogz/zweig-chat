/* Zweig — окно «Новый канал» в том же виде, что «Новая папка» и экран
 * в мобильном приложении: название, публичный или закрытый, описание и
 * участники с поиском. Служебный «slug» больше не показываем — сервер
 * делает его сам из названия.
 */
(function () {
  'use strict';

  const L = {
    ru: {
      eyebrow: 'новый канал', title: 'Создайте канал', name: 'Название канала', desc: 'Описание (необязательно)',
      pub: 'Публичный', pubHint: 'виден всем, вступить может любой', priv: 'Закрытый', privHint: 'только приглашённые',
      who: 'кого позвать', picked: 'выбрано', search: 'Поиск людей', none: 'Никого не нашли',
      create: 'Создать канал', fail: 'Не удалось создать канал', server: 'сервер',
      people: (n) => { const a = n % 10, b = n % 100; return a === 1 && b !== 11 ? 'участник' : a >= 2 && a <= 4 && (b < 12 || b > 14) ? 'участника' : 'участников'; },
    },
    en: {
      eyebrow: 'new channel', title: 'Create a channel', name: 'Channel name', desc: 'Description (optional)',
      pub: 'Public', pubHint: 'visible to everyone, anyone can join', priv: 'Private', privHint: 'invited people only',
      who: 'who to invite', picked: 'selected', search: 'Search people', none: 'Nobody found',
      create: 'Create channel', fail: 'Could not create the channel', server: 'server',
      people: (n) => (n === 1 ? 'member' : 'members'),
    },
  };
  const tr = (k) => L[(typeof currentLang !== 'undefined' && currentLang === 'en') ? 'en' : 'ru'][k];
  const escH = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ico = (n) => `<span class="material-icons-round">${n}</span>`;

  let root = null;
  let st = null; // { type, picked:Set, query, saving }

  function build() {
    root = document.createElement('div');
    root.id = 'newchat-sheet';
    root.className = 'modal fsheet';
    root.style.display = 'none';
    root.innerHTML = `<div class="modal-backdrop"></div>
      <div class="fsheet-box" role="dialog" aria-modal="true">
        <button class="btn-icon fsheet-close" type="button" aria-label="Закрыть">${ico('close')}</button>
        <div class="fsheet-eyebrow"></div><h2 class="fsheet-title"></h2>
        <div class="fsheet-namerow"><div class="fsheet-preview"></div>
          <input type="text" class="fsheet-name nc-name" maxlength="60" autocomplete="off"></div>
        <div class="nc-types"></div>
        <input type="text" class="nc-desc" maxlength="200" autocomplete="off">
        <div class="fsheet-label"></div>
        <div class="fsheet-search">${ico('search')}<input type="text" autocomplete="off"><button type="button" class="btn-icon-xs fsheet-clear">${ico('close')}</button></div>
        <div class="fsheet-list"></div>
        <button type="button" class="btn-action btn-accent fsheet-submit"></button>
      </div>`;
    document.body.appendChild(root);
    root.querySelector('.modal-backdrop').onclick = close;
    root.querySelector('.fsheet-close').onclick = close;
    const name = root.querySelector('.nc-name');
    name.oninput = renderSubmit;
    name.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
    const search = root.querySelector('.fsheet-search input');
    search.oninput = () => { st.query = search.value.trim().toLowerCase(); renderList(); };
    root.querySelector('.fsheet-clear').onclick = () => { search.value = ''; st.query = ''; renderList(); search.focus(); };
    root.querySelector('.fsheet-submit').onclick = submit;
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && root.style.display !== 'none') close(); });
  }

  function close() { if (root) root.style.display = 'none'; }

  function renderTypes() {
    const opt = (v, icon, title, hint) => `<button type="button" class="nc-type${st.type === v ? ' on' : ''}" data-type="${v}">
      ${ico(icon)}<span><b>${escH(title)}</b><small>${escH(hint)}</small></span></button>`;
    root.querySelector('.nc-types').innerHTML = opt('public', 'tag', tr('pub'), tr('pubHint')) + opt('private', 'lock', tr('priv'), tr('privHint'));
    root.querySelectorAll('.nc-type').forEach((b) => { b.onclick = () => { st.type = b.dataset.type; renderTypes(); }; });
    root.querySelector('.fsheet-preview').innerHTML = ico(st.type === 'private' ? 'lock' : 'tag');
  }

  function people() {
    const list = typeof pickerCandidates === 'function' ? pickerCandidates() : allUsers.filter((u) => u.username !== currentUser);
    // Боты в конце списка: их тоже можно позвать в канал, но ищут обычно людей.
    return list.slice().sort((a, b) => ((a.role === 'bot') - (b.role === 'bot'))
      || (a.display_name || a.username).localeCompare(b.display_name || b.username, 'ru'));
  }

  function renderList() {
    const q = st.query;
    const list = people().filter((u) => !q || `${u.display_name || ''} ${u.username} ${u.nickname || ''} ${u.home_server || ''}`.toLowerCase().includes(q));
    const box = root.querySelector('.fsheet-list');
    if (!list.length) { box.innerHTML = `<div class="fsheet-empty">${escH(tr('none'))}</div>`; renderSubmit(); return; }
    box.innerHTML = list.map((u) => {
      const on = st.picked.has(u.username);
      const name = u.display_name || u.username;
      const av = u.avatar_path ? `<img src="${escH(srv(u.avatar_path))}" alt="">` : escH(name.charAt(0).toUpperCase());
      const sub = `@${escH(u.username)}${u.role === 'bot' ? ' · бот' : ''}${u.home_server ? ` · ${escH(tr('server'))} ${escH(u.home_server)}` : ''}`;
      return `<button type="button" class="fsheet-chat${on ? ' on' : ''}" data-u="${escH(u.username)}">
        <span class="fsheet-ava round">${av}</span>
        <span class="fsheet-cname"><b>${escH(name)}</b><small>${sub}</small></span>
        <span class="fsheet-check">${on ? ico('check') : ''}</span></button>`;
    }).join('');
    box.querySelectorAll('.fsheet-chat').forEach((b) => {
      b.onclick = () => {
        const u = b.dataset.u;
        if (st.picked.has(u)) st.picked.delete(u); else st.picked.add(u);
        renderList();
      };
    });
    renderSubmit();
  }

  function renderSubmit() {
    const n = st.picked.size;
    root.querySelector('.fsheet-label').textContent = `// ${tr('who')}${n ? ` · ${tr('picked')} ${n}` : ''}`;
    const btn = root.querySelector('.fsheet-submit');
    btn.textContent = n ? `${tr('create')} · ${n} ${tr('people')(n)}` : tr('create');
    btn.disabled = !root.querySelector('.nc-name').value.trim() || st.saving;
  }

  async function submit() {
    const name = root.querySelector('.nc-name').value.trim();
    if (!name || st.saving) return;
    st.saving = true;
    renderSubmit();
    try {
      const res = await apiFetch('/channels', {
        method: 'POST',
        body: { name, description: root.querySelector('.nc-desc').value.trim(), type: st.type, members: [...st.picked] },
      });
      if (!res || !res.ok) {
        let msg = tr('fail');
        try { const d = await res.json(); if (d && d.detail) msg = d.detail; } catch (_) { /* без подробностей */ }
        showToast(msg, 'error');
        return;
      }
      const ch = await res.json();
      close();
      await loadChannels();
      openChannel(ch.slug);
      showToast(t('toast.channelCreated'), 'success');
    } finally {
      st.saving = false;
      if (root.style.display !== 'none') renderSubmit();
    }
  }

  function open() {
    if (!root) build();
    st = { type: 'public', picked: new Set(), query: '', saving: false };
    root.querySelector('.fsheet-eyebrow').textContent = '// ' + tr('eyebrow');
    root.querySelector('.fsheet-title').textContent = tr('title');
    const name = root.querySelector('.nc-name');
    name.placeholder = tr('name');
    name.value = '';
    const desc = root.querySelector('.nc-desc');
    desc.placeholder = tr('desc');
    desc.value = '';
    const search = root.querySelector('.fsheet-search input');
    search.placeholder = tr('search');
    search.value = '';
    renderTypes();
    renderList();
    root.style.display = 'flex';
    setTimeout(() => name.focus(), 30);
  }

  window.showNewChatDialog = open;
  window.showCreateChannelDialog = open;
})();
