renderDock('home');
setupBack('index.html');

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

fetch('banks.json').then(r => r.json()).then(d => {
  document.getElementById('out').innerHTML = d.blocks.map(b => `
    <div class="card" style="margin-bottom:12px">
      <div class="h2" style="margin-bottom:2px">${esc(b.title)}</div>
      <div class="b-short">${esc(b.short)}</div>
      <div class="b-text">${esc(b.text)}</div>
      ${(b.flags || []).map(f => `<div class="b-flag ${f.level}">
        <span class="fi" data-icon="${f.level === 'warn' ? 'alert-triangle' : 'info'}"></span>
        <span>${esc(f.text)}</span></div>`).join('')}
    </div>`).join('');
  document.getElementById('src').innerHTML =
    'Источники: <span class="src">' + d.sources.map(s =>
      `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title)}</a>`
    ).join(' · ') + '</span><br>Обновлено ' + esc(d.updated) +
    '. Справочная информация, не юридическая и не налоговая консультация.';
  Icons.hydrate();
}).catch(() => {
  document.getElementById('out').innerHTML =
    '<div class="state">Не получилось загрузить данные. Обнови страницу.</div>';
});
