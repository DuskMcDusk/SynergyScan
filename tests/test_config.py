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


def test_a_config_saved_with_a_bom_still_loads():
    """Notepad and PowerShell 5.1 can save config.json with a BOM."""
    paths.config_path().write_bytes(b'\xef\xbb\xbf{"allow_lan": true, "port": 8123}')
    s = config.load()
    assert (s.allow_lan, s.port) == (True, 8123)


def test_set_value_changes_one_key_and_keeps_the_rest():
    paths.config_path().write_bytes(b'\xef\xbb\xbf{"port": 8123, "from_newer": 1}')
    config.set_value("channel_url", "https://example.invalid/beta.json")
    raw = paths.config_path().read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")              # written as plain UTF-8
    data = json.loads(raw)
    assert data == {"port": 8123, "from_newer": 1,
                    "channel_url": "https://example.invalid/beta.json"}
    assert "auto_update" not in data                           # no defaults pinned
    config.set_value("channel_url", None)
    assert "channel_url" not in json.loads(paths.config_path().read_text(encoding="utf-8"))


def test_set_value_creates_the_starter_file_first():
    config.set_value("channel_url", "https://example.invalid/beta.json")
    data = json.loads(paths.config_path().read_text(encoding="utf-8"))
    assert data["port"] == 8000 and data["channel_url"].endswith("beta.json")


def test_the_installer_command_sets_and_unsets_the_channel(capsys):
    assert config.main(["set", "channel_url", "https://example.invalid/beta.json"]) == 0
    assert config.load().channel_url == "https://example.invalid/beta.json"
    assert config.main(["unset", "channel_url"]) == 0
    assert config.load().channel_url == config.DEFAULT_CHANNEL_URL
    paths.config_path().write_text("{not json", encoding="utf-8")
    assert config.main(["set", "channel_url", "x"]) == 1      # reported, not a traceback
    assert "could not update" in capsys.readouterr().out
