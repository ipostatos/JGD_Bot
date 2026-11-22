renderDock('about');
const T = 'https://justandrei.github.io/jdg-tools/';
const doc = (slug, t, d) => ({ t, d, u: `reader.html?file=docs/${slug}.pdf&title=${encodeURIComponent(t)}` });
const CATS = [
  { t: 'ВНЖ по ИП (karta pobytu)', ic: 'id-card', items: [
    { t: 'Генератор заявки на ВНЖ', d: 'заполняет официальный PDF по шагам', u: T + 'wniosek', hot: true },
    { t: 'ВНЖ чек-лист А4', d: 'какие документы собрать', u: T + 'checklist', hot: true },
    doc('wojewodztwa', 'Подача по воеводствам', 'таблица: сроки, нюансы, контакты'),
    { t: 'Шаблон заявки на ВНЖ (Word)', d: 'заполняемый DOCX — скачать', u: 'docs/zayavka-vnzh-shablon.docx' },
    // чек-лист Андрея (10.2026): анкета, Oświadczenie cudzoziemca и Załącznik nr 1
    // для ИП больше не нужны — оставляем шаблоны, но говорим об этом прямо
    doc('anketa-przedsiebiorcy', 'Анкета предпринимателя', 'по чек-листу ВНЖ больше не нужна — шаблон на случай, если попросят'),
    doc('biznes-plan', 'Бизнес-план', 'нужен, если за прошлый год нет PIT или доход ниже порога воеводства'),
    doc('tabelka', '«Tabelka» — работа и налоги за год', 'заявление о расчётах за прошлый год'),
    doc('oswiadczenie-cudzoziemca', 'Oświadczenie cudzoziemca', 'из чек-листа ВНЖ убрано — обычно уже не требуют'),
    doc('osoby-na-utrzymaniu', 'Заявление о людях на содержании', 'шаблон заявления'),
    doc('osoby-na-utrzymaniu-kto', 'Кто пишет заявление о содержании', 'справочная таблица'),
    doc('karta-pobytu-bez-adresu', 'Карта побыта без адреса', 'шаблон заявления'),
    doc('zalacznik-1', 'Załącznik nr 1', 'от ИП, похоже, уже нигде не требуют — пример на всякий случай'),
  ]},
  { t: 'Справки и госуслуги', ic: 'file-text', items: [
    doc('spravka-zus', 'Справка о страховке в ZUS', 'инструкция, как создать в eZUS'),
    doc('spravka-nalogi', 'Справка об отсутствии долгов по налогам', 'пример + как получить'),
    doc('800plus', 'Заявка на 800+ через zus.pl', 'пошаговая инструкция'),
    { t: 'Пояснительная к регистрации VAT', d: 'шаблон: налоговая часто просит её вместе с договором аренды',
      u: 'https://docs.google.com/document/d/1Civ3Rln75Xc_SasJV3C9r0TQ9l6QSlKFPWeYKSIPLoA/edit?usp=sharing' },
    { t: 'Генератор заявок в PDF', d: 'польский проездной документ (PDP), пособие по Карте Поляка',
      u: T + 'pdf/gen/' },
    doc('zamena-inspektora-gdansk', 'Замена инспектора (Гданьск)', 'шаблон просьбы'),
    { t: 'CEIDG viewer', d: 'полная актуальная запись в реестре', u: T + 'ceidg/viewer.html' },
  ]},
  { t: 'Утилиты', ic: 'settings', items: [
    { t: 'Склейка PDF', d: 'PDF и фото в один файл, квитанции банков, паспорт — два разворота на лист',
      u: 'merge.html', hot: true, mine: 'JavaScript + pdf-lib, прямо в браузере — файлы никуда не отправляются' },
    { t: 'Фото для MOS', d: 'подгонка под рамку 684×883 с овалом и линией глаз',
      u: 'photo.html', hot: true, mine: 'JavaScript + Canvas, прямо в браузере — фото никуда не отправляется' },
  ]},
  { t: 'Справочники', ic: 'library', items: [
    { t: 'KSeF: что уже обязательно', d: 'этапы 2026-2027, порог 10 000 zł, чек-лист подключения',
      u: 'ksef.html', hot: true, mine: 'Python на сервере + JavaScript, факты сверены с ksef.podatki.gov.pl' },
    { t: 'Ошибки ZUS по кодам', d: 'что значит код из «uwagi i błędy» и как чинить',
      u: 'zus_err.html?from=tools.html', hot: true, mine: 'база из 32 кодов, поиск на JavaScript, ответы бота на Python' },
    { t: 'Проверка контрагента по NIP', d: 'статус VAT, данные фирмы, счёт в белом списке',
      u: 'nip.html', mine: 'Python + FastAPI: White List, VIES, GUS и CEIDG в одном запросе' },
  ]},
  { t: 'Данные', ic: 'bar-chart', items: [
    { t: 'Składki ZUS по годам', d: '2025 · 2026 · 2027, все режимы и формы налогообложения',
      u: 'calc.html', mine: 'расчётное ядро на Python (сверено с таблицей гайда), интерфейс на JavaScript' },
    doc('vazhnye-daty', 'Важные даты предпринимателя', 'сводная таблица сроков'),
    { t: 'Калькулятор Ryczałt (таблица)', d: 'интерактивная Google-таблица', u: 'https://docs.google.com/spreadsheets/d/14Y0ReOUMWA17pt931vgY2R0pSFYzqYOQ-XBzmkhllOw/edit' },
    { t: 'Калькулятор Ryczałt — разные ставки', d: 'версия таблицы для нескольких ставок ryczałtu',
      u: 'https://docs.google.com/spreadsheets/d/1-JGDDuYLgtFsCoguOJrsGEs7Hs1xeT-i4O-x9fPAVWk/edit?usp=sharing' },
  ]},
];
document.getElementById('cats').innerHTML = CATS.map(c => `
  <div class="card" style="margin-bottom:10px;padding:var(--sp-3) var(--sp-4)">
    <div class="h2"><span class="emi" style="color:var(--accent)">${Icons.svg(c.ic)}</span>${c.t}</div>
    ${c.items.map(i => {
      const ext = i.u.startsWith('http') || i.u.endsWith('.docx');
      return `
      <a class="row-card" style="margin:6px 0 0;background:var(--bg);border:none"
         href="${i.u}"${ext ? ' target="_blank" rel="noopener"' : ''}>
        <span class="body"><span class="t" style="font-size:14px">${i.hot ? `<span class="emi" style="color:var(--warn)">${Icons.svg('star')}</span>` : ''}${i.t}</span>
        <span class="d">${i.d}</span>
        ${i.mine ? `<span class="mine">${Icons.svg('wrench')}<span>${i.mine}</span></span>` : ''}
        </span><span class="chev">${ext ? '↗' : '›'}</span></a>`;
    }).join('')}
  </div>`).join('');
