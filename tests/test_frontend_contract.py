from __future__ import annotations

from pathlib import Path

STATIC_ROOT = Path(__file__).parents[1] / "src" / "birdcast_uk" / "static"


def static_text(name: str) -> str:
    return (STATIC_ROOT / name).read_text(encoding="utf-8")


def test_frontend_ignores_blank_metrics_and_uses_metric_specific_weights() -> None:
    app = static_text("app.js")
    crow = static_text("crow-radar-detail.js")

    assert 'typeof value === "string" && !value.trim()' in app
    assert "const parsed = Number(value);" in app
    assert "const value = finiteNumber(row[metric]);" in app
    assert "vid: row._vidCount ? row._vidTotal : null" in app
    assert "height_m: row._heightProfiles ? row._heightTotal / row._heightProfiles : null" in app
    assert "speed_ms: row._speedProfiles ? row._speedTotal / row._speedProfiles : null" in app
    assert "_weightedProfiles" not in app
    assert 'typeof value === "string" && !value.trim()' in crow


def test_frontend_discards_stale_loads_and_stops_at_sparse_archive_edges() -> None:
    app = static_text("app.js")

    model_load = app[
        app.index('if (request.view === "modelled")') : app.index("function configureControls()")
    ]
    assert "generation: ++state.loadGeneration" in app
    assert "request.generation === state.loadGeneration" in app
    assert model_load.index("if (!loadIsCurrent(request)) return false;") < model_load.index(
        "state.modelDayPayload = modelDayPayload;"
    )
    assert "state.modelDayKey === modelDayKey()" in app
    published_date = app[
        app.index("async function selectPublishedModelDate") : app.index("async function stepHour")
    ]
    assert published_date.index("if (!hours.length) continue;") < published_date.index(
        "state.date = date;"
    )
    assert "selectPublishedModelDate(availableModelDates(), 1)" in app
    assert "dates.filter((date) => date > state.date)" in app
    assert "dates.filter((date) => date < state.date).reverse()" in app
    assert "(dateIndex + direction + dates.length) % dates.length" not in app
    assert "const adjacentHour = direction > 0" in app
    assert "if (!advanced) return stopAnimation();" in app
    assert "generation !== state.animationGeneration" in app
    assert 'state.showArrows && state.pulse === "sp"' in app
    assert "if (!event.ctrlKey && !event.metaKey) return;" in app


def test_empty_and_dynamic_frontend_states_are_exposed_accessibly() -> None:
    app = static_text("app.js")
    html = static_text("index.html")
    styles = static_text("styles.css")

    unavailable = app[
        app.index("if (!state.historical && !state.model && !state.europe)") : app.index(
            "if (!state.historical) state.view"
        )
    ]
    assert "configureControls()" not in unavailable
    assert "setDataControlsDisabled(true)" in unavailable
    assert "updatePressedButtons" in app
    assert 'button.setAttribute("aria-pressed", String(active))' in app
    assert 'button.setAttribute("aria-label", button.title)' in app
    assert 'playing ? "Pause hourly animation" : "Play hourly animation"' in app
    assert "European relative activity research product" in app
    assert 'if (!frame.getAttribute("src"))' in app
    assert (
        'mapTitle").textContent = relative ? "European relative bird activity and observed flow"'
        in app
    )
    assert 'id="statusBadge" class="badge" role="status" aria-live="polite"' in html
    assert 'id="mapSummary" class="visually-hidden" role="status" aria-live="polite"' in html
    assert 'aria-describedby="mapSummary"' in html
    assert 'aria-label="European relative bird activity and observed flow"' in html
    assert 'title="European relative bird activity and observed flow"' in html
    assert 'id="supportToggle"' in html
    assert "this is not calibrated prediction uncertainty" in html
    assert "showUncertainty" not in app
    assert "Observation record available" in html
    assert ">Unavailable</span>" not in html
    assert 'button.classList.remove("active")' in app
    assert "across ${values.length} radar" in app
    assert "const parsed = Object.fromEntries" in app
    assert "longitude === null || latitude === null" in app
    assert "button:focus-visible" in styles
    assert "touch-action: pan-y" in styles


def test_crow_detail_uses_safe_dynamic_text_and_cancels_superseded_fetches() -> None:
    crow = static_text("crow-radar-detail.js")

    assert '<div class="title">${label}' not in crow
    assert '<div class="status">${message}' not in crow
    assert 'querySelector(".title").textContent' in crow
    assert 'status.textContent = String(message || "")' in crow
    assert "option.textContent =" in crow
    assert 'role="status" aria-live="polite"' in crow
    assert 'button.setAttribute("aria-pressed", String(active))' in crow
    assert "queueMicrotask" in crow
    assert "new AbortController()" in crow
    assert "fetch(urlFor(template, radar" in crow
    assert "{signal}" in crow
    assert 'if (error.name === "AbortError") return' in crow
    assert "const hasProfiles = Boolean(this.data && this.data.profiles.length)" in crow
    assert "No ten-minute profiles are available" in crow
    assert "return values.length ? values.reduce" in crow
    assert "return pairs.length ? pairs.reduce" in crow
