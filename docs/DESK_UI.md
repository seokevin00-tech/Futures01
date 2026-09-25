# Futures Desk — the risk console

A local application for sizing a trade against this account's real constraints.
It runs a standard-library HTTP server on loopback and opens a browser tab; the
"executable" is that server packaged with a Python runtime.

**It reads its configuration, its account state and its own UI from the folder
it sits in — not from inside the build.** That is the central design constraint
and it is enforced in `futures_agents/ui/paths.py`, pinned by
`tests/test_ui_desk.py::test_project_root_is_the_executables_folder_when_frozen`.

---

## Running it

From source:

```bash
python -m futures_agents.ui                 # opens http://127.0.0.1:8787
python -m futures_agents.ui --where         # just print the resolved paths
python -m futures_agents.ui --home ./desk   # use a specific project folder
python -m futures_agents.ui --port 0        # let the OS pick a free port
```

Packaged:

```bash
pip install pyinstaller
pyinstaller packaging/futures-desk.spec
./dist/futures-desk/futures-desk
```

## The project folder

Whatever directory the executable sits in (or the repository root, when running
from source). Override with `--home` or `$FA_DESK_HOME`.

```
futures-desk[.exe]     the application
desk-config.json       your risk configuration      ← edit freely
desk-account.json      equity, peak, day ledger      ← edit freely
assets/                the UI: HTML, CSS, JS         ← edit freely
data/                  journal database
_internal/             Python runtime — do not edit
```

Every file above `_internal/` is plain text, re-read on **every request**.
Change one in a text editor, click anything in the UI, and the change is live.
No rebuild, no restart.

`assets/` is optional. If it is absent the application serves the copy inside
the build, so a bare executable still works. If it is present it always wins —
copy it out of `_internal/futures_agents/ui/assets/` to restyle the console.

## The screens

**Trade ticket.** You type the picture — there is no data feed, and the app
never fetches a price. Every keystroke re-runs the real
`RiskManager.assess()`. You get:

- the **engine verdict** — approved or vetoed, with every blocking reason;
- **discretionary sizing** — what the account's limits would allow if you accept
  the read is discretionary. Kept in a separate block with its own heading,
  because it is not an approval and must never be read as one;
- **what each stop costs** — per contract, in dollars and as a share of equity,
  across 0.25×–3×ATR, with the rows below the 0.5×ATR floor and inside the
  contract's tick floor flagged;
- **if this loses, repeatedly** — equity, buffer, multiplier and trading mode
  after each successive full stop, until something halts;
- **guards** — the 0.5×ATR floor, the tick floor, the 15:00–16:00 ET window,
  whether the symbol has a measured framework, and the index-complex advisory
  described below.

**Risk studio.** All 25 `AccountConfig` fields as paired slider + number box,
grouped, each with an explanation of what it does and why the default is what it
is. The de-risk ladder is an editable table. Derived figures — failure line,
reserve, usable buffer, multiplier, budget, and *which limit is currently
binding* — recompute as you drag, before you save.

Save writes `desk-config.json`. A rejected save changes nothing at all: the
validation is whole-document, so you cannot end up with half an edit applied.

**Account.** Equity, peak, today's ledger, open positions, and an equity curve
with the failure line drawn as a dashed reference.

**Agents.** The roster and the order a cycle runs in, plus whether a reasoning
model is actually reachable. Every number on the risk screens is computed
without a model; the agents add judgement on top, never evidence.

**Evidence.** The per-symbol framework table including the negative verdicts,
the eight measured rules with their evidence, and the cost of a median
structural stop for every contract in the registry.

## Colour

Fixed by `CALLOUT.md` and converted from the xterm-256 indices in
`futures_agents/alerts.py`, so the terminal, the HTML dashboard and this console
agree exactly:

| meaning | colour | ink | contrast |
|---|---|---|---|
| BUY / LONG | `#005fff` blue | white | 5.15:1 |
| SELL / SHORT | `#ff8700` orange | black | 8.72:1 |
| NO TRADE | `#bcbcbc` grey | black | 11.06:1 |

**Blue and orange appear nowhere else in the UI** — not as a border, an accent,
a chart mark or a hover state. `directionBadge()` in `app.js` is the only
function permitted to apply them. The terminal renderer moved SIGNAL off blue
and NO TRADE off orange precisely so nothing in peripheral vision could be
mistaken for a direction; decorating a panel with those hues would undo that.

Status is always an icon **and** a word **and** a colour, never colour alone.

## Two things the console says that the engine does not

**1. Most tickets are vetoed, and that is correct.** The engine refuses any
trade with no measured history. Nothing in this library is live-eligible — no
strategy cleared its multiple-testing threshold — so a hand-typed ticket is
vetoed by default. The discretionary block still sizes it, under its own label,
so the screen is useful without being dishonest.

**2. The index complex is wider than the correlation groups.** `CALLOUT.md`
states that MES/MNQ/NQ/ES are one index complex. `config.py` assigns MNQ/NQ to
`US_EQUITY_TECH` and MES/ES/MYM/YM to `US_EQUITY_BROAD`, so the correlation
limit does **not** fire on a simultaneous MNQ and MES position — the engine
counts them as two diversified trades.

The console does not change that behaviour; correlation groups are risk policy
and changing one alters sizing across the whole system. It raises an advisory
instead (`api.INDEX_COMPLEX`), so the gap is visible at the moment it would cost
something. `test_the_index_complex_gap_is_surfaced_even_though_the_engine_allows_it`
pins both halves: if the groups are ever reconciled, that test fails loudly
rather than letting the advisory become a lie.

## Security

The server binds `127.0.0.1` and is not a network service. Three checks stop a
page in another browser tab from driving it, since it can rewrite your risk
configuration:

- a per-run token, generated at startup and carried in the URL the launcher
  opens (it is stripped from the address bar so a screenshot cannot leak it);
- `Origin` rejection for any cross-site caller;
- loopback binding, with a printed warning if `--host` widens it.

Static serving resolves every path and refuses anything outside `assets/`.
Request bodies over 512 KB are refused unread.

`--open-access` disables the token check. It exists for scripted probing on a
machine you control and prints a warning.
