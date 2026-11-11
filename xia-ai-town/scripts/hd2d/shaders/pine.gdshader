shader_type spatial;
// Shared occlusion/depth/wind path, with restrained foliage/trunk light steps.
render_mode blend_mix, depth_prepass_alpha, cull_disabled, specular_disabled;
#include "res://scripts/hd2d/shaders/scenery_common.gdshaderinc"
void light() {
    float diffuse = clamp((dot(NORMAL, LIGHT) + 0.12) / 1.12, 0.0, 1.0);
    float level = floor(diffuse * 4.0 + 0.35) / 4.0;
    // Preserve the actual shadow attenuation and lamp/main-light colors.
    // Godot multiplies the integrated diffuse result by ALBEDO. Applying it
    // here again would square the dark green and lose the lit leaf masses.
    DIFFUSE_LIGHT += LIGHT_COLOR * ATTENUATION * level / PI;
}
