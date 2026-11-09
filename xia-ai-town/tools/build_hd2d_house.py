"""Rebuild the single P1 inn with Blender (no external generators/addons needed).

CLI:
  blender --background --factory-startup --python-exit-code 1 --python tools/build_hd2d_house.py
  blender --background --factory-startup --python-exit-code 1 --python tools/build_hd2d_house.py -- --check
Live MCP: runpy.run_path(absolute_path)["build"]()

Owns ONLY the XIA_P1_Inn scene. Does not delete other scenes or save over the
user's open file. The editable .blend is written as an isolated scene library
under ignored asserts/hd2d/buildings/. All static architecture shares one mesh
and one local-occlusion group, with one surface per named material. Godot supplies
the existing pixel textures/shader and separate animated lanterns. No PNG is edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets/hd2d/buildings"
SCENE = "XIA_P1_Inn"
OWNER = "xia.p1.inn.builder.v1"
SCALE = 1.0 / 16.0
# Model-local logical (x, height, map_y); exported glTF/Godot axes are X/Y/Z.
FOOTPRINT = [-96, -64, 192, 128]
MATERIALS = {
    "plaster": {"texture": "materials/plaster", "tint": [1, 1, 1]},
    "wood": {"texture": "materials/timber", "tint": [0.94, 0.94, 0.94]},
    "wood_light": {"texture": "materials/joinery", "tint": [0.88, 0.87, 0.83]},
    "wood_recess": {"texture": "materials/door_wood", "tint": [0.68, 0.70, 0.70]},
    "paper": {"texture": "materials/window_paper", "tint": [0.78, 0.80, 0.77]},
    "bronze": {"texture": "materials/stone", "tint": [0.65, 0.57, 0.39], "flat_color": "958062", "color_mix": 0.8},
    "roof": {"texture": "materials/roof", "tint": [0.67, 0.74, 0.77]},
    "stone": {"texture": "materials/stone", "tint": [0.72, 0.73, 0.69]},
    "sign": {"texture": "components/sign", "tint": [1, 1, 1]},
}
PROFILE = [(0, 152), (22, 142), (44, 129), (64, 117), (74, 115), (80, 116)]
XS = [-108, -100, -88, -64, -32, 0, 32, 64, 88, 100, 108]


def point(p):
    x, h, z = p
    return (x * SCALE, -z * SCALE, h * SCALE)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def enum(op, key, preferred):
    """Dynamic enums can return no items; the operator validates those values."""
    prop = op.get_rna_type().properties[key]
    choices = [item.identifier for item in prop.enum_items]
    if choices and preferred not in choices:
        raise RuntimeError(f"{key}: {preferred} unavailable; supported: {choices}")
    return preferred


def make_material(key, spec):
    name = "XIA_P1_" + key
    mat = bpy.data.materials.get(name)
    if mat and mat.get("xia_owner") != OWNER:
        raise RuntimeError(f"Refusing unrelated material {name}")
    mat = mat or bpy.data.materials.new(name)
    mat["xia_owner"] = OWNER
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.inputs["Roughness"].default_value = 0.95
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(str(ROOT / "assets/hd2d" / (spec["texture"] + ".png")), check_existing=True)
    tex.interpolation = "Closest"
    multiply = nodes.new("ShaderNodeMixRGB")
    multiply.blend_type = "MULTIPLY"
    multiply.inputs[0].default_value = 1
    multiply.inputs[2].default_value = (*spec["tint"], 1)
    mat.node_tree.links.new(tex.outputs["Color"], multiply.inputs[1])
    color = multiply.outputs[0]
    if spec.get("color_mix", 0):
        flat = spec["flat_color"]
        mixed = nodes.new("ShaderNodeMixRGB")
        mixed.blend_type = "MIX"
        mixed.inputs[0].default_value = spec["color_mix"]
        mixed.inputs[2].default_value = tuple(int(flat[i*2:i*2+2],16)/255 * spec["tint"][i] for i in range(3)) + (1,)
        mat.node_tree.links.new(color, mixed.inputs[1])
        color = mixed.outputs[0]
    mat.node_tree.links.new(color, shader.inputs["Base Color"])
    mat.node_tree.links.new(shader.outputs[0], output.inputs["Surface"])
    mat.diffuse_color = (*spec["tint"], 1)
    return mat


class Component:
    """Accumulate static geometry into one mesh, deduplicating material slots."""
    def __init__(self, name, group):
        self.name, self.group = name, group
        self.vertices, self.faces, self.uvs, self.material_ids = [], [], [], []
        self.slots = []
        self.boxes = []
        self.top_covers = []

    def face(self, vertices, material, uv=None):
        if material not in self.slots:
            self.slots.append(material)
        if uv is None:
            # Metric UVs: one source texel per logical pixel on 64px textures.
            a, b, c = (Vector(v) for v in vertices[:3])
            normal = (b-a).cross(c-a)
            axis = max(range(3), key=lambda i: abs(normal[i]))
            axes = {0: (2, 1), 1: (0, 2), 2: (0, 1)}[axis]
            uv = [(v[axes[0]] / 64, v[axes[1]] / 64) for v in vertices]
        offset = len(self.vertices)
        self.vertices.extend(point(v) for v in vertices)
        self.faces.append(tuple(range(offset, offset+len(vertices))))
        self.uvs.append(uv)
        self.material_ids.append(self.slots.index(material))

    def box(self, a, b, material):
        assert all(hi > lo for lo, hi in zip(a, b))
        # Preserve the existing named surface order when deferring geometry.
        if material not in self.slots:
            self.slots.append(material)
        self.boxes.append((a, b, material))

    @staticmethod
    def subtract_rect(rect, cover):
        """Subtract one coplanar overlap, keeping up to four exterior rectangles."""
        u0, v0, u1, v1 = rect
        a, b, c, d = max(u0, cover[0]), max(v0, cover[1]), min(u1, cover[2]), min(v1, cover[3])
        if c-a <= 1e-6 or d-b <= 1e-6:
            return [rect]
        return [r for r in [(u0,v0,a,v1), (c,v0,u1,v1), (a,v0,c,b), (a,d,c,v1)]
                if r[2]-r[0] > 1e-6 and r[3]-r[1] > 1e-6]

    @staticmethod
    def merge_rects(rectangles):
        """Coalesce neighbours created by clipping, without covering any holes."""
        rectangles = sorted(rectangles)
        while True:
            for i, a in enumerate(rectangles):
                for j in range(i+1, len(rectangles)):
                    b = rectangles[j]
                    same_u = abs(a[0]-b[0]) <= 1e-6 and abs(a[2]-b[2]) <= 1e-6
                    same_v = abs(a[1]-b[1]) <= 1e-6 and abs(a[3]-b[3]) <= 1e-6
                    if (same_u and (abs(a[3]-b[1]) <= 1e-6 or abs(b[3]-a[1]) <= 1e-6)
                            or same_v and (abs(a[2]-b[0]) <= 1e-6 or abs(b[2]-a[0]) <= 1e-6)):
                        rectangles[i] = (min(a[0],b[0]), min(a[1],b[1]), max(a[2],b[2]), max(a[3],b[3]))
                        rectangles.pop(j)
                        break
                else:
                    continue
                break
            else:
                return rectangles

    def box_shell(self):
        """Keep the union boundary, with the last timber/trim owning shared faces.

        Joining objects alone leaves touching caps and overlapping front faces
        in the mesh. Those fight for depth, especially with double-sided fading.
        Clip in logical space before export; metric UVs and flat normals survive.
        """
        surfaces = {}
        for index, (lo, hi, material) in enumerate(self.boxes):
            for axis in range(3):
                u, v = [a for a in range(3) if a != axis]
                for sign in (-1, 1):
                    plane = lo[axis] if sign < 0 else hi[axis]
                    rectangles = [(lo[u], lo[v], hi[u], hi[v])]
                    for other, (a, b, _) in enumerate(self.boxes):
                        if other == index or plane < a[axis]-1e-6 or plane > b[axis]+1e-6:
                            continue
                        # A coincident outward face has one deterministic owner;
                        # a face touching the other solid's inside is internal.
                        outside = a[axis] if sign < 0 else b[axis]
                        if abs(plane-outside) <= 1e-6 and other < index:
                            continue
                        cover = (a[u], a[v], b[u], b[v])
                        rectangles = [piece for rect in rectangles for piece in self.subtract_rect(rect, cover)]
                        if not rectangles:
                            break
                    if axis == 1 and sign == 1:
                        # A gable's open foot meets the timber cap: neither the
                        # cap nor the gable should emit an internal closing face.
                        for height, cover in self.top_covers:
                            if abs(plane-height) <= 1e-6:
                                rectangles = [piece for rect in rectangles for piece in self.subtract_rect(rect, cover)]
                    surfaces.setdefault((axis, sign, plane, material), []).extend(rectangles)
        for (axis, sign, plane, material), rectangles in surfaces.items():
            u, v = [a for a in range(3) if a != axis]
            for u0, v0, u1, v1 in self.merge_rects(rectangles):
                vertices = []
                for pu, pv in [(u0,v0),(u1,v0),(u1,v1),(u0,v1)]:
                    p = [0.0, 0.0, 0.0]
                    p[axis], p[u], p[v] = plane, pu, pv
                    vertices.append(tuple(p))
                if sign != (1 if axis in (0, 2) else -1):
                    vertices.reverse()
                self.face(vertices, material)

    def front_frame(self, x0, x1, bottom, top, back, front, width, material):
        """Closed timber frame, with no overlapping coplanar front faces."""
        assert x1-x0 > 2*width and top-bottom > 2*width
        self.box((x0,bottom,back),(x1,bottom+width,front),material)
        self.box((x0,top-width,back),(x1,top,front),material)
        for left,right in [(x0,x0+width),(x1-width,x1)]:
            self.box((left,bottom+width,back),(right,top-width,front),material)

    def pull_ring(self, x, h, z, material):
        # Low-poly bronze ring, not a painted highlight. The hole stays open.
        for i in range(8):
            a,b = (2*math.pi*j/8 for j in (i,i+1))
            cross = [(x+r*math.cos(t), h+r*math.sin(t))
                     for r,t in [(1.45,a),(1.45,b),(0.85,b),(0.85,a)]]
            self.face([(cx,cy,z) for cx,cy in cross],material)
            self.face([(cx,cy,z-0.6) for cx,cy in reversed(cross)],material)
            for j in (0,2):
                k=(j+1)%4
                self.face([(cross[j][0],cross[j][1],z),
                           (cross[k][0],cross[k][1],z),
                           (cross[k][0],cross[k][1],z-0.6),
                           (cross[j][0],cross[j][1],z-0.6)],material)

    def beam(self, a, b, width, depth, material):
        # Rectangular timber along a slope, with sealed ends.
        a, b = Vector(a), Vector(b)
        tangent = (b-a).normalized()
        right = Vector((1,0,0))
        up = tangent.cross(right).normalized()
        points = [[tuple(center + right*sx*width/2 + up*sy*depth/2)
                   for sx, sy in [(-1,-1),(1,-1),(1,1),(-1,1)]] for center in (a,b)]
        self.face(list(reversed(points[0])),material)
        self.face(points[1],material)
        for i in range(4):
            j = (i+1)%4
            self.face([points[0][i],points[0][j],points[1][j],points[1][i]],material)

    def card(self, x0, x1, low, high, z, material):
        # Trim the existing transparent padding in UV space, never stretch the
        # entire 96px canvas. Physical wood backing closes every alpha cutout.
        image = bpy.data.images.get(Path(MATERIALS[material]["texture"]).name + ".png")
        if image is None:
            image = bpy.data.images.load(str(ROOT / "assets/hd2d" / (MATERIALS[material]["texture"] + ".png")), check_existing=True)
        w,h = image.size
        alpha = list(image.pixels)[3::4]
        coords = [(i % w, i // w) for i,a in enumerate(alpha) if a >= 0.5]
        u0 = min(x for x,y in coords)/w
        u1 = (max(x for x,y in coords)+1)/w
        v0 = min(y for x,y in coords)/h
        v1 = (max(y for x,y in coords)+1)/h
        self.face([(x0,low,z),(x1,low,z),(x1,high,z),(x0,high,z)], material,
                  [(u0,v0),(u1,v0),(u1,v1),(u0,v1)])

    def finish(self, scene, materials):
        mesh = bpy.data.meshes.new(self.name)
        mesh.from_pydata(self.vertices, [], self.faces)
        mesh.update()
        for key in self.slots:
            mesh.materials.append(materials[key])
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for poly, coords, slot in zip(mesh.polygons, self.uvs, self.material_ids):
            poly.material_index = slot
            poly.use_smooth = False
            for loop, uv in zip(poly.loop_indices, coords):
                uv_layer.data[loop].uv = uv
        mesh.calc_loop_triangles()
        for tri in mesh.loop_triangles:
            a,b,c = (mesh.vertices[i].co for i in tri.vertices)
            if (b-a).cross(c-a).length < 1e-8:
                raise RuntimeError(f"Degenerate triangle in {self.name}")
        obj = bpy.data.objects.new(self.name, mesh)
        obj["xia_owner"] = OWNER
        obj["fade_group"] = self.group
        scene.collection.objects.link(obj)
        return obj


def corner_lift(x):
    return max(0, (abs(x)-80)/28)**2 * 3.5


def roof_height(distance):
    for (a,ha),(b,hb) in zip(PROFILE,PROFILE[1:]):
        if a <= distance <= b:
            return ha + (hb-ha)*(distance-a)/(b-a)
    raise ValueError(distance)


def front_opening(wall, lo, hi, bottom, top, door):
    """Broad integer-pixel joinery with genuine recesses, no flat facade cards.

    Retain the physical reveal/casing/sill depths; larger quiet panels and
    fewer 2-3px rails read at native game scale rather than as miniature joinery.
    """
    backing = 53.5 if door else 58.5
    sill = 73 if door else 70
    wall.box((lo,bottom,52),(hi,top,backing),"wood_recess")
    wall.front_frame(lo-3,hi+3,bottom,top+3,backing,69,3,"wood")
    wall.box((lo-4,top+3,62),(hi+4,top+5,70),"wood_light")
    wall.box((lo-4,bottom-1,55),(hi+4,bottom+1,sill),"stone" if door else "wood_light")
    for x in (lo,hi-2):
        wall.box((x,bottom+1,54),(x+2,top,63),"wood_light")
    middle=(lo+hi)/2
    if door:
        # Two large framed door leaves. Board joints are modeled 1px gaps,
        # rather than subpixel cuts; restrained stepped handles stay visible.
        for left,right in [(lo+2,middle-1),(middle+1,hi-2)]:
            low,high=bottom+2,top-2
            wall.box((left,low,53.6),(right,high,55),"wood_recess")
            wall.front_frame(left,right,low,high,55,58,3,"wood_light")
            rail=bottom+25
            wall.box((left+3,rail,55),(right-3,rail+3,58),"wood_light")
            split=(left+right)/2
            for a,b in [(left+3,split-0.5),(split+0.5,right-3)]:
                wall.box((a,low+3,55),(b,rail,56),"wood_recess")
                wall.box((a,rail+3,55),(b,high-3,56),"wood_recess")
            handle=right-3 if right<middle else left+3
            height=bottom+36
            wall.box((handle-1,height-2,58),(handle+1,height+3,59),"bronze")
            # A five-pixel angular ring makes one coherent pixel silhouette.
            wall.front_frame(handle-2.5,handle+2.5,height-3,height+2,59,60,1,"bronze")
    else:
        # A single broad window frame with simple three-column/two-row panes.
        # Paper fills are recessed solid volumes; daylight/shadows remain real.
        low,high=bottom+2,top-2
        left,right=lo+2,hi-2
        wall.box((left,low,59),(right,high,61),"paper")
        wall.front_frame(left,right,low,high,61,64.5,3,"wood_light")
        for fraction in (1/3,2/3):
            center=round(left+(right-left)*fraction)
            wall.box((center-1,low+3,61),(center+1,high-3,65),"wood_light")
        center=round((low+high)/2)
        wall.box((left+3,center-1,61),(right-3,center+1,65),"wood_light")
        for x in (lo+3,hi-6):
            wall.box((x,bottom-5,63),(x+3,bottom-1,69),"wood")


def build_components():
    # Local masks operate per fragment, so walls/roof/joinery no longer need
    # separate meshes or fade groups. Keep source construction steps readable.
    house = Component("structure", "inn/structure")
    house.box((-96,0,-64),(96,5,64),"stone")
    # Coping only around the perimeter; no giant stone lid exposed by fading.
    for a,b in [((-96,5,-64),(96,7,-59)),((-96,5,59),(96,7,64)),
                ((-96,5,-59),(-91,7,59)),((91,5,-59),(96,7,59))]:
        house.box(a,b,"stone")
    # Continuous facade spans separated only by the actual door/window holes,
    # not by the old fade bays. Avoid hidden duplicate faces at bay boundaries.
    openings = [(-79,-43,36,68,False),(-21,21,7,73,True),(43,79,36,68,False)]
    left = -96
    for lo,hi,bottom,top,door in openings:
        house.box((left,7,54),(lo,110,64),"plaster")
        house.box((left,7,63.9),(lo,13,64.3),"stone")
        if bottom > 7:
            house.box((lo,7,54),(hi,bottom,64),"plaster")
        house.box((lo,top,54),(hi,110,64),"plaster")
        front_opening(house,lo,hi,bottom,top,door)
        left = hi
    house.box((left,7,54),(96,110,64),"plaster")
    house.box((left,7,63.9),(96,13,64.3),"stone")
    house.box((-96,106,60),(96,110,65),"wood")
    house.box((-28,89,65),(28,101,68),"wood")
    house.card(-27,27,90,100,68.1,"sign")
    house.box((-29,88,65),(29,90,68.5),"wood_light")
    house.box((-29,101,65),(29,103,68.5),"wood_light")
    for x in [-21,20]:
        house.box((x,103,63),(x+1,113,66),"wood")
    # Rear wall, weathering band and head rail are each continuous spans.
    house.box((-96,7,-64),(96,110,-60),"plaster")
    house.box((-96,7,-64.2),(96,14,-60),"stone")
    house.box((-96,106,-64.2),(96,111,-59.5),"wood")
    for x0,x1 in [(-96,-92),(92,96)]:
        house.box((x0,7,-60),(x1,110,60),"plaster")
        house.box((x0-0.15,7,-60),(x1+0.15,14,60),"stone")
        # Wall-top timber owns heights 110..114. Start the gable above it;
        # triangulate the two sides but close only the OUTER sloping contour.
        # Sealed triangle prisms left duplicate radial walls and black seams.
        base = 114
        profile = [(-d,h) for d,h in reversed(PROFILE[:-1])] + PROFILE[1:-1]
        contour = [(-64,base)] + [(z,h-3) for z,h in profile if abs(z)<64] + [(64,base)]
        house.top_covers.append((base, (x0,-64,x1,64)))
        anchor = (0,base)
        for (za,ha),(zb,hb) in zip(contour,contour[1:]):
            triangle = [anchor,(za,ha),(zb,hb)]
            house.face([(x0,h,z) for z,h in reversed(triangle)],"plaster")
            house.face([(x1,h,z) for z,h in triangle],"plaster")
            house.face([(x0,ha,za),(x1,ha,za),(x1,hb,zb),(x0,hb,zb)],"plaster")
    for x in [-93,-35,35,93]:
        house.box((x-2.4,7,60),(x+2.4,113,65),"wood")
        house.box((x-3.2,7,59.5),(x+3.2,15,65.5),"stone")
        house.box((x-3.5,105,59),(x+3.5,112,67),"wood_light")
    # Wall cap is a coherent timber ring, NOT a rectangle covering the house.
    for a,b in [((-96,110,-64),(96,114,-59)),((-96,110,59),(96,114,64)),
                ((-96,110,-59),(-91,114,59)),((91,110,-59),(96,114,59))]:
        house.box(a,b,"wood")
    for sign in [1,-1]:
        distances = [0]
        for (a,ha),(b,hb) in zip(PROFILE,PROFILE[1:]):
            distances.append(distances[-1]+math.hypot(b-a,hb-ha))
        for x0,x1 in zip(XS,XS[1:]):
            for j,((d0,h0),(d1,h1)) in enumerate(zip(PROFILE,PROFILE[1:])):
                top=[(x0,h0+corner_lift(x0),sign*d0),(x1,h0+corner_lift(x1),sign*d0),
                     (x1,h1+corner_lift(x1),sign*d1),(x0,h1+corner_lift(x0),sign*d1)]
                uv=[(x0/64,-distances[j]/64),(x1/64,-distances[j]/64),
                    (x1/64,-distances[j+1]/64),(x0/64,-distances[j+1]/64)]
                bottom=[(x,h-3,z) for x,h,z in top]
                house.face(top if sign<0 else list(reversed(top)),"roof",uv if sign<0 else list(reversed(uv)))
                house.face(bottom if sign>0 else list(reversed(bottom)),"wood")
                if j==len(PROFILE)-2:
                    house.face([bottom[3],bottom[2],top[2],top[3]],"wood_light")
                # Both roof slopes meet here. Closing each at the ridge would
                # export the same internal vertical face twice.
                if x0==XS[0]:
                    house.face([bottom[0],bottom[3],top[3],top[0]],"roof")
                if x1==XS[-1]:
                    house.face([bottom[2],bottom[1],top[1],top[2]],"roof")
        # Exposed rafter tails, restricted to the eave rather than filling the loft.
        for x in range(-100,101,10):
            house.beam((x,roof_height(60)+corner_lift(x)-5,sign*60),
                      (x,roof_height(79)+corner_lift(x)-5,sign*79),2.4,2.2,"wood_light")
        # Low-sided curved clay end caps affect the silhouette; the field stays textured.
        for x in range(-104,105,8):
            points=[]
            for j in range(6):
                theta=math.pi*j/5
                points.append((x+3.5*math.cos(theta),115+corner_lift(x)+2.2*math.sin(theta)))
            for (xa,ha),(xb,hb) in zip(points,points[1:]):
                house.face([(xa,ha,sign*78),(xb,hb,sign*78),(xb,hb,sign*81),(xa,ha,sign*81)],"roof")
    for x0,x1 in zip(XS,XS[1:]):
        cross=[(-3,151),(-2.5,155),(0,157),(2.5,155),(3,151)]
        for (z0,h0),(z1,h1) in zip(cross,cross[1:]):
            house.face([(x0,h0+corner_lift(x0),z0),(x1,h0+corner_lift(x1),z0),
                        (x1,h1+corner_lift(x1),z1),(x0,h1+corner_lift(x0),z1)],"roof")
        if x0==XS[0]:
            house.face([(x0,h+corner_lift(x0),z) for z,h in reversed(cross)],"roof")
        if x1==XS[-1]:
            house.face([(x1,h+corner_lift(x1),z) for z,h in cross],"roof")
    house.box_shell()
    return [house]


def glb_signature(path):
    data = Path(path).read_bytes()
    magic,version,size = struct.unpack_from('<4sII',data)
    assert magic == b'glTF' and version==2 and size==len(data)
    length,kind = struct.unpack_from('<II',data,12)
    assert kind==0x4E4F534A
    doc = json.loads(data[20:20+length])
    doc.pop("asset",None)  # Exporter version is not model content.
    normalized = json.dumps(doc,sort_keys=True,separators=(',',':')).encode()+data[20+length:]
    return hashlib.sha256(normalized).hexdigest(),doc


def build(output_dir=None, save_source=True, check=False):
    old = bpy.data.scenes.get(SCENE)
    if old:
        if old.get("xia_owner") != OWNER:
            raise RuntimeError("Refusing to replace a scene not owned by this builder")
        for obj in list(old.objects):
            if obj.get("xia_owner") != OWNER:
                raise RuntimeError(f"User object {obj.name} in builder scene; move it before rebuilding")
        for obj in list(old.objects):
            mesh = obj.data if obj.type=="MESH" else None
            bpy.data.objects.remove(obj,do_unlink=True)
            if mesh and mesh.users==0:
                bpy.data.meshes.remove(mesh)
        scene=old
    else:
        scene=bpy.data.scenes.new(SCENE)
        scene["xia_owner"]=OWNER
    if bpy.context.window:
        bpy.context.window.scene=scene
    materials={key:make_material(key,spec) for key,spec in MATERIALS.items()}
    parts=build_components()
    objects=[part.finish(scene,materials) for part in parts]
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[0]
    directory=Path(output_dir) if output_dir else OUT
    directory.mkdir(parents=True,exist_ok=True)
    glb=directory/'inn.glb'
    options=dict(filepath=str(glb),export_format=enum(bpy.ops.export_scene.gltf,'export_format','GLB'),
                 export_materials=enum(bpy.ops.export_scene.gltf,'export_materials','VIEWPORT'),
                 export_image_format='NONE',
                 use_selection=True,use_active_scene=True,export_extras=False,export_yup=True,
                 export_cameras=False,export_lights=False,export_apply=True)
    # Ownership/fade metadata stay in the source .blend and sidecar JSON.
    # Runtime GLB needs no custom properties; a live session's addon RNA flags
    # otherwise leak into extras and break deterministic background rebuilds.
    bpy.ops.export_scene.gltf(**options)
    signature,doc=glb_signature(glb)
    metadata={"schema":2,"builder":"tools/build_hd2d_house.py","builder_sha256":digest(__file__),
              "geometry_sha256":signature,"origin":"footprint center at ground; Godot X/right Y/up Z/map-y",
              "logical_scale":SCALE,"footprint":FOOTPRINT,"materials":MATERIALS,
              "texture_sha256":{s["texture"]:digest(ROOT/'assets/hd2d'/(s["texture"]+'.png')) for s in MATERIALS.values()},
              "components":{p.name:{"group":p.group,"materials":p.slots} for p in parts},
              "mesh_count":len(objects),"surface_count":sum(len(p.slots) for p in parts),
              "triangles":sum(len(o.data.loop_triangles) for o in objects),
              "lanterns":[{"local":[-34,79,77],"size":[18,24],"group":"inn/lantern/0"},
                          {"local":[34,79,77],"size":[18,24],"group":"inn/lantern/1"}],
              "probes":{"rear_roof":{"local":[-64,roof_height(70),-70],"group":"inn/structure"}},
              "review":"Single P1 candidate; gameplay footprint unchanged; requires real GPU and user visual approval"}
    (directory/'inn.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    if save_source:
        source=ROOT/'asserts/hd2d/buildings/inn.blend'
        source.parent.mkdir(parents=True,exist_ok=True)
        bpy.data.libraries.write(str(source),{scene},path_remap='RELATIVE',fake_user=True)
    # Leave an immediately inspectable 3/4 view in the dedicated scene.
    if bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type=='VIEW_3D':
                area.spaces.active.region_3d.view_location=Vector((0,0,4.0))
                area.spaces.active.region_3d.view_distance=20
                area.spaces.active.region_3d.view_rotation=Vector((0.35,-1,0.85)).to_track_quat('Z','Y')
    print('XIA_HOUSE_BUILD',json.dumps({"glb":str(glb),"meshes":metadata['mesh_count'],"surfaces":metadata['surface_count'],"triangles":metadata['triangles'],"signature":signature}))
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.check:
        with tempfile.TemporaryDirectory(prefix='xia-house-check-') as temp:
            actual=build(temp,save_source=False,check=True)
            expected=json.loads((OUT/'inn.json').read_text())
            if actual != expected or glb_signature(OUT/'inn.glb')[0] != actual['geometry_sha256']:
                raise RuntimeError('House geometry/metadata differs; rebuild the GLB')
            print('XIA_HOUSE_CHECK_PASS: deterministic geometry, UVs, groups, material slots and source hashes')
    else:
        build()


if __name__=='__main__':
    main()
