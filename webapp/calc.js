/* Расчётное ядро — JS-зеркало calc.py. Ставки только из data/rates_2026.json. */
(function () {
  let R = null, Y = null;
  window.ratesReady = Promise.all([
    fetch('data/rates_2026.json').then(r => r.json()),
    fetch('data/rates_years.json').then(r => r.json()),
  ]).then(([j, y]) => { R = j; Y = y; window.RATES = j; window.YEARS = y; return j; });

  const r2 = (x) => Math.round((x + 1e-9) * 100) / 100;
  const yr = (year) => Y.years[String(year || 2026)];

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
      for (const t of z.ryczalt_tiers)
        if (t.max_revenue === null || annualIncome <= t.max_revenue) return t.monthly;
    }
    const rate = form === 'skala' ? z.skala_rate : z.liniowy_rate;
    return r2(Math.max(annualIncome / 12 * rate, z.min_monthly));
  };

  window.pitSkala = function (base) {
    const p = R.pit;
    if (base <= p.free_amount) return 0;
    if (base <= p.skala_threshold) return r2((base - p.free_amount) * p.skala_rate1);
    return r2((p.skala_threshold - p.free_amount) * p.skala_rate1 +
      (base - p.skala_threshold) * p.skala_rate2);
  };

  window.compareForms = function (rev, costs, stage, chorobowe, ryczaltRate) {
    const z = R.zdrowotna;
    const spol = r2(spoleczneMonthly(stage, chorobowe).total * 12);
    const dochod = Math.max(rev - costs - spol, 0);
    const out = {};

    const zdrSk = r2(zdrowotnaMonthly('skala', dochod) * 12);
    const taxSk = pitSkala(dochod);
    out.skala = { tax: taxSk, zdrowotna: zdrSk,
      net: r2(rev - costs - spol - zdrSk - taxSk) };

    const zdrLi = r2(zdrowotnaMonthly('liniowy', dochod) * 12);
    const baseLi = Math.max(dochod - Math.min(zdrLi, z.liniowy_deduct_limit_year), 0);
    const taxLi = r2(baseLi * R.pit.liniowy_rate);
    out.liniowy = { tax: taxLi, zdrowotna: zdrLi,
      net: r2(rev - costs - spol - zdrLi - taxLi) };

    const zdrRy = r2(zdrowotnaMonthly('ryczalt', rev) * 12);
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
