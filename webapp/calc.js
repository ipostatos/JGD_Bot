/* Расчётное ядро — JS-зеркало calc.py. Ставки по годам — data/rates_years.json,
   лимит VAT и прочее — data/rates_2026.json. */
(function () {
  let R = null, Y = null;
  window.ratesReady = Promise.all([
    fetch('data/rates_2026.json').then(r => r.json()),
    fetch('data/rates_years.json').then(r => r.json()),
  ]).then(([j, y]) => { R = j; Y = y; window.RATES = j; window.YEARS = y; return j; });

  const r2 = (x) => Math.round((x + 1e-9) * 100) / 100;
  // год по умолчанию — тот, что действует по календарю (зеркало calc.current_year):
  // раньше здесь стояло 2026, и 1 января дашборд считал бы по прошлогодним ставкам
  window.currentRatesYear = function (today) {
    const y = (today || new Date()).getFullYear();
    const known = Object.keys(Y.years).map(Number).sort((a, b) => a - b);
    const past = known.filter(k => k <= y);
    return past.length ? past[past.length - 1] : known[0];
  };
  const yr = (year) => Y.years[String(year || currentRatesYear())];
  window.ratesYears = () => Object.keys(Y.years).map(Number).sort((a, b) => b - a);
  window.isForecast = (year, field) => (yr(year).forecast || []).includes(field);
  window.missingForCompare = function (year) {
    const y = yr(year), z = y.zdrowotna, out = [];
    if (!y.pit) out.push('pit');
    if (z.ryczalt_tiers === null) out.push('zdrowotna.ryczalt_tiers');
    if (z.liniowy_deduct_limit_year === null) out.push('zdrowotna.liniowy_deduct_limit_year');
    return out;
  };
  window.yearSource = (year, field) => (yr(year).sources || {})[field] || '';

  window.spoleczneMonthly = function (stage, chorobowe, year) {
    const rr = Y.spoleczne_rates;
    const y = yr(year);
    let base = 0, fp = false;
    if (stage === 'pref') base = y.pref_base;
    if (stage === 'duzy') { base = y.duzy_base; fp = true; }
    const p = {
      emerytalna: r2(base * rr.emerytalna),
      rentowa: r2(base * rr.rentowa),
      wypadkowa: r2(base * rr.wypadkowa),
      chorobowa: (chorobowe && stage !== 'ulga') ? r2(base * rr.chorobowa) : 0,
      fp_fs: fp ? r2(base * rr.fp_fs) : 0,
    };
    p.total = r2(p.emerytalna + p.rentowa + p.wypadkowa + p.chorobowa + p.fp_fs);
    return p;
  };

  window.zdrowotnaMonthly = function (form, annualIncome, year) {
    const z = yr(year).zdrowotna;
    if (form === 'ryczalt') {
      if (z.ryczalt_tiers === null) return null;   // ставки года ещё не объявлены
      for (const t of z.ryczalt_tiers)
        if (t.max_revenue === null || annualIncome <= t.max_revenue) return t.monthly;
    }
    const rate = form === 'skala' ? z.skala_rate : z.liniowy_rate;
    return r2(Math.max(annualIncome / 12 * rate, z.min_monthly));
  };

  window.pitSkala = function (base, year) {
    const p = yr(year).pit;
    if (base <= p.free_amount) return 0;
    let tax = 0, lo = p.free_amount;
    for (const b of p.brackets) {
      const hi = b.upto === null ? Infinity : b.upto;
      if (base > lo) tax += (Math.min(base, hi) - lo) * b.rate;
      lo = hi;
    }
    return r2(tax);
  };

  window.compareForms = function (rev, costs, stage, chorobowe, ryczaltRate, year) {
    year = year || currentRatesYear();
    const missing = missingForCompare(year);
    if (missing.length) return { unavailable: missing };
    const z = yr(year).zdrowotna, pit = yr(year).pit;
    const spol = r2(spoleczneMonthly(stage, chorobowe, year).total * 12);
    const dochod = Math.max(rev - costs - spol, 0);
    const out = {};

    const zdrSk = r2(zdrowotnaMonthly('skala', dochod, year) * 12);
    const taxSk = pitSkala(dochod, year);
    out.skala = { tax: taxSk, zdrowotna: zdrSk,
      net: r2(rev - costs - spol - zdrSk - taxSk) };

    const zdrLi = r2(zdrowotnaMonthly('liniowy', dochod, year) * 12);
    const baseLi = Math.max(dochod - Math.min(zdrLi, z.liniowy_deduct_limit_year), 0);
    const taxLi = r2(baseLi * pit.liniowy_rate);
    out.liniowy = { tax: taxLi, zdrowotna: zdrLi,
      net: r2(rev - costs - spol - zdrLi - taxLi) };

    const zdrRy = r2(zdrowotnaMonthly('ryczalt', rev, year) * 12);
    const baseRy = Math.max(rev - spol - zdrRy * z.ryczalt_deduct_share, 0);
    const taxRy = r2(baseRy * ryczaltRate);
    out.ryczalt = { tax: taxRy, zdrowotna: zdrRy,
      net: r2(rev - costs - spol - zdrRy - taxRy) };

    for (const k of ['skala', 'liniowy', 'ryczalt']) out[k].spoleczne = spol;
    out.best = ['skala', 'liniowy', 'ryczalt']
      .reduce((a, b) => out[b].net > out[a].net ? b : a, 'skala');
    return out;
  };

  window.vatLimit = function (ytd, startedISO) {
    let limit = R.vat_exemption_limit;
    if (startedISO) {
      const d = new Date(startedISO + 'T00:00:00');
      const end = new Date(d.getFullYear(), 11, 31);
      const jan1 = new Date(d.getFullYear(), 0, 1);
      const yearDays = Math.round((end - jan1) / 864e5) + 1;
      const leftDays = Math.round((end - d) / 864e5) + 1;
      limit = limit * leftDays / yearDays;
    }
    limit = r2(limit);
    return { limit, used: r2(ytd), left: r2(Math.max(limit - ytd, 0)),
      exceeded: ytd > limit };
  };
})();
