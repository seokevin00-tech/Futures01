# News & macro: is `econ_calendar.py` fit to condition a backtest on?

Agent: news_macro. Date: 2026-09-22. Repo `/home/user/Futures01`, branch
`claude/intelligent-feynman-ongyjw`, HEAD `f82bd35`.

---

## Verdict

**Before this pass: no.** The design was right and the numbers were wrong. Of
the eight date-bearing rules, six were off by a median of one to four days, and
the single highest-impact event on the sheet - the FOMC decision - was produced
by an arithmetic pattern that was fitted to the 2026 schedule and reproduces
only **52 of 72** published decisions over 2019-2027 while inventing **20 that
never happened**. In 2023 it got 3 of 8. A blackout filter that stands aside on
the wrong day is worse than no filter: it pays the opportunity cost *and* takes
the event risk.

**After this pass: yes, with three named caveats.** Every rule I could measure
is now fitted to published release dates rather than guessed, and the measured
error is in the table below. The caveats:

1. **Core PCE is still only right about half the time** and I could not fix it.
   BEA re-cut the Personal Income and Outlays schedule for 2026 (releases moved
   roughly four weeks after the reference month and paired with GDP), so no
   single business-day index fits both the pre-2026 and post-2026 regimes. The
   rule is unchanged at "last business day"; it is right 6 times in 15 and runs
   late by a median of 1 day.
2. **The 2025-26 government shutdown broke the schedule for a year** and no
   recurrence rule can model that. October 2025 CPI was never published; the
   September CPI came out on 24 Oct 2025; November CPI on 18 Dec 2025; the
   January 2026 Employment Situation came out on a *Wednesday*, 11 Feb 2026.
   Any backtest crossing 2025-10 to 2026-03 should treat the news channel as
   unreliable rather than merely noisy.
3. **The blackout window is still too short after the event** (see below), and
   it is a single global pair of numbers that cannot express "5 minutes for
   jobless claims, 60 minutes for an FOMC press conference".

**Separately and importantly: this repo contains no real market data.** There
are no CSVs in `data/`; `data/samples/` is empty; `load_symbol()` falls through
to `synthetic_series()`, a seeded random walk that places its one daily "news
shock" at a *uniformly random minute*. I measured this: across 165,600
synthetic 1-minute MNQ bars, 08:30 ranks **243rd of 1,380** minutes by mean
absolute bar move (MCL: 247th). If these were real bars, 08:30 would be the
largest pre-RTH minute of the day by a wide margin. Consequence: the 8 rows
currently in the `news_reactions` table are measurements of a random walk, not
of news, and no reaction statistic derived from this repo means anything yet. I
have not deleted them; they should be quarantined or re-measured against real
bars before any confidence number is computed from them.

---

## Rule-by-rule verification

Method: BLS publishes archived news releases at
`bls.gov/news.release/archives/<series>_MMDDYYYY.htm` - the **release date is
encoded in the filename**, so a search result's URL is itself primary evidence
even when the page cannot be fetched. I collected 26 CPI, 18 Employment
Situation and 20 PPI release dates this way, plus BEA GDP/Personal-Income dates
and Census retail dates from release-page titles and PDF embargo lines, plus
FOMC decision days from `federalreserve.gov` meeting/minutes/press-conference
URLs. "clean" excludes the 2025-26 shutdown-distorted releases.

| Rule | What the source says | Projected vs actual (before) | Error (before) | Change made | Error (after) | Verdict |
|---|---|---|---|---|---|---|
| **CPI** `business_day=10` 08:30 | BLS sets CPI dates ~2 years ahead; it is **not** a business-day rule. Measured index over 26 releases: 8 in 20 cases, range 7-10, at 08:30 ET | 3/26 exact | MAE **2.12 d**, median **+2 d** (systematically late) | → `business_day=8`, counted on the **federal** calendar | 18/26 exact (18/25 clean), MAE **0.77 d** (0.48 clean) | **Was wrong. Fixed.** Note the repo's own live agent calendar (`agents/news_macro.py`) already used 8 - the deterministic core disagreed with it by two business days |
| **PPI** `business_day=11` 08:30 | Measured index over 15 clean releases: modal 9, range 7-12. Empirically PPI ≈ CPI ± 1 day | 2/20 exact | MAE **4.05 d** | → `business_day=9` | 8/20 exact (7/15 clean), MAE 3.20 d (**1.07 clean**) | **Was wrong. Fixed.** Residual error is almost entirely the six shutdown-era prints (e.g. Dec-2025 data on 30 Jan 2026) |
| **Nonfarm payrolls** first Friday, `holiday_shift=-1` | BLS: released on the **third Friday after the conclusion of the reference week** (the Sun-Sat week containing the 12th). "First Friday" is folklore; it fails whenever the 12th falls early in its week | 11/18 exact. Misses: 8 Mar 2024, 10 Jan 2025, 9 Jan 2026, 8 May 2026 (all −7 d), 3 Apr 2026 (−1 d) | MAE **2.50 d** | → new `empsit` pattern implementing the reference-week rule, + a documented one-week slip when it lands 1-3 Jan (observed for Dec-2024 and Dec-2025 data) | **16/18 exact (15/15 clean), MAE 0.00 clean** | **Pattern was wrong; the earlier-shift direction was right.** Confirmed: BLS moved the June-2026 report to **Thursday 2 July 2026** for the Friday 3 July holiday |
| **Payrolls holiday shift = earlier** | Verified. DOL note: "Because of the observance of the Independence Day holiday on Friday, July 3, BLS released its monthly Employment Situation Report on Thursday, July 2, at 8:30 a.m. EDT" | — | — | kept `-1` | — | **Correct as written** |
| **Initial claims** weekly Thursday 08:30, shift earlier | DOL/ETA: published each **Thursday 08:30 ET**, with exceptions when Thursday is a federal holiday. Thanksgiving 2025: released **Wednesday 26 Nov 2025** | not date-measured (weekly; day-of-week is right by construction) | — | none to the pattern; the holiday test now uses the federal calendar | — | **Day and time correct. Thanksgiving shift verified. The New Year's-Day direction is UNVERIFIED** - I found a dol.gov release dated Friday 2 Jan 2026, which would be a *later* shift, and could not confirm what it was. Flagged, not changed |
| **Retail sales** `business_day=12` 08:30 | Census Advance Monthly Retail, 08:30 ET. Measured index over 12 clean releases: modal 11, range 10-12 | 4/13 exact | MAE **1.85 d** | → `business_day=11` | 6/13 exact (6/12 clean), MAE 1.31 d (0.92 clean) | **Was wrong. Fixed.** Still the loosest of the monthly rules |
| **GDP** `business_day=18`, every month | The monthly cadence **is real** - BEA publishes one estimate every month (advance/second/third in rotation: 30 Apr, 28 May, 25 Jun, 30 Jul, 26 Aug, 30 Sep 2026). But it lands at **month end**, not business day 18 | 2/11 exact | MAE **2.91 d**, median **−2 d** (systematically early) | → `business_day=-2` | 5/11 exact (5/10 clean), MAE **1.36 d** (0.90 clean) | **Cadence right, date wrong. Date fixed.** The "fires every month" behaviour is modelling something real and should stay |
| **Core PCE** `business_day=-1` 08:30 | BEA Personal Income & Outlays, 08:30 ET. 2025: 31 Jan, 28 Feb, 28 Mar, 30 Apr, 30 May, 27 Jun - i.e. roughly the **last Friday**. 2026 after BEA's schedule change: 30 Apr, 28 May, 25 Jun, 30 Jul, 26 Aug, 30 Sep - now co-released with GDP | 6/15 exact | MAE **4.27 d** (1.50 clean), always late | **none** - no single index fits both regimes; I did not want to overfit one era | unchanged | **Partially wrong, deliberately NOT changed.** Best alternatives measured: `bd=-1` 6/12 clean exact / MAE 1.50; `bd=-2` 4/12 exact / MAE 1.00. Neither dominates. Documented in the rule |
| **ISM Manufacturing** `business_day=1` 10:00 | ISM: Manufacturing Report On Business released on the **first business day of the month at 10:00 a.m. ET** | — | — | none | — | **Pattern and time correct against the stated ISM schedule. I did NOT verify actual ISM release dates** (ismworld.org is blocked from this session); this is a rule check, not a date check |
| **FOMC statement** `fomc` pattern, 14:00 | Fed: "The Committee releases a policy statement at 2 p.m. Eastern Time on the second day of each regularly scheduled meeting". The dates are set by vote, not by arithmetic | **52/72** decisions covered over 2019-2027; **20/72 projections spurious (28%)**. Per year: 2019 7/8, 2020 5/8, 2021 5/8, 2022 6/8, 2023 **3/8**, 2024 **4/8**, 2025 7/8, 2026 8/8, 2027 7/8 | up to **±7 d**, plus 4 whole meetings a year with no blackout at all in 2023-24 | → stored `_FOMC_DECISION_DAYS` table for **2019-2027**, verified against federalreserve.gov; the pattern is kept only as the fallback outside that range | **72/72 exact 2019-2027** | **The 8/8-for-2026 claim is true and misleading** - the pattern was fitted to 2026. It is the worst rule in the file on any other year. Fixed by storing the published schedule |
| **FOMC press conference** 14:00-rule at 14:30, HIGH | Fed: "the Chair holds a news conference at 2:30 p.m. Eastern Time the same day". Powell moved to a presser after **every** meeting starting with the 30 Jan 2019 FOMC; before that they followed only SEP meetings | same date errors as the statement, **plus** it fired at 14:30 on every pre-2019 meeting, which is wrong 4 times a year for 2011-2018 | — | → new `fomc_presser` pattern, gated to meetings on/after 2019-01-30 | 72/72 for 2019-2027; **no 14:30 event before 2019** (declared gap, not a guess) | **Time correct; separate-HIGH rating correct** (see impact ratings below). Date source and pre-2019 gating fixed |

### Code defects found alongside the rules

| Defect | Where | Status |
|---|---|---|
| Business days counted on the **exchange** holiday table (`timeutil.is_market_holiday`), which (a) omits Columbus Day and Veterans Day, when BLS/BEA/Census are shut and the market trades, (b) *includes* Good Friday, when the agencies publish, and (c) **is only populated for 2025-2027**, so every holiday adjustment is silently inert in a 2019-2024 backtest | `econ_calendar._business_days`, `_shift_off_holiday` | **Fixed.** New computed `is_federal_holiday()` (with Juneteenth from 2021 and weekend-observance rules). Worked example: BLS published payrolls on Good Friday **3 Apr 2026** and CME opened an abbreviated equity-index session around it; the old code shifted the release to 2 Apr |
| Blackout window inverted: `-after_min <= (start - ev.when) <= before_min` gives `[event−5, event+10]`, the mirror image of the documented `[event−10, event+5]`. It left the minutes *immediately before* an 08:30 print open for business | `econ_calendar.event_proximity`, `features.SymbolFrame._build_news_proximity` | **Already fixed in the working tree by a concurrent change** - I found it independently and confirm the fix is correct. I did not touch it |
| `days[idx - 1]` evaluated *before* the bounds check, so the guard behind it is dead code and any `business_day` past the shortest month's count raises `IndexError` instead of clamping | `econ_calendar._rule_dates` | **Fixed** |
| A weekly event shifted backwards off a holiday into the previous month was deleted by the `d.month == month` filter - the calendar went silent on exactly the weeks it should not | `econ_calendar._rule_dates` | **Fixed** (filter now skipped for the weekly patterns; `project_events` de-duplicates) |
| Two calendars in one repo disagreed: `econ_calendar.ECON_RULES` had CPI at business day 10 / PPI 11 / retail 12, while `agents/news_macro.ECONOMIC_CALENDAR` had 8 / 9 / 11 for the same releases | both | Now consistent on those three. `agents/news_macro.py` still uses `is_market_holiday` for its business-day counting and still uses first-Friday for payrolls - **not fixed, outside the file I was authorised to edit** |

### Before/after, 2026-09-22 + 30 days

Projected market-wide (no symbol), corrected rules:

```
2026-09-24 Thu 08:30  MEDIUM US Initial Jobless Claims
2026-09-29 Tue 08:30  MEDIUM US GDP
2026-09-30 Wed 08:30  HIGH   US Core PCE
2026-10-01 Thu 08:30  MEDIUM US Initial Jobless Claims
2026-10-01 Thu 10:00  MEDIUM ISM Manufacturing
2026-10-02 Fri 08:30  HIGH   US Nonfarm Payrolls
2026-10-13 Tue 08:30  HIGH   US CPI
2026-10-14 Wed 08:30  MEDIUM US PPI
2026-10-16 Fri 08:30  MEDIUM US Retail Sales
```

The same window under the old rules put CPI on 15 Oct (+2 d), PPI on 16 Oct
(+2 d), retail on 19 Oct (+3 d) and payrolls on 2 Oct (correct, by luck: this
is a month where the reference-week rule and the first-Friday rule agree).

`python -m pytest tests/ -q` → **658 passed**.

---

## Missing releases

Ranked by how much they matter to the four contracts this desk trades
(MNQ, MES, MGC, MCL). "Add" means I would put it in `ECON_RULES`.

| # | Release | Schedule (verified where stated) | Moves | Verdict |
|---|---|---|---|---|
| 1 | **EIA Weekly Petroleum Status Report** (crude inventories) | Wednesday **10:30 ET**; "for some weeks that include holidays, releases are delayed by one day" (→ Thursday) | **MCL, hard.** The largest *scheduled* mover of WTI, ~52×/year. Negligible for MNQ/MES/MGC | **ADDED.** This was the single biggest gap: MCL is in the contract list and the calendar contained **zero** energy events, so for MCL the entire HIGH set was equity/rates releases. Implemented as `eia_weekly`, HIGH, **scoped by symbol** (`symbols=("MCL","CL","MNG","NG")`) so it cannot black out MNQ. `project_events(..., symbol="MCL")` opts in; existing callers are unaffected. **`features.SymbolFrame._build_news_proximity` must pass `symbol=self.symbol` for MCL backtests to see it - I did not make that change** (outside my edit scope) |
| 2 | **ISM Services PMI** | Third business day of the month, **10:00 ET** (fourth in January) | MES/MNQ; occasionally the biggest 10:00 move of the month | **Add.** Cheap, well-defined, and already present in the repo's live agent calendar at HIGH. Not added here only because I could not verify actual ISM release dates from a primary source |
| 3 | **ADP National Employment Report** | Wednesday **08:15 ET**, two days before payrolls | MNQ/MES/MGC, moderately - it is the market's payroll prior | **Add at MEDIUM.** Time verified via the NY Fed indicator calendar; its date is a simple function of the (now correct) payroll date |
| 4 | **JOLTS** | **10:00 ET** | MES/MNQ; briefly became a top-tier mover in 2023-24 when the Fed was watching vacancies | **Add at MEDIUM**, with the explicit note that its importance is regime-dependent - exactly the thing a static impact rating models badly |
| 5 | **University of Michigan consumer sentiment** | **10:00 ET**; preliminary mid-month Friday, final late-month Friday | MES/MNQ small; the **inflation-expectations sub-index** has produced outsized moves (it is a stated Fed input) | **Add at MEDIUM.** Two releases a month; the final is near-noise, the preliminary is not |
| 6 | **Treasury quarterly refunding (QRA)** | Refunding statement **08:30 ET Wednesday**, typically the first Wednesday of Feb/May/Aug/Nov; borrowing estimates the preceding Monday ~15:00 | MES/MNQ/MGC **via the long end**. The Aug-2023 QRA was a top-5 bond event of the year | **Add at MEDIUM, 4×/year.** Low cost, and it is the one calendar item that moves equities through duration rather than through growth or inflation |
| 7 | **Treasury coupon auctions** (10y, 30y) | Competitive close **13:00 ET** for notes/bonds (11:30 for bills) | MES/MNQ/MGC on tail/no-tail, but usually seconds, not minutes | **Do not add.** The date depends on the refunding schedule and settlement conventions; a recurrence rule would be wrong often enough to be net-negative, and the reaction is small and short |
| 8 | **Conference Board consumer confidence** | Last Tuesday, 10:00 ET | MES/MNQ, small | **Do not add** to the deterministic core. It is already in the live agent calendar; adding a MEDIUM event that never creates a blackout buys nothing a backtest can condition on |
| 9 | **Fed speeches / Chair testimony (Humphrey-Hawkins)** | Semiannual testimony, no fixed rule | MES/MNQ/MGC, sometimes a lot | **Do not add.** Not rule-derivable. This belongs to the live agent, which can read the Fed's calendar, not to the backtest core |
| 10 | **Geopolitical events, OPEC+ meetings** | OPEC+ meets on announced but irregular dates | MCL, heavily | **Do not add as a rule.** OPEC+ dates move; a wrong rule is worse than an absent one. Live-agent territory |

Gold (MGC) deserves a note: it has **no dedicated release** on this calendar
and does not need one. Gold trades off real yields and the dollar, so its
scheduled movers are exactly CPI, payrolls, core PCE and the FOMC - all already
present and now on the right dates.

---

## Blackout window

**This section is CITATION, not measurement.** I could not measure reaction
decay and I want to be explicit about why rather than produce a number that
looks measured.

**What I did measure** (in this repo, reproducible): `futures_agents/data/loader.py`
has no CSV to load - `data/samples/` is empty - so `load_symbol()` returns
`synthetic_series()`, whose single daily "news shock" is placed at
`rng.randrange(n_minutes)`, i.e. a uniformly random minute, uncorrelated with
any release time. Over 165,600 synthetic 1-minute bars, mean |close−open| at
08:30 ranks **243rd of 1,380** minutes for MNQ and **247th of 1,380** for MCL;
10:30 ranks 163rd/166th; 14:00 ranks 238th/231st. There is no news signature in
these bars, therefore no reaction decay to measure, therefore the existing
`news_reactions` rows measure noise.

**What the literature says:**

- Ederington & Lee (1993, *Journal of Finance* 48:1161-91), on scheduled US
  macro releases in interest-rate and FX futures: the main price adjustment
  occurs **within the first minute**; volatility is **above normal for about 15
  minutes** and slightly elevated for **several hours** afterwards.
- Gürkaynak, Sack & Swanson (2005) and the monetary-policy-surprise literature
  built on it measure FOMC announcement effects over a **30-minute window: 10
  minutes before to 20 minutes after** the announcement. That window exists
  because it is the interval over which the market is judged to have fully
  repriced - it is the literature's own answer to "how long does it last".
- Energy: work on EIA inventory announcements in US energy futures finds price
  return, volatility and volume respond very quickly and the response **lasts
  about 25 minutes**.
- FOMC press conference: Boguth, Grégoire & Martineau document a **volatility
  spike just after 14:30**, coinciding with the start of the press conference,
  and larger market moves on meeting days that carry one. The conference runs
  roughly an hour and the Q&A is where the moves happen.

**Recommendation.** The current `-10 / +5` is defensible on the *before* side
and too short on the *after* side for every event on the sheet.

| Event class | Recommended window | Basis |
|---|---|---|
| 08:30 macro (CPI, payrolls, core PCE) | **−10 / +15 min** | Ederington-Lee "above normal for about 15 minutes" |
| FOMC statement, 14:00 | **−15 / +30 min** | GSS-style 30-minute repricing window, widened on the entry side because pre-announcement liquidity thins |
| FOMC press conference, 14:30 | **−5 / +60 min** (in practice: treat 14:00-15:15 as one blocked block) | The conference runs ~1 hour and the largest moves are in the Q&A |
| EIA inventories, 10:30 (MCL only) | **−5 / +25 min** | ~25-minute energy response |

The opportunity cost of widening is small and measurable: the corrected rules
project **51 high-impact events across 40 distinct days in 2024**. At −10/+5
that is ~14 hours of blackout per year; at −10/+15 it is ~22 hours. Roughly
0.4% of a 23-hour session year either way.

Two structural blockers to implementing this properly, both of which I am
reporting rather than changing:

1. `event_proximity`/`_build_news_proximity` take **one global (before, after)
   pair**. Per-event windows need the two numbers to move onto `CalendarRule`.
   Until then, `AccountConfig.news_blackout_after_min = 5` is the binding
   constraint and I would raise it to **15** as the single best compromise.
2. If `after` goes to 15, the `post_news_window` filter in
   `strategies/library.py` (currently "5 to 60 minutes after a high-impact
   release") **overlaps the blackout** for 10 of those minutes, so a snapshot
   would simultaneously report `in_news_blackout=True` and
   `post_news_window=True`. Its lower bound must move to match `after`.

---

## Impact ratings

Only HIGH creates a blackout, so HIGH-vs-MEDIUM is doing real work.

- **CPI = HIGH: keep.** No serious dispute, and it is the release the whole
  rates complex is positioned into.
- **PPI = MEDIUM: keep, but flagged as the least defensible rating on the
  sheet.** The argument for promoting it is that since 2022 desks map PPI's
  health-care, portfolio-management and airfare components straight into the
  core-PCE nowcast, and PPI now lands the day after CPI, so it moves rates and
  therefore MNQ/MGC. I could **not** verify that with a quantitative source and
  I have no data to measure it, so I left it at MEDIUM rather than assert it.
- **FOMC press conference as its own HIGH at 14:30: correct.** Boguth et al.
  document a distinct volatility spike at 14:30, separate from the 14:00
  statement. The two are genuinely different events - the statement moves the
  path, the Q&A moves the interpretation - and modelling them as one 14:00
  event would leave the 14:30 spike unguarded. **Now correctly gated to
  meetings from 30 Jan 2019**, when Powell moved to a presser after every
  meeting; before that they were quarterly and the 14:30 event fired on dates
  when no press conference existed.
- **ISM Manufacturing = MEDIUM: this is the rating I would most like to
  re-measure.** The only ranking study I found - Gilbert, Scotti, Strasser &
  Vega, "Is the intrinsic value of macroeconomic news announcements related to
  their asset price impact?" - concludes that the ISM/NAPM index moves markets
  *the most*, above the employment report, because it carries comparable
  information a few days earlier. This repo's own live agent calendar rates ISM
  HIGH. The deterministic core rates it MEDIUM. **I did not change it**,
  because promoting it would add ~12 blackout days a year on the strength of
  one paper I could only read through a search summary, but the inconsistency
  between the two calendars in this repo should be resolved deliberately.
- **Core PCE = HIGH: keep, weakest of the three.** Defensible as the Fed's
  preferred gauge, but it lands ~2.5 weeks after CPI has already revealed most
  of its content, so its surprise component is structurally smaller. Cannot
  measure here.
- **Retail sales / GDP / claims = MEDIUM: keep.** No evidence to move them.

---

## What I could not verify

Stated explicitly, because an unchecked rule is more useful reported as
unchecked than asserted as correct.

- **I could not fetch a single primary-source page.** Every `.gov` domain I
  tried (`bls.gov`, `bea.gov`, `census.gov`, `federalreserve.gov`, `eia.gov`,
  `dol.gov`, `newyorkfed.org`, `fred.stlouisfed.org`) and every secondary one
  returned `EGRESS_BLOCKED` from WebFetch; `curl https://www.bls.gov/...`
  returned `CONNECT tunnel failed, response 403`, i.e. an organisation egress
  policy denial, which the proxy README says to report rather than route
  around. **All evidence below came from WebSearch result titles, URLs and
  snippets.** BLS archive URLs are strong evidence because the release date is
  in the filename; BEA/Census/Fed dates rest on page titles and PDF embargo
  lines and are one step weaker.
- **Release dates I did not obtain:** CPI for Jan/May/Nov 2024 and Jan 2025;
  the Sep-2026 CPI date (two secondary sources say 11 Sep 2026, no BLS URL);
  payrolls for Aug/Sep 2024 and Feb-Nov 2025; most 2024 PPI; all 2024 retail
  sales; all 2024 GDP; **all ISM release dates** (I verified only ISM's stated
  schedule rule, not that it holds).
- **The jobless-claims holiday-shift direction is only half verified.**
  Thanksgiving → Wednesday is confirmed (DOL release dated 26 Nov 2025). A
  dol.gov ETA release dated **Friday 2 Jan 2026** suggests the New Year's-Day
  case may shift *later*, which the uniform `holiday_shift=-1` would get wrong.
  I could not confirm what that release was and did not change the rule.
- **The EIA holiday rule is modelled, not transcribed.** EIA states only that
  "for some weeks that include holidays, releases are delayed by one day". I
  implemented "a federal holiday on the Monday, Tuesday or Wednesday of that
  week pushes the report to Thursday", which reproduces the Memorial Day,
  Labor Day and MLK cases correctly and leaves Thanksgiving week on Wednesday.
  I could not fetch EIA's actual per-week schedule table to check every week.
- **No reaction statistics.** No real bars exist in this repo, so every
  quantitative claim about how MNQ/MES/MGC/MCL behave after a release is
  absent from this document by design.

---

## Sources

Every URL below was surfaced by WebSearch during this pass. None could be
fetched directly (see above); BLS archive URLs are cited because the filename
encodes the release date.

**BLS - release dates (filename = release date)**
- https://www.bls.gov/news.release/archives/cpi_02132024.htm, `cpi_03122024`, `cpi_04102024`, `cpi_06122024`, `cpi_07112024`, `cpi_08142024`, `cpi_09112024`, `cpi_10102024`, `cpi_12112024`
- `cpi_02122025`, `cpi_03122025`, `cpi_04102025`, `cpi_05132025`, `cpi_06112025`, `cpi_07152025`, `cpi_08122025`, `cpi_09112025`, `cpi_12182025`
- `cpi_01132026`, `cpi_02132026`, `cpi_03112026`, `cpi_04102026`, `cpi_05122026`, `cpi_06102026`, `cpi_07142026`, `cpi_08122026`
- https://www.bls.gov/news.release/archives/empsit_02022024.htm, `empsit_03082024`, `empsit_04052024`, `empsit_05032024`, `empsit_06072024`, `empsit_07052024`, `empsit_10042024`, `empsit_11012024`, `empsit_12062024`, `empsit_01102025`, `empsit_12162025`, `empsit_01092026`, `empsit_02112026`, `empsit_04032026`, `empsit_05082026`, `empsit_06052026`, `empsit_07022026`, `empsit_08072026`
- https://www.bls.gov/news.release/archives/ppi_02162024.htm, `ppi_03142024`, `ppi_04112024`, `ppi_05142024`, `ppi_02132025`, `ppi_03132025`, `ppi_04112025`, `ppi_05152025`, `ppi_06122025`, `ppi_07162025`, `ppi_09102025`, `ppi_11252025`, `ppi_01142026`, `ppi_01302026`, `ppi_02272026`, `ppi_03182026`, `ppi_06112026`, `ppi_07152026`, `ppi_08132026`, `ppi_09102026`
- https://www.bls.gov/schedule/news_release/cpi.htm and https://www.bls.gov/schedule/news_release/empsit.htm (schedule pages, blocked)
- https://www.bls.gov/bls/092025-cpi-reschedule-notice.htm and https://www.bls.gov/bls/2025-lapse-revised-release-dates.htm (shutdown reschedule)
- https://www.bls.gov/news.release/empsit.tn.htm (Employment Situation technical note / reference week)

**BEA**
- https://www.bea.gov/news/schedule
- https://www.bea.gov/news/blog/2026-01-07/economic-release-schedule-updates-gdp-personal-income-and-outlays (the 2026 schedule change)
- https://www.bea.gov/news/blog/2025-12-10/economic-release-schedule-updates
- https://www.bea.gov/sites/default/files/2026-03/pi0126.pdf ("EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Friday, March 13, 2026")
- https://www.bea.gov/sites/default/files/2026-02/pi1225.pdf ("8:30 a.m. EST, Friday, February 20, 2026")
- https://bea.gov/sites/default/files/2026-05/pi0426.pdf ("8:30 a.m. EDT, Thursday, May 28, 2026")
- https://www.bea.gov/sites/default/files/2025-04/gdp1q25-adv.pdf, https://www.bea.gov/sites/default/files/2025-07/gdp2q25-adv.pdf
- https://www.bea.gov/news/2026/gdp-advance-estimate-1st-quarter-2026, `.../gdp-second-estimate-and-corporate-profits-1st-quarter-2026`, `.../gdp-advance-estimate-2nd-quarter-2026`, `.../gdp-second-estimate-and-corporate-profits-2nd-quarter-2026`
- https://www.bea.gov/news/2026/personal-income-and-outlays-january-2026, `...-february-2026`, `...-march-2026`, `...-june-2026`

**Census**
- https://www.census.gov/retail/release_schedule.html
- https://www.census.gov/retail/marts/www/martsdates.pdf
- https://www.census.gov/economic-indicators/econcards/assets/pdf/censusreleaseglance_2025.pdf
- https://www.census.gov/economic-indicators/calendar-listview.html (blocked)

**Federal Reserve**
- https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- https://www.federalreserve.gov/newsevents/pressreleases/monetary20180525a.htm (2019 schedule), `monetary20190517a` (2020), `monetary20200702a` (2021), `monetary20210604a` (2022), `monetary20220624a` (2023), `monetary20230623a` (2024), `monetary20240809a` (2025 and 2026), `monetary20250905a` (2027)
- https://www.federalreserve.gov/monetarypolicy/fomcminutes20260128.htm, `fomcminutes20260318`, `fomcminutes20260429`, `fomcminutes20260729`, `fomcpresconf20260617`, `fomcpresconf20260916`, `fomcpresconf20250129`, `fomcminutes20250618`
- https://www.federalreserve.gov/monetarypolicy/fomcminutes20200315.htm and https://www.federalreserve.gov/monetarypolicy/fomcpresconf20200315.htm (the emergency meeting that replaced 17-18 March 2020)
- https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20190130.pdf (first after-every-meeting press conference)
- https://www.federalreserve.gov/econres/notes/feds-notes/questions-and-answers-the-information-content-of-the-post-fomc-meeting-press-conference-20211012.html
- https://www.cnbc.com/2018/06/13/feds-powell-says-he-will-begin-press-conferences-following-each-meeting-starting-in-january.html

**DOL / ETA**
- https://www.dol.gov/newsroom/releases/eta/eta20251126 (Thanksgiving-week claims, Wednesday 26 Nov 2025)
- https://www.dol.gov/newsroom/releases/eta/eta20260102 (the unresolved New Year case)
- https://www.dol.gov/ui/data.pdf, https://oui.doleta.gov/unemploy/claims.asp
- https://www.dol.gov/newsroom/releases/opa/opa20200701 ("the ETA Unemployment Insurance Weekly Claims release, which occurs each Thursday at 8:30 a.m."; also the Thursday 2 July payroll-release precedent)

**EIA**
- https://www.eia.gov/petroleum/supply/weekly/schedule.php ("released after 10:30 a.m. eastern time on Wednesday"; "for some weeks that include holidays, releases are delayed by one day")
- https://www.eia.gov/petroleum/weekly/includes/schedule.php, https://www.eia.gov/reports/upcoming.php

**ISM**
- https://www.ismworld.org/supply-management-news-and-reports/reports/rob-report-calendar/
- https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/

**Treasury**
- https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents
- https://home.treasury.gov/news/press-releases/po946 ("New procedures for Treasury's quarterly refunding announcement")
- https://treasurydirect.gov/auctions/general-auction-timing/

**Other schedules**
- https://www.newyorkfed.org/research/calendars/i-sep26.html (ADP 08:15, JOLTS 10:00, Michigan 10:00)
- https://statspolicy.gov/assets/fcsm/files/docs/OMB_pfei_schedule_release_dates_cy2026.pdf (OMB principal federal economic indicators schedule)

**Reaction-duration literature (citations, not measurements)**
- Ederington & Lee (1993), "How Markets Process Information: News Releases and Volatility", *Journal of Finance* 48:1161-91 — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1993.tb04750.x
- Gürkaynak, Sack & Swanson (2005), "Do Actions Speak Louder Than Words?" — https://www.federalreserve.gov/pubs/feds/2005/200529/200529pap.pdf
- Bjursell, Gentle & Wang (2015), "Inventory announcements, jump dynamics, volatility and trading volume in U.S. energy futures markets", *Energy Economics* — https://www.sciencedirect.com/science/article/abs/pii/S0140988314002771
- Wen, Indriawan, Lien & Xu (2023), "Intraday Return Predictability in the Crude Oil Market: The Role of EIA Inventory Announcements" — https://journals.sagepub.com/doi/abs/10.5547/01956574.44.4.zwen
- Boguth, Grégoire & Martineau, "Shaping Expectations and Coordinating Attention: The Unintended Consequences of FOMC Press Conferences", *JFQA* — https://www.ssrn.com/abstract=2698477
- Gilbert, Scotti, Strasser & Vega, "Is the Intrinsic Value of Macroeconomic News Announcements Related to Their Asset Price Impact?" — https://www.federalreserve.gov/econresdata/feds/2015/files/2015046r1pap.pdf
- Andersen, Bollerslev, Diebold & Vega (2007), "Real-time price discovery in global stock, bond and foreign exchange markets", *JIE* 73:251-277 — https://www.nber.org/papers/w11312

**Good Friday payrolls / CME session**
- https://www.cmegroup.com/tools-information/holiday-calendar/files/2026/2026-good-friday-clearing-advisory.pdf
- https://investinglive.com/news/why-are-non-farm-payrolls-being-released-on-good-friday-heres-whats-open-and-what-isnt-20260402/

**"First Friday" is folklore**
- https://www.marketplace.org/story/2023/03/03/why-no-jobs-report-first-friday-this-month
