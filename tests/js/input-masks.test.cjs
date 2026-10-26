const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/static/js/input-masks.js', 'utf8');
function setup(kind, initial = '') {
    const listeners = {};
    const field = {value: initial, dataset: {mask: kind}, selectionStart: initial.length, selectionEnd: initial.length,
        matches: selector => Boolean(kind) && selector === 'input[data-mask]',
        setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; },
        dispatchEvent(event) { emit(event.type, event); }};
    const document = {activeElement: field, readyState: 'complete', querySelectorAll: () => [field],
        addEventListener(name, callback) { (listeners[name] ||= []).push(callback); }};
    function emit(name, extra = {}) {
        const event = {target: field, cancelable: true, preventDefault() { this.prevented = true; }, ...extra};
        for (const handler of listeners[name] || []) handler(event);
        return event;
    }
    vm.runInNewContext(source, {document, window: {addEventListener() {}}, Event: class { constructor(type) { this.type = type; } }});
    return {field, emit, type(value, start = value.length, end = start) { field.value = value; field.setSelectionRange(start, end); emit('input'); }};
}
test('CPF digitado, colado e já formatado tem a mesma apresentação', () => {
    const h = setup('cpf');
    for (const [value, expected] of [['529', '529'], ['5299', '529.9'], ['52998224725', '529.982.247-25'], ['529.982.247-25', '529.982.247-25'], ['', '']]) {
        h.type(value); assert.equal(h.field.value, expected);
    }
    assert.equal(setup('cpf', '52998224725').field.value, '529.982.247-25');
});
test('corrigir no meio preserva a posição e a seleção dos dígitos', () => {
    const h = setup('cpf');
    h.type('52998224725', 4, 7);
    assert.equal(h.field.selectionStart, 5);
    assert.equal(h.field.selectionEnd, 9);
});
test('backspace e delete junto à pontuação não prendem o cursor', () => {
    const h = setup('cpf', '52998224725');
    h.field.setSelectionRange(4, 4);
    assert.equal(h.emit('beforeinput', {inputType: 'deleteContentBackward'}).prevented, true);
    assert.equal(h.field.value.replace(/\D/g, ''), '5298224725');
    assert.equal(h.field.selectionStart, 2);
    h.type('52998224725'); h.field.setSelectionRange(3, 3);
    h.emit('beforeinput', {inputType: 'deleteContentForward'});
    assert.equal(h.field.value.replace(/\D/g, ''), '5298224725');
    assert.equal(h.field.selectionStart, 3);
});
test('telefone fixo, celular e código do Brasil sem confundir DDD 55', () => {
    const h = setup('phone');
    for (const [value, expected] of [['8836711234', '(88) 3671-1234'], ['88999991234', '(88) 99999-1234'], ['+55 (88) 99999-1234', '+55 (88) 99999-1234'], ['5588999991234', '+55 (88) 99999-1234'], ['55999991234', '(55) 99999-1234'], ['+351912345678', '+351912345678']]) {
        h.type(value); assert.equal(h.field.value, expected);
    }
});
test('delegação atende campos adicionados depois e não altera campos sem máscara', () => {
    const h = setup('cpf');
    const next = {...h.field, value: '11144477735'};
    h.emit('input', {target: next});
    assert.equal(next.value, '111.444.777-35');
    const unmasked = {...next, value: 'senha.exemplo-123', matches: () => false};
    h.emit('input', {target: unmasked});
    assert.equal(unmasked.value, 'senha.exemplo-123');
});
test('composição não é interrompida e UF é normalizada ao concluir', () => {
    const h = setup('uf');
    h.field.value = 'ce'; h.emit('input', {isComposing: true});
    assert.equal(h.field.value, 'ce');
    h.emit('compositionend'); assert.equal(h.field.value, 'CE');
});
