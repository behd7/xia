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
# ScenicLayout supplies the gameplay footprints; these dimensions are checked
# when authoring the scene. The original P1 inn is a separate unchanged asset.
BUILDINGS = {
    'jianyang': (192, 112, 104, '剑阳道馆'),
    'xuantie': (176, 128, 100, '玄铁堂'),
    'yanyu': (192, 112, 94, '烟雨小筑'),
    'clinic': (144, 96, 86, '药铺'),
    'smith': (160, 112, 90, '铁匠铺'),
    'temple': (160, 112, 98, '破庙'),
    'inn_town': (224, 128, 150, '清溪客栈'),
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


def building_legacy(key):
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


def append_piece(target, piece, offset=(0, 0, 0)):
    """Merge authored modules without exporting hidden nested scene objects."""
    piece.box_shell()
    dx, dh, dz = offset
    start = len(target.vertices)
    target.vertices.extend((x+dx*house.SCALE, y-dz*house.SCALE, z+dh*house.SCALE)
                           for x, y, z in piece.vertices)
    for face, uv, slot in zip(piece.faces, piece.uvs, piece.material_ids):
        material = piece.slots[slot]
        if material not in target.slots:
            target.slots.append(material)
        target.faces.append(tuple(start+i for i in face))
        target.uvs.append(uv)
        target.material_ids.append(target.slots.index(material))


def roof_at(p, x, z, w, d, eave, rise=27, worn=False):
    piece = house.Component('roof_module', '')
    roof(piece, w/2, d/2, eave, rise, worn)
    # Thin ridge ends and tile end caps articulate the eaves at game distance.
    for side in (-1, 1):
        for px in range(int(-w/2), int(w/2)+1, 8):
            segment(piece, (px, eave+2, side*(d/2+10)), (px, eave+2, side*(d/2+14)), 1.4, 'roof', 5)
    append_piece(p, piece, (x, 0, z))


def hall(p, x, z, w, d, base, high, *, upper=False, stone=False, open_front=False):
    piece = house.Component('hall_module', '')
    hx, hz = w/2, d/2
    wall = 'stone' if stone else 'plaster'
    piece.box((-hx, base, -hz), (hx, base+4, hz), 'stone' if not upper else 'wood')
    piece.box((-hx, base+4, -hz), (hx, high, -hz+4), wall)
    for side in (-1, 1):
        a, b = sorted((side*hx, side*(hx-4)))
        piece.box((a, base+4, -hz+4), (b, high, hz), wall)
        # Side elevation carries framed shutter recesses, not a blank plaster cube.
        for sz in (-d*.22, d*.22):
            a, b = sorted((side*(hx+.3), side*(hx+2)))
            piece.box((a, base+22, sz-10), (b, min(high-8, base+49), sz+10), 'wood_recess')
            for hh in range(int(base+24), int(min(high-8, base+49)), 6):
                piece.box((a, hh, sz-10), (b+0.2, hh+1.4, sz+10), 'wood_light')
    if not open_front:
        holes = [(-hx*.65-11, -hx*.65+11, base+24, high-12, False),
                 (-14, 14, base+4, high-10, not upper),
                 (hx*.65-11, hx*.65+11, base+24, high-12, False)]
        if w < 80:
            holes = [(-hx*.45, hx*.45, base+22, high-12, False)]
        cursor = -hx
        for left, right, low, top, door in holes:
            piece.box((cursor, base+4, hz-4), (left, high, hz), wall)
            if low > base+4:
                piece.box((left, base+4, hz-4), (right, low, hz), wall)
            piece.box((left, top, hz-4), (right, high, hz), wall)
            opening(piece, left, right, low, top, hz, door)
            cursor = right
        piece.box((cursor, base+4, hz-4), (hx, high, hz), wall)
    for px in (-hx+2, -hx/3, hx/3, hx-2):
        piece.box((px-2.2, base+4, hz-2), (px+2.2, high+3, hz+2), 'wood')
        piece.box((px-3, base, hz-3), (px+3, base+9, hz+3), 'stone')
        # Diagonal braces below the eave.
        piece.beam((px, high-12, hz), (px+9 if px < 0 else px-9, high, hz), 2.5, 2.5, 'wood_light')
    piece.box((-hx-1, high, -hz-1), (hx+1, high+4, hz+1), 'wood')
    append_piece(p, piece, (x, 0, z))


def rail(p, x0, x1, z, base, height=21):
    for x in range(int(x0), int(x1)+1, 12):
        p.box((x-1, base, z-1), (x+1, base+height, z+1), 'wood_light')
    for h in (base+3, base+height):
        p.box((x0-2, h, z-1.6), (x1+2, h+2, z+1.6), 'wood')


def awning(p, x0, x1, back, front, high, cloth=False):
    for x in (x0+2, x1-2):
        p.box((x-2, 0, front-2), (x+2, high-9, front+2), 'wood')
        p.box((x-3, 0, front-3), (x+3, 7, front+3), 'stone')
        p.beam((x, high-22, front), (x, high, back), 2, 2, 'wood_light')
    p.face([(x0,high,back),(x1,high,back),(x1,high-9,front),(x0,high-9,front)], 'cloth' if cloth else 'roof')
    p.face([(x0,high-1,back),(x0,high-10,front),(x1,high-10,front),(x1,high-1,back)], 'wood_light')
    p.box((x0, high-12, front-1), (x1, high-9, front+1), 'cloth' if cloth else 'wood')


def gable_steps(p, x, depth, base, peak):
    for z0,z1,high in [(-depth/2,-depth/6,peak-13),(-depth/6,depth/6,peak),(depth/6,depth/2,peak-13)]:
        p.box((x-2,base,z0),(x+2,high,z1),'plaster')
        p.box((x-3,high,z0-1),(x+3,high+3,z1+1),'roof')


def building(key):
    w,d,_,_ = BUILDINGS[key]
    p = house.Component(key+'_structure', 'building/'+key)
    if key == 'inn_town':
        hall(p,0,-6,w,d-12,0,78,open_front=True)
        hall(p,0,-13,w-16,d-36,82,145,upper=True)
        roof_at(p,0,-13,w-10,d-30,149,33)
        awning(p,-w/2-4,w/2+4,34,70,79)
        # The gallery is a real deck with spaced balusters and deep shadow.
        p.box((-w/2,80,30),(w/2,84,66),'wood_light')
        rail(p,-w/2+2,-22,64,84)
        rail(p,22,w/2-2,64,84)
        p.box((-31,57,53),(31,72,58),'wood_recess')
        p.front_frame(-33,33,55,74,57,60,2,'wood_light')
        for x in (-68,68):
            p.box((x-17,6,46),(x+17,28,50),'wood_recess')
            for xx in range(int(x-16),int(x+16),8):
                p.box((xx,8,50),(xx+6,26,52),'wood_light')
    elif key == 'jianyang':
        # Tall central hall, low symmetrical wings and a double roof.
        hall(p,0,-6,110,d-18,7,111)
        roof_at(p,0,-6,116,d-12,115,34)
        for x in (-76,76):
            hall(p,x,8,40,76,0,66)
            roof_at(p,x,8,44,78,70,18)
        awning(p,-55,55,28,60,79)
        for step in range(3):
            p.box((-28,step*2,56-step*4),(28,step*2+2,68-step*4),'stone')
        for x in (-40,40):
            segment(p,(x,4,62),(x,60,62),1.5,'iron',6,.4)
    elif key == 'xuantie':
        hall(p,0,0,w,d,0,93,stone=True)
        roof_at(p,0,0,w-5,d,97,26)
        for x in (-w/2,w/2):
            gable_steps(p,x,d,50,134)
        awning(p,-44,44,38,70,79)
        for x in (-58,58):
            p.box((x-8,3,58),(x+8,28,68),'iron')
            p.box((x-11,26,56),(x+11,31,70),'stone')
        # Broad iron straps anchor the massive doors.
        for h in (22,44,66):
            p.box((-13,h,65),(13,h+2,67),'iron')
    elif key == 'yanyu':
        hall(p,-28,-10,122,84,0,79)
        hall(p,-28,-14,112,76,83,136,upper=True)
        roof_at(p,-28,-14,122,82,140,27)
        # An open tea veranda on one side changes the entire outline.
        p.box((36,0,-36),(96,5,54),'stone')
        for x in (42,90):
            for z in (-30,48):
                p.box((x-2,5,z-2),(x+2,68,z+2),'wood')
        roof_at(p,66,9,64,90,71,19)
        rail(p,42,92,49,5,19)
        p.box((-89,81,25),(32,85,54),'wood_light')
        rail(p,-87,30,52,85,17)
    elif key == 'clinic':
        hall(p,0,-6,w,d-12,0,77)
        roof_at(p,0,-6,w,d-12,81,25)
        awning(p,-w/2+4,w/2-4,28,56,64,True)
        for x in (-50,46):
            p.box((x-12,0,48),(x+12,26,54),'wood')
            for xx in (x-8,x,x+8):
                for h in (5,13,21):
                    p.box((xx-3,h,54),(xx+3,h+5,56),'wood_light')
                    p.box((xx-1,h+2,56),(xx+1,h+3,57),'bronze')
        # Hanging herb bunches under the eave.
        for x in (-52,-38,40,54):
            segment(p,(x,62,52),(x,52,52),.5,'straw',5)
            volume(p,(x,48,52),(6,11,5),'foliage',x,6)
    elif key == 'smith':
        hall(p,-24,-12,112,88,0,76,stone=True,open_front=True)
        roof_at(p,-24,-12,118,94,80,26)
        awning(p,32,84,-30,64,69)
        # Open forge mouth, hood, chimney and anvil define a working shop.
        p.box((-66,4,-38),(-30,52,-8),'stone')
        p.box((-60,10,-7),(-36,32,-4),'wood_recess')
        p.box((-57,11,-3),(-39,14,1),'bronze')
        p.box((-62,52,-34),(-34,62,-10),'iron')
        p.box((-57,62,-30),(-39,148,-13),'stone')
        p.box((-60,146,-33),(-36,151,-10),'iron')
        p.box((48,0,30),(66,12,50),'bark')
        p.box((47,12,32),(68,24,48),'iron')
        p.box((41,24,29),(74,28,50),'iron')
    elif key == 'temple':
        hall(p,0,-5,w-18,d-18,6,91,open_front=True)
        roof_at(p,0,-5,w,d,96,38,True)
        for x in (-62,62):
            segment(p,(x,5,53),(x,88,53),3,'wood_recess',8,2.6)
            volume(p,(x,5,56),(20,8,18),'moss',int(x),7)
        # Recessed altar, cracked relief, fallen stones and weathered entrance.
        p.box((-26,6,-35),(26,32,-10),'stone')
        volume(p,(0,57,-23),(26,44,20),'stone',8,8)
        for x,z in ((-70,60),(48,63),(72,-36)):
            volume(p,(x,4,z),(15,8,11),'rock',x,6)
        for step in range(3):
            p.box((-35,step*2,53-step*4),(35,step*2+2,67-step*4),'stone')
    p.box_shell()
    return [p]


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
        volume(p, (0, height/2, 0), (29, height, 22), 'rock', len(key), 9)
        if key in ('outcrop','mossy_boulder','lichen_rock'):
            volume(p,(9,height*.27,5),(17,height*.55,14),'rock',len(key)+9,7)
            volume(p,(-7,height*.73,-2),(13,3.5,9),'stone',len(key)+4,7)
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
        for x in range(-4,5,2):
            segment(p,(x,46,0),(x+1,46,0),2.9,'straw',8)
        segment(p, (0, 17, 0), (0, 46, 0), .6, 'straw', 4)
        segment(p,(16,46,0),(20,46,0),1.8,'iron',6)
        segment(p,(20,39,0),(20,47,0),1.5,'wood',6)
        # Separate overhanging stone coping segments above the older well shaft.
        for i in range(12):
            a,b=i*math.tau/12+.015,(i+1)*math.tau/12-.015
            pts=[(math.cos(t)*r,h,math.sin(t)*r) for h in (21,25) for r in (10.5,17.5) for t in (a,b)]
            for face in ((0,1,5,4),(2,6,7,3),(4,5,7,6),(0,2,3,1),(0,4,6,2),(1,3,7,5)):
                p.face([pts[j] for j in face],'stone')
        roof(p, 15, 12, 62, 13)
    elif key == 'lantern_post':
        p.box((-5, 0, -5), (5, 5, 5), 'stone')
        segment(p, (0, 5, 0), (0, 78, 0), 2.2, 'wood', 6, 1.7)
        p.box((-2, 74, -2), (12, 78, 2), 'wood_light')
    elif key == 'fence':
        for x in (-19, 0, 19):
            segment(p,(x,0,-1),(x+.6,25+(int(x)%3),0),2,'wood',6,1.5)
            for h in (8,17):
                segment(p,(x,h-1,-2),(x,h+2,-2),2.5,'straw',6)
        for h in (8, 17):
            p.box((-22, h, -1.5), (22, h+2, 1.5), 'wood_light')
    elif key == 'table':
        for z in (-9,-4.5,0,4.5,9):
            p.box((-17,19,z-2.1),(17,22,z+2.1),'wood_light')
        for x in (-14,14):
            p.box((x-1.5,9,-9),(x+1.5,12,9),'wood')
        for x in (-13, 13):
            for z in (-7, 7):
                p.box((x-1.8, 0, z-1.8), (x+1.8, 19, z+1.8), 'wood')
        for z in (-16, 16):
            p.box((-16, 10, z-2), (16, 13, z+2), 'wood_light')
            for x in (-12, 12):
                p.box((x-1, 0, z-1.5), (x+1, 10, z+1.5), 'wood')
        volume(p, (2, 25, 0), (6, 6, 6), 'bronze', 2, 8)
        segment(p,(2,27,0),(2,28,0),2.8,'bronze',8,1.4)
        segment(p,(4,25,0),(8,28,0),1.2,'bronze',6,.65)
        for x,z in ((-7,-4),(-6,5),(10,4)):
            segment(p,(x,22,z),(x,24.2,z),1.5,'paper',8,1.8)
            segment(p,(x,24.2,z),(x,24.3,z),1.15,'wood_recess',8)
    elif key == 'rack':
        for x in (-18, 18):
            p.box((x-2, 0, -3), (x+2, 35, 3), 'wood')
            p.box((x-5, 0, -8), (x+5, 3, 8), 'wood')
        for h in (10, 30):
            p.box((-20, h, -2), (20, h+3, 2), 'wood_light')
        for x in (-11, -3, 5, 13):
            segment(p, (x, 2, 0), (x, 42, 0), 1, 'wood_light', 5)
            p.box((x-3,36,-1),(x+3,38,1),'bronze')
            segment(p,(x,38,0),(x,52,0),1.6,'iron',4,.15)
            segment(p,(x,28,0),(x,35,0),1.25,'wood_recess',6)
    elif key == 'bale':
        volume(p, (0, 11, 0), (24, 20, 19), 'straw', 3, 8)
        for x in (-6, 6):
            p.box((x-1, 1, -10), (x+1, 20, -8), 'wood_recess')
            p.box((x-1, 20, -8), (x+1, 21, 8), 'wood_recess')
    elif key == 'barrel':
        for i in range(12):
            a,b = i*math.tau/12+.025,(i+1)*math.tau/12-.025
            rings = [[(math.cos(t)*r,h,math.sin(t)*r) for t in (a,b)] for h,r in ((1,8),(7,10),(17,10),(23,8))]
            for lo,hi in zip(rings,rings[1:]):
                p.face([lo[0],lo[1],hi[1],hi[0]],'wood_light' if i%3 else 'wood')
        segment(p,(0,22,0),(0,23,0),7.8,'wood_recess',12)
        for h in (5,18):
            segment(p,(0,h,0),(0,h+1.6,0),10.2,'iron',12)
    elif key == 'herbs':
        p.box((-14,0,-9),(14,2,9),'wood_light')
        for z in (-9,9):
            p.box((-15,2,z-1),(15,7,z+1),'wood')
        for x in (-14,14):
            p.box((x-1,2,-9),(x+1,7,9),'wood')
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
                branch=(x+(-1)**j*9,h-8-j*12,z+3)
                segment(p,(x,h-12-j*12,z),branch,.6,'foliage',5,.3)
                for k in range(5):
                    volume(p,(branch[0]+(k-2)*3,branch[1]-abs(k-2),branch[2]+(k%2)*3),(3,1.6,13),'foliage',i+j+k,4)
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
    shader = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
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
    # Generic orphan names such as 'table' can exist in a live Blender file.
    # Give owned meshes a stable namespace without deleting unrelated data.
    for obj, part in zip(objects, parts):
        mesh_name = 'XIA_P3_'+part.name
        obj.data.name = mesh_name
        if obj.data.name != mesh_name:
            raise RuntimeError('Unrelated mesh uses reserved name: '+mesh_name)
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
                'review': 'P4 differentiated town architecture; scenic_layout owns frontend placement; NPCs excluded from scene review'}
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
