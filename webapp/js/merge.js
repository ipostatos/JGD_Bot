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

// Тип по «магическим байтам», а не по f.type: Android и часть вебвью отдают
// пустой или неверный MIME, из-за чего PDF уходил в картиночную ветку, а
// WebP/HEIC — в embedJpg, роняя всю склейку без указания виновного файла.
function sniff(data) {
  const b = new Uint8Array(data.slice(0, 12));
  const s = String.fromCharCode.apply(null, b);
  if (s.startsWith('%PDF')) return 'pdf';
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4E && b[3] === 0x47) return 'png';
  if (b[0] === 0xFF && b[1] === 0xD8 && b[2] === 0xFF) return 'jpg';
  if (s.startsWith('RIFF') && s.slice(8, 12) === 'WEBP') return 'webp';
  if (s.slice(4, 8) === 'ftyp') return 'heic';   // HEIC/HEIF контейнер
  return 'unknown';
}

const userError = (text) => { const e = new Error(text); e.userMessage = text; return e; };

// pdf.js уже лежит в приложении для читалки — грузим его только когда нужен
let pdfjs = null;
async function loadPdfjs() {
  if (!pdfjs) {
    pdfjs = await import('../vendor/pdfjs/pdf.mjs');
    pdfjs.GlobalWorkerOptions.workerSrc = 'vendor/pdfjs/pdf.worker.mjs';
  }
  return pdfjs;
}

// Зашифрованный PDF (банковские квитанции: пароль владельца, открываются без
// пароля). pdf-lib не умеет расшифровывать: с ignoreEncryption страницы
// копировались как есть и в склейке выходили ПУСТЫМИ, а мы писали «Готово».
// pdf.js расшифровывает — рисуем страницы и вкладываем их картинками.
async function addRasterized(out, f) {
  const lib = await loadPdfjs();
  // в pdf.js 6 документ закрывается через задачу загрузки, а не сам
  const task = lib.getDocument({ data: new Uint8Array(f.data.slice(0)),
    isEvalSupported: false });
  let doc;
  try {
    doc = await task.promise;
  } catch (e) {
    if (e && e.name === 'PasswordException') {
      throw userError(`Файл «${f.name}» закрыт паролем на открытие. Открой его, `
        + 'сохрани копию без пароля (или «Печать → Сохранить как PDF») и добавь снова.');
    }
    throw userError(`Файл «${f.name}» не получилось прочитать как PDF.`);
  }
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const size = page.getViewport({ scale: 1 });           // размер в пунктах
    const vp = page.getViewport({ scale: 2 });             // ~150 dpi для печати
    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(vp.width); canvas.height = Math.ceil(vp.height);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvasContext: ctx, viewport: vp }).promise;
    const blob = await new Promise(r => canvas.toBlob(r, 'image/jpeg', 0.9));
    const img = await out.embedJpg(await blob.arrayBuffer());
    out.addPage([size.width, size.height])
      .drawImage(img, { x: 0, y: 0, width: size.width, height: size.height });
  }
  await task.destroy();
}

const isEncrypted = (e) => e && (e.constructor?.name === 'EncryptedPDFError'
  || /encrypted/i.test(String(e.message)));

async function build() {
  const out = await PDFDocument.create();
  const autorot = $('autorot').checked, odd = $('oddstart').checked;
  const passport = $('passport').checked;
  let half = null;              // лист, у которого занята только верхняя половина
  let rasterized = 0;
  for (const f of files) {
    const kind = sniff(f.data);
    if (kind === 'webp' || kind === 'heic' || kind === 'unknown') {
      const e = new Error();
      e.userMessage = `Файл «${f.name}» в формате ${kind === 'unknown' ? 'непонятном' : kind.toUpperCase()} — `
        + 'PDF умеет вкладывать только JPG, PNG и PDF. Конвертируй его в JPG и добавь снова.';
      throw e;
    }
    const intoHalf = passport && kind !== 'pdf' && half;
    if (!intoHalf && odd && out.getPageCount() % 2 === 1) out.addPage(A4); // нечётный старт
    if (kind === 'pdf') {
      half = null;
      let src;
      try {
        src = await PDFDocument.load(f.data);
      } catch (e) {
        if (!isEncrypted(e)) throw userError(`Файл «${f.name}» не получилось прочитать как PDF.`);
        await addRasterized(out, f);
        rasterized++;
        continue;
      }
      (await out.copyPages(src, src.getPageIndices())).forEach(p => out.addPage(p));
    } else if (passport) {
      // два разворота на A4: верхняя и нижняя половины листа-портрета
      const img = kind === 'png' ? await out.embedPng(f.data) : await out.embedJpg(f.data);
      const [pw, ph] = A4, m = 18, gap = 12;
      const page = half || out.addPage(A4);
      const boxW = pw - 2 * m, boxH = (ph - 2 * m - gap) / 2;
      const k = Math.min(boxW / img.width, boxH / img.height);
      const w = img.width * k, h = img.height * k;
      const top = !half;
      const boxY = top ? m + boxH + gap : m;
      page.drawImage(img, { x: (pw - w) / 2, y: boxY + (boxH - h) / 2, width: w, height: h });
      half = top ? page : null;
    } else {
      const img = kind === 'png'
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
  return { bytes: await out.save(), rasterized };
}

const rasterNote = (n) => n ? ` Зашифрованных PDF: ${n} — вложены картинками, `
  + 'текст в них не выделяется, для печати это не важно.' : '';

$('make').onclick = async () => {
  $('msg').textContent = 'Собираю…'; haptic('medium');
  try {
    const { bytes, rasterized } = await build();
    const mobile = tg && (tg.platform === 'ios' || tg.platform === 'android');
    if (mobile && tg.initData) {
      // мобильный Telegram: blob не скачивается — публикуем на час и открываем системно
      const r = await fetch('/api/tmpfile?ext=pdf', { method: 'POST',
        headers: { 'x-init-data': tg.initData }, body: bytes });
      if (!r.ok) throw 0;
      const { url } = await r.json();
      tg.openLink(new URL(url, location.href).href);
      $('msg').textContent = 'Готово! PDF открыт — оттуда можно сохранить или напечатать.'
        + rasterNote(rasterized);
    } else {
      const blob = new Blob([bytes], { type: 'application/pdf' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'sklejka.pdf'; a.click();
      $('msg').textContent = 'Готово! Файл скачан.' + rasterNote(rasterized);
    }
  } catch (e) {
    console.error('merge failed', e);
    // если знаем, какой файл виноват — говорим прямо, а не общим «проверь файлы»
    $('msg').textContent = (e && e.userMessage)
      || 'Не получилось собрать — проверь файлы и попробуй снова.';
  }
};

refresh();
