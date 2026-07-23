renderDock('home');
setupBack('index.html');

const out = document.getElementById('out');
const SIG_ICON = { ok: 'check-circle', warn: 'alert-triangle', bad: 'x-circle' };

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function vatChip(status) {
  const map = { 'Czynny': ['ok', 'VAT czynny'], 'Zwolniony': ['warn', 'VAT zwolniony'] };
  const [tone, text] = map[status] || ['bad', 'нет в реестре VAT'];
  return `<span class="chip" style="color:var(--${tone})">${text}</span>`;
}

function render(d) {
  const lvl = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)' }[d.level];
  const rows = [
    ['NIP', d.nip], ['REGON', d.regon], ['KRS', d.krs],
    ['Адрес', d.address], ['В реестре с', d.registered],
    ['Основной PKD', d.pkd_main], ['PKD', (d.pkd || []).join(', ')],
  ].filter(([, v]) => v).map(([k, v]) =>
    `<div class="kv"><span>${k}</span><b>${esc(v)}</b></div>`).join('');

  const accs = (d.accounts || []).slice(0, 8).map(a =>
    a.replace(/(.{4})/g, '$1 ').trim()).join('<br>');
  const total = d.accounts_total || d.accounts.length;
  const more = total > 8 ? `<div class="muted" style="font-size:12px;margin-top:4px">
    …и ещё ${total - 8}</div>` : '';

  const srcLabel = { whitelist: 'Белый список', vies: 'VIES', gus: 'GUS', ceidg: 'CEIDG' };
  const srcs = Object.entries(d.sources).map(([k, v]) =>
    `<span class="chip ${v === 'ok' ? '' : 'off'}">${srcLabel[k]}: ${
      { ok: 'есть данные', empty: 'нет записи', off: 'не подключён', error: 'ошибка' }[v]}</span>`).join('');

  out.innerHTML = `
    <div class="card" style="margin-bottom:12px;border-left:3px solid ${lvl}">
      <div class="nip-name">${esc(d.name || 'Название не найдено')}</div>
      <div class="nip-chips">${vatChip(d.vat_status)}
        ${d.activity ? `<span class="chip">${esc(d.activity)}</span>` : ''}
        <span class="chip" style="color:${lvl}">сигналы: ${d.score}/100</span>
        ${d.cached ? '<span class="chip">из кэша</span>' : ''}</div>
      ${d.signals.map(s => `<div class="sig ${s.level}">
        <span class="si" data-icon="${SIG_ICON[s.level]}"></span>
        <span>${esc(s.text)}</span></div>`).join('')}
    </div>
    ${rows ? `<div class="card" style="margin-bottom:12px">
      <div class="h2" style="margin-bottom:4px">Данные фирмы</div>${rows}</div>` : ''}
    ${d.accounts.length ? `<div class="card" style="margin-bottom:12px">
      <div class="h2" style="margin-bottom:6px">Счета в белом списке</div>
      <div class="acc-list">${accs}</div>${more}</div>` : ''}
    <div class="card" style="margin-bottom:12px">
      <div class="h2" style="margin-bottom:4px">Источники</div>
      <div class="src-row">${srcs}</div></div>`;
  Icons.hydrate();
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ initData: tg ? tg.initData : '' }, body)),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'Ошибка запроса');
  return data;
}

document.getElementById('go').onclick = async () => {
  const nip = document.getElementById('nip').value.trim();
  if (!nip) return;
  haptic('medium');
  out.innerHTML = '<div class="state">Спрашиваем реестры…</div>';
  try { render(await post('/api/nip', { nip })); }
  catch (e) { out.innerHTML = `<div class="state">${esc(e.message)}</div>`; }
};

document.getElementById('accgo').onclick = async () => {
  const nip = document.getElementById('nip').value.trim();
  const account = document.getElementById('acc').value.trim();
  const box = document.getElementById('accout');
  if (!nip) { box.innerHTML = '<div class="muted">Сначала укажи NIP выше.</div>'; return; }
  if (!account) return;
  haptic('medium');
  box.innerHTML = '<div class="muted">Проверяем…</div>';
  try {
    const d = await post('/api/nip/account', { nip, account });
    const tone = d.assigned ? 'ok' : 'bad';
    box.innerHTML = `<div class="sig ${tone}">
      <span class="si" data-icon="${SIG_ICON[tone]}"></span><span>${esc(d.note)}</span></div>`;
    Icons.hydrate();
  } catch (e) { box.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
};

document.getElementById('nip').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('go').click();
});
