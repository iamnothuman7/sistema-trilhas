/* Presentation only: server-side validation remains authoritative. */
(function () {
    'use strict';
    const digits = value => String(value).replace(/\D/g, '');
    function format(value, kind) {
        if (kind === 'uf') return value.replace(/[^a-z]/gi, '').toUpperCase().slice(0, 2);
        let number = digits(value);
        if (kind === 'cpf') {
            return number.slice(0, 11).replace(/^(\d{3})(\d)/, '$1.$2')
                .replace(/^(\d{3}\.\d{3})(\d)/, '$1.$2')
                .replace(/(\.\d{3})(\d)/, '$1-$2');
        }
        if (kind === 'phone') {
            // Keep an explicit country code; do not mistake the Brazilian DDD 55 for it.
            if (/^\s*\+/.test(value) && !number.startsWith('55')) return '+' + number.slice(0, 15);
            const country = number.startsWith('55') && (number.length > 11 || /^\s*\+/.test(value));
            if (country) number = number.slice(2);
            number = number.slice(0, 11);
            const local = number.length > 2 ? '(' + number.slice(0, 2) + ') ' + number.slice(2) : number;
            return (country ? '+55 ' : '') + local.replace(number.length > 10 ? /(\d{5})(\d)/ : /(\d{4})(\d)/, '$1-$2');
        }
        return value;
    }
    const significant = (value, kind) => kind === 'uf' ? value.replace(/[^a-z]/gi, '') : digits(value);
    function caret(value, count, kind) {
        if (!count) return 0;
        for (let index = 0, seen = 0; index < value.length; index++) {
            if (significant(value[index], kind)) seen++;
            if (seen === count) return index + 1;
        }
        return value.length;
    }
    function update(field) {
        if (!field?.matches?.('input[data-mask]')) return;
        const kind = field.dataset.mask;
        const old = field.value;
        const start = significant(old.slice(0, field.selectionStart ?? old.length), kind).length;
        const end = significant(old.slice(0, field.selectionEnd ?? old.length), kind).length;
        const formatted = format(old, kind);
        if (old === formatted) return;
        field.value = formatted;
        if (document.activeElement === field) field.setSelectionRange(caret(formatted, start, kind), caret(formatted, end, kind));
    }
    // Delegation also covers participants added to a group purchase after page load.
    ['input', 'change', 'focusin', 'compositionend'].forEach(name => document.addEventListener(name, event => {
        if (!event.isComposing) update(event.target);
    }));
    document.addEventListener('beforeinput', event => {
        const field = event.target;
        if (!field?.matches?.('input[data-mask]') || event.isComposing || !event.cancelable) return;
        const backward = event.inputType === 'deleteContentBackward';
        if (!backward && event.inputType !== 'deleteContentForward') return;
        const position = field.selectionStart;
        if (position === null || position !== field.selectionEnd) return;
        const kind = field.dataset.mask;
        let index = backward ? position - 1 : position;
        if (index < 0 || index >= field.value.length || significant(field.value[index], kind)) return;
        // Deleting a separator removes the adjacent digit instead of trapping the cursor.
        while (index >= 0 && index < field.value.length && !significant(field.value[index], kind)) index += backward ? -1 : 1;
        if (index < 0 || index >= field.value.length) return;
        event.preventDefault();
        field.value = field.value.slice(0, index) + field.value.slice(index + 1);
        const next = backward ? index : position;
        field.setSelectionRange(next, next);
        field.dispatchEvent(new Event('input', {bubbles: true}));
    });
    const refresh = () => document.querySelectorAll('input[data-mask]').forEach(update);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', refresh);
    else refresh();
    window.addEventListener('pageshow', refresh);
}());
