uniform sampler2D albedo_texture : source_color, filter_nearest_mipmap, repeat_enable;
uniform vec4 tint : source_color = vec4(1.0);
uniform vec2 uv_scale = vec2(1.0);
uniform vec4 flat_color : source_color = vec4(1.0);
uniform float color_mix : hint_range(0.0, 1.0) = 0.0;
uniform float fade_residue : hint_range(0.0, 0.1) = 0.04;
instance uniform float occlusion_fade = 0.0;
instance uniform vec2 occlusion_weights = vec2(0.0);
// Pixel-space center and half extent, relative to the native 3D viewport.
instance uniform vec4 occlusion_mask0 = vec4(0.0);
instance uniform vec4 occlusion_mask1 = vec4(0.0);
// World-space vertical body plane, facing the camera: normal.xyz, distance.w.
instance uniform vec4 occlusion_plane0 = vec4(0.0);
instance uniform vec4 occlusion_plane1 = vec4(0.0);
uniform bool upright = false;
uniform bool foliage_sway = false;
uniform bool volume_foliage = false;
uniform bool vertex_color = false;
uniform float sway = 0.0;
uniform bool hanging = false;
uniform float glow = 0.0;
uniform bool water_motion = false;
varying vec3 position_world;
varying vec3 pine_local;
varying vec3 pine_normal;
void vertex() {
	mat4 world = MODEL_MATRIX;
	if (upright) {
		vec3 up = vec3(0.0, 1.0, 0.0);
		vec3 right = normalize(cross(up, MAIN_CAM_INV_VIEW_MATRIX[2].xyz));
		world = mat4(vec4(right * length(MODEL_MATRIX[0].xyz), 0.0),
			vec4(up * length(MODEL_MATRIX[1].xyz), 0.0),
			vec4(normalize(cross(right, up)) * length(MODEL_MATRIX[2].xyz), 0.0), MODEL_MATRIX[3]);
	}
	// Never evaluate motion on tiled architecture: UV.y can exceed one and
	// pow(negative, 2) previously produced NaN walls/roofs on RADV.
	if (volume_foliage && sway != 0.0) {
		// Closed boughs keep their depth as they move. All vertices use the
		// tree-local height, so neither a UV seam nor the tree foot can swim.
		float weight = clamp(VERTEX.y / 9.75, 0.0, 1.0);
		VERTEX.x += sin(TIME * 1.2 + MODEL_MATRIX[3].x + VERTEX.y * 0.35) * sway * weight;
		VERTEX.z += cos(TIME * 0.8 + MODEL_MATRIX[3].z) * sway * 0.5 * weight;
	} else if ((upright || foliage_sway) && sway != 0.0) {
		float weight = clamp(hanging ? UV.y : (1.0 - UV.y), 0.0, 1.0);
		VERTEX.x += sin(TIME * 1.2 + MODEL_MATRIX[3].x) * sway * weight * weight;
	}
	pine_local = VERTEX;
	pine_normal = NORMAL;
	position_world = (world * vec4(VERTEX, 1.0)).xyz;
	MODELVIEW_MATRIX = VIEW_MATRIX * world;
	MODELVIEW_NORMAL_MATRIX = mat3(MODELVIEW_MATRIX);
}
float local_mask(vec2 pixel, vec4 mask, vec4 plane) {
	if (mask.z <= 0.0 || mask.w <= 0.0) { return 0.0; }
	// Keep faces behind the character opaque, even within a selected component.
	float foreground = step(0.0001, dot(position_world, plane.xyz) - plane.w);
	float corner = min(mask.z * 0.75, mask.w * 0.45);
	vec2 q = abs(pixel - mask.xy) - (mask.zw - vec2(corner));
	float distance = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - corner;
	float feather = max(3.0, mask.z * 0.40);
	return foreground * (1.0 - smoothstep(0.0, feather, distance));
}
void fragment() {
	vec2 sample_uv = UV * uv_scale;
	if (water_motion) {
		sample_uv += vec2(TIME * 0.006, sin(TIME * 0.6) * 0.008);
	}
	vec4 tex = texture(albedo_texture, sample_uv) * tint;
	if (water_motion) {
		float wave = sin(position_world.x * 6.0 + position_world.z * 4.0 - TIME * 0.9);
		tex.rgb = mix(vec3(0.15, 0.28, 0.27), tex.rgb, 0.35) * (1.0 + step(0.82, wave) * 0.10);
	}
	if (volume_foliage) {
		// Project the surface at one texel per logical pixel on every side;
		// an XZ-only UV stretched needle rows vertically down steep bough lips.
		vec3 axis = abs(pine_normal);
		vec2 uv = axis.y >= max(axis.x, axis.z) ? pine_local.xz :
			(axis.x > axis.z ? pine_local.zy : pine_local.xy);
		tex = texture(albedo_texture, uv * 0.25) * tint;
		// Texture is subordinate to the bough's broad light/mid/shadow masses.
		vec3 quiet = vec3(0.12, 0.23, 0.12) * tint.rgb;
		tex.rgb = mix(quiet, tex.rgb, 0.50);
	}
	if (tex.a < 0.5) { discard; }
	vec2 pixel = SCREEN_UV * VIEWPORT_SIZE;
	float reveal = min(occlusion_fade, max(
		local_mask(pixel, occlusion_mask0, occlusion_plane0) * occlusion_weights.x,
		local_mask(pixel, occlusion_mask1, occlusion_plane1) * occlusion_weights.y));
	// Closed crowns AND the branched/rooted trunk can stack multiple front/
	// back surfaces. Their material uses a lower residue; all other scenery
	// keeps 4%. Outside the body mask and in shadow passes opacity stays one.
	float opacity = IN_SHADOW_PASS ? 1.0 : mix(1.0, fade_residue, reveal);
	ALBEDO = mix(tex.rgb, flat_color.rgb * tint.rgb, color_mix);
	if (vertex_color) { ALBEDO *= COLOR.rgb; }
	if (volume_foliage || vertex_color) { SPECULAR = 0.0; }
	ROUGHNESS = 0.95;
	ALPHA = tex.a * opacity;
	EMISSION = tex.rgb * glow;
}
