const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/static/js/theme.js', 'utf8');

function app(saved, unavailable = false) {
  const events = {}, windowEvents = {}, attributes = {}, clicks = {};
  const root = { dataset: {}, style: {} };
  const button = { setAttribute: (k, v) => attributes[k] = v, addEventListener: (k, v) => clicks[k] = v };
  const writes = [];
  vm.runInNewContext(source, {
    document: { documentElement: root, querySelector: () => ({ setAttribute() {} }), querySelectorAll: () => [button], addEventListener: (k, v) => events[k] = v },
    window: { addEventListener: (k, v) => windowEvents[k] = v },
    localStorage: { getItem() { if (unavailable) throw Error('blocked'); return saved; }, setItem(...args) { if (unavailable) throw Error('blocked'); writes.push(args); } }
  });
  return { root, attributes, clicks, events, windowEvents, writes };
}
test('saved light preference is applied before DOMContentLoaded', () => {
  const page = app('light');
  assert.equal(page.root.dataset.theme, 'light');
  assert.equal(page.root.style.colorScheme, 'light');
});
test('new visitors start dark and button persists an accessible light preference', () => {
  const page = app();
  assert.equal(page.root.dataset.theme, 'dark');
  page.events.DOMContentLoaded(); page.clicks.click();
  assert.equal(page.attributes['aria-pressed'], 'true');
  assert.equal(page.attributes['aria-label'], 'Ativar tema escuro');
  assert.deepEqual(page.writes, [['morcego-theme', 'light']]);
});
test('theme still toggles when storage is unavailable', () => {
  const page = app(null, true);
  page.events.DOMContentLoaded(); page.clicks.click();
  assert.equal(page.root.dataset.theme, 'light');
});
test('other tabs synchronize and unrecognized stored values fall back safely', () => {
  const page = app('unexpected');
  assert.equal(page.root.dataset.theme, 'dark');
  page.windowEvents.storage({ key: 'morcego-theme', newValue: 'light' });
  assert.equal(page.root.dataset.theme, 'light');
  page.windowEvents.storage({ key: 'something-else', newValue: 'dark' });
  assert.equal(page.root.dataset.theme, 'light');
});
