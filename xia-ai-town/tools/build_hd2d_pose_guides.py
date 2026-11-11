#!/usr/bin/env python3
"""Rebuild pose-only guides for image generation; these are not runtime sprites.

One shared low-clearance walking cycle is projected in all eight directions.
The model receives the skeleton plus the separate original outfit reference.
"""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DIRECTIONS = ('s', 'sw', 'w', 'nw', 'n', 'ne', 'e', 'se')


def pose(column: int):
    walking = column >= 2
    phase = (column - 2) / 8 if walking else 0.25
    hip_height = (0.025 + math.sqrt(0.45 ** 2 - (0.135 * math.cos(phase * math.tau)) ** 2) - 0.008) if walking else 0.473 + column * 0.002
    points = {}
    for side, sign in [('left', 1), ('right', -1)]:
        t = (phase + (0.5 if side == 'right' else 0)) % 1
        z = 0.135 * math.cos(t * math.tau) if walking else 0.0
        lift = 0.045 * math.sin((t - 0.5) * math.tau) if walking and t > 0.5 else 0.0
        foot = np.array([sign * 0.072, 0.025 + lift, z])
        hip = np.array([sign * 0.065, hip_height, 0.0])
        delta = foot - hip
        distance = np.linalg.norm(delta)
        # Equal thigh/shin IK, with the knee bending gently towards the toes.
        normal = np.array([0.0, -delta[2], delta[1]])
        normal *= -1 if normal[2] < 0 else 1
        normal /= np.linalg.norm(normal)
        knee = (hip + foot) / 2 + normal * math.sqrt(max(0, 0.225 ** 2 - (distance / 2) ** 2))
        arm_z = -z * 0.7
        shoulder = np.array([sign * 0.132, hip_height + 0.27, 0.008])
        elbow = np.array([sign * 0.147, hip_height + 0.13, arm_z * 0.35])
        hand = np.array([sign * 0.143, hip_height + 0.01, arm_z])
        points.update({f'{side}_hip': hip, f'{side}_knee': knee, f'{side}_foot': foot,
                       f'{side}_shoulder': shoulder, f'{side}_elbow': elbow, f'{side}_hand': hand})
    points['head'] = np.array([0.0, hip_height + 0.415, 0.006])
    points['nose'] = points['head'] + np.array([0.0, -0.005, 0.103])
    points['bun'] = points['head'] + np.array([0.0, 0.103, -0.038])
    return points


def build() -> None:
    out = ROOT / 'asserts/hd2d/characters/hero-v2'
    out.mkdir(parents=True, exist_ok=True)
    for row, direction in enumerate(DIRECTIONS):
        angle = -row * math.pi / 4
        image = Image.new('RGB', (1600, 1024), '#ff00ff')
        draw = ImageDraw.Draw(image)
        for col in range(10):
            x0, y0 = col % 5 * 320 + 160, col // 5 * 512 + 451
            p = pose(col)
            def project(v):
                x, height, z = v
                x, z = x * math.cos(angle) + z * math.sin(angle), -x * math.sin(angle) + z * math.cos(angle)
                return (round(x0 + x * 380), round(y0 - height * 380 + z * 65))
            screen = {k: project(v) for k, v in p.items()}
            order = sorted(('left', 'right'), key=lambda side: -p[f'{side}_hip'][0] * math.sin(angle))
            for side in order:
                color = '#7e969f' if side == 'right' else '#cdd6d1'
                for a, b in [('hip', 'knee'), ('knee', 'foot'), ('shoulder', 'elbow'), ('elbow', 'hand')]:
                    draw.line([screen[f'{side}_{a}'], screen[f'{side}_{b}']], fill=color, width=16)
                foot = p[f'{side}_foot']
                draw.line([project(foot + np.array([0, -0.01, -0.025])), project(foot + np.array([0, -0.01, 0.06]))], fill=color, width=13)
                for joint in ('knee', 'elbow'):
                    x, y = screen[f'{side}_{joint}']
                    draw.ellipse((x-7, y-7, x+7, y+7), fill='#e9c08a')
            draw.polygon([screen[k] for k in ('left_shoulder', 'right_shoulder', 'right_hip', 'left_hip')], fill='#9daaa6')
            x, y = screen['head']
            draw.ellipse((x-34, y-39, x+34, y+34), fill='#d4bf9e')
            x, y = screen['bun']
            draw.ellipse((x-12, y-10, x+12, y+10), fill='#303b40')
            if math.cos(angle) >= -0.01:
                x, y = screen['nose']
                draw.ellipse((x-4, y-4, x+4, y+4), fill='#986e50')
        image.save(out / f'guide-{direction}.png')
        print(out / f'guide-{direction}.png')

if __name__ == '__main__':
    build()
