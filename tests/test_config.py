"""Machine settings are found, not hard-coded: Java prefs parsing and the install search order."""

from image2cash import config


def test_workspace_prefs_are_read_like_java_properties(tmp_path):
    prefs = tmp_path / "x.prefs"
    prefs.write_text(
        "#comment\n"
        r"GENERAL_WORKSPACE=D\:\\Fakturama_Test_Working" + "\n"
        r"jakarta.persistence.jdbc.url=jdbc\:hsqldb\:hsql\://localhost\:9001/fakdbneu" + "\n",
        encoding="latin-1",
    )
    p = config.read_prefs(prefs)
    assert p["GENERAL_WORKSPACE"] == r"D:\Fakturama_Test_Working"
    assert p["jakarta.persistence.jdbc.url"] == "jdbc:hsqldb:hsql://localhost:9001/fakdbneu"
    assert config.read_prefs(tmp_path / "missing.prefs") == {}


def test_install_search_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
    (b / "Fakturama.exe").write_text("")
    assert config.find_home(str(a), [b]) == a  # the setting always wins
    assert config.find_home(None, [lambda: None, a, b / "Fakturama.exe"]) == b  # first folder with the exe
    assert config.find_home(None, [a, lambda: None]) is None


def test_window_title_ignores_case_and_a_trailing_slash():
    import re
    from pathlib import Path

    from image2cash.ui.core import main_title

    pat = main_title(Path("F:/test"))
    for title in (r"Fakturama - F:\test", r"Fakturama - f:\TEST", "Fakturama - F:\\test\\"):
        assert re.match(pat, title), title
    for title in (r"Fakturama - F:\test2", r"Fakturama - F:\other", r"Fakturama - D:\Fakturama_Working_Directory"):
        assert not re.match(pat, title), title  # never another workspace's window
    assert not re.match(main_title(None), r"Fakturama - F:\test")


def test_a_new_workspace_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    """The hang on a friend's PC: the app started before Fakturama first ran on the workspace."""
    monkeypatch.delenv("FAKTURAMA_WORKDIR", raising=False)
    monkeypatch.setattr(config, "PREFS_FILE", tmp_path / "fakturama.prefs")
    assert config.workdir() is None  # Fakturama has not run on it yet
    config.PREFS_FILE.write_text("GENERAL_WORKSPACE=F" + "\\" + ":" + "\\" + "\\" + "test\n", encoding="latin-1")
    assert str(config.workdir()) == "F:" + "\\" + "test"  # Fakturama asked, the user chose F:\test
