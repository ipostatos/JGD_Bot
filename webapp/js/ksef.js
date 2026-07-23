renderDock('home');
setupBack('index.html');

const SIG_ICON = { ok: 'check-circle', warn: 'alert-triangle', bad: 'x-circle' };
const MON = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function ruDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.getDate() + ' ' + MON[d.getMonth()] + ' ' + d.getFullYear();
}

function renderFacts(f) {
  const today = new Date();
  document.getElementById('tl').innerHTML = f.stages.map(s => {
    const d = new Date(s.date + 'T00:00:00');
    const cls = d <= today ? 'done' : (d - today < 92 * 864e5 ? 'now' : '');
    return `<div class="tl-row ${cls}">
      <div class="tl-date">${ruDate(s.date)}${d <= today ? ' · уже действует' : ''}</div>
      <div class="tl-title">${esc(s.title)}</div>
      <div class="tl-who">${esc(s.who)}</div>
      <div class="tl-what">${esc(s.what)}</div></div>`;
  }).join('');

  document.getElementById('relief').innerHTML = f.relief.map(r =>
    `<div class="step"><span class="num">✓</span><span><span class="t">${esc(r.title)}</span>
      <span class="d">${esc(r.text)}</span></span></div>`).join('');

  document.getElementById('excluded').innerHTML =
    f.excluded.map(x => `<li>${esc(x)}</li>`).join('');

  document.getElementById('steps').innerHTML = f.steps.map((s, i) =>
    `<div class="step"><span class="num">${i + 1}</span><span><span class="t">${esc(s.t)}</span>
      <span class="d">${esc(s.d)}</span></span></div>`).join('');

  document.getElementById('access').innerHTML = f.access.map(a =>
    `<div class="step"><span class="num"><span class="si" data-icon="circle-dot"></span></span>
      <span><span class="t">${a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)} ↗</a>` : esc(a.title)}</span>
      <span class="d">${esc(a.text)}</span></span></div>`).join('');

  document.getElementById('faq').innerHTML = f.faq.map(q =>
    `<details class="faq-item"><summary>${esc(q.q)}</summary><p>${esc(q.a)}</p></details>`).join('');

  document.getElementById('upd').textContent = ruDate(f.updated);
  document.getElementById('src').innerHTML = f.sources.map(s =>
    `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)} ↗</a>`).join('');
  Icons.hydrate();
}

function renderStatus(st, infakt, hasProfile) {
  const lvl = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)' }[st.level] || 'var(--border)';
  const months = Math.round(st.days_to_full / 30.4);
  const left = st.days_to_full > 60 ? `~${months} мес.` : `${st.days_to_full} дн.`;
  document.getElementById('status').innerHTML = `
    <div class="card" style="margin-bottom:12px;border-left:3px solid ${lvl}">
      <div class="kf-head">${esc(st.headline)}</div>
      <div class="kf-chips">
        <span class="chip">до полного KSeF: ${left}</span>
        ${infakt ? '<span class="chip">данные из inFakt</span>'
          : hasProfile ? '<span class="chip">по профилю из «Плана»</span>'
            : '<span class="chip">общие правила · <a href="plan.html">заполни профиль</a></span>'}
      </div>
      ${st.lines.map(l => `<div class="sig ${l.level}">
        <span class="si" data-icon="${esc(l.icon)}"></span><span>${esc(l.text)}</span></div>`).join('')}
    </div>`;
  Icons.hydrate();
}

loadJSON('ksef.json').then(renderFacts).catch(() => {});

(async function () {
  const p = getProfile();
  // Пустой профиль не выдаём за «не плательщик VAT» — поля шлём только те,
  // что человек реально заполнил, остальное для сервера остаётся неизвестным.
  const prof = {};
  if (p.reg) { prof.reg = p.reg; prof.vat = !!p.vat; }
  try {
    const r = await fetch('/api/ksef', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: tg ? tg.initData : '', profile: prof }),
    });
    if (!r.ok) throw new Error('http ' + r.status);
    const d = await r.json();
    renderStatus(d.status, d.infakt, !!prof.reg);
  } catch (e) {
    document.getElementById('status').innerHTML =
      '<div class="state">Не получилось посчитать статус. Этапы и правила ниже — они не зависят от профиля.</div>';
  }
})();
