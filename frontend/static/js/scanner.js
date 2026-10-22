/** Credenciamento e entregas. Dados de participantes são inseridos como texto. */
class EventScanner {
    constructor(eventId, lookupUrl, confirmUrl, deliverUrl) {
        Object.assign(this, { eventId, lookupUrl, confirmUrl, deliverUrl });
        this.video = document.getElementById('scannerVideo');
        this.resultContainer = document.getElementById('scannerResult');
        this.manualInput = document.getElementById('manualCodeInput');
        this.manualBtn = document.getElementById('btnManualSearch');
        this.stream = null;
        this.scanning = false;
        this.currentReg = null;
        this.currentCode = '';
        this.lookupSequence = 0;
        this.pendingAction = false;
        this.deliveryIntention = null;
        this.detector = null;
        this.resultContainer.setAttribute('aria-live', 'polite');
        this.manualBtn?.addEventListener('click', () => this.searchManual());
        this.manualInput?.addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); this.searchManual(); }
        });
        window.addEventListener('pagehide', () => this.stopCamera());
    }
    node(tag, className, text) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = String(text ?? '—');
        return element;
    }
    icon(name) {
        const icon = this.node('i');
        icon.dataset.lucide = name;
        icon.setAttribute('aria-hidden', 'true');
        return icon;
    }
    feedback(success) {
        if (typeof AudioFeedback !== 'undefined') {
            if (success) AudioFeedback.playSuccess();
            else AudioFeedback.playError();
        }
    }
    searchManual() {
        const code = this.manualInput?.value.trim();
        if (code && !this.pendingAction) this.lookupCode(code);
    }
    cameraMessage(message) {
        const placeholder = document.getElementById('scannerPlaceholder');
        if (placeholder) placeholder.replaceChildren(this.node('p', 'text-muted', message));
    }
    async startCamera() {
        if (!('BarcodeDetector' in window)) {
            this.cameraMessage('Leitura automática indisponível neste navegador. Use a busca por número, nome ou código.');
            return;
        }
        try {
            this.detector = new BarcodeDetector({ formats: ['qr_code'] });
            this.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
            this.video.srcObject = this.stream;
            await this.video.play();
            document.getElementById('scannerPlaceholder')?.replaceChildren();
            this.scanning = true;
            this.scanLoop();
        } catch (_) {
            this.stopCamera();
            this.cameraMessage('Câmera indisponível ou sem permissão. Use a busca por número, nome ou código.');
        }
    }
    stopCamera() {
        this.scanning = false;
        this.stream?.getTracks().forEach(track => track.stop());
        this.stream = null;
    }
    async scanLoop() {
        if (!this.scanning) return;
        if (!this.pendingAction) {
            try {
                const barcodes = await this.detector.detect(this.video);
                if (barcodes.length) {
                    await this.lookupCode(barcodes[0].rawValue);
                    await new Promise(resolve => setTimeout(resolve, 2500));
                }
            } catch (_) { /* Uma imagem sem QR não interrompe a leitura contínua. */ }
        }
        if (this.scanning) requestAnimationFrame(() => this.scanLoop());
    }
    async lookupCode(code) {
        const sequence = ++this.lookupSequence;
        this.resultContainer.setAttribute('aria-busy', 'true');
        try {
            const url = new URL(this.lookupUrl, window.location.href);
            url.searchParams.set('code', code);
            const response = await fetch(url, { headers: { Accept: 'application/json' } });
            const data = await response.json();
            if (sequence !== this.lookupSequence) return;
            if (!response.ok || !data.found) {
                this.currentReg = null;
                this.feedback(false);
                this.renderError(data.error || 'Inscrição não encontrada.');
                return;
            }
            this.currentCode = code;
            this.currentReg = data;
            this.feedback(true);
            this.renderParticipantCard(data);
        } catch (_) {
            if (sequence === this.lookupSequence) {
                this.currentReg = null;
                this.renderError('Não foi possível consultar a inscrição. Verifique sua conexão e tente novamente.');
            }
        } finally {
            if (sequence === this.lookupSequence) this.resultContainer.setAttribute('aria-busy', 'false');
        }
    }
    renderParticipantCard(data) {
        if (this.deliveryIntention && this.deliveryIntention.registrationId !== data.registration_id) this.deliveryIntention = null;
        this.currentReg = data;
        const eligible = data.commercial_state === 'ACTIVE' && ['READY', 'CHECKED_IN'].includes(data.operational_state);
        const hasCheckedIn = Boolean(data.has_checked_in);
        const card = this.node('div', 'card mg-scanner-participant');
        const heading = this.node('div', 'mg-row mg-element-gap');
        const person = this.node('div');
        person.append(
            this.node('h2', 'section-title', '#' + (data.number || '—') + ' · ' + (data.name || 'Participante')),
            this.node('p', 'text-muted mg-meta', 'Equipe: ' + (data.team || '—') + ' · Categoria: ' + (data.category || '—')),
            this.node('p', 'mg-strong mg-text-brand', 'Camiseta: ' + (data.shirt_size || '—'))
        );
        const status = hasCheckedIn ? 'Presença confirmada' : eligible ? 'Aguardando check-in' : 'Inscrição com pendência';
        heading.append(person, this.node('span', 'badge ' + (hasCheckedIn ? 'badge-success' : 'badge-warning'), status));
        card.append(heading);
        this.actionMessage = this.node('div', 'alert alert-error mg-element-gap');
        this.actionMessage.setAttribute('role', 'alert');
        this.actionMessage.hidden = true;
        card.append(this.actionMessage);
        if (!eligible) card.append(this.node('p', 'alert alert-warning mg-element-gap', 'Credenciamento e entregas indisponíveis. A inscrição precisa estar ativa e liberada para participação.'));
        if (hasCheckedIn) {
            card.append(this.node('p', 'alert alert-success mg-element-gap', 'Presença registrada' + (data.checked_in_at ? ' às ' + data.checked_in_at : '') + '.'));
        } else if (eligible) {
            const checkin = this.node('button', 'btn btn-primary btn-block mg-element-gap');
            checkin.type = 'button';
            checkin.append(this.icon('scan-line'), document.createTextNode('Confirmar presença'));
            checkin.addEventListener('click', () => this.confirmCheckin(data.registration_id, checkin));
            card.append(checkin);
        }
        const benefits = this.node('div', 'mg-inset-card');
        benefits.append(this.node('h3', 'section-title', 'Kits e materiais'));
        const entitlements = Array.isArray(data.entitlements) ? data.entitlements : [];
        entitlements.forEach(entitlement => {
            const row = this.node('div', 'mg-row mg-scanner-benefit');
            const description = this.node('div');
            description.append(
                this.node('strong', '', entitlement.title),
                this.node('p', 'text-muted mg-meta', 'Entregue: ' + (entitlement.quantity_used ?? 0) + ' / ' + (entitlement.quantity_total ?? 0))
            );
            row.append(description);
            const consumed = entitlement.is_consumed || Number(entitlement.quantity_used) >= Number(entitlement.quantity_total);
            if (entitlement.state === 'REVOKED') row.append(this.node('span', 'badge badge-danger', 'Revogado'));
            else if (consumed) row.append(this.node('span', 'badge badge-success', 'Retirado'));
            else if (!eligible) row.append(this.node('span', 'badge badge-warning', 'Indisponível'));
            else {
                const deliver = this.node('button', 'btn btn-sm btn-secondary');
                deliver.type = 'button';
                deliver.append(this.icon('package-check'), document.createTextNode('Entregar'));
                deliver.addEventListener('click', () => this.deliverBenefit(entitlement.id, deliver));
                row.append(deliver);
            }
            benefits.append(row);
        });
        if (!entitlements.length) benefits.append(this.node('p', 'text-muted mg-meta', 'Nenhum kit cadastrado para esta modalidade.'));
        card.append(benefits);
        this.resultContainer.replaceChildren(card);
        window.renderTrilhasIcons?.(this.resultContainer);
    }
    renderError(message) {
        const alert = this.node('div', 'alert alert-error', message);
        alert.setAttribute('role', 'alert');
        this.resultContainer.replaceChildren(alert);
    }
    async submitAction(url, field, id, button, idempotencyKey = null) {
        if (this.pendingAction) return;
        this.pendingAction = true;
        this.lookupSequence += 1;
        const originalNodes = [...button.childNodes].map(node => node.cloneNode(true));
        const controls = [...this.resultContainer.querySelectorAll('button')];
        controls.forEach(control => { control.disabled = true; });
        if (this.actionMessage) this.actionMessage.hidden = true;
        button.textContent = 'Registrando…';
        const form = new FormData();
        form.append(field, id);
        if (idempotencyKey) form.append('idempotency_key', idempotencyKey);
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'X-CSRFToken': this.getCSRFToken(), Accept: 'application/json' },
                body: form
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'A operação não foi concluída. Tente novamente.');
            if (idempotencyKey && this.deliveryIntention?.key === idempotencyKey) this.deliveryIntention = null;
            await this.lookupCode(this.currentCode);
        } catch (error) {
            this.feedback(false);
            if (this.actionMessage) {
                this.actionMessage.textContent = error instanceof SyntaxError || error instanceof TypeError
                    ? idempotencyKey
                        ? 'Falha de comunicação. Tente novamente; a mesma entrega não será registrada duas vezes.'
                        : 'Falha de comunicação. Consulte a inscrição novamente antes de repetir a operação.'
                    : error.message;
                this.actionMessage.hidden = false;
            }
        } finally {
            this.pendingAction = false;
            this.resultContainer.setAttribute('aria-busy', 'false');
            controls.forEach(control => { control.disabled = false; });
            button.replaceChildren(...originalNodes);
        }
    }
    confirmCheckin(registrationId, button) {
        return this.submitAction(this.confirmUrl, 'registration_id', registrationId, button);
    }
    deliverBenefit(entitlementId, button) {
        if (this.pendingAction) return;
        const registrationId = this.currentReg?.registration_id;
        if (!this.deliveryIntention || this.deliveryIntention.registrationId !== registrationId || this.deliveryIntention.entitlementId !== entitlementId) {
            this.deliveryIntention = { registrationId, entitlementId, key: this.operationUUID() };
        }
        return this.submitAction(this.deliverUrl, 'entitlement_id', entitlementId, button, this.deliveryIntention.key);
    }
    operationUUID() {
        if (globalThis.crypto.randomUUID) return globalThis.crypto.randomUUID();
        const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    getCSRFToken() {
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }
}
