import json

from synergyscan import config, paths


def test_ensure_file_creates_editable_starter_config():
    assert not paths.config_path().exists()
    config.ensure_file()
    data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    assert data == {"site_name": "SynergyScan", "allow_lan": False, "port": 8000}
    assert "channel_url" not in data      # defaults must stay changeable by a release


def test_ensure_file_never_overwrites():
    paths.config_path().write_text('{"allow_lan": true}', encoding="utf-8")
    config.ensure_file()
    assert config.load().allow_lan is True
