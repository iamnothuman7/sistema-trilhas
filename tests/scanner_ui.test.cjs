const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

// DOM mínimo: escrever HTML falha para manter a regressão de segurança explícita.
class Element {
  constructor(tag) { this.tagName = tag; this.childNodes = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.disabled = false; }
  set innerHTML(_) { throw new Error('Dados do scanner não devem ser inseridos como HTML.'); }
  set textContent(value) { this.childNodes = []; this.text = String(value); }
  get textContent() { return (this.text || '') + this.childNodes.map(node => node.textContent).join(''); }
  append(...nodes) { this.childNodes.push(...nodes); }
  replaceChildren(...nodes) { this.text = ''; this.childNodes = nodes; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  querySelectorAll(tag) {
    return this.childNodes.flatMap(node => [...(node.tagName === tag ? [node] : []), ...node.querySelectorAll(tag)]);
  }
  cloneNode(deep) {
    const clone = new Element(this.tagName);
    clone.text = this.text;
    clone.dataset = { ...this.dataset };
    if (deep) clone.childNodes = this.childNodes.map(node => node.cloneNode(true));
    return clone;
  }
}

function setup(fetch = async () => { throw new TypeError('offline'); }) {
  const elements = Object.fromEntries(['scannerVideo', 'scannerResult', 'manualCodeInput', 'btnManualSearch', 'scannerPlaceholder'].map(id => [id, new Element('div')]));
  const context = vm.createContext({
    document: {
      getElementById: id => elements[id],
      createElement: tag => new Element(tag),
      createTextNode: value => { const node = new Element('#text'); node.textContent = value; return node; },
      cookie: 'csrftoken=test-token'
    },
    window: { addEventListener() {}, renderTrilhasIcons() {}, location: { href: 'https://example.test/gestao/' } },
    fetch, FormData, URL, Error, SyntaxError, TypeError, setTimeout, crypto: webcrypto,
  });
  const source = fs.readFileSync(path.join(__dirname, '../frontend/static/js/scanner.js'), 'utf8');
  vm.runInContext(source + '\nglobalThis.Scanner = EventScanner;', context);
  return { scanner: new context.Scanner('event', '/lookup', '/confirm', '/deliver'), elements };
}

function participant(overrides = {}) {
  return {
    registration_id: 'registration', number: 101, name: 'Piloto', team: 'Equipe',
    category: 'Completo', shirt_size: 'M', commercial_state: 'ACTIVE',
    operational_state: 'READY', has_checked_in: false,
    entitlements: [{ id: 'benefit', title: 'Kit', quantity_total: 2, quantity_used: 0, state: 'AVAILABLE', is_consumed: false }],
    ...overrides,
  };
}

test('campos pessoais e erros são texto literal, sem HTML executável', () => {
  const { scanner, elements } = setup();
  const payload = '<img src=x onerror=alert(1)>';
  scanner.renderParticipantCard(participant({ name: payload, team: payload, category: payload, shirt_size: payload, entitlements: [{ id: "x');alert(1)//", title: payload, quantity_total: 2, quantity_used: 0 }] }));
  assert.equal(elements.scannerResult.querySelectorAll('img').length, 0);
  assert.ok(elements.scannerResult.textContent.includes(payload));
  assert.equal(elements.scannerResult.querySelectorAll('button').length, 2);
  scanner.renderError(payload);
  assert.equal(elements.scannerResult.textContent, payload);
});

test('inscrição cancelada ou operacionalmente pendente não oferece ações', () => {
  for (const overrides of [{ commercial_state: 'CANCELED' }, { commercial_state: 'RESERVED' }, { operational_state: 'NOT_READY' }, { operational_state: 'CLOSED' }]) {
    const { scanner, elements } = setup();
    scanner.renderParticipantCard(participant(overrides));
    assert.equal(elements.scannerResult.querySelectorAll('button').length, 0);
    assert.ok(elements.scannerResult.textContent.includes('indisponíveis'));
  }
});

test('benefício revogado ou consumido não pode ser entregue', () => {
  for (const overrides of [{ state: 'REVOKED' }, { quantity_used: 2, is_consumed: true }]) {
    const { scanner, elements } = setup();
    const data = participant();
    Object.assign(data.entitlements[0], overrides);
    scanner.renderParticipantCard(data);
    assert.equal(elements.scannerResult.querySelectorAll('button').length, 1);
    assert.equal(elements.scannerResult.querySelectorAll('button')[0].textContent, 'Confirmar presença');
  }
});

test('entrega parcial recarrega o saldo e mantém disponível a quantidade restante', async () => {
  let posted;
  const { scanner, elements } = setup(async (url, options) => {
    posted = { url, options };
    return { ok: true, json: async () => ({ success: true, quantity_used: 1, remaining: 1 }) };
  });
  scanner.currentCode = 'QR-token';
  scanner.renderParticipantCard(participant());
  scanner.lookupCode = async code => {
    assert.equal(code, 'QR-token');
    const data = participant();
    Object.assign(data.entitlements[0], { quantity_used: 1, state: 'PARTIALLY_USED' });
    scanner.renderParticipantCard(data);
  };
  await scanner.deliverBenefit('benefit', elements.scannerResult.querySelectorAll('button')[1]);
  assert.equal(posted.url, '/deliver');
  assert.equal(posted.options.method, 'POST');
  assert.equal(posted.options.body.get('entitlement_id'), 'benefit');
  assert.match(posted.options.body.get('idempotency_key'), /^[0-9a-f-]{36}$/);
  assert.equal(posted.options.headers['X-CSRFToken'], 'test-token');
  assert.ok(elements.scannerResult.textContent.includes('Entregue: 1 / 2'));
  assert.equal(elements.scannerResult.querySelectorAll('button').length, 2);
});

test('recusa do servidor restaura controles e exibe a mensagem sem HTML', async () => {
  const message = '<b>Inscrição bloqueada</b>';
  const { scanner, elements } = setup(async () => ({ ok: false, json: async () => ({ success: false, error: message }) }));
  scanner.renderParticipantCard(participant());
  const button = elements.scannerResult.querySelectorAll('button')[0];
  await scanner.confirmCheckin('registration', button);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Confirmar presença');
  assert.equal(scanner.actionMessage.hidden, false);
  assert.equal(scanner.actionMessage.textContent, message);
  assert.equal(scanner.pendingAction, false);
});

test('entrega mantém UUID após falha de rede e troca somente após sucesso', async () => {
  const keys = [];
  const { scanner, elements } = setup(async (_url, options) => {
    keys.push(options.body.get('idempotency_key'));
    if (keys.length === 1) throw new TypeError('connection lost after request');
    return { ok: true, json: async () => ({ success: true }) };
  });
  scanner.renderParticipantCard(participant());
  scanner.lookupCode = async () => scanner.renderParticipantCard(participant());
  await scanner.deliverBenefit('benefit', elements.scannerResult.querySelectorAll('button')[1]);
  assert.equal(scanner.actionMessage.hidden, false);
  // Looking up the same registration after an uncertain result must retain the key.
  scanner.renderParticipantCard(participant());
  await scanner.deliverBenefit('benefit', elements.scannerResult.querySelectorAll('button')[1]);
  await scanner.deliverBenefit('benefit', elements.scannerResult.querySelectorAll('button')[1]);
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
  assert.match(keys[0], /^[0-9a-f-]{36}$/);
});

test('resposta ilegível preserva UUID e trocar benefício ou inscrição inicia outra intenção', async () => {
  const keys = [];
  const { scanner, elements } = setup(async (_url, options) => {
    keys.push(options.body.get('idempotency_key'));
    return { ok: true, json: async () => { throw new SyntaxError('interrupted response'); } };
  });
  scanner.renderParticipantCard(participant());
  const button = () => elements.scannerResult.querySelectorAll('button')[1];
  await scanner.deliverBenefit('benefit', button());
  await scanner.deliverBenefit('benefit', button());
  await scanner.deliverBenefit('another-benefit', button());
  scanner.renderParticipantCard(participant({ registration_id: 'another-registration' }));
  await scanner.deliverBenefit('another-benefit', button());
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
  assert.notEqual(keys[2], keys[3]);
});
