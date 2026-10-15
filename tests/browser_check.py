"""Optional real-browser verification, separate from the offline unit suite.

Run on a machine/CI runner with Playwright and its browsers installed. This
script launches an isolated headless test profile and reads generated fixtures.
It does not attach to an existing user browser or use an authenticated profile.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import re
import shutil

from kaitai_svg.cli import convert_file
from kaitai_svg.loader import load_graph
from kaitai_svg.model import Graph, Node, Row
from kaitai_svg.render import render_svg


ROOT = Path(__file__).resolve().parents[1]
DOC = '첫 번째 설명입니다.\n\n<script>window.svgInjected = true</script> & "quoted"'


def fixtures(directory: Path) -> tuple[Path, Path, Path, Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("packet.ksy", "common.ksy"):
        shutil.copyfile(ROOT / "tests" / "fixtures" / name, directory / name)
    default_packet = directory / "packet-default.svg"
    shutil.copyfile(convert_file(directory / "packet.ksy"), default_packet)
    packet = convert_file(directory / "packet.ksy", show_imports=True, show_nested_types=True,
                          interactive=True)
    source = directory / "hover_check.ksy"
    # JSON strings are valid quoted YAML scalars; preserve the exact doc text.
    source.write_text(
        "meta: {id: hover_check, imports: [common]}\n"
        "doc: Root documentation\nseq:\n"
        "  - id: payload\n    type: common::identifier\n"
        f"    doc: {json.dumps(DOC, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    typography = directory / "typography.svg"
    samples = ("WMwm@" * 24, "가독성한글漢字" * 9)
    typography.write_text(render_svg(Graph(
        "Wide Latin and Korean font bounds",
        [Node(
            f"font-{index}", content, subtitle=content,
            rows=[Row("field", content, content, details=content,
                      pos_label=content, size_label=content)],
        ) for index, content in enumerate(samples)],
        [],
    ), interactive=True), encoding="utf-8")
    static_documented = directory / "hover_check-default.svg"
    shutil.copyfile(convert_file(source), static_documented)
    return default_packet, packet, static_documented, convert_file(source, interactive=True), typography


def sqlite_fixtures(directory: Path) -> tuple[Path, Path, Graph]:
    """Build static and interactive browser targets from the shipped SQLite example."""
    for name in ("sqlite3.ksy", "vlq_base128_be.ksy"):
        shutil.copyfile(ROOT / "examples" / name, directory / name)
    source = directory / "sqlite3.ksy"
    graph = load_graph(source)
    static = convert_file(source)
    interactive = directory / "sqlite3-interactive.svg"
    interactive.write_text(render_svg(graph, interactive=True), encoding="utf-8")
    return static, interactive, graph


def assert_static_diagram(page, graph: Graph, expect) -> None:
    """A plain SVG keeps native docs/selection and never takes over wheel/drag."""
    expect(page.locator("script,#controls,#tooltip,foreignObject,[tabindex],[data-tooltip]")).to_have_count(0)
    dimensions = page.locator("svg").evaluate("""svg => ({
      width: Number(svg.getAttribute('width')), height: Number(svg.getAttribute('height')),
      viewWidth: svg.viewBox.baseVal.width, viewHeight: svg.viewBox.baseVal.height,
    })""")
    if (dimensions["width"] <= 0 or dimensions["height"] <= 0 or
            dimensions["width"] != dimensions["viewWidth"] or
            dimensions["height"] != dimensions["viewHeight"]):
        raise AssertionError(f"Static SVG must have its intrinsic diagram dimensions: {dimensions}")
    styles = page.locator("svg,#viewport,.diagram-title,.field-label").evaluate_all("""items =>
      items.flatMap(item => {
        const style = getComputedStyle(item);
        return style.userSelect === 'none' || style.touchAction !== 'auto'
          ? [{element: item.getAttribute('class') || item.id || item.tagName,
              userSelect: style.userSelect, touchAction: style.touchAction}] : [];
      })""")
    if styles:
        raise AssertionError(f"Static SVG still captures selection/touch gestures: {styles}")
    for node in graph.nodes:
        card = page.locator(f'.node[data-node-id={json.dumps(node.id)}]')
        if node.doc:
            expect(card.locator(":scope > title")).to_contain_text(node.doc)
        for row in node.rows:
            if row.doc:
                item = card.locator(f'.row[data-row-id={json.dumps(row.id)}]')
                expect(item.locator(":scope > title")).to_contain_text(row.doc)
    if page.evaluate("Boolean(window.svgInjected)"):
        raise AssertionError("Static native documentation was interpreted as script")

    viewport = page.locator("#viewport")
    original_transform = viewport.get_attribute("transform")
    title = page.locator(".diagram-title")
    title.scroll_into_view_if_needed()
    bounds = title.bounding_box()
    if not bounds:
        raise AssertionError("Static title has no browser bounds")
    page.mouse.move(bounds["x"] + 1, bounds["y"] + bounds["height"] / 2)
    page.mouse.down()
    page.mouse.move(min(bounds["x"] + bounds["width"] - 1, 1400),
                    bounds["y"] + bounds["height"] / 2, steps=8)
    page.mouse.up()
    if viewport.get_attribute("transform") != original_transform:
        raise AssertionError("Static SVG changed the diagram transform while dragging")
    # Check native word selection separately from the drag gesture: Chromium
    # can leave a drag selection empty on a short, fully visible SVG title.
    title.dblclick()
    if not page.evaluate("Boolean(window.getSelection()?.toString())"):
        raise AssertionError("Static SVG text could not be selected with a pointer")
    page.mouse.move(700, 900)
    page.mouse.wheel(0, 200)
    # Two frames allow native scrolling and any accidentally retained handler.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    if viewport.get_attribute("transform") != original_transform:
        raise AssertionError("Static SVG changed the diagram transform on wheel input")
    first_row = page.locator(".row").first
    if first_row.count():
        background = first_row.locator(".row-hit")
        before = background.evaluate("item => getComputedStyle(item).fill")
        first_row.hover()
        if background.evaluate("item => getComputedStyle(item).fill") != before:
            raise AssertionError("Static SVG retained the interactive row hover highlight")
    page.evaluate("() => { window.getSelection()?.removeAllRanges(); window.scrollTo(0, 0); }")
    page.mouse.move(1400, 950)


def assert_text_in_cards(page) -> None:
    """Check real fallback-font bounds, including the 16-unit side padding."""
    overflow = page.locator(".node").evaluate_all("""nodes => nodes.flatMap(node => {
      const box = node.querySelector('.node-box').getBoundingClientRect();
      const matrix = node.getScreenCTM(), scale = Math.hypot(matrix.a, matrix.b);
      const margin = 16 * scale, tolerance = Math.max(.2, .5 * scale);
      // Glyph side bearings can extend about one device pixel left of the
      // text origin; still enforce the full right-side wrapping budget.
      const leftTolerance = Math.max(1.1, 1.5 * scale);
      return [...node.querySelectorAll('text')].flatMap(text => {
        const r = text.getBoundingClientRect();
        // End-anchored entry counts can have the same small glyph bearing on
        // their right edge that left-anchored text has on its left edge.
        const rightTolerance = text.getAttribute('text-anchor') === 'end' ? leftTolerance : tolerance;
        return r.left < box.left + margin - leftTolerance ||
               r.right > box.right - margin + rightTolerance ||
               r.top < box.top - tolerance || r.bottom > box.bottom + tolerance
          ? [{node: node.dataset.nodeId, role: text.getAttribute('class'), text: text.textContent,
              rightOverrun: r.right - (box.right - margin), leftOverrun: box.left + margin - r.left,
              topOverrun: box.top - r.top, bottomOverrun: r.bottom - box.bottom, scale}]
          : [];
      });
    })""")
    if overflow:
        raise AssertionError(f"Text outside its card padding: {overflow}")
    # A string can fit its card while still colliding with an adjacent column.
    # Measure the actual rendered fallback font against each cell's content box.
    cell_overflow = page.locator(".table-cell").evaluate_all("""cells => cells.flatMap(cell => {
      const matrix = cell.getScreenCTM(), scale = Math.hypot(matrix.a, matrix.b);
      const left = new DOMPoint(Number(cell.dataset.cellLeft), 0).matrixTransform(matrix).x;
      const right = new DOMPoint(Number(cell.dataset.cellRight), 0).matrixTransform(matrix).x;
      const leftTolerance = Math.max(1.1, 1.5 * scale), tolerance = Math.max(.2, .5 * scale);
      const row = cell.closest('.row').querySelector('.row-hit').getBoundingClientRect();
      return [...cell.querySelectorAll('text')].flatMap(text => {
        const bounds = text.getBoundingClientRect();
        const rightTolerance = getComputedStyle(text).textAnchor === 'end' ? leftTolerance : tolerance;
        return bounds.left < left - leftTolerance || bounds.right > right + rightTolerance ||
               bounds.top < row.top - tolerance || bounds.bottom > row.bottom + tolerance
          ? [{node: cell.closest('.node').dataset.nodeId,
              row: cell.closest('.row').dataset.rowId, column: cell.dataset.column,
              text: text.textContent, left, right, bounds: {left: bounds.left, right: bounds.right}}]
          : [];
      });
    })""")
    if cell_overflow:
        raise AssertionError(f"Text outside its table cell: {cell_overflow}")


def assert_technical_fonts(page) -> None:
    failures = page.locator("svg").evaluate("""svg => {
      const mono = '.node-title,.field-label,.field-type,.field-pos,.field-size,.field-detail,.type-size';
      const sans = '.node-subtitle,.table-heading,.section-label,.diagram-title,.tooltip-box';
      return [[mono, 'monospace'], [sans, 'sans-serif']].flatMap(([selector, expected]) =>
        [...svg.querySelectorAll(selector)].flatMap(element => {
          const family = getComputedStyle(element).fontFamily;
          return family.includes(expected) ? [] : [{role: element.getAttribute('class'), family, expected}];
        }));
    }""")
    if failures:
        raise AssertionError(f"Wrong rendered font families: {failures}")


def assert_relationship_styles(page, expect) -> None:
    """Color, line pattern, and endpoint semantics survive keyboard highlight."""
    expected = {
        "import": ("rgb(182, 106, 22)", "9px, 5px", None, "arrow-import"),
        "contains": ("rgb(23, 125, 114)", "none", "diamond-contains", None),
    }
    for kind, (color, dash, start, end) in expected.items():
        edges = page.locator(f".edge.edge-kind-{kind}")
        if not edges.count():
            raise AssertionError(f"Fixture has no {kind} relationships")
        edge = edges.first
        path = edge.locator(".edge-path")
        def style() -> dict:
            return path.evaluate("""path => {
              const style = getComputedStyle(path);
              return {color: style.stroke, dash: style.strokeDasharray,
                      start: path.getAttribute('marker-start'), end: path.getAttribute('marker-end')};
            }""")
        before = style()
        expected_style = {"color": color, "dash": dash,
                          "start": f"url(#{start})" if start else None,
                          "end": f"url(#{end})" if end else None}
        if before != expected_style:
            raise AssertionError(f"Incorrect {kind} style: {before}, expected {expected_style}")
        edge.focus()
        expect(edge).to_have_class(re.compile(r"\bactive\b"))
        if style() != before:
            raise AssertionError(f"Highlight obscured {kind} relationship semantics")
        page.keyboard.press("Escape")
    # Do not leave a focused connector around subsequent pointer tests.
    page.locator("svg").focus()
    page.keyboard.press("Escape")


def assert_relationship_visibility(page, graph: Graph, expect, *, show_imports: bool,
                                   show_nested_types: bool) -> None:
    """Options independently hide structural lines, preserving all definitions/references."""
    visibility = {"import": show_imports, "contains": show_nested_types}
    visible_edges = sum(visibility.get(edge.kind, True) for edge in graph.edges)
    expect(page.locator(".edge")).to_have_count(visible_edges)
    for kind, visible in visibility.items():
        edges = [edge for edge in graph.edges if edge.kind == kind]
        if not edges:
            raise AssertionError(f"Relationship visibility fixture has no {kind} edges")
        expect(page.locator(f".edge.edge-kind-{kind}")).to_have_count(len(edges) if visible else 0)
        expect(page.locator(f".legend .edge-kind-{kind}")).to_have_count(1 if visible else 0)
    actual_nodes = set(page.locator(".node").evaluate_all("nodes => nodes.map(node => node.dataset.nodeId)"))
    if actual_nodes != {node.id for node in graph.nodes}:
        raise AssertionError("Changing relationship visibility removed schema definitions")
    files = {node.id: node.source_file for node in graph.nodes}
    references = [edge for edge in graph.edges if edge.kind in {"type", "enum"}]
    if not any(files[edge.source] != files[edge.target] for edge in references):
        raise AssertionError("Relationship visibility fixture has no cross-file references")
    for edge in references:
        selector = (f'.edge.edge-kind-{edge.kind}[data-source={json.dumps(edge.source)}]'
                    f'[data-target={json.dumps(edge.target)}]')
        if edge.source_row is not None:
            selector += f'[data-source-row={json.dumps(edge.source_row)}]'
        expect(page.locator(selector)).to_have_count(1)


def assert_file_groups(page) -> None:
    """File panels contain their nodes and routing leaves their captions clear."""
    failures = page.locator("svg").evaluate("""svg => {
      const panels = [...svg.querySelectorAll('.file-group')].map(group => ({
        file: group.dataset.sourceFile,
        bounds: group.querySelector('.file-group-box').getBoundingClientRect(),
      }));
      const failures = [];
      for (const node of svg.querySelectorAll('.node[data-source-file]')) {
        if (!node.dataset.sourceFile) continue;
        const matches = panels.filter(panel => panel.file === node.dataset.sourceFile);
        if (matches.length !== 1) { failures.push({node: node.dataset.nodeId, panelCount: matches.length}); continue; }
        const card = node.querySelector('.node-box').getBoundingClientRect();
        const panel = matches[0].bounds;
        if (card.left < panel.left - .5 || card.right > panel.right + .5 ||
            card.top < panel.top - .5 || card.bottom > panel.bottom + .5)
          failures.push({node: node.dataset.nodeId, error: 'node outside source-file panel'});
      }
      for (let i = 0; i < panels.length; i++) {
        for (let j = i + 1; j < panels.length; j++) {
          const a = panels[i].bounds, b = panels[j].bounds;
          if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > .5 &&
              Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > .5)
            failures.push({files: [panels[i].file, panels[j].file], error: 'source-file panels overlap'});
        }
      }
      const labels = [...svg.querySelectorAll('.file-group-title,.file-group-count')].map(label => ({
        text: label.textContent,
        file: label.closest('.file-group').dataset.sourceFile,
        bounds: label.getBoundingClientRect(),
      }));
      for (const path of svg.querySelectorAll('.edge-path')) {
        const values = path.getAttribute('d').match(/-?\\d+(?:\\.\\d+)?/g).map(Number);
        const matrix = path.getScreenCTM(), padding = Math.hypot(matrix.a, matrix.b);
        for (let i = 2; i < values.length; i += 2) {
          const a = new DOMPoint(values[i - 2], values[i - 1]).matrixTransform(matrix);
          const b = new DOMPoint(values[i], values[i + 1]).matrixTransform(matrix);
          for (const label of labels) {
            const r = label.bounds;
            const left = r.left - padding, right = r.right + padding;
            const top = r.top - padding, bottom = r.bottom + padding;
            const crosses = Math.abs(a.x - b.x) < .001
              ? left < a.x && a.x < right && Math.max(Math.min(a.y, b.y), top) < Math.min(Math.max(a.y, b.y), bottom)
              : top < a.y && a.y < bottom && Math.max(Math.min(a.x, b.x), left) < Math.min(Math.max(a.x, b.x), right);
            if (crosses) failures.push({file: label.file, text: label.text,
              edge: path.closest('.edge').getAttribute('aria-label'), error: 'connector crosses file caption'});
          }
        }
      }
      return failures;
    }""")
    if failures:
        raise AssertionError(f"File grouping layout is invalid: {failures}")


def edge_hover_point(edge) -> dict:
    """Find a visible segment midpoint where real hit testing reaches this edge."""
    point = edge.locator(".edge-hit").evaluate("""path => {
      const values = path.getAttribute('d').match(/-?\\d+(?:\\.\\d+)?/g).map(Number);
      const segments = [];
      for (let i = 2; i < values.length; i += 2) {
        const a = {x: values[i - 2], y: values[i - 1]};
        const b = {x: values[i], y: values[i + 1]};
        segments.push({a, b, length: Math.hypot(b.x - a.x, b.y - a.y)});
      }
      segments.sort((a, b) => b.length - a.length);
      for (const {a, b} of segments) {
        for (const ratio of [.5, .4, .6, .25, .75]) {
          const p = new DOMPoint(a.x + (b.x - a.x) * ratio,
                                 a.y + (b.y - a.y) * ratio).matrixTransform(path.getScreenCTM());
          if (document.elementFromPoint(p.x, p.y) === path) return {x: p.x, y: p.y};
        }
      }
      return null;
    }""")
    if not point:
        raise AssertionError("No visible connector segment accepts pointer input")
    return point


def check_browser(browser_type, output: Path, expect) -> dict:
    default_packet, packet, static_documented, documented, typography = fixtures(output)
    packet_graph = load_graph(output / "packet.ksy")
    sqlite_static, sqlite_interactive, sqlite_graph = sqlite_fixtures(output)
    browser = browser_type.launch(headless=True)
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
    page = None
    page_errors: list[str] = []
    try:
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(default_packet.as_uri())
        assert_static_diagram(page, packet_graph, expect)
        assert_relationship_visibility(page, packet_graph, expect,
                                       show_imports=False, show_nested_types=False)
        assert_text_in_cards(page)
        assert_file_groups(page)
        assert_technical_fonts(page)
        page.locator("svg").screenshot(path=str(output / "packet-default.png"))

        for interactive in (False, True):
            for suffix, show_imports, show_nested_types in (
                ("default", False, False), ("imports-only", True, False),
                ("nested-types-only", False, True), ("all-relationships", True, True),
            ):
                if (not interactive and suffix == "default") or (interactive and suffix == "all-relationships"):
                    continue  # These two modes have dedicated interaction checks.
                mode = "interactive" if interactive else "static"
                variant = output / f"packet-{mode}-{suffix}.svg"
                variant.write_text(render_svg(packet_graph, show_imports=show_imports,
                                              show_nested_types=show_nested_types,
                                              interactive=interactive), encoding="utf-8")
                page.goto(variant.as_uri())
                if interactive:
                    expect(page.locator("#controls")).to_be_visible()
                else:
                    assert_static_diagram(page, packet_graph, expect)
                assert_relationship_visibility(page, packet_graph, expect,
                                               show_imports=show_imports, show_nested_types=show_nested_types)
                assert_text_in_cards(page)
                assert_file_groups(page)
                page.screenshot(path=str(output / f"packet-{mode}-{suffix}.png"))

        page.goto(sqlite_static.as_uri())
        assert_static_diagram(page, sqlite_graph, expect)
        assert_relationship_visibility(page, sqlite_graph, expect,
                                       show_imports=False, show_nested_types=False)
        assert_text_in_cards(page)
        assert_file_groups(page)
        page.screenshot(path=str(output / "sqlite-static.png"))

        page.goto(sqlite_interactive.as_uri())
        expect(page.locator("#controls")).to_be_visible()
        assert_relationship_visibility(page, sqlite_graph, expect,
                                       show_imports=False, show_nested_types=False)
        assert_text_in_cards(page)
        expect(page.locator(".node")).to_have_count(len(sqlite_graph.nodes))
        sqlite_row = page.locator(".row[data-tooltip]").first
        if not sqlite_row.count():
            raise AssertionError("SQLite example has no documented field to inspect")
        sqlite_row.hover()
        expect(page.locator("#tooltip")).to_be_visible()
        page.get_by_role("button", name="Zoom in", exact=True).click()
        expect(page.locator("#viewport")).not_to_have_attribute("transform", "translate(0 0) scale(1)")
        page.get_by_role("button", name="Reset view (0)", exact=True).click()
        expect(page.locator("#viewport")).to_have_attribute("transform", "translate(0 0) scale(1)")
        page.screenshot(path=str(output / "sqlite-interactive.png"))

        page.goto(static_documented.as_uri())
        assert_static_diagram(page, load_graph(output / "hover_check.ksy"), expect)
        expect(page.get_by_role("group", name="payload: common::identifier", exact=True)
               .locator(":scope > title")).to_contain_text(DOC)
        page.locator("svg").screenshot(path=str(output / "documentation-default.png"))

        page.goto(packet.as_uri())
        expect(page.locator("#controls")).to_be_visible()
        expect(page.locator(".node")).to_have_count(7)
        expect(page.locator(".edge")).to_have_count(11)
        assert_relationship_visibility(page, packet_graph, expect,
                                       show_imports=True, show_nested_types=True)

        assert_text_in_cards(page)
        assert_file_groups(page)
        assert_technical_fonts(page)
        assert_relationship_styles(page, expect)
        # Standalone SVG fills the viewport and has no HTML document.body.
        # Playwright's full_page sizing waits for a body and would time out.
        page.screenshot(path=str(output / "packet.png"))

        # A field selects its own outgoing connection, including when its parent
        # has several unrelated type, enum, import, and containment connections.
        sender = page.get_by_role("group", name="sender: common::identifier", exact=True)
        sender_id = sender.get_attribute("data-row-id")
        source_id = sender.evaluate("row => row.closest('.node').dataset.nodeId")
        edge_selector = f'.edge[data-source={json.dumps(source_id)}][data-source-row={json.dumps(sender_id)}]'
        sender_edge = page.locator(edge_selector)
        expect(sender_edge).to_have_count(1)
        sender.hover()
        expect(page.locator(".edge.active")).to_have_count(1)
        expect(sender_edge).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator(".edge.muted")).to_have_count(10)
        unrelated = page.locator(f'.edge[data-source={json.dumps(source_id)}]:not([data-source-row={json.dumps(sender_id)}])')
        if not unrelated.count() or not unrelated.evaluate_all("edges => edges.every(edge => edge.classList.contains('muted'))"):
            raise AssertionError("Unrelated connections from the sender's parent were not muted")
        page.screenshot(path=str(output / "sender-focus.png"))

        # Use pointer movement onto the real SVG stroke, not a synthetic event.
        page.keyboard.press("Escape")
        hover_point = edge_hover_point(sender_edge)
        page.mouse.move(hover_point["x"], hover_point["y"])
        expect(page.locator("#tooltip")).to_be_visible()
        expect(page.locator(".edge.active")).to_have_count(1)
        expect(sender_edge).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator(".edge.muted")).to_have_count(10)
        endpoint_titles = sender_edge.evaluate("""edge => {
          const nodes = [...document.querySelectorAll('.node')];
          return [edge.dataset.source, edge.dataset.target].map(id =>
            nodes.find(node => node.dataset.nodeId === id).getAttribute('aria-label'));
        }""")
        expect(page.locator(".tooltip-box")).to_contain_text(" → ".join(endpoint_titles))
        page.screenshot(path=str(output / "edge-hover.png"))

        # Connections must expose the same endpoints and isolated highlight
        # through real keyboard traversal, without requiring a pointing device.
        page.mouse.move(1400, 950)
        page.keyboard.press("Escape")
        expect(page.locator("#tooltip")).to_be_hidden()
        for _ in range(page.locator('[tabindex="0"]').count() + 1):
            page.keyboard.press("Tab")
            if sender_edge.evaluate("edge => edge === document.activeElement"):
                break
        expect(sender_edge).to_be_focused()
        expect(page.locator("#tooltip")).to_be_visible()
        expect(page.locator(".tooltip-box")).to_contain_text(" → ".join(endpoint_titles))
        expect(page.locator(".edge.active")).to_have_count(1)
        expect(sender_edge).to_have_class(re.compile(r"\bactive\b"))
        expect(page.locator(".edge.muted")).to_have_count(10)
        page.screenshot(path=str(output / "edge-focus.png"))

        # Repeated wide Latin and Korean/Han characters exercise every node
        # text role; short example labels alone cannot expose font overflows.
        page.mouse.move(1400, 950)
        page.goto(typography.as_uri())
        expect(page.locator("#controls")).to_be_visible()
        expect(page.locator(".node")).to_have_count(2)
        for role in ("node-title", "node-subtitle", "field-pos", "field-size", "field-label", "field-type", "field-detail"):
            if not page.locator(".node").evaluate_all("""(nodes, role) => nodes.every(node =>
                node.querySelectorAll('.' + role).length > 1)""", role):
                raise AssertionError(f"Typography fixture did not wrap {role}")
        assert_text_in_cards(page)
        assert_technical_fonts(page)
        page.screenshot(path=str(output / "typography.png"))

        page.goto(documented.as_uri())
        expect(page.locator("#controls")).to_be_visible()
        row = page.get_by_role("group", name="payload: common::identifier", exact=True)
        tip = page.locator("#tooltip")
        tip_text = page.locator(".tooltip-box")
        viewport = page.locator("#viewport")
        reset = page.get_by_role("button", name="Reset view (0)", exact=True)

        # Real hit testing, script startup in SVG/XML, and foreignObject layout.
        row.hover()
        expect(tip).to_be_visible()
        if tip_text.text_content() != "payload\n\ncommon::identifier\n\n" + DOC:
            raise AssertionError("Hover tooltip changed or omitted documentation")
        if page.evaluate("Boolean(window.svgInjected)"):
            raise AssertionError("Documentation was interpreted as script")
        page.screenshot(path=str(output / "hover.png"))

        page.keyboard.press("Escape")
        expect(tip).to_be_hidden()
        # Reach the documented field through real sequential keyboard navigation.
        page.mouse.move(700, 900)
        for _ in range(page.locator('[tabindex="0"]').count() + 1):
            page.keyboard.press("Tab")
            if row.evaluate("element => element === document.activeElement"):
                break
        expect(row).to_be_focused()
        expect(tip).to_be_visible()
        if tip_text.text_content() != "payload\n\ncommon::identifier\n\n" + DOC:
            raise AssertionError("Tab focus did not show the field documentation")
        page.screenshot(path=str(output / "focus.png"))

        # Wheel zoom, controls, keyboard reset, and real pointer dragging.
        page.mouse.move(700, 900)
        page.mouse.wheel(0, -200)
        expect(viewport).to_have_attribute("transform", re.compile(r"scale\(1\."))
        reset.click()
        expect(viewport).to_have_attribute("transform", "translate(0 0) scale(1)")
        page.mouse.move(700, 900)
        page.mouse.wheel(0, 200)
        expect(viewport).to_have_attribute("transform", re.compile(r"scale\(0\."))
        reset.click()
        page.get_by_role("button", name="Zoom out", exact=True).click()
        expect(viewport).to_have_attribute("transform", re.compile(r"scale\(0\."))
        page.keyboard.press("0")
        expect(viewport).to_have_attribute("transform", "translate(0 0) scale(1)")
        page.get_by_role("button", name="Zoom in", exact=True).click()
        expect(viewport).not_to_have_attribute("transform", "translate(0 0) scale(1)")
        page.keyboard.press("0")
        expect(viewport).to_have_attribute("transform", "translate(0 0) scale(1)")
        page.mouse.move(700, 900)
        page.mouse.down()
        page.mouse.move(760, 940, steps=8)
        page.mouse.up()
        expect(viewport).not_to_have_attribute("transform", "translate(0 0) scale(1)")
        if page.evaluate("Boolean(window.getSelection()?.toString())"):
            raise AssertionError("Panning selected diagram text")
        if tip_text.evaluate("element => getComputedStyle(element).userSelect") != "text":
            raise AssertionError("Documentation must remain selectable")
        page.screenshot(path=str(output / "pan.png"))
        reset.click()
        expect(viewport).to_have_attribute("transform", "translate(0 0) scale(1)")

        # Narrow view: a visible tooltip must remain inside the browser viewport.
        page.set_viewport_size({"width": 360, "height": 640})
        row.hover()
        expect(tip).to_be_visible()
        bounds = tip_text.bounding_box()
        if not bounds or bounds["x"] < 0 or bounds["y"] < 0:
            raise AssertionError(f"Tooltip has invalid bounds: {bounds}")
        if bounds["x"] + bounds["width"] > 361 or bounds["y"] + bounds["height"] > 641:
            raise AssertionError(f"Tooltip overflows the viewport: {bounds}")
        page.screenshot(path=str(output / "narrow.png"))
        if page_errors:
            raise AssertionError(f"Browser script errors: {page_errors}")
        return {"browser": browser_type.name, "version": browser.version, "status": "passed",
                "imports_hidden_by_default": "passed", "imports_opt_in": "passed",
                "nested_types_hidden_by_default": "passed", "nested_types_opt_in": "passed",
                "relationship_option_combinations": 4,
                "interaction_and_relationship_option_combinations": 8,
                "static_by_default": "passed", "interactive_opt_in": "passed",
                "static_native_docs_and_selection": "passed",
                "sqlite_static_and_interactive": "passed",
                "all_definitions_and_references": "passed"}
    except BaseException:
        if page is not None:
            try:
                page.screenshot(path=str(output / "failure.png"))
            except Exception:
                # A crashed page must not hide the original verification failure.
                pass
        raise
    finally:
        try:
            context.close()
        except Exception:
            # The browser can already be gone after a tab process crash.
            pass
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chromium", "firefox"), default="chromium")
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".scratch" / "browser-qa")
    args = parser.parse_args()
    try:
        from playwright.sync_api import expect, sync_playwright
    except ImportError:
        parser.error("Install the optional browser checker with uv run --with playwright==1.64.0")
    output = (args.output_dir / args.browser).resolve()
    with sync_playwright() as playwright:
        result = check_browser(getattr(playwright, args.browser), output, expect)
    result.update(system=platform.system(), python=platform.python_version())
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
