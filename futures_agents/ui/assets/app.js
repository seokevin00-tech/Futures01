/* Futures Desk - the console's behaviour.
 *
 * No framework, no build step, no CDN. The file is read from the project folder
 * at request time, so editing it and reloading the page is the whole edit cycle.
 *
 * Three rules this file follows, all of them about not overstating what is known:
 *
 * 1. The engine's verdict and the discretionary size are rendered as two
 *    separate blocks with two different headings. They are never merged, and a
 *    veto is never styled to look like an approval with a caveat.
 * 2. Blue and orange are applied by exactly one function, `directionBadge`.
 *    Nothing else in this file may set those two hues.
 * 3. Every number shown came from the server's own risk engine. The browser
 *    formats; it does not decide.
 */
'use strict';

// --------------------------------------------------------------------- token

/* The launcher opens `/?token=...`. Keep it in memory and strip it from the
 * visible URL so a screenshot of the console does not leak the capability. */
const TOKEN = (() => {
  const u = new URL(window.location.href);
  const t = u.searchParams.get('token') || '';
  if (t) {
    u.searchParams.delete('token');
    window.history.replaceState({}, '', u.pathname + (u.search || '') + u.hash);
  }
  return t;
})();

const $ = (id) => document.getElementById(id);

// ----------------------------------------------------------------- transport

async function call(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: {
      'X-Desk-Token': TOKEN,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = null;
  try { payload = await res.json(); } catch (_) { /* non-JSON error page */ }
  if (!res.ok) {
    const msg = (payload && payload.error) || `${res.status} ${res.statusText}`;
    const err = new Error(msg);
    err.field = payload && payload.field;
    err.status = res.status;
    throw err;
  }
  return payload;
}

// ----------------------------------------------------------------- formatting

const money = (v, dp = 0) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : (v < 0 ? '-$' : '$') + Math.abs(v).toLocaleString('en-US',
        { minimumFractionDigits: dp, maximumFractionDigits: dp });

const pct = (v, dp = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : (v * 100).toFixed(dp) + '%';

const num = (v, dp = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(dp);

const price = (v, dec) =>
  v === null || v === undefined || v === '' || Number.isNaN(Number(v))
    ? '—' : Number(v).toFixed(dec ?? 2);

/* Text nodes only. Every string rendered here is either a server-computed
 * number or engine prose, and engine prose is model-written, so it goes in as
 * text and never as markup. */
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = String(text);
  return n;
}

function table(target, headers, rows, caption) {
  const t = $(target);
  t.textContent = '';
  if (caption) {
    const c = el('caption', null, caption);
    t.appendChild(c);
  }
  const thead = el('thead');
  const hr = el('tr');
  headers.forEach((h) => hr.appendChild(el('th', null, h)));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tb = el('tbody');
  rows.forEach((row) => {
    const tr = el('tr', row.cls || null);
    (row.cells || row).forEach((cell) => {
      const td = el('td');
      if (cell && typeof cell === 'object' && cell.node) td.appendChild(cell.node);
      else td.textContent = cell === null || cell === undefined ? '—' : String(cell);
      tr.appendChild(td);
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
}

let toastTimer = null;
function toast(message, bad) {
  const box = $('toast');
  box.textContent = message;
  box.className = 'toast' + (bad ? ' bad' : '');
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, bad ? 9000 : 4000);
}

// ---------------------------------------------------------------------- state

const S = {
  boot: null,          // last bootstrap payload
  cfg: null,           // account config being edited (may differ from saved)
  savedCfg: null,      // the config as last written to disk
  preview: null,
  dirty: false,
};

// ------------------------------------------------------------------- tabs

function showTab(name) {
  document.querySelectorAll('[data-panel]').forEach((s) => {
    s.hidden = s.id !== 'tab-' + name;
  });
  document.querySelectorAll('nav.tabs button').forEach((b) => {
    b.setAttribute('aria-selected', String(b.dataset.tab === name));
  });
  if (name === 'evidence') renderCostTable();
}

document.querySelectorAll('nav.tabs button').forEach((b) => {
  b.addEventListener('click', () => showTab(b.dataset.tab));
});

// ------------------------------------------------------------------- header

function renderHeader(acct, agents) {
  $('kEquity').textContent = money(acct.equity, 2);
  $('kPeak').textContent = money(acct.peak_equity, 0);
  $('kFail').textContent = money(acct.failure_equity, 0);
  $('kBuffer').textContent = money(acct.usable_buffer, 0);
  $('kMult').textContent = num(acct.derisk_multiplier, 2) + '×';
  $('kDay').textContent = money(acct.day ? acct.day.realised_pnl : 0, 2);
  $('kBudget').textContent = money(acct.next_trade_budget, 0);

  const consumed = Math.max(0, Math.min(1, acct.buffer_consumed_pct || 0));
  const meter = $('kBufferMeter');
  meter.firstElementChild.style.width = (consumed * 100).toFixed(1) + '%';
  meter.className = 'meter' + (consumed >= 0.7 ? ' crit' : consumed >= 0.25 ? ' hot' : '');
  meter.setAttribute('aria-label',
    `Usable buffer ${pct(1 - consumed, 0)} remaining (${money(acct.usable_buffer, 0)})`);

  /* Mode carries a word and an icon as well as a colour - a colleague with a
   * colour-vision deficiency must read the same thing at the same glance. */
  const mp = $('modePill');
  const halted = !acct.mode_can_trade;
  const reduced = acct.mode === 'REDUCED';
  mp.className = 'pill ' + (halted ? 'halt' : reduced ? 'warn' : 'ok');
  mp.textContent = (halted ? '✖ ' : reduced ? '⚠ ' : '✓ ') + acct.mode;
  mp.title = (acct.mode_reasons || []).join('; ') || 'no constraints active';

  if (agents) {
    const lp = $('llmPill');
    const ok = agents.api_key_present && agents.llm_enabled_in_config;
    lp.className = 'pill' + (ok ? ' ok' : '');
    lp.textContent = ok ? '✓ model ready' : 'deterministic only';
  }
}

// ------------------------------------------------------ direction badge (only)

/* The ONLY place blue and orange are applied. See the note at the top of
 * app.css: reusing those hues anywhere else would undo the whole reason the
 * terminal renderer moved SIGNAL off blue and NO TRADE off orange. */
function directionBadge(decision) {
  const map = {
    LONG:  ['long',    '▲', 'BUY / LONG'],
    SHORT: ['short',   '▼', 'SELL / SHORT'],
    NONE:  ['notrade', '▬', 'NO TRADE'],
  };
  const [cls, glyph, label] = map[decision] || map.NONE;
  const box = el('div', 'badge-dir ' + cls);
  box.appendChild(el('span', 'glyph', glyph));
  box.appendChild(el('span', null, label));
  return box;
}

// -------------------------------------------------------------- ticket inputs

function contract() {
  return S.boot.contracts[$('fSymbol').value] || null;
}

function readTicket() {
  const hist = {
    trades: $('hTrades').value,
    win_rate: $('hWin').value,
    expectancy_r: $('hExp').value,
    out_of_sample_trades: $('hOosTrades').value,
    out_of_sample_expectancy_r: $('hOosExp').value,
    robustness_score: $('hRobust').value,
    sample_is_sufficient: $('hSufficient').checked,
  };
  const anyHist = ['trades', 'win_rate', 'expectancy_r', 'out_of_sample_trades',
                   'out_of_sample_expectancy_r', 'robustness_score']
    .some((k) => String(hist[k]).trim() !== '');

  return {
    symbol: $('fSymbol').value,
    direction: $('fDirection').value,
    entry: $('fEntry').value,
    stop: $('fStop').value,
    targets: [$('fT1').value, $('fT2').value].filter((v) => String(v).trim() !== ''),
    confidence: $('fConfidence').value,
    timeframe: $('fTimeframe').value,
    volatility: $('fVolatility').value,
    news_risk: $('fNewsRisk').value,
    atr: $('fAtr').value,
    atr_median: $('fAtrMedian').value,
    historical: anyHist ? hist : null,
  };
}

function renderSymbolMeta() {
  const spec = contract();
  if (!spec) return;
  const fw = S.boot.guidance.frameworks[spec.symbol];
  const box = $('symbolFramework');
  box.textContent = '';

  if (fw && fw.status === 'measured') {
    const tf = fw.timeframes.map((t) => S.boot.timeframe_labels[String(t)] || t + 'm');
    box.appendChild(el('span', 'pill ok', '✓ measured'));
    box.appendChild(document.createTextNode(
      ` ${fw.signals.join(', ')} at ${tf.join(' and ')}. ${fw.note}`));
  } else {
    box.appendChild(el('span', 'pill warn', '⚠ no framework'));
    box.appendChild(document.createTextNode(
      ' ' + (fw ? fw.note : 'Not part of the measurement programme.')));
  }

  $('atrHint').textContent = 'typical ' + num(spec.typical_atr_points, 2);
  if (!$('fAtr').value) $('fAtr').placeholder = num(spec.typical_atr_points, 2);

  /* Timeframe list, with the measured ones marked. The graveyard warning below
   * the box is the measurement, not a preference. */
  const tfSel = $('fTimeframe');
  const keep = tfSel.value;
  tfSel.textContent = '';
  [5, 15, 30, 60, 120, 240, 1440].forEach((m) => {
    const o = el('option', null,
      (S.boot.timeframe_labels[String(m)] || m + 'm') +
      (fw && fw.timeframes.includes(m) ? '  ✓ measured' : ''));
    o.value = String(m);
    tfSel.appendChild(o);
  });
  tfSel.value = keep && [...tfSel.options].some((o) => o.value === keep) ? keep : '60';
  onTimeframeChange();
}

function onTimeframeChange() {
  const tf = Number($('fTimeframe').value);
  const spec = contract();
  const fw = spec ? S.boot.guidance.frameworks[spec.symbol] : null;
  const w = $('tfWarn');
  w.textContent = '';
  if (tf < 60) {
    w.textContent = 'Sub-hourly is a graveyard: at 5 minutes only 11-16% of ' +
      'strategies make money.';
  } else if (fw && fw.status === 'measured' && !fw.timeframes.includes(tf)) {
    w.textContent = `${spec.symbol} was not measured favourably at this ` +
      `timeframe. Measured: ${fw.timeframes.map((t) => t + 'm').join(', ')}.`;
  }
}

function renderStopMeta() {
  const spec = contract();
  if (!spec) return;
  const entry = Number($('fEntry').value);
  const stop = Number($('fStop').value);
  const t1 = Number($('fT1').value);
  if (entry && stop && spec.tick_size > 0) {
    const pts = Math.abs(entry - stop);
    const ticks = pts / spec.tick_size;
    $('stopMeta').textContent =
      `${num(pts, spec.price_decimals)}pts \u00b7 ${ticks.toFixed(0)}t \u00b7 ` +
      `${money(pts * spec.point_value, 0)}/ct`;
    $('t1Meta').textContent = (t1 && pts > 0)
      ? `${num(Math.abs(t1 - entry) / pts, 2)}R` : '';
  } else {
    $('stopMeta').textContent = '';
    $('t1Meta').textContent = '';
  }
}

// ---------------------------------------------------------------- the preview

let previewTimer = null;
function schedulePreview() {
  renderStopMeta();
  clearTimeout(previewTimer);
  previewTimer = setTimeout(runPreview, 220);
}

async function runPreview() {
  const ticket = readTicket();
  if (!ticket.entry || !ticket.stop) {
    $('engineVerdict').textContent = 'Enter an entry and a stop.';
    $('engineVerdict').className = 'verdict neutral';
    return;
  }
  try {
    const p = await call('POST', '/api/preview', ticket);
    S.preview = p;
    renderPreview(p);
    renderHeader(p.account, S.boot.agents);
  } catch (e) {
    toast('Preview failed: ' + e.message, true);
  }
}

function renderPreview(p) {
  const a = p.assessment;
  const d = p.discretionary;
  const spec = p.contract;
  const dec = a.approved ? p.proposal.direction : 'NONE';

  // ---- callout badge -------------------------------------------------
  const badgeBox = $('calloutBadge');
  badgeBox.textContent = '';
  badgeBox.appendChild(directionBadge(dec));
  if (!a.approved) {
    const n = el('p', 'help',
      'Grey because the engine did not approve this. The direction you typed is ' +
      'in the ticket; it is not a callout until risk clears it.');
    badgeBox.appendChild(n);
  }

  const nums = $('calloutNums');
  nums.textContent = '';
  const shown = a.approved ? a.contracts : d.contracts;
  const shownRisk = a.approved ? a.dollar_risk : d.dollar_risk;
  [
    ['Entry', price(p.proposal.entry, spec.price_decimals)],
    ['Stop', price(p.proposal.stop, spec.price_decimals)],
    ['Target', p.proposal.targets.length
      ? p.proposal.targets.map((t) => price(t, spec.price_decimals)).join(' / ') : '—'],
    ['Stop distance', `${num(p.proposal.risk_points, spec.price_decimals)} pts / ${num(p.proposal.risk_ticks, 0)}t`],
    ['R:R (final)', num(p.proposal.reward_risk, 2)],
    ['Contracts', shown],
    ['At risk', money(shownRisk, 2)],
    ['% of equity', pct(shownRisk / (p.account.equity || 1), 2)],
  ].forEach(([k, v]) => {
    const box = el('div', 'num');
    box.appendChild(el('div', 'k', k));
    box.appendChild(el('div', 'v', v));
    nums.appendChild(box);
  });

  // ---- engine verdict ------------------------------------------------
  const ev = $('engineVerdict');
  ev.textContent = '';
  ev.className = 'verdict ' + (a.approved ? 'approved' : 'vetoed');
  const h = el('h3');
  h.appendChild(el('span', 'pill ' + (a.approved ? 'ok' : 'halt'),
    a.approved ? '✓ APPROVED' : '✖ VETOED'));
  h.appendChild(el('span', null, a.approved
    ? `${a.contracts} contract(s), ${money(a.dollar_risk, 2)} at risk`
    : `${a.vetoes.length} blocking condition${a.vetoes.length === 1 ? '' : 's'}`));
  ev.appendChild(h);

  if (a.vetoes.length) {
    const ul = el('ul', 'reasons veto');
    a.vetoes.forEach((v) => ul.appendChild(el('li', null, v)));
    ev.appendChild(ul);
  }
  if (a.approved) {
    const g = el('div', 'numrow');
    [
      ['R:R after costs', num(a.reward_risk_after_costs, 2)],
      ['Costs', money(a.expected_cost, 2)],
      ['Buffer if stopped', pct(a.buffer_consumed_if_stopped_pct, 1)],
      ['Multiplier applied', num(a.risk_multiplier_applied, 2) + '×'],
    ].forEach(([k, v]) => {
      const b = el('div', 'num');
      b.appendChild(el('div', 'k', k));
      b.appendChild(el('div', 'v', v));
      g.appendChild(b);
    });
    ev.appendChild(g);
  }
  if (a.warnings.length) {
    const ul = el('ul', 'reasons');
    a.warnings.forEach((v) => ul.appendChild(el('li', null, '⚠ ' + v)));
    ev.appendChild(ul);
  }
  if (a.notes.length) {
    const det = el('details', 'adv');
    det.appendChild(el('summary', null, `Engine notes (${a.notes.length})`));
    const ul = el('ul', 'reasons');
    a.notes.forEach((v) => ul.appendChild(el('li', null, v)));
    det.appendChild(ul);
    ev.appendChild(det);
  }

  // ---- discretionary -------------------------------------------------
  $('discSub').textContent = d.label;
  const db = $('discBlock');
  db.textContent = '';
  db.className = 'verdict neutral';
  const dg = el('div', 'numrow');
  [
    ['Budget', money(d.budget, 2)],
    ['Per contract', money(d.per_contract_dollars, 2)],
    ['% equity / contract', pct(d.per_contract_pct_of_equity, 2)],
    ['Contracts', d.contracts],
    ['Total at risk', money(d.dollar_risk, 2)],
    ['% of equity', pct(d.account_risk_pct, 2)],
    ['Buffer if stopped', d.buffer_consumed_if_stopped_pct === null
      ? '—' : pct(d.buffer_consumed_if_stopped_pct, 1)],
    ['T1 net R', num(d.first_target_net_r, 2)],
  ].forEach(([k, v]) => {
    const b = el('div', 'num');
    b.appendChild(el('div', 'k', k));
    b.appendChild(el('div', 'v', v));
    dg.appendChild(b);
  });
  db.appendChild(dg);
  if (d.notes.length) {
    const ul = el('ul', 'reasons');
    d.notes.forEach((v) => ul.appendChild(el('li', null, v)));
    db.appendChild(ul);
  }

  // ---- stop ladder ---------------------------------------------------
  const maxCost = Math.max(...p.stop_ladder.map((r) => r.per_contract_dollars), 1);
  const here = p.proposal.risk_points;
  // Nearest ladder row to the stop actually typed, so the user can see where
  // on the cost curve they have placed themselves. Computed once, not per row.
  const nearest = p.stop_ladder.length
    ? p.stop_ladder.reduce((best, x) =>
        Math.abs(x.points - here) < Math.abs(best.points - here) ? x : best,
        p.stop_ladder[0])
    : null;
  const rows = p.stop_ladder.map((r) => {
    const flags = [];
    if (r.below_atr_floor) flags.push('under 0.5×ATR floor');
    if (r.below_tick_floor) flags.push(`inside ${spec.symbol} noise floor`);
    const bar = el('div', 'barcell');
    bar.appendChild(el('span', 'bar')).style.width =
      Math.max(2, (r.per_contract_dollars / maxCost) * 70) + 'px';
    bar.appendChild(el('span', null, money(r.per_contract_dollars, 0)));
    return {
      cls: r === nearest ? 'chosen' : (flags.length ? 'muted' : null),
      cells: [
        num(r.atr_multiple, 2) + '×ATR',
        num(r.points, spec.price_decimals),
        num(r.ticks, 0),
        { node: bar },
        pct(r.per_contract_pct_of_equity, 2),
        r.contracts_at_budget,
        money(r.total_dollars_at_budget, 0),
        flags.join('; ') || '—',
      ],
    };
  });
  const ladderCaption =
    `At the current budget of ${money(p.account.next_trade_budget, 0)}. ` +
    `One ${spec.symbol} contract at a median structural stop ` +
    `(${num(spec.median_stop_points, spec.price_decimals)} pts) risks ` +
    `${money(spec.median_stop_dollars, 0)} — ` +
    `${pct(spec.median_stop_dollars / (p.account.equity || 1), 2)} of this account.`;
  table('ladderTable',
    ['Stop', 'Points', 'Ticks', 'Per contract', '% equity', 'Contracts', 'Total risk', 'Flags'],
    rows, ladderCaption);

  // ---- loss walk -----------------------------------------------------
  table('walkTable',
    ['Loss #', 'Equity', 'Day P&L', 'Usable buffer', 'Next multiplier', 'Mode'],
    p.loss_walk.map((w) => ({
      cls: w.terminal ? 'chosen' : null,
      cells: [w.loss_number, money(w.equity, 0), money(w.day_pnl, 0),
              money(w.usable_buffer, 0), num(w.derisk_multiplier, 2) + '×', w.mode],
    })),
    p.loss_walk.length
      ? 'Projection, not prediction: each row assumes the full stop is hit.'
      : 'No size to project — the ticket is not sizeable yet.');

  // ---- guards --------------------------------------------------------
  const g = p.guards;
  const gl = $('guardList');
  gl.textContent = '';
  const guards = [
    [!g.below_atr_half_floor, 'Stop is at or beyond the 0.5×ATR floor',
      g.atr_half_floor_points === null
        ? 'No ATR supplied, so the floor could not be checked.'
        : `Floor is ${num(g.atr_half_floor_points, spec.price_decimals)} pts; ` +
          `this stop is ${num(p.proposal.risk_points, spec.price_decimals)} pts.`],
    [!g.below_tick_floor, `Stop clears ${spec.symbol}'s ${g.tick_floor}-tick noise floor`,
      `This stop is ${num(p.proposal.risk_ticks, 0)} ticks.`],
    [!g.in_1500_1600_et, 'Outside the 15:00-16:00 ET window', g.dead_hour_note +
      ` It is currently ${g.now_et}.`],
    [p.framework.status === 'measured',
      p.framework.status === 'measured'
        ? `${spec.symbol} has a measured framework`
        : `${spec.symbol} has no measured framework`,
      p.framework.note],
  ];
  // Shown only when it applies: a second position in the index complex that the
  // engine's correlation groups do not catch.
  if (g.index_complex) {
    guards.push([false, 'Second position in the index complex', g.index_complex.note]);
  }
  guards.forEach(([ok, title, detail]) => {
    const box = el('div', 'verdict ' + (ok ? 'approved' : 'warn'));
    box.style.marginBottom = '.5rem';
    const hh = el('h3');
    hh.appendChild(el('span', 'pill ' + (ok ? 'ok' : 'warn'), ok ? '✓ pass' : '⚠ check'));
    hh.appendChild(el('span', null, title));
    box.appendChild(hh);
    box.appendChild(el('div', 'help', detail));
    gl.appendChild(box);
  });
}

// ------------------------------------------------------------- risk studio

function fieldValue(meta, value) {
  if (meta.kind === 'bool') return value ? 'on' : 'off';
  if (meta.kind === 'pct') return pct(value, value < 0.01 ? 3 : 2);
  if (meta.kind === 'money') return money(value, 0);
  if (meta.kind === 'int') return String(value);
  if (meta.kind === 'r') return num(value, 3) + 'R';
  return num(value, 2);
}

function renderStudio() {
  const host = $('studioGroups');
  host.textContent = '';
  const groups = new Map();
  S.boot.account_field_meta.forEach((m) => {
    if (!groups.has(m.group)) groups.set(m.group, []);
    groups.get(m.group).push(m);
  });

  /* Two columns of panels rather than one long scroll: the account-level knobs
   * and the per-trade knobs are read together, so they should be visible
   * together. */
  const colA = el('div');
  const colB = el('div');
  let i = 0;
  groups.forEach((metas, group) => {
    const panel = el('div', 'panel');
    panel.appendChild(el('h2', null, group));
    const grid = el('div', 'grid-fields');
    metas.forEach((m) => grid.appendChild(studioField(m)));
    panel.appendChild(grid);
    (i++ % 2 === 0 ? colA : colB).appendChild(panel);
  });
  host.appendChild(colA);
  host.appendChild(colB);
  renderLadderEditor();
  renderDerived();
}

function studioField(meta) {
  const wrap = el('label', 'f');
  const lab = el('span', 'lab');
  lab.appendChild(el('span', null, meta.label));
  const cur = el('span', 'cur', fieldValue(meta, S.cfg[meta.key]));
  lab.appendChild(cur);
  wrap.appendChild(lab);

  if (meta.kind === 'bool') {
    const row = el('div', 'chk');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = Boolean(S.cfg[meta.key]);
    box.addEventListener('change', () => {
      S.cfg[meta.key] = box.checked;
      cur.textContent = fieldValue(meta, box.checked);
      markDirty();
    });
    row.appendChild(box);
    row.appendChild(el('span', null, meta.help.split('.')[0] + '.'));
    wrap.appendChild(row);
    return wrap;
  }

  /* Slider plus number box, bound both ways. The slider is for exploring the
   * shape of a limit; the number box is for setting it exactly. Offering only
   * one of the two makes a risk control either imprecise or unexplorable. */
  const pair = el('div', 'rangepair');
  const range = document.createElement('input');
  range.type = 'range';
  range.min = meta.min;
  range.max = meta.max;
  range.step = meta.step;
  range.value = S.cfg[meta.key];
  const box = document.createElement('input');
  box.type = 'number';
  box.min = meta.min;
  box.max = meta.max;
  box.step = meta.step;
  box.value = S.cfg[meta.key];

  const apply = (raw, from) => {
    const v = Number(raw);
    if (Number.isNaN(v)) return;
    S.cfg[meta.key] = meta.kind === 'int' ? Math.round(v) : v;
    if (from !== 'range') range.value = S.cfg[meta.key];
    if (from !== 'box') box.value = S.cfg[meta.key];
    cur.textContent = fieldValue(meta, S.cfg[meta.key]);
    markDirty();
  };
  range.addEventListener('input', () => apply(range.value, 'range'));
  box.addEventListener('input', () => apply(box.value, 'box'));

  pair.appendChild(range);
  pair.appendChild(box);
  wrap.appendChild(pair);
  wrap.appendChild(el('div', 'help', meta.help));
  return wrap;
}

/* The derived numbers, recomputed in the browser from the edited config so the
 * consequence of a slider is visible before it is saved. The formulas mirror
 * AccountConfig.usable_buffer and derisk_multiplier exactly; the server remains
 * the authority once Save is pressed. */
function renderDerived() {
  const c = S.cfg;
  const acct = S.boot.account;
  const equity = acct.equity;
  const peak = Math.max(acct.peak_equity, equity);
  const failure = (c.trailing_drawdown ? peak : c.starting_equity) - c.max_total_drawdown;
  const reserve = c.max_total_drawdown * c.protected_buffer_pct;
  const usable = Math.max(0, Math.max(0, equity - failure) - reserve);
  const full = Math.max(1e-9, c.max_total_drawdown * (1 - c.protected_buffer_pct));
  const consumed = Math.min(1, Math.max(0, 1 - usable / full));
  let mult = c.derisk_ladder.length ? c.derisk_ladder[0][1] : 1;
  c.derisk_ladder.forEach(([t, m]) => { if (consumed >= t) mult = m; });
  const base = usable * c.base_risk_pct_of_buffer;
  const cap = equity * c.max_risk_pct_of_equity;
  const budget = Math.min(base, cap, c.max_dollar_risk) * mult;
  const binding = budget <= 0 ? 'none'
    : (cap < base && cap < c.max_dollar_risk) ? 'equity ceiling'
    : (c.max_dollar_risk < base) ? 'dollar ceiling' : 'buffer fraction';

  const host = $('derived');
  host.textContent = '';
  [
    ['Failure line', money(failure, 0)],
    ['Reserved', money(reserve, 0)],
    ['Usable buffer', money(usable, 0)],
    ['Buffer consumed', pct(consumed, 1)],
    ['Multiplier', num(mult, 2) + '×'],
    ['Budget / trade', money(budget, 0)],
    ['Binding limit', binding],
    ['Budget % of equity', pct(budget / (equity || 1), 3)],
  ].forEach(([k, v]) => {
    const b = el('div', 'num');
    b.appendChild(el('div', 'k', k));
    b.appendChild(el('div', 'v', v));
    host.appendChild(b);
  });
}

function renderLadderEditor() {
  const rows = S.cfg.derisk_ladder.map((row, idx) => {
    const consumed = document.createElement('input');
    consumed.type = 'number';
    consumed.step = '0.01';
    consumed.min = '0';
    consumed.max = '1';
    consumed.value = row[0];
    consumed.addEventListener('input', () => {
      S.cfg.derisk_ladder[idx][0] = Number(consumed.value);
      renderDerived();
      markDirty();
    });

    const mult = document.createElement('input');
    mult.type = 'number';
    mult.step = '0.05';
    mult.min = '0';
    mult.max = '5';
    mult.value = row[1];
    mult.addEventListener('input', () => {
      S.cfg.derisk_ladder[idx][1] = Number(mult.value);
      renderDerived();
      markDirty();
    });

    const del = el('button', 'btn sm danger', 'Remove');
    del.addEventListener('click', () => {
      if (S.cfg.derisk_ladder.length <= 1) {
        toast('The ladder needs at least one row.', true);
        return;
      }
      S.cfg.derisk_ladder.splice(idx, 1);
      renderLadderEditor();
      renderDerived();
      markDirty();
    });

    return { cells: [
      `row ${idx + 1}`,
      { node: consumed },
      { node: mult },
      pct(row[1], 0) + ' of base risk',
      { node: del },
    ] };
  });
  table('ladderEditor',
    ['#', 'Buffer consumed ≥', 'Multiplier', 'Meaning', ''],
    rows,
    'Thresholds must strictly increase; multipliers must not increase.');
}

function markDirty() {
  S.dirty = true;
  $('dirtyPill').hidden = false;
  renderDerived();
}

// ---------------------------------------------------------------- account tab

function renderAccountTab() {
  const a = S.boot.account;
  $('aEquity').value = a.equity;
  $('aPeak').value = a.peak_equity;
  $('aDayPnl').value = a.day ? a.day.realised_pnl : 0;
  $('aDayTrades').value = a.day ? a.day.trades_taken : 0;
  $('aDayLosses').value = a.day ? a.day.consecutive_losses : 0;

  const host = $('acctNums');
  host.textContent = '';
  [
    ['Drawdown', money(a.drawdown, 0) + ' (' + pct(a.drawdown_pct, 2) + ')'],
    ['To failure', money(a.remaining_drawdown, 0)],
    ['Usable buffer', money(a.usable_buffer, 0)],
    ['Day loss budget left', money(a.remaining_daily_loss_budget, 0)],
    ['Trades today', a.day ? a.day.trades_taken : 0],
    ['Consecutive losses', a.day ? a.day.consecutive_losses : 0],
    ['Closed trades', a.closed_trades],
    ['Open risk', money(a.open_risk, 0)],
  ].forEach(([k, v]) => {
    const b = el('div', 'num');
    b.appendChild(el('div', 'k', k));
    b.appendChild(el('div', 'v', v));
    host.appendChild(b);
  });

  renderSpark(a.equity_curve || []);
  renderPositions(a.open_positions || []);
}

/* One series, so no legend and no categorical colour - the heading names it.
 * The failure line is drawn as a reference rule because equity is only
 * meaningful relative to it. */
function renderSpark(curve) {
  const host = $('equitySpark');
  host.textContent = '';
  if (curve.length < 2) {
    $('sparkNote').textContent =
      'The curve appears once the account has more than one recorded point.';
    return;
  }
  const W = 640, H = 120, PAD = 4;
  const vals = curve.map((c) => c[1]);
  const fail = S.boot.account.failure_equity;
  const lo = Math.min(...vals, fail);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const x = (i) => PAD + (i / (curve.length - 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - ((v - lo) / span) * (H - 2 * PAD);

  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', String(H));
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label',
    `Equity from ${money(vals[0], 0)} to ${money(vals[vals.length - 1], 0)}, ` +
    `failure line at ${money(fail, 0)}`);

  const rule = document.createElementNS(ns, 'line');
  rule.setAttribute('x1', PAD); rule.setAttribute('x2', W - PAD);
  rule.setAttribute('y1', y(fail)); rule.setAttribute('y2', y(fail));
  rule.setAttribute('stroke', 'var(--halt)');
  rule.setAttribute('stroke-width', '1');
  rule.setAttribute('stroke-dasharray', '4 3');
  svg.appendChild(rule);

  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', curve.map((c, i) =>
    `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(c[1]).toFixed(1)}`).join(' '));
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', 'var(--seq-4)');
  path.setAttribute('stroke-width', '2');
  path.setAttribute('stroke-linejoin', 'round');
  path.setAttribute('stroke-linecap', 'round');
  svg.appendChild(path);

  host.appendChild(svg);
  $('sparkNote').textContent =
    `${curve.length} points. Dashed rule is the failure line at ${money(fail, 0)}.`;
}

function renderPositions(positions) {
  if (!positions.length) {
    table('posTable', ['Symbol', 'Side', 'Contracts', 'Entry', 'Stop', 'At risk', ''],
      [], 'No open positions recorded.');
    return;
  }
  const rows = positions.map((p) => {
    const spec = S.boot.contracts[p.symbol] || { price_decimals: 2 };
    const close = el('button', 'btn sm', 'Close…');
    close.addEventListener('click', () => closePositionFlow(p));
    return { cells: [
      p.symbol, p.direction, p.contracts,
      price(p.entry, spec.price_decimals), price(p.stop, spec.price_decimals),
      money(p.dollar_risk, 2), { node: close },
    ] };
  });
  table('posTable', ['Symbol', 'Side', 'Contracts', 'Entry', 'Stop', 'At risk', ''], rows);
}

async function closePositionFlow(pos) {
  const raw = window.prompt(
    `Realised P&L for ${pos.symbol} (${pos.contracts} contract(s)).\n` +
    `Negative for a loss, e.g. -${Math.round(pos.dollar_risk)}`, '0');
  if (raw === null) return;
  const pnl = Number(raw);
  if (Number.isNaN(pnl)) { toast('That is not a number.', true); return; }
  try {
    const r = await call('POST', '/api/position/close', { symbol: pos.symbol, pnl });
    S.boot.account = r.account;
    renderHeader(r.account, S.boot.agents);
    renderAccountTab();
    toast(`Closed ${pos.symbol} at ${money(pnl, 2)}.`);
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------- agents tab

function renderAgents() {
  const ag = S.boot.agents;
  const ol = $('cycleList');
  ol.textContent = '';
  ag.cycle.forEach((c) => ol.appendChild(el('li', null, c)));

  table('agentTable', ['Stage', 'Agent', 'Role', 'Reads', 'Note'],
    ag.roster.map((a) => ({ cells: [a.stage, a.key, a.name, a.reads, a.note] })));

  const box = $('llmBlock');
  box.textContent = '';
  const ready = ag.api_key_present && ag.llm_enabled_in_config;
  box.className = 'verdict ' + (ready ? 'approved' : 'neutral');
  const h = el('h3');
  h.appendChild(el('span', 'pill ' + (ready ? 'ok' : 'warn'),
    ready ? '✓ available' : '⚠ deterministic only'));
  h.appendChild(el('span', null, `${ag.model} at ${ag.effort} effort`));
  box.appendChild(h);
  if (ag.note) box.appendChild(el('div', 'help', ag.note));
  if (ag.staffing_error) {
    box.appendChild(el('div', 'help', 'Staffing probe: ' + ag.staffing_error));
  }
  const ul = el('ul', 'reasons');
  ul.appendChild(el('li', null,
    `API key present: ${ag.api_key_present ? 'yes' : 'no'}`));
  ul.appendChild(el('li', null,
    `LLM enabled in config: ${ag.llm_enabled_in_config ? 'yes' : 'no'}`));
  if ((ag.roles || []).length) {
    ul.appendChild(el('li', null, `Roles defined: ${ag.roles.join(', ')}`));
  }
  box.appendChild(ul);
}

// ----------------------------------------------------------- evidence tab

function renderEvidence() {
  const fw = S.boot.guidance.frameworks;
  table('fwTable', ['Symbol', 'Verdict', 'Signals', 'Timeframes', 'Independent', 'Note'],
    Object.keys(fw).map((k) => {
      const f = fw[k];
      const pill = el('span', 'pill ' + (f.status === 'measured' ? 'ok' : 'warn'),
        f.status === 'measured' ? '✓ measured' : '⚠ none');
      return { cells: [
        f.symbol, { node: pill },
        f.signals.length ? f.signals.join(', ') : '—',
        f.timeframes.length ? f.timeframes.map((t) => t + 'm').join(', ') : '—',
        f.independent ? 'yes' : 'no — index complex',
        f.note,
      ] };
    }));

  table('rulesTable', ['Rule', 'Evidence'],
    S.boot.guidance.rules.map((r) => ({ cells: [r.rule, r.basis] })));
}

function renderCostTable() {
  const equity = S.boot.account.equity || 1;
  const rows = Object.keys(S.boot.contracts)
    .map((k) => S.boot.contracts[k])
    .filter((c) => c.median_stop_dollars > 0)
    .sort((a, b) => b.median_stop_dollars - a.median_stop_dollars)
    .map((c) => {
      const share = c.median_stop_dollars / equity;
      const pill = el('span', 'pill ' + (share > 0.02 ? 'halt' : share > 0.01 ? 'warn' : 'ok'),
        share > 0.02 ? '✖ heavy' : share > 0.01 ? '⚠ sizeable' : '✓ workable');
      return { cls: share > 0.02 ? 'chosen' : null, cells: [
        c.symbol, c.name, c.is_micro ? 'micro' : 'full',
        num(c.median_stop_points, c.price_decimals),
        money(c.median_stop_dollars, 0), pct(share, 2), { node: pill },
      ] };
    });
  table('costTable',
    ['Symbol', 'Name', 'Size', 'Stop (pts)', 'Per contract', '% of equity', ''],
    rows,
    'One contract, a stop of roughly one ATR. "Heavy" is more than 2% of equity ' +
    'risked by a single contract, which leaves position size a switch rather than a dial.');
}

// ------------------------------------------------------------------- footer

function renderFooter() {
  const p = S.boot.paths;
  const f = $('footer');
  f.textContent = '';
  const add = (label, value) => {
    const d = el('div');
    d.appendChild(el('b', null, label + ': '));
    d.appendChild(document.createTextNode(value));
    f.appendChild(d);
  };
  add('project folder', p.root);
  add('risk config', p.config_file + (p.config_exists ? '' : '  (not yet written — defaults in use)'));
  add('account state', p.state_file + (p.state_exists ? '' : '  (not yet written)'));
  add('UI assets', (p.assets_dir || 'none') + '  [' + p.assets_source + ']');
  add('mode', p.frozen ? 'packaged executable' : 'running from source');
  $('studioFile').textContent = p.config_file;
  $('acctFile').textContent = p.state_file;
}

// --------------------------------------------------------------------- wiring

function fillSymbolSelects() {
  const syms = Object.keys(S.boot.contracts).sort();
  const preferred = S.boot.system.symbols;
  [['fSymbol', true], ['pSymbol', false]].forEach(([id, markFw]) => {
    const sel = $(id);
    sel.textContent = '';
    const order = [...preferred, ...syms.filter((s) => !preferred.includes(s))];
    order.forEach((s) => {
      const c = S.boot.contracts[s];
      if (!c) return;
      const f = S.boot.guidance.frameworks[s];
      const tag = markFw && f && f.status === 'measured' ? '  ✓' : '';
      const o = el('option', null, `${s} — ${c.name}${tag}`);
      o.value = s;
      sel.appendChild(o);
    });
  });
  $('fSymbol').value = preferred.includes('MCL') ? 'MCL' : (preferred[0] || syms[0]);
}

function wireTicket() {
  ['fEntry', 'fStop', 'fT1', 'fT2', 'fAtr', 'fAtrMedian', 'fDirection',
   'fVolatility', 'fNewsRisk', 'hTrades', 'hWin', 'hExp', 'hOosTrades',
   'hOosExp', 'hRobust'].forEach((id) => {
    $(id).addEventListener('input', schedulePreview);
  });
  $('hSufficient').addEventListener('change', schedulePreview);
  $('fSymbol').addEventListener('change', () => {
    renderSymbolMeta();
    schedulePreview();
  });
  $('fTimeframe').addEventListener('change', () => {
    onTimeframeChange();
    schedulePreview();
  });

  const cSlider = $('fConfidence');
  const cBox = $('fConfidenceN');
  const syncConf = (from) => {
    const v = Number(from === 'box' ? cBox.value : cSlider.value);
    if (Number.isNaN(v)) return;
    cSlider.value = v;
    cBox.value = v;
    $('confCur').textContent = v.toFixed(2);
    schedulePreview();
  };
  cSlider.addEventListener('input', () => syncConf('slider'));
  cBox.addEventListener('input', () => syncConf('box'));

  $('btnRecalc').addEventListener('click', runPreview);
  $('btnClear').addEventListener('click', () => {
    ['fEntry', 'fStop', 'fT1', 'fT2', 'fAtr', 'fAtrMedian', 'hTrades', 'hWin',
     'hExp', 'hOosTrades', 'hOosExp', 'hRobust'].forEach((id) => { $(id).value = ''; });
    $('hSufficient').checked = false;
    renderStopMeta();
    $('engineVerdict').textContent = 'Enter an entry and a stop.';
    $('engineVerdict').className = 'verdict neutral';
  });
}

function wireStudio() {
  $('btnSaveConfig').addEventListener('click', async () => {
    try {
      const boot = await call('POST', '/api/config', { account: S.cfg });
      adoptBootstrap(boot);
      S.dirty = false;
      $('dirtyPill').hidden = true;
      toast('Saved to ' + boot.saved);
      runPreview();
    } catch (e) {
      toast('Not saved — ' + e.message + (e.field ? ` (field: ${e.field})` : ''), true);
    }
  });
  $('btnRevertConfig').addEventListener('click', () => {
    S.cfg = JSON.parse(JSON.stringify(S.savedCfg));
    S.dirty = false;
    $('dirtyPill').hidden = true;
    renderStudio();
    toast('Reverted to the saved configuration.');
  });
  $('btnLadderAdd').addEventListener('click', () => {
    const last = S.cfg.derisk_ladder[S.cfg.derisk_ladder.length - 1] || [0, 1];
    S.cfg.derisk_ladder.push([Math.min(1, last[0] + 0.1), Math.max(0, last[1] - 0.1)]);
    renderLadderEditor();
    renderDerived();
    markDirty();
  });
  $('btnLadderReset').addEventListener('click', () => {
    S.cfg.derisk_ladder = [[0, 1], [0.25, 0.75], [0.5, 0.5], [0.7, 0.3],
                           [0.85, 0.15], [1, 0]];
    renderLadderEditor();
    renderDerived();
    markDirty();
  });
}

function wireAccount() {
  $('btnSaveAccount').addEventListener('click', async () => {
    try {
      const r = await call('POST', '/api/account', {
        equity: $('aEquity').value,
        peak_equity: $('aPeak').value,
        day_realised_pnl: $('aDayPnl').value,
        day_trades_taken: $('aDayTrades').value,
        day_consecutive_losses: $('aDayLosses').value,
      });
      S.boot.account = r.account;
      renderHeader(r.account, S.boot.agents);
      renderAccountTab();
      renderDerived();
      toast('Account saved to ' + r.saved);
      runPreview();
    } catch (e) { toast(e.message, true); }
  });

  $('btnResetDay').addEventListener('click', async () => {
    if (!window.confirm('Start a fresh session ledger? Equity and the peak are kept.')) return;
    try {
      const r = await call('POST', '/api/day/reset', {});
      S.boot.account = r.account;
      renderHeader(r.account, S.boot.agents);
      renderAccountTab();
      toast('New session ledger started.');
      runPreview();
    } catch (e) { toast(e.message, true); }
  });

  $('btnAddPos').addEventListener('click', async () => {
    try {
      const r = await call('POST', '/api/position/open', {
        symbol: $('pSymbol').value,
        direction: $('pDirection').value,
        contracts: $('pContracts').value,
        entry: $('pEntry').value,
        stop: $('pStop').value,
      });
      S.boot.account = r.account;
      renderHeader(r.account, S.boot.agents);
      renderAccountTab();
      toast(`Recorded ${r.opened.symbol} ×${r.opened.contracts}.`);
      runPreview();
    } catch (e) { toast(e.message, true); }
  });
}

// ---------------------------------------------------------------------- boot

function adoptBootstrap(boot) {
  S.boot = boot;
  S.savedCfg = JSON.parse(JSON.stringify(boot.account_config));
  if (!S.cfg || !S.dirty) S.cfg = JSON.parse(JSON.stringify(boot.account_config));
  $('caveat').textContent = '';
  $('caveat').appendChild(el('strong', null, 'Read this once: '));
  $('caveat').appendChild(document.createTextNode(boot.guidance.caveat));
  renderHeader(boot.account, boot.agents);
  renderFooter();
  renderStudio();
  renderAccountTab();
  renderAgents();
  renderEvidence();
}

async function main() {
  try {
    const boot = await call('GET', '/api/bootstrap');
    adoptBootstrap(boot);
    fillSymbolSelects();
    renderSymbolMeta();
    wireTicket();
    wireStudio();
    wireAccount();
    showTab('ticket');
  } catch (e) {
    document.body.textContent = '';
    const p = el('div', 'panel');
    p.appendChild(el('h2', null, 'Could not start'));
    p.appendChild(el('p', null, e.message));
    p.appendChild(el('p', 'help',
      'If this says the session token is missing, reopen the URL the application ' +
      'printed when it started — it carries a one-time token.'));
    document.body.appendChild(p);
  }
}

window.addEventListener('beforeunload', (e) => {
  if (S.dirty) { e.preventDefault(); e.returnValue = ''; }
});

main();
