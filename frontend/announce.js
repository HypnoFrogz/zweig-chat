/* Zweig — «Вышла новая версия».
 *
 * 1. Админка → вкладка «Обновления»: кнопка, которая рассылает уведомление
 *    всем пользователям этого сервера (push на телефоны, плашка в вебе и на
 *    компьютере). Сервер — routes/app_update.py.
 * 2. Плашка «Доступна новая версия · Обновить» для тех, кто сейчас в вебе
 *    или в приложении для компьютера: интерфейс у них берётся с сервера,
 *    поэтому обновиться = перезагрузить страницу.
 */
(function () {
  'use strict';

  const escH = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const ico = (n) => `<span class="material-icons-round">${n}</span>`;
  const $ = (id) => document.getElementById(id);

  /* ── Плашка для веба и компьютера ─────────────────────────────── */
  function showBanner(data) {
    let bar = $('update-banner');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'update-banner';
      bar.setAttribute('role', 'status');
      document.body.appendChild(bar);
    }
    const title = data.title || 'Вышла новая версия Zweig';
    const isUpdate = data.kind !== 'announce';
    bar.innerHTML = `${ico(isUpdate ? 'system_update' : 'campaign')}<div><b>${escH(title)}</b><small>${escH(data.text || 'Обновите страницу, чтобы получить новую версию.')}</small></div>
      ${isUpdate ? '<button type="button" class="btn-action btn-accent" id="update-banner-go">Обновить</button>' : ''}
      <button type="button" class="btn-icon" id="update-banner-x" aria-label="${isUpdate ? 'Позже' : 'Закрыть'}">${ico('close')}</button>`;
    bar.classList.add('show');
    if (isUpdate) $('update-banner-go').onclick = () => location.reload();
    $('update-banner-x').onclick = () => bar.classList.remove('show');
  }

  const origWs = window.handleWsEvent;
  window.handleWsEvent = function (data) {
    if (data && data.event === 'app_update') { showBanner(data); return; }
    return origWs.apply(this, arguments);
  };

  /* ── Админка: вкладка «Обновления» ────────────────────────────── */
  const DEF_TITLE = 'Вышла новая версия Zweig';
  const DEF_BODY = 'Обновите приложение, чтобы получить новые возможности и исправления.';

  function renderPanel() {
    const panel = document.querySelector('.admin-tab-panel[data-panel="advanced"]');
    if (!panel || panel.dataset.ready) return;
    panel.dataset.ready = '1';
    const tab = document.querySelector('.admin-tab[data-tab="advanced"]');
    if (tab) tab.innerHTML = `${ico('system_update')}<span>Обновления</span>`;
    panel.innerHTML = `<div class="admin-section upd-card">
      <div class="upd-head">${ico('campaign')}<div><b>Уведомить о новой версии</b>
        <small>Всем пользователям этого сервера. Телефоны получат push со ссылкой в App Store или Google Play, в вебе и в приложении для компьютера появится плашка «Обновить».</small></div></div>
      <div class="upd-stats" id="upd-stats">…</div>
      <div class="upd-plats">
        <label><input type="checkbox" value="android" checked> Android</label>
        <label><input type="checkbox" value="ios" checked> iPhone</label>
        <label><input type="checkbox" value="web" checked> Веб и компьютер</label>
      </div>
      <div class="input-group"><span class="material-icons-round input-icon">title</span><input type="text" id="upd-title" maxlength="80"></div>
      <div class="input-group"><span class="material-icons-round input-icon">notes</span><input type="text" id="upd-body" maxlength="240"></div>
      <div class="upd-preview"><span class="upd-app">${ico('forum')}</span><div><b id="upd-pv-title"></b><small id="upd-pv-body"></small></div></div>
      <div class="upd-actions"><button type="button" class="btn-action btn-accent" id="upd-send">${ico('send')}Отправить уведомление</button>
        <span class="upd-result" id="upd-result"></span></div>
      <div class="admin-form-hint">Отправить можно не чаще раза в 10 минут. Push приходит только на телефоны, где разрешены уведомления.</div>
    </div>`;
    const t = $('upd-title'), b = $('upd-body');
    t.value = DEF_TITLE; b.value = DEF_BODY;
    t.placeholder = 'Заголовок'; b.placeholder = 'Текст уведомления';
    const pv = () => { $('upd-pv-title').textContent = t.value || DEF_TITLE; $('upd-pv-body').textContent = b.value || DEF_BODY; };
    t.oninput = pv; b.oninput = pv; pv();
    $('upd-send').onclick = send;
  }

  async function loadStats() {
    const box = $('upd-stats');
    if (!box) return;
    const res = await apiFetch('/admin/app-update/stats');
    if (!res || !res.ok) { box.textContent = ''; return; }
    const s = await res.json();
    box.innerHTML = `<span>${ico('android')}<b>${s.android}</b> Android</span><span>${ico('phone_iphone')}<b>${s.ios}</b> iPhone</span><span>${ico('computer')}<b>${s.online}</b> сейчас в сети</span>`
      + (s.people != null ? `<div class="upd-note">Телефоны с включёнными уведомлениями есть у ${s.people} чел. Кто ни разу не открывал приложение на телефоне или запретил уведомления, push не получит.</div>` : '');
    if (s.cooldown > 0) $('upd-result').textContent = `Уже отправлено, повторить можно через ${Math.ceil(s.cooldown / 60)} мин.`;
  }

  async function send() {
    const platforms = [...document.querySelectorAll('.upd-plats input:checked')].map((i) => i.value);
    if (!platforms.length) { showToast('Выберите хотя бы одну платформу', 'error'); return; }
    if (!confirm('Отправить уведомление о новой версии всем пользователям сервера?')) return;
    const btn = $('upd-send');
    btn.disabled = true;
    const res = await apiFetch('/admin/app-update/notify', { method: 'POST', body: { platforms, title: $('upd-title').value, body: $('upd-body').value } });
    btn.disabled = false;
    const d = res ? await res.json().catch(() => ({})) : {};
    if (!res || !res.ok) { $('upd-result').textContent = d.detail || 'Не удалось отправить'; return; }
    const parts = [];
    if (platforms.includes('android')) parts.push(`Android-устройств: ${d.android}`);
    if (platforms.includes('ios')) parts.push(`iPhone: ${d.ios}`);
    if (platforms.includes('web')) parts.push(`в сети: ${d.web}`);
    let msg = 'Отправлено — ' + parts.join(', ');
    if (d.failed) msg += `, не дошло: ${d.failed}`;
    if (d.push_disabled) msg += '. Push на телефоны на этом сервере не настроен (нет ключа Firebase).';
    $('upd-result').textContent = msg;
    showToast('Уведомление отправлено', 'success');
  }

  const origSwitch = window.switchAdminTab;
  window.switchAdminTab = function (tab) {
    const r = origSwitch.apply(this, arguments);
    if (tab === 'advanced') { renderPanel(); loadStats(); }
    return r;
  };
  // Подпись вкладки — сразу, чтобы не висело «Расширенные настройки».
  document.addEventListener('DOMContentLoaded', () => {
    const tab = document.querySelector('.admin-tab[data-tab="advanced"]');
    if (tab) tab.innerHTML = `${ico('system_update')}<span>Обновления</span>`;
  });
  if (document.readyState !== 'loading') {
    const tab = document.querySelector('.admin-tab[data-tab="advanced"]');
    if (tab) tab.innerHTML = `${ico('system_update')}<span>Обновления</span>`;
  }
})();
