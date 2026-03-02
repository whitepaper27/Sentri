"""Test the sentri init CLI command."""

from click.testing import CliRunner

from sentri.cli.main import cli


def test_init_creates_structure(tmp_path, monkeypatch):
    """Test that 'sentri init' creates the directory structure."""
    import sentri.config.initializer as initializer
    import sentri.config.paths as paths

    sentri_home = tmp_path / ".sentri"
    dirs = [
        sentri_home,
        sentri_home / "brain",
        sentri_home / "agents",
        sentri_home / "alerts",
        sentri_home / "environments",
        sentri_home / "workflows",
        sentri_home / "data",
        sentri_home / "logs",
        sentri_home / "config",
    ]

    # Patch both paths module and initializer's own imported references
    for mod in (paths, initializer):
        monkeypatch.setattr(mod, "SENTRI_HOME", sentri_home)
        monkeypatch.setattr(mod, "DB_PATH", sentri_home / "data" / "sentri.db")
        monkeypatch.setattr(mod, "CONFIG_PATH", sentri_home / "config" / "sentri.yaml")
        monkeypatch.setattr(mod, "ALL_DIRS", dirs)

    runner = CliRunner()
    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert sentri_home.exists()
    assert (sentri_home / "data" / "sentri.db").exists()
    assert (sentri_home / "config" / "sentri.yaml").exists()
