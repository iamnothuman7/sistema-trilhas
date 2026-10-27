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

function setup(fetch = async () => { throw new TypeError('offline'); }, overrides = {}) {
  const elements = Object.fromEntries(['scannerVideo', 'scannerResult', 'manualCodeInput', 'btnManualSearch', 'scannerPlaceholder',
    'btnStartCamera', 'btnStopCamera', 'btnSwitchCamera', 'cameraStatus', 'scannerPhoto'].map(id => [id, new Element('div')]));
  elements.scannerVideo.play = async () => {};
  elements.scannerVideo.pause = () => {};
  const context = vm.createContext({
    document: {
      getElementById: id => elements[id],
      createElement: tag => new Element(tag),
      createTextNode: value => { const node = new Element('#text'); node.textContent = value; return node; },
      cookie: 'csrftoken=test-token'
    },
    window: { addEventListener() {}, renderTrilhasIcons() {}, location: { href: 'https://example.test/gestao/' } },
    fetch, FormData, URL, Error, SyntaxError, TypeError, AbortController, setTimeout, clearTimeout, crypto: webcrypto,
    ...overrides,
  });
  const source = fs.readFileSync(path.join(__dirname, '../frontend/static/js/scanner.js'), 'utf8');
  vm.runInContext(source + '\nglobalThis.Scanner = window.EventScanner;', context);
  return { scanner: new context.Scanner('event', '/lookup', '/confirm', '/deliver'), elements, context };
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

function mediaStream(deviceId = 'rear') {
  const track = { stopped: 0, stop() { this.stopped += 1; }, getSettings: () => ({ deviceId }) };
  return { track, getTracks: () => [track], getVideoTracks: () => [track] };
}

test('câmera funciona sem BarcodeDetector com decoder local e prefere traseira sem áudio', async () => {
  const stream = mediaStream();
  let constraints;
  const { scanner, elements } = setup(undefined, {
    jsQR: () => null, setTimeout: () => 1, clearTimeout() {},
    navigator: { mediaDevices: { getUserMedia: async value => { constraints = value; return stream; }, enumerateDevices: async () => [] } },
  });
  await scanner.startCamera();
  assert.equal(scanner.scanning, true);
  assert.equal(constraints.audio, false);
  assert.equal(constraints.video.facingMode.ideal, 'environment');
  assert.equal(elements.btnStartCamera.disabled, true);
  assert.equal(elements.btnStopCamera.disabled, false);
  scanner.stopCamera();
  assert.equal(stream.track.stopped, 1);
  assert.equal(elements.scannerVideo.srcObject, null);
  assert.equal(scanner.scanning, false);
  assert.equal(elements.btnStartCamera.disabled, false);
});

test('câmera informa HTTPS e permissão negada sem prender controles', async () => {
  const { scanner, elements, context } = setup(undefined, {
    jsQR: () => null,
    navigator: { mediaDevices: { getUserMedia: async () => { throw Object.assign(new Error('denied'), { name: 'NotAllowedError' }); } } },
  });
  context.window.isSecureContext = false;
  await scanner.startCamera();
  assert.match(elements.cameraStatus.textContent, /HTTPS/);
  context.window.isSecureContext = true;
  await scanner.startCamera();
  assert.match(elements.cameraStatus.textContent, /Permissão da câmera negada/);
  assert.equal(scanner.cameraStarting, false);
  assert.equal(elements.btnStartCamera.disabled, false);
});

test('cancelar enquanto permissão está pendente encerra mídia devolvida depois', async () => {
  let grant;
  const stream = mediaStream();
  const { scanner } = setup(undefined, {
    jsQR: () => null,
    navigator: { mediaDevices: { getUserMedia: () => new Promise(resolve => { grant = resolve; }) } },
  });
  const starting = scanner.startCamera();
  await Promise.resolve();
  scanner.stopCamera();
  grant(stream);
  await starting;
  assert.equal(stream.track.stopped, 1);
  assert.equal(scanner.scanning, false);
  assert.equal(scanner.stream, null);
});

test('trocar câmera encerra a anterior e solicita o dispositivo seguinte', async () => {
  const streams = [mediaStream('rear'), mediaStream('front')];
  const calls = [];
  const { scanner } = setup(undefined, {
    jsQR: () => null, setTimeout: () => 1, clearTimeout() {},
    navigator: { mediaDevices: {
      getUserMedia: async value => { calls.push(value); return streams[calls.length - 1]; },
      enumerateDevices: async () => ['rear', 'front'].map(deviceId => ({ kind: 'videoinput', deviceId })),
    } },
  });
  await scanner.startCamera();
  await scanner.switchCamera();
  assert.equal(streams[0].track.stopped, 1);
  assert.equal(calls[1].video.deviceId.exact, 'front');
  assert.equal(scanner.deviceId, 'front');
  scanner.stopCamera();
  assert.equal(streams[1].track.stopped, 1);
});

test('decoder local lê QR real e fallback atende detector nativo incompleto', async () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures/scanner_qr_matrix.json'), 'utf8'));
  const size = fixture.rows.length * 4;
  const pixels = new Uint8ClampedArray(size * size * 4);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
    const offset = (y * size + x) * 4;
    const color = fixture.rows[Math.floor(y / 4)][Math.floor(x / 4)] === '1' ? 0 : 255;
    pixels.set([color, color, color, 255], offset);
  }
  const { scanner } = setup(undefined, { jsQR: require('../frontend/static/vendor/jsqr/jsQR.js') });
  scanner.canvas.getContext = () => ({ drawImage() {}, getImageData: () => ({ data: pixels, width: size, height: size }) });
  scanner.detector = { detect: async () => { throw new Error('native unsupported'); } };
  assert.equal(await scanner.decodeImage({ width: size, height: size }), fixture.value);
  assert.equal(scanner.detector, null);
});

test('leitura de QR para câmera e consulta uma ficha, sem confirmar entrega automaticamente', async () => {
  const { scanner, elements } = setup();
  const stream = mediaStream();
  scanner.stream = stream;
  scanner.scanning = true;
  elements.scannerVideo.readyState = 2;
  scanner.decodeImage = async () => 'opaque-qr-token';
  const lookups = [];
  scanner.lookupCode = async code => lookups.push(code);
  scanner.submitAction = () => assert.fail('QR não autoriza entrega automática');
  await scanner.scanLoop();
  await scanner.scanLoop();
  assert.deepEqual(lookups, ['opaque-qr-token']);
  assert.equal(scanner.lookupMethod, 'qr');
  assert.equal(stream.track.stopped, 1);
});

test('busca ambígua exige seleção e resposta antiga não troca a ficha atual', async () => {
  let oldResponse;
  const { scanner, elements } = setup(async url => {
    if (url.searchParams.get('code') === 'antigo') return new Promise(resolve => { oldResponse = resolve; });
    if (url.searchParams.get('registration_id')) return { ok: true, json: async () => ({ found: true, ...participant({ name: 'Nome escolhido' }) }) };
    return { ok: true, json: async () => ({ ambiguous: true, matches: [
      { registration_id: 'registration', name: 'Nome <script>', number: 101, city: 'Tianguá' },
      { registration_id: 'other', name: 'Outro nome', number: 102, city: 'Fortaleza' },
    ] }) };
  });
  const stale = scanner.lookupCode('antigo');
  await scanner.lookupCode('Piloto');
  assert.equal(scanner.currentReg, null);
  assert.equal(elements.scannerResult.querySelectorAll('button').length, 2);
  assert.ok(elements.scannerResult.textContent.includes('Nome <script>'));
  await elements.scannerResult.querySelectorAll('button')[0].listeners.click();
  oldResponse({ ok: true, json: async () => ({ found: true, ...participant({ name: 'Nome antigo' }) }) });
  await stale;
  assert.ok(elements.scannerResult.textContent.includes('Nome escolhido'));
  assert.ok(!elements.scannerResult.textContent.includes('Nome antigo'));
});

test('recarga da aba preserva chave e quantidade de entrega com resposta incerta', async () => {
  const saved = new Map();
  const window = { addEventListener() {}, location: { href: 'https://example.test/' },
    sessionStorage: { getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value) } };
  const requests = [];
  const fetch = async (_url, options) => { requests.push(options.body); throw new TypeError('network failure'); };
  const first = setup(fetch, { window });
  first.scanner.renderParticipantCard(participant());
  await first.scanner.deliverBenefit('benefit', first.elements.scannerResult.querySelectorAll('button')[1], 2);
  assert.equal(first.elements.scannerResult.querySelectorAll('input')[0].readOnly, true);
  assert.equal(first.elements.scannerResult.querySelectorAll('input')[0].value, '2');
  const second = setup(fetch, { window });
  second.scanner.renderParticipantCard(participant());
  assert.match(second.elements.scannerResult.textContent, /Conferir entrega pendente/);
  await second.scanner.deliverBenefit('benefit', second.elements.scannerResult.querySelectorAll('button')[1], 1);
  assert.equal(requests[0].get('idempotency_key'), requests[1].get('idempotency_key'));
  assert.equal(requests[1].get('quantity'), '2');
  assert.equal(saved.values().next().value.includes('Piloto'), false);
});

test('recusa definitiva permite corrigir quantidade; sessão expirada não vira erro de rede', async () => {
  const { scanner, elements } = setup(async () => ({ ok: false, status: 409, json: async () => ({ success: false, error: 'Saldo insuficiente' }) }));
  scanner.renderParticipantCard(participant());
  await scanner.deliverBenefit('benefit', elements.scannerResult.querySelectorAll('button')[1], 2);
  assert.equal(scanner.deliveryIntentions.benefit, undefined);
  assert.equal(elements.scannerResult.querySelectorAll('input')[0].readOnly, false);
  assert.match(elements.scannerResult.textContent, /Saldo insuficiente/);
  const expired = setup(async () => ({ redirected: true, status: 200 }));
  await expired.scanner.lookupCode('42');
  assert.match(expired.elements.scannerResult.textContent, /sessão expirou/);
});

test('foto é decodificada localmente, URL temporária é liberada e arquivo inválido é recusado', async () => {
  let revoked;
  class Photo {
    constructor() { this.naturalWidth = this.naturalHeight = 400; }
    set src(_) { Promise.resolve().then(() => this.onload()); }
  }
  class LocalURL extends URL {
    static createObjectURL() { return 'blob:local-qr'; }
    static revokeObjectURL(url) { revoked = url; }
  }
  const { scanner, elements } = setup(undefined, { jsQR: () => null, Image: Photo, URL: LocalURL });
  scanner.decodeImage = async () => 'photo-qr';
  const calls = [];
  scanner.lookupCode = async code => calls.push(code);
  await scanner.readPhoto({ size: 20, type: 'image/png' });
  assert.deepEqual(calls, ['photo-qr']);
  assert.equal(revoked, 'blob:local-qr');
  assert.equal(scanner.lookupMethod, 'qr');
  await scanner.readPhoto({ size: 16 * 1024 * 1024, type: 'image/png' });
  assert.match(elements.cameraStatus.textContent, /até 15 MB/);
  assert.equal(calls.length, 1);
});
