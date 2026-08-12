renderDock('home');
// ?from приходит из ссылки — через safeNav, иначе from=javascript:… исполнялся бы
setupBack(() => safeNav(qs.get('from') || 'tools.html'));

const SEV_NAME = { K: 'критическая', Z: 'обычная', I: 'инфо' };
let DATA = [], sevFilter = '', docFilter = '';

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function hl(text, q) {
  if (!q) return esc(text);
  // подсвечиваем по СЫРОМУ тексту и экранируем каждый кусок отдельно: подсветка
  // поверх уже экранированного HTML попадала внутрь &amp;/&quot; и рвала сущность
  const re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return String(text == null ? '' : text).split(re)
    .map((seg, i) => (i % 2 ? '<mark>' + esc(seg) + '</mark>' : esc(seg)))
    .join('');
}

function card(e, q, i) {
  const doc = (e.doc || []).map(d => `<span class="chip">${esc(d)}</span>`).join('');
  return `<div class="card err" data-i="${i}">
    <div class="top">
      ${e.code ? `<span class="code">${hl(e.code, q)}</span>` : '<span class="code">—</span>'}
      <span class="sev ${e.severity}">${e.severity} · ${SEV_NAME[e.severity]}</span>${doc}
    </div>
    <div class="tt">${hl(e.title_ru, q)}</div>
    <div class="pl">${hl(e.msg_pl, q)}</div>
    <div class="body hidden">
      <div class="why">${hl(e.why_ru, q)}</div>
      <h4>Что делать</h4>
      <ol>${e.fix_ru.map(f => `<li>${hl(f, q)}</li>`).join('')}</ol>
    </div></div>`;
}

function apply() {
  const q = document.getElementById('q').value.trim();
  const ql = q.toLowerCase();
  const hits = DATA.filter(e =>
    (!sevFilter || e.severity === sevFilter) &&
    (!docFilter || (e.doc || []).includes(docFilter)) &&
    (!ql || [e.code, e.title_ru, e.msg_pl, e.why_ru, ...(e.tags || []), ...e.fix_ru]
      .join(' ').toLowerCase().includes(ql)));
  const list = document.getElementById('list');
  list.innerHTML = hits.length
    ? hits.map((e, i) => card(e, q, DATA.indexOf(e))).join('')
    : '<div class="state">Ничего не нашлось. Попробуй часть кода или слово из польского текста.</div>';
  // одна находка по точному коду — сразу раскрываем
  if (hits.length === 1) list.querySelector('.body')?.classList.remove('hidden');
  list.querySelectorAll('.err').forEach(el => el.onclick = () => {
    haptic('light');
    el.querySelector('.body').classList.toggle('hidden');
  });
}

loadJSON('zus_errors.json').then(d => {
  DATA = d.errors;
  const docs = [...new Set(DATA.flatMap(e => e.doc || []))].sort();
  document.getElementById('docs').innerHTML = docs.map(x =>
    `<span class="chip doc" data-v="${esc(x)}" style="cursor:pointer">${esc(x)}</span>`).join('');
  document.querySelectorAll('#docs .doc').forEach(el => el.onclick = () => {
    const v = el.dataset.v;
    docFilter = (docFilter === v) ? '' : v;
    document.querySelectorAll('#docs .doc').forEach(x =>
      x.style.color = x.dataset.v === docFilter ? 'var(--link)' : '');
    apply();
  });
  const pre = qs.get('code');
  if (pre) document.getElementById('q').value = pre;
  apply();
}).catch(() => {
  document.getElementById('list').innerHTML = '<div class="state">Не удалось загрузить базу</div>';
});

document.getElementById('q').addEventListener('input', apply);
document.querySelectorAll('#sev button').forEach(b => b.onclick = () => {
  document.querySelectorAll('#sev button').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); sevFilter = b.dataset.v; apply();
});
