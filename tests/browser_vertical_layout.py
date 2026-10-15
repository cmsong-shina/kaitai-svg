"""Optional isolated browser checks for long, responsive vertical SVG documents.

Run: uv run --with playwright==1.64.0 python tests/browser_vertical_layout.py
The shipped schemas are read only; generated SVG/HTML/PNG/JSON files stay under
--output-dir. No existing browser profile or authenticated session is used.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform

from kaitai_svg.loader import load_graph
from kaitai_svg.render import render_svg


ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = {"desktop": {"width": 1140, "height": 800},
             "mobile": {"width": 390, "height": 700}}


def fixtures(output: Path) -> list[dict]:
    """Render each width/mode once, then reuse it in both browser sizes."""
    output.mkdir(parents=True, exist_ok=True)
    graph = load_graph(ROOT / "examples" / "sqlite3.ksy")
    result = []
    for width in (1200, 1440):
        for interactive in (False, True):
            mode = "interactive" if interactive else "static"
            name = f"sqlite-{width}-{mode}"
            options = {"layout_width": width} if width != 1200 else {}
            svg = render_svg(graph, layout="vertical-optimized", router="orthogonal",
                             interactive=interactive, **options)
            (output / f"{name}.svg").write_text(svg, encoding="utf-8")
            embedded = svg.partition("?>")[2].lstrip()
            wrapper = output / f"{name}.html"
            wrapper.write_text(
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>Vertical layout browser regression</title><style>'
                '*{box-sizing:border-box}html,body{margin:0;padding:0}'
                'body{font-family:system-ui;background:#dce6f1}'
                'main{width:min(960px,100%);margin:0 auto;padding:16px}'
                'h1{font-size:16px;line-height:24px;margin:0 0 16px}'
                '</style></head><body><main><h1>SQLite schema</h1>' + embedded +
                '</main></body></html>\n', encoding="utf-8",
            )
            result.append(dict(name=name, width=width, interactive=interactive, path=wrapper))
    return result


def frames(page) -> None:
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def settle_scroll(page) -> None:
    page.evaluate("""() => new Promise(resolve => {
      let previous = scrollY, stable = 0, count = 0;
      function frame() {
        stable = Math.abs(scrollY - previous) < .1 ? stable + 1 : 0;
        previous = scrollY;
        if (stable >= 6 || ++count >= 120) resolve(); else requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    })""")


def dimensions(page) -> dict:
    return page.locator("svg").evaluate("""svg => {
      const box = svg.getBoundingClientRect(), parent = svg.parentElement.getBoundingClientRect();
      return {width: box.width, height: box.height, left: box.left, right: box.right,
        intrinsicWidth: Number(svg.getAttribute('width')), intrinsicHeight: Number(svg.getAttribute('height')),
        viewWidth: svg.viewBox.baseVal.width, viewHeight: svg.viewBox.baseVal.height,
        parentWidth: parent.width, documentWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth, viewportHeight: innerHeight, scrollY};
    }""")


def assert_responsive(page, target_width: int) -> dict:
    actual = dimensions(page)
    if not 0 < actual["intrinsicWidth"] <= target_width:
        raise AssertionError(f"SVG exceeds its layout width: {actual}")
    if (actual["intrinsicWidth"] != actual["viewWidth"] or
            actual["intrinsicHeight"] != actual["viewHeight"]):
        raise AssertionError(f"Missing intrinsic dimensions: {actual}")
    expected_height = actual["width"] * actual["viewHeight"] / actual["viewWidth"]
    if abs(actual["height"] - expected_height) > 1:
        raise AssertionError(f"Responsive embedding lost its height/aspect ratio: {actual}")
    if (actual["documentWidth"] > actual["viewportWidth"] + 1 or actual["left"] < -.5 or
            actual["right"] > actual["viewportWidth"] + .5):
        raise AssertionError(f"Vertical diagram overflows the page horizontally: {actual}")
    if actual["height"] <= actual["viewportHeight"]:
        raise AssertionError(f"Regression fixture is not a long document: {actual}")
    if not page.locator(".diagram-title,.node-title").evaluate_all("items => items.every(item => { const box = item.getBoundingClientRect(); return box.width > 0 && box.height > 0; })"):
        raise AssertionError("Visible fallback-font text has no rendered dimensions")
    return actual


def assert_inside_viewport(page, locator, label: str) -> dict:
    box = locator.bounding_box()
    size = page.viewport_size
    if (not box or box["x"] < -.5 or box["y"] < -.5 or
            box["x"] + box["width"] > size["width"] + .5 or
            box["y"] + box["height"] > size["height"] + .5):
        raise AssertionError(f"{label} is outside the visible browser viewport: {box}")
    return box


def assert_scrolls_without_zoom(page) -> float:
    viewport = page.locator("#viewport")
    before_transform = viewport.get_attribute("transform")
    before_scroll = page.evaluate("scrollY")
    page.mouse.move(page.viewport_size["width"] / 2, page.viewport_size["height"] * .65)
    page.mouse.wheel(0, 420)
    page.wait_for_function("previous => scrollY > previous + 30", arg=before_scroll)
    settle_scroll(page)
    if viewport.get_attribute("transform") != before_transform:
        raise AssertionError("Ordinary document wheel scrolling zoomed or panned the diagram")
    return page.evaluate("scrollY")


def assert_documentation(page, expect) -> None:
    rows = page.locator(".row[data-tooltip]")
    if not rows.count():
        raise AssertionError("SQLite has no documented fields for tooltip checks")
    # Use a genuinely visible field near the bottom of the viewport to exercise
    # tooltip clamping after the document has already scrolled.
    row_index = rows.evaluate_all("""rows => {
      const indices = rows.flatMap((row, index) => {
        const b = row.getBoundingClientRect();
        return b.top >= 55 && b.bottom < innerHeight - 20 ? [{index, bottom: b.bottom}] : [];
      });
      indices.sort((a, b) => b.bottom - a.bottom);
      return indices[0]?.index ?? -1;
    }""")
    row = rows.nth(row_index) if row_index >= 0 else rows.first
    if row_index < 0:
        row.scroll_into_view_if_needed()
    settle_scroll(page)
    box = row.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    expect(page.locator("#tooltip")).to_be_visible()
    assert_inside_viewport(page, page.locator(".tooltip-box"), "Hover tooltip")
    if not page.locator(".tooltip-box").text_content():
        raise AssertionError("Hover tooltip contains no documentation")
    page.keyboard.press("Escape")
    row.focus()
    expect(row).to_be_focused()
    expect(page.locator("#tooltip")).to_be_visible()
    assert_inside_viewport(page, page.locator(".tooltip-box"), "Keyboard focus tooltip")
    page.keyboard.press("Escape")
    offscreen_index = rows.evaluate_all("rows => rows.findIndex(row => row.getBoundingClientRect().top > innerHeight + 40)")
    if offscreen_index >= 0:
        below_fold = rows.nth(offscreen_index)
        below_fold.focus()
        expect(below_fold).to_be_focused()
        settle_scroll(page)
        expect(page.locator("#tooltip")).to_be_visible()
        assert_inside_viewport(page, page.locator(".tooltip-box"), "Tooltip after native focus scrolling")
        page.keyboard.press("Escape")
    # Exercise an overflowing documentation popup without changing the source
    # schema or introducing a second SVG fixture. Pointer events still hit the
    # real emitted tooltip; only this isolated browser's doc string is longer.
    row.evaluate("row => row.dataset.tooltip = Array(80).fill('Long documentation line for native popup scrolling.').join('\\n')")
    row.scroll_into_view_if_needed()
    settle_scroll(page)
    box = row.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    expect(page.locator("#tooltip")).to_be_visible()
    popup = page.locator(".tooltip-box")
    if not popup.evaluate("box => box.scrollHeight > box.clientHeight"):
        raise AssertionError("Long documentation did not create a scrollable tooltip")
    bounds = popup.bounding_box()
    page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
    page.mouse.wheel(0, 180)
    page.wait_for_function("document.querySelector('.tooltip-box').scrollTop > 0")
    expect(page.locator("#tooltip")).to_be_visible()
    assert_inside_viewport(page, popup, "Scrolled long documentation tooltip")
    page.keyboard.press("Escape")


def assert_touch_allowed(page, browser_name: str) -> str:
    """Check uncancelled pointer gestures everywhere and a real Chromium swipe."""
    viewport = page.locator("#viewport")
    original = viewport.get_attribute("transform")
    result = page.locator("svg").evaluate("""svg => {
      const x = innerWidth / 2, y = innerHeight * .7;
      const options = {bubbles:true,cancelable:true,pointerType:'touch',pointerId:71,
                       isPrimary:true,button:0,buttons:1,clientX:x,clientY:y};
      const down = new PointerEvent('pointerdown', options);
      const move = new PointerEvent('pointermove', {...options, clientY:y-180});
      svg.dispatchEvent(down); svg.dispatchEvent(move);
      svg.dispatchEvent(new PointerEvent('pointerup', {...options, buttons:0, clientY:y-180}));
      return {cancelled:down.defaultPrevented || move.defaultPrevented,
              touchAction:getComputedStyle(svg).touchAction};
    }""")
    if (result["cancelled"] or "pan-y" not in result["touchAction"] or
            viewport.get_attribute("transform") != original):
        raise AssertionError(f"Touch document scrolling is intercepted: {result}")
    if browser_name != "chromium":
        return "uncancelled_touch_pointer_and_native_pan_css"
    session = page.context.new_cdp_session(page)
    try:
        x = page.viewport_size["width"] / 2
        start = page.viewport_size["height"] * .8
        before_scroll = page.evaluate("scrollY")
        session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": start}]})
        for step in range(1, 9):
            session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": start - step * 30}]})
        session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_function("previous => scrollY > previous + 30", arg=before_scroll)
        if viewport.get_attribute("transform") != original:
            raise AssertionError("A native touchscreen swipe panned the diagram")
    finally:
        session.detach()
    return "native_touchscreen_swipe_and_uncancelled_pointer"


def check_browser(browser_type, targets: list[dict], output: Path, expect) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    browser = browser_type.launch(headless=True)
    errors = []
    results = []
    page = None
    try:
        for size_name, size in VIEWPORTS.items():
            context = browser.new_context(viewport=size, has_touch=size_name == "mobile",
                                          device_scale_factor=1, reduced_motion="reduce")
            try:
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                for target in targets:
                    name = f'{target["name"]}-{size_name}'
                    page.goto(target["path"].as_uri(), wait_until="load")
                    page.mouse.move(0, 0)
                    frames(page)
                    actual = assert_responsive(page, target["width"])
                    if target["interactive"]:
                        expect(page.locator("#controls")).to_be_visible()
                        expect(page.locator("#tooltip")).to_be_hidden()
                        assert_inside_viewport(page, page.locator("#controls"), "Initial controls")
                    else:
                        expect(page.locator("#controls,#tooltip,script")).to_have_count(0)
                    page.screenshot(path=str(output / f"{name}-top.png"))
                    scrolled = assert_scrolls_without_zoom(page)
                    if target["interactive"]:
                        assert_inside_viewport(page, page.locator("#controls"), "Scrolled controls")
                        assert_documentation(page, expect)
                    page.screenshot(path=str(output / f"{name}-scroll.png"))
                    touch_check = None
                    if target["interactive"]:
                        viewport = page.locator("#viewport")
                        original = viewport.get_attribute("transform")
                        page.mouse.move(size["width"] / 2, size["height"] / 2)
                        page.keyboard.down("Control")
                        try:
                            page.mouse.wheel(0, -120)
                        finally:
                            page.keyboard.up("Control")
                        page.wait_for_function("""before => {
                          const current = document.querySelector('#viewport').getAttribute('transform');
                          return current !== null && current !== before;
                        }""", arg=original)
                        page.get_by_role("button", name="Reset view (0)", exact=True).click()
                        expect(viewport).to_have_attribute("transform", "translate(0 0) scale(1)")
                        if size_name == "mobile":
                            touch_check = assert_touch_allowed(page, browser_type.name)
                    assert_responsive(page, target["width"])
                    results.append(dict(case=name, status="passed", dimensions=actual,
                                        natural_scroll_y=scrolled, touch_check=touch_check))
                if errors:
                    raise AssertionError(f"Browser script errors: {errors}")
            except BaseException as exc:
                if errors:
                    exc.add_note(f"Browser script errors: {errors}")
                if page is not None:
                    try:
                        page.screenshot(path=str(output / "failure.png"))
                    except Exception:
                        pass
                raise
            finally:
                context.close()
        return dict(browser=browser_type.name, version=browser.version, status="passed", cases=results)
    finally:
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("all", "chromium", "firefox"), default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".scratch" / "vertical-qa")
    args = parser.parse_args()
    # Firefox can leave font readiness pending for a very tall inline SVG even
    # while its visible fallback fonts are painted. Do not let that unrelated
    # readiness promise block screenshots of the measured visible viewport.
    os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        parser.error("Install the optional checker with uv run --with playwright==1.64.0")
    output = args.output_dir.resolve()
    targets = fixtures(output)
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
