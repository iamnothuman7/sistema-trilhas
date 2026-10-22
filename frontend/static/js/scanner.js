/**
 * Equipe Morcego - Scanner Móvel de Credenciamento
 * Leitura de QR Code e entrega rápida de kits com proteção contra duplicidade
 */

class EventScanner {
    constructor(eventId, lookupUrl, confirmUrl, deliverUrl) {
        this.eventId = eventId;
        this.lookupUrl = lookupUrl;
        this.confirmUrl = confirmUrl;
        this.deliverUrl = deliverUrl;

        this.video = document.getElementById("scannerVideo");
        this.stream = null;
        this.scanning = false;
        this.currentReg = null;

        this.initElements();
    }

    initElements() {
        this.resultContainer = document.getElementById("scannerResult");
        this.manualInput = document.getElementById("manualCodeInput");
        this.manualBtn = document.getElementById("btnManualSearch");

        if (this.manualBtn) {
            this.manualBtn.addEventListener("click", () => {
                const code = this.manualInput.value.trim();
                if (code) this.lookupCode(code);
            });
        }

        if (this.manualInput) {
            this.manualInput.addEventListener("keypress", (e) => {
                if (e.key === "Enter") {
                    const code = this.manualInput.value.trim();
                    if (code) this.lookupCode(code);
                }
            });
        }
    }

    async startCamera() {
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "environment" }
            });
            this.video.srcObject = this.stream;
            this.video.setAttribute("playsinline", true);
            await this.video.play();
            this.scanning = true;
            this.scanLoop();
        } catch (err) {
            console.warn("Não foi possível acessar a câmera traseira:", err);
            // Mensagem amigável na interface
            const placeholder = document.getElementById("scannerPlaceholder");
            if (placeholder) {
                placeholder.innerHTML = `<p style="color: #9CA3AF; padding: 2rem;">Câmera não disponível no dispositivo ou permissão negada. Utilize o campo de busca rápida abaixo por número ou nome.</p>`;
            }
        }
    }

    stopCamera() {
        this.scanning = false;
        if (this.stream) {
            this.stream.getTracks().forEach(track => track.stop());
        }
    }

    async scanLoop() {
        if (!this.scanning) return;

        // Se o navegador possuir a API nativa BarcodeDetector
        if ("BarcodeDetector" in window) {
            try {
                const detector = new BarcodeDetector({ formats: ["qr_code"] });
                const barcodes = await detector.detect(this.video);
                if (barcodes.length > 0) {
                    const code = barcodes[0].rawValue;
                    this.lookupCode(code);
                    // Pausa 3 segundos para evitar múltiplas leituras
                    await new Promise(r => setTimeout(r, 2500));
                }
            } catch (e) {
                console.error("Erro no BarcodeDetector:", e);
            }
        }

        if (this.scanning) {
            requestAnimationFrame(() => this.scanLoop());
        }
    }

    async lookupCode(code) {
        try {
            const url = `${this.lookupUrl}?code=${encodeURIComponent(code)}`;
            const res = await fetch(url);
            const data = await res.json();

            if (!data.found) {
                AudioFeedback.playError();
                this.renderError(data.error || "Inscrição não encontrada.");
                return;
            }

            AudioFeedback.playSuccess();
            this.currentReg = data;
            this.renderParticipantCard(data);
        } catch (err) {
            console.error("Erro na busca de inscrição:", err);
            this.renderError("Erro de comunicação com o servidor.");
        }
    }

    renderParticipantCard(data) {
        const isReady = data.operational_state === "READY" || data.operational_state === "CHECKED_IN";
        const hasCheckedIn = data.has_checked_in;

        let benefitsHtml = "";
        data.entitlements.forEach(ent => {
            const isDone = ent.is_consumed;
            benefitsHtml += `
                <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 0; border-bottom: 1px solid var(--border);">
                    <div>
                        <div style="font-weight: 700; color: #FFF;">${ent.title}</div>
                        <div style="font-size: 0.8rem; color: var(--text-muted);">
                            Entregue: <span id="used-${ent.id}">${ent.quantity_used}</span> / ${ent.quantity_total}
                        </div>
                    </div>
                    <div>
                        ${isDone 
                            ? `<span class="badge badge-success">Já Retirado</span>`
                            : `<button class="btn btn-sm btn-primary" onclick="scannerApp.deliverBenefit('${ent.id}', this)">Entregar</button>`
                        }
                    </div>
                </div>
            `;
        });

        this.resultContainer.innerHTML = `
            <div class="card" style="border-color: ${hasCheckedIn ? 'var(--secondary)' : 'var(--primary)'};">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem;">
                    <div>
                        <div style="font-size: 1.4rem; font-weight: 900; color: #FFF;">
                            #${data.number || '---'} - ${data.name}
                        </div>
                        <div style="color: var(--text-muted); font-size: 0.95rem;">
                            Equipe: <strong>${data.team}</strong> | Categoria: <strong>${data.category}</strong>
                        </div>
                        <div style="color: var(--primary); font-weight: 700; margin-top: 0.25rem;">
                            Camiseta: Tamanho ${data.shirt_size}
                        </div>
                    </div>
                    <div>
                        <span class="badge ${hasCheckedIn ? 'badge-success' : 'badge-warning'}">
                            ${hasCheckedIn ? 'PRESENÇA CONFIRMADA' : 'AGUARDANDO CHECK-IN'}
                        </span>
                    </div>
                </div>

                <!-- Botão de Presença -->
                <div style="margin-bottom: 1.5rem;">
                    ${hasCheckedIn
                        ? `<div class="alert alert-success" style="padding: 0.6rem 1rem; margin-bottom: 0;">Presença registrada às ${data.checked_in_at}</div>`
                        : `<button class="btn btn-success btn-block btn-lg" onclick="scannerApp.confirmCheckin('${data.registration_id}', this)">
                            Confirmar Presença (Check-in)
                           </button>`
                    }
                </div>

                <!-- Lista de Benefícios e Kits -->
                <div style="background-color: var(--surface-hover); padding: 1rem; border-radius: var(--radius-md);">
                    <div style="font-size: 0.85rem; font-weight: 800; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.5rem;">
                        Entregas de Kits e Materiais
                    </div>
                    ${benefitsHtml || '<p style="color: var(--text-muted);">Nenhum kit cadastrado para esta modalidade.</p>'}
                </div>
            </div>
        `;
    }

    renderError(msg) {
        this.resultContainer.innerHTML = `
            <div class="alert alert-error" style="text-align: center;">
                <strong>Atenção:</strong> ${msg}
            </div>
        `;
    }

    async confirmCheckin(regId, btn) {
        btn.disabled = true;
        btn.innerText = "Registrando...";

        const formData = new FormData();
        formData.append("registration_id", regId);

        try {
            const res = await fetch(this.confirmUrl, {
                method: "POST",
                headers: { "X-CSRFToken": this.getCSRFToken() },
                body: formData
            });
            const data = await res.json();
            if (data.success) {
                AudioFeedback.playSuccess();
                this.lookupCode(this.currentReg.number || this.currentReg.name);
            }
        } catch (e) {
            AudioFeedback.playError();
            btn.disabled = false;
            btn.innerText = "Confirmar Presença";
        }
    }

    async deliverBenefit(entId, btn) {
        btn.disabled = true;
        btn.innerText = "...";

        const formData = new FormData();
        formData.append("entitlement_id", entId);

        try {
            const res = await fetch(this.deliverUrl, {
                method: "POST",
                headers: { "X-CSRFToken": this.getCSRFToken() },
                body: formData
            });
            const data = await res.json();
            if (data.success) {
                AudioFeedback.playSuccess();
                btn.outerHTML = `<span class="badge badge-success">Entregue Agora</span>`;
                const counter = document.getElementById(`used-${entId}`);
                if (counter) counter.innerText = data.quantity_used;
            } else {
                AudioFeedback.playError();
                alert(data.error);
                btn.disabled = false;
                btn.innerText = "Entregar";
            }
        } catch (e) {
            AudioFeedback.playError();
            btn.disabled = false;
            btn.innerText = "Entregar";
        }
    }

    getCSRFToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : "";
    }
}
