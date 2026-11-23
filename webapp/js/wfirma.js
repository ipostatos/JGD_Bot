// Окно ключей wFirma API: ввод, проверка, выбор фирмы, статус, отключение.
// Ключи на сервер уходят один раз при подключении; обратно сервер их не отдаёт.
renderDock('plan');
setupBack('plan.html');
(function () {
  const $ = id => document.getElementById(id);
  const WFKEY = 'jdg_wfirma_on';
  const inTg = tg && tg.initData;
  let needAppKey = false;
  // имя фирмы и тексты ошибок приходят извне — только экранированными
  const esc = v => String(v == null ? '' : v).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const msg = t => { $('wk-msg').textContent = t; };

  async function api(path, method, extra) {
    const r = await fetch(path, {
      method: method || 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ initData: tg.initData }, extra || {})),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(data.detail || 'Ошибка'); e.status = r.status; e.data = data; throw e; }
    return data;
  }

  function setNeedAppKey(v) {
    needAppKey = v;
    $('wk-app-box').classList.toggle('hidden', !v);
    $('wk-app-help').classList.toggle('hidden', !v);
  }

  function showForm() {
    $('wk-on').classList.add('hidden');
    $('wk-form').classList.remove('hidden');
    $('wk-help').classList.remove('hidden');
  }

  function showState(s) {
    const rows = [];
    if (s.company_name) rows.push(`<div class="res-row"><span>Фирма</span><span class="v">${esc(s.company_name)}</span></div>`);
    if (s.access_tail) rows.push(`<div class="res-row"><span>Access key</span><span class="v">…${esc(s.access_tail)}</span></div>`);
    if (s.since) rows.push(`<div class="res-row"><span>Подключено</span><span class="v">${
      new Date(s.since * 1000).toLocaleDateString('ru-RU')}</span></div>`);
    rows.push(`<div class="res-row" style="border:none"><span>App key</span><span class="v">${
      s.own_app_key ? 'твой' : 'приложения JDG HUB'}</span></div>`);
    $('wk-state').innerHTML = rows.join('');
    $('wk-form').classList.add('hidden');
    $('wk-help').classList.add('hidden');
    $('wk-on').classList.remove('hidden');
    if (window.Icons) Icons.hydrate();
  }

  $('wk-show').onchange = () => {
    const t = $('wk-show').checked ? 'text' : 'password';
    ['wk-access', 'wk-secret', 'wk-app'].forEach(id => { $(id).type = t; });
  };

  $('wk-connect').onclick = async () => {
    if (!inTg) { msg('Открой через Telegram — ключи привязываются к твоему аккаунту.'); return; }
    const access = $('wk-access').value.trim(), secret = $('wk-secret').value.trim();
    const app = $('wk-app').value.trim();
    if (!access || !secret) { msg('Нужны оба ключа: Access key и Secret key.'); return; }
    if (needAppKey && !app) { msg('Нужен ещё App key — ключ приложения wFirma.'); return; }
    const box = $('wk-company-box');
    const company = box.classList.contains('hidden') ? '' : $('wk-company').value;
    msg('Проверяю ключи в wFirma…'); haptic('medium');
    $('wk-connect').disabled = true;
    try {
      await api('/api/wfirma/connect', 'POST',
        { access_key: access, secret_key: secret, app_key: app, company_id: company });
      localStorage.setItem(WFKEY, '1');
      ['wk-access', 'wk-secret', 'wk-app'].forEach(id => { $(id).value = ''; });
      box.classList.add('hidden');
      msg('');
      try { tg.HapticFeedback.notificationOccurred('success'); } catch (e) {}
      showState(await api('/api/wfirma/status'));
    } catch (e) {
      if (e.status === 409 && e.data && e.data.companies) {
        // у ключа несколько фирм — выбираем, какую показывать
        $('wk-company').innerHTML = e.data.companies.map(c =>
          `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
        box.classList.remove('hidden');
        msg('У ключа несколько фирм — выбери свою и нажми ещё раз.');
      } else {
        msg(e.message);
      }
    } finally {
      $('wk-connect').disabled = false;
    }
  };

  $('wk-replace').onclick = () => { haptic('light'); showForm(); $('wk-access').focus(); };

  $('wk-disconnect').onclick = async () => {
    try { await api('/api/wfirma', 'DELETE'); } catch (e) {}
    localStorage.removeItem(WFKEY);
    showForm();
    msg('Ключи удалены.');
  };

  fetch('/api/health').then(r => r.json()).then(h => setNeedAppKey(!h.wfirma_app_key))
    .catch(() => setNeedAppKey(true));
  if (inTg) {
    api('/api/wfirma/status').then(s => {
      if (s.connected) { localStorage.setItem(WFKEY, '1'); showState(s); }
      else localStorage.removeItem(WFKEY);
    }).catch(() => {});
  }
})();
