/**
 * Equipe Morcego - Sistema Trilhas
 * Utilitários principais JavaScript
 */

function copyToClipboard(text, btnElement, successMsg = "Copiado!") {
    if (!navigator.clipboard) {
        // Fallback para navegadores legados
        const textArea = document.createElement("textarea");
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            if (btnElement) {
                const originalText = btnElement.innerText;
                btnElement.innerText = successMsg;
                setTimeout(() => { btnElement.innerText = originalText; }, 2000);
            }
        } catch (err) {
            console.error('Falha ao copiar:', err);
        }
        document.body.removeChild(textArea);
        return;
    }

    navigator.clipboard.writeText(text).then(() => {
        if (btnElement) {
            const originalText = btnElement.innerText;
            btnElement.innerText = successMsg;
            btnElement.classList.add("btn-success");
            setTimeout(() => {
                btnElement.innerText = originalText;
                btnElement.classList.remove("btn-success");
            }, 2000);
        }
    }).catch(err => {
        console.error("Erro ao copiar para a área de transferência:", err);
    });
}

// Feedback sonoro para o scanner de credenciamento
const AudioFeedback = {
    ctx: null,
    init() {
        if (!this.ctx && (window.AudioContext || window.webkitAudioContext)) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            this.ctx = new AudioContextClass();
        }
    },
    playSuccess() {
        try {
            this.init();
            if (!this.ctx) return;
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();
            osc.connect(gain);
            gain.connect(this.ctx.destination);
            osc.frequency.setValueAtTime(587.33, this.ctx.currentTime); // D5
            osc.frequency.setValueAtTime(880, this.ctx.currentTime + 0.1); // A5
            gain.gain.setValueAtTime(0.2, this.ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.35);
            osc.start(this.ctx.currentTime);
            osc.stop(this.ctx.currentTime + 0.35);
        } catch (e) {
            console.log("Audio feedback error", e);
        }
    },
    playError() {
        try {
            this.init();
            if (!this.ctx) return;
            const osc = this.ctx.createOscillator();
            const gain = this.ctx.createGain();
            osc.connect(gain);
            gain.connect(this.ctx.destination);
            osc.type = "sawtooth";
            osc.frequency.setValueAtTime(160, this.ctx.currentTime);
            gain.gain.setValueAtTime(0.25, this.ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + 0.4);
            osc.start(this.ctx.currentTime);
            osc.stop(this.ctx.currentTime + 0.4);
        } catch (e) {
            console.log("Audio feedback error", e);
        }
    }
};
