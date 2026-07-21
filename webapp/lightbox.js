// Лайтбокс для картинок: тап по любому <img> в зоне контента открывает картинку
// на весь экран с возможностью зума (pinch / двойной тап / кнопки) и панорамирования.
// Нужно там, где в схемах мелкий текст (памятка, конспект, шпаргалки).
//
// Подключение: <script src="lightbox.js" data-scope="#rBody"></script>
//   data-scope — CSS-селектор контейнера(ов), внутри которого картинки кликабельны.
//                Если не задан — берётся весь документ.
(function () {
  "use strict";

  var script = document.currentScript;
  var scopeSel = (script && script.getAttribute("data-scope")) || "body";

  // создаём оверлей один раз
  var overlay = document.createElement("div");
  overlay.id = "lbOverlay";
  overlay.innerHTML =
    '<img id="lbImg" alt="">' +
    '<div id="lbBar">' +
      '<button data-act="out">−</button>' +
      '<button data-act="reset">100%</button>' +
      '<button data-act="in">+</button>' +
      '<button data-act="close">✕</button>' +
    '</div>';
  var style = document.createElement("style");
  style.textContent =
    '#lbOverlay{position:fixed; inset:0; z-index:9999; background:rgba(0,0,0,.92);' +
      'display:none; touch-action:none; overflow:hidden}' +
    '#lbOverlay.open{display:flex; align-items:center; justify-content:center;' +
      'box-sizing:border-box; padding-bottom:56px}' +   // резерв под панель — центр в видимой зоне
    '#lbImg{max-width:none; max-height:none; transform-origin:center center;' +
      'will-change:transform; -webkit-user-select:none; user-select:none}' +
    '#lbBar{position:absolute; left:0; right:0; bottom:0; height:56px; display:flex; gap:8px;' +
      'align-items:center; justify-content:center; background:rgba(0,0,0,.55); padding:0 12px}' +
    '#lbBar button{width:46px; height:40px; border-radius:10px; border:1px solid rgba(255,255,255,.25);' +
      'background:rgba(255,255,255,.08); color:#fff; font-size:16px; cursor:pointer; line-height:1}' +
    '#lbBar button[data-act="reset"]{width:60px; font-size:13px}';
  document.head.appendChild(style);
  document.addEventListener("DOMContentLoaded", function(){ document.body.appendChild(overlay); });
  // если DOM уже готов
  if (document.body) document.body.appendChild(overlay);

  var img = overlay.querySelector("#lbImg");
  // fitW/fitH — размер вписанной картинки (при scale=1); нужны для границ панорамы.
  var state = { scale:1, x:0, y:0, fitW:0, fitH:0, baseScale:1 };

  // видимая зона = экран минус панель (её высота зарезервирована padding'ом оверлея)
  function viewW(){ return window.innerWidth; }
  function viewH(){ return window.innerHeight - 56; }

  function clampScale(s){
    return Math.max(state.baseScale, Math.min(s, state.baseScale * 8));
  }

  // жёсткие границы панорамы: картинка не уходит за край, а меньше экрана — центрируется
  function clampPan(){
    var maxX = Math.max(0, (state.fitW * state.scale - viewW()) / 2);
    var maxY = Math.max(0, (state.fitH * state.scale - viewH()) / 2);
    state.x = Math.max(-maxX, Math.min(state.x, maxX));
    state.y = Math.max(-maxY, Math.min(state.y, maxY));
  }

  function apply(){
    clampPan();  // без transition — отскока нет, край жёсткий
    img.style.transform =
      "translate(" + state.x + "px," + state.y + "px) scale(" + state.scale + ")";
  }

  // зум с якорем в точке (px,py) экрана: точка под пальцами/курсором остаётся на месте
  function zoomAt(factor, px, py){
    var ns = clampScale(state.scale * factor);
    var af = ns / state.scale;
    var icx = viewW() / 2 + state.x, icy = viewH() / 2 + state.y;  // центр картинки на экране
    state.x += (px - icx) * (1 - af);
    state.y += (py - icy) * (1 - af);
    state.scale = ns;
  }

  function open(src){
    state.scale = 1; state.baseScale = 1; state.x = 0; state.y = 0;
    img.style.transform = "";
    img.src = src;
    img.onload = function(){
      var r = Math.min(viewW() / img.naturalWidth, viewH() / img.naturalHeight, 1);
      state.fitW = img.naturalWidth * r;
      state.fitH = img.naturalHeight * r;
      img.style.width  = state.fitW + "px";
      img.style.height = state.fitH + "px";
      state.scale = 1; state.baseScale = 1; state.x = 0; state.y = 0;
      apply();
    };
    overlay.classList.add("open");
    if (window.Telegram && window.Telegram.WebApp) {
      try { window.Telegram.WebApp.HapticFeedback.impactOccurred("light"); } catch(e){}
    }
  }
  function close(){ overlay.classList.remove("open"); img.src = ""; }

  function reset(){ state.scale = state.baseScale; state.x = 0; state.y = 0; apply(); }

  // кнопки панели — зум от центра видимой зоны
  overlay.querySelector("#lbBar").addEventListener("click", function(e){
    var b = e.target.closest("button"); if (!b) return;
    var a = b.dataset.act;
    if (a === "in")        { zoomAt(1.4, viewW()/2, viewH()/2); apply(); }
    else if (a === "out")  { zoomAt(1/1.4, viewW()/2, viewH()/2); apply(); }
    else if (a === "reset") reset();
    else if (a === "close") close();
  });

  // тап по картинкам в зоне
  document.addEventListener("click", function(e){
    var im = e.target.closest("img");
    if (!im) return;
    if (!im.closest(scopeSel)) return;
    if (im.id === "lbImg") return;
    e.preventDefault();
    open(im.currentSrc || im.src);
  });

  // ── жесты внутри оверлея ──
  var drag = null, pinch = null;
  // gest — характер текущей серии касаний, чтобы отличить настоящий тап от pinch/пана
  var gest = { moved:false, multi:false, sx:0, sy:0 };
  var lastTap = 0, lastX = 0, lastY = 0;
  function dist(t){ var dx=t[0].clientX-t[1].clientX, dy=t[0].clientY-t[1].clientY; return Math.hypot(dx,dy); }
  function mid(t){ return { x:(t[0].clientX+t[1].clientX)/2, y:(t[0].clientY+t[1].clientY)/2 }; }

  // пересинхронизация жеста по текущему числу пальцев — убирает прыжок при
  // переходе «2 пальца → 1» и «1 → 2» (drag/pinch переинициализируются с места).
  function syncGesture(touches){
    if (touches.length >= 2){
      var m = mid(touches);
      pinch = { d: dist(touches), mx: m.x, my: m.y };
      drag = null;
    } else if (touches.length === 1){
      drag = { x: touches[0].clientX, y: touches[0].clientY, ox: state.x, oy: state.y };
      pinch = null;
    } else {
      pinch = null; drag = null;
    }
  }

  overlay.addEventListener("touchstart", function(e){
    if (e.target.closest("#lbBar")) return;
    // новая серия касаний (первый палец на экране) — сбрасываем характер жеста
    if (e.touches.length === e.changedTouches.length){
      gest.moved = false; gest.multi = false;
      gest.sx = e.changedTouches[0].clientX; gest.sy = e.changedTouches[0].clientY;
    }
    if (e.touches.length >= 2) gest.multi = true;   // был pinch — это уже не тап
    syncGesture(e.touches);
  }, { passive:false });

  overlay.addEventListener("touchmove", function(e){
    if (pinch && e.touches.length >= 2){
      e.preventDefault();
      var d = dist(e.touches), m = mid(e.touches);
      zoomAt(d / pinch.d, m.x, m.y);          // зум к точке между пальцами
      state.x += m.x - pinch.mx;              // + панорама за движением пальцев
      state.y += m.y - pinch.my;
      apply();
      pinch.d = d; pinch.mx = m.x; pinch.my = m.y;  // инкрементально — без дрейфа
    } else if (drag && e.touches.length === 1){
      e.preventDefault();
      var t = e.touches[0];
      if (Math.abs(t.clientX - gest.sx) > 10 || Math.abs(t.clientY - gest.sy) > 10) gest.moved = true;
      state.x = drag.ox + (t.clientX - drag.x);
      state.y = drag.oy + (t.clientY - drag.y);
      apply();
    }
  }, { passive:false });

  overlay.addEventListener("touchend", function(e){
    syncGesture(e.touches);  // остались пальцы — жест продолжается без прыжка

    if (e.target.closest("#lbBar")) return;
    if (e.touches.length !== 0) return;         // ждём полного отпускания всех пальцев
    if (gest.multi || gest.moved) return;       // был pinch/пан — не тап, двойной тап не считаем
    // настоящий двойной тап: два одиночных касания рядом и в пределах 300 мс
    var now = Date.now(), ct = e.changedTouches[0];
    if (now - lastTap < 300 && Math.hypot(ct.clientX - lastX, ct.clientY - lastY) < 40){
      var zoomedIn = state.scale > state.baseScale * 1.2;
      var target = zoomedIn ? state.baseScale : state.baseScale * 2.5;
      zoomAt(target / state.scale, ct.clientX, ct.clientY);   // зум к точке касания
      apply();
      e.preventDefault();
      lastTap = 0;                              // сброс, чтобы тройной тап не сработал
    } else {
      lastTap = now; lastX = ct.clientX; lastY = ct.clientY;
    }
  }, { passive:false });

  // одиночный клик по фону (не по картинке/панели) — закрыть
  overlay.addEventListener("click", function(e){
    if (e.target === overlay) close();
  });

  // системная «назад» Telegram внутри оверлея: закрыть лайтбокс, не уходя со страницы
  var prevNavBack = window.__navBack;
  window.__navBack = function(){
    if (overlay.classList.contains("open")){ close(); return true; }
    return typeof prevNavBack === "function" ? prevNavBack() : false;
  };
})();
