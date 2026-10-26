/* Zweig — окно «Новая папка» / «Изменить папку», как в мобильном приложении:
 * название, одноцветный значок и сразу выбор чатов с поиском.
 *
 * Значки — Material Icons Round. Ключ значка хранится на сервере строкой
 * (chat_folders.icon) и совпадает с набором в mobile/lib/widgets/folder_sheet.dart,
 * поэтому папка выглядит одинаково в вебе, на компьютере и на телефоне.
 */
(function () {
  'use strict';

  const ICONS = [
    'work', 'business_center', 'apartment', 'badge', 'assignment', 'task_alt',
    'groups', 'handshake', 'support_agent', 'person', 'diversity_3', 'family_restroom',
    'rocket_launch', 'lightbulb', 'flag', 'bolt', 'star', 'bookmark',
    'local_shipping', 'inventory_2', 'warehouse', 'factory', 'construction', 'build',
    'payments', 'receipt_long', 'account_balance', 'savings', 'request_quote', 'shopping_cart',
    'forum', 'campaign', 'notifications', 'mail', 'call', 'hub',
    'home', 'school', 'favorite', 'pets', 'sports_esports', 'flight',
  ];
  const QUICK = ['inventory_2', 'local_shipping', 'warehouse', 'work', 'groups', 'handshake',
    'payments', 'star', 'rocket_launch', 'forum', 'home'];

  const L = {
    ru: {
      newT: 'Новая папка', editT: 'Изменить папку', eyebrowNew: 'новая папка', eyebrowEdit: 'папка',
      lead: 'Соберите чаты вместе', name: 'Название папки', what: 'что положить', picked: 'выбрано',
      search: 'Поиск по чатам', none: 'Ничего не найдено', create: 'Создать папку', save: 'Сохранить',
      inFolder: 'в папке', more: 'Все значки', less: 'Свернуть', fail: 'Не удалось сохранить папку',
      chats: (n) => { const a = n % 10, b = n % 100; return a === 1 && b !== 11 ? 'чат' : a >= 2 && a <= 4 && (b < 12 || b > 14) ? 'чата' : 'чатов'; },
    },
    en: {
      newT: 'New folder', editT: 'Edit folder', eyebrowNew: 'new folder', eyebrowEdit: 'folder',
      lead: 'Group your chats', name: 'Folder name', what: 'what goes in', picked: 'selected',
      search: 'Search chats', none: 'Nothing found', create: 'Create folder', save: 'Save',
      inFolder: 'in', more: 'All icons', less: 'Collapse', fail: 'Could not save the folder',
      chats: (n) => (n === 1 ? 'chat' : 'chats'),
    },
  };
  const tr = (k) => {
    const lang = (typeof currentLang !== 'undefined' && currentLang === 'en') ? 'en' : 'ru';
    return L[lang][k];
  };
  const escH = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ico = (n) => `<span class="material-icons-round">${n}</span>`;

  let root = null;
  let state = null; // { catId, icon, allIcons, picked:Set(slug), query }

  function chatTitle(ch) {
    if (ch.type === 'direct' && typeof dmPeer === 'function') return dmPeer(ch).name;
    return ch.name || ch.slug;
  }
  function chatAvatar(ch) {
    if (ch.type === 'direct' && typeof dmPeer === 'function') {
      const p = dmPeer(ch);
      return p.avatar ? `<img src="${escH(srv(p.avatar))}" alt="">` : escH((p.name || '?').charAt(0).toUpperCase());
    }
    if (ch.avatar_path) return `<img src="${escH(srv(ch.avatar_path))}" alt="">`;
    return ico(ch.type === 'private' ? 'lock' : 'tag');
  }
  function folderOf(ch) {
    return categories.find((c) => c.id !== (state && state.catId) && c.channelIds.includes(ch.id));
  }

  function build() {
    root = document.createElement('div');
    root.id = 'folder-sheet';
    root.className = 'modal fsheet';
    root.style.display = 'none';
    root.innerHTML = `<div class="modal-backdrop"></div>
      <div class="fsheet-box" role="dialog" aria-modal="true">
        <button class="btn-icon fsheet-close" type="button" aria-label="Закрыть">${ico('close')}</button>
        <div class="fsheet-eyebrow"></div><h2 class="fsheet-title"></h2>
        <div class="fsheet-namerow"><div class="fsheet-preview"></div>
          <input type="text" class="fsheet-name" maxlength="40" autocomplete="off"></div>
        <div class="fsheet-icons"></div>
        <div class="fsheet-label"></div>
        <div class="fsheet-search">${ico('search')}<input type="text" autocomplete="off"><button type="button" class="btn-icon-xs fsheet-clear">${ico('close')}</button></div>
        <div class="fsheet-list"></div>
        <button type="button" class="btn-action btn-accent fsheet-submit"></button>
      </div>`;
    document.body.appendChild(root);
    root.querySelector('.modal-backdrop').onclick = close;
    root.querySelector('.fsheet-close').onclick = close;
    const name = root.querySelector('.fsheet-name');
    name.oninput = renderSubmit;
    name.onkeydown = (e) => { if (e.key === 'Enter') submit(); };
    const search = root.querySelector('.fsheet-search input');
    search.oninput = () => { state.query = search.value.trim().toLowerCase(); renderList(); };
    root.querySelector('.fsheet-clear').onclick = () => { search.value = ''; state.query = ''; renderList(); search.focus(); };
    root.querySelector('.fsheet-submit').onclick = submit;
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && root.style.display !== 'none') close(); });
  }

  function close() { if (root) root.style.display = 'none'; }

  function renderIcons() {
    const keys = state.allIcons ? ICONS : QUICK.concat(QUICK.includes(state.icon) ? [] : [state.icon]);
    root.querySelector('.fsheet-icons').innerHTML = keys.map((k) =>
      `<button type="button" class="fsheet-ic${k === state.icon ? ' on' : ''}" data-ic="${k}" title="${k}">${ico(k)}</button>`).join('')
      + `<button type="button" class="fsheet-ic fsheet-more" title="${escH(tr(state.allIcons ? 'less' : 'more'))}">${ico(state.allIcons ? 'expand_less' : 'more_horiz')}</button>`;
    root.querySelectorAll('[data-ic]').forEach((b) => {
      b.onclick = () => { state.icon = b.dataset.ic; renderIcons(); };
    });
    root.querySelector('.fsheet-more').onclick = () => { state.allIcons = !state.allIcons; renderIcons(); };
    root.querySelector('.fsheet-preview').innerHTML = ico(state.icon);
  }

  function renderList() {
    const q = state.query;
    const list = [...channels]
      .sort((a, b) => (b.last_msg_timestamp || b.created_at || '').localeCompare(a.last_msg_timestamp || a.created_at || ''))
      .filter((ch) => !q || chatTitle(ch).toLowerCase().includes(q));
    const box = root.querySelector('.fsheet-list');
    if (!list.length) { box.innerHTML = `<div class="fsheet-empty">${escH(tr('none'))}</div>`; renderSubmit(); return; }
    box.innerHTML = list.map((ch) => {
      const on = state.picked.has(ch.slug);
      const other = folderOf(ch);
      const sub = other ? `<small>${escH(tr('inFolder'))} «${escH(other.name)}»</small>` : '';
      return `<button type="button" class="fsheet-chat${on ? ' on' : ''}" data-slug="${escH(ch.slug)}">
        <span class="fsheet-ava${ch.type === 'direct' ? ' round' : ''}">${chatAvatar(ch)}</span>
        <span class="fsheet-cname"><b>${escH(chatTitle(ch))}</b>${sub}</span>
        <span class="fsheet-check">${on ? ico('check') : ''}</span></button>`;
    }).join('');
    box.querySelectorAll('.fsheet-chat').forEach((b) => {
      b.onclick = () => {
        const slug = b.dataset.slug;
        if (state.picked.has(slug)) state.picked.delete(slug); else state.picked.add(slug);
        renderList();
      };
    });
    renderSubmit();
  }

  function renderSubmit() {
    const n = state.picked.size;
    root.querySelector('.fsheet-label').textContent = `// ${tr('what')}${n ? ` · ${tr('picked')} ${n}` : ''}`;
    const btn = root.querySelector('.fsheet-submit');
    const base = state.catId ? tr('save') : tr('create');
    btn.textContent = !state.catId && n ? `${base} · ${n} ${tr('chats')(n)}` : base;
    btn.disabled = !root.querySelector('.fsheet-name').value.trim() || state.saving;
  }

  async function submit() {
    const name = root.querySelector('.fsheet-name').value.trim();
    if (!name || state.saving) return;
    state.saving = true;
    renderSubmit();
    const slugs = [...state.picked];
    try {
      let catId = state.catId;
      if (!catId) {
        const res = await apiFetch('/chat-folders', { method: 'POST', body: { name, icon: state.icon, channel_slugs: slugs } });
        if (!res || !res.ok) throw new Error('create');
        catId = (await res.json()).id;
      } else {
        const res = await apiFetch(`/chat-folders/${catId}`, { method: 'PUT', body: { name, icon: state.icon } });
        if (!res || !res.ok) throw new Error('update');
        const before = new Set(state.initial);
        for (const s of slugs) if (!before.has(s)) await apiFetch(`/chat-folders/${catId}/channels`, { method: 'POST', body: { channel_slug: s } });
        for (const s of before) if (!state.picked.has(s)) await apiFetch(`/chat-folders/${catId}/channels/${encodeURIComponent(s)}`, { method: 'DELETE' });
      }
      // В вебе чат лежит в одной папке: выбранные чаты убираем из прежних.
      for (const s of slugs) {
        const ch = channels.find((c) => c.slug === s);
        const other = ch && folderOf(ch);
        if (other && other.id !== catId) {
          other.channelIds = other.channelIds.filter((id) => id !== ch.id);
          apiFetch(`/chat-folders/${other.id}/channels/${encodeURIComponent(s)}`, { method: 'DELETE' }).catch(() => {});
        }
      }
      await loadCategories();
      saveCategories();
      renderSidebar();
      close();
    } catch (e) {
      console.error('[folders]', e);
      showToast(tr('fail'), 'error');
    } finally {
      state.saving = false;
      if (root.style.display !== 'none') renderSubmit();
    }
  }

  function openFolderSheet(catId) {
    if (!root) build();
    const cat = catId ? categories.find((c) => c.id === catId) : null;
    const initial = cat ? cat.channelIds.map((id) => (channels.find((c) => c.id === id) || {}).slug).filter(Boolean) : [];
    state = { catId: cat ? cat.id : null, icon: (cat && cat.icon) || QUICK[0], allIcons: false,
      picked: new Set(initial), initial, query: '', saving: false };
    root.querySelector('.fsheet-eyebrow').textContent = '// ' + tr(cat ? 'eyebrowEdit' : 'eyebrowNew');
    root.querySelector('.fsheet-title').textContent = cat ? tr('editT') : tr('lead');
    const name = root.querySelector('.fsheet-name');
    name.placeholder = tr('name');
    name.value = cat ? cat.name : '';
    const search = root.querySelector('.fsheet-search input');
    search.placeholder = tr('search');
    search.value = '';
    renderIcons();
    renderList();
    root.style.display = 'flex';
    setTimeout(() => name.focus(), 30);
  }

  window.openFolderSheet = openFolderSheet;
  window.showCategoryPrompt = () => openFolderSheet(null);
})();
