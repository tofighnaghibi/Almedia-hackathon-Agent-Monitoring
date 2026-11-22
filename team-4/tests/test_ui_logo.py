"""The brand icon of the main console: the radar sweep the owner chose out of five versions on 22.11, drawn only in the
theme's colours, and the same drawing on the browser tab."""
import re

import pytest
from fastapi.testclient import TestClient

import ui.app as ui


@pytest.fixture
def client():
    return TestClient(ui.app)


def _page_and_script(client) -> tuple[str, str]:
    html = client.get("/").text
    src = re.search(r'<script src="(/static/v3/app\.js\?v=\d+)"></script>', html).group(1)
    return html, client.get(src).text


def _mark(html: str) -> str:
    """The inside of the header's icon."""
    return re.search(r'<div class="brand">\s*<svg class="mark" viewBox="0 0 24 24" aria-hidden="true">(.*?)</svg>',
                     html, re.S).group(1)


def test_the_header_icon_is_the_radar_sweep_with_one_accent_blip(client):
    html, _ = _page_and_script(client)
    mark = _mark(html)
    assert re.search(r'<circle cx="12" cy="12" r="9\.25" fill="none" stroke="currentColor"', mark)   # the scope
    assert mark.count('class="acc"') == 1                                                         # the blip
    assert "Eye of the System</h1>" in html


def test_the_icon_is_drawn_only_in_the_theme_colours(client):
    html, _ = _page_and_script(client)
    mark = _mark(html)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgb\(|\bid=|style=|url\(|<text", mark)
    for colour in re.findall(r'(?:fill|stroke)="([^"]+)"', mark):
        assert colour in ("currentColor", "none"), colour
    css = client.get("/static/v3/style.css").text
    assert ".mark .acc { fill: var(--iris); }" in css


def test_the_tab_icon_is_the_same_drawing_and_follows_the_theme(client):
    html, js = _page_and_script(client)
    assert '<link rel="icon" id="favicon"' in html
    assert "mark.innerHTML.replace(/currentColor/g, ink)" in js and "link.href = 'data:image/svg+xml,'" in js
    assert "showLogo();      // the tab's icon takes the new theme's colours" in js
    assert "nextLogo" not in js and "v3-logo" not in js                  # the choice is made: no switch left
