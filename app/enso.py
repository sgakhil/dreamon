"""Ensō brush geometry, independent of any drawing library.

An ensō is one brushstroke: it lands with a little press, runs round, thins,
and breaks into dry bristles as the ink runs out, leaving the circle open.
`stamps()` returns that stroke as overlapping circles (x, y, r, t), where t is
how far along the stroke (0..1) each stamp sits. Filling all the circles as one
path (non-zero winding) gives the stroke without alpha build-up.

The same seed always gives the same ensō, and a smaller `progress` draws the
beginning of the same stroke with the brush lifting off at its end, so a
sitting's ensō grows smoothly and each day's ensō keeps its own shape.
"""

from __future__ import annotations

import math
import random

Stamp = tuple[float, float, float, float]   # x, y, radius, t


def stamps(cx: float, cy: float, radius: float, progress: float = 1.0, seed: int = 0,
           width: float = 0.13, bristles: bool = True, lift: float = 0.05) -> list[Stamp]:
    """The stroke drawn as far as `progress`; short of the end, the brush lifts
    off over the last `lift` of the drawn part, narrowing to a point."""
    rnd = random.Random(seed)
    ph = [rnd.uniform(0, math.tau) for _ in range(5)]
    start = math.radians(122 + rnd.uniform(-8, 8))      # lands at the lower left
    sweep = math.radians(332 + rnd.uniform(-6, 4))      # runs clockwise, leaves a gap
    full_w = radius * width
    step = max(0.35, min(full_w * 0.1, 1.0))
    n = max(12, int(radius * sweep / step))
    progress = max(0.0, min(1.0, progress))
    last = int(n * progress)

    # Bristles: each runs out of ink at its own point; the outer ones first.
    dry_from = 0.66
    strand_count = 11
    strands = []
    for j in range(strand_count):
        pos = j / (strand_count - 1) * 2 - 1             # -1 … 1 across the brush
        reach = 1 - 0.45 * abs(pos) ** 1.2
        ends = dry_from + (1 - dry_from) * reach * rnd.uniform(0.45, 1.0)
        thick = rnd.uniform(0.07, 0.15)
        gaps = [(g, g + rnd.uniform(0.004, 0.02)) for g in
                (rnd.uniform(dry_from, ends) for _ in range(rnd.randint(0, 2)))]
        strands.append((pos, ends, thick, gaps))

    out: list[Stamp] = []
    for i in range(last + 1):
        t = i / n
        th = start + sweep * t
        r = radius * (1 + 0.02 * math.sin(2 * th + ph[0]) + 0.011 * math.sin(3 * th + ph[1])
                      - 0.035 * (t - 0.5))              # the tail drifts slightly inward
        x, y = cx + r * math.cos(th), cy + r * math.sin(th)
        nx, ny = math.cos(th), math.sin(th)              # across the stroke

        # width: a firm press, a full body that breathes, then the ink thins
        body = 1.15 - 0.35 * t
        body *= 1 + 0.10 * math.sin(4.7 * t * math.tau / 4 + ph[2]) + 0.05 * math.sin(11 * t + ph[3])
        if t < 0.03:
            body *= 1 + 0.18 * (1 - t / 0.03)            # a blunt press, not a ball
        half = full_w * body / 2
        if progress < 1.0 and progress - t < lift:      # the brush lifting off
            half *= 0.18 + 0.82 * ((progress - t) / lift) ** 0.7
        drift = half * 0.12 * math.sin(7 * t + ph[4])    # the brush wanders across its line
        x, y = x + nx * drift, y + ny * drift

        if not bristles or t < dry_from:
            jag = 1 + rnd.uniform(-0.05, 0.05)
            out.append((x, y, half * jag, t))
            if t < 0.03:                                  # ragged landing edge
                for k in (-0.55, 0.55):
                    out.append((x + nx * half * k - ny * half * 0.35,
                                y + ny * half * k + nx * half * 0.35,
                                half * rnd.uniform(0.45, 0.6), t))
            continue

        dry = (t - dry_from) / (1 - dry_from)
        core = half * (1 - dry / 0.35) if dry < 0.35 else 0
        if core > 0.3:
            out.append((x, y, core, t))
        for pos, ends, thick, gaps in strands:
            if t > ends or any(a <= t <= b for a, b in gaps):
                continue
            off = pos * half * (1 - 0.25 * dry)
            out.append((x + nx * off, y + ny * off, max(0.4, half * thick), t))
    return out


def dot(cx: float, cy: float, radius: float) -> list[Stamp]:
    """A single small mark, for a day with no practice."""
    return [(cx, cy, radius, 0.0)]


def split_by(stamps_: list[Stamp], shares: list[tuple[str, float]]) -> list[tuple[str, list[Stamp]]]:
    """Split a stroke into consecutive runs, one per (name, fraction-of-stroke)."""
    out: list[tuple[str, list[Stamp]]] = []
    bounds = []
    acc = 0.0
    for name, share in shares:
        bounds.append((name, acc, acc + share))
        acc += share
    for name, lo, hi in bounds:
        run = [s for s in stamps_ if lo <= s[3] < hi or (hi >= acc and s[3] >= lo)]
        if run:
            out.append((name, run))
    return out

