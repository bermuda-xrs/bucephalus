from __future__ import annotations

from conftest import make_config

from agent.config import config_from_raw


def test_a_misspelled_key_is_named_not_silently_dropped(tmp_path, capsys):
    raw = make_config(tmp_path).model_dump(mode="json")
    raw["mechanisms"]["compactoin"] = False
    raw["backends"]["cloud"]["carry_feild"] = True
    raw["mechanisim"] = {"x": 1}
    cfg = config_from_raw(raw)
    named = cfg.profile_provenance["ignored_keys"]
    assert set(named) == {"mechanisms.compactoin", "backends.cloud.carry_feild", "mechanisim"}
    assert "compactoin" in capsys.readouterr().err


def test_a_clean_config_names_nothing(tmp_path, capsys):
    cfg = config_from_raw(make_config(tmp_path).model_dump(mode="json"))
    assert cfg.profile_provenance["ignored_keys"] == [] and "ignored" not in capsys.readouterr().err
