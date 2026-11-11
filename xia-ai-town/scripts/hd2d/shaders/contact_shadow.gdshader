shader_type spatial;
render_mode unshaded, blend_mix, depth_draw_never, cull_disabled;
void fragment() {
	float r = length((UV - vec2(0.5)) * 2.0);
	ALBEDO = vec3(0.055, 0.07, 0.06);
	ALPHA = (1.0 - smoothstep(0.25, 1.0, r)) * 0.30;
}
