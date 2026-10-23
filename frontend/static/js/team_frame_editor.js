/* Staff-only team artwork: local Canvas rendering, without upload or backup requests. */
(function () {
    'use strict';
    const FORMATS = { story: { width: 1080, height: 1920 }, feed: { width: 1080, height: 1350 } };
    const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || min));

    function imagePlacement(image, box, fit = 'contain', zoom = 100, positionX = 50, positionY = 50) {
        const width = image.naturalWidth || image.width;
        const height = image.naturalHeight || image.height;
        const scale = (fit === 'cover' ? Math.max(box.width / width, box.height / height) : Math.min(box.width / width, box.height / height)) * clamp(zoom, 100, 250) / 100;
        const renderedWidth = width * scale, renderedHeight = height * scale;
        return { x: box.x + (box.width - renderedWidth) * clamp(positionX, 0, 100) / 100,
            y: box.y + (box.height - renderedHeight) * clamp(positionY, 0, 100) / 100,
            width: renderedWidth, height: renderedHeight };
    }

    function wrappedLines(context, text, maxWidth) {
        const lines = []; let line = '';
        const words = String(text || '').trim().split(/\s+/).filter(Boolean);
        for (const word of words) {
            if (context.measureText(line ? `${line} ${word}` : word).width <= maxWidth) {
                line = line ? `${line} ${word}` : word;
                continue;
            }
            if (line) { lines.push(line); line = ''; }
            for (const character of Array.from(word)) {
                if (line && context.measureText(line + character).width > maxWidth) {
                    lines.push(line); line = '';
                }
                line += character;
            }
        }
        if (line) lines.push(line);
        return lines;
    }

    function textBlock(context, text, box, options = {}) {
        let size = options.size || 40;
        const minSize = options.minSize || 24, lineHeight = options.lineHeight || 1.16;
        let lines;
        do {
            context.font = `${options.weight || 700} ${size}px Inter, Arial, sans-serif`;
            lines = wrappedLines(context, text, box.width);
            if (lines.length * size * lineHeight <= box.height || size <= minSize) break;
            size -= 2;
        } while (true);
        const maxLines = Math.max(1, Math.floor(box.height / (size * lineHeight)));
        if (lines.length > maxLines) {
            lines = lines.slice(0, maxLines);
            let last = lines[maxLines - 1];
            while (last && context.measureText(last + '…').width > box.width) last = Array.from(last).slice(0, -1).join('');
            lines[maxLines - 1] = last + '…';
        }
        context.fillStyle = options.color || '#faf5f0';
        context.textAlign = options.align || 'center';
        context.textBaseline = 'top';
        const x = options.align === 'left' ? box.x : box.x + box.width / 2;
        lines.forEach((line, index) => context.fillText(line, x, box.y + index * size * lineHeight));
        return { lines, size };
    }

    function roundedRect(context, x, y, width, height, radius) {
        context.beginPath(); context.moveTo(x + radius, y);
        context.arcTo(x + width, y, x + width, y + height, radius);
        context.arcTo(x + width, y + height, x, y + height, radius);
        context.arcTo(x, y + height, x, y, radius);
        context.arcTo(x, y, x + width, y, radius); context.closePath();
    }

    function containedImage(context, image, box) {
        if (!image) return;
        const rectangle = imagePlacement(image, box);
        context.drawImage(image, rectangle.x, rectangle.y, rectangle.width, rectangle.height);
    }

    function drawArtwork(context, config, state, assets) {
        const dimensions = FORMATS[state.format] || FORMATS.story;
        const { width, height } = dimensions;
        const story = height === 1920;
        const background = context.createLinearGradient(0, 0, width, height);
        background.addColorStop(0, '#342219'); background.addColorStop(.45, '#171310'); background.addColorStop(1, '#0c0b0a');
        context.fillStyle = background; context.fillRect(0, 0, width, height);
        context.save(); context.globalAlpha = .1; context.fillStyle = '#d5c7bb';
        for (let y = -100; y < height + 100; y += 120) {
            context.beginPath(); context.moveTo(0, y); context.lineTo(65, y + 35); context.lineTo(65, y + 82); context.lineTo(0, y + 47); context.closePath(); context.fill();
            context.beginPath(); context.moveTo(width, y); context.lineTo(width - 65, y + 35); context.lineTo(width - 65, y + 82); context.lineTo(width, y + 47); context.closePath(); context.fill();
        }
        context.restore();
        context.fillStyle = '#f97316'; context.fillRect(0, 0, width, 9);
        containedImage(context, assets.edition, { x: story ? 180 : 270, y: story ? 55 : 25, width: story ? 720 : 540, height: story ? 340 : 220 });
        textBlock(context, config.eventTitle, { x: 100, y: story ? 418 : 266, width: 880, height: story ? 112 : 84 }, { size: story ? 44 : 34, minSize: 26, weight: 650 });
        const badgeY = story ? 556 : 365;
        context.fillStyle = '#f97316'; roundedRect(context, 230, badgeY, 620, story ? 48 : 42, 10); context.fill();
        textBlock(context, 'EQUIPE CONFIRMADA', { x: 240, y: badgeY + 8, width: 600, height: 40 }, { size: story ? 30 : 26, weight: 800, color: '#17120e' });
        const photoBox = { x: 72, y: story ? 624 : 425, width: 936, height: story ? 710 : 490 };
        context.save(); roundedRect(context, photoBox.x, photoBox.y, photoBox.width, photoBox.height, 28); context.clip();
        context.fillStyle = '#27221d'; context.fillRect(photoBox.x, photoBox.y, photoBox.width, photoBox.height);
        if (assets.photo) {
            const rectangle = imagePlacement(assets.photo, photoBox, state.fit, state.zoom, state.x, state.y);
            context.drawImage(assets.photo, rectangle.x, rectangle.y, rectangle.width, rectangle.height);
        } else if (assets.teamLogo) {
            containedImage(context, assets.teamLogo, { x: photoBox.x + 56, y: photoBox.y + 42, width: photoBox.width - 112, height: photoBox.height - 84 });
        } else {
            textBlock(context, 'Adicione a foto ou a logo da equipe', { x: 190, y: photoBox.y + photoBox.height / 2 - 50, width: 700, height: 120 }, { size: 38, color: '#b5aaa1', weight: 500 });
        }
        if (assets.photo && assets.teamLogo) {
            containedImage(context, assets.teamLogo, { x: state.logoPosition === 'left' ? photoBox.x + 28 : photoBox.x + photoBox.width - 248, y: photoBox.y + photoBox.height - 208, width: 220, height: 180 });
        }
        context.restore();
        context.strokeStyle = '#746151'; context.lineWidth = 2; roundedRect(context, photoBox.x, photoBox.y, photoBox.width, photoBox.height, 28); context.stroke();
        textBlock(context, state.team.name.toLocaleUpperCase('pt-BR'), { x: 80, y: story ? 1380 : 950, width: 920, height: story ? 230 : 170 }, { size: story ? 80 : 64, minSize: 30, weight: 800 });
        textBlock(context, state.subtitle.slice(0, 70), { x: 110, y: story ? 1632 : 1135, width: 860, height: story ? 92 : 60 }, { size: story ? 34 : 28, minSize: 24, color: '#d6c9bd', weight: 500 });
        const footerY = height - 150;
        context.fillStyle = '#675143'; context.fillRect(80, footerY, 920, 2);
        textBlock(context, config.dateLabel, { x: 80, y: footerY + 25, width: 760, height: 46 }, { size: 30, color: '#ffb27d', align: 'left' });
        textBlock(context, config.location, { x: 80, y: footerY + 72, width: 760, height: 50 }, { size: 27, minSize: 22, color: '#d6c9bd', weight: 500, align: 'left' });
        containedImage(context, assets.brand, { x: 885, y: footerY + 17, width: 115, height: 110 });
        return dimensions;
    }

    function filename(config, state) {
        const clean = value => String(value).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 75);
        return `equipe-confirmada-${clean(state.team.name) || 'equipe'}-${clean(config.eventSlug)}-${state.format}.png`;
    }

    function loadImage(source, anonymous = false) {
        return new Promise((resolve, reject) => {
            const image = new Image();
            if (anonymous) image.crossOrigin = 'anonymous';
            image.onload = () => resolve(image);
            image.onerror = () => reject(new Error('Não foi possível abrir a imagem. Use PNG, JPG ou WebP.'));
            image.src = source;
        });
    }

    async function initialize() {
        const host = document.getElementById('teamArtEditor');
        if (!host) return;
        const config = JSON.parse(document.getElementById('teamArtConfig').textContent);
        const byId = id => document.getElementById(id);
        const canvas = byId('teamArtCanvas'), context = canvas.getContext('2d');
        const status = byId('teamArtStatus'), download = byId('downloadTeamArt');
        if (!context) { status.textContent = 'Este navegador não conseguiu iniciar a prévia. Tente um navegador atualizado.'; return; }
        const assets = {}, pending = new Set(), versions = { photo: 0, teamLogo: 0 };
        let assetsReady = false, exporting = false, nextFrame = null;
        const readState = () => ({
            team: config.teams.find(team => team.id === byId('teamChoice').value) || config.teams[0],
            format: byId('teamArtFormat').value, subtitle: byId('teamArtSubtitle').value.slice(0, 70),
            fit: byId('teamPhotoFit').value, zoom: byId('teamPhotoZoom').value,
            x: byId('teamPhotoX').value, y: byId('teamPhotoY').value, logoPosition: byId('teamLogoPosition').value,
        });
        function updateDownload() { download.disabled = !assetsReady || pending.size > 0 || exporting || !(assets.photo || assets.teamLogo); }
        function draw() {
            nextFrame = null;
            const state = readState(), size = FORMATS[state.format] || FORMATS.story;
            if (canvas.width !== size.width || canvas.height !== size.height) { canvas.width = size.width; canvas.height = size.height; }
            drawArtwork(context, config, state, assets);
            canvas.setAttribute('aria-label', `Arte da equipe ${state.team.name}, ${config.eventTitle}, ${size.width} por ${size.height} pixels`);
            byId('teamArtDimensions').textContent = `${size.width} × ${size.height} px`;
            ['Zoom', 'X', 'Y'].forEach(key => { byId(`teamPhoto${key}Value`).textContent = `${byId(`teamPhoto${key}`).value}%`; });
            updateDownload();
        }
        function scheduleDraw() { if (nextFrame === null) nextFrame = requestAnimationFrame(draw); }
        function message(text, error = false) { status.textContent = text; status.classList.toggle('text-danger', error); }
        host.querySelectorAll('input:not([type="file"]),select').forEach(control => control.addEventListener('input', scheduleDraw));
        byId('teamChoice').setAttribute('aria-describedby', 'teamChoiceHelp');
        function bindImage(inputId, assetKey, removeId) {
            const input = byId(inputId), remove = byId(removeId);
            input.addEventListener('change', async () => {
                const file = input.files[0];
                if (!file) return;
                const version = ++versions[assetKey];
                pending.add(assetKey); updateDownload();
                let url;
                try {
                    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('Use uma imagem PNG, JPG ou WebP.');
                    if (file.size > config.maxUploadBytes) throw new Error('A imagem deve ter até 15 MB.');
                    url = URL.createObjectURL(file);
                    const image = await loadImage(url);
                    if (image.naturalWidth * image.naturalHeight > config.maxImagePixels) throw new Error('A imagem deve ter até 24 megapixels.');
                    if (version !== versions[assetKey]) return;
                    assets[assetKey] = image; remove.hidden = false;
                    message('Imagem pronta. Ajuste a composição e confira a prévia.');
                } catch (error) {
                    if (version !== versions[assetKey]) return;
                    input.value = '';
                    message(error.message + (assets[assetKey] ? ' A imagem anterior foi mantida.' : ''), true);
                } finally {
                    if (url) URL.revokeObjectURL(url);
                    if (version === versions[assetKey]) { pending.delete(assetKey); draw(); }
                }
            });
            remove.addEventListener('click', () => {
                versions[assetKey]++; pending.delete(assetKey); delete assets[assetKey]; input.value = ''; remove.hidden = true;
                message(assets.photo || assets.teamLogo ? 'Imagem removida. Confira a prévia atualizada.' : 'Adicione uma foto ou uma logo para baixar a arte.');
                draw();
            });
        }
        bindImage('teamPhoto', 'photo', 'removeTeamPhoto'); bindImage('teamLogo', 'teamLogo', 'removeTeamLogo');
        byId('resetTeamComposition').addEventListener('click', () => {
            byId('teamPhotoFit').value = 'contain'; byId('teamPhotoZoom').value = '100';
            byId('teamPhotoX').value = byId('teamPhotoY').value = '50'; byId('teamLogoPosition').value = 'right';
            draw(); message('Enquadramento restaurado.');
        });
        download.addEventListener('click', async () => {
            if (download.disabled) return;
            exporting = true; updateDownload(); message('Preparando a imagem PNG…');
            try {
                if (document.fonts) await document.fonts.ready;
                draw();
                const name = filename(config, readState());
                const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('Não foi possível preparar o arquivo.')), 'image/png'));
                const url = URL.createObjectURL(blob), link = document.createElement('a');
                link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
                setTimeout(() => URL.revokeObjectURL(url), 60000);
                message('Imagem preparada. Confira os downloads do navegador.');
            } catch (error) {
                message('Não foi possível baixar a imagem. Confira se a logo da edição abre normalmente e tente novamente.', true);
            } finally { exporting = false; updateDownload(); }
        });
        try {
            const [edition, brand] = await Promise.all([loadImage(config.editionLogo, true), loadImage(config.brandLogo, true)]);
            assets.edition = edition; assets.brand = brand;
            if (document.fonts) await document.fonts.ready;
            assetsReady = true; message('Escolha uma foto ou uma logo da equipe para começar.');
        } catch (error) { message('Não foi possível carregar a identidade da edição. Confira a logo em Aparência da edição e recarregue a página.', true); }
        draw();
    }

    if (typeof module !== 'undefined' && module.exports) module.exports = { FORMATS, imagePlacement, wrappedLines, textBlock, drawArtwork, filename };
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
        else initialize();
    }
})();
