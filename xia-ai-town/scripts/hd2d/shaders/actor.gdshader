shader_type spatial;
// A painted upright represents a body, not a flat wall. A sky-biased normal
// and wrapped diffuse keep side/backlit views readable under the scene lights.
render_mode blend_mix, depth_draw_opaque, cull_disabled, diffuse_lambert_wrap, specular_disabled;
uniform sampler2D albedo_texture : source_color, filter_nearest, repeat_disable;
uniform bool unshaded = false;

void vertex() {
	// Match Sprite3D's fixed-Y billboard, including its pitch scale and foot.
	vec3 up = vec3(0.0, 1.0, 0.0);
	vec3 right = normalize(cross(up, MAIN_CAM_INV_VIEW_MATRIX[2].xyz));
	mat4 world = mat4(
		vec4(right * length(MODEL_MATRIX[0].xyz), 0.0),
		vec4(up * length(MODEL_MATRIX[1].xyz), 0.0),
		vec4(normalize(cross(right, up)) * length(MODEL_MATRIX[2].xyz), 0.0),
		MODEL_MATRIX[3]);
	MODELVIEW_MATRIX = VIEW_MATRIX * world;
	MODELVIEW_NORMAL_MATRIX = mat3(MODELVIEW_MATRIX);
}

void fragment() {
	vec4 tex = texture(albedo_texture, UV) * COLOR;
	ALPHA = tex.a;
	ALPHA_SCISSOR_THRESHOLD = 0.5;
	// Only the shading normal tilts; the upright, silhouette and depth do not.
	vec3 sky_normal = (VIEW_MATRIX * vec4(0.0, 1.0, 0.0, 0.0)).xyz;
	NORMAL = normalize(NORMAL * 0.5 + sky_normal * 0.8660254);
	ROUGHNESS = 1.0;
	ALBEDO = unshaded ? vec3(0.0) : tex.rgb;
	EMISSION = unshaded ? tex.rgb : vec3(0.0);
}
