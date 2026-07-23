renderDock('home');
setupBack('index.html');

const out = document.getElementById('out');

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function rateRow(h) {
  return `<div class="rate"><b>${esc(h.rate)}%</b>
    <span class="pkd-desc">${esc(h.who)}</span></div>`;
}

function card(x, opts = {}) {
  const flags = (x.flags || []).map(f => `<div class="pkd-flag ${f.level}">
    <span class="fi" data-icon="${f.level === 'warn' ? 'alert-triangle' : 'info'}"></span>
    <span><b>${esc(f.title)}</b> — ${esc(f.text)}</span></div>`).join('');
  const rates = (x.rate_hints || []).map(rateRow).join('');
  const desc = (x.includes || [])[0];
  // старый номер показываем только там, где он и есть предмет разговора:
  // в аудите своих кодов и при вводе кода PKD 2007 (сервер его там и присылает)
  const old = (x.was_pkd2007 || []).length
    ? `<span class="chip">раньше ${esc(x.was_pkd2007.join(', '))}</span>` : '';
  return `<div class="card" style="margin-bottom:12px${opts.border ? ';border-left:3px solid ' + opts.border : ''}">
    <div class="chips"><span class="chip">${esc(x.section || '')}</span>${old}
      ${opts.chip || ''}</div>
    <div class="pkd-code">${esc(x.code)}</div>
    <div class="pkd-name">${esc(x.name)}</div>
    ${desc ? `<div class="pkd-desc">${esc(desc.slice(0, 320))}</div>` : ''}
    ${rates ? `<div style="margin-top:10px;padding-top:9px;border-top:1px solid var(--line)">
      <div class="muted" style="font-size:12px;margin-bottom:2px">Вероятная ставка ryczałtu</div>
      ${rates}</div>` : ''}
    ${flags}
  </div>`;
}

function render(d) {
  if (!d.results || !d.results.length) {
    out.innerHTML = `<div class="state">Ничего не нашлось. Опиши деятельность обычными
      словами: «делаю сайты», «вожу такси», «шью одежду».</div>`;
    return;
  }
  const head = d.migration ? `<div class="card" style="margin-bottom:12px;border-left:3px solid var(--warn)">
      <div class="pkd-code">${esc(d.migration.old)}</div>
      <div class="pkd-name">${esc(d.migration.old_name)}</div>
      <div class="pkd-desc">${esc(d.note)}</div></div>` : '';
  out.innerHTML = head + d.results.map(x => card(x)).join('')
    + `<p class="foot" style="margin-top:0">${esc(d.note && !d.migration ? d.note : '')}</p>`;
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

async function search(q) {
  document.getElementById('q').value = q;
  haptic('medium');
  out.innerHTML = '<div class="state">Ищем по справочнику…</div>';
  try { render(await post('/api/pkd', { q })); }
  catch (e) { out.innerHTML = `<div class="state">${esc(e.message)}</div>`; }
}

document.getElementById('go').onclick = () => {
  const q = document.getElementById('q').value.trim();
  if (q) search(q);
};
document.getElementById('q').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('go').click();
});
document.querySelectorAll('.ex button').forEach(b => {
  b.onclick = () => search(b.dataset.q);
});

document.getElementById('mygo').onclick = async () => {
  const nip = document.getElementById('nip').value.trim();
  const box = document.getElementById('myout');
  if (!nip) return;
  haptic('medium');
  box.innerHTML = '<div class="muted">Спрашиваем CEIDG…</div>';
  try {
    const d = await post('/api/pkd/my', { nip });
    if (!d.items || !d.items.length) {
      box.innerHTML = `<div class="muted">${esc(d.note || 'Кодов не видно.')}</div>`;
      return;
    }
    const tone = d.outdated ? 'var(--warn)' : 'var(--ok)';
    box.innerHTML = `<div class="pkd-flag ${d.outdated ? 'warn' : 'note'}">
        <span class="fi" data-icon="${d.outdated ? 'alert-triangle' : 'check-circle'}"></span>
        <span>${esc(d.summary)}</span></div>`
      + d.items.map(i => i.status === 'outdated'
        ? card({ code: i.code, name: i.name, section: 'PKD 2007' },
               { border: 'var(--warn)', chip: '<span class="chip">устарел</span>' })
          + (i.replace_with || []).map(x => card(x,
              { chip: '<span class="chip">замена</span>' })).join('')
        : i.status === 'unknown'
          ? `<div class="card" style="margin-bottom:12px"><div class="pkd-code">${esc(i.code)}</div>
             <div class="pkd-desc">Такого кода нет ни в PKD 2025, ни в ключах перехода —
             проверь запись в CEIDG.</div></div>`
          : card(i, { border: tone })).join('');
    Icons.hydrate();
  } catch (e) { box.innerHTML = `<div class="muted">${esc(e.message)}</div>`; }
};
