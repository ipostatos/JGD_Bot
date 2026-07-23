"use strict";
// pdf.js 6 (legacy-сборка) поставляется только модулем — отсюда type="module"
// и импорт вместо глобального pdfjsLib. Обновлён с 3.11.174, где была
// CVE-2024-4367 (её мы и так гасили isEvalSupported:false).
// путь на уровень вверх: файл лежит в webapp/js/, а вендор — в webapp/vendor/
import * as pdfjsLib from "../vendor/pdfjs/pdf.mjs";
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); }
const $ = id => document.getElementById(id);
$("print").innerHTML = Icons.svg("printer");
$("dl").innerHTML = Icons.svg("download");

const params = new URLSearchParams(location.search);
const rawFile = params.get("file") || "";
document.title = params.get("title") || "Документ";
// надёжная «назад»: подписка на onClick И на событие (на разных клиентах приходит
// только одно), гард от двойного вызова; детерминированный уход при пустой истории
if (tg && tg.BackButton) {
  const bb = tg.BackButton; let busy = false;
  const goBack = () => {
    if (busy) return; busy = true; setTimeout(() => busy = false, 0);
    if (history.length > 1) history.back(); else location.replace('index.html');
  };
  bb.show();
  try { bb.offClick && bb.offClick(goBack); } catch (e) {}
  bb.onClick(goBack);
  try { tg.onEvent && tg.onEvent('backButtonClicked', goBack); } catch (e) {}
}

// БЕЗОПАСНОСТЬ: только свои файлы из docs/ (same-origin, без обхода каталога),
// иначе ?file=https://evil/x.pdf грузил бы чужой PDF в origin мини-аппа.
function safeDocPath(f){
  if (!/^docs\/[A-Za-z0-9_-]+\.pdf$/.test(f)) return "";
  if (f.indexOf("..") !== -1) return "";
  return f;
}
const file = safeDocPath(rawFile);
const fileUrl = file ? new URL(file, location.href).href : "";

function openExternal(){ if (tg && tg.openLink) tg.openLink(fileUrl); else window.open(fileUrl, "_blank"); }
$("dl").onclick = openExternal;

function fail(msg){
  $("status").classList.remove("hidden");
  $("pages").classList.add("hidden");
  $("status").innerHTML =
    `<div style="font-size:30px">${Icons.svg("file-text")}</div><div>${msg}</div>` +
    (fileUrl ? `<a id="ext">Открыть в браузере</a>` : "");
  const ext = $("ext"); if (ext) ext.onclick = openExternal;
}

if (!file){ fail("Документ не найден"); }
else if (!pdfjsLib || !pdfjsLib.getDocument){ fail("Просмотрщик не загрузился"); }
else {
  pdfjsLib.GlobalWorkerOptions.workerSrc = "vendor/pdfjs/pdf.worker.mjs";
  startReader();
}

function startReader(){
  let pdf = null, numPages = 0;
  let scale = 1;
  const rendered = new Set();
  const canvases = [];

  fetch(fileUrl, { method: "GET", headers: { Range: "bytes=0-0" } })
    .then(r => {
      if (!r.ok && r.status !== 206) {
        if (r.status === 404) throw new Error("Файл не найден на сервере");
        throw new Error("Сервер вернул ошибку " + r.status);
      }
      loadPdf();
    })
    .catch(e => fail(e.message || "Не удалось загрузить файл"));

  function loadPdf(){
    // isEvalSupported:false — не выполнять код из PDF (CVE-2024-4367)
    const task = pdfjsLib.getDocument({ url: fileUrl, disableStream:true, disableRange:true,
      disableAutoFetch:true, isEvalSupported:false });
    task.onProgress = p => {
      if (p.total){ $("loadBar").style.width = Math.round(p.loaded/p.total*100) + "%"; }
    };
    task.promise.then(doc => {
      pdf = doc; numPages = doc.numPages;
      $("status").classList.add("hidden");
      $("pages").classList.remove("hidden");
      buildPlaceholders();
      renderVisible();
      updateInfo();
    }).catch(err => fail("Не удалось открыть PDF: " + (err?.message || "формат не распознан")));
  }

  function buildPlaceholders(){
    const box = $("pages");
    box.innerHTML = "";
    const w = box.clientWidth - 20;
    for (let i = 1; i <= numPages; i++){
      const c = document.createElement("canvas");
      c.dataset.page = i;
      c.style.width = w + "px";
      c.style.height = "60vh";
      canvases[i] = c;
      box.appendChild(c);
    }
  }

  const dpr = () => Math.min(window.devicePixelRatio || 1, 2);

  function renderPage(i){
    if (rendered.has(i) || !pdf) return Promise.resolve();
    rendered.add(i);
    return pdf.getPage(i).then(page => {
      const containerW = $("pages").clientWidth - 20;
      const vp1 = page.getViewport({ scale: 1 });
      const eff = (containerW / vp1.width) * scale * dpr();
      const vp = page.getViewport({ scale: eff });
      const c = canvases[i];
      c.width = vp.width; c.height = vp.height;
      c.style.width  = (vp.width / dpr()) + "px";
      c.style.height = (vp.height / dpr()) + "px";
      return page.render({ canvasContext: c.getContext("2d", { alpha:false }),
        viewport: vp }).promise;
    }).catch(()=>{ rendered.delete(i); });
  }

  function renderVisible(){
    const box = $("pages");
    const top = box.scrollTop, bottom = top + box.clientHeight;
    for (let i = 1; i <= numPages; i++){
      const c = canvases[i];
      const y = c.offsetTop, h = c.offsetHeight || box.clientHeight;
      if (y + h >= top - box.clientHeight && y <= bottom + box.clientHeight) renderPage(i);
    }
    updateInfo();
  }

  function reRenderAll(){
    const box = $("pages");
    const ratio = box.scrollHeight ? box.scrollTop / box.scrollHeight : 0;
    const was = [...rendered];
    rendered.clear();
    was.forEach(renderPage);
    requestAnimationFrame(() => {
      box.scrollTop = ratio * box.scrollHeight;
      renderVisible();
    });
  }

  function updateInfo(){
    const box = $("pages");
    const center = box.scrollTop + box.clientHeight/2;
    let cur = 1, best = Infinity;
    for (let i = 1; i <= numPages; i++){
      const c = canvases[i]; const mid = c.offsetTop + (c.offsetHeight||0)/2;
      const d = Math.abs(mid - center);
      if (d < best){ best = d; cur = i; }
    }
    $("pageInfo").textContent = `${cur} / ${numPages}`;
    $("zoomLbl").textContent = Math.round(scale*100) + "%";
  }

  let scrollTmr;
  $("pages").addEventListener("scroll", () => {
    clearTimeout(scrollTmr); scrollTmr = setTimeout(renderVisible, 80);
  }, { passive:true });

  $("zoomIn").onclick = () => { scale = Math.min(scale*1.25, 4); reRenderAll(); };
  $("zoomOut").onclick = () => { scale = Math.max(scale/1.25, 0.5); reRenderAll(); };

  // печать: в мобильном Telegram WebView window.print() не работает —
  // открываем PDF системно (там есть печать/шеринг); на десктопе и в браузере
  // дорисовываем все страницы и печатаем
  $("print").onclick = async () => {
    const mobile = tg && (tg.platform === "ios" || tg.platform === "android");
    if (mobile) { openExternal(); return; }
    for (let i = 1; i <= numPages; i++) await renderPage(i);
    setTimeout(() => window.print(), 100);
  };

  // двойной тап — 100% ↔ 200%
  let lastTap = 0;
  $("pages").addEventListener("touchend", e => {
    const now = Date.now();
    if (now - lastTap < 300){
      scale = scale > 1.2 ? 1 : 2;
      reRenderAll();
      e.preventDefault();
    }
    lastTap = now;
  }, { passive:false });

  window.addEventListener("resize", () => { reRenderAll(); }, { passive:true });
}
