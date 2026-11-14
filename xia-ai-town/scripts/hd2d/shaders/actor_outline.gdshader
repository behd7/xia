shader_type spatial;
// Second pass of an actor sprite. The inverted depth test keeps only pixels
// that some nearer surface covers, so scenery stays fully opaque and a hidden
// body reads as a contour drawn over its cover. Never lit, fogged or shadowed.
render_mode unshaded, blend_mix, depth_draw_never, depth_test_inverted, cull_disabled, shadows_disabled, fog_disabled;
uniform sampler2D albedo_texture : filter_nearest, repeat_disable;
uniform vec2 frames = vec2(10.0, 8.0);
uniform float edge_texels = 2.0;
uniform float fill : hint_range(0.0, 1.0) = 0.22;
instance uniform float outline_strength = 0.0;
instance uniform vec4 outline_color : source_color = vec4(0.96, 0.89, 0.69, 1.0);

void vertex() {
	// Same fixed-Y billboard as actor.gdshader: identical silhouette and foot.
	vec3 up = vec3(0.0, 1.0, 0.0);
	vec3 right = normalize(cross(up, MAIN_CAM_INV_VIEW_MATRIX[2].xyz));
	mat4 world = mat4(
		vec4(right * length(MODEL_MATRIX[0].xyz), 0.0),
		vec4(up * length(MODEL_MATRIX[1].xyz), 0.0),
		vec4(normalize(cross(right, up)) * length(MODEL_MATRIX[2].xyz), 0.0),
		MODEL_MATRIX[3]);
	MODELVIEW_MATRIX = VIEW_MATRIX * world;
	MODELVIEW_NORMAL_MATRIX = mat3(MODELVIEW_MATRIX);
	// Sit just in front of the body pass so the body itself never counts as
	// cover. Moving along the view ray keeps every screen pixel in place.
	vec4 view = MODELVIEW_MATRIX * vec4(VERTEX, 1.0);
	if (PROJECTION_MATRIX[3][3] == 0.0) {
		view.xyz *= 1.0 - 0.08 / max(-view.z, 0.1);
	} else {
		view.z += 0.08;
	}
	POSITION = PROJECTION_MATRIX * view;
}

bool solid(vec2 uv, vec2 low, vec2 high) {
	// Neighbouring atlas frames are not part of this pose.
	if (any(lessThan(uv, low)) || any(greaterThanEqual(uv, high))) { return false; }
	return texture(albedo_texture, uv).a >= 0.5;
}

void fragment() {
	if (outline_strength <= 0.0 || texture(albedo_texture, UV).a < 0.5) { discard; }
	vec2 cell = 1.0 / frames;
	vec2 low = floor(UV * frames) * cell;
	vec2 high = low + cell;
	vec2 texel = 1.0 / vec2(textureSize(albedo_texture, 0));
	float edge = 0.0;
	for (float reach = 1.0; reach <= edge_texels; reach += 1.0) {
		vec2 d = texel * reach;
		if (!solid(UV + vec2(d.x, 0.0), low, high) || !solid(UV - vec2(d.x, 0.0), low, high) ||
			!solid(UV + vec2(0.0, d.y), low, high) || !solid(UV - vec2(0.0, d.y), low, high)) {
			edge = 1.0;
		}
	}
	ALBEDO = outline_color.rgb;
	ALPHA = outline_strength * mix(fill, 1.0, edge) * outline_color.a;
}
