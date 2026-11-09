shader_type spatial;
// Local, feathered translucency around at most two protected bodies. The rest
// of a selected wall/roof keeps its normal opacity, depth test and shadow.
render_mode blend_mix, depth_prepass_alpha, cull_disabled;
#include "res://scripts/hd2d/shaders/scenery_common.gdshaderinc"
