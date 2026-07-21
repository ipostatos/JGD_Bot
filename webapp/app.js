/* JDG Гид — общий каркас Mini App: Telegram init, нижний dock, утилиты. */
(function () {
  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) { tg.ready(); tg.expand(); }
  window.tg = tg;

  window.fmtZl = (x) => (Math.round(x * 100) / 100)
    .toLocaleString('pl-PL', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' zł';

  window.qs = new URLSearchParams(location.search);

  // нижняя навигация; active — ключ текущей страницы
  window.renderDock = function (active) {
    const items = [
      { k: 'home', href: 'index.html', ic: '📖', t: 'Гайд' },
      { k: 'calc', href: 'calc.html', ic: '🧮', t: 'Расчёты' },
      { k: 'plan', href: 'plan.html', ic: '📅', t: 'План' },
      { k: 'about', href: 'about.html', ic: '⭐', t: 'Ещё' },
    ];
    const dock = document.createElement('nav');
    dock.className = 'dock';
    dock.innerHTML = items.map(i =>
      `<a href="${i.href}" class="${i.k === active ? 'on' : ''}">` +
      `<span class="di">${i.ic}</span><span class="dt">${i.t}</span></a>`).join('');
    document.body.appendChild(dock);
    document.body.classList.add('has-dock');
  };

  window.loadJSON = async function (url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(url + ' -> ' + r.status);
    return r.json();
  };

  // профиль предпринимателя — localStorage (MVP)
  const PKEY = 'jdg_profile_v1';
  window.getProfile = () => {
    try { return JSON.parse(localStorage.getItem(PKEY)) || {}; }
    catch { return {}; }
  };
  window.setProfile = (p) => localStorage.setItem(PKEY, JSON.stringify(p));

  window.haptic = (type) => {
    try { tg && tg.HapticFeedback.impactOccurred(type || 'light'); } catch (e) { }
  };
})();
