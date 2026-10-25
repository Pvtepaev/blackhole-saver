// upscales the reduced-resolution black hole render to the window. Its alpha
// marks pixels the hole visibly changes; elsewhere the desktop screenshot is
// sampled at native resolution so text stays sharp away from the hole.
uniform sampler2D uTex;
uniform sampler2D uBg;
uniform int       uHasBg;
in vec2 vUV;
out vec4 outColor;

void main() {
    vec4 c = texture(uTex, vUV);
    if (uHasBg == 1)
        c.rgb = mix(texture(uBg, vec2(vUV.x, 1.0 - vUV.y)).rgb, c.rgb, c.a);
    outColor = vec4(c.rgb, 1.0);
}
