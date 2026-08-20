from __future__ import annotations

from pathlib import Path

STATIC_ROOT = Path(__file__).parents[1] / "src" / "birdcast_uk" / "static_europe"


def static_text(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def test_europe_frontend_discards_stale_day_loads_and_skips_sparse_dates() -> None:
    app = static_text("app.js")

    selection = app[
        app.index("async function selectAvailableDate") : app.index("function configure")
    ]
    assert "new AbortController()" in app
    assert "generation:++state.loadGeneration" in app
    assert selection.index("if(!requestIsCurrent(request))") < selection.index(
        "commitDay(date,day,edge)"
    )
    assert "Array.isArray(state.manifest.available_dates)" in app
    assert "dates.filter(date=>date>state.date)" in app
    assert "dates.filter(date=>date<state.date).reverse()" in app
    assert "if(!day)continue" in selection


def test_europe_frontend_playback_stops_and_has_accessible_state_labels() -> None:
    app = static_text("app.js")
    html = static_text("index.html")

    assert "setInterval" not in app
    assert 'if(result!=="selected")' in app
    assert 'state.playing?"Pause":"Play"' in app
    assert 'button.setAttribute("aria-pressed",String(state.playing))' in app
    assert 'aria-label="Play hourly animation"' in html
    assert 'role="status" aria-live="polite"' in html


def test_europe_frontend_does_not_present_ambiguous_uncertainty() -> None:
    app = static_text("app.js")
    html = static_text("index.html")

    assert "uncertaintyToggle" not in html
    assert "state.uncertainty" not in app
    assert "f.uncertainty" not in app
    assert ">Model support</span>" in html
    assert "Historical cross-network reanalysis" in html
    assert "Research preview" in html
