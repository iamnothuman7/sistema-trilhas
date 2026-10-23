const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function select(options, selectedIndex = 0) {
  options.forEach((option, index) => { option.index = index; });
  return { options, selectedIndex, get selectedOptions() { return this.selectedIndex < 0 ? [] : [this.options[this.selectedIndex]]; } };
}
const product = { value: 'pilot' };
const lot = select([
  { dataset: { product: 'pilot', cents: '12000' } },
  { dataset: { product: 'other', cents: '12000' } },
]);
const shirt = select([
  { value: 'M', dataset: { product: 'pilot', extraCents: '0' } },
  { value: 'SPECIAL', dataset: { product: 'pilot', extraCents: '1500' } },
  { value: 'M', dataset: { product: 'other', extraCents: '2000' } },
], 1);
const participant = { querySelector(selector) {
  if (selector.includes('participant_product')) return product;
  if (selector.includes('participant_lot')) return lot;
  if (selector.includes('participant_shirt')) return shirt;
  return {};
} };
const handlers = {};
const container = { querySelectorAll() { return [participant]; }, addEventListener(name, fn) { handlers[name] = fn; } };
const total = {};
const elements = { registrationForm: {}, participantsContainer: container, registrationTotal: total, btnAddParticipant: { addEventListener() {} } };
const scriptPath = path.join(__dirname, '..', 'frontend', 'static', 'js', 'registration.js');
vm.runInNewContext(fs.readFileSync(scriptPath, 'utf8'), {
  document: { getElementById(id) { return elements[id]; }, addEventListener(name, fn) { fn(); } },
  Intl, window: {},
});
assert.match(total.textContent, /135,00/, 'initial displayed total includes the selected variant extra');
shirt.selectedIndex = 0;
handlers.change();
assert.match(total.textContent, /120,00/, 'zero-extra choice keeps the authorized base price');
product.value = 'other';
handlers.change();
assert.equal(shirt.selectedIndex, 2, 'matching size code on another product selects the enabled option by index');
assert.match(total.textContent, /140,00/, 'changing product recalculates its own extra');
assert.equal(shirt.options[0].disabled, true, 'other products cannot submit the hidden variant');
console.log('Registration totals and variant selection: 4 checks passed.');
