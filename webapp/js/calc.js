renderDock('calc');

function segInit(id, cb) {
  const seg = document.getElementById(id);
  seg.addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    seg.querySelectorAll('button').forEach(x => x.classList.remove('on'));
    b.classList.add('on'); haptic(); cb();
  });
}
const segVal = (id) => document.querySelector('#' + id + ' button.on').dataset.v;

// вкладки
segInit('tabs', () => {
  const t = segVal('tabs') || document.querySelector('#tabs button.on').dataset.t;
  ['zus', 'cmp', 'vat'].forEach(k =>
    document.getElementById('tab-' + k).classList.toggle('hidden', k !== t));
});
document.querySelectorAll('#tabs button').forEach(b => b.dataset.v = b.dataset.t);

ratesReady.then(() => {
  document.getElementById('upd').textContent = RATES.updated;

  // ── ZUS ──
  const zres = document.getElementById('zres');
  function zus() {
    const year = +segVal('zyear');
    const stage = segVal('zstage'), form = segVal('zform');
    const chor = document.getElementById('zchor').checked;
    const inc = +document.getElementById('zinc').value || 0;
    document.getElementById('zinc-label').textContent = form === 'ryczalt'
      ? 'Годовой przychód (выручка), zł'
      : 'Годовой dochód (доход = выручка − расходы), zł';
    const s = spoleczneMonthly(stage, chor, year);
    const zdr = zdrowotnaMonthly(form, inc, year);
    const rows = [];
    if (stage === 'ulga')
      rows.push(`<div class="res-row"><span>Składki społeczne</span><span class="v">0 zł <span class="muted">(льгота 6 мес)</span></span></div>`);
    else {
      rows.push(`<div class="res-row"><span>Emerytalna</span><span class="v">${fmtZl(s.emerytalna)}</span></div>`);
      rows.push(`<div class="res-row"><span>Rentowa</span><span class="v">${fmtZl(s.rentowa)}</span></div>`);
      rows.push(`<div class="res-row"><span>Wypadkowa</span><span class="v">${fmtZl(s.wypadkowa)}</span></div>`);
      if (s.fp_fs) rows.push(`<div class="res-row"><span>Fundusz Pracy</span><span class="v">${fmtZl(s.fp_fs)}</span></div>`);
      if (chor) rows.push(`<div class="res-row"><span>Chorobowa (добров.)</span><span class="v">${fmtZl(s.chorobowa)}</span></div>`);
    }
    if (stage === 'ulga' && chor)
      rows.push(`<div class="res-row"><span class="muted">На Uldze na start chorobową подключить нельзя</span><span></span></div>`);
    rows.push(`<div class="res-row"><span>Zdrowotna (${form})</span><span class="v">${fmtZl(zdr)}</span></div>`);
    rows.push(`<div class="res-row total"><span><b>Итого в месяц</b></span><span class="v">${fmtZl(s.total + zdr)}</span></div>`);
    if (stage !== 'ulga' && s.total > 0)
      rows.push(`<div class="res-row" style="border:none"><span class="muted"><span class="emi" style="color:var(--ok)">${Icons.svg('tree-palm')}</span>Месяц <a href="article.html?id=zus_vacation">ZUS-каникул</a> экономит</span><span class="v" style="color:var(--ok)">${fmtZl(s.total)}</span></div>`);
    zres.innerHTML = rows.join('');
  }
  segInit('zyear', zus); segInit('zstage', zus); segInit('zform', zus);
  document.getElementById('zchor').addEventListener('change', zus);
  document.getElementById('zinc').addEventListener('input', zus);
  zus();

  // ── сравнение форм ──
  const cres = document.getElementById('cres');
  const NAMES = { skala: 'Skala 12/32%', liniowy: 'Liniowy 19%', ryczalt: 'Ryczałt' };
  function cmp() {
    const rev = (+document.getElementById('crev').value || 0) * 12;
    const cost = (+document.getElementById('ccost').value || 0) * 12;
    const rate = +document.getElementById('cryc').value;
    const r = compareForms(rev, cost, segVal('cstage'), false, rate);
    cres.innerHTML = ['ryczalt', 'liniowy', 'skala'].map(f => {
      const d = r[f], win = r.best === f;
      return `<div class="card ${win ? 'win' : ''}" style="margin-bottom:8px">
        <div class="res-row" style="border:none;padding-bottom:2px">
          <span><b>${NAMES[f]}${f === 'ryczalt' ? ' ' + (rate * 100).toLocaleString('pl-PL') + '%' : ''}</b>
          ${win ? '<span class="tag"> · выгоднее всего</span>' : ''}</span></div>
        <div class="res-row"><span>Налог / год</span><span class="v">${fmtZl(d.tax)}</span></div>
        <div class="res-row"><span>Zdrowotna / год</span><span class="v">${fmtZl(d.zdrowotna)}</span></div>
        <div class="res-row"><span>Społeczne / год</span><span class="v">${fmtZl(d.spoleczne)}</span></div>
        <div class="res-row total"><span><b>На руки / мес</b></span><span class="v">${fmtZl(d.net / 12)}</span></div>
      </div>`;
    }).join('');
  }
  segInit('cstage', cmp);
  ['crev', 'ccost', 'cryc'].forEach(id =>
    document.getElementById(id).addEventListener('input', cmp));
  cmp();

  // ── VAT ──
  const vres = document.getElementById('vres');
  function vat() {
    const ytd = +document.getElementById('vytd').value || 0;
    const mid = document.getElementById('vmid').checked;
    document.getElementById('vdate-f').classList.toggle('hidden', !mid);
    const v = vatLimit(ytd, mid ? document.getElementById('vdate').value : null);
    const pct = Math.min(v.used / v.limit * 100, 100);
    const tone = v.exceeded ? 'bad' : pct > 80 ? 'warn' : 'ok';
    vres.innerHTML = `
      <div class="res-row"><span>Твой лимит${mid ? ' (пропорционально)' : ''}</span><span class="v">${fmtZl(v.limit)}</span></div>
      <div class="res-row"><span>Использовано</span><span class="v">${fmtZl(v.used)}</span></div>
      <div class="res-row total"><span><b>${v.exceeded ? 'Лимит превышен!' : 'Осталось'}</b></span>
        <span class="v">${v.exceeded ? '⚠️ нужна регистрация VAT' : fmtZl(v.left)}</span></div>
      <div class="meter ${tone}" style="margin-top:10px"><i style="width:${pct}%"></i></div>
      <div class="muted" style="font-size:12px;margin-top:6px">${pct.toFixed(0)}% лимита</div>`;
  }
  ['vytd', 'vdate'].forEach(id => document.getElementById(id).addEventListener('input', vat));
  document.getElementById('vmid').addEventListener('change', vat);
  vat();
});
