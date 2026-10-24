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


def test_the_value_under_a_misspelled_key_is_never_kept(tmp_path):
    import json

    raw = make_config(tmp_path).model_dump(mode="json")
    raw["backends"]["cloud"]["api_kye"] = "sk-not-a-real-secret-123"
    raw["mechanisms"]["compactoin"] = "sk-not-a-real-secret-456"
    cfg = config_from_raw(raw)
    dumped = json.dumps(cfg.model_dump(mode="json"), default=str)
    assert "sk-not-a-real-secret" not in dumped, "only the names of unknown keys are kept"
    assert "backends.cloud.api_kye" in cfg.profile_provenance["ignored_keys"]
