#!/usr/bin/env python3
"""Fabrique les ressources de la vidéo de démo, sans rien emprunter : 5 visuels de projets abstraits (générés) et une
courte piste ambient (synthétisée). Résultat reproductible (graine fixe).

    python3 scripts/make-demo-assets.py --out /tmp/demo-assets      # projet-1.jpg … projet-5.jpg, music.wav

Dépendances de ce script seulement (pas de LiveQuill) : numpy, Pillow.
"""
import argparse, math, os, wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1600, 1200
rng = np.random.default_rng(7)


def grid():
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    return x / W, y / H


def save(arr, path):
    Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.6)).save(path, quality=88)


def blobs(path):
    """Halo de couleurs : quelques sources lumineuses floues."""
    x, y = grid()
    img = np.zeros((H, W, 3), np.float32) + np.array([0.04, 0.03, 0.10])
    for cx, cy, r, col in [(.3, .4, .35, (1, .35, .45)), (.7, .55, .4, (.3, .45, 1)), (.55, .2, .25, (1, .75, .3)), (.2, .85, .3, (.5, .2, .9))]:
        d = np.exp(-((x - cx) ** 2 + ((y - cy) * .75) ** 2) / (r * r * .18))
        img += d[..., None] * np.array(col) * .9
    img *= 2.2
    save(img / (1 + img) * 1.15, path)


def rings(path):
    """Anneaux concentriques en interférence."""
    x, y = grid()
    a = np.sin(math.tau * 14 * np.hypot(x - .35, (y - .5) * .75))
    b = np.sin(math.tau * 11 * np.hypot(x - .7, (y - .45) * .75))
    v = (a + b) * .25 + .5
    img = np.stack([.95 * v + .05, .55 * v * v + .1, .25 + .2 * (1 - v)], -1)
    save(img, path)


def flow(path):
    """Champ de lignes qui suivent un courant."""
    im = Image.new('RGB', (W, H), (12, 14, 18))
    d = ImageDraw.Draw(im)
    for _ in range(1400):
        px, py = rng.uniform(0, W), rng.uniform(0, H)
        col = tuple(int(c) for c in rng.choice([(64, 224, 208), (240, 240, 230), (255, 120, 90)], p=[.55, .3, .15]))
        pts = []
        for _ in range(60):
            ang = math.sin(px / 260) * 2.2 + math.cos(py / 210) * 1.7
            px, py = px + math.cos(ang) * 6, py + math.sin(ang) * 6
            pts.append((px, py))
        d.line(pts, fill=col, width=1)
    im.filter(ImageFilter.GaussianBlur(0.5)).save(path, quality=88)


def terrain(path):
    """Courbes de niveau d'un relief imaginaire."""
    x, y = grid()
    z = np.sin(x * 7 + np.sin(y * 5) * 1.5) + np.cos(y * 6 + np.sin(x * 4) * 1.8) + .5 * np.sin((x + y) * 13)
    bands = (np.mod(z * 6, 1) < .08).astype(np.float32)
    base = (z - z.min()) / (z.max() - z.min())
    img = np.stack([.10 + .25 * base, .30 + .45 * base, .18 + .15 * base], -1) * .7 + bands[..., None] * np.array([.9, .95, .8]) * .5
    save(img, path)


def shapes(path):
    """Composition géométrique, façon affiche."""
    im = Image.new('RGB', (W, H), (240, 233, 222))
    d = ImageDraw.Draw(im)
    pal = [(230, 72, 51), (24, 24, 28), (36, 89, 214), (247, 191, 64)]
    for i in range(7):
        for j in range(5):
            x0, y0, s = 80 + i * 210, 90 + j * 210, 170
            c = pal[int(rng.integers(len(pal)))]
            k = int(rng.integers(4))
            if k == 0:
                d.ellipse((x0, y0, x0 + s, y0 + s), fill=c)
            elif k == 1:
                d.rectangle((x0, y0, x0 + s, y0 + s), fill=c)
            elif k == 2:
                d.pieslice((x0, y0, x0 + 2 * s, y0 + 2 * s), 180, 270, fill=c)
            else:
                d.polygon([(x0, y0 + s), (x0 + s / 2, y0), (x0 + s, y0 + s)], fill=c)
    im.save(path, quality=90)


def music(path, seconds=60, rate=44100):
    """Ambient doux : nappes d'accords, petits motifs pincés, réverbération, fondus."""
    t = np.arange(int(seconds * rate)) / rate
    out = np.zeros_like(t)
    note = lambda n: 440 * 2 ** ((n - 69) / 12)
    chords = [[48, 55, 59, 64], [45, 52, 57, 60, 64], [41, 48, 52, 57], [43, 50, 55, 59, 62]]   # Cmaj7 Am9 Fmaj7 G6
    bar = 4.0
    for k in range(int(seconds / bar) + 1):
        start, ch = k * bar, chords[k % len(chords)]
        seg = (t >= start) & (t < start + bar + 1.5)
        tt = t[seg] - start
        env = np.minimum(1, tt / 1.2) * np.exp(-np.maximum(0, tt - bar + .3) * 2.5)
        for n in ch:
            f = note(n)
            out[seg] += env * .06 * (np.sin(math.tau * f * tt) + .35 * np.sin(math.tau * f * 1.003 * tt) + .2 * np.sin(math.tau * f * 2 * tt))
        for i, n in enumerate([ch[-1] + 12, ch[1] + 12, ch[2] + 12, ch[-1] + 12, ch[0] + 24, ch[2] + 12, ch[1] + 12, ch[-1] + 12]):
            s0 = start + i * bar / 8
            seg2 = (t >= s0) & (t < s0 + 1.2)
            u = t[seg2] - s0
            out[seg2] += .05 * np.exp(-u * 4) * np.sin(math.tau * note(n) * u)
    wet = out.copy()
    for d, g in [(.031, .45), (.047, .38), (.071, .3), (.113, .25)]:          # réverbération : échos amortis
        n = int(d * rate)
        for _ in range(6):
            wet[n:] += wet[:-n] * g * .5
    mix = .7 * out + .3 * wet / np.max(np.abs(wet))
    mix *= np.minimum(1, t / 3) * np.minimum(1, (seconds - t) / 4)          # fondus d'entrée et de sortie
    mix = mix / np.max(np.abs(mix)) * .6
    stereo = np.stack([mix, np.roll(mix, int(.012 * rate))], -1)
    with wave.open(path, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes((stereo * 32767).astype('<i2').tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--seconds', type=float, default=60)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for i, f in enumerate([blobs, rings, flow, terrain, shapes], 1):
        f(os.path.join(a.out, f'project-{i}.jpg'))
    music(os.path.join(a.out, 'music.wav'), a.seconds)
    print(a.out)


if __name__ == '__main__':
    main()
