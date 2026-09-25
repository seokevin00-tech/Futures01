"""Tests for the desk console: paths, validation, the API and the server.

The invariant that matters most here is the one the packaging exists to
guarantee: **the application's configuration is never baked into the build.**
``test_project_root_is_the_executables_folder_when_frozen`` pins that directly,
because if it ever regresses the failure is silent - a user edits a file, sees
no change, and trades a size the screen is not showing.

Everything runs against a temporary project folder. Nothing in these tests
touches ``csv/raw/``, which is read-only source data.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from futures_agents.config import AccountConfig, get_contract
from futures_agents.ui import api, guidance
from futures_agents.ui.paths import DESK_HOME_ENV, ProjectPaths, project_root
from futures_agents.ui.server import build_server
from futures_agents.ui.state import (ConfigError, account_state_from_dict,
                                     account_state_to_dict, coerce_account_config,
                                     load_account_state, load_system_config,
                                     save_system_config)


@pytest.fixture()
def desk(tmp_path, monkeypatch) -> ProjectPaths:
    """A throwaway project folder, wired up the way the launcher wires one."""
    monkeypatch.setenv(DESK_HOME_ENV, str(tmp_path))
    return ProjectPaths(root=tmp_path).ensure()


# ---------------------------------------------------------------- paths

def test_project_root_follows_the_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(DESK_HOME_ENV, str(tmp_path))
    assert project_root() == tmp_path.resolve()


def test_project_root_is_the_executables_folder_when_frozen(tmp_path, monkeypatch):
    """The whole point of the packaging: beside the exe, never inside the bundle.

    ``sys._MEIPASS`` is the temporary directory a one-file build unpacks into and
    deletes on exit. Resolving the project root there would mean every restart
    silently reverted the user's risk configuration to the build's defaults.
    """
    monkeypatch.delenv(DESK_HOME_ENV, raising=False)
    exe = tmp_path / "bin" / "futures-desk"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe), raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_MEI_temp"), raising=False)

    assert project_root() == exe.parent
    assert project_root() != Path(sys._MEIPASS)


def test_external_assets_win_over_the_bundled_copy(desk):
    assert desk.assets_are_external is False   # nothing beside the exe yet
    (desk.root / "assets").mkdir()
    assert desk.assets_are_external is True
    assert desk.assets_dir == desk.root / "assets"
    assert desk.describe()["assets_source"] == "project folder"


# ----------------------------------------------------------- validation

def test_ladder_that_sizes_up_into_a_drawdown_is_rejected():
    """The one edit that can invert the engine rather than merely mis-tune it."""
    base = AccountConfig()
    with pytest.raises(ConfigError) as exc:
        coerce_account_config(base, {"derisk_ladder": [[0.0, 0.5], [0.5, 0.9]]})
    assert "size the account UP" in str(exc.value)


def test_ladder_thresholds_must_increase_and_start_at_zero():
    base = AccountConfig()
    with pytest.raises(ConfigError, match="must start at 0.0"):
        coerce_account_config(base, {"derisk_ladder": [[0.2, 1.0], [0.5, 0.5]]})
    with pytest.raises(ConfigError, match="strictly increase"):
        coerce_account_config(base, {"derisk_ladder": [[0.0, 1.0], [0.0, 0.5]]})


def test_soft_limit_beyond_the_hard_limit_is_rejected():
    with pytest.raises(ConfigError, match="could never take effect"):
        coerce_account_config(AccountConfig(), {"daily_soft_loss_limit": 9_999})


def test_min_risk_above_max_risk_is_rejected():
    with pytest.raises(ConfigError, match="no trade could ever be sized"):
        coerce_account_config(AccountConfig(), {"min_dollar_risk": 900})


def test_reserving_the_whole_allowance_is_rejected():
    with pytest.raises(ConfigError):
        coerce_account_config(AccountConfig(), {"protected_buffer_pct": 1.0})


def test_unknown_keys_are_ignored_not_fatal():
    """A newer UI talking to an older build should degrade, not break."""
    out = coerce_account_config(AccountConfig(), {"not_a_real_field": 3,
                                                 "max_trades_per_day": 4})
    assert out.max_trades_per_day == 4


def test_non_numeric_input_names_the_field():
    with pytest.raises(ConfigError) as exc:
        coerce_account_config(AccountConfig(), {"daily_loss_limit": "lots"})
    assert exc.value.field == "daily_loss_limit"


# --------------------------------------------------------- persistence

def test_config_round_trips_through_the_project_folder(desk):
    cfg = load_system_config(desk)
    cfg.account = coerce_account_config(cfg.account, {"starting_equity": 25_000,
                                                      "max_trades_per_day": 3})
    save_system_config(cfg, desk)
    assert desk.config_file.is_file()

    reloaded = load_system_config(desk)
    assert reloaded.account.starting_equity == 25_000
    assert reloaded.account.max_trades_per_day == 3
    # The ladder survives the JSON hop as tuples, not lists of lists.
    assert reloaded.account.derisk_ladder[0] == (0.0, 1.0)


def test_account_state_persists_inputs_not_derived_reporting(desk):
    cfg = load_system_config(desk)
    state = load_account_state(cfg, desk)
    state.equity = 47_500.0
    payload = account_state_to_dict(state)
    # Derived fields are functions of the config; persisting them would let a
    # config change silently disagree with the stored numbers.
    assert "usable_buffer" not in payload
    assert "derisk_multiplier" not in payload
    assert payload["equity"] == 47_500.0

    back = account_state_from_dict(cfg.account, payload)
    assert back.equity == 47_500.0


def test_unreadable_state_file_raises_rather_than_being_overwritten(desk):
    """Silently replacing it would discard the account's drawdown history."""
    desk.state_file.write_text("{ not json")
    cfg = load_system_config(desk)
    with pytest.raises(ConfigError, match="could not be read as JSON"):
        load_account_state(cfg, desk)


# ---------------------------------------------------------------- api

def test_bootstrap_describes_every_account_field(desk):
    boot = api.bootstrap(p=desk)
    meta_keys = {m["key"] for m in boot["account_field_meta"]}
    # Every editable field carries a label and an explanation; a risk control the
    # user cannot interpret is one they will set wrongly.
    assert meta_keys <= set(AccountConfig.__dataclass_fields__)
    assert all(m["help"] for m in boot["account_field_meta"])
    assert boot["account"]["equity"] == 50_000.0


def test_a_ticket_with_no_measured_history_is_vetoed(desk):
    """The honest default. Nothing in this library is live-eligible."""
    out = api.preview({"symbol": "MCL", "direction": "LONG", "entry": 78.50,
                       "stop": 77.60, "targets": [80.30], "confidence": 0.7,
                       "atr": 1.85, "atr_median": 1.80}, p=desk)
    assert out["assessment"]["approved"] is False
    assert any("no measured history" in v for v in out["assessment"]["vetoes"])
    # ...but the discretionary block still sizes it, under its own label.
    assert out["discretionary"]["contracts"] >= 1
    assert "NOT an approval" in out["discretionary"]["label"]


def test_discretionary_sizing_is_never_reported_as_approval(desk):
    # A 10-point MGC stop is $100 per contract, inside the default $240 budget,
    # so the discretionary block has something to size. A 30-point stop would be
    # $300 and size to zero, which would test nothing.
    out = api.preview({"symbol": "MGC", "direction": "LONG", "entry": 2650.0,
                       "stop": 2640.0, "targets": [2680.0], "confidence": 0.7,
                       "atr": 28.0, "atr_median": 28.0}, p=desk)
    assert out["assessment"]["approved"] is False
    assert out["assessment"]["dollar_risk"] == 0.0
    assert out["discretionary"]["dollar_risk"] > 0


def test_stop_ladder_flags_the_half_atr_floor(desk):
    out = api.preview({"symbol": "MCL", "direction": "LONG", "entry": 78.50,
                       "stop": 78.30, "targets": [79.00], "atr": 1.85}, p=desk)
    flagged = [r for r in out["stop_ladder"] if r["below_atr_floor"]]
    assert flagged and all(r["atr_multiple"] < 0.5 for r in flagged)
    assert out["guards"]["below_atr_half_floor"] is True


def test_loss_walk_terminates_at_a_hard_stop(desk):
    out = api.preview({"symbol": "MCL", "direction": "LONG", "entry": 78.50,
                       "stop": 77.60, "targets": [80.30], "atr": 1.85}, p=desk)
    walk = out["loss_walk"]
    assert walk, "a sizeable ticket must project its losses"
    assert walk[-1]["terminal"] is True
    # Modes only ever get stricter as losses accumulate.
    assert walk[0]["equity"] > walk[-1]["equity"]


def test_config_update_is_rejected_whole_or_applied_whole(desk):
    before = api.bootstrap(p=desk)["account_config"]["daily_loss_limit"]
    with pytest.raises(api.ApiError) as exc:
        api.update_config({"account": {"daily_soft_loss_limit": 99_999}}, p=desk)
    assert exc.value.status == 422
    after = api.bootstrap(p=desk)["account_config"]["daily_loss_limit"]
    assert after == before, "a rejected update must change nothing"


def test_correlated_exposure_is_refused(desk):
    """Two positions inside one configured correlation group is refused."""
    api.add_position({"symbol": "MES", "direction": "LONG", "contracts": 1,
                      "entry": 5800.0, "stop": 5780.0}, p=desk)
    # MYM shares MES's US_EQUITY_BROAD group.
    out = api.preview({"symbol": "MYM", "direction": "LONG", "entry": 44000.0,
                       "stop": 43700.0, "targets": [44600.0], "confidence": 0.7,
                       "atr": 330.0, "atr_median": 330.0}, p=desk)
    assert any("US_EQUITY_BROAD" in v for v in out["assessment"]["vetoes"])


def test_the_index_complex_gap_is_surfaced_even_though_the_engine_allows_it(desk):
    """MNQ + MES: the brief calls it one complex, the engine calls it two groups.

    ``CALLOUT.md`` states that MES/MNQ/NQ/ES are one index complex, but
    ``config.py`` assigns MNQ to US_EQUITY_TECH and MES to US_EQUITY_BROAD, so
    the correlation limit does not fire. This test pins BOTH halves: that the
    engine really does permit it, and that the console says so anyway. If the
    correlation groups are ever reconciled, this test fails loudly rather than
    letting the advisory quietly become a lie.
    """
    api.add_position({"symbol": "MES", "direction": "LONG", "contracts": 1,
                      "entry": 5800.0, "stop": 5780.0}, p=desk)
    out = api.preview({"symbol": "MNQ", "direction": "LONG", "entry": 20000.0,
                       "stop": 19880.0, "targets": [20240.0], "confidence": 0.7,
                       "atr": 120.0, "atr_median": 120.0}, p=desk)

    assert not any("correlation" in v.lower() for v in out["assessment"]["vetoes"]), \
        "engine behaviour changed - reconcile the advisory in api.INDEX_COMPLEX"
    advisory = out["guards"]["index_complex"]
    assert advisory is not None
    assert advisory["engine_blocks"] is False
    assert "MES" in advisory["open_in_complex"]
    assert "one index complex" in advisory["note"]


def test_closing_a_position_moves_equity_and_the_day_ledger(desk):
    api.add_position({"symbol": "MGC", "direction": "LONG", "contracts": 1,
                      "entry": 2650.0, "stop": 2640.0}, p=desk)
    out = api.close_position({"symbol": "MGC", "pnl": -100.0}, p=desk)
    assert out["account"]["equity"] == 49_900.0
    assert out["account"]["day"]["realised_pnl"] == -100.0
    assert out["account"]["day"]["consecutive_losses"] == 1


# ------------------------------------------------------------ guidance

def test_symbols_without_a_framework_say_so_explicitly():
    """A blank verdict reads as approval, so there are no blank verdicts."""
    for sym in ("MES", "MNQ"):
        fw = guidance.framework_for(sym)
        assert fw.status == "none"
        assert "placebo" in fw.note or "NO TRADE" in fw.note
    unknown = guidance.framework_for("ZZZ")
    assert unknown.status == "none" and unknown.note


def test_mgc_is_offered_at_60m_only():
    """MGC at 240m is worse than its own placebo, so it is not on the list."""
    assert guidance.framework_for("MGC").timeframes == (60,)
    assert guidance.framework_for("MCL").timeframes == (60, 240)


def test_the_median_stop_cost_matches_the_contract_specs():
    """The brief's headline risk figure, recomputed rather than quoted.

    ``CALLOUT.md`` states that a median trade in the FULL-SIZE index contracts
    risks about $2,700 - 5.4% of a $50,000 account per contract - and that the
    micros are the reason to prefer micros. This pins the arithmetic so the
    claim cannot drift away from the specs it is derived from.
    """
    nq = get_contract("NQ")
    mnq = get_contract("MNQ")
    assert nq.typical_atr_points == mnq.typical_atr_points
    # One ATR on the full-size contract is an order of magnitude more money.
    assert nq.point_value == 10 * mnq.point_value
    full = 135.0 * nq.point_value          # a median structural stop
    assert full == pytest.approx(2_700.0)
    assert full / 50_000 == pytest.approx(0.054)
    micro = 135.0 * mnq.point_value
    assert micro == pytest.approx(270.0)
    assert micro / 50_000 == pytest.approx(0.0054)


# -------------------------------------------------------------- server

@pytest.fixture()
def server(desk):
    srv = build_server(port=0, project=desk)
    srv.start_background()
    yield srv
    srv.shutdown()


def _get(url, token=None, origin=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("X-Desk-Token", token)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def test_api_requires_the_session_token(server):
    assert _get(server.base_url + "/api/bootstrap")[0] == 401
    assert _get(server.base_url + "/api/bootstrap", token=server.token)[0] == 200


def test_api_refuses_a_cross_site_origin(server):
    """A page in another tab must not be able to rewrite the risk config."""
    code, _ = _get(server.base_url + "/api/bootstrap", token=server.token,
                   origin="https://example.invalid")
    assert code == 403


def test_static_serving_cannot_escape_the_assets_directory(server):
    for path in ("/../../etc/passwd", "/..%2f..%2fetc%2fpasswd", "/./../setup.py"):
        code, _ = _get(server.base_url + path)
        assert code in (400, 403, 404), f"{path} returned {code}"


def test_oversized_bodies_are_refused(server):
    req = urllib.request.Request(server.base_url + "/api/preview",
                                 data=b"x" * 10, method="POST")
    req.add_header("X-Desk-Token", server.token)
    req.add_header("Content-Length", str(10 * 1024 * 1024))
    try:
        urllib.request.urlopen(req, timeout=10)
        pytest.fail("should have been refused")
    except urllib.error.HTTPError as e:
        assert e.code == 413
    except (urllib.error.URLError, OSError):
        pass    # the server closed the connection, which is also a refusal


def test_the_ui_is_served_from_the_project_folder_when_present(server, desk):
    (desk.root / "assets").mkdir(exist_ok=True)
    (desk.root / "assets" / "app.html").write_text("<h1>local copy</h1>")
    with urllib.request.urlopen(server.base_url + "/", timeout=10) as r:
        assert b"local copy" in r.read()
