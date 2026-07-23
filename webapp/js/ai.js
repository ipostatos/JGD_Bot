renderDock('home');
setupBack('index.html');
const $ = id => document.getElementById(id);
$('send').innerHTML = Icons.svg('send');
const chat = $('chat');

// ── строка ввода и нижний бар при клавиатуре ──
// Проблемы: askbar налипал на dock (фикс bottom:64px < реальной высоты dock с
// safe-area), и оба прыгали при открытии клавиатуры. Решение: сажаем askbar точно
// на измеренную высоту dock; когда клавиатура открыта (visualViewport меньше окна)
// — поднимаем askbar над ней, dock прячем (иначе он за клавиатурой и «скачет»).
const askbar = document.querySelector('.askbar');
function layoutBar() {
  const dock = document.querySelector('.dock');
  const vv = window.visualViewport;
  const kb = vv ? Math.max(0, window.innerHeight - vv.height - vv.offsetTop) : 0;
  if (kb > 80) {                      // клавиатура открыта
    if (dock) dock.style.display = 'none';
    askbar.style.bottom = kb + 'px';
    document.body.style.paddingBottom = (kb + askbar.offsetHeight + 16) + 'px';
  } else {                            // покой: строго над dock
    if (dock) dock.style.display = '';                       // сперва показать dock,
    const dh = dock ? dock.getBoundingClientRect().height : 0; // потом мерить (иначе 0)
    askbar.style.bottom = dh + 'px';
    document.body.style.paddingBottom = (dh + askbar.offsetHeight + 16) + 'px';
  }
}
layoutBar();
if (window.visualViewport) {
  visualViewport.addEventListener('resize', layoutBar);
  visualViewport.addEventListener('scroll', layoutBar);
}
window.addEventListener('resize', layoutBar);
window.addEventListener('orientationchange', () => setTimeout(layoutBar, 200));

function add(kind, html) {
  const div = document.createElement('div');
  div.className = 'msg ' + kind;
  div.innerHTML = html;
  chat.appendChild(div);
  window.scrollTo(0, document.body.scrollHeight);
  return div;
}
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
// ответы модели: после экранирования разрешаем только **жирный**
const fmt = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');

let busy = false;
async function askAI(question) {
  if (busy || !question.trim()) return;
  if (!tg || !tg.initData) {
    add('a', '<div class="bub">Открой ассистента через Telegram, чтобы задавать вопросы.</div>');
    return;
  }
  busy = true; $('send').disabled = true; $('q').value = '';
  add('q', `<div class="bub">${esc(question)}</div>`);
  const wait = add('a', '<div class="bub typing"><i></i><i></i><i></i></div>');
  haptic('medium');
  try {
    const r = await fetch('/api/ask', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: tg.initData, question, profile: getProfile() }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || 'Ошибка');
    const srcs = (data.sources || []).map(s =>
      s.id.startsWith('chatfaq')
        ? `<span class="chip">${s.title}</span>`
        : `<a class="chip" style="color:var(--link)" href="article.html?id=${s.id}">${s.title}</a>`).join('');
    wait.innerHTML = `<div class="bub">${fmt(data.answer)}</div>` +
      (srcs ? `<div class="srcs">${srcs}</div>` : '');
    if (typeof data.left === 'number') $('left').textContent = data.left;
  } catch (e) {
    wait.innerHTML = `<div class="bub">⚠️ ${esc(e.message || 'Не получилось, попробуй ещё раз')}</div>`;
  }
  busy = false; $('send').disabled = false;
  window.scrollTo(0, document.body.scrollHeight);
}

$('send').onclick = () => askAI($('q').value);
$('q').addEventListener('keydown', e => { if (e.key === 'Enter') askAI($('q').value); });
chat.addEventListener('click', e => {
  const c = e.target.closest('.chip[data-q]');
  if (c) askAI(c.dataset.q);
});
