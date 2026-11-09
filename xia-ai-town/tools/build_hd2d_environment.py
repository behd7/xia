"""Rebuild P3 town architecture and furniture in isolated Blender scenes.

Uses the approved pixel materials and the P1 exterior-shell mesher. Source .blend
libraries stay under ignored asserts/hd2d/. Runtime GLBs contain geometry only;
Godot owns placement, shared materials, lighting and local occlusion. No gameplay
coordinates, old textures, user scenes or P1 assets are rewritten.

blender --background --factory-startup --python-exit-code 1 \
    --python xia-ai-town/tools/build_hd2d_environment.py [-- --check]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_hd2d_house as house

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/hd2d'
OWNER = 'xia.p3.environment.builder.v1'
house.OWNER = OWNER
MATERIALS = {
    **{k: v for k, v in house.MATERIALS.items() if k != 'sign'},
    'iron': {'texture': 'materials/stone', 'tint': [.32, .36, .37]},
    'rock': {'texture': 'materials/rock', 'tint': [.86, .86, .82]},
    'earth': {'texture': 'materials/earth', 'tint': [.92, .88, .72]},
    'cloth': {'texture': 'materials/window_paper', 'tint': [.48, .58, .57]},
    'straw': {'texture': 'materials/wood', 'tint': [.72, .66, .42]},
    'foliage': {'texture': 'materials/pine_needles', 'tint': [.8, .94, .79], 'foliage': True},
    'bark': {'texture': 'materials/bark', 'tint': [.85, .83, .76]},
    'flower': {'texture': 'materials/window_paper', 'tint': [.83, .60, .60]},
    'moss': {'texture': 'materials/stone', 'tint': [.56, .64, .45]},
    'water': {'texture': 'materials/stone', 'tint': [.29, .48, .48], 'water': True},
}
# These sizes are checked against TownLayout when instantiated, never used for
# gameplay. The inn continues to use its already approved P1 GLB unchanged.
BUILDINGS = {
    'jianyang': (160, 96, 104, '剑阳道馆'),
    'xuantie': (160, 96, 100, '玄铁堂'),
    'yanyu': (160, 96, 94, '烟雨小筑'),
    'clinic': (128, 96, 86, '药铺'),
    'smith': (128, 96, 90, '铁匠铺'),
    'temple': (160, 112, 98, '破庙'),
}
PROPS = ['pine', 'dead_tree', 'shrub', 'bramble', 'boulder', 'outcrop', 'well',
         'fence', 'table', 'rack', 'lantern', 'bale', 'barrel', 'herbs', 'dummy',
         'mat', 'pine2', 'cypress', 'bare_tree', 'dry_grass', 'mossy_boulder',
         'slab', 'lichen_rock', 'log', 'stump', 'bamboo', 'thorn_bush',
         'flower_shrub', 'puddle', 'cairn', 'wheel', 'firewood']
# Pine and lantern use the approved sample models/textures at runtime.
SHARED = {'pine', 'pine2', 'lantern'}


def segment(part, a, b, radius, material, sides=6, tip=None):
    """Closed tapered branch with deterministic, metric UVs and flat normals."""
    from mathutils import Vector
    a, b = Vector(a), Vector(b)
    axis = (b-a).normalized()
    ref = Vector((0, 1, 0)) if abs(axis.y) < .9 else Vector((1, 0, 0))
    u = axis.cross(ref).normalized()
    v = axis.cross(u).normalized()
    rings = [[tuple(center+(u*math.cos(t)+v*math.sin(t))*r)
              for t in [2*math.pi*i/sides for i in range(sides)]]
             for center, r in ((a, radius), (b, radius if tip is None else tip))]
    part.face(list(reversed(rings[0])), material)
    part.face(rings[1], material)
    for i in range(sides):
        j = (i+1) % sides
        part.face([rings[0][i], rings[0][j], rings[1][j], rings[1][i]], material)


def volume(part, center, size, material, seed=0, sides=8):
    """Closed asymmetric low-poly mass; leaves and rocks are never whole cards."""
    x, h, z = center
    sx, sy, sz = size
    rings = []
    first_face = len(part.faces)
    for row, (height, radius) in enumerate(((-.46, .52), (-.13, 1), (.22, .9), (.48, .38))):
        ring = []
        for i in range(sides):
            angle = -i*2*math.pi/sides
            ripple = 1 + .09*math.sin(i*2.3+seed+row*.7)
            ring.append((x+math.cos(angle)*sx*radius*.5*ripple,
                         h+height*sy, z+math.sin(angle)*sz*radius*.5*ripple))
        rings.append(ring)
    part.face(list(reversed(rings[0])), material)
    part.face(rings[-1], material)
    for lower, upper in zip(rings, rings[1:]):
        for i in range(sides):
            j = (i+1) % sides
            part.face([lower[i], lower[j], upper[j], upper[i]], material)
    # Signed volume catches globally reversed normals even when every edge is
    # closed and every triangle is non-degenerate. Check before mesh merging.
    signed = 0.0
    for face in part.faces[first_face:]:
        a = part.vertices[face[0]]
        for i in range(1, len(face)-1):
            b, c = part.vertices[face[i]], part.vertices[face[i+1]]
            signed += (a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0]))/6
    if signed <= 1e-12:
        raise RuntimeError('Closed volume has inward normals: '+part.name)


def roof(part, half_x, half_z, eave, rise=32, worn=False):
    xs = [-half_x-10, -half_x, 0, half_x, half_x+10]
    depth = half_z+12
    profile = [(0, eave+rise), (half_z*.45, eave+rise*.63),
               (half_z, eave+2), (depth, eave+3)]
    lift = lambda x: max(0, (abs(x)-half_x+12)/22)**2*2
    for sign in (-1, 1):
        for xi, (x0, x1) in enumerate(zip(xs, xs[1:])):
            for row, ((z0, h0), (z1, h1)) in enumerate(zip(profile, profile[1:])):
                top = [(x0, h0+lift(x0), sign*z0), (x1, h0+lift(x1), sign*z0),
                       (x1, h1+lift(x1), sign*z1), (x0, h1+lift(x0), sign*z1)]
                # One chipped section in the temple eave, with a closed break.
                if worn and xi == 0 and row == 2 and sign == 1:
                    top[2] = (top[2][0], top[2][1]-3, top[2][2]-3)
                    top[3] = (top[3][0], top[3][1]-3, top[3][2]-3)
                low = [(x, h-3, z) for x, h, z in top]
                part.face(top if sign < 0 else list(reversed(top)), 'roof')
                part.face(low if sign > 0 else list(reversed(low)), 'wood')
                if row == len(profile)-2:
                    part.face([low[3], low[2], top[2], top[3]], 'wood_light')
                if xi == 0:
                    part.face([low[0], low[3], top[3], top[0]], 'roof')
                if xi == len(xs)-2:
                    part.face([low[2], low[1], top[1], top[2]], 'roof')
        for x in range(int(-half_x), int(half_x)+1, 12):
            part.beam((x, eave-3, sign*(half_z-3)),
                      (x, eave+lift(x)-2, sign*(depth-1)), 2.5, 2.5, 'wood_light')
    # A single solid ridge meets both slopes. Gable infill does not close each
    # triangle fan internally; only the two outer slopes need a thickness cap.
    part.box((-half_x-10, eave+rise-.4, -2.5), (half_x+10, eave+rise+4, 2.5), 'roof')
    for side in (-1, 1):
        x0, x1 = sorted((side*half_x, side*(half_x-4)))
        for sign in (-1, 1):
            triangle = [(0, eave-2), (0, eave+rise-3), (sign*half_z, eave-2)]
            if sign < 0:
                triangle.reverse()
            part.face([(x0, h, z) for z, h in reversed(triangle)], 'plaster')
            part.face([(x1, h, z) for z, h in triangle], 'plaster')
            za, ha = 0, eave+rise-3
            zb, hb = sign*half_z, eave-2
            cap = [(x0, ha, za), (x1, ha, za), (x1, hb, zb), (x0, hb, zb)]
            part.face(cap if sign < 0 else list(reversed(cap)), 'plaster')


def opening(part, left, right, low, high, front, door=False, damaged=False):
    back = front-7
    part.box((left, low, back-2), (right, high, back), 'wood_recess' if door else 'paper')
    part.front_frame(left-2.5, right+2.5, low, high+2.5, back, front+3, 2.5, 'wood')
    part.box((left-4, low-1, front-5), (right+4, low+1, front+5), 'stone' if door else 'wood_light')
    if door:
        middle = (left+right)/2
        for a, b in ((left+2, middle-1), (middle+1, right-2)):
            part.front_frame(a, b, low+2, high-2, back, front-3, 2, 'wood_light')
        for x in (middle-3.5, middle+3.5):
            part.front_frame(x-2, x+2, low+28, low+33, front-3, front-1, 1, 'bronze')
    else:
        for x in (left+(right-left)/3, left+(right-left)*2/3):
            if not damaged or x < (left+right)/2:
                part.box((x-1, low+2, back), (x+1, high-2, front+1), 'wood_light')
        part.box((left+2, (low+high)/2-1, back), (right-2, (low+high)/2+1, front+1), 'wood_light')


def building(key):
    width, depth, height, _ = BUILDINGS[key]
    hx, hz = width/2, depth/2
    part = house.Component(key+'_structure', f'building/{key}')
    part.box((-hx, 0, -hz), (hx, 6, hz), 'stone')
    part.box((-hx, 6, -hz), (hx, height, -hz+4), 'plaster')
    for a, b in ((-hx, -hx+4), (hx-4, hx)):
        part.box((a, 6, -hz+4), (b, height, hz), 'plaster')
    # Front wall is a union of solid spans around real recessed openings.
    door_half = 13 if key in ('clinic', 'smith') else 16
    windows = [(-hx*.67-12, -hx*.67+12), (hx*.67-12, hx*.67+12)]
    holes = [(a, b, 33, 65, False) for a, b in windows] + [(-door_half, door_half, 6, 71, True)]
    holes.sort()
    start = -hx
    for left, right, low, high, door in holes:
        if left > start:
            part.box((start, 6, hz-4), (left, height, hz), 'plaster')
        if low > 6:
            part.box((left, 6, hz-4), (right, low, hz), 'plaster')
        part.box((left, high, hz-4), (right, height, hz), 'plaster')
        opening(part, left, right, low, high, hz, door, key == 'temple')
        start = right
    part.box((start, 6, hz-4), (hx, height, hz), 'plaster')
    # Coherent perimeter rail, vertical posts, stone feet and exposed corbels.
    for z0, z1 in ((-hz, -hz+5), (hz-5, hz)):
        part.box((-hx, height, z0), (hx, height+4, z1), 'wood')
    for x0, x1 in ((-hx, -hx+5), (hx-5, hx)):
        part.box((x0, height, -hz+5), (x1, height+4, hz-5), 'wood')
    for x in (-hx+3, -door_half-5, door_half+5, hx-3):
        part.box((x-2, 6, hz-1), (x+2, height+2, hz+3), 'wood')
        part.box((x-3, 6, hz-2), (x+3, 13, hz+4), 'stone')
        part.box((x-4, height-5, hz-2), (x+4, height, hz+5), 'wood_light')
    part.box((-25, 76, hz), (25, 87, hz+3), 'wood_recess')
    part.front_frame(-27, 27, 75, 88, hz+2, hz+4, 2, 'wood_light')
    roof(part, hx, hz, height+5, 34 if key in ('jianyang', 'temple') else 28, key == 'temple')
    # Occupation-specific solid reliefs/fixtures, readable from moving cameras.
    if key == 'jianyang':
        segment(part, (-17, 81, hz+5), (17, 81, hz+5), 1.3, 'bronze', 4)
        part.box((-2, 77, hz+4), (2, 85, hz+6), 'bronze')
        for x in (-hx+18, hx-18):
            part.box((x-6, 6, hz+1), (x+6, 10, hz+10), 'stone')
            segment(part, (x, 10, hz+5), (x, 45, hz+5), 1.8, 'wood', 6)
            segment(part, (x, 45, hz+5), (x, 68, hz+5), 1.3, 'iron', 4)
    elif key == 'xuantie':
        part.box((-16, 78, hz+4), (16, 84, hz+7), 'iron')
        part.box((-5, 76, hz+4), (5, 87, hz+8), 'bronze')
        for x in (-hx+16, hx-16):
            volume(part, (x, 14, hz+3), (15, 17, 15), 'stone', int(x))
            part.box((x-7, 22, hz-2), (x+7, 25, hz+8), 'iron')
    elif key == 'yanyu':
        for x in (-16, -8, 0, 8, 16):
            segment(part, (x, 78, hz+5), (x, 85-abs(x)*.16, hz+5), 1, 'bronze', 4)
        for x in (-hx+9, hx-9):
            part.box((x-5, 8, hz+1), (x+5, 61, hz+3), 'cloth')
    elif key == 'clinic':
        part.box((-2, 77, hz+4), (2, 86, hz+6), 'bronze')
        part.box((-8, 80, hz+4), (8, 83, hz+6), 'bronze')
        # Shallow herbal drawers are inside the facade envelope, never a new
        # obstacle across the door or the shop interaction apron.
        for x in (-hx+9, -hx+17, -hx+25):
            for h in (14, 23, 32):
                part.box((x, h, hz+.2), (x+7, h+7, hz+4), 'wood_light')
                part.box((x+2, h+3, hz+4), (x+5, h+4, hz+5), 'bronze')
    elif key == 'smith':
        # Stone chimney rises beside the roof, with a dark recessed mouth.
        part.box((hx-25, 6, -hz+8), (hx-7, height+36, -hz+26), 'stone')
        part.box((hx-27, height+36, -hz+6), (hx-5, height+40, -hz+28), 'iron')
        part.box((hx-23, height+40, -hz+24), (hx-9, height+42, -hz+26), 'stone')
        part.box((-14, 80, hz+4), (14, 83, hz+8), 'iron')
        part.box((-6, 77, hz+4), (6, 81, hz+7), 'iron')
    elif key == 'temple':
        # Worn columns, boarded lower pane, a chipped ridge and stone incense
        # vessel distinguish the ruin without adding an enterable interior.
        segment(part, (-hx*.67-10, 35, hz+3), (-hx*.67+10, 55, hz+3), 1.6, 'bark', 4)
        for x in (-hx+5, hx-5):
            part.box((x-3, 15, hz+3), (x+3, 65, hz+4), 'wood_recess')
        volume(part, (0, 82, hz+5), (10, 9, 3), 'bronze', 7, 6)
        for x in (-hx+14, hx-14):
            volume(part, (x, 10, hz-3), (13, 8, 13), 'moss', int(x))
    part.box_shell()
    return [part]


def prop(key):
    p = house.Component(key, 'prop/'+key)
    if key in ('shrub', 'bramble', 'thorn_bush', 'flower_shrub'):
        for i, (x, z, h, width) in enumerate(((-6, 1, 9, 15), (4, -3, 12, 17), (0, 5, 7, 15))):
            volume(p, (x, h, z), (width, h*1.6, width*.8), 'foliage', i)
            segment(p, (x, 0, z), (x, h, z), 1, 'bark', 5, .5)
        if key in ('bramble', 'thorn_bush'):
            for i in range(5):
                a = i*2.4
                segment(p, (0, 3, 0), (math.sin(a)*13, 14+i, math.cos(a)*9), 1, 'bark', 4, .3)
        if key == 'flower_shrub':
            for x, h, z in ((-7, 14, 2), (5, 20, -3), (0, 13, 6), (10, 13, 2)):
                volume(p, (x, h, z), (4, 3, 4), 'flower', int(h), 5)
    elif key in ('dead_tree', 'bare_tree'):
        segment(p, (0, 0, 0), (-3, 34, 2), 4, 'bark', 7, 2.7)
        segment(p, (-3, 34, 2), (4, 74, 1), 2.7, 'bark', 6, .9)
        for i, (x, h, z) in enumerate(((-23, 55, 4), (20, 57, -6), (-12, 78, -4), (15, 82, 3))):
            segment(p, (-2, 29+i*7, 1), (x, h, z), 2, 'bark', 5, .6)
            segment(p, (x, h, z), (x*1.16, h+12, z+4), .6, 'bark', 4, .25)
        for x, z in ((-7, -3), (6, 4), (0, -8)):
            segment(p, (x, 0, z), (0, 5, 0), 2, 'bark', 5, 3)
    elif key in ('boulder', 'outcrop', 'mossy_boulder', 'slab', 'lichen_rock'):
        height = 10 if key == 'slab' else (31 if key == 'outcrop' else 19)
        volume(p, (0, height/2, 0), (29, height, 22), 'rock', len(key), 7)
        if key in ('mossy_boulder', 'lichen_rock'):
            volume(p, (-4, height*.88, -3), (16, 3, 13), 'moss', 5, 6)
    elif key == 'well':
        # Open octagonal ring: water below the rim, never a solid covered box.
        for i in range(8):
            a, b = i*math.pi/4, (i+1)*math.pi/4
            outer = [(math.cos(t)*16, math.sin(t)*16) for t in (a, b)]
            inner = [(math.cos(t)*11, math.sin(t)*11) for t in (a, b)]
            p.face([(x, 22, z) for x, z in reversed((outer[0], outer[1], inner[1], inner[0]))], 'stone')
            p.face(list(reversed([(outer[0][0], 0, outer[0][1]), (outer[1][0], 0, outer[1][1]),
                    (outer[1][0], 22, outer[1][1]), (outer[0][0], 22, outer[0][1])])), 'stone')
            p.face(list(reversed([(inner[1][0], 3, inner[1][1]), (inner[0][0], 3, inner[0][1]),
                    (inner[0][0], 22, inner[0][1]), (inner[1][0], 22, inner[1][1])])), 'wood_recess')
        p.face([(math.cos(i*math.pi/4)*11, 4, math.sin(i*math.pi/4)*11) for i in reversed(range(8))], 'water')
        for x in (-13, 13):
            p.box((x-2, 18, -2), (x+2, 62, 2), 'wood')
        segment(p, (-16, 46, 0), (16, 46, 0), 2.2, 'wood_light', 8)
        segment(p, (0, 17, 0), (0, 46, 0), .6, 'straw', 4)
        roof(p, 15, 12, 62, 13)
    elif key == 'lantern_post':
        p.box((-5, 0, -5), (5, 5, 5), 'stone')
        segment(p, (0, 5, 0), (0, 78, 0), 2.2, 'wood', 6, 1.7)
        p.box((-2, 74, -2), (12, 78, 2), 'wood_light')
    elif key == 'fence':
        for x in (-19, 0, 19):
            p.box((x-1.5, 0, -2), (x+1.5, 23, 2), 'wood')
        for h in (8, 17):
            p.box((-22, h, -1.5), (22, h+2, 1.5), 'wood_light')
    elif key == 'table':
        p.box((-17, 19, -11), (17, 22, 11), 'wood_light')
        for x in (-13, 13):
            for z in (-7, 7):
                p.box((x-1.8, 0, z-1.8), (x+1.8, 19, z+1.8), 'wood')
        for z in (-16, 16):
            p.box((-16, 10, z-2), (16, 13, z+2), 'wood_light')
            for x in (-12, 12):
                p.box((x-1, 0, z-1.5), (x+1, 10, z+1.5), 'wood')
        volume(p, (2, 24, 0), (5, 5, 5), 'stone', 2, 6)
    elif key == 'rack':
        for x in (-18, 18):
            p.box((x-2, 0, -3), (x+2, 35, 3), 'wood')
            p.box((x-5, 0, -8), (x+5, 3, 8), 'wood')
        for h in (10, 30):
            p.box((-20, h, -2), (20, h+3, 2), 'wood_light')
        for x in (-11, -3, 5, 13):
            segment(p, (x, 2, 0), (x, 42, 0), 1, 'wood_light', 5)
            segment(p, (x, 42, 0), (x, 53, 0), 1.5, 'iron', 4, .4)
    elif key == 'bale':
        volume(p, (0, 11, 0), (24, 20, 19), 'straw', 3, 8)
        for x in (-6, 6):
            p.box((x-1, 1, -10), (x+1, 20, -8), 'wood_recess')
            p.box((x-1, 20, -8), (x+1, 21, 8), 'wood_recess')
    elif key == 'barrel':
        segment(p, (0, 1, 0), (0, 22, 0), 9, 'wood_light', 10)
        for h in (4, 17):
            segment(p, (0, h, 0), (0, h+2, 0), 9.5, 'iron', 10)
    elif key == 'herbs':
        p.box((-14, 0, -9), (14, 4, 9), 'wood')
        for x in (-8, 0, 8):
            volume(p, (x, 7, 0), (8, 8, 14), 'foliage', x, 6)
            p.box((x-1, 6, -6), (x+1, 8, 6), 'straw')
    elif key == 'dummy':
        segment(p, (0, 0, 0), (0, 41, 0), 3, 'wood', 7)
        volume(p, (0, 29, 0), (17, 22, 12), 'straw', 4, 8)
        volume(p, (0, 45, 0), (11, 11, 10), 'straw', 5, 7)
        segment(p, (-21, 30, 0), (21, 30, 0), 2.6, 'wood_light', 6)
        p.box((-8, 0, -6), (8, 3, 6), 'stone')
    elif key == 'mat':
        p.box((-17, 0, -11), (17, 1, 11), 'straw')
        for x in (-15, 13):
            p.box((x, 1, -11), (x+2, 1.3, 11), 'cloth')
    elif key == 'cypress':
        segment(p, (0, 0, 0), (2, 89, -1), 4, 'bark', 7, 1)
        for i in range(7):
            volume(p, (math.sin(i)*3, 32+i*10, math.cos(i)*2),
                   (28-i*2, 28, 25-i*2), 'foliage', i, 7)
    elif key == 'dry_grass':
        for i in range(9):
            angle = i*2.3
            segment(p, (0, 0, 0), (math.sin(angle)*8, 8+i%4*2, math.cos(angle)*6),
                    .75, 'straw', 4, .15)
    elif key in ('log', 'firewood', 'stump'):
        if key == 'stump':
            segment(p, (0, 0, 0), (1, 12, 0), 7, 'bark', 8, 6)
            segment(p, (1, 12, 0), (1, 12.4, 0), 4.7, 'wood_light', 8)
            for x, z in ((-8, 0), (6, 5), (0, -8)):
                segment(p, (x, 0, z), (0, 4, 0), 2.8, 'bark', 5, 4)
        else:
            for i in range(1 if key == 'log' else 6):
                y = 4+i//3*6
                z = (i%3-1)*6 if key == 'firewood' else 0
                segment(p, (-14, y, z), (14, y, z+1), 3.5 if key == 'firewood' else 5,
                        'bark', 7)
                segment(p, (14, y, z+1), (14.3, y, z+1), 2.7 if key == 'firewood' else 4,
                        'wood_light', 7)
    elif key == 'bamboo':
        for i, (x, z, h) in enumerate(((-7, 0, 66), (2, -4, 77), (8, 3, 58))):
            segment(p, (x, 0, z), (x+2, h, z), 1.5, 'foliage', 6, 1)
            for low in range(10, h, 12):
                segment(p, (x, low, z), (x, low+1.2, z), 1.8, 'moss', 6)
            for j in range(3):
                volume(p, (x+(-1)**j*7, h-8-j*12, z+3), (21, 5, 12), 'foliage', i+j, 6)
    elif key == 'puddle':
        volume(p, (0, .25, 0), (29, .3, 18), 'water', 2, 9)
        volume(p, (-10, 1, 4), (8, 2, 5), 'moss', 4, 6)
    elif key == 'cairn':
        for i in range(4):
            volume(p, ((i%2)*2, 3+i*5, 0), (19-i*3, 7, 14-i*2), 'stone', i, 6)
    elif key == 'wheel':
        for i in range(12):
            a, b = i*math.pi/6, (i+1)*math.pi/6
            segment(p, (math.cos(a)*13, 14+math.sin(a)*13, 0),
                    (math.cos(b)*13, 14+math.sin(b)*13, 0), 2, 'wood', 5)
            if i%2 == 0:
                segment(p, (0, 14, 0), (math.cos(a)*12, 14+math.sin(a)*12, 0), 1.2, 'wood_light', 4)
        segment(p, (0, 14, -3), (0, 14, 3), 3, 'iron', 8)
    else:
        raise ValueError(key)
    p.box_shell()
    return [p]


def gate():
    p = house.Component('gate', 'town/gate')
    for x0, x1 in ((-48, -32), (32, 48)):
        p.box((x0, 0, -16), (x1, 104, 16), 'stone')
        p.box((x0-1, 100, -17), (x1+1, 106, 17), 'wood')
    # Fixed-Y pitch compensation can raise the hero's envelope to 95 logical
    # pixels at 65 degrees. Keep the road clear through that entire height.
    p.box((-48, 100, -16), (48, 112, 16), 'stone')
    p.box((-30, 105, 16), (30, 116, 19), 'wood_recess')
    p.front_frame(-32, 32, 104, 117, 17, 20, 2, 'wood_light')
    roof(p, 49, 18, 117, 18)
    p.box_shell()
    return [p]


def make_material(key):
    name = 'XIA_P3_'+key
    old = bpy.data.materials.get(name)
    if old and old.get('xia_owner') != OWNER:
        raise RuntimeError('Refusing unrelated material '+name)
    mat = old or bpy.data.materials.new(name)
    mat['xia_owner'] = OWNER
    mat.diffuse_color = (*MATERIALS[key]['tint'], 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = mat.diffuse_color
    shader.inputs['Roughness'].default_value = 1
    return mat


def export_asset(key, parts, directory, materials, save_source):
    name = 'XIA_P3_'+key
    scene = bpy.data.scenes.get(name)
    if scene:
        if scene.get('xia_owner') != OWNER or any(o.get('xia_owner') != OWNER for o in scene.objects):
            raise RuntimeError('Refusing to replace a user scene/object: '+name)
        for obj in list(scene.objects):
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)
    else:
        scene = bpy.data.scenes.new(name)
        scene['xia_owner'] = OWNER
    if bpy.context.window:
        bpy.context.window.scene = scene
    objects = [p.finish(scene, materials) for p in parts]
    if any(obj.name != part.name for obj, part in zip(objects, parts)):
        raise RuntimeError('Blender renamed a component; use asset-unique object names: '+key)
    # glTF selection can otherwise retain objects in previously active scenes.
    for obj in scene.objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    directory.mkdir(parents=True, exist_ok=True)
    path = directory/(key+'.glb')
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_materials='VIEWPORT',
                              export_image_format='NONE', use_selection=True, use_active_scene=True,
                              export_extras=False, export_yup=True, export_cameras=False,
                              export_lights=False, export_apply=True)
    signature, doc = house.glb_signature(path)
    if doc.get('images') or doc.get('textures'):
        raise RuntimeError('Runtime GLB must use shared Godot textures')
    if save_source:
        source = ROOT/'asserts/hd2d'/directory.relative_to(OUT)/(key+'.blend')
        source.parent.mkdir(parents=True, exist_ok=True)
        bpy.data.libraries.write(str(source), {scene}, path_remap='RELATIVE', fake_user=True)
    bounds = [[min(v[i] for p in parts for v in p.vertices), max(v[i] for p in parts for v in p.vertices)] for i in range(3)]
    record = {'path': path.relative_to(directory.parent).as_posix(), 'geometry_sha256': signature,
              'mesh_count': len(parts), 'surface_count': sum(len(p.slots) for p in parts),
              'triangles': sum(len(o.data.loop_triangles) for o in objects),
              'components': {p.name: {'materials': p.slots} for p in parts}, 'bounds_blender': bounds}
    print('XIA_ENV_ASSET', key, record['triangles'], signature)
    return record


def build(output=OUT, save_source=True):
    output = Path(output)
    materials = {key: make_material(key) for key in MATERIALS}
    assets = {}
    for key, (w, d, h, title) in BUILDINGS.items():
        record = export_asset(key, building(key), output/'buildings', materials, save_source)
        record.update(footprint=[-w/2, -d/2, w, d], title=title,
                      lanterns=[[-w/2+14, d/2+8, 62], [w/2-14, d/2+8, 62]] if key in ('jianyang', 'xuantie', 'yanyu') else [])
        assets['building/'+key] = record
    for index, key in enumerate(PROPS):
        if key not in SHARED:
            record = export_asset(key, prop(key), output/'props', materials, save_source)
            record['index'] = index
            assets['prop/'+key] = record
    assets['prop/lantern_post'] = export_asset('lantern_post', prop('lantern_post'), output/'props', materials, save_source)
    assets['town/gate'] = export_asset('gate', gate(), output/'props', materials, save_source)
    metadata = {'schema': 1, 'builder': 'tools/build_hd2d_environment.py',
                'builder_sha256': house.digest(__file__), 'shell_builder_sha256': house.digest(house.__file__),
                'logical_scale': house.SCALE, 'materials': MATERIALS,
                'texture_sha256': {v['texture']: house.digest(ROOT/'assets/hd2d'/(v['texture']+'.png')) for v in MATERIALS.values()},
                'prop_keys': PROPS, 'assets': assets,
                'shared': {'prop/pine': 'props/pine.glb', 'prop/pine2': 'props/pine.glb', 'prop/lantern': 'props/lantern.png'},
                'review': 'P3 environment batch; original TownLayout owns gameplay; eight-way NPC/monster production is separate'}
    (output/'environment.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+'\n')
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.check:
        with tempfile.TemporaryDirectory(prefix='xia-env-check-') as folder:
            actual = build(Path(folder), save_source=False)
            expected = json.loads((OUT/'environment.json').read_text())
            if actual != expected:
                raise RuntimeError('Environment metadata differs; rebuild assets')
            for key, record in actual['assets'].items():
                if house.glb_signature(OUT/record['path'])[0] != record['geometry_sha256']:
                    raise RuntimeError('Environment GLB differs: '+key)
        print('XIA_ENV_CHECK_PASS: all assets, shells, material names, texture hashes and UVs are reproducible')
    else:
        build()


if __name__ == '__main__':
    main()
