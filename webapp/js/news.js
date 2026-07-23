renderDock('home');
setupBack('index.html');

const TOPIC_ICON = { 'ZUS': 'shield-check', 'VAT': 'file-check', 'PIT': 'coins',
  'ryczałt': 'coins', 'KSeF': 'file-text', 'легализация': 'flag' };

// Лента строится из ЧУЖОГО текста: заголовки скрейпятся с gov-сайтов, выжимку
// пишет модель. Без экранирования разметка оттуда исполнилась бы у всех
// читателей, а javascript:-ссылка приехала бы в href.
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function safeUrl(u) {
  return /^https?:\/\//i.test(String(u || '')) ? esc(u) : '#';
}

loadJSON('/api/news').then(({ items }) => {
  const feed = document.getElementById('feed');
  if (!items.length) {
    feed.innerHTML = '<div class="state">Пока тихо — важных изменений нет.<br>Как появятся, будут здесь (и пушем, если подпишешься).</div>';
    return;
  }
  feed.innerHTML = items.map(n => {
    const d = new Date(n.found_at * 1000)
      .toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
    const imp = n.importance >= 3 ? '<span class="imp3">важно · действуй</span>'
      : n.importance === 2 ? '<span class="imp2">стоит прочитать</span>' : '';
    return `<div class="card news-card">
      <div class="src"><span class="chip">${esc(n.source)}</span><span>${d}</span>${imp}</div>
      <div class="tt">${esc(n.title)}</div>
      <div class="sm">${esc(n.summary || '')}</div>
      <div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">
        ${(n.topics || []).map(t => `<span class="chip">${esc(t)}</span>`).join('')}
        <a class="chip" style="color:var(--link)" href="${safeUrl(n.url)}" target="_blank" rel="noopener">оригинал ↗</a>
      </div></div>`;
  }).join('');
}).catch(() => {
  document.getElementById('feed').innerHTML = '<div class="state">Не удалось загрузить ленту</div>';
});

// подписка
const SKEY = 'jdg_news_sub';
const btn = document.getElementById('subbtn');
function syncBtn() {
  const on = localStorage.getItem(SKEY) === '1';
  btn.textContent = on ? '🔕 Отключить уведомления' : '🔔 Включить уведомления';
  btn.classList.toggle('ghost', on);
}
btn.onclick = async () => {
  if (!tg || !tg.initData) {
    document.getElementById('subhint').textContent = 'Открой через Telegram, чтобы подписаться.';
    return;
  }
  haptic('medium');
  const on = localStorage.getItem(SKEY) === '1';
  const p = getProfile();
  try {
    const r = await fetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: tg.initData, news_sub: !on,
        form: p.form || 'unknown', vat: !!p.vat }),
    });
    if (!r.ok) throw 0;
    localStorage.setItem(SKEY, on ? '0' : '1');
    syncBtn();
  } catch { document.getElementById('subhint').textContent = 'Не получилось, попробуй позже.'; }
};
syncBtn();
