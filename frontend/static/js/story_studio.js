(() => {
  'use strict';

  const canvas = document.getElementById('story-canvas');
  if (!canvas) return;

  const context = canvas.getContext('2d');
  const input = document.getElementById('photo-input');
  const uploadButton = document.getElementById('upload-button');
  const downloadButton = document.getElementById('download-button');
  const changeButton = document.getElementById('change-photo-button');
  const resetButton = document.getElementById('reset-button');
  const zoomInput = document.getElementById('zoom-input');
  const controls = document.getElementById('photo-controls');
  const status = document.getElementById('editor-status');
  const uploadLabel = document.getElementById('upload-label');
  const help = document.getElementById('photo-help');
  const exportNote = document.getElementById('export-note');
  const retryButton = document.getElementById('retry-cutout-button');
  const downloadLabel = document.getElementById('download-label');
  const pilotNameInput = document.getElementById('pilot-name-input');
  const teamNameInput = document.getElementById('team-name-input');

  const WIDTH = 1080;
  const HEIGHT = 1920;
  const versionsEl = document.getElementById('event-versions');
  const versions = versionsEl ? JSON.parse(versionsEl.textContent) : [];
  if (!versions.length) return;

  let activeVersion = versions.find(v => v.id === document.body.dataset.version) || versions[0];
  const versionButtons = [...document.querySelectorAll('[data-event-version]')];
  const COLORS = { ...activeVersion.palette };
  let PHOTO = { ...activeVersion.layout.photo };
  let POP_OUT = activeVersion.layout.pop_out;
  let editionArtwork = null;
  let editionDecorations = {};
  const imageCache = new Map();
  const editionResourceCache = new Map();
  const tintedImageCache = new WeakMap();
  const savedSnapshots = new Set();
  const pendingSnapshots = new Set();
  let preloadScheduled = false;
  let generatedTimer = 0;
  let editionReady = false;
  let editionRequest = 0;
  const framingByLayout = new Map();
  const TYPE = '"Inter", "Barlow Condensed", "Arial Narrow", Impact, sans-serif';

  const background = document.createElement('canvas');
  const foreground = document.createElement('canvas');
  const frameLayer = document.createElement('canvas');
  frameLayer.width = background.width = foreground.width = canvas.width = WIDTH;
  frameLayer.height = background.height = foreground.height = canvas.height = HEIGHT;

  const state = {
    montageId: null,
    photo: null,
    cutout: null,
    subjectBounds: null,
    face: null,
    faceCandidates: [],
    framingTouched: false,
    normalizedPhoto: null,
    controller: null,
    cutoutError: false,
    brandReady: false,
    pilotName: '',
    teamName: '',
    zoom: 1,
    offsetX: 0,
    offsetY: 0,
    busy: false,
    exporting: false,
    request: 0,
    frame: 0,
    drag: null,
  };
  const brandLogo = new Image();

  function announce(message, isError = false) {
    if (status) {
      status.textContent = message;
      status.dataset.state = isError ? 'error' : 'success';
    }
  }

  function artworkIsReady() {
    return editionReady;
  }

  function syncControls() {
    const hasPhoto = Boolean(state.photo);
    const isBusy = state.busy || state.exporting;
    document.body.classList.toggle('has-photo', hasPhoto);
    canvas.classList.toggle('is-editable', hasPhoto);
    canvas.style.touchAction = hasPhoto ? 'none' : 'pan-y';
    canvas.setAttribute('aria-busy', String(isBusy || !artworkIsReady()));
    const previewTag = document.querySelector('.preview-tag');
    if (previewTag) previewTag.textContent = editionReady ? 'PRÉVIA DO SEU STORY 3D' : 'Carregando modelo…';
    if (controls) controls.hidden = !hasPhoto;
    versionButtons.forEach(button => { button.disabled = state.exporting; });
    if (uploadButton) uploadButton.disabled = isBusy;
    if (changeButton) { changeButton.disabled = isBusy; changeButton.hidden = !hasPhoto; }
    if (resetButton) { resetButton.disabled = state.exporting; resetButton.hidden = !hasPhoto; }
    if (retryButton) { retryButton.hidden = !state.cutoutError; retryButton.disabled = isBusy; }
    if (downloadButton) {
      downloadButton.disabled = !hasPhoto || (!state.cutout && !state.cutoutError) || isBusy || !artworkIsReady();
    }
    if (downloadLabel) {
      downloadLabel.textContent = state.busy && hasPhoto
        ? 'IA Recortando Foto…'
        : state.exporting
        ? 'Gerando PNG Alta Definição…'
        : state.cutoutError
        ? 'Tentar Recorte Novamente'
        : '⬇️ Baixar Meu Story 3D';
    }
    if (zoomInput) {
      zoomInput.disabled = isBusy || !hasPhoto;
      zoomInput.value = String(state.zoom);
    }
    if (uploadLabel) {
      uploadLabel.textContent = state.busy
        ? 'IA Retirando o Fundo…'
        : hasPhoto
        ? '📸 Trocar Minha Foto'
        : '📸 Adicionar Minha Foto';
    }
    if (help) {
      help.textContent = hasPhoto
        ? (state.busy ? 'Retirando o fundo da sua foto com IA…' : 'Arraste a foto para ajustar o efeito 3D fora da moldura.')
        : 'Escolha uma foto sua ou com sua moto para começar.';
    }
    if (exportNote) {
      exportNote.textContent = state.exporting
        ? 'Preparando seu arquivo PNG…'
        : 'PNG Alta Qualidade · 1080 × 1920 px';
    }
  }

  function archPath(ctx, x, y, width, height, inset = 0) {
    x += inset;
    y += inset;
    width -= inset * 2;
    height -= inset * 2;
    const top = Math.min(width / 2, 360);
    const bottom = 42;
    ctx.beginPath();
    ctx.moveTo(x + top, y);
    ctx.lineTo(x + width - top, y);
    ctx.arcTo(x + width, y, x + width, y + top, top);
    ctx.lineTo(x + width, y + height - bottom);
    ctx.quadraticCurveTo(x + width, y + height, x + width - bottom, y + height);
    ctx.lineTo(x + bottom, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - bottom);
    ctx.lineTo(x, y + top);
    ctx.arcTo(x, y, x + top, y, top);
    ctx.closePath();
  }

  function text(ctx, value, x, y, size, color, weight = 800, align = 'left') {
    ctx.fillStyle = color;
    ctx.textAlign = align;
    ctx.font = `${weight} ${size}px ${TYPE}`;
    ctx.fillText(value, x, y);
  }

  function drawContained(ctx, image, box) {
    const scale = Math.min(box.width / image.naturalWidth, box.height / image.naturalHeight);
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    ctx.drawImage(image, box.x + (box.width - width) / 2, box.y + (box.height - height) / 2, width, height);
  }

  function loadImage(source, priority = 'high') {
    if (!source) return Promise.resolve(null);
    const cached = imageCache.get(source);
    if (cached) return cached.promise;
    const image = new Image();
    image.decoding = 'async';
    image.fetchPriority = priority;
    const promise = new Promise((resolve, reject) => {
      const fail = () => { imageCache.delete(source); reject(new Error('artwork-load-failed')); };
      image.onload = async () => {
        try {
          if (image.decode) await image.decode();
          resolve(image);
        } catch (_) { resolve(image); }
      };
      image.onerror = fail;
      image.src = source;
    });
    imageCache.set(source, { image, promise });
    return promise;
  }

  function tintSilhouette(image, color) {
    let colors = tintedImageCache.get(image);
    if (!colors) { colors = new Map(); tintedImageCache.set(image, colors); }
    if (colors.has(color)) return colors.get(color);
    const layer = document.createElement('canvas');
    layer.width = image.naturalWidth;
    layer.height = image.naturalHeight;
    const ctx = layer.getContext('2d');
    ctx.drawImage(image, 0, 0);
    ctx.globalCompositeOperation = 'source-in';
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, layer.width, layer.height);
    colors.set(color, layer);
    return layer;
  }

  function prepareEdition(version, priority = 'high') {
    const decoration = version.decorations;
    const sources = [version.logo, decoration?.crowd];
    const cached = editionResourceCache.get(version.id);
    if (cached) return cached;
    const entry = { assets: null, promise: null };
    entry.promise = Promise.all(sources.map(source => loadImage(source, priority)))
      .then(([logo, crowd]) => {
        entry.assets = {
          logo,
          decorations: {
            crowd: crowd ? tintSilhouette(crowd, decoration.tone) : null,
          }
        };
        return entry.assets;
      }).catch(error => { editionResourceCache.delete(version.id); throw error; });
    editionResourceCache.set(version.id, entry);
    return entry;
  }

  function drawFireSparks(ctx) {
    const decoration = activeVersion.decorations;
    if (editionDecorations.crowd) {
      const image = editionDecorations.crowd;
      const figures = [
        { crop: [610, 159, 305, 540], x: 330, y: 395, height: 1050, angle: 0.06, alpha: 0.25 },
        { crop: [1008, 288, 260, 502], x: 650, y: 850, height: 900, angle: -0.12, alpha: 0.29 },
        { crop: [218, 240, 246, 550], x: -125, y: 475, height: 1120, angle: -0.09, alpha: 0.54 },
        { crop: [1380, 284, 232, 506], x: 835, y: 680, height: 1030, angle: 0.12, alpha: 0.50 },
      ];
      figures.forEach(({ crop, x, y, height, angle, alpha }) => {
        const [sx, sy, sw, sh] = crop;
        const width = height * sw / sh;
        ctx.save();
        ctx.globalAlpha = alpha;
        if (alpha < 0.3) {
          ctx.beginPath(); ctx.rect(0, 0, WIDTH, 1350); ctx.clip();
        }
        ctx.translate(x + width / 2, y + height / 2);
        ctx.rotate(angle);
        ctx.drawImage(image, sx, sy, sw, sh, -width / 2, -height / 2, width, height);
        ctx.restore();
      });
    }
    // Off-road fiery embers and dynamic sparks
    ctx.save();
    ctx.fillStyle = COLORS.yellow;
    ctx.strokeStyle = COLORS.yellow;
    ctx.globalAlpha = 0.28;
    ctx.lineWidth = 3;
    for (let i = 0; i < 36; i += 1) {
      const x = (i * 197 + 43) % WIDTH;
      const y = 50 + (i * 139) % 1350;
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(i * 0.73);
      ctx.fillRect(-3, -10, 7, 18);
      ctx.restore();
    }
    ctx.restore();
  }

  function drawCredit(ctx, y) {
    if (!state.brandReady) return;
    const logoWidth = 148;
    const logoHeight = logoWidth * brandLogo.naturalHeight / brandLogo.naturalWidth;
    const gap = 16;
    ctx.save();
    ctx.font = '600 26px Arial, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
    const label = 'Desenvolvido por';
    const metrics = ctx.measureText(label);
    const left = (WIDTH - metrics.width - gap - logoWidth) / 2;
    const baseline = y + (metrics.actualBoundingBoxAscent - metrics.actualBoundingBoxDescent) / 2;
    ctx.fillStyle = '#ffffff';
    ctx.fillText(label, left, baseline + 3);
    ctx.drawImage(brandLogo, left + metrics.width + gap, y - logoHeight / 2, logoWidth, logoHeight);
    ctx.restore();
  }

  function buildLayers() {
    const bg = background.getContext('2d');
    const fg = foreground.getContext('2d');
    const border = frameLayer.getContext('2d');
    border.clearRect(0, 0, WIDTH, HEIGHT);
    bg.clearRect(0, 0, WIDTH, HEIGHT);
    fg.clearRect(0, 0, WIDTH, HEIGHT);

    const layout = activeVersion.layout;
    const gradient = bg.createLinearGradient(0, 0, WIDTH * 0.4, HEIGHT);
    activeVersion.background_colors.forEach((color, index, colors) => {
      gradient.addColorStop(index / (colors.length - 1), color);
    });
    bg.fillStyle = gradient;
    bg.fillRect(0, 0, WIDTH, HEIGHT);

    const light = bg.createRadialGradient(540, 320, 0, 540, 320, 920);
    light.addColorStop(0, 'rgba(255, 145, 0, 0.24)');
    light.addColorStop(1, 'rgba(255, 255, 255, 0)');
    bg.fillStyle = light;
    bg.fillRect(0, 0, WIDTH, HEIGHT);

    drawFireSparks(bg);
    if (editionReady && editionArtwork) {
      drawContained(bg, editionArtwork, layout.logo);
    }

    // Arch Frame & Shadow
    border.save();
    border.shadowColor = 'rgba(0,0,0,0.45)';
    border.shadowBlur = 28;
    border.shadowOffsetY = 14;
    archPath(border, PHOTO.x - 18, PHOTO.y - 18, PHOTO.width + 36, PHOTO.height + 36);
    border.fillStyle = COLORS.navy;
    border.fill();
    border.restore();

    archPath(border, PHOTO.x - 10, PHOTO.y - 10, PHOTO.width + 20, PHOTO.height + 20);
    border.fillStyle = COLORS.yellow;
    border.fill();

    archPath(border, PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height);
    border.globalCompositeOperation = 'destination-out';
    border.fill();
    border.globalCompositeOperation = 'source-over';

    archPath(bg, PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height);
    bg.fillStyle = '#26140c';
    bg.fill();

    // Tilted 3D Banner ("EU VOU!")
    fg.save();
    fg.translate(540, layout.banner_y);
    fg.rotate(-0.035);
    fg.fillStyle = COLORS.navy;
    fg.beginPath();
    fg.roundRect(-448, -76, 920, 178, 18);
    fg.fill();
    fg.fillStyle = COLORS.yellow;
    fg.beginPath();
    fg.roundRect(-466, -98, 920, 178, 18);
    fg.fill();
    text(fg, 'EU VOU!', -6, 41, 156, COLORS.blue, 900, 'center');
    fg.restore();

    // Pilot Name / Team Stamp if typed
    if (state.pilotName || state.teamName) {
      fg.save();
      fg.shadowColor = 'rgba(0,0,0,0.85)';
      fg.shadowBlur = 14;
      const stampText = [state.pilotName, state.teamName].filter(Boolean).join(' • ').toUpperCase();
      fg.fillStyle = 'rgba(15, 23, 42, 0.9)';
      fg.beginPath();
      fg.roundRect(140, 1475, 800, 74, 37);
      fg.fill();
      fg.strokeStyle = COLORS.yellow;
      fg.lineWidth = 3;
      fg.stroke();
      text(fg, stampText, WIDTH / 2, 1524, 34, '#ffffff', 900, 'center');
      fg.restore();
    }

    drawCredit(fg, layout.credit_y);

    // Structured Footer
    const footerY = layout.footer_y;
    fg.fillStyle = COLORS.navy;
    fg.fillRect(0, footerY, WIDTH, HEIGHT - footerY);
    fg.strokeStyle = 'rgba(255,255,255,0.22)';
    fg.lineWidth = 2;
    fg.beginPath(); fg.moveTo(60, footerY); fg.lineTo(1020, footerY); fg.stroke();

    const columns = layout.footer_columns;
    fg.save();
    fg.textBaseline = 'alphabetic';
    text(fg, activeVersion.weekday, columns.date_x, footerY + 46, 23, COLORS.yellow, 800, 'center');
    text(fg, activeVersion.date_short, columns.date_x, footerY + 114, 58, '#ffffff', 900, 'center');
    text(fg, 'CONCENTRAÇÃO', columns.concentration_x, footerY + 46, 23, COLORS.yellow, 800, 'center');
    text(fg, activeVersion.concentration_time, columns.concentration_x, footerY + 114, 66, '#ffffff', 900, 'center');
    text(fg, 'LARGADA', columns.departure_x, footerY + 46, 23, COLORS.yellow, 800, 'center');
    text(fg, activeVersion.departure_time, columns.departure_x, footerY + 114, 66, '#ffffff', 900, 'center');

    fg.fillStyle = 'rgba(255,255,255,0.24)';
    columns.dividers.forEach(x => fg.fillRect(x, footerY + 28, 1.5, 101));
    fg.fillRect(90, footerY + 151, 900, 1.5);
    text(fg, activeVersion.footer_title + ' · ' + activeVersion.footer_subtitle, WIDTH / 2, footerY + 205, 28, '#ffffff', 800, 'center');
    fg.restore();
  }

  function imageSize() {
    return {
      width: state.photo?.naturalWidth || state.photo?.width || 1,
      height: state.photo?.naturalHeight || state.photo?.height || 1,
    };
  }

  function getPlacement() {
    const size = imageSize();
    const bounds = state.subjectBounds;
    const face = bounds && state.face;
    const scale = (face
      ? Math.max(PHOTO.width / size.width, (PHOTO.height + POP_OUT + 20) / Math.max(1, size.height - bounds.top), PHOTO.width * 0.26 / Math.max(1, face.right - face.left))
      : bounds
      ? Math.max(PHOTO.width / size.width, (PHOTO.height + POP_OUT + 20) / Math.max(1, size.height - bounds.top))
      : Math.max(PHOTO.width / size.width, PHOTO.height / size.height)) * state.zoom;
    const width = size.width * scale;
    const height = size.height * scale;
    const subjectCenter = face ? face.centerX : bounds ? (bounds.left + bounds.right) / 2 : size.width / 2;
    const baseX = Math.max(PHOTO.x + PHOTO.width - width,
      Math.min(PHOTO.x, PHOTO.x + PHOTO.width / 2 - subjectCenter * scale));
    const baseY = face ? PHOTO.y - POP_OUT - bounds.top * scale : bounds
      ? Math.max(PHOTO.y + PHOTO.height - height, Math.min(PHOTO.y, PHOTO.y - POP_OUT - bounds.top * scale))
      : PHOTO.y + (PHOTO.height - height) / 2;
    state.offsetX = Math.max(PHOTO.x + PHOTO.width - width - baseX, Math.min(PHOTO.x - baseX, state.offsetX));
    const minY = face ? Math.min(baseY, PHOTO.y + PHOTO.height - height) : PHOTO.y + PHOTO.height - height;
    state.offsetY = Math.max(minY - baseY, Math.min(PHOTO.y - baseY, state.offsetY));
    return { x: baseX + state.offsetX, y: baseY + state.offsetY, width, height };
  }

  function render() {
    state.frame = 0;
    context.clearRect(0, 0, WIDTH, HEIGHT);
    context.drawImage(background, 0, 0);
    if (state.photo) {
      const placement = getPlacement();
      context.save();
      archPath(context, PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height);
      context.clip();
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = 'high';
      context.drawImage(state.photo, placement.x, placement.y, placement.width, placement.height);
      context.restore();
    } else {
      context.save();
      context.strokeStyle = 'rgba(255, 157, 0, 0.45)';
      context.fillStyle = 'rgba(255, 157, 0, 0.12)';
      context.lineWidth = 8;
      context.beginPath();
      context.arc(PHOTO.x + PHOTO.width / 2, PHOTO.y + PHOTO.height * 0.38, PHOTO.width * 0.09, 0, Math.PI * 2);
      context.fill();
      context.stroke();
      context.beginPath();
      context.arc(PHOTO.x + PHOTO.width / 2, PHOTO.y + PHOTO.height * 0.64, PHOTO.width * 0.165, Math.PI, Math.PI * 2);
      context.stroke();
      text(context, 'SUA FOTO AQUI', PHOTO.x + PHOTO.width / 2, PHOTO.y + PHOTO.height * 0.76, 43, COLORS.yellow, 900, 'center');
      context.restore();
    }
    // Arch border, then the transparent cutout popping out of the top!
    context.drawImage(frameLayer, 0, 0);
    if (state.photo && state.cutout) {
      const placement = getPlacement();
      context.save();
      archPath(context, PHOTO.x, PHOTO.y, PHOTO.width, PHOTO.height);
      context.rect(PHOTO.x, 0, PHOTO.width, PHOTO.y + PHOTO.width / 2);
      context.clip();
      context.imageSmoothingEnabled = true;
      context.imageSmoothingQuality = 'high';
      context.drawImage(state.cutout, placement.x, placement.y, placement.width, placement.height);
      context.restore();
    }
    context.drawImage(foreground, 0, 0);
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
    const scale = Math.min(1, 1600 / Math.max(width, height));
    const normalized = document.createElement('canvas');
    normalized.width = Math.max(1, Math.round(width * scale));
    normalized.height = Math.max(1, Math.round(height * scale));
    const ctx = normalized.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(decoded, 0, 0, normalized.width, normalized.height);
    return new Promise((resolve, reject) => normalized.toBlob(
      result => result ? resolve(result) : reject(new Error('Não foi possível preparar essa foto.')), 'image/png'));
  }

  function findSubjectBounds(photo) {
    const width = photo.naturalWidth || photo.width;
    const height = photo.naturalHeight || photo.height;
    const mask = document.createElement('canvas');
    const ratio = Math.min(1, 700 / Math.max(width, height));
    mask.width = Math.max(1, Math.round(width * ratio));
    mask.height = Math.max(1, Math.round(height * ratio));
    const ctx = mask.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(photo, 0, 0, mask.width, mask.height);
    const pixels = ctx.getImageData(0, 0, mask.width, mask.height).data;
    let left = mask.width, right = 0, top = mask.height, bottom = 0, count = 0;
    for (let y = 0; y < mask.height; y += 1) {
      let rowCount = 0;
      for (let x = 0; x < mask.width; x += 1) {
        if (pixels[(y * mask.width + x) * 4 + 3] > 80) {
          left = Math.min(left, x); right = Math.max(right, x); rowCount += 1; count += 1;
        }
      }
      if (rowCount >= 3) { top = Math.min(top, y); bottom = y; }
    }
    if (count < mask.width * mask.height * 0.005 || bottom <= top) {
      return { left: 0, right: width, top: height * 0.05, bottom: height };
    }
    return { left: left / ratio, right: right / ratio, top: top / ratio, bottom: bottom / ratio };
  }

  let faceWorker = null;
  let faceSequence = 0;
  const faceRequests = new Map();

  function faceEngine() {
    if (!('Worker' in window) || !('createImageBitmap' in window)) return null;
    if (faceWorker) return faceWorker;
    try {
      faceWorker = new Worker('/static/face-worker.js');
      faceWorker.onmessage = ({ data }) => {
        const pending = faceRequests.get(data.id);
        if (!pending) return;
        clearTimeout(pending.timer); faceRequests.delete(data.id);
        pending.resolve(data.faces || []);
      };
      faceWorker.onerror = () => {
        faceRequests.forEach(pending => { clearTimeout(pending.timer); pending.resolve([]); });
        faceRequests.clear(); faceWorker?.terminate(); faceWorker = null;
      };
      faceWorker.postMessage({ id: 0 });
      return faceWorker;
    } catch (_) { return null; }
  }

  function selectPortraitFace() {
    const bounds = state.subjectBounds;
    const inside = bounds ? state.faceCandidates.filter(face => face.centerX >= bounds.left && face.centerX <= bounds.right
      && (face.top + face.bottom) / 2 >= bounds.top && (face.top + face.bottom) / 2 <= bounds.bottom) : state.faceCandidates;
    state.face = [...inside].sort((a, b) => (b.right - b.left) * (b.bottom - b.top) - (a.right - a.left) * (a.bottom - a.top))[0] || null;
  }

  async function detectPortraitFace(photo, request) {
    const worker = faceEngine();
    if (!worker) return;
    let bitmap;
    try {
      const width = photo.naturalWidth || photo.width, height = photo.naturalHeight || photo.height;
      const ratio = Math.min(1, 1024 / Math.max(width, height));
      bitmap = await createImageBitmap(photo, { resizeWidth: Math.max(1, Math.round(width * ratio)), resizeHeight: Math.max(1, Math.round(height * ratio)), resizeQuality: 'high' });
      if (request !== state.request) { bitmap.close(); return; }
      const id = ++faceSequence;
      const faces = await new Promise(resolve => {
        const timer = setTimeout(() => { faceRequests.delete(id); resolve([]); }, 12000);
        faceRequests.set(id, { resolve, timer });
        worker.postMessage({ id, bitmap }, [bitmap]); bitmap = null;
      });
      if (request !== state.request) return;
      state.faceCandidates = faces.map(face => ({ left: face.left * width, right: face.right * width, top: face.top * height, bottom: face.bottom * height, centerX: face.centerX * width, eyeY: face.eyeY * height }));
      if (!state.framingTouched) selectPortraitFace();
      if (state.face && state.cutout && !state.framingTouched) {
        state.offsetX = state.offsetY = 0; framingByLayout.clear(); render();
      }
      syncControls();
    } catch (_) { bitmap?.close(); }
  }

  function csrfToken() {
    return document.cookie.split('; ').find(value => value.startsWith('csrftoken='))?.slice(10)
      || document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  }

  async function backupGenerated(blob, montageId, version) {
    if (!montageId) return false;
    try {
      const form = new FormData();
      form.append('version', version);
      form.append('pilot_name', state.pilotName);
      form.append('team_name', state.teamName);
      form.append('image', blob, 'montagem.png');
      const response = await fetch('/api/montages/' + montageId + '/backup/', {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() }
      });
      return response.ok;
    } catch (_) { return false; }
  }

  function scheduleGenerated(montageId) {
    if (!montageId) return;
    clearTimeout(generatedTimer);
    generatedTimer = setTimeout(async () => {
      try {
        buildLayers();
        render();
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        if (blob) await backupGenerated(blob, montageId, activeVersion.id);
      } catch (_) {}
    }, 600);
  }

  async function removeBackground(request) {
    state.controller?.abort();
    const controller = new AbortController();
    state.controller = controller;
    state.busy = true;
    state.cutoutError = false;
    state.montageId = null;
    syncControls();
    announce('IA retirando o fundo e preparando o efeito 3D fora da moldura…');
    const timeout = window.setTimeout(() => controller.abort(), 120000);
    try {
      const form = new FormData();
      form.append('photo', state.normalizedPhoto, 'retrato.png');
      const response = await fetch('/api/remove-background/', {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrfToken() },
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        throw new Error(data?.error || 'Não foi possível retirar o fundo. Tente novamente.');
      }
      const result = await decodePhoto(await response.blob());
      if (request !== state.request) { releasePhoto(result); return; }
      const bounds = findSubjectBounds(result);
      state.montageId = response.headers.get('X-Montage-ID');
      releasePhoto(state.cutout);
      state.cutout = result;
      state.subjectBounds = bounds;
      selectPortraitFace();
      state.zoom = 1;
      state.offsetX = state.offsetY = 0;
      framingByLayout.clear();
      state.busy = false;
      syncControls();
      render();
      announce('🔥 Pronto! Sua foto saiu da moldura em 3D! Ajuste o zoom e baixe seu Story.');
      scheduleGenerated(state.montageId);
    } catch (error) {
      if (request !== state.request) return;
      state.busy = false;
      state.cutoutError = true;
      syncControls();
      announce(error.message || 'Erro ao recortar fundo. Toque para tentar novamente.', true);
    } finally {
      window.clearTimeout(timeout);
      if (state.controller === controller) state.controller = null;
    }
  }

  async function useFile(file) {
    if (!file) return;
    const request = ++state.request;
    state.busy = true;
    syncControls();
    announce('Preparando sua foto…');
    let decoded;
    try {
      decoded = await decodePhoto(file);
      const blob = await normalizePhoto(decoded);
      releasePhoto(decoded);
      decoded = null;
      const photo = await decodePhoto(blob);
      if (request !== state.request) { releasePhoto(photo); return; }
      releasePhoto(state.photo);
      releasePhoto(state.cutout);
      state.montageId = null;
      state.photo = photo;
      framingByLayout.clear();
      state.cutout = null;
      state.subjectBounds = null;
      state.face = null; state.faceCandidates = []; state.framingTouched = false;
      state.normalizedPhoto = blob;
      state.cutoutError = false;
      state.zoom = 1;
      state.offsetX = state.offsetY = 0;
      syncControls();
      render();
      void detectPortraitFace(photo, request);
      await removeBackground(request);
    } catch (error) {
      if (request !== state.request) return;
      state.busy = false;
      syncControls();
      announce(error.message || 'Não foi possível abrir essa foto.', true);
    } finally {
      releasePhoto(decoded);
      if (request === state.request) input.value = '';
    }
  }

  function choosePhoto() {
    if (!state.busy && !state.exporting) { faceEngine(); input.click(); }
  }

  function reset() {
    framingByLayout.clear();
    state.request += 1;
    state.montageId = null;
    state.controller?.abort();
    releasePhoto(state.cutout);
    state.cutout = null;
    state.subjectBounds = null;
    state.face = null; state.faceCandidates = []; state.framingTouched = false;
    state.normalizedPhoto = null;
    state.cutoutError = false;
    releasePhoto(state.photo);
    state.photo = null;
    state.zoom = 1;
    state.offsetX = state.offsetY = 0;
    state.busy = false;
    state.drag = null;
    input.value = '';
    syncControls();
    render();
    announce('Tudo pronto para uma nova foto.');
  }

  function canvasPoint(event) {
    const bounds = canvas.getBoundingClientRect();
    return { x: (event.clientX - bounds.left) * WIDTH / bounds.width, y: (event.clientY - bounds.top) * HEIGHT / bounds.height };
  }

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
    state.offsetX = state.drag.offsetX + point.x - state.drag.x;
    state.offsetY = state.drag.offsetY + point.y - state.drag.y;
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

  zoomInput?.addEventListener('input', () => {
    state.framingTouched = true;
    if (!state.photo) return;
    state.zoom = Math.min(2.5, Math.max(1, Number(zoomInput.value) || 1));
    getPlacement();
    scheduleRender();
  });

  pilotNameInput?.addEventListener('input', () => {
    state.pilotName = pilotNameInput.value.trim();
    buildLayers();
    scheduleRender();
  });

  teamNameInput?.addEventListener('input', () => {
    state.teamName = teamNameInput.value.trim();
    buildLayers();
    scheduleRender();
  });

  input?.addEventListener('change', () => useFile(input.files?.[0]));
  uploadButton?.addEventListener('click', choosePhoto);
  changeButton?.addEventListener('click', choosePhoto);
  resetButton?.addEventListener('click', reset);
  retryButton?.addEventListener('click', () => {
    if (state.normalizedPhoto && !state.busy && !state.exporting) removeBackground(state.request);
  });

  downloadButton?.addEventListener('click', async () => {
    if (state.cutoutError && state.normalizedPhoto && !state.busy && !state.exporting) {
      await removeBackground(state.request);
      return;
    }
    if (!state.photo || state.busy || state.exporting || !artworkIsReady()) return;
    state.exporting = true;
    syncControls();
    announce('Preparando sua montagem em alta qualidade…');
    try {
      if (document.fonts?.ready) await document.fonts.ready;
      buildLayers();
      render();
      const blob = await new Promise((resolve, reject) => {
        canvas.toBlob((result) => result ? resolve(result) : reject(new Error('empty-export')), 'image/png');
      });
      if (state.montageId) {
        const form = new FormData();
        form.append('version', activeVersion.id);
        form.append('pilot_name', state.pilotName);
        form.append('team_name', state.teamName);
        form.append('image', blob, 'montagem.png');
        void fetch('/api/montages/' + state.montageId + '/download/', {
          method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-CSRFToken': csrfToken() }
        }).catch(() => {});
      }
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = activeVersion.filename || 'story-3-trilhao-tiangua-2027.png';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60000);
      announce('✅ Story 3D baixado com sucesso! Poste no Instagram e marque @equipemorcego.');
    } catch (_) {
      announce('Não foi possível baixar agora. Tente novamente.', true);
    } finally {
      state.exporting = false;
      syncControls();
    }
  });

  function loadEditionArtwork() {
    const request = ++editionRequest;
    const version = activeVersion;
    const entry = prepareEdition(version);
    const apply = assets => {
      if (request !== editionRequest || version !== activeVersion) return;
      editionArtwork = assets.logo;
      editionDecorations = assets.decorations;
      editionReady = true;
      buildLayers(); render(); syncControls();
      if (state.montageId) scheduleGenerated(state.montageId);
    };
    if (entry.assets) { apply(entry.assets); return; }
    editionReady = false;
    syncControls();
    void entry.promise.then(apply).catch(() => {
      if (request !== editionRequest) return;
      syncControls();
    });
  }

  function selectEdition(identifier) {
    const version = versions.find(item => item.id === identifier);
    if (!version || state.exporting) return;
    activeVersion = version;
    PHOTO = { ...activeVersion.layout.photo };
    POP_OUT = activeVersion.layout.pop_out;
    Object.assign(COLORS, activeVersion.palette);
    versionButtons.forEach(button => {
      const active = button.dataset.eventVersion === activeVersion.id;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('active-version', active);
    });
    const studioEdition = document.getElementById('studio-edition');
    if (studioEdition) studioEdition.textContent = `${activeVersion.label} · ${activeVersion.selector_detail}`;
    loadEditionArtwork();
    announce(`${version.label} selecionado. ${state.photo ? 'Sua foto e recorte 3D foram mantidos!' : 'Adicione sua foto para criar o efeito 3D.'}`);
  }

  versionButtons.forEach(button => {
    button.addEventListener('click', () => selectEdition(button.dataset.eventVersion));
  });

  brandLogo.onload = () => { state.brandReady = true; buildLayers(); render(); syncControls(); };
  brandLogo.onerror = () => { state.brandReady = false; buildLayers(); render(); syncControls(); };
  brandLogo.src = '/static/assets/nabio-white.webp';

  loadEditionArtwork();
  buildLayers();
  render();
  syncControls();
})();
