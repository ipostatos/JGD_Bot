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
      { k: 'home', href: 'index.html', ic: 'house', t: 'Главная' },
      { k: 'guide', href: 'guide.html', ic: 'book-open', t: 'Гайд' },
      { k: 'calc', href: 'calc.html', ic: 'calculator', t: 'Расчёты' },
      { k: 'plan', href: 'plan.html', ic: 'calendar', t: 'План' },
      { k: 'about', href: 'about.html', ic: 'star', t: 'Ещё' },
    ];
    const dock = document.createElement('nav');
    dock.className = 'dock';
    dock.innerHTML = items.map(i =>
      `<a href="${i.href}" class="${i.k === active ? 'on' : ''}">` +
      `<span class="di">${Icons.svg(i.ic)}</span><span class="dt">${i.t}</span></a>`).join('');
    document.body.appendChild(dock);
    document.body.classList.add('has-dock');
  };

  // авто-подстановка SVG по data-icon после загрузки DOM (паттерн ISSA)
  document.addEventListener('DOMContentLoaded', () => window.Icons && Icons.hydrate());

  // Надёжная кнопка «Назад» (паттерн ISSA nav.js). Раньше страницы вешали только
  // bb.onClick — но на части клиентов приходит ТОЛЬКО событие backButtonClicked
  // (и наоборот), отсюда «иногда не срабатывает». Подписываемся на оба, гардом
  // схлопываем двойной вызов. target: URL-строка или функция. Перед переходом
  // даём шанс window.__navBack() (лайтбокс/внутренняя навигация страницы).
  window.setupBack = function (target) {
    if (!tg || !tg.BackButton) return;
    const bb = tg.BackButton;
    let busy = false;
    function goBack() {
      if (busy) return;
      busy = true; setTimeout(() => busy = false, 0);
      if (typeof window.__navBack === 'function') {
        try { if (window.__navBack() === true) return; } catch (e) {}
      }
      if (typeof target === 'function') { target(); return; }
      location.replace(target || 'index.html');  // href/history.back в webview ненадёжны
    }
    bb.show();
    try { bb.offClick && bb.offClick(goBack); } catch (e) {}
    bb.onClick(goBack);
    try { tg.onEvent && tg.onEvent('backButtonClicked', goBack); } catch (e) {}
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
