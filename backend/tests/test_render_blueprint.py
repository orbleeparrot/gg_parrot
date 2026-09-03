from pathlib import Path


def test_render_blueprint_includes_position_news_background_worker():
    blueprint = (
        Path(__file__).resolve().parents[2] / "render.yaml"
    ).read_text(encoding="utf-8")

    assert "  - type: worker\n" in blueprint
    web = blueprint.split("  - type: web\n", 1)[1].split("  - type: worker\n", 1)[0]
    worker = blueprint.split("  - type: worker\n", 1)[1]

    assert '- key: ANTHROPIC_NEWS_TRANSLATION_API_KEY\n        sync: false' in web
    assert (
        '- key: ANTHROPIC_NEWS_TRANSLATION_MODEL\n'
        '        value: "claude-haiku-4-5"'
        in web
    )
    assert '- key: ANTHROPIC_API_KEY\n' not in web
    assert '- key: ANTHROPIC_MODEL\n' not in web
    assert '- key: NEWS_TITLE_TRANSLATION_ENABLED\n        value: "true"' in web
    assert (
        '- key: NEWS_TITLE_TRANSLATION_MAX_CALLS_PER_DAY\n        value: "20"'
        in web
    )
    assert (
        '- key: ANTHROPIC_NEWS_TRANSLATION_MAX_TOKENS\n        value: "1024"'
        in web
    )

    assert "name: gg-parrot-position-news" in worker
    assert "runtime: docker" in worker
    assert "dockerfilePath: backend/Dockerfile.prefect" in worker
    assert "dockerContext: backend" in worker
    assert "python -m app.workflows.position_news serve" in worker
    assert "maxShutdownDelaySeconds: 300" in worker
    assert "healthCheckPath:" not in worker
    assert 'value: "claude-haiku-4-5"' in worker
    assert 'value: "2"' in worker
    assert 'value: "10"' in worker
    assert 'value: "256"' in worker
    assert (
        '- key: POSITION_NEWS_COLLECTION_SECONDS\n        value: "60"'
        in worker
    )

    for key in ("DATABASE_URL", "PREFECT_API_URL", "PREFECT_API_KEY"):
        assert f"- key: {key}\n        sync: false" in worker

    dockerfile = (
        Path(__file__).resolve().parents[1] / "Dockerfile.prefect"
    ).read_text(encoding="utf-8")
    requirements = (
        Path(__file__).resolve().parents[1] / "requirements.txt"
    ).read_text(encoding="utf-8")

    assert "mcr.microsoft.com/playwright/python:v1.62.0-noble" in dockerfile
    assert "playwright==1.62.0" in requirements


def test_prefect_serve_pauses_schedule_when_worker_stops():
    workflow = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "workflows"
        / "position_news.py"
    ).read_text(encoding="utf-8")

    assert "pause_on_shutdown=True" in workflow
    assert "paused=False" in workflow
    assert "pause_on_shutdown=False" not in workflow
