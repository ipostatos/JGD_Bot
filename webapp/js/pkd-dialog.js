/* Точный подбор PKD: диалог вместо одной строки поиска.
 *
 * Клиент ничего не решает про мебель. Он рисует то, что вернул сервер:
 * вопрос с его вариантами, деятельности с их именами и кодами. Ни одного
 * `if (question.id === 'furniture...')` здесь быть не должно — иначе следующая
 * отрасль потребует переписывать интерфейс, а правила разъедутся между
 * сервером и браузером.
 *
 * Состояние живёт на странице и умещается в четыре поля: текст, задача,
 * ответы, последний ответ сервера. Сервер не хранит ничего, поэтому «Назад» —
 * это «убрать последний ответ и спросить заново», а не откат по своей истории.
 * sessionStorage — только необязательное восстановление после случайного
 * refresh, но не источник истины: при любом сомнении переспрашиваем сервер.
 */
(() => {
  const root = document.getElementById('dialog');
  if (!root) return;

  const KEY = 'pkd_dialog_v1';
  const S = { query: '', intent: 'find_codes', answers: [], last: null,
              busy: false, seq: 0 };
  let controller = null;
  // экран сам знает, какие кнопки выключены не из-за загрузки, а по смыслу
  // («Продолжить» без выбранного варианта); после снятия блокировки их
  // состояние надо вернуть, иначе кнопка выглядит рабочей и ничего не делает
  let syncControls = null;

  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // ── обезличенный счётчик разговоров ──────────────────────────────────────
  // Случайные 16 hex живут во вкладке и нужны ровно для того, чтобы один
  // разговор не считался пятью. Ни к пользователю, ни к Telegram, ни к IP
  // это не привязано; вкладку закрыли — идентификатора нет.
  const SKEY = 'pkd_dialog_sid';
  let sid = '';
  try {
    sid = sessionStorage.getItem(SKEY) || '';
    if (!/^[0-9a-f]{16}$/.test(sid)) {
      const b = new Uint8Array(8);
      crypto.getRandomValues(b);
      sid = [...b].map(x => x.toString(16).padStart(2, '0')).join('');
      sessionStorage.setItem(SKEY, sid);
    }
  } catch (e) { sid = ''; }            // приватный режим — просто не считаемся

  function note(event) {
    // сообщать интерфейсные события — дело необязательное: молчащая
    // аналитика не должна ломать подбор, поэтому ошибки глотаем
    try {
      fetch('/api/pkd/dialog/event', {
        method: 'POST', keepalive: true,
        headers: { 'Content-Type': 'application/json', 'X-Dialog-Session': sid },
        body: JSON.stringify({ event }),
      }).catch(() => {});
    } catch (e) {}
  }

  // ── необязательное восстановление после refresh ──────────────────────────
  function persist() {
    try {
      sessionStorage.setItem(KEY, JSON.stringify(
        { query: S.query, intent: S.intent, answers: S.answers }));
    } catch (e) { /* приватный режим — просто не восстановимся */ }
  }
  function forget() { try { sessionStorage.removeItem(KEY); } catch (e) {} }
  function restore() {
    try {
      const v = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (v && typeof v.query === 'string' && Array.isArray(v.answers)) return v;
    } catch (e) {}
    return null;
  }

  // ── обмен с сервером ─────────────────────────────────────────────────────
  async function ask() {
    if (controller) controller.abort();            // старый ответ не должен
    controller = new AbortController();            // перерисовать новый диалог
    const mySeq = ++S.seq;
    setBusy(true);
    persist();
    try {
      const r = await fetch('/api/pkd/dialog', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Dialog-Session': sid },
        signal: controller.signal,
        body: JSON.stringify({
          query: S.query, intent: S.intent, answers: S.answers,
          dialog_schema_version: 1,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (mySeq !== S.seq) return;                 // пришёл ответ на старый запрос
      if (!r.ok) return httpError(r.status, data);
      S.last = data;
      render(data);
    } catch (e) {
      if (e.name === 'AbortError') return;
      renderError('Не удалось выполнить подбор. Ваши ответы сохранены на этой '
        + 'странице — попробуйте ещё раз.', true);
    } finally {
      if (mySeq === S.seq) setBusy(false);
    }
  }

  function httpError(status, data) {
    const err = (data && data.detail) || {};
    if (status === 409) {
      // вопрос обновился: снимаем устаревший ответ и пересчитываем заново,
      // технических версий человеку не показываем
      S.answers = S.answers.filter(a => a.question_id !== err.question_id);
      renderError('Вопрос обновился. Проверьте ответ ещё раз.');
      return ask();
    }
    if (status === 422) return renderError(
      'Ответ больше не подходит к текущему вопросу. Выберите вариант ещё раз.');
    if (status === 413) return renderError(
      'Описание слишком длинное. Сократите его до основных действий и услуг.');
    if (status === 429) return renderError(
      'Слишком много запросов. Попробуйте немного позже.');
    if (status === 404) return renderError('Точный подбор сейчас недоступен.');
    // 400 и 500 одинаково не показываем сырой текст бэкенда
    renderError('Не удалось выполнить подбор. Ваши ответы сохранены на этой '
      + 'странице — попробуйте ещё раз.', true);
  }

  function setBusy(on) {
    S.busy = on;
    // блокируем повторную отправку, но текущий вопрос не разбираем
    root.querySelectorAll('button, input').forEach(el => { el.disabled = on; });
    if (!on && syncControls) syncControls();
    const l = document.getElementById('dlg-loading');
    if (l) l.textContent = on ? 'Подбираем…' : '';
  }

  // ── начальный экран ──────────────────────────────────────────────────────
  function start(prev) {
    S.answers = [];
    S.last = null;
    syncControls = null;
    root.innerHTML = `
      <div class="card" style="margin-bottom:12px">
        <div class="h2" style="margin-bottom:4px" id="dlg-head" tabindex="-1">
          Опишите, чем вы занимаетесь</div>
        <p class="muted" style="font-size:13px;margin:0 0 10px">
          Например: «делаю мебель на заказ и устанавливаю встроенные кухни».
          Пока режим понимает мебель, столярные изделия, окна и двери.</p>
        <div class="field"><label for="dlg-q">Чем занимаетесь</label>
          <input id="dlg-q" type="text" autocomplete="off" maxlength="300"
                 placeholder="делаю мебель на заказ / ремонтирую шкафы"></div>
        <fieldset style="border:0;padding:0;margin:0 0 10px">
          <legend class="muted" style="font-size:13px;padding:0">Что нужно</legend>
          <label class="opt"><input type="radio" name="dlg-intent" value="find_codes" checked>
            <span>Найти подходящие коды</span></label>
          <label class="opt"><input type="radio" name="dlg-intent" value="find_codes_and_primary">
            <span>Определить также главный код</span></label>
        </fieldset>
        <button id="dlg-go" class="btn">Продолжить</button>
        <div id="dlg-loading" class="muted" aria-live="polite" style="font-size:13px"></div>
      </div>`;
    const input = document.getElementById('dlg-q');
    input.value = (prev && prev.query) || S.query;
    if (prev && prev.intent) {
      const r = root.querySelector(`input[name="dlg-intent"][value="${prev.intent}"]`);
      if (r) r.checked = true;
    }
    document.getElementById('dlg-go').onclick = () => {
      S.query = input.value.trim();
      S.intent = root.querySelector('input[name="dlg-intent"]:checked').value;
      if (S.query) ask();
    };
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); document.getElementById('dlg-go').click(); }
    });
  }

  function reset() { note('reset'); forget(); S.query = ''; start(); }

  function render(d) {
    if (d.status === 'needs_clarification' && d.next_question)
      return renderQuestion(d.next_question);
    renderResult(d);
  }

  // ── экран вопроса ────────────────────────────────────────────────────────
  function renderQuestion(q) {
    const multi = q.type === 'multi_select';
    const opts = q.options.map(o => `
      <label class="opt">
        <input type="${multi ? 'checkbox' : 'radio'}" name="dlg-opt" value="${esc(o.id)}">
        <span>${esc(o.label)}</span>
      </label>`).join('');

    root.innerHTML = `
      <div class="card" style="margin-bottom:12px">
        <fieldset style="border:0;padding:0;margin:0">
          <legend class="h2" style="padding:0;margin-bottom:4px" tabindex="-1"
                  id="dlg-question">${esc(q.text)}</legend>
          ${q.help ? `<p class="muted" style="font-size:13px;margin:0 0 10px">${esc(q.help)}</p>` : ''}
          ${opts}
        </fieldset>
        <div class="ex" style="margin-top:12px">
          <button id="dlg-next" class="btn" disabled>Продолжить</button>
        </div>
        <div class="ex" style="margin-top:8px">
          <button id="dlg-back" class="btn ghost">Назад</button>
          <button id="dlg-reset" class="btn ghost">Начать заново</button>
        </div>
        <div id="dlg-loading" class="muted" aria-live="polite" style="font-size:13px"></div>
      </div>`;

    const next = document.getElementById('dlg-next');
    const chosen = () => [...root.querySelectorAll('input[name="dlg-opt"]:checked')]
      .map(i => i.value);
    syncControls = () => { next.disabled = chosen().length === 0; };
    root.querySelectorAll('input[name="dlg-opt"]').forEach(i => {
      // запрос по кнопке, а не по клику: случайный тап по варианту не должен
      // отправлять ответ и менять результат
      i.addEventListener('change', syncControls);
    });
    next.onclick = () => {
      const options = chosen();
      if (!options.length) return;
      S.answers = S.answers.filter(a => a.question_id !== q.id);
      S.answers.push({ question_id: q.id, question_version: q.version,
                       option_ids: options });
      ask();
    };
    document.getElementById('dlg-back').onclick = back;
    document.getElementById('dlg-reset').onclick = reset;
    document.getElementById('dlg-question').focus();
  }

  function back() {
    // сервер без состояния: возврат — это снять последний ответ и спросить
    // заново, а не откатиться на заранее сохранённый прошлый экран
    note('back');
    if (!S.answers.length) return start();
    S.answers.pop();
    ask();
  }

  // ── результаты ───────────────────────────────────────────────────────────
  function codeCard(c) {
    const why = (c.why || []).map(w => `<li>${esc(w)}</li>`).join('');
    return `
      <div class="pkd-code">${esc(c.code)}</div>
      <div class="pkd-name">${esc(c.name)}</div>
      ${why ? `<ul class="pkd-desc" style="margin:6px 0 0;padding-left:18px">${why}</ul>` : ''}`;
  }

  function unitBlock(u, showLabel) {
    // имя деятельности берём с сервера: сопоставление kind→человеку живёт
    // в правилах, не в интерфейсе
    const head = showLabel ? `<div class="h2" style="margin:0 0 4px;font-size:15px">${esc(u.label)}</div>` : '';
    if (u.state === 'outside_current_pack') {
      return `<div class="card" style="margin-bottom:10px">${head}
        <p class="pkd-desc" style="margin:0">Этот раздел точный подбор пока
        не поддерживает. Подходящий код существует — его подберёт обычный
        поиск.</p></div>`;
    }
    const codes = (u.codes || []).map(codeCard).join(
      '<hr style="border:0;border-top:1px solid var(--line);margin:10px 0">');
    return `<div class="card" style="margin-bottom:10px">${head}${codes}</div>`;
  }

  function primaryNote(d) {
    const st = d.primary_code_status;
    if (st === 'single_activity')
      return `<p class="pkd-desc">Главный код: <b>${esc(d.primary_code)}</b>.
        У вас одна самостоятельная деятельность, поэтому выбирать между
        направлениями не требуется.</p>`;
    if (st === 'user_selected')
      return `<p class="pkd-desc">Главный код по вашему ответу о выручке:
        <b>${esc(d.primary_code)}</b>.</p>`;
    if (st === 'revenue_information_required')
      return `<p class="pkd-desc">Чтобы определить главный код, нужно указать,
        какая деятельность будет давать наибольшую долю выручки.</p>`;
    if (st === 'activity_resolution_required')
      return `<p class="pkd-desc">Главный код пока определить нельзя: одна из
        деятельностей относится к разделу, который этот режим ещё не
        поддерживает.</p>`;
    return '';
  }

  function renderResult(d) {
    syncControls = null;
    const units = d.activity_units || [];
    const pack = d.status === 'resolved_package';
    let head, body, actions;

    if (d.status === 'unrecognized_activity') {
      head = `<div class="h2" style="margin-bottom:4px">Не удалось определить деятельность</div>`;
      body = `<div class="card" style="margin-bottom:10px"><p class="pkd-desc" style="margin:0">
        Попробуйте описать конкретное действие и объект: «ремонтирую мебель»,
        «устанавливаю встроенные кухни», «производим деревянные окна».</p></div>`;
      actions = `
        <button id="dlg-back" class="btn ghost">Изменить описание</button>
        <button id="dlg-legacy" class="btn ghost">Искать обычным способом</button>`;
    } else if (d.status === 'outside_current_pack') {
      head = `<div class="h2" style="margin-bottom:4px">Эта деятельность — другой раздел</div>`;
      body = units.map(u => unitBlock(u, false)).join('') ||
        `<div class="card" style="margin-bottom:10px"><p class="pkd-desc" style="margin:0">
         За такую деятельность отвечает другой набор правил, который пока
         не подключён. Код существует — его подберёт обычный поиск.</p></div>`;
      // routing_hint наружу как текст не показываем, но им решаем, что предложить
      actions = `
        <button id="dlg-back" class="btn ghost">Назад</button>
        <button id="dlg-legacy" class="btn ghost">Искать в обычном справочнике</button>
        <button id="dlg-reset" class="btn ghost">Начать новый подбор</button>`;
    } else {
      head = pack
        ? `<div class="h2" style="margin-bottom:8px">Вам могут понадобиться несколько кодов</div>`
        : `<div class="h2" style="margin-bottom:8px">Подходящий код</div>`;
      // в пакете подписываем каждую деятельность, в одиночном — код говорит сам
      body = units.map(u => unitBlock(u, pack)).join('');
      // «Назад» есть и здесь: ответ определяет код, и переигрывать его человек
      // должен одним нажатием, а не набором описания заново
      actions = `
        <button id="dlg-back" class="btn ghost">Назад</button>
        <button id="dlg-legacy" class="btn ghost">Проверить в обычном справочнике</button>
        <button id="dlg-reset" class="btn ghost">Начать новый подбор</button>`;
    }

    root.innerHTML = `
      <div id="dlg-result" tabindex="-1">
        ${head}${body}${primaryNote(d)}
        <div class="ex" style="margin-top:6px">${actions}</div>
        <div id="dlg-loading" class="muted" aria-live="polite" style="font-size:13px"></div>
      </div>`;

    const legacy = document.getElementById('dlg-legacy');
    if (legacy) legacy.onclick = () => {
      note('legacy');
      const q = document.getElementById('q');
      if (q) q.value = S.query;                    // переносим исходный запрос
      window.pkdSetMode && window.pkdSetMode('legacy');
      const go = document.getElementById('go');
      if (go) go.click();
    };
    const bck = document.getElementById('dlg-back');
    if (bck) bck.onclick = back;
    const rst = document.getElementById('dlg-reset');
    if (rst) rst.onclick = reset;
    document.getElementById('dlg-result').focus();
  }

  function renderError(message, retry) {
    const box = document.createElement('div');
    box.className = 'state';
    box.setAttribute('role', 'alert');
    box.textContent = message;
    if (retry) {
      const b = document.createElement('button');
      b.className = 'btn ghost';
      b.style.marginTop = '8px';
      b.textContent = 'Повторить';
      b.onclick = ask;
      box.appendChild(b);
    }
    const old = root.querySelector('[role="alert"]');
    if (old) old.remove();
    root.prepend(box);
  }

  // Стартуем с чистого экрана. Восстановление из sessionStorage предлагаем
  // только как удобство: подставляем прошлый текст и задачу, но заново их
  // не отправляем — источник истины всегда сервер.
  window.pkdDialogStart = () => start(restore());
  // открытие режима считаем один раз на вкладку: щёлканье между вкладками —
  // это не пять разных разговоров
  let opened = false;
  window.pkdDialogOpened = () => { if (!opened) { opened = true; note('start'); } };
  window.pkdDialogStart();
})();
