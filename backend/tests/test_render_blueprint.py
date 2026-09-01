from pathlib import Path


def test_render_blueprint_includes_position_news_background_worker():
    blueprint = (
        Path(__file__).resolve().parents[2] / "render.yaml"
    ).read_text(encoding="utf-8")

    assert "  - type: worker\n" in blueprint
    worker = blueprint.split("  - type: worker\n", 1)[1]

    assert "name: gg-parrot-position-news" in worker
    assert "rootDir: backend" in worker
    assert "pip install -r requirements-prefect.txt" in worker
    assert "python -m app.workflows.position_news serve" in worker
    assert "maxShutdownDelaySeconds: 300" in worker
    assert "healthCheckPath:" not in worker

    for key in ("DATABASE_URL", "PREFECT_API_URL", "PREFECT_API_KEY"):
        assert f"- key: {key}\n        sync: false" in worker
