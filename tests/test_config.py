from pathlib import Path

from app.config import Settings


def test_explicit_project_root_resolves_packaged_runtime_resources(tmp_path):
    settings = Settings(
        app_project_root=str(tmp_path / "runtime-root" / ".." / "runtime-root"),
        app_database_path="var/runtime.db",
        eval_dataset_path="evals/golden.v7.jsonl",
    )

    assert settings.project_root == (tmp_path / "runtime-root").resolve()
    assert settings.resolved_app_database_path == settings.project_root / "var/runtime.db"
    assert settings.resolved_eval_dataset_path == settings.project_root / "evals/golden.v7.jsonl"


def test_default_project_root_still_points_to_source_checkout():
    assert (Settings().project_root / "skills").is_dir()
    assert (Settings().project_root / "evals").is_dir()
    assert Settings().project_root == Path(__file__).resolve().parents[1]
