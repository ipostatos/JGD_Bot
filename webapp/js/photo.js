renderDock('about');
const $ = id => document.getElementById(id);
const cv = $('cv'), ctx = cv.getContext('2d');
let img = null, scale = 1, rot = 0, ox = 0, oy = 0; // трансформация фото
let fit = 1;                                       // масштаб «заполнить кадр»
const clampScale = (s) => Math.min(Math.max(s, fit * 0.25), fit * 12);

function draw() {
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
  if (!img) return;
  ctx.save();
  ctx.translate(cv.width / 2 + ox, cv.height / 2 + oy);
  ctx.rotate(rot * Math.PI / 180);
  ctx.scale(scale, scale);
  ctx.drawImage(img, -img.width / 2, -img.height / 2);
  ctx.restore();
}

$('pick').addEventListener('change', (e) => {
  const f = e.target.files[0]; if (!f) return;
  const url = URL.createObjectURL(f);
  const im = new Image();
  im.onload = () => {
    img = im; rot = 0; ox = 0; oy = 0;
    fit = Math.max(cv.width / im.width, cv.height / im.height);   // заполнить кадр
    scale = fit;
    $('editor').classList.remove('hidden');
    draw(); URL.revokeObjectURL(url);
  };
  im.src = url;
});

// Жесты: один палец — перетаскивание, два — масштаб (координаты в пикселях
// canvas). Раньше щипка не было вовсе, и жест доставался странице — приложение
// «уезжало» вместо зума. Поэтому: touch-action:none на #stage, preventDefault
// на указателях и глушение iOS-событий gesture* (их touch-action не покрывает).
const px = (e) => {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left) * cv.width / r.width,
          (e.clientY - r.top) * cv.height / r.height];
};
const stage = $('stage');
const pts = new Map();     // активные указатели: id -> [x, y]
let drag = null;           // база панорамы одним пальцем
let pinch = null;          // база жеста двумя пальцами

function pinchNow() {
  const [a, b] = [...pts.values()];
  return { d: Math.hypot(b[0] - a[0], b[1] - a[1]),
           c: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] };
}

function startPinch() {
  const s = pinchNow();
  pinch = { d: s.d || 1, c: s.c, scale0: scale, ox0: ox, oy0: oy };
  drag = null;
}

// Масштабируем вокруг точки между пальцами, а не вокруг центра кадра: иначе
// фото уползает из-под пальцев. Поворот на щипок намеренно не вешаем — лицо
// на фото MOS должно оставаться ровным, для разворота есть кнопки ⟲ ⟳.
function applyPinch() {
  const s = pinchNow();
  const k = clampScale(pinch.scale0 * (s.d / pinch.d)) / pinch.scale0;
  const o0 = [cv.width / 2 + pinch.ox0, cv.height / 2 + pinch.oy0];
  ox = s.c[0] + k * (o0[0] - pinch.c[0]) - cv.width / 2;
  oy = s.c[1] + k * (o0[1] - pinch.c[1]) - cv.height / 2;
  scale = pinch.scale0 * k;
  draw();
}

stage.addEventListener('pointerdown', (e) => {
  if (!img) return;
  e.preventDefault();
  try { stage.setPointerCapture(e.pointerId); } catch (err) { /* уже отпущен */ }
  pts.set(e.pointerId, px(e));
  if (pts.size === 2) startPinch();
  else if (pts.size === 1) drag = px(e);
});

stage.addEventListener('pointermove', (e) => {
  if (!pts.has(e.pointerId)) return;
  e.preventDefault();
  pts.set(e.pointerId, px(e));
  if (pts.size >= 2 && pinch) { applyPinch(); return; }
  if (drag) {
    const p = px(e);
    ox += p[0] - drag[0]; oy += p[1] - drag[1]; drag = p; draw();
  }
});

function pointerEnd(e) {
  pts.delete(e.pointerId);
  if (pts.size < 2) pinch = null;
  drag = pts.size === 1 ? [...pts.values()][0] : null;  // палец остался — панорама
}
stage.addEventListener('pointerup', pointerEnd);
stage.addEventListener('pointercancel', pointerEnd);

// Safari/iOS шлёт свои gesture*-события поверх указателей — без этого щипок
// зумит страницу целиком.
['gesturestart', 'gesturechange', 'gestureend'].forEach(t =>
  stage.addEventListener(t, (e) => e.preventDefault(), { passive: false }));
stage.addEventListener('touchmove', (e) => e.preventDefault(), { passive: false });

// колесо/трекпад на десктопе
stage.addEventListener('wheel', (e) => {
  if (!img) return;
  e.preventDefault();
  const p = px(e);
  const k = clampScale(scale * (e.deltaY < 0 ? 1.1 : 1 / 1.1)) / scale;
  ox = p[0] + k * (cv.width / 2 + ox - p[0]) - cv.width / 2;
  oy = p[1] + k * (cv.height / 2 + oy - p[1]) - cv.height / 2;
  scale *= k;
  draw();
}, { passive: false });

document.querySelector('.padbtns').addEventListener('click', (e) => {
  const a = e.target.closest('button')?.dataset.a; if (!a || !img) return;
  haptic();
  if (a === 'zin') scale = clampScale(scale * 1.06);
  if (a === 'zout') scale = clampScale(scale / 1.06);
  if (a === 'rotl') rot -= 90;
  if (a === 'rotr') rot += 90;
  if (a === 'up') oy -= 12;
  if (a === 'down') oy += 12;
  draw();
});

$('save').onclick = () => {
  haptic('medium');
  cv.toBlob(async (blob) => {
    const mobile = tg && (tg.platform === 'ios' || tg.platform === 'android');
    try {
      if (mobile && tg.initData) {
        const r = await fetch('/api/tmpfile?ext=jpg', { method: 'POST',
          headers: { 'x-init-data': tg.initData }, body: blob });
        if (!r.ok) throw 0;
        const { url } = await r.json();
        tg.openLink(new URL(url, location.href).href);
        $('msg').textContent = 'Готово! Фото открыто — сохрани из просмотрщика.';
      } else {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob); a.download = 'mos-photo.jpg'; a.click();
        $('msg').textContent = 'Готово! Файл скачан.';
      }
    } catch { $('msg').textContent = 'Не получилось сохранить, попробуй ещё раз.'; }
  }, 'image/jpeg', 0.92);
};

draw();
