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
        this.deliveryIntentions = {};
        this.lookupMethod = 'manual';
        try {
            const saved = JSON.parse(window.sessionStorage?.getItem('scanner-deliveries-' + eventId) || '{}');
            if (saved && typeof saved === 'object' && !Array.isArray(saved)) {
                Object.entries(saved).forEach(([id, value]) => {
                    if (value && value.entitlementId === id && typeof value.registrationId === 'string' &&
                        typeof value.key === 'string' && /^[0-9a-f-]{36}$/i.test(value.key) &&
                        Number.isInteger(value.quantity) && value.quantity > 0 && value.quantity <= 1000) this.deliveryIntentions[id] = value;
                });
            }
        } catch (_) { /* Storage may be unavailable. */ }
        this.detector = null;
        this.cameraGeneration = 0;
        this.cameraTimer = null;
        this.cameraStarting = false;
        this.cameraFacing = 'environment';
        this.devices = [];
        this.deviceId = '';
        this.lookupPending = false;
        this.canvas = document.createElement('canvas');
        this.startButton = document.getElementById('btnStartCamera');
        this.stopButton = document.getElementById('btnStopCamera');
        this.switchButton = document.getElementById('btnSwitchCamera');
        this.photoInput = document.getElementById('scannerPhoto');
        this.startButton?.addEventListener('click', () => this.startCamera());
        this.stopButton?.addEventListener('click', () => this.stopCamera());
        this.switchButton?.addEventListener('click', () => this.switchCamera());
        this.photoInput?.addEventListener('change', () => this.readPhoto(this.photoInput.files?.[0]));
        this.resultContainer.setAttribute('aria-live', 'polite');
        this.manualBtn?.addEventListener('click', () => this.searchManual());
        this.manualInput?.addEventListener('keydown', event => {
            if (event.key === 'Enter') { event.preventDefault(); this.searchManual(); }
        });
        window.addEventListener('pagehide', () => this.stopCamera());
        document.addEventListener?.('visibilitychange', () => {
            if (document.hidden) this.stopCamera('Câmera pausada ao sair da tela. Toque em Iniciar câmera para continuar.');
        });
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
        if (code && !this.pendingAction) {
            this.lookupMethod = 'manual';
            this.stopCamera('Busca manual selecionada. Você pode iniciar a câmera novamente.');
            this.lookupCode(code);
        }
    }
    cameraMessage(message) {
        const placeholder = document.getElementById('scannerPlaceholder');
        if (placeholder) placeholder.replaceChildren(this.node('p', 'text-muted', message));
        const status = document.getElementById('cameraStatus');
        if (status) status.textContent = message;
    }
    cameraControls() {
        if (this.startButton) this.startButton.disabled = this.cameraStarting || this.scanning;
        if (this.stopButton) this.stopButton.disabled = !this.cameraStarting && !this.scanning;
        if (this.switchButton) this.switchButton.disabled = !this.scanning;
    }
    async prepareDecoder() {
        this.detector = null;
        if (globalThis.BarcodeDetector) {
            try {
                const formats = await BarcodeDetector.getSupportedFormats();
                if (formats.includes('qr_code')) this.detector = new BarcodeDetector({ formats: ['qr_code'] });
            } catch (_) { /* The local decoder also works when the native detector is incomplete. */ }
        }
        if (!this.detector && typeof globalThis.jsQR !== 'function') throw new Error('DECODER_UNAVAILABLE');
    }
    async startCamera(deviceId = '') {
        if (this.cameraStarting || this.scanning || this.pendingAction) return;
        if (window.isSecureContext === false) {
            this.cameraMessage('A câmera precisa de HTTPS. Abra o endereço oficial seguro ou use a busca manual.');
            return;
        }
        if (!globalThis.navigator?.mediaDevices?.getUserMedia) {
            this.cameraMessage('Este navegador não oferece acesso à câmera. Abra no Safari ou Chrome atualizado, use uma foto do QR ou a busca manual.');
            return;
        }
        const generation = ++this.cameraGeneration;
        this.cameraStarting = true;
        this.cameraControls();
        this.cameraMessage('Aguardando permissão da câmera. Permita o acesso quando o navegador perguntar.');
        try {
            await this.prepareDecoder();
            if (generation !== this.cameraGeneration) return;
            const constraints = deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: this.cameraFacing } };
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { ...constraints, width: { ideal: 1280 }, height: { ideal: 720 } } });
            } catch (error) {
                if (!['OverconstrainedError', 'NotFoundError'].includes(error.name)) throw error;
                if (generation !== this.cameraGeneration) return;
                stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: true });
            }
            if (generation !== this.cameraGeneration) { stream.getTracks().forEach(track => track.stop()); return; }
            this.stream = stream;
            this.video.srcObject = stream;
            this.video.muted = true;
            this.video.setAttribute('playsinline', '');
            await this.video.play();
            if (generation !== this.cameraGeneration) return;
            this.deviceId = stream.getVideoTracks()[0]?.getSettings?.().deviceId || '';
            try { this.devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'videoinput'); } catch (_) { this.devices = []; }
            if (generation !== this.cameraGeneration) return;
            document.getElementById('scannerPlaceholder')?.replaceChildren();
            this.scanning = true;
            this.cameraStarting = false;
            const status = document.getElementById('cameraStatus');
            if (status) status.textContent = 'Aponte para o QR da credencial. A leitura abre a ficha; nenhuma entrega é automática.';
            this.cameraControls();
            this.scanLoop(generation);
        } catch (error) {
            if (generation !== this.cameraGeneration) return;
            const messages = {
                NotAllowedError: 'Permissão da câmera negada. Libere a câmera nas permissões deste site e tente novamente; ou use uma foto do QR ou a busca manual.',
                NotReadableError: 'A câmera está ocupada ou indisponível. Feche outros aplicativos que a utilizam e tente novamente.',
                NotFoundError: 'Nenhuma câmera foi encontrada. Use uma foto do QR ou a busca por nome ou número.',
                SecurityError: 'O navegador bloqueou a câmera. Abra o site seguro diretamente no Safari ou Chrome.',
            };
            this.stopCamera(error.message === 'DECODER_UNAVAILABLE' ? 'O leitor de QR não carregou. Atualize a página ou use a busca manual.' :
                (messages[error.name] || 'Não foi possível iniciar a câmera. Tente novamente ou use uma foto do QR ou a busca manual.'));
        }
    }
    stopCamera(message = 'Câmera parada. Toque em Iniciar câmera para ler outra credencial.') {
        this.cameraGeneration += 1;
        this.scanning = false;
        this.cameraStarting = false;
        clearTimeout(this.cameraTimer);
        this.stream?.getTracks().forEach(track => track.stop());
        this.stream = null;
        this.video.pause?.();
        this.video.srcObject = null;
        this.cameraControls();
        this.cameraMessage(message);
    }
    async switchCamera() {
        if (!this.scanning) return;
        const current = this.devices.findIndex(device => device.deviceId === this.deviceId);
        const next = this.devices.length > 1 ? this.devices[(current + 1) % this.devices.length].deviceId : '';
        this.cameraFacing = this.cameraFacing === 'environment' ? 'user' : 'environment';
        this.stopCamera('Trocando câmera…');
        return this.startCamera(next);
    }
    async decodeImage(source, maxSize = 900) {
        if (this.detector) {
            try {
                const codes = await this.detector.detect(source);
                if (codes.length) return codes[0].rawValue;
            } catch (_) { this.detector = null; }
        }
        if (typeof globalThis.jsQR !== 'function') return '';
        const width = source.videoWidth || source.naturalWidth || source.width;
        const height = source.videoHeight || source.naturalHeight || source.height;
        if (!width || !height) return '';
        const scale = Math.min(1, maxSize / Math.max(width, height));
        this.canvas.width = Math.round(width * scale);
        this.canvas.height = Math.round(height * scale);
        const context = this.canvas.getContext('2d', { willReadFrequently: true });
        context.drawImage(source, 0, 0, this.canvas.width, this.canvas.height);
        const frame = context.getImageData(0, 0, this.canvas.width, this.canvas.height);
        return jsQR(frame.data, frame.width, frame.height, { inversionAttempts: 'attemptBoth' })?.data || '';
    }
    async scanLoop(generation = this.cameraGeneration) {
        if (!this.scanning || generation !== this.cameraGeneration) return;
        if (!this.pendingAction && !this.lookupPending && this.video.readyState >= 2) {
            try {
                const code = await this.decodeImage(this.video);
                if (generation !== this.cameraGeneration) return;
                if (code) {
                    this.stopCamera('QR lido. Confira o participante e confirme cada ação na ficha.');
                    this.lookupMethod = 'qr';
                    await this.lookupCode(code);
                    return;
                }
            } catch (_) { /* Wait for the next frame; never invent a successful read. */ }
        }
        if (this.scanning && generation === this.cameraGeneration) this.cameraTimer = setTimeout(() => this.scanLoop(generation), 300);
    }
    async readPhoto(file) {
        if (!file || this.pendingAction) return;
        this.stopCamera('Lendo a foto do QR neste dispositivo…');
        if (file.size > 15 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
            this.cameraMessage('Selecione uma imagem JPG, PNG ou WebP de até 15 MB.');
            return;
        }
        const generation = this.cameraGeneration;
        let objectUrl;
        try {
            await this.prepareDecoder();
            objectUrl = URL.createObjectURL(file);
            const photo = new Image();
            await new Promise((resolve, reject) => { photo.onload = resolve; photo.onerror = reject; photo.src = objectUrl; });
            if (generation !== this.cameraGeneration) return;
            if (photo.naturalWidth * photo.naturalHeight > 24000000) throw new Error('IMAGE_TOO_LARGE');
            const code = await this.decodeImage(photo, 1600);
            if (generation !== this.cameraGeneration) return;
            if (!code) { this.cameraMessage('Nenhum QR reconhecido na imagem. Aproxime, evite reflexos e tente outra foto ou busque pelo número.'); return; }
            this.cameraMessage('QR reconhecido na foto. Confira a ficha antes de confirmar a entrega.');
            this.lookupMethod = 'qr';
            await this.lookupCode(code);
        } catch (error) {
            if (generation === this.cameraGeneration) this.cameraMessage(error.message === 'IMAGE_TOO_LARGE' ? 'A foto é muito grande. Use uma imagem com até 24 megapixels.' : 'Não foi possível ler esta foto. Tente uma imagem nítida do QR ou a busca manual.');
        } finally {
            if (objectUrl) URL.revokeObjectURL(objectUrl);
            if (this.photoInput) this.photoInput.value = '';
        }
    }
    async lookupCode(code, registrationId = '') {
        const sequence = ++this.lookupSequence;
        this.lookupPending = true;
        this.resultContainer.setAttribute('aria-busy', 'true');
        try {
            const url = new URL(this.lookupUrl, window.location.href);
            if (registrationId) url.searchParams.set('registration_id', registrationId);
            else url.searchParams.set('code', code);
            const response = await fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
            if ([401, 403].includes(response.status) || response.redirected) throw new Error('ACCESS_REQUIRED');
            const data = await response.json();
            if (sequence !== this.lookupSequence) return;
            if (response.ok && data.ambiguous) {
                this.currentReg = null;
                this.renderMatches(data);
                return;
            }
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
        } catch (error) {
            if (sequence === this.lookupSequence) {
                this.currentReg = null;
                this.renderError(error.message === 'ACCESS_REQUIRED' ? 'Sua sessão expirou ou não tem acesso a esta edição. Entre novamente com uma conta autorizada.' : 'Não foi possível consultar a inscrição. Verifique sua conexão e tente novamente.');
            }
        } finally {
            if (sequence === this.lookupSequence) {
                this.lookupPending = false;
                this.resultContainer.setAttribute('aria-busy', 'false');
            }
        }
    }
    renderMatches(data) {
        const card = this.node('section', 'card');
        card.append(this.node('h2', 'section-title', 'Selecione a inscrição correta'),
            this.node('p', 'text-muted', 'Confirme nome, número e cidade com o participante.'));
        (data.matches || []).forEach(item => {
            const button = this.node('button', 'btn btn-secondary scanner-match');
            button.type = 'button';
            button.append(this.node('strong', '', '#' + (item.number || '—') + ' · ' + item.name),
                this.node('span', '', [item.city, item.team, item.category].filter(Boolean).join(' · ')));
            button.addEventListener('click', () => this.lookupCode('', item.registration_id));
            card.append(button);
        });
        if (data.more_results) card.append(this.node('p', 'text-muted', 'Há mais resultados. Digite o nome completo ou o número da inscrição para refinar.'));
        this.resultContainer.replaceChildren(card);
    }
    renderParticipantCard(data) {
        this.currentReg = data;
        const eligible = data.eligible !== false && data.commercial_state === 'ACTIVE' && ['READY', 'CHECKED_IN'].includes(data.operational_state);
        const hasCheckedIn = Boolean(data.has_checked_in);
        const card = this.node('div', 'card mg-scanner-participant');
        const heading = this.node('div', 'mg-row mg-element-gap');
        const person = this.node('div');
        person.append(
            this.node('h2', 'section-title', '#' + (data.number || '—') + ' · ' + (data.name || 'Participante')),
            this.node('p', 'text-muted mg-meta', 'Equipe: ' + (data.team || '—') + ' · Cidade: ' + (data.city || '—') + ' · Categoria: ' + (data.category || '—')),
            this.node('p', 'mg-strong mg-text-brand', 'Camiseta: ' + (data.shirt_size || '—'))
        );
        const status = hasCheckedIn ? 'Presença confirmada' : eligible ? 'Aguardando check-in' : 'Inscrição com pendência';
        heading.append(person, this.node('span', 'badge ' + (hasCheckedIn ? 'badge-success' : 'badge-warning'), status));
        card.append(heading);
        this.actionMessage = this.node('div', 'alert alert-error mg-element-gap');
        this.actionMessage.setAttribute('role', 'alert');
        this.actionMessage.hidden = true;
        card.append(this.actionMessage);
        if (!eligible) card.append(this.node('p', 'alert alert-warning mg-element-gap', 'Credenciamento e entregas indisponíveis. ' + (data.blocked_reason || 'A inscrição precisa estar ativa e liberada para participação.')));
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
            const remaining = Math.max(0, Number(entitlement.quantity_total) - Number(entitlement.quantity_used));
            const row = this.node('div', 'mg-row mg-scanner-benefit');
            const description = this.node('div');
            description.append(
                this.node('strong', '', entitlement.title),
                this.node('p', 'text-muted mg-meta', 'Entregue: ' + (entitlement.quantity_used ?? 0) + ' / ' + (entitlement.quantity_total ?? 0) + ' · Restante: ' + remaining)
            );
            row.append(description);
            const consumed = entitlement.is_consumed || Number(entitlement.quantity_used) >= Number(entitlement.quantity_total);
            if (entitlement.state === 'REVOKED') row.append(this.node('span', 'badge badge-danger', 'Revogado'));
            else if (consumed) row.append(this.node('span', 'badge badge-success', 'Retirado'));
            else if (!eligible) row.append(this.node('span', 'badge badge-warning', 'Indisponível'));
            else {
                const controls = this.node('div', 'scanner-delivery-controls');
                const pending = this.deliveryIntentions[entitlement.id];
                let quantityInput;
                if (remaining > 1) {
                    const label = this.node('label', '', 'Quantidade a entregar');
                    quantityInput = this.node('input', 'form-control scanner-quantity');
                    quantityInput.type = 'number';
                    quantityInput.min = '1';
                    quantityInput.max = String(Math.min(remaining, 1000));
                    quantityInput.value = String(pending?.quantity || 1);
                    quantityInput.readOnly = Boolean(pending);
                    label.append(quantityInput);
                    controls.append(label);
                }
                const deliver = this.node('button', 'btn btn-sm btn-secondary');
                deliver.type = 'button';
                deliver.append(this.icon('package-check'), document.createTextNode(pending ? 'Conferir entrega pendente' : remaining > 1 ? 'Confirmar entrega' : 'Entregar 1 unidade'));
                deliver.addEventListener('click', () => this.deliverBenefit(entitlement.id, deliver, pending?.quantity || Number(quantityInput?.value || 1)));
                controls.append(deliver);
                row.append(controls);
            }
            benefits.append(row);
        });
        if (!entitlements.length) benefits.append(this.node('p', 'text-muted mg-meta', 'Nenhum kit cadastrado para esta modalidade.'));
        card.append(benefits);
        const history = this.node('section', 'scanner-delivery-history');
        history.append(this.node('h3', 'section-title', 'Histórico de entregas'));
        const deliveries = Array.isArray(data.delivery_history) ? data.delivery_history : [];
        if (deliveries.length) {
            const list = this.node('ul');
            deliveries.forEach(item => list.append(this.node('li', '', item.occurred_at + ' · ' + item.quantity + ' × ' + item.title + ' · ' + (item.action === 'REVERSED' ? 'Estorno por ' : 'Entregue por ') + item.operator)));
            history.append(list, this.node('p', 'text-muted mg-meta', 'Até 20 movimentações mais recentes desta inscrição.'));
        } else history.append(this.node('p', 'text-muted', 'Nenhuma entrega registrada para esta inscrição.'));
        card.append(history);
        this.resultContainer.replaceChildren(card);
        window.renderTrilhasIcons?.(this.resultContainer);
    }
    renderError(message) {
        const alert = this.node('div', 'alert alert-error', message);
        alert.setAttribute('role', 'alert');
        this.resultContainer.replaceChildren(alert);
    }
    async submitAction(url, field, id, button, idempotencyKey = null, quantity = 1) {
        if (this.pendingAction) return;
        this.pendingAction = true;
        this.lookupSequence += 1;
        this.lookupPending = false;
        const originalNodes = [...button.childNodes].map(node => node.cloneNode(true));
        const controls = [...this.resultContainer.querySelectorAll('button'), ...this.resultContainer.querySelectorAll('input')];
        controls.forEach(control => { control.disabled = true; });
        if (this.actionMessage) this.actionMessage.hidden = true;
        button.textContent = 'Registrando…';
        const form = new FormData();
        form.append(field, id);
        form.append('lookup_method', this.lookupMethod);
        if (idempotencyKey) { form.append('idempotency_key', idempotencyKey); form.append('quantity', quantity); }
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'X-CSRFToken': this.getCSRFToken(), Accept: 'application/json' },
                body: form
            });
            if ([401, 403].includes(response.status) || response.redirected) throw new Error('Sua sessão expirou ou não possui acesso. Entre novamente antes de continuar.');
            const data = await response.json();
            if (!response.ok || !data.success) {
                // A structured refusal confirms no new delivery was committed; allow a corrected quantity.
                // Network errors, redirects and unreadable responses retain the original operation key.
                if (idempotencyKey && data.success === false && response.status >= 400 && response.status < 500) {
                    delete this.deliveryIntentions[id];
                    this.persistIntentions();
                    this.renderParticipantCard(this.currentReg);
                }
                throw new Error(data.error || 'A operação não foi concluída. Tente novamente.');
            }
            if (idempotencyKey) {
                delete this.deliveryIntentions[id];
                this.persistIntentions();
                this.deliveryIntention = null;
            }
            await this.lookupCode(this.currentCode, this.currentReg?.registration_id);
        } catch (error) {
            this.feedback(false);
            if (idempotencyKey && this.deliveryIntentions[id] && this.currentReg) {
                // Make the preserved quantity visible and immutable immediately after an uncertain response.
                this.renderParticipantCard(this.currentReg);
            }
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
    persistIntentions() {
        try { window.sessionStorage?.setItem('scanner-deliveries-' + this.eventId, JSON.stringify(this.deliveryIntentions)); } catch (_) { /* In-memory retry remains safe. */ }
    }
    deliverBenefit(entitlementId, button, quantity = 1) {
        if (this.pendingAction) return;
        if (!Number.isInteger(quantity) || quantity < 1 || quantity > 1000) {
            this.actionMessage.textContent = 'Informe uma quantidade inteira dentro do saldo disponível.';
            this.actionMessage.hidden = false;
            return;
        }
        const registrationId = this.currentReg?.registration_id;
        if (!this.deliveryIntentions[entitlementId] || this.deliveryIntentions[entitlementId].registrationId !== registrationId) {
            this.deliveryIntentions[entitlementId] = { registrationId, entitlementId, quantity, key: this.operationUUID() };
            this.persistIntentions();
        }
        this.deliveryIntention = this.deliveryIntentions[entitlementId];
        return this.submitAction(this.deliverUrl, 'entitlement_id', entitlementId, button, this.deliveryIntention.key, this.deliveryIntention.quantity);
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
        const field = document.getElementById('scannerCSRF');
        if (field?.value) return field.value;
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }
}
