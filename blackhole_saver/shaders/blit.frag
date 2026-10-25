// upscales the reduced-resolution black hole render to the window
uniform sampler2D uTex;
in vec2 vUV;
out vec4 outColor;

void main() {
    outColor = vec4(texture(uTex, vUV).rgb, 1.0);
}
