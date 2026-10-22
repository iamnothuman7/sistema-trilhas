/**
 * Equipe Morcego - Gerador Interativo de Molduras Digitais (Canvas 2D)
 * 100% Client-Side / Nenhuma foto é enviada ao servidor
 */

class FrameCanvasEditor {
    constructor(canvasId, options = {}) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas.getContext("2d");

        this.width = options.width || 1080;
        this.height = options.height || 1350;
        this.canvas.width = this.width;
        this.canvas.height = this.height;

        this.overlayImg = new Image();
        this.overlayImg.crossOrigin = "anonymous";
        this.userImg = null;

        // Transformações da imagem do usuário
        this.scale = 1.0;
        this.rotation = 0; // em graus
        this.panX = 0;
        this.panY = 0;

        // Textos opcionais
        this.pilotName = "";
        this.teamName = "";
        this.textColor = options.textColor || "#FFFFFF";
        this.textY = options.textY || (this.height - 180);

        // Estado de arraste
        this.isDragging = false;
        this.dragStartX = 0;
        this.dragStartY = 0;

        this.initEvents();
    }

    loadOverlay(url) {
        return new Promise((resolve, reject) => {
            this.overlayImg.onload = () => {
                this.render();
                resolve();
            };
            this.overlayImg.onerror = reject;
            this.overlayImg.src = url;
        });
    }

    loadUserPhoto(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const img = new Image();
            img.onload = () => {
                this.userImg = img;
                // Centralizar foto inicialmente cobrindo a moldura
                const scaleX = this.width / img.width;
                const scaleY = this.height / img.height;
                this.scale = Math.max(scaleX, scaleY);
                this.panX = (this.width - img.width * this.scale) / 2;
                this.panY = (this.height - img.height * this.scale) / 2;
                this.rotation = 0;
                this.render();
            };
            img.src = e.target.result;
        };
        reader.readAsDataURL(file);
    }

    initEvents() {
        // Suporte a Mouse Drag
        this.canvas.addEventListener("mousedown", (e) => {
            if (!this.userImg) return;
            this.isDragging = true;
            this.dragStartX = e.offsetX;
            this.dragStartY = e.offsetY;
        });

        window.addEventListener("mousemove", (e) => {
            if (!this.isDragging || !this.userImg) return;
            const rect = this.canvas.getBoundingClientRect();
            const scaleFactor = this.width / rect.width;
            const currentX = (e.clientX - rect.left);
            const currentY = (e.clientY - rect.top);

            const dx = (currentX - this.dragStartX) * scaleFactor;
            const dy = (currentY - this.dragStartY) * scaleFactor;

            this.panX += dx;
            this.panY += dy;

            this.dragStartX = currentX;
            this.dragStartY = currentY;
            this.render();
        });

        window.addEventListener("mouseup", () => {
            this.isDragging = false;
        });

        // Suporte a Touch Drag (Celular)
        this.canvas.addEventListener("touchstart", (e) => {
            if (!this.userImg || e.touches.length !== 1) return;
            this.isDragging = true;
            const rect = this.canvas.getBoundingClientRect();
            this.dragStartX = e.touches[0].clientX - rect.left;
            this.dragStartY = e.touches[0].clientY - rect.top;
        }, { passive: false });

        this.canvas.addEventListener("touchmove", (e) => {
            if (!this.isDragging || !this.userImg || e.touches.length !== 1) return;
            e.preventDefault();
            const rect = this.canvas.getBoundingClientRect();
            const scaleFactor = this.width / rect.width;
            const currentX = e.touches[0].clientX - rect.left;
            const currentY = e.touches[0].clientY - rect.top;

            const dx = (currentX - this.dragStartX) * scaleFactor;
            const dy = (currentY - this.dragStartY) * scaleFactor;

            this.panX += dx;
            this.panY += dy;

            this.dragStartX = currentX;
            this.dragStartY = currentY;
            this.render();
        }, { passive: false });

        this.canvas.addEventListener("touchend", () => {
            this.isDragging = false;
        });
    }

    render() {
        this.ctx.clearRect(0, 0, this.width, this.height);

        // 1. Fundo neutro
        this.ctx.fillStyle = "#0B0F19";
        this.ctx.fillRect(0, 0, this.width, this.height);

        // 2. Foto do Piloto (com transformações de Zoom, Rotação e Posição)
        if (this.userImg) {
            this.ctx.save();
            const centerX = this.panX + (this.userImg.width * this.scale) / 2;
            const centerY = this.panY + (this.userImg.height * this.scale) / 2;

            this.ctx.translate(centerX, centerY);
            this.ctx.rotate((this.rotation * Math.PI) / 180);
            this.ctx.scale(this.scale, this.scale);
            this.ctx.drawImage(
                this.userImg,
                -this.userImg.width / 2,
                -this.userImg.height / 2
            );
            this.ctx.restore();
        } else {
            // Placeholder orientativo
            this.ctx.fillStyle = "#9CA3AF";
            this.ctx.font = "bold 36px sans-serif";
            this.ctx.textAlign = "center";
            this.ctx.fillText("Selecione sua foto abaixo para começar", this.width / 2, this.height / 2);
        }

        // 3. Moldura Transparente Oficial por cima
        if (this.overlayImg.complete && this.overlayImg.naturalWidth > 0) {
            this.ctx.drawImage(this.overlayImg, 0, 0, this.width, this.height);
        }

        // 4. Textos Personalizados (Nome do Piloto e Equipe)
        if (this.pilotName || this.teamName) {
            this.ctx.save();
            this.ctx.textAlign = "center";
            this.ctx.shadowColor = "rgba(0, 0, 0, 0.9)";
            this.ctx.shadowBlur = 12;

            if (this.pilotName) {
                this.ctx.font = "900 44px sans-serif";
                this.ctx.fillStyle = this.textColor;
                this.ctx.fillText(this.pilotName.toUpperCase(), this.width / 2, this.textY);
            }

            if (this.teamName) {
                this.ctx.font = "bold 30px sans-serif";
                this.ctx.fillStyle = "#F59E0B";
                this.ctx.fillText(this.teamName.toUpperCase(), this.width / 2, this.textY + 44);
            }
            this.ctx.restore();
        }
    }

    download(filename = "minha-moldura-trilhao.png") {
        const link = document.createElement("a");
        link.download = filename;
        link.href = this.canvas.toDataURL("image/png", 1.0);
        link.click();
    }
}
