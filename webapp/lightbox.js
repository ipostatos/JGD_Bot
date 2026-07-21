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
    '#lbOverlay.open{display:flex; align-items:center; justify-content:center}' +
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
  var state = { scale:1, x:0, y:0, natW:0, natH:0, baseScale:1 };

  function apply(){
    // картинка центрирована flex'ом контейнера; зум и панорама — от центра.
    img.style.transform =
      "translate(" + state.x + "px," + state.y + "px) scale(" + state.scale + ")";
  }

  function open(src){
    // сбрасываем состояние сразу, чтобы при показе не мелькнул прошлый зум/сдвиг
    state.scale = 1; state.baseScale = 1; state.x = 0; state.y = 0;
    img.style.transform = "";
    img.src = src;
    img.onload = function(){
      // картинку вписываем в экран средствами CSS (max-width/height в пикселях),
      // дальнейший зум идёт поверх через scale. baseScale всегда 1.
      var maxW = window.innerWidth, maxH = window.innerHeight - 56;
      var r = Math.min(maxW / img.naturalWidth, maxH / img.naturalHeight, 1);
      img.style.width  = (img.naturalWidth  * r) + "px";
      img.style.height = (img.naturalHeight * r) + "px";
      state.scale = 1; state.baseScale = 1; state.x = 0; state.y = 0;
      apply();
    };
    overlay.classList.add("open");
    if (window.Telegram && window.Telegram.WebApp) {
      try { window.Telegram.WebApp.HapticFeedback.impactOccurred("light"); } catch(e){}
    }
  }
  function close(){ overlay.classList.remove("open"); img.src = ""; }

  function zoomBy(factor){
    state.scale = Math.max(state.baseScale, Math.min(state.scale * factor, state.baseScale * 8));
    apply();
  }
  function reset(){ state.scale = state.baseScale; state.x = 0; state.y = 0; apply(); }

  // кнопки панели
  overlay.querySelector("#lbBar").addEventListener("click", function(e){
    var b = e.target.closest("button"); if (!b) return;
    var a = b.dataset.act;
    if (a === "in") zoomBy(1.4);
    else if (a === "out") zoomBy(1/1.4);
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
  function dist(t){ var dx=t[0].clientX-t[1].clientX, dy=t[0].clientY-t[1].clientY; return Math.hypot(dx,dy); }
  function mid(t){ return { x:(t[0].clientX+t[1].clientX)/2, y:(t[0].clientY+t[1].clientY)/2 }; }

  overlay.addEventListener("touchstart", function(e){
    if (e.touches.length === 2){
      pinch = { d: dist(e.touches), s: state.scale };
      drag = null;
    } else if (e.touches.length === 1){
      drag = { x:e.touches[0].clientX, y:e.touches[0].clientY, ox:state.x, oy:state.y };
    }
  }, { passive:false });

  overlay.addEventListener("touchmove", function(e){
    if (pinch && e.touches.length === 2){
      e.preventDefault();
      var k = dist(e.touches) / pinch.d;
      state.scale = Math.max(state.baseScale, Math.min(pinch.s * k, state.baseScale * 8));
      apply();
    } else if (drag && e.touches.length === 1){
      e.preventDefault();
      state.x = drag.ox + (e.touches[0].clientX - drag.x);
      state.y = drag.oy + (e.touches[0].clientY - drag.y);
      apply();
    }
  }, { passive:false });

  overlay.addEventListener("touchend", function(e){
    if (e.touches.length === 0){ pinch = null; drag = null; }
  });

  // двойной тап в оверлее — 1x ↔ 2.5x
  var lastTap = 0;
  overlay.addEventListener("touchend", function(e){
    if (e.target.closest("#lbBar")) return;
    var now = Date.now();
    if (now - lastTap < 300){
      state.scale = state.scale > state.baseScale * 1.2 ? state.baseScale : state.baseScale * 2.5;
      state.x = 0; state.y = 0; apply();
      e.preventDefault();
    }
    lastTap = now;
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
