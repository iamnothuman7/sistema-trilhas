const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { imagePlacement, textBlock, drawArtwork, filename } = require(path.join(__dirname, '..', 'frontend', 'static', 'js', 'team_frame_editor.js'));

function drawingContext() {
    const text = [], images = [], fills = [];
    return { text, images, fills, font: '700 40px Inter',
        measureText(value) { return { width: Array.from(value).length * Number(this.font.match(/(\d+)px/)[1]) * .58 }; },
        fillText(value, x, y) { text.push({ value, x, y, font: this.font }); },
        drawImage(image, x, y, width, height) { images.push({ image, x, y, width, height }); },
        fillRect(x, y, width, height) { fills.push({ x, y, width, height }); },
        createLinearGradient() { return { addColorStop() {} }; },
        beginPath() {}, moveTo() {}, lineTo() {}, arcTo() {}, closePath() {}, fill() {}, stroke() {}, save() {}, restore() {}, clip() {},
    };
}

test('contain preserves the full group photo; cover crops symmetrically and positioning changes the crop', () => {
    const image = { naturalWidth: 1600, naturalHeight: 900 }, box = { x: 0, y: 0, width: 900, height: 900 };
    assert.deepEqual(imagePlacement(image, box), { x: 0, y: 196.875, width: 900, height: 506.25 });
    assert.deepEqual(imagePlacement(image, box, 'cover'), { x: -350, y: 0, width: 1600, height: 900 });
    assert.equal(imagePlacement(image, box, 'cover', 100, 0, 50).x, 0);
    assert.equal(imagePlacement(image, box, 'cover', 100, 100, 50).x, -700);
});

test('long team names and unbroken words fit the artwork text area', () => {
    const context = drawingContext();
    const name = 'Equipe ' + 'A'.repeat(110);
    const result = textBlock(context, name, { x: 80, y: 950, width: 920, height: 170 }, { size: 64, minSize: 30, weight: 800 });
    assert.equal(result.lines.join('').replace(/\s/g, ''), name.replace(/\s/g, ''));
    assert.ok(result.lines.length * result.size * 1.16 <= 170);
    assert.ok(result.lines.every(line => context.measureText(line).width <= 920));
});

for (const [format, height] of [['story', 1920], ['feed', 1350]]) {
    test(`${format} draws the selected edition, exact dimensions and unaltered logo aspect ratio`, () => {
        const context = drawingContext();
        const logo = { width: 600, height: 300 }, photo = { width: 1600, height: 900 };
        const config = { eventTitle: 'Trilha de abril', dateLabel: '18 ABR · 2027', location: 'Tianguá / CE' };
        const state = { format, team: { name: 'Equipe Serra' }, subtitle: 'Nos vemos na trilha!', fit: 'contain', zoom: 100, x: 50, y: 50, logoPosition: 'right' };
        assert.deepEqual(drawArtwork(context, config, state, { edition: logo, brand: logo, photo, teamLogo: logo }), { width: 1080, height });
        assert.deepEqual(context.fills[0], { x: 0, y: 0, width: 1080, height });
        for (const value of ['EQUIPE CONFIRMADA', 'EQUIPE SERRA', 'Trilha de abril', config.dateLabel]) assert.ok(context.text.some(line => line.value === value));
        assert.ok(context.images.filter(item => item.image === logo).every(item => item.width / item.height === 2));
        assert.ok(context.images.every(item => Number.isFinite(item.x) && item.height > 0));
    });
}

test('download filename is local-safe and names the actual event, team and format', () => {
    const name = filename({ eventSlug: 'trilha-abril-2027' }, { team: { name: '../../Equipe São João <>' }, format: 'feed' });
    assert.equal(name, 'equipe-confirmada-equipe-sao-joao-trilha-abril-2027-feed.png');
});
