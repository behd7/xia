"""Build the single P1 pine: rooted bent trunk and closed modular needle masses.

blender --background --factory-startup --python-exit-code 1 --python tools/build_hd2d_tree.py
blender --background --factory-startup --python-exit-code 1 --python tools/build_hd2d_tree.py -- --check
Live Blender: runpy.run_path(absolute_path)["build"]()
Only owns XIA_P1_Pine; never clears unrelated scenes or saves the open .blend.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import runpy
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'assets/hd2d/props'
SCENE = 'XIA_P1_Pine'
OWNER = 'xia.p1.pine.builder.v1'
SCALE = 1 / 16
MATERIALS = {
    'bark': {'texture': 'materials/bark', 'tint': [0.89, 0.90, 0.86]},
    'needles': {'texture': 'materials/pine_needles', 'tint': [0.81, 0.87, 0.73]},
}


def point(p):
    x, h, z = p
    return (x*SCALE, -z*SCALE, h*SCALE)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Mesh:
    def __init__(self, name, material):
        self.name, self.material = name, material
        self.vertices, self.faces, self.uvs, self.colors = [], [], [], []

    def face(self, points, uv, color=(1,1,1,1)):
        start = len(self.vertices)
        self.vertices.extend(point(p) for p in points)
        self.faces.append(tuple(range(start, start+len(points))))
        self.uvs.append(uv)
        self.colors.append(color)

    def tube(self, path, sides=10):
        """Irregular tapered rings follow the branch tangent; root and forks bend."""
        rings = []
        distance = 0.0
        for i, (position, radius) in enumerate(path):
            center = Vector(position)
            previous = Vector(path[max(0,i-1)][0])
            following = Vector(path[min(len(path)-1,i+1)][0])
            tangent = (following-previous).normalized()
            right = tangent.cross(Vector((0,0,1))).normalized()
            if right.length < 0.5:
                right = tangent.cross(Vector((1,0,0))).normalized()
            back = tangent.cross(right).normalized()
            if i:
                distance += (center-previous).length
            ring = []
            for j in range(sides):
                angle = math.tau*j/sides
                # Keep ridges coherent vertically; never a regular hexagonal pole.
                irregular = 1 + 0.11*math.sin(j*2.3+0.4) + 0.06*math.cos(j*4.1)
                p = center + (right*math.cos(angle)+back*math.sin(angle))*radius*irregular
                ring.append(tuple(p))
            rings.append((ring,distance,radius))
        for (a, av, ar), (b, bv, br) in zip(rings,rings[1:]):
            for j in range(sides):
                k = (j+1)%sides
                u0, u1 = j/sides*math.tau*ar/64, (j+1)/sides*math.tau*ar/64
                self.face([a[j],a[k],b[k],b[j]],[(u0,av/64),(u1,av/64),(u1,bv/64),(u0,bv/64)])
        for ring, _, _ in (rings[0],rings[-1]):
            self.face(list(reversed(ring)),[(0,0)]*sides)

    def bough(self, center, radii, seed):
        """One closed, scalloped pine spray; broad upper crown, hanging lower lip.

        Rings describe real depth, never a plane or camera-facing whole-tree
        image. Shared lobe rhythms and varied branch orientation come from the
        modular pixel-art method, adapted here to volume rather than 2D layers.
        """
        cx,cy,cz = center
        rx,ry,rz = radii
        segments = 32
        rings = []
        for y, radius in [(-0.90,0.35),(-0.58,0.78),(-0.12,1.0),(0.24,0.94),(0.64,0.65),(0.90,0.28)]:
            ring = []
            for j in range(segments):
                a = math.tau*j/segments
                # Macro lobes make a recognizable bough silhouette. A smaller
                # scallop rhythm represents needle bunches without noisy spikes.
                lobe = 1 + 0.16*math.sin(a*5+seed) + 0.095*math.sin(a*13-seed*1.7)
                drop = 0.075*math.cos(a*7+seed) * (1-abs(y))
                ring.append((cx+rx*radius*lobe*math.cos(a),cy+ry*(y+drop),cz+rz*radius*lobe*math.sin(a)))
            rings.append(ring)
        color = (0.94+0.06*math.sin(seed),0.96+0.04*math.sin(seed),0.94+0.035*math.cos(seed),1)
        for a,b in zip(rings,rings[1:]):
            for j in range(segments):
                k=(j+1)%segments
                pts=[a[j],a[k],b[k],b[j]]
                self.face(pts,[(p[0]/32,p[2]/32) for p in pts],color)
        for ring, height, reverse in [(rings[0],cy-ry,True),(rings[-1],cy+ry,False)]:
            for j in range(segments):
                pts=[(cx,height,cz),ring[j],ring[(j+1)%segments]]
                if reverse:
                    pts.reverse()
                self.face(pts,[(p[0]/32,p[2]/32) for p in pts],color)

    def finish(self, scene, material):
        # Weld only within the material, so smooth normals follow curved tree
        # volumes instead of every triangle. UV/color remain per face corner.
        vertices, faces, lookup = [], [], {}
        for face in self.faces:
            indices=[]
            for index in face:
                v=self.vertices[index]
                key=tuple(round(axis,7) for axis in v)
                if key not in lookup:
                    lookup[key]=len(vertices)
                    vertices.append(v)
                indices.append(lookup[key])
            faces.append(indices)
        mesh=bpy.data.meshes.new(self.name)
        mesh.from_pydata(vertices,[],faces)
        mesh.update()
        uv=mesh.uv_layers.new(name='UVMap')
        colors=mesh.color_attributes.new(name='Color',type='FLOAT_COLOR',domain='CORNER')
        for poly, coords, color in zip(mesh.polygons,self.uvs,self.colors):
            poly.use_smooth=True
            for loop, coord in zip(poly.loop_indices,coords):
                uv.data[loop].uv=coord
                colors.data[loop].color=color
        # Recalculate winding, including closed top/bottom caps. No dependency
        # on the current selection/mode or on an installed Blender addon.
        import bmesh
        bm=bmesh.new()
        bm.from_mesh(mesh)
        bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
        if any(len(edge.link_faces)!=2 for edge in bm.edges):
            raise RuntimeError(f'Open or non-manifold pine volume: {self.name}')
        bm.to_mesh(mesh)
        bm.free()
        mesh.materials.append(material)
        mesh.calc_loop_triangles()
        for tri in mesh.loop_triangles:
            a,b,c=(mesh.vertices[i].co for i in tri.vertices)
            if (b-a).cross(c-a).length<1e-8:
                raise RuntimeError(f'Degenerate pine triangle: {self.name}')
        obj=bpy.data.objects.new(self.name,mesh)
        obj['xia_owner']=OWNER
        scene.collection.objects.link(obj)
        return obj


def parts():
    trunk=Mesh('trunk','bark')
    foliage=Mesh('crown','needles')
    spine=[((0,0,0),7.4),((-1,5,1),6.4),((-3,18,1),5.1),((-4,36,0),4.2),
           ((-2,55,-1),3.6),((2,74,-2),3.0),((3,95,0),2.3),((1,118,1),1.5),((0,144,0),0.45)]
    trunk.tube(spine,12)
    for j in range(5):
        a=math.tau*j/5+0.3
        trunk.tube([((0,6,0),3.4),((math.cos(a)*7,2.2,math.sin(a)*7),2.4),
                    ((math.cos(a)*13,0.6,math.sin(a)*13),0.6)],8)
    boughs=[]
    # Asymmetric alternation leaves recognizable gaps between branch tiers.
    # Modules have comparable cluster scale, rather than a stack of cones.
    for tier,(height,radius,width,thickness) in enumerate([(53,25,20,6),(81,22,18,6),(108,16,15,6),(133,10,12,7)]):
        for j in range(3):
            angle=math.radians(12+tier*71+j*120)
            reach=radius*(1+0.10*math.sin(tier*1.8+j*2.1))
            x,z=math.cos(angle)*reach,math.sin(angle)*reach
            h=height+math.sin(j*2.3+tier)*3
            start=Vector((-2 if tier<2 else 2,height-13,0))
            end=Vector((x,h-3,z))
            elbow=start.lerp(end,0.58)+Vector((0,-2,0))
            trunk.tube([(tuple(start),2.2-tier*0.3),(tuple(elbow),1.5-tier*0.2),(tuple(end),0.55)],8)
            boughs.append(((x,h,z),(width,thickness,width*0.80),tier*3+j+0.6))
    boughs.extend([((0,144,0),(10,8,9),13.4),((1,153,0),(5,6,5),14.8)])
    for center, radii, seed in boughs:
        foliage.bough(center,radii,seed)
    return [trunk,foliage],boughs


def build(output_dir=None,save_source=True):
    scene=bpy.data.scenes.get(SCENE)
    if scene:
        if scene.get('xia_owner')!=OWNER or any(o.get('xia_owner')!=OWNER for o in scene.objects):
            raise RuntimeError('Refusing to replace user objects in pine scene')
        for obj in list(scene.objects):
            mesh=obj.data
            bpy.data.objects.remove(obj,do_unlink=True)
            if mesh.users==0:
                bpy.data.meshes.remove(mesh)
    else:
        scene=bpy.data.scenes.new(SCENE)
        scene['xia_owner']=OWNER
    if bpy.context.window:
        bpy.context.window.scene=scene
    materials={}
    for key,spec in MATERIALS.items():
        name='XIA_Pine_'+key
        mat=bpy.data.materials.get(name)
        if mat and mat.get('xia_owner')!=OWNER:
            raise RuntimeError('Refusing unrelated pine material')
        mat=mat or bpy.data.materials.new(name)
        mat['xia_owner']=OWNER
        mat.use_nodes=True
        nodes=mat.node_tree.nodes
        nodes.clear()
        output=nodes.new('ShaderNodeOutputMaterial')
        shader=nodes.new('ShaderNodeBsdfPrincipled')
        shader.inputs['Roughness'].default_value=1
        texture=nodes.new('ShaderNodeTexImage')
        texture.image=bpy.data.images.load(str(ROOT/'assets/hd2d'/(spec['texture']+'.png')),check_existing=True)
        texture.interpolation='Closest'
        tint=nodes.new('ShaderNodeMixRGB')
        tint.blend_type='MULTIPLY'
        tint.inputs[0].default_value=1
        tint.inputs[2].default_value=(*spec['tint'],1)
        mat.node_tree.links.new(texture.outputs['Color'],tint.inputs[1])
        attributes=nodes.new('ShaderNodeVertexColor')
        attributes.layer_name='Color'
        shaded=nodes.new('ShaderNodeMixRGB')
        shaded.blend_type='MULTIPLY'
        shaded.inputs[0].default_value=1
        mat.node_tree.links.new(tint.outputs[0],shaded.inputs[1])
        mat.node_tree.links.new(attributes.outputs['Color'],shaded.inputs[2])
        mat.node_tree.links.new(shaded.outputs[0],shader.inputs['Base Color'])
        mat.node_tree.links.new(shader.outputs[0],output.inputs['Surface'])
        materials[key]=mat
    components,boughs=parts()
    objects=[p.finish(scene,materials[p.material]) for p in components]
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active=objects[0]
    directory=Path(output_dir) if output_dir else OUT
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/'pine.glb'
    bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',export_materials='VIEWPORT',export_image_format='NONE',
        use_selection=True,use_active_scene=True,export_extras=False,export_yup=True,export_cameras=False,export_lights=False,export_apply=True,
        export_vertex_color='ACTIVE',export_all_vertex_colors=False,export_active_vertex_color_when_no_material=True)
    helpers=runpy.run_path(str(ROOT/'tools/build_hd2d_house.py'))
    metadata={'schema':1,'builder':'tools/build_hd2d_tree.py','builder_sha256':digest(__file__),
        'geometry_sha256':helpers['glb_signature'](path)[0],'logical_scale':SCALE,
        'mesh_count':len(objects),'surface_count':2,'triangles':sum(len(o.data.loop_triangles) for o in objects),
        'materials':MATERIALS,'texture_sha256':{s['texture']:digest(ROOT/'assets/hd2d'/(s['texture']+'.png')) for s in MATERIALS.values()},
        'components':{p.name:{'material':p.material,'volume_foliage':p.material=='needles'} for p in components},
        'bough_count':len(boughs),'origin':'root foot; Godot X/right Y/up Z/map-y',
        'review':'Single P1 rooted pine with closed bough volumes; no whole-tree cards; requires real GPU and visual approval'}
    (directory/'pine.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    if save_source:
        source=ROOT/'asserts/hd2d/props/pine.blend'
        source.parent.mkdir(parents=True,exist_ok=True)
        bpy.data.libraries.write(str(source),{scene},path_remap='RELATIVE',fake_user=True)
    print('XIA_PINE_BUILD',json.dumps({'glb':str(path),'meshes':metadata['mesh_count'],'triangles':metadata['triangles'],'boughs':len(boughs)}))
    return metadata


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.check:
        with tempfile.TemporaryDirectory(prefix='xia-pine-check-') as folder:
            actual=build(folder,save_source=False)
            expected=json.loads((OUT/'pine.json').read_text())
            helpers=runpy.run_path(str(ROOT/'tools/build_hd2d_house.py'))
            if actual!=expected or helpers['glb_signature'](OUT/'pine.glb')[0]!=actual['geometry_sha256']:
                raise RuntimeError('Pine assets differ; rebuild pine.glb')
            print('XIA_PINE_CHECK_PASS: deterministic volumes, UVs, materials and source hashes')
    else:
        build()


if __name__=='__main__':
    main()
