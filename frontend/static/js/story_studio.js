(() => {
  'use strict';

  function initializeStudio(previewCanvas = null) {
    const previewOnly = Boolean(previewCanvas);
    const element = id => previewOnly ? null : document.getElementById(id);
    const canvas = previewCanvas || element('story-canvas');
    if (!canvas) return;

    const context = canvas.getContext('2d');
    const internalEditor = canvas.dataset.internalEditor === 'true';
    const studio = previewCanvas || document.querySelector('.story-studio');
    const input = element('photo-input');
    const uploadButton = element('upload-button');
    const downloadButton = element('download-button');
    const changeButton = element('change-photo-button');
    const resetButton = element('reset-button');
    const zoomInput = element('zoom-input');
    const controls = element('photo-controls');
    const status = element('editor-status');
    const uploadLabel = element('upload-label');
    const help = element('photo-help');
    const exportNote = element('export-note');
    const downloadLabel = element('download-label');
    const pilotNameInput = element('pilot-name-input');
    const teamNameInput = element('team-name-input');
    const registrationSelect = element('story-registration');
    const subjectSelect = element('artwork-subject');
    const centerButton = element('center-photo-button');
    const rememberPhoto = element('remember-photo');
    const photoDraftKey = () => registrationSelect?.value || 'trilhao_guest_photo';
    const isTeamArtwork = () => internalEditor && subjectSelect?.value === 'team';

    const WIDTH = 1080;
    let activeFormat = (canvas.dataset.format || studio?.dataset.format) === 'feed' ? 'feed' : 'story';
    let HEIGHT = activeFormat === 'feed' ? 1350 : 1920;
    const formatButtons = previewOnly ? [] : [...document.querySelectorAll('[data-art-format]')];
    const versionsEl = document.getElementById(previewOnly ? 'frame-catalog-version' : 'event-versions');
    const versions = versionsEl ? (previewOnly ? [JSON.parse(versionsEl.textContent)] : JSON.parse(versionsEl.textContent)) : [];

    let activeVersion = versions.find(v => v.id === studio?.dataset.version) || versions[0] || {
      id: 'trilhao-fogo',
      overlay_story: '/static/img/frames/frame_story_2027.png',
      overlay_feed: '/static/img/frames/frame_feed_2027.png',
      layout: {
        photo: { x: 73, y: 325, width: 933, height: 1015 },
        cut: 25,
        pilot_box: { x: 250, y: 1528, max_width: 725, font_size: 44 },
        team_box: { x: 260, y: 1641, max_width: 715, font_size: 38 },
        overlay: '/static/img/frames/frame_story_2027.png',
      },
      feed_layout: {
        photo: { x: 167, y: 189, width: 746, height: 690 },
        cut: 22,
        pilot_box: { x: 265, y: 1032, max_width: 670, font_size: 34 },
        team_box: { x: 270, y: 1111, max_width: 665, font_size: 30 },
        overlay: '/static/img/frames/frame_feed_2027.png',
      }
    };

    let activeLayout = activeFormat === 'feed' ? activeVersion.feed_layout : activeVersion.layout;
    let PHOTO = { ...activeLayout.photo };
    let CUT = activeLayout.cut || (activeFormat === 'feed' ? 22 : 25);
    let PILOT_BOX = activeLayout.pilot_box;
    let TEAM_BOX = activeLayout.team_box;

    const imageCache = new Map();
    let editionOverlay = null;
    let editionReady = false;
    let editionRequest = 0;
    const framingByLayout = new Map();
    const TYPE = '"Barlow Condensed", "Inter", "Impact", "Arial Narrow", sans-serif';

    canvas.width = WIDTH;
    canvas.height = HEIGHT;
    canvas.style.setProperty('--art-ratio', `1080/${HEIGHT}`);

    const state = {
      photo: null,
      normalizedPhoto: null,
      framingTouched: false,
      pilotName: pilotNameInput?.value.trim() || (registrationSelect?.selectedOptions[0]?.dataset.name || ''),
      teamName: teamNameInput?.value.trim() || (registrationSelect?.selectedOptions[0]?.dataset.team || ''),
      zoom: 1,
      offsetX: 0,
      offsetY: 0,
      busy: false,
      exporting: false,
      request: 0,
      frame: 0,
      drag: null,
    };

    function announce(message, isError = false) {
      if (status) {
        status.textContent = message;
        status.dataset.state = isError ? 'error' : 'success';
      }
    }

    function artworkIsReady() {
      return editionReady && Boolean(editionOverlay);
    }

    function syncControls() {
      if (previewOnly) return;
      const hasPhoto = Boolean(state.photo);
      const isBusy = state.busy || state.exporting;
      if (registrationSelect) registrationSelect.disabled = isBusy;
      document.body.classList.toggle('has-photo', hasPhoto);
      canvas.classList.toggle('is-editable', hasPhoto);
      canvas.style.touchAction = hasPhoto ? 'none' : 'pan-y';
      canvas.setAttribute('aria-busy', String(isBusy || !artworkIsReady()));

      const previewTag = document.querySelector('.preview-tag');
      if (previewTag) {
        previewTag.textContent = editionReady ? `PRÉVIA DO ${activeFormat.toUpperCase()}` : 'Carregando moldura…';
      }
      if (controls) controls.hidden = !hasPhoto;

      formatButtons.forEach(button => {
        button.disabled = state.exporting;
        button.setAttribute('aria-pressed', String(button.dataset.artFormat === activeFormat));
      });

      if (teamNameInput) teamNameInput.disabled = state.exporting;
      if (pilotNameInput) pilotNameInput.disabled = state.exporting;
      if (subjectSelect) subjectSelect.disabled = isBusy;
      if (centerButton) centerButton.disabled = isBusy;

      const formatLabel = document.querySelector('.studio-format');
      if (formatLabel) formatLabel.textContent = activeFormat === 'feed' ? '4:5' : '9:16';

      if (uploadButton) uploadButton.disabled = isBusy;
      if (changeButton) { changeButton.disabled = isBusy; changeButton.hidden = !hasPhoto; }
      if (resetButton) { resetButton.disabled = state.exporting; resetButton.hidden = !hasPhoto; }

      if (downloadButton) {
        downloadButton.disabled = !hasPhoto || isBusy || !artworkIsReady();
      }
      if (downloadLabel) {
        downloadLabel.textContent = state.exporting
          ? 'Gerando PNG Alta Definição…'
          : `Baixar ${activeFormat === 'feed' ? 'Feed (1080 × 1350)' : 'Story (1080 × 1920)'}`;
      }
      if (zoomInput) {
        zoomInput.disabled = isBusy || !hasPhoto;
        zoomInput.value = String(state.zoom);
      }
      if (uploadLabel) {
        uploadLabel.textContent = state.busy
          ? 'Carregando foto…'
          : (hasPhoto ? 'Trocar foto' : 'Adicionar minha foto');
      }
      if (help) {
        help.textContent = hasPhoto
          ? 'Arraste a foto na prévia ou use o zoom para ajustar o enquadramento.'
          : 'Escolha uma foto sua ou na trilha para começar.';
      }
      if (exportNote) {
        exportNote.textContent = state.exporting
          ? 'Preparando seu arquivo PNG…'
          : `PNG Alta Qualidade · 1080 × ${HEIGHT} px`;
      }
    }

    function chamferPath(ctx, x, y, width, height, cut = 24) {
      ctx.beginPath();
      ctx.moveTo(x + cut, y);
      ctx.lineTo(x + width - cut, y);
      ctx.lineTo(x + width, y + cut);
      ctx.lineTo(x + width, y + height - cut);
      ctx.lineTo(x + width - cut, y + height);
      ctx.lineTo(x + cut, y + height);
      ctx.lineTo(x, y + height - cut);
      ctx.lineTo(x, y + cut);
      ctx.closePath();
    }

    function text(ctx, value, x, y, size, color, weight = 800, align = 'left', maxWidth = undefined) {
      ctx.save();
      ctx.fillStyle = color;
      ctx.textAlign = align;
      ctx.font = `${weight} ${size}px ${TYPE}`;
      if (maxWidth) ctx.fillText(value, x, y, maxWidth);
      else ctx.fillText(value, x, y);
      ctx.restore();
    }

    function drawBoxText(ctx, textVal, startX, centerY, maxWidth, baseFontSize) {
      if (!textVal) return;
      ctx.save();
      ctx.fillStyle = '#FFFFFF';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';

      let fontSize = baseFontSize;
      ctx.font = `800 ${fontSize}px ${TYPE}`;
      let measuredWidth = ctx.measureText(textVal).width;
      if (measuredWidth > maxWidth && measuredWidth > 0) {
        fontSize = Math.max(18, Math.floor(baseFontSize * (maxWidth / measuredWidth)));
        ctx.font = `800 ${fontSize}px ${TYPE}`;
      }

      ctx.fillText(textVal, startX, centerY);
      ctx.restore();
    }

    function loadImage(source) {
      if (!source) return Promise.resolve(null);
      const cached = imageCache.get(source);
      if (cached) return cached.promise;
      const image = new Image();
      image.decoding = 'async';
      const promise = new Promise((resolve, reject) => {
        image.onload = () => resolve(image);
        image.onerror = (e) => {
          imageCache.delete(source);
          reject(e);
        };
        image.src = source;
      });
      imageCache.set(source, { image, promise });
      return promise;
    }

    function imageSize() {
      return {
        width: state.photo?.naturalWidth || state.photo?.width || 1,
        height: state.photo?.naturalHeight || state.photo?.height || 1,
      };
    }

    function getPlacement() {
      const size = imageSize();
      const coverScale = Math.max(PHOTO.width / size.width, PHOTO.height / size.height);
      const scale = coverScale * state.zoom;
      const width = size.width * scale;
      const height = size.height * scale;
      const baseX = PHOTO.x + (PHOTO.width - width) / 2;
      const baseY = PHOTO.y + (PHOTO.height - height) / 2;

      const maxDeltaX = Math.max(0, (width - PHOTO.width) / 2);
      const maxDeltaY = Math.max(0, (height - PHOTO.height) / 2);
      state.offsetX = Math.max(-maxDeltaX, Math.min(maxDeltaX, state.offsetX));
      state.offsetY = Math.max(-maxDeltaY, Math.min(maxDeltaY, state.offsetY));

      return { x: baseX + state.offsetX, y: baseY + state.offsetY, width, height };
    }

    function render() {
      state.frame = 0;
      context.clearRect(0, 0, WIDTH, HEIGHT);

      // 1. Dark background
      context.fillStyle = '#0a0a0a';
      context.fillRect(0, 0, WIDTH, HEIGHT);

      // 2. Photo Layer
      if (state.photo) {
        const placement = getPlacement();
        context.save();
        chamferPath(context, PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height, CUT);
        context.clip();
        context.fillStyle = '#101210';
        context.fillRect(PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = 'high';
        context.drawImage(state.photo, placement.x, placement.y, placement.width, placement.height);
        context.restore();
      } else {
        context.save();
        chamferPath(context, PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height, CUT);
        context.clip();
        context.fillStyle = '#140c08';
        context.fillRect(PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height);

        // Elegant geometric camera badge
        const cx = PHOTO.x + PHOTO.width / 2;
        const cy = PHOTO.y + PHOTO.height * 0.44;
        context.strokeStyle = 'rgba(249, 115, 22, 0.45)';
        context.fillStyle = 'rgba(249, 115, 22, 0.12)';
        context.lineWidth = 4;
        context.beginPath();
        context.arc(cx, cy, 54, 0, Math.PI * 2);
        context.fill();
        context.stroke();

        // Stylized geometric cross/plus
        context.strokeStyle = '#f97316';
        context.lineWidth = 6;
        context.lineCap = 'round';
        context.beginPath();
        context.moveTo(cx - 20, cy);
        context.lineTo(cx + 20, cy);
        context.moveTo(cx, cy - 20);
        context.lineTo(cx, cy + 20);
        context.stroke();

        text(context, 'ADICIONE SUA FOTO', cx, PHOTO.y + PHOTO.height * 0.58, activeFormat === 'feed' ? 32 : 38, '#f97316', 900, 'center');
        text(context, 'Toque no botão abaixo ou arraste uma foto', cx, PHOTO.y + PHOTO.height * 0.64, activeFormat === 'feed' ? 18 : 22, 'rgba(255, 255, 255, 0.65)', 600, 'center');
        context.restore();
      }

      // 3. Official Frame Overlay
      if (editionOverlay) {
        context.drawImage(editionOverlay, 0, 0, WIDTH, HEIGHT);
      }

      // 4. Pilot Name text in PILOTO: box
      const pilotName = (previewOnly
        ? 'PILOTO EXEMPLO'
        : (isTeamArtwork() ? '' : (state.pilotName || ''))
      ).trim().toUpperCase();

      if (pilotName && PILOT_BOX) {
        drawBoxText(context, pilotName, PILOT_BOX.x, PILOT_BOX.y, PILOT_BOX.max_width, PILOT_BOX.font_size);
      }

      // 5. Team Name text in EQUIPE: box
      const teamName = (previewOnly
        ? 'EQUIPE MORCEGO'
        : (state.teamName || '')
      ).trim().toUpperCase();

      if (teamName && TEAM_BOX) {
        drawBoxText(context, teamName, TEAM_BOX.x, TEAM_BOX.y, TEAM_BOX.max_width, TEAM_BOX.font_size);
      }
    }

    function scheduleRender() {
      if (!state.frame) state.frame = window.requestAnimationFrame(render);
    }

    function releasePhoto(photo) {
      if (photo && typeof photo.close === 'function') photo.close();
    }

    async function decodePhoto(file) {
      if ('createImageBitmap' in window) {
        try {
          return await createImageBitmap(file, { imageOrientation: 'from-image' });
        } catch (_) {}
      }
      const url = URL.createObjectURL(file);
      try {
        const image = new Image();
        image.decoding = 'async';
        await new Promise((resolve, reject) => {
          image.onload = resolve;
          image.onerror = reject;
          image.src = url;
        });
        return image;
      } finally {
        URL.revokeObjectURL(url);
      }
    }

    async function normalizePhoto(decoded) {
      const width = decoded.naturalWidth || decoded.width;
      const height = decoded.naturalHeight || decoded.height;
      const scale = Math.min(1, 2048 / Math.max(width, height));
      const normalized = document.createElement('canvas');
      normalized.width = Math.max(1, Math.round(width * scale));
      normalized.height = Math.max(1, Math.round(height * scale));
      const ctx = normalized.getContext('2d');
      ctx.imageSmoothingQuality = 'high';
      ctx.drawImage(decoded, 0, 0, normalized.width, normalized.height);
      return new Promise((resolve, reject) => normalized.toBlob(
        result => result ? resolve(result) : reject(new Error('Não foi possível preparar essa foto.')), 'image/png'));
    }

    async function useFile(file) {
      if (!file) return;
      const request = ++state.request;
      state.busy = true;
      syncControls();
      announce('Carregando foto…');
      let decoded;
      try {
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
          throw new Error('Use uma imagem JPG, PNG ou WebP.');
        }
        if (file.size > 15 * 1024 * 1024) throw new Error('A imagem deve ter até 15 MB.');
        decoded = await decodePhoto(file);
        if ((decoded.naturalWidth || decoded.width) * (decoded.naturalHeight || decoded.height) > 40_000_000) {
          throw new Error('A imagem deve ter até 40 megapixels.');
        }
        const blob = await normalizePhoto(decoded);
        releasePhoto(decoded);
        decoded = null;
        const photo = await decodePhoto(blob);
        if (request !== state.request) { releasePhoto(photo); return; }
        releasePhoto(state.photo);
        state.photo = photo;
        state.normalizedPhoto = blob;
        state.zoom = 1;
        state.offsetX = 0;
        state.offsetY = 0;
        state.framingTouched = false;
        framingByLayout.clear();
        state.busy = false;
        syncControls();
        render();
        announce('Foto enquadrada! Arraste na moldura ou ajuste o zoom.');
        if (rememberPhoto?.checked && photoDraftKey()) {
          const saved = await window.TrilhasPhotoDraft?.save(photoDraftKey(), blob);
          if (!saved) announce('Foto pronta para baixar.');
        }
      } catch (error) {
        if (request !== state.request) return;
        state.busy = false;
        syncControls();
        announce(error.message || 'Não foi possível abrir essa foto.', true);
      } finally {
        releasePhoto(decoded);
        if (request === state.request && input) input.value = '';
      }
    }

    function choosePhoto() {
      if (!state.busy && !state.exporting && input) {
        input.click();
      }
    }

    function reset() {
      framingByLayout.clear();
      state.request += 1;
      releasePhoto(state.photo);
      state.photo = null;
      state.normalizedPhoto = null;
      state.zoom = 1;
      state.offsetX = 0;
      state.offsetY = 0;
      state.busy = false;
      state.drag = null;
      if (input) input.value = '';
      syncControls();
      render();
      announce('Tudo pronto para uma nova foto.');
    }

    function canvasPoint(event) {
      const bounds = canvas.getBoundingClientRect();
      return {
        x: (event.clientX - bounds.left) * WIDTH / bounds.width,
        y: (event.clientY - bounds.top) * HEIGHT / bounds.height,
      };
    }

    function centerPhoto() {
      state.zoom = 1;
      state.offsetX = 0;
      state.offsetY = 0;
      syncControls();
      scheduleRender();
    }

    function loadEditionArtwork() {
      const request = ++editionRequest;
      const version = activeVersion;
      const overlayUrl = activeLayout?.overlay
        || (activeFormat === 'feed' ? version.overlay_feed : version.overlay_story)
        || (activeFormat === 'feed' ? '/static/img/frames/frame_feed_2027.png' : '/static/img/frames/frame_story_2027.png');

      editionReady = false;
      syncControls();

      loadImage(overlayUrl).then(img => {
        if (request !== editionRequest) return;
        editionOverlay = img;
        editionReady = true;
        render();
        syncControls();
      }).catch(err => {
        console.warn('Tentando fallback para overlay:', err);
        const fallbackUrl = activeFormat === 'feed' ? '/static/img/frames/frame_feed_2027.png' : '/static/img/frames/frame_story_2027.png';
        loadImage(fallbackUrl).then(img => {
          if (request !== editionRequest) return;
          editionOverlay = img;
          editionReady = true;
          render();
          syncControls();
        }).catch(e => {
          console.error('Falha crítica ao carregar moldura:', e);
          syncControls();
        });
      });
    }

    function framingKey() {
      return `${activeVersion.id || 'default'}:${activeFormat}`;
    }

    function saveFraming() {
      framingByLayout.set(framingKey(), { zoom: state.zoom, offsetX: state.offsetX, offsetY: state.offsetY });
    }

    function applyFormatLayout() {
      activeLayout = activeFormat === 'feed' ? activeVersion.feed_layout : activeVersion.layout;
      PHOTO = { ...activeLayout.photo };
      CUT = activeLayout.cut || (activeFormat === 'feed' ? 22 : 25);
      PILOT_BOX = activeLayout.pilot_box;
      TEAM_BOX = activeLayout.team_box;
      HEIGHT = activeFormat === 'feed' ? 1350 : 1920;
      canvas.width = WIDTH;
      canvas.height = HEIGHT;
      canvas.style.setProperty('--art-ratio', `1080/${HEIGHT}`);
      const framing = framingByLayout.get(framingKey());
      state.zoom = framing?.zoom || 1;
      state.offsetX = framing?.offsetX || 0;
      state.offsetY = framing?.offsetY || 0;
      state.drag = null;
      canvas.classList.remove('is-dragging');
    }

    if (previewOnly) {
      applyFormatLayout();
      loadEditionArtwork();
      return;
    }

    // Pointer Interaction (drag & pan photo)
    canvas.addEventListener('pointerdown', (event) => {
      if (!state.photo || state.busy || state.exporting || (event.pointerType === 'mouse' && event.button !== 0)) return;
      const point = canvasPoint(event);
      state.framingTouched = true;
      state.drag = { id: event.pointerId, x: point.x, y: point.y, offsetX: state.offsetX, offsetY: state.offsetY };
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add('is-dragging');
      event.preventDefault();
    });

    canvas.addEventListener('pointermove', (event) => {
      if (!state.drag || event.pointerId !== state.drag.id) return;
      const point = canvasPoint(event);
      state.offsetX = state.drag.offsetX + (point.x - state.drag.x);
      state.offsetY = state.drag.offsetY + (point.y - state.drag.y);
      getPlacement();
      scheduleRender();
    });

    function endDrag(event) {
      if (!state.drag || event.pointerId !== state.drag.id) return;
      state.drag = null;
      canvas.classList.remove('is-dragging');
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    }
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);

    // Zoom Control
    zoomInput?.addEventListener('input', () => {
      state.framingTouched = true;
      if (!state.photo) return;
      state.zoom = Math.min(2.5, Math.max(1, Number(zoomInput.value) || 1));
      getPlacement();
      scheduleRender();
    });

    // Text inputs
    pilotNameInput?.addEventListener('input', () => {
      state.pilotName = pilotNameInput.value.trim();
      scheduleRender();
    });

    teamNameInput?.addEventListener('input', () => {
      state.teamName = teamNameInput.value.trim();
      scheduleRender();
    });

    subjectSelect?.addEventListener('change', () => {
      const field = document.getElementById('pilot-name-field');
      if (field) field.hidden = isTeamArtwork();
      scheduleRender();
    });

    centerButton?.addEventListener('click', centerPhoto);

    input?.addEventListener('change', () => useFile(input.files?.[0]));
    uploadButton?.addEventListener('click', choosePhoto);
    document.getElementById('photo-upload-shortcut')?.addEventListener('click', choosePhoto);
    changeButton?.addEventListener('click', choosePhoto);
    resetButton?.addEventListener('click', () => {
      if (photoDraftKey()) window.TrilhasPhotoDraft?.remove(photoDraftKey());
      if (rememberPhoto) rememberPhoto.checked = false;
      reset();
    });

    registrationSelect?.addEventListener('change', () => {
      reset();
      const option = registrationSelect.selectedOptions[0];
      if (pilotNameInput) pilotNameInput.value = state.pilotName = option?.dataset.name || '';
      if (teamNameInput) teamNameInput.value = state.teamName = option?.dataset.team || '';
      scheduleRender();
      if (rememberPhoto) rememberPhoto.checked = false;
      restorePhoto();
    });

    rememberPhoto?.addEventListener('change', async () => {
      if (!photoDraftKey()) return;
      if (rememberPhoto.checked && state.normalizedPhoto) {
        await window.TrilhasPhotoDraft?.save(photoDraftKey(), state.normalizedPhoto);
      } else if (!rememberPhoto.checked) {
        await window.TrilhasPhotoDraft?.remove(photoDraftKey());
      }
    });

    async function restorePhoto() {
      if (!rememberPhoto || !photoDraftKey()) return;
      const key = photoDraftKey();
      const request = state.request;
      const blob = await window.TrilhasPhotoDraft?.load(key);
      if (blob && key === photoDraftKey() && request === state.request && !state.photo) {
        rememberPhoto.checked = true;
        await useFile(new File([blob], 'foto-salva.png', { type: 'image/png' }));
      }
    }

    // Format switching
    formatButtons.forEach(button => button.addEventListener('click', () => {
      const format = button.dataset.artFormat;
      if (state.exporting || !['story', 'feed'].includes(format) || format === activeFormat) return;
      saveFraming();
      activeFormat = format;
      applyFormatLayout();
      loadEditionArtwork();
      render();
      syncControls();
      announce(`${format === 'feed' ? 'Feed (1080 × 1350)' : 'Story (1080 × 1920)'} selecionado. ${state.photo ? 'Sua foto foi mantida.' : 'Adicione sua foto para começar.'}`);
    }));

    function downloadBlob(blob, filename) {
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    }

    downloadButton?.addEventListener('click', async () => {
      if (!state.photo || state.busy || state.exporting || !artworkIsReady()) return;
      state.exporting = true;
      syncControls();
      announce('Preparando sua arte em alta qualidade…');
      try {
        if (document.fonts?.ready) await document.fonts.ready;
        render();
        const blob = await new Promise((resolve, reject) => {
          canvas.toBlob((result) => result ? resolve(result) : reject(new Error('empty-export')), 'image/png');
        });
        const name = (isTeamArtwork() ? state.teamName : state.pilotName)
          .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 64);
        const filename = `arte-${isTeamArtwork() ? 'equipe' : 'piloto'}-${name || 'confirmado'}-3-trilhao-2027-${activeFormat}.png`;
        const file = new File([blob], filename, { type: 'image/png' });

        if (navigator.canShare && navigator.canShare({ files: [file] }) && /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent)) {
          try {
            await navigator.share({
              files: [file],
              title: `3º Trilhão Tianguá 2027 - ${activeFormat === 'feed' ? 'Feed' : 'Story'}`,
            });
          } catch (shareErr) {
            if (shareErr.name !== 'AbortError') {
              downloadBlob(blob, filename);
            }
          }
        } else {
          downloadBlob(blob, filename);
        }
        announce(`${activeFormat === 'feed' ? 'Feed' : 'Story'} baixado! Você também pode alternar o formato e baixar o outro.`);
      } catch (error) {
        announce(error.message || 'Não foi possível baixar agora. Tente novamente.', true);
      } finally {
        state.exporting = false;
        syncControls();
      }
    });

    // Initialize
    applyFormatLayout();
    loadEditionArtwork();
    syncControls();
    restorePhoto();
    document.fonts?.ready.then(() => { if (canvas.isConnected) render(); });
  }

  document.querySelectorAll('[data-frame-preview]').forEach(canvas => initializeStudio(canvas));
  initializeStudio();
})();
