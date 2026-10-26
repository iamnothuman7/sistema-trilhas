document.addEventListener('DOMContentLoaded', function () {
    const form = document.getElementById('registrationForm');
    if (!form) return;
    const container = document.getElementById('participantsContainer');
    const total = document.getElementById('registrationTotal');
    const addButton = document.getElementById('btnAddParticipant');
    const maxParticipants = Number(form.dataset?.maxParticipants) || 20;
    const formatter = new Intl.NumberFormat('pt-BR', {style: 'currency', currency: 'BRL'});

    function refresh() {
        let cents = 0;
        const participants = Array.from(container.querySelectorAll('[data-participant]'));
        addButton.disabled = participants.length >= maxParticipants;
        participants.forEach(function (participant, index) {
            participant.querySelector('[data-participant-title]').textContent = 'Participante ' + (index + 1);
            participant.querySelector('[data-remove-participant]').hidden = participants.length === 1;
            const product = participant.querySelector('[name="participant_product[]"]');
            const lot = participant.querySelector('[name="participant_lot[]"]');
            const shirt = participant.querySelector('[name="participant_shirt[]"]');
            [lot, shirt].forEach(function (select) {
                Array.from(select.options).forEach(function (option) {
                    option.hidden = option.disabled = option.dataset.product !== product.value;
                });
                if (!select.selectedOptions[0] || select.selectedOptions[0].disabled) {
                    const first = Array.from(select.options).find(function (option) { return !option.disabled; });
                    select.selectedIndex = first ? first.index : -1;
                }
            });
            cents += Number(lot.selectedOptions[0]?.dataset.cents || 0)
                + Number(shirt.selectedOptions[0]?.dataset.extraCents || 0);
        });
        total.textContent = formatter.format(cents / 100);
    }

    addButton.addEventListener('click', function () {
        if (container.querySelectorAll('[data-participant]').length >= maxParticipants) return;
        const participant = container.querySelector('[data-participant]').cloneNode(true);
        participant.querySelectorAll('input').forEach(function (input) { input.value = ''; });
        container.appendChild(participant);
        refresh();
        participant.querySelector('input').focus();
        if (window.renderTrilhasIcons) window.renderTrilhasIcons();
    });
    container.addEventListener('click', function (event) {
        const remove = event.target.closest('[data-remove-participant]');
        if (remove && container.querySelectorAll('[data-participant]').length > 1) {
            remove.closest('[data-participant]').remove();
            refresh();
        }
    });
    container.addEventListener('change', refresh);
    refresh();
});
