// Execute the exact generated SVG script against a small deterministic SVG DOM.
// This verifies event/state behavior; browser rendering and hit testing remain
// separate concerns. Node is a development check, never a runtime dependency.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));

class Element {
  constructor(tag, classes = '', dataset = {}, id = '') {
    this.tagName = tag; this.dataset = dataset; this.id = id;
    this.children = []; this.parentElement = null; this.style = {};
    this.attributes = {}; this.listeners = {}; this.textContent = '';
    this.scrollTop = 0; this.scrollHeight = 110;
    const values = new Set(classes.split(' ').filter(Boolean));
    this.classList = {
      contains: value => values.has(value),
      remove: (...items) => items.forEach(item => values.delete(item)),
      toggle: (value, on) => { if (on ?? !values.has(value)) values.add(value); else values.delete(value); },
    };
  }
  set innerHTML(value) { throw Error('Documentation must use textContent, never innerHTML'); }
  append(...children) { for (const child of children) { child.parentElement = this; this.children.push(child); } return this; }
  matches(selector) {
    return selector.split(',').some(part => {
      part = part.trim();
      if (part.startsWith('#')) return this.id === part.slice(1);
      if (part.startsWith('.')) return this.classList.contains(part.slice(1));
      if (part === '[data-tooltip]') return this.dataset.tooltip !== undefined;
      if (part === '[data-action]') return this.dataset.action !== undefined;
      return this.tagName === part;
    });
  }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  contains(target) { return target === this || this.children.some(child => child.contains(target)); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  getBoundingClientRect() { return {left: 0, top: 0, right: 1200, bottom: 800, width: 1200, height: 800}; }
  fire(name, properties = {}) {
    const event = {target: this, buttons: 0, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, ...properties};
    for (const listener of this.listeners[name] || []) listener(event);
    return event;
  }
}

const svg = new Element('svg');
const viewport = new Element('g', '', {}, 'viewport');
const controls = new Element('g', 'controls', {}, 'controls');
const tooltip = new Element('g', 'tooltip-layer', {}, 'tooltip');
const tipObject = new Element('foreignObject');
const tipText = new Element('div', 'tooltip-box');
tooltip.append(tipObject.append(tipText));
const buttons = Object.fromEntries(['in', 'out', 'reset'].map(action => [action, new Element('g', 'control', {action})]));
controls.append(...Object.values(buttons));
const nodes = fixture.nodes.map(item => new Element('g', 'node', {nodeId: item.id, tooltip: item.tooltip}));
for (let i = 0; i < nodes.length; i++) {
  for (const row of fixture.nodes[i].rows) nodes[i].append(new Element('g', 'row', {rowId: row.id, tooltip: row.tooltip}));
}
const edges = fixture.edges.map(edge => new Element('g', 'edge', edge));
viewport.append(...edges, ...nodes);
svg.append(viewport, controls, tooltip);
svg.getScreenCTM = () => ({a: 1, b: 0, inverse() { return this; }});
svg.createSVGPoint = () => ({x: 0, y: 0, matrixTransform() { return {x: this.x, y: this.y}; }});
const captured = new Set();
svg.setPointerCapture = id => captured.add(id);
svg.hasPointerCapture = id => captured.has(id);
svg.releasePointerCapture = id => captured.delete(id);
const timers = new Map(); let nextTimer = 1;
const window = new Element('window');
vm.runInNewContext(fixture.script, {
  document: {currentScript: {ownerSVGElement: svg}}, window, console,
  setTimeout: callback => { const id = nextTimer++; timers.set(id, callback); return id; },
  clearTimeout: id => timers.delete(id),
});
function flushTimers() { for (const [id, fn] of timers) { timers.delete(id); fn(); } }
function transform() { return viewport.getAttribute('transform'); }

assert.equal(controls.style.display, 'inline', 'controls initialized');
assert.match(controls.getAttribute('transform'), /translate\(1046 16\)/, 'controls anchored in visible viewport');

// Hover/focus preserve multiline, markup-like documentation as inert text.
const row = nodes[0].children[0];
svg.fire('pointerover', {target: row, clientX: 200, clientY: 210});
assert.equal(tipText.textContent, fixture.nodes[0].rows[0].tooltip);
assert.ok(tipText.textContent.includes('\n\n'), 'paragraphs preserved');
assert.ok(tipText.textContent.includes('<script>'), 'apparent markup remains literal');
assert.equal(tooltip.style.visibility, 'visible');
assert.ok(nodes[0].classList.contains('active'));
assert.ok(!nodes[1].classList.contains('muted'), 'connected type remains readable');
assert.ok(nodes[2].classList.contains('muted'), 'unrelated type dimmed');
assert.ok(edges[0].classList.contains('active'));
assert.ok(edges[1].classList.contains('muted'), 'another field on the same node is not highlighted');
svg.fire('pointerover', {target: edges[1], clientX: 600, clientY: 300});
assert.equal(tipText.textContent, fixture.edges[1].tooltip, 'connection hover identifies both endpoints');
assert.ok(edges[1].classList.contains('active'));
assert.ok(edges[0].classList.contains('muted'));
assert.ok(nodes[0].classList.contains('active'));
assert.ok(nodes[2].classList.contains('active'));
assert.ok(nodes[1].classList.contains('muted'));
svg.fire('focusin', {target: nodes[1]});
assert.equal(tipText.textContent, fixture.nodes[1].tooltip, 'keyboard focus shows node docs');
svg.fire('keydown', {key: 'Escape'});
assert.equal(tooltip.style.visibility, 'hidden');
assert.ok(nodes.every(node => !node.classList.contains('muted')), 'highlight reset');

// Wheel zoom anchors around the pointer, and controls/keyboard reset the view.
let event = svg.fire('wheel', {target: svg, deltaY: -100, clientX: 400, clientY: 300});
assert.ok(event.defaultPrevented);
assert.match(transform(), /scale\(1\.[0-9]+\)/);
assert.notEqual(transform(), 'translate(0 0) scale(1)');
buttons.reset.fire('click');
assert.equal(transform(), 'translate(0 0) scale(1)');
buttons.in.fire('keydown', {key: 'Enter'});
assert.match(transform(), /scale\(1.3\)/);
svg.fire('keydown', {key: '0'});
assert.equal(transform(), 'translate(0 0) scale(1)');
buttons.out.fire('click');
assert.match(transform(), /scale\(0\.[0-9]+\)/);
svg.fire('keydown', {key: 'Home'});

// Dragging uses root SVG coordinates and releases capture on pointerup.
svg.fire('pointerdown', {target: row, button: 0, buttons: 1, pointerId: 7, clientX: 100, clientY: 100});
svg.fire('pointermove', {target: row, buttons: 1, pointerId: 7, clientX: 103, clientY: 100});
assert.equal(transform(), 'translate(0 0) scale(1)', 'small movements stay clicks');
svg.fire('pointermove', {target: row, buttons: 1, pointerId: 7, clientX: 130, clientY: 125});
assert.equal(transform(), 'translate(30 25) scale(1)');
assert.ok(captured.has(7));
assert.equal(tooltip.style.visibility, 'hidden', 'drag closes tooltip');
svg.fire('pointerup', {pointerId: 7});
assert.ok(!captured.has(7));
svg.fire('keydown', {key: '0'});

// A press can leave the SVG before the 4px drag threshold. Without capture,
// its release is delivered outside the SVG, so the next move has buttons=0.
svg.fire('pointerdown', {target: svg, button: 0, buttons: 1, pointerId: 8, clientX: 1199, clientY: 100});
svg.fire('pointerout', {target: svg, relatedTarget: null});
svg.fire('pointermove', {target: row, buttons: 0, pointerId: 8, clientX: 900, clientY: 100});
assert.equal(transform(), 'translate(0 0) scale(1)', 'release outside SVG must not leave a stale drag');
assert.ok(!captured.has(8), 'a released pointer must not be captured');
assert.equal(svg.style.cursor, '');

// Capture can be transferred away without pointerup reaching this SVG.
svg.fire('pointerdown', {target: row, button: 0, buttons: 1, pointerId: 9, clientX: 100, clientY: 100});
svg.fire('pointermove', {target: row, buttons: 1, pointerId: 9, clientX: 120, clientY: 115});
assert.equal(transform(), 'translate(20 15) scale(1)');
captured.delete(9);
svg.fire('lostpointercapture', {pointerId: 9});
svg.fire('pointermove', {target: row, buttons: 1, pointerId: 9, clientX: 170, clientY: 170});
assert.equal(transform(), 'translate(20 15) scale(1)', 'capture loss must terminate the drag');
assert.equal(svg.style.cursor, '');
svg.fire('keydown', {key: '0'});

// Tooltip scrolling is not intercepted by diagram zoom.
event = svg.fire('wheel', {target: tipText, deltaY: 100, clientX: 100, clientY: 100});
assert.ok(!event.defaultPrevented);
assert.equal(transform(), 'translate(0 0) scale(1)');
svg.fire('pointerover', {target: row, clientX: 1195, clientY: 795});
const tipPosition = tooltip.getAttribute('transform').match(/translate\(([\d.-]+) ([\d.-]+)\)/);
assert.ok(Number(tipPosition[1]) >= 12 && Number(tipPosition[1]) + Number(tipObject.getAttribute('width')) <= 1188, 'tooltip stays within viewport width');
assert.ok(Number(tipPosition[2]) >= 12 && Number(tipPosition[2]) + Number(tipObject.getAttribute('height')) <= 788, 'tooltip stays within viewport height');
svg.fire('pointerout', {target: row, relatedTarget: null});
flushTimers();
assert.equal(tooltip.style.visibility, 'hidden');
window.fire('resize');
assert.equal(controls.style.display, 'inline');
console.log('SVG hover, keyboard focus, escaped multiline docs, highlighting, zoom, pan, reset and tooltip bounds passed.');
