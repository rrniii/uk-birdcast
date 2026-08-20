from __future__ import annotations

from pathlib import Path

STATIC_ROOT = Path(__file__).parents[1] / "src" / "birdcast_uk" / "static_coastal"


def static_text(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def test_coastal_frontend_discards_stale_day_loads() -> None:
    app = static_text("app.js")
    selection = app[
        app.index("async function selectAvailableDate") : app.index("function configure")
    ]

    assert "new AbortController()" in app
    assert "generation: ++state.loadGeneration" in app
    assert "fetchDay(date, request.controller.signal)" in selection
    assert selection.index("if (!requestIsCurrent(request))") < selection.index(
        "commitDay(date, day, edge)"
    )
    assert 'return "cancelled"' in selection


def test_coastal_frontend_uses_sparse_dates_with_range_fallback() -> None:
    app = static_text("app.js")
    published = app[app.index("function publishedDates") : app.index("async function fetchDay")]

    assert "Array.isArray(state.manifest && state.manifest.available_dates)" in published
    assert "[...new Set(declared)]" in published
    assert "timestamp <= end" in published
    assert "timestamp += 86400000" in published
    assert "dates.filter((date) => date > state.date)" in app
    assert "dates.filter((date) => date < state.date).reverse()" in app
    assert "if (!day) continue;" in app


def test_coastal_frontend_playback_is_serial_and_stops_at_archive_bound() -> None:
    app = static_text("app.js")
    playback = app[app.index("function play()") : app.index("function updatePlayButton")]

    assert "setInterval" not in app
    assert "const result = await move(1);" in playback
    assert playback.index("await move(1)") < playback.index(
        "state.timer = setTimeout(() => playTick(generation), 650);",
        playback.index("async function playTick"),
    )
    assert 'if (result !== "selected")' in playback
    assert (
        'if (result === "none") announce("Playback stopped at the last published hour")' in playback
    )
    assert "generation !== state.animationGeneration" in playback


def test_coastal_frontend_reports_loading_and_accessible_play_state() -> None:
    app = static_text("app.js")
    html = static_text("index.html")
    styles = static_text("styles.css")

    assert 'canvas.setAttribute("aria-busy", String(loading))' in app
    assert 'state.playing ? "Pause" : "Play"' in app
    assert 'button.setAttribute("aria-label", button.title)' in app
    assert 'button.setAttribute("aria-pressed", String(state.playing))' in app
    assert 'id="mapStatus"' in html
    assert 'role="status" aria-live="polite" aria-atomic="true"' in html
    assert 'aria-label="Play hourly animation" aria-pressed="false"' in html
    assert 'aria-describedby="mapStatus" aria-busy="false"' in html
    assert ".visually-hidden" in styles


def test_coastal_frontend_keeps_relative_research_contract_and_normal_scroll() -> None:
    app = static_text("app.js")
    html = static_text("index.html")

    assert "within-radar activity percentile" in html
    assert "no absolute MTR claim" in html
    assert "if (!event.ctrlKey && !event.metaKey) return;" in app
    assert "touch-action:pan-y" in static_text("styles.css")
