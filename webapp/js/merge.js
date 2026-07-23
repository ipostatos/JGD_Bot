renderDock('about');
const { PDFDocument } = PDFLib;
const A4 = [595.28, 841.89];
const files = [];
const $ = id => document.getElementById(id);

function refresh() {
  $('flist').innerHTML = files.map((f, i) =>
    `<div class="res-row"><span>${i + 1}. ${f.name}</span>
     <span class="v muted">${(f.data.byteLength / 1024).toFixed(0)} КБ</span></div>`).join('')
    || '<div class="muted" style="font-size:13px">Пока ничего не добавлено</div>';
  const has = files.length > 0;
  $('undo').disabled = $('clear').disabled = $('make').disabled = !has;
}

$('pick').addEventListener('change', async (e) => {
  for (const f of e.target.files) {
    files.push({ name: f.name, type: f.type, data: await f.arrayBuffer() });
  }
  e.target.value = '';
  haptic(); refresh();
});
$('undo').onclick = () => { files.pop(); refresh(); };
$('clear').onclick = () => { files.length = 0; refresh(); };

async function build() {
  const out = await PDFDocument.create();
  const autorot = $('autorot').checked, odd = $('oddstart').checked;
  for (const f of files) {
    if (odd && out.getPageCount() % 2 === 1) out.addPage(A4); // добить до нечётного старта
    if (f.type === 'application/pdf') {
      const src = await PDFDocument.load(f.data, { ignoreEncryption: true });
      (await out.copyPages(src, src.getPageIndices())).forEach(p => out.addPage(p));
    } else {
      const img = f.type === 'image/png'
        ? await out.embedPng(f.data) : await out.embedJpg(f.data);
      const landscape = autorot && img.width > img.height;
      const [pw, ph] = landscape ? [A4[1], A4[0]] : A4;
      const page = out.addPage([pw, ph]);
      const m = 18; // поля ~6 мм
      const k = Math.min((pw - 2 * m) / img.width, (ph - 2 * m) / img.height);
      const w = img.width * k, h = img.height * k;
      page.drawImage(img, { x: (pw - w) / 2, y: (ph - h) / 2, width: w, height: h });
    }
  }
  return out.save();
}

$('make').onclick = async () => {
  $('msg').textContent = 'Собираю…'; haptic('medium');
  try {
    const bytes = await build();
    const mobile = tg && (tg.platform === 'ios' || tg.platform === 'android');
    if (mobile && tg.initData) {
      // мобильный Telegram: blob не скачивается — публикуем на час и открываем системно
      const r = await fetch('/api/tmpfile?ext=pdf', { method: 'POST',
        headers: { 'x-init-data': tg.initData }, body: bytes });
      if (!r.ok) throw 0;
      const { url } = await r.json();
      tg.openLink(new URL(url, location.href).href);
      $('msg').textContent = 'Готово! PDF открыт — оттуда можно сохранить или напечатать.';
    } else {
      const blob = new Blob([bytes], { type: 'application/pdf' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'sklejka.pdf'; a.click();
      $('msg').textContent = 'Готово! Файл скачан.';
    }
  } catch { $('msg').textContent = 'Не получилось собрать — проверь файлы и попробуй снова.'; }
};

refresh();
