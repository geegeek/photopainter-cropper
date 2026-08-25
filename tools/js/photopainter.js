/*
 * photopainter.js - convert photos to the Waveshare PhotoPainter 7-colour BMP,
 * producing the very same bytes as Waveshare's official `convert`.
 *
 * The dithering is a line-by-line port of Pillow 10/11 `libImaging`:
 *   Convert.c  topalette()   - the RGB + Floyd-Steinberg branch
 *   Palette.c  ImagingPaletteCacheUpdate() - the nearest-colour cache
 * including the quirks that make it different from a textbook Floyd-Steinberg
 * (integer error terms, division truncated toward zero, nearest colour looked
 * up on coordinates quantised to multiples of 4, first palette index wins).
 * A "correct" from-scratch implementation differs on ~62% of the pixels.
 *
 * The JPEG must be decoded by libjpeg-turbo to get the same pixels Pillow sees:
 * `sharp` (libvips) does, and is verified bit-identical here; the pure-JS
 * `jpeg-js` is NOT (differences up to 27 per channel), so it must not be used.
 *
 * Usage:  node photopainter.js <folder|file> [--jobs N] [--force]
 */

'use strict';

const DEVICE_PALETTE = [
  [0, 0, 0], [255, 255, 255], [0, 255, 0], [0, 0, 255],
  [255, 0, 0], [255, 255, 0], [255, 128, 0],
];

/** The 256-entry RGBA palette the official tool builds (7 colours + 249 black). */
function buildPalette() {
  const pal = new Uint8Array(256 * 4);
  for (let i = 0; i < 256; i++) {
    const c = i < DEVICE_PALETTE.length ? DEVICE_PALETTE[i] : [0, 0, 0];
    pal[i * 4] = c[0]; pal[i * 4 + 1] = c[1]; pal[i * 4 + 2] = c[2]; pal[i * 4 + 3] = 255;
  }
  return pal;
}

const BOX = 8;
const BOXVOLUME = BOX * BOX * BOX;
const RSTEP = 4, GSTEP = 4, BSTEP = 4;
const UINT_MAX = 4294967295;

const clip8 = (v) => (v <= 0 ? 0 : v < 256 ? v : 255);
const cacheIndex = (r, g, b) => (r >> 2) + (g >> 2) * 64 + (b >> 2) * 4096;

/** Port of ImagingPaletteCacheUpdate: fill one 32x32x32 box of the lookup cache. */
function cacheUpdate(pal, palSize, cache, r, g, b) {
  const dmin = new Uint32Array(256);
  let dmax = UINT_MAX;

  const r0 = r & 0xe0, r1 = r0 + 0x1f, rc = (r0 + r1) >> 1;
  const g0 = g & 0xe0, g1 = g0 + 0x1f, gc = (g0 + g1) >> 1;
  const b0 = b & 0xe0, b1 = b0 + 0x1f, bc = (b0 + b1) >> 1;
  const sq = (a, b2) => (a - b2) * (a - b2);

  for (let i = 0; i < palSize; i++) {
    const pr = pal[i * 4], pg = pal[i * 4 + 1], pb = pal[i * 4 + 2];
    let tmin = pr < r0 ? sq(pr, r0) : pr > r1 ? sq(pr, r1) : 0;
    let tmax = pr <= rc ? sq(pr, r1) : sq(pr, r0);
    tmin += pg < g0 ? sq(pg, g0) : pg > g1 ? sq(pg, g1) : 0;
    tmax += pg <= gc ? sq(pg, g1) : sq(pg, g0);
    tmin += pb < b0 ? sq(pb, b0) : pb > b1 ? sq(pb, b1) : 0;
    tmax += pb <= bc ? sq(pb, b1) : sq(pb, b0);
    dmin[i] = tmin;
    if (tmax < dmax) dmax = tmax;
  }

  const d = new Uint32Array(BOXVOLUME).fill(UINT_MAX);
  const c = new Uint8Array(BOXVOLUME);

  for (let i = 0; i < palSize; i++) {
    if (dmin[i] > dmax) continue;
    let ri = (r0 - pal[i * 4]);
    let gi = (g0 - pal[i * 4 + 1]);
    let bi = (b0 - pal[i * 4 + 2]);
    let rd = ri * ri + gi * gi + bi * bi;
    ri = ri * (2 * RSTEP) + RSTEP * RSTEP;
    gi = gi * (2 * GSTEP) + GSTEP * GSTEP;
    bi = bi * (2 * BSTEP) + BSTEP * BSTEP;
    let rx = ri, j = 0;
    for (let rr = 0; rr < BOX; rr++) {
      let gd = rd, gx = gi;
      for (let gg = 0; gg < BOX; gg++) {
        let bd = gd, bx = bi;
        for (let bb = 0; bb < BOX; bb++) {
          if ((bd >>> 0) < d[j]) { d[j] = bd >>> 0; c[j] = i; }
          bd += bx;
          bx += 2 * BSTEP * BSTEP;
          j++;
        }
        gd += gx;
        gx += 2 * GSTEP * GSTEP;
      }
      rd += rx;
      rx += 2 * RSTEP * RSTEP;
    }
  }

  let j = 0;
  for (let rr = r0; rr < r1; rr += 4)
    for (let gg = g0; gg < g1; gg += 4)
      for (let bb = b0; bb < b1; bb += 4)
        cache[cacheIndex(rr, gg, bb)] = c[j++];
}

/**
 * Port of topalette(): map an RGB buffer to palette indices with Floyd-Steinberg.
 * @param {Uint8Array} rgb  width*height*3 bytes
 * @returns {Uint8Array} width*height palette indices
 */
function ditherToPalette(rgb, width, height, pal = buildPalette(), palSize = 256) {
  const cache = new Int16Array(64 * 64 * 64).fill(0x100);
  const errors = new Int32Array((width + 1) * 3);
  const out = new Uint8Array(width * height);

  for (let y = 0; y < height; y++) {
    let r = 0, r0 = 0, r1 = 0, r2 = 0;
    let g = 0, g0 = 0, g1 = 0, g2 = 0;
    let b = 0, b0 = 0, b1 = 0, b2 = 0;
    let inPos = y * width * 3;
    const outPos = y * width;
    let e = 0;

    for (let x = 0; x < width; x++, inPos += 3) {
      // the C code divides by 16 with truncation toward zero
      r = clip8(rgb[inPos] + (((r + errors[e + 3]) / 16) | 0));
      g = clip8(rgb[inPos + 1] + (((g + errors[e + 4]) / 16) | 0));
      b = clip8(rgb[inPos + 2] + (((b + errors[e + 5]) / 16) | 0));

      const ci = cacheIndex(r, g, b);
      if (cache[ci] === 0x100) cacheUpdate(pal, palSize, cache, r, g, b);
      const idx = cache[ci];
      out[outPos + x] = idx;

      r -= pal[idx * 4];
      g -= pal[idx * 4 + 1];
      b -= pal[idx * 4 + 2];

      let d2;
      r2 = r; d2 = r + r; r += d2; errors[e] = r + r0; r += d2; r0 = r + r1; r1 = r2; r += d2;
      g2 = g; d2 = g + g; g += d2; errors[e + 1] = g + g0; g += d2; g0 = g + g1; g1 = g2; g += d2;
      b2 = b; d2 = b + b; b += d2; errors[e + 2] = b + b0; b += d2; b0 = b + b1; b1 = b2; b += d2;

      e += 3;
    }
    // the original writes the blue carries into the last slot of all three
    errors[e] = b0; errors[e + 1] = b1; errors[e + 2] = b2;
  }
  return out;
}

/** Palette indices back to RGB, as `.convert("RGB")` does. */
function paletteToRgb(indices, pal = buildPalette()) {
  const out = Buffer.alloc(indices.length * 3);
  for (let i = 0; i < indices.length; i++) {
    const p = indices[i] * 4;
    out[i * 3] = pal[p]; out[i * 3 + 1] = pal[p + 1]; out[i * 3 + 2] = pal[p + 2];
  }
  return out;
}

/**
 * 24-bit BMP exactly as Pillow writes it: 54-byte header, bottom-up BGR rows,
 * resolution 3780 ppm (Pillow's 96 dpi default; a source with its own dpi would
 * change this field).
 */
function writeBmp24(rgb, width, height, dpi = 96) {
  const stride = (width * 3 + 3) & ~3;
  const imageSize = stride * height;
  const buf = Buffer.alloc(54 + imageSize);
  const ppm = Math.floor(dpi * 39.3701 + 0.5);
  buf.write('BM', 0, 'ascii');
  buf.writeUInt32LE(54 + imageSize, 2);
  buf.writeUInt32LE(0, 6);
  buf.writeUInt32LE(54, 10);
  buf.writeUInt32LE(40, 14);
  buf.writeInt32LE(width, 18);
  buf.writeInt32LE(height, 22);
  buf.writeUInt16LE(1, 26);
  buf.writeUInt16LE(24, 28);
  buf.writeUInt32LE(0, 30);
  buf.writeUInt32LE(imageSize, 34);
  buf.writeInt32LE(ppm, 38);
  buf.writeInt32LE(ppm, 42);
  buf.writeUInt32LE(0, 46);
  buf.writeUInt32LE(0, 50);
  for (let y = 0; y < height; y++) {
    const src = y * width * 3;
    const dst = 54 + (height - 1 - y) * stride;   // BMP rows go bottom-up
    for (let x = 0; x < width; x++) {
      buf[dst + x * 3] = rgb[src + x * 3 + 2];    // B
      buf[dst + x * 3 + 1] = rgb[src + x * 3 + 1]; // G
      buf[dst + x * 3 + 2] = rgb[src + x * 3];     // R
    }
  }
  return buf;
}

/** Frame a decoded image into TWxTH on white, as the official --mode scale does. */
function scaleAndPad(rgb, w, h, TW, TH) {
  if (w === TW && h === TH) return rgb;          // exactly what Pillow does: a copy
  throw new Error(
    `this image is ${w}x${h}, not ${TW}x${TH}: resizing in JS would not match ` +
    `Pillow's resampler bit for bit. Feed it images already at the target size ` +
    `(the cropper's *_pp.jpg), or do the resize with Pillow.`);
}

module.exports = {
  DEVICE_PALETTE, buildPalette, ditherToPalette, paletteToRgb, writeBmp24, scaleAndPad,
};

// --------------------------------------------------------------------- CLI
if (require.main === module) {
  const fs = require('fs');
  const path = require('path');
  let sharp;
  try {
    sharp = require('sharp');
  } catch (err) {
    console.error('sharp is required (it decodes JPEG with libjpeg-turbo, the same\n' +
                  'library Pillow uses - a pure-JS decoder gives different pixels):\n' +
                  '  npm install sharp');
    process.exit(2);
  }

  const args = process.argv.slice(2);
  const target = args.find((a) => !a.startsWith('-'));
  const force = args.includes('--force');
  if (!target) {
    console.error('usage: node photopainter.js <folder|file> [--force]');
    process.exit(2);
  }

  const files = fs.statSync(target).isDirectory()
    ? fs.readdirSync(target)
        .filter((f) => /\.(jpe?g|png|bmp)$/i.test(f) &&
                       !/_(scale|cut)_output\.bmp$/i.test(f) && !f.startsWith('._'))
        .map((f) => path.join(target, f))
    : [target];

  (async () => {
    const pal = buildPalette();
    let done = 0, skipped = 0, failed = 0;
    const started = Date.now();
    for (const file of files) {
      const out = file.replace(/\.[^.]+$/, '') + '_scale_output.bmp';
      if (!force && fs.existsSync(out) &&
          fs.statSync(out).mtimeMs >= fs.statSync(file).mtimeMs) { skipped++; continue; }
      try {
        const { data, info } = await sharp(file).raw().toBuffer({ resolveWithObject: true });
        if (info.channels !== 3) throw new Error(`${info.channels} channels, expected 3`);
        const [TW, TH] = info.width > info.height ? [800, 480] : [480, 800];
        const frame = scaleAndPad(data, info.width, info.height, TW, TH);
        const idx = ditherToPalette(frame, TW, TH, pal, 256);
        fs.writeFileSync(out, writeBmp24(paletteToRgb(idx, pal), TW, TH));
        done++;
      } catch (err) {
        failed++;
        console.error(`FAIL ${path.basename(file)}: ${err.message}`);
      }
    }
    const secs = ((Date.now() - started) / 1000).toFixed(1);
    console.log(`converted ${done}, skipped ${skipped}, failed ${failed} in ${secs}s`);
    process.exit(failed ? 1 : 0);
  })();
}
