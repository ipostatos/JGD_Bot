const tg = window.Telegram?.WebApp;
tg?.ready?.(); tg?.expand?.();
setupBack('index.html');
Icons?.hydrate?.();

let tab = 'over';   // обзор — первый экран кокпита
const $ = (id) => document.getElementById(id);
// Источник данных: wFirma, только если подключена она одна. Закрытие месяца и
// платёжные пакеты считает движок Hermes на начислениях ZUS/налога inFakt —
// в API wFirma их нет, поэтому у wFirma только обзор.
const PROVIDER = localStorage.getItem('jdg_wfirma_on') === '1'
  && localStorage.getItem('jdg_infakt_on') !== '1' ? 'wfirma' : 'infakt';
const SRC = PROVIDER === 'wfirma' ? 'wFirma' : 'inFakt';
if (PROVIDER === 'wfirma') {
  $('tabs').classList.add('hidden');          // вкладка одна — переключатель не нужен
  $('foot').innerHTML = 'Цифры — из твоей wFirma, мы их только читаем.<br>'
    + 'Справочная информация, не юридическая и не налоговая консультация.';
  $('sub').textContent = 'Обзор продаж, расходов, VAT и KSeF — на твоих данных wFirma';
}
$('month').value = new Date().toISOString().slice(0, 7);
$('monthbar').classList.add('hidden');  // на обзоре выбор месяца не нужен

async function api(path, extra) {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ initData: tg?.initData || '' }, extra || {})),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || 'Ошибка');
  return data;
}

function setTab(t) {
  tab = t;
  ['over', 'close', 'pay'].forEach((k) => $('tab-' + k).classList.toggle('on', t === k));
  $('monthbar').classList.toggle('hidden', t === 'over');  // обзор — всегда «сейчас»
  run();
}

// вкладки вешаются здесь, а не через onclick в разметке: инлайновые обработчики
// блокирует CSP без 'unsafe-inline'
document.querySelectorAll('.seg button[data-tab]').forEach((b) => {
  b.addEventListener('click', () => setTab(b.dataset.tab));
});

function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function showPanel() { $('gate').classList.add('hidden'); $('panel').classList.remove('hidden'); }

async function run() {
  showPanel();
  const out = $('out');
  out.innerHTML = `<div class="card muted">Считаю на данных ${SRC}…</div>`;
  try {
    const month = $('month').value;
    const path = PROVIDER === 'wfirma' ? '/api/wfirma/overview'
      : { over: '/api/infakt/overview', close: '/api/infakt/close',
          pay: '/api/infakt/pay' }[tab];
    const d = await api(path, { month });
    if (d.connected === false) { showGate(); return; }
    out.innerHTML = tab === 'over' ? renderOverview(d)
      : tab === 'close' ? renderClose(d) : renderPay(d);
    Icons?.hydrate?.();
    if (tab === 'over') bindOverview(d);
    out.querySelectorAll('[data-copy]').forEach((b) => b.addEventListener('click', () => {
      navigator.clipboard?.writeText(b.dataset.copy); b.textContent = 'Скопировано ✓';
    }));
  } catch (e) {
    out.innerHTML = `<div class="card">${esc(String(e.message || e))}</div>`;
  }
}

/* ── Обзор ──────────────────────────────────────────────────────────────────
   График — стек «прибыль + налоги + расходы = доход» за 12 месяцев: одна шкала,
   сумма сегментов равна доходу месяца. Цвета — категориальные слоты 1–3 из
   палитры dataviz (валидированы для светлой и тёмной поверхности Telegram);
   в светлой теме два из трёх ниже 3:1 к фону, поэтому обязательны легенда,
   подписи и табличный вид — они здесь есть. */
const zl = (x) => (x || 0).toLocaleString('pl-PL',
  { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + ' zł';
const MONTH_SHORT = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
const mLabel = (m) => MONTH_SHORT[+m.slice(5, 7) - 1];
// Три сегмента, а не четыре: на 390px бар шириной ~20px, дробить ZUS и налог
// нечитаемо — разбивка есть в подсказке по тапу и в таблице.
const SERIES = [
  { name: 'Прибыль', val: (r) => r.profit },
  { name: 'Налоги (ZUS + PIT)', val: (r) => r.zus + r.tax },
  { name: 'Расходы', val: (r) => r.costs },
];

// у wFirma нет начислений ZUS и налога: сегмент «Налоги» не рисуем вовсе,
// иначе нулевой сегмент выглядел бы как «налогов нет»
const seriesFor = (d) => d.taxes_available === false
  ? [{ name: 'Прибыль до налогов', val: (r) => r.profit }, { name: 'Расходы', val: (r) => r.costs, slot: 3 }]
  : SERIES;

function chartSVG(series, segs = SERIES) {
  const W = 320, H = 132, PAD_B = 16, PAD_T = 6;
  const n = series.length, gap = 5;
  const bw = (W - gap * (n - 1)) / n;
  const max = Math.max(...series.map((r) =>
    Math.max(r.income, r.costs + r.zus + r.tax)), 1);
  const y = (v) => PAD_T + (H - PAD_B - PAD_T) * (1 - v / max);
  let bars = '', labels = '';
  series.forEach((r, i) => {
    const x = i * (bw + gap);
    let acc = 0;
    segs.forEach((s, si) => {
      const v = Math.max(0, s.val(r));
      if (v <= 0) return;
      const y0 = y(acc), y1 = y(acc + v);
      const h = Math.max(1, y0 - y1 - 2);              // 2px просвет между сегментами
      const isTop = si === 0 || acc + v >= (r.income - 0.5);
      bars += `<rect x="${x.toFixed(1)}" y="${y1.toFixed(1)}" width="${bw.toFixed(1)}"
        height="${h.toFixed(1)}" rx="${isTop ? 3 : 0}" fill="var(--s${s.slot || si + 1})"></rect>`;
      acc += v;
    });
    bars += `<rect class="hit" data-i="${i}" x="${(x - gap / 2).toFixed(1)}" y="0"
      width="${(bw + gap).toFixed(1)}" height="${H - PAD_B}" fill="transparent"></rect>`;
    if (i % 2 === n % 2) labels += `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 4}"
      text-anchor="middle" class="ax">${mLabel(r.month)}</text>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img"
    aria-label="Доход, налоги и расходы за 12 месяцев">
    <line x1="0" y1="${H - PAD_B}" x2="${W}" y2="${H - PAD_B}" class="base"></line>
    ${bars}${labels}</svg>`;
}

function renderOverview(d) {
  const m = d.month, ytd = d.ytd, h = d.health, v = d.vat;
  const prev = d.prev;
  const delta = prev && prev.income
    ? Math.round((m.income - prev.income) / prev.income * 100) : null;
  const deltaStr = delta === null ? ''
    : `<span class="delta ${delta >= 0 ? 'up' : 'down'}">${delta >= 0 ? '+' : ''}${delta}% к ${mLabel(prev.month)}</span>`;

  const taxes = d.taxes_available !== false;
  const segs = seriesFor(d);
  let s = d.company_name
    ? `<p class="muted" style="font-size:12px;margin:0 0 8px">${esc(d.company_name)}${
        d.tax_form ? ' · ' + esc(d.tax_form) : ''} · данные wFirma</p>` : '';
  s += `<div class="card kpis">
    <div class="kpi"><span class="l">Доход месяца</span><b>${zl(m.income)}</b>${deltaStr}</div>
    <div class="kpi"><span class="l">${taxes ? 'Прибыль после налогов' : 'Прибыль до налогов'}</span><b>${zl(m.profit)}</b></div>
    ${taxes ? `<div class="kpi"><span class="l">Обязательства месяца</span><b>${zl(m.zus + m.tax)}</b>
      <span class="sml">ZUS ${zl(m.zus)} + налог ${zl(m.tax)}</span></div>`
      : `<div class="kpi"><span class="l">Расходы месяца</span><b>${zl(m.costs)}</b>
      <span class="sml">ZUS и аванс налога wFirma по API не отдаёт</span></div>`}
  </div>`;

  if (d.jpk) {
    s += `<div class="card">
      <div class="h2" style="margin-bottom:6px">VAT за ${esc(d.jpk.period)} по JPK</div>
      <div class="row"><span>К оплате до 25-го</span><span class="v">${zl(d.jpk.to_pay)}</span></div>
      ${d.jpk.carry ? `<div class="row"><span>Переносится на следующий период</span>
        <span class="v">${zl(d.jpk.carry)}</span></div>` : ''}
      <p class="muted" style="font-size:11px;margin:4px 0 0">Сумма из декларации JPK_V7,
        которую посчитала wFirma.</p></div>`;
  }
  if (d.ksef) {
    s += `<div class="card">
      <div class="h2" style="margin-bottom:6px">KSeF</div>
      <div class="row"><span>Фактур с номером KSeF</span><span class="v">${d.ksef.with}</span></div>
      <div class="row"><span>С апреля 2026 без номера KSeF</span>
        <span class="v ${d.ksef.without ? 'bad' : 'ok'}">${d.ksef.without}</span></div></div>`;
  }

  s += `<div class="card">
    <div class="h2" style="margin-bottom:2px">Здоровье учёта
      <span class="hscore ${h.level}">${h.score}/100</span></div>
    <p class="muted" style="font-size:12px;margin:0 0 8px">Из 100 вычитается только то,
      что видно в данных — каждый пункт ниже.</p>
    ${h.items.map((i) => `<div class="hrow ${i.level}">
      <span class="hi" data-icon="${{ ok: 'check-circle', warn: 'alert-triangle', bad: 'x-circle' }[i.level]}"></span>
      <span>${esc(i.text)}</span>${i.cut ? `<span class="cut">−${i.cut}</span>` : ''}</div>`).join('')}
  </div>`;

  if (v) {
    s += `<div class="card">
      <div class="h2" style="margin-bottom:6px">Лимит VAT ${d.year}</div>
      <div class="meter"><span style="width:${Math.min(100, v.used_pct)}%"></span></div>
      <div class="row"><span>Использовано</span><span class="v">${zl(v.used)} · ${v.used_pct}%</span></div>
      <div class="row"><span>Осталось выставить</span><span class="v">${zl(v.left)}</span></div>
      ${v.pace ? `<div class="row"><span>Темп (3 мес.)</span><span class="v">${zl(v.pace)}/мес</span></div>` : ''}
      ${v.eta === 'уже превышен'
        ? '<div class="row"><span class="v bad">Лимит превышен — нужна регистрация VAT</span></div>'
        : v.eta === 'далеко'
          ? '<div class="row"><span>При текущем темпе лимита хватит больше чем на 2 года</span></div>'
          : v.eta
            ? `<div class="row"><span>При текущем темпе лимит закончится</span>
               <span class="v">${esc(v.eta)}${v.months_left ? ` · через ${v.months_left} мес.` : ''}</span></div>`
            : ''}
    </div>`;
  } else if (!d.jpk) {                 // у wFirma VAT уже показан карточкой JPK
    s += '<div class="card"><div class="row"><span>VAT</span><span class="v">плательщик · JPK до 25-го</span></div></div>';
  }

  s += `<div class="card">
    <div class="h2" style="margin-bottom:6px">12 месяцев</div>
    <div class="legend">${segs.map((x, i) =>
      `<span class="lg"><i style="background:var(--s${x.slot || i + 1})"></i>${x.name}</span>`).join('')}</div>
    <div class="chart">${chartSVG(d.series, segs)}</div>
    <div id="tip" class="tip muted">Нажми на столбец, чтобы увидеть месяц целиком</div>
    <p class="muted" style="font-size:11px;margin:0">Высота столбца — доход месяца.
      Если ${taxes ? 'налоги и расходы его превысили' : 'расходы его превысили'}, столбец
      выше дохода: месяц ушёл в минус.</p>
    <button class="copy" id="tblbtn" style="margin-top:8px">Показать таблицей</button>
    <div id="tbl" class="hidden tblwrap">
      <table class="tbl"><thead><tr><th>Месяц</th><th>Доход</th><th>Расходы</th>
        ${taxes ? '<th>ZUS</th><th>Налог</th>' : ''}<th>Прибыль</th></tr></thead><tbody>
        ${d.series.map((r) => `<tr><td>${esc(r.month)}</td><td>${zl(r.income)}</td>
          <td>${zl(r.costs)}</td>${taxes ? `<td>${zl(r.zus)}</td><td>${zl(r.tax)}</td>` : ''}
          <td>${zl(r.profit)}</td></tr>`).join('')}
      </tbody></table></div>
  </div>`;

  s += `<div class="card">
    <div class="h2" style="margin-bottom:6px">Итого за ${d.year}</div>
    <div class="row"><span>Доход</span><span class="v">${zl(ytd.income)}</span></div>
    <div class="row"><span>Расходы</span><span class="v">${zl(ytd.costs)}</span></div>
    ${taxes ? `<div class="row"><span>ZUS + налог</span><span class="v">${zl(ytd.zus + ytd.tax)}</span></div>` : ''}
    <div class="row"><span>${taxes ? 'Прибыль' : 'Прибыль до налогов'}</span><span class="v">${zl(ytd.profit)}</span></div>
    ${ytd.burden_pct !== null ? `<div class="row"><span>Эффективная нагрузка</span>
      <span class="v">${ytd.burden_pct}% от дохода</span></div>` : ''}
  </div>`;

  if (d.unpaid?.length) {
    s += `<div class="card"><div class="h2" style="margin-bottom:6px">Ждут оплаты
      <span class="badge">${zl(d.unpaid_total)}</span></div>
      ${d.unpaid.map((u) => `<div class="row"><span>${esc(u.number || '—')}</span>
        <span class="v">${zl(u.amount)} · до ${esc(u.due || '—')}</span></div>`).join('')}</div>`;
  }
  return s;
}

function bindOverview(d) {
  const tip = $('tip');
  document.querySelectorAll('.hit').forEach((el) => el.addEventListener('click', () => {
    const r = d.series[+el.dataset.i];
    haptic?.('light');
    tip.innerHTML = d.taxes_available === false
      ? `<b>${esc(r.month)}</b> · доход ${zl(r.income)} · расходы ${zl(r.costs)}
      · прибыль до налогов ${zl(r.profit)}`
      : `<b>${esc(r.month)}</b> · доход ${zl(r.income)} · расходы ${zl(r.costs)}
      · ZUS ${zl(r.zus)} · налог ${zl(r.tax)} · прибыль ${zl(r.profit)}`;
  }));
  const btn = $('tblbtn'), tbl = $('tbl');
  btn.addEventListener('click', () => {
    const shown = !tbl.classList.toggle('hidden');
    btn.textContent = shown ? 'Скрыть таблицу' : 'Показать таблицей';
  });
}

function renderClose(d) {
  const badge = d.verdict === 'PASS'
    ? '<span class="verdict ok">PASS</span>'
    : `<span class="verdict bad">${esc(d.verdict)}</span>`;
  let h = `<div class="card"><div class="h2" style="margin-bottom:6px">${esc(d.month)} · ${badge}</div>`;
  h += d.lines.map((l) => `<div class="row"><span>${esc(l)}</span></div>`).join('');
  h += '</div>';
  if (d.checks?.length) {
    h += '<div class="card"><div class="h2" style="margin-bottom:6px">Сверка Hermes ↔ inFakt</div>';
    h += d.checks.map((c) =>
      `<div class="row"><span>${c.ok ? '✅' : '🛑'} ${esc(c.name)}</span>
       <span class="v ${c.ok ? 'ok' : 'bad'}">${esc(c.hermes)}${c.ok ? '' : ' ≠ ' + esc(c.source)}</span></div>`
    ).join('');
    h += '</div>';
  }
  if (d.problems?.length) {
    h += '<div class="card"><div class="h2" style="margin-bottom:6px">Требует внимания</div>';
    h += d.problems.map((p) => `<div class="row"><span>⚠️ ${esc(p)}</span></div>`).join('');
    h += '</div>';
  }
  if (d.profile_notes?.length) {
    h += '<div class="card"><div class="muted" style="font-size:12px">' +
      d.profile_notes.map((n) => '· ' + esc(n)).join('<br>') + '</div></div>';
  }
  return h;
}

function renderPay(d) {
  if (!d.packages?.length) return '<div class="card">Нет обязательств за этот месяц</div>';
  return d.packages.map((p) => `
    <div class="card">
      <div class="h2" style="margin-bottom:4px">${esc(p.recipient)}
        ${p.status === 'paid' ? '<span class="badge">оплачено</span>' : ''}</div>
      <div class="row"><span>Сумма</span><span class="v">${esc(p.amount)} zł</span></div>
      <div class="row"><span>Срок</span><span class="v">${esc(p.deadline)}</span></div>
      <div class="row"><span>Тытул</span><span class="v">${esc(p.title)}</span></div>
      <div class="acct">${esc(p.account)}</div>
      <button class="copy" data-copy="${esc(p.account)}">Скопировать счёт</button>
    </div>`).join('') +
    '<p class="foot" style="margin-top:8px">Перевод делаешь сам в своём банке.</p>';
}

function showGate() { $('gate').classList.remove('hidden'); $('panel').classList.add('hidden'); }

$('run').addEventListener('click', run);
if (tg && tg.initData) run(); else showGate();
