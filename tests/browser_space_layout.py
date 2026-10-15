"""Optional headless browser QA for adaptive card widths and spacing.

Run: uv run --with playwright==1.64.0 python tests/browser_space_layout.py
Requires the selected Playwright browsers to be installed. Generated fixtures,
screenshots and JSON reports stay under --output-dir; existing browser profiles
and the shipped schemas are never modified.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform

from browser_check import assert_file_groups, assert_technical_fonts, assert_text_in_cards
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Graph, Node, Row
from kaitai_svg.render import render_svg


ROOT = Path(__file__).resolve().parents[1]


def typography_graph() -> Graph:
    """Exercise every text role, including narrow enum cells and file titles."""
    source = "schemas/" + "긴_파일명_한글_" * 5 + "typography.ksy"
    nodes = []
    for index, content in enumerate(("WMwm@_wide_identifier_" * 9, "한글漢字넓은문자_" * 14)):
        nodes.append(Node(
            f"type-{index}", content, subtitle=content, doc=f"Type documentation {index}",
            rows=[Row("field", content, content, details=content,
                      doc=f"Field documentation {index}: " + content,
                      pos_label=content, size_label=content)],
            source_file=source,
        ))
    nodes.append(Node(
        "enum", "상태_열거형_" * 10, kind="enum", source_file=source,
        rows=[Row(f"enum-{index}", "WMwm@_한글_열거형_" * 8,
                  str(123456789012345678901234567890 + index),
                  details="Enum value documentation " + "설명_" * 20,
                  doc="Enum documentation", kind="enum") for index in range(3)],
    ))
    return Graph("Adaptive Latin, CJK and enum typography", nodes, [], source)


def fixtures(output: Path, routers: tuple[str, ...]) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    sqlite = load_graph(ROOT / "examples" / "sqlite3.ksy")
    visible_edges = sum(edge.kind not in {"import", "contains"} for edge in sqlite.edges)
    if (len(sqlite.nodes), visible_edges) != (15, 24):
        raise AssertionError("Update the SQLite browser fixture's expected 15 definitions/24 references")
    result = []
    for router in routers:
        for graph_name, graph, widths in (("sqlite", sqlite, (None, 1200, 4000)),
                                          ("typography", typography_graph(), (680,))):
            for width in widths:
                for interactive in (False, True):
                    mode = "interactive" if interactive else "static"
                    name = f"{graph_name}-{router}-{width or 'auto'}-{mode}"
                    target = output / f"{name}.svg"
                    target.write_text(render_svg(
                        graph, layout="optimized-with-space", layout_width=width,
                        router=router, interactive=interactive,
                    ), encoding="utf-8")
                    result.append(dict(
                        name=name, path=target, width=width, interactive=interactive,
                        graph_name=graph_name, router=router, root_source_file=graph.root_source_file,
                        nodes=len(graph.nodes),
                        edges=sum(edge.kind not in {"import", "contains"} for edge in graph.edges),
                    ))
    return result


def assert_geometry(page, target: dict) -> dict:
    """Check emitted coordinates as well as real browser text bounds."""
    actual = page.locator("svg").evaluate(r"""(svg, rootSource) => {
      const inverse = svg.getScreenCTM().inverse();
      const rectangle = element => {
        const x = element.x.baseVal.value, y = element.y.baseVal.value;
        const width = element.width.baseVal.value, height = element.height.baseVal.value;
        const transform = inverse.multiply(element.getScreenCTM());
        const a = new DOMPoint(x, y).matrixTransform(transform);
        const b = new DOMPoint(x + width, y + height).matrixTransform(transform);
        return {left:a.x, top:a.y, right:b.x, bottom:b.y};
      };
      const cards = [...svg.querySelectorAll('.node-box')].map(rectangle);
      const panels = [...svg.querySelectorAll('.file-group-box')].map(rectangle);
      const view = svg.viewBox.baseVal, tolerance = .05, failures = [];
      const inside = box => box.left >= view.x - tolerance && box.top >= view.y - tolerance &&
        box.right <= view.x + view.width + tolerance && box.bottom <= view.y + view.height + tolerance;
      const overlaps = (a,b) => Math.min(a.right,b.right) - Math.max(a.left,b.left) > tolerance &&
        Math.min(a.bottom,b.bottom) - Math.max(a.top,b.top) > tolerance;
      for (const [kind, boxes] of [['card',cards],['panel',panels]]) {
        for (let i=0; i<boxes.length; i++) {
          if (!inside(boxes[i])) failures.push({kind,index:i,error:'outside canvas'});
          for (let j=i+1; j<boxes.length; j++)
            if (overlaps(boxes[i],boxes[j])) failures.push({kind,indices:[i,j],error:'overlap'});
        }
      }
      for (const [index,path] of [...svg.querySelectorAll('.edge-path')].entries()) {
        const values = path.getAttribute('d').match(/-?\d+(?:\.\d+)?(?:e[-+]?\d+)?/gi).map(Number);
        const points = [];
        for (let i=0; i<values.length; i+=2) points.push({x:values[i],y:values[i+1]});
        for (const point of points)
          if (!Number.isFinite(point.x) || !Number.isFinite(point.y) ||
              point.x < view.x-tolerance || point.x > view.x+view.width+tolerance ||
              point.y < view.y-tolerance || point.y > view.y+view.height+tolerance)
            failures.push({edge:index,point,error:'invalid canvas point'});
        for (let i=1; i<points.length; i++) {
          const a=points[i-1],b=points[i],vertical=Math.abs(a.x-b.x)<tolerance;
          if (!vertical && Math.abs(a.y-b.y)>tolerance)
            failures.push({edge:index,error:'nonorthogonal segment'});
          for (const box of cards) {
            const crosses = vertical
              ? a.x > box.left+tolerance && a.x < box.right-tolerance &&
                Math.min(Math.max(a.y,b.y),box.bottom)-Math.max(Math.min(a.y,b.y),box.top)>tolerance
              : a.y > box.top+tolerance && a.y < box.bottom-tolerance &&
                Math.min(Math.max(a.x,b.x),box.right)-Math.max(Math.min(a.x,b.x),box.left)>tolerance;
            if (crosses) failures.push({edge:index,error:'connector enters card'});
          }
        }
      }
      const rootColumns = new Set([...svg.querySelectorAll('.node')]
        .filter(node => node.dataset.sourceFile === rootSource)
        .map(node => rectangle(node.querySelector('.node-box')).left.toFixed(3))).size;
      return {nodes:cards.length,edges:svg.querySelectorAll('.edge').length,
        width:view.width,height:view.height,root_columns:rootColumns,failures};
    }""", target["root_source_file"])
    if actual["failures"]:
        raise AssertionError(f"Invalid adaptive SVG geometry: {actual['failures']}")
    if (actual["nodes"], actual["edges"]) != (target["nodes"], target["edges"]):
        raise AssertionError(f"Missing definitions/references: {actual}")
    if target["width"] is not None and actual["width"] > target["width"] + .05:
        raise AssertionError(f"SVG exceeds its width cap: {actual}")
    if target["graph_name"] == "sqlite" and target["width"] == 4000 and actual["root_columns"] < 3:
        raise AssertionError(f"Wide SQLite layout did not use at least three root-file columns: {actual}")
    # Inspect glyph padding at intrinsic scale. A tall interactive SVG can fit
    # thousands of units into 1000 device pixels; tiny hinted glyphs then have
    # device-pixel side bearings larger than the helper's padding tolerance.
    # Restore the emitted presentation before testing actual user interaction.
    svg = page.locator("svg")
    original_style = svg.get_attribute("style")
    svg.evaluate("""svg => {
      svg.style.width = svg.viewBox.baseVal.width + 'px';
      svg.style.height = svg.viewBox.baseVal.height + 'px';
    }""")
    try:
        assert_text_in_cards(page)
        assert_technical_fonts(page)
        assert_file_groups(page)
    finally:
        svg.evaluate("""(svg, style) => {
          if (style === null) svg.removeAttribute('style'); else svg.setAttribute('style',style);
          window.dispatchEvent(new Event('resize'));
        }""", original_style)
    return {key: value for key, value in actual.items() if key != "failures"}


def assert_interaction(page, expect) -> None:
    expect(page.locator("#controls")).to_be_visible()
    row = page.locator(".row[data-tooltip]").first
    if not row.count():
        raise AssertionError("Interactive fixture has no field documentation")
    tooltip = page.locator("#tooltip")
    page.mouse.move(0, 0)
    page.locator("svg").focus()
    page.keyboard.press("Escape")
    for _ in range(page.locator('[tabindex="0"]').count() + 1):
        page.keyboard.press("Tab")
        if row.evaluate("item => item === document.activeElement"):
            break
    expect(row).to_be_focused()
    expect(tooltip).to_be_visible()
    expected = row.get_attribute("data-tooltip")
    expect(page.locator(".tooltip-box")).to_have_text(expected)
    bounds = page.locator(".tooltip-box").bounding_box()
    viewport = page.viewport_size
    if (not bounds or bounds["x"] < -.5 or bounds["y"] < -.5 or
            bounds["x"] + bounds["width"] > viewport["width"] + .5 or
            bounds["y"] + bounds["height"] > viewport["height"] + .5):
        raise AssertionError(f"Keyboard tooltip exceeds the viewport: {bounds}")
    page.keyboard.press("Escape")
    expect(tooltip).to_be_hidden()
    original = page.locator("#viewport").get_attribute("transform")
    page.get_by_role("button", name="Zoom in", exact=True).click()
    if page.locator("#viewport").get_attribute("transform") == original:
        raise AssertionError("Adaptive layout zoom control did not change the viewport")
    page.get_by_role("button", name="Reset view (0)", exact=True).click()
    expect(page.locator("#viewport")).to_have_attribute("transform", "translate(0 0) scale(1)")


def check_browser(browser_type, targets: list[dict], output: Path, expect) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    browser = browser_type.launch(headless=True)
    context = None
    page = None
    current_case = "browser startup"
    errors = []
    results = []
    narrow_heights = {}
    try:
        context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                      device_scale_factor=1, reduced_motion="reduce")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        for target in targets:
            current_case = target["name"]
            page.goto(target["path"].as_uri(), wait_until="load")
            page.mouse.move(0, 0)
            page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            actual = assert_geometry(page, target)
            if target["graph_name"] == "sqlite":
                comparison = target["router"], target["interactive"]
                if target["width"] == 1200:
                    narrow_heights[comparison] = actual["height"]
                elif target["width"] == 4000 and actual["height"] >= narrow_heights[comparison]:
                    raise AssertionError(f"4000px SQLite layout did not become shorter than 1200px: {actual}")
            if target["interactive"]:
                assert_interaction(page, expect)
                # Reset must retain every cell's winning dimensions and wrapping.
                assert_geometry(page, target)
            else:
                expect(page.locator("script,#controls,#tooltip,foreignObject,[tabindex],[data-tooltip]")).to_have_count(0)
            page.screenshot(path=str(output / f"{target['name']}.png"))
            if errors:
                raise AssertionError(f"Browser script/console errors: {errors}")
            results.append(dict(case=target["name"], status="passed", **actual))
        failure_image = output / "failure.png"
        if failure_image.exists():
            failure_image.unlink()
        return dict(browser=browser_type.name, version=browser.version, status="passed",
                    text_bounds_scale=1, viewport=page.viewport_size, cases=results,
                    content_sandbox_disabled=(browser_type.name == "firefox" and
                                              os.environ.get("MOZ_DISABLE_CONTENT_SANDBOX") == "1"))
    except BaseException as error:
        error.add_note(f"Browser fixture: {current_case}")
        if page is not None:
            try:
                page.screenshot(path=str(output / "failure.png"))
            except Exception:
                pass
        raise
    finally:
        if context is not None:
            context.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("all", "chromium", "firefox"), default="all")
    parser.add_argument("--router", choices=("all", "default", "orthogonal"), default="orthogonal")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".scratch" / "space-layout-qa")
    args = parser.parse_args()
    os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        parser.error("Install the optional checker with uv run --with playwright==1.64.0")
    output = args.output_dir.resolve()
    routers = ("default", "orthogonal") if args.router == "all" else (args.router,)
    targets = fixtures(output, routers)
    names = ("chromium", "firefox") if args.browser == "all" else (args.browser,)
    results = []
    with sync_playwright() as playwright:
        for name in names:
            result = check_browser(getattr(playwright, name), targets, output / name, expect)
            results.append(result)
            (output / name / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = dict(status="passed", system=platform.system(), python=platform.python_version(), browsers=results)
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
