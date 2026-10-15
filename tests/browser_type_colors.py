"""Optional real-browser check for type-edge CSS, markers and highlighting.

Run with: uv run --with playwright==1.64.0 python tests/browser_type_colors.py
"""

import argparse
import json
from pathlib import Path

from kaitai_svg.model import Edge, Graph, Node, Row
from kaitai_svg.palette import TYPE_EDGE_COLORS
from kaitai_svg.render import render_svg


ROOT = Path(__file__).resolve().parents[1]


def check(browser_type, output, expect):
    output.mkdir(parents=True, exist_ok=True)
    rows = [Row(f"field_{index:02}", f"field_{index:02}", "body") for index in range(13)]
    graph = Graph("Type connection colors", [
        Node("root", "root", rows=rows, source_file="colors.ksy"),
        Node("body", "body", source_file="colors.ksy"),
    ], [Edge("root", "body", row.id, row.label) for row in rows])
    browser = browser_type.launch()
    errors = []
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 1050}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        for interactive in (False, True):
            source = output / ("interactive.svg" if interactive else "static.svg")
            source.write_text(render_svg(graph, color_type_edges=True, interactive=interactive), encoding="utf-8")
            page.goto(source.as_uri(), wait_until="load")
            edges = page.locator(".edge-kind-type")
            expect(edges).to_have_count(13)
            def colors():
                return edges.evaluate_all("""edges => edges.map(edge => {
                  const path = edge.querySelector('.edge-path');
                  const marker = document.querySelector(path.getAttribute('marker-end').slice(4, -1));
                  return {stroke: getComputedStyle(path).stroke,
                          port: getComputedStyle(edge.querySelector('.port')).fill,
                          arrow: getComputedStyle(marker.querySelector('path')).fill};
                })""")
            actual = colors()
            expected = ["rgb({}, {}, {})".format(*(int(color[i:i + 2], 16) for i in (1, 3, 5)))
                        for color in TYPE_EDGE_COLORS]
            if [item["stroke"] for item in actual] != expected + expected[:1]:
                raise AssertionError(f"Type edge colors differ from the palette: {actual}")
            if not all(item["stroke"] == item["port"] == item["arrow"] for item in actual):
                raise AssertionError(f"Type edge stroke, port and arrow colors differ: {actual}")
            if interactive:
                edges.first.focus()
                expect(edges.first).to_have_class("edge edge-kind-type type-edge-color-0 active")
                if edges.first.locator(".edge-path").evaluate("p => getComputedStyle(p).strokeWidth") != "3px":
                    raise AssertionError("Focused type edge did not become thicker")
                if colors() != actual:
                    raise AssertionError("Focusing the first type edge changed its colors")
                expect(page.locator("#tooltip")).to_be_visible()
                edges.nth(1).focus()
                if colors() != actual:
                    raise AssertionError("Focusing the second type edge changed its colors")
                page.keyboard.press("Escape")
            else:
                expect(page.locator("script,[tabindex],#controls")).to_have_count(0)
            page.screenshot(path=str(source.with_suffix(".png")))
        if errors:
            raise AssertionError(f"Browser script errors: {errors}")
        return {"browser": browser_type.name, "version": browser.version, "status": "passed",
                "static_and_interactive": True, "unique_colors": 12, "thirteenth_reuses_first": True,
                "stroke_port_arrow_match": True, "focus_preserves_colors": True}
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chromium", "firefox"), default="chromium")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".scratch" / "browser-qa" / "type-colors")
    args = parser.parse_args()
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        parser.error("Install the optional browser checker with uv run --with playwright==1.64.0")
    output = (args.output_dir / args.browser).resolve()
    with sync_playwright() as playwright:
        result = check(getattr(playwright, args.browser), output, expect)
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
