shader_type spatial;
render_mode cull_disabled;
varying float tip_weight;
void vertex() {
	tip_weight = clamp(VERTEX.y / 0.65, 0.0, 1.0);
	float phase = MODEL_MATRIX[3].x * 0.7 + MODEL_MATRIX[3].z * 0.45;
	VERTEX.x += sin(TIME * 1.6 + phase) * 0.065 * tip_weight * tip_weight;
	VERTEX.z += cos(TIME * 0.9 + phase) * 0.03 * tip_weight;
}
void fragment() {
	ALBEDO = pow(COLOR.rgb, vec3(2.2)) * vec3(0.78, 0.88, 0.68);
	ROUGHNESS = 1.0;
	SPECULAR = 0.0;
	// A quiet ground-contact tint, still shaded by real sun and building shadows.
	AO = mix(0.65, 1.0, tip_weight);
}
