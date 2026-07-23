renderDock('about');

// Из какого состояния гайда собран контент: видно, насколько он свежий,
// и с чем сверяться, если в статье что-то устарело.
loadJSON('data/content.json').then(d => {
  const s = d.source || {};
  if (!s.date) return;
  const [y, m, day] = s.date.split('-');
  document.getElementById('guideSrc').textContent =
    `гайд, на котором построено приложение · версия от ${day}.${m}.${y}`;
}).catch(() => {});
