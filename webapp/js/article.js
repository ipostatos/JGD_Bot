renderDock('guide');
const id = (qs.get('id') || 'index').replace(/[^\w-]/g, '');
const art = document.getElementById('art');

// системная кнопка «назад» Telegram
// __navBack (лайтбокс) обрабатывается внутри setupBack: сперва закрыть зум, потом уйти
setupBack(() => history.length > 1 ? history.back() : location.replace('index.html'));

fetch('data/articles/' + id + '.html')
  .then(r => { if (!r.ok) throw 0; return r.text(); })
  .then(html => {
    art.innerHTML = html;
    window.Icons?.hydrate?.(art);   // Lucide-иконки в callout-блоках статьи
    document.getElementById('srclink').href = 'https://sobolevbel.github.io/jdg/' + id + '/';
    // подсветка искомого слова при переходе из поиска
    const q = qs.get('q');
    if (q) {
      const walker = document.createTreeWalker(art, NodeFilter.SHOW_TEXT);
      const rx = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'i');
      const nodes = [];
      while (walker.nextNode()) if (rx.test(walker.currentNode.data)) nodes.push(walker.currentNode);
      nodes.slice(0, 50).forEach(n => {
        const span = document.createElement('span');
        span.innerHTML = n.data.replace(rx, '<mark>$1</mark>');
        n.replaceWith(span);
      });
      const first = art.querySelector('mark');
      if (first) setTimeout(() => first.scrollIntoView({ block: 'center' }), 50);
    }
    if (location.hash) {
      const el = document.querySelector(location.hash);
      if (el) el.scrollIntoView();
    }
    // словарь: живой фильтр по пунктам списка
    if (id === 'glossary') {
      document.getElementById('glossbar').classList.remove('hidden');
      const items = [...art.querySelectorAll('ul > li')];
      document.getElementById('gq').addEventListener('input', (e) => {
        const s = e.target.value.trim().toLowerCase();
        items.forEach(li =>
          li.style.display = !s || li.textContent.toLowerCase().includes(s) ? '' : 'none');
      });
    }
  })
  .catch(() => art.innerHTML = '<div class="state">Статья не найдена 🤷<br><a href="index.html">На главную</a></div>');
