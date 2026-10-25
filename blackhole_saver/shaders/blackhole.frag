// blackhole.frag — a geodesic-traced black hole screensaver
//
// Ported from blackhole.glsl in ghostty-blackhole by s13k
// (https://github.com/s0xDk/ghostty-blackhole, MIT License), which follows
// Eric Bruneton's "Real-time High-Quality Rendering of Non-Rotating Black
// Holes" (https://ebruneton.github.io/black_hole_shader/). Bruneton
// precomputes Schwarzschild geodesics into lookup textures; here, as in the
// original, each pixel's null geodesic is integrated numerically instead —
// the Binet-form photon acceleration  a = -(3/2) h² x / r⁵  reproduces the
// exact Schwarzschild bending. Everything the camera sees falls out of that
// integration rather than being painted on:
//
//   * the shadow            — rays with impact parameter under
//                             b_crit = (3√3/2) r_s spiral into the horizon
//   * gravitational lensing — escaped rays are projected back onto the
//                             desktop "sky" plane: the screenshot bends,
//                             magnifies, and mirrors inside the Einstein ring
//   * photon ring           — rays winding near the r = 1.5 r_s photon sphere
//   * accretion disk        — a thin Keplerian disk the ray may cross several
//                             times (the far side arcs over and under the
//                             shadow); blackbody color from a Shakura–Sunyaev
//                             temperature profile, shifted and beamed by the
//                             relativistic factor g = √(1 − 1.5 r_s/r)/(1 − β·k̂)
//   * starfield             — a lensed sky so the bending reads even over
//                             empty background
//
// Screensaver port changes (vs. the Ghostty original):
//   * Ghostty size modes (pomodoro / tokens / demo), cursor-color decoding,
//     iDate, iTimeCursorChange and WORK_AREA are gone; size follows a slow
//     time-driven grow loop (or a fixed size) and the hole drifts across the
//     whole screen.
//   * GL fragCoord runs bottom-up (Ghostty's runs top-down), so the y flips
//     in the screen <-> world mapping are dropped and the background texture
//     (a desktop screenshot, rows top-down) is sampled with 1 - y.
//   * iChannel0 is a screenshot of the desktop taken just before launch; with
//     no screenshot a procedural 2D starfield stands in for it.
//
// Units: r_s (Schwarzschild radius) = 1. The screen mapping ties the shadow
// radius b_crit to HOLE_RADIUS * sz.
//
// The tunables below arrive as #defines prepended by shader.py from
// ~/.config/blackhole-saver/config.toml:
//   N_STEPS, HOLE_RADIUS, LENS_DEPTH, STAR_GAIN,
//   DISK_INNER, DISK_OUTER, DISK_INCL, DISK_ROLL, DISK_GAIN, DISK_OPACITY,
//   DISK_TEMP, DOPPLER_MIX, DISK_BEAM, DISK_SPEED, DISK_WIND, DISK_CONTRAST,
//   EXPOSURE, DRIFT_SPEED, DILATION_MIN,
//   SIZE_MODE, SIZE_MIN, FIXED_SIZE, GROW_PERIOD

uniform vec3      iResolution;
uniform float     iTime;
uniform sampler2D iChannel0;
uniform int       uHasBg;   // 1 = iChannel0 holds a desktop screenshot
uniform float     uSeed;    // per-monitor drift phase offset, seconds
uniform float     uFade;    // 0 -> 1 fade-in at launch

out vec4 outColor;

#define SIZE_MODE_GROW  0   // size breathes between SIZE_MIN and 1 over GROW_PERIOD s
#define SIZE_MODE_FIXED 1   // size stays at FIXED_SIZE

// The disk's whole look in one bundle (kept from the original, where the
// demo mode blended presets; here it just carries the tunables and the
// compiler folds it back to constants).
struct DiskLook {
    float temp, incl, roll, inner, outer, opac, dopp, beam,
          gain, contr, wind, speed, expo, star;
};
const DiskLook LOOK_DEFAULT = DiskLook(
    DISK_TEMP, DISK_INCL, DISK_ROLL, DISK_INNER, DISK_OUTER, DISK_OPACITY,
    DOPPLER_MIX, DISK_BEAM, DISK_GAIN, DISK_CONTRAST, DISK_WIND, DISK_SPEED,
    EXPOSURE, STAR_GAIN);

// critical impact parameter of a Schwarzschild hole, in r_s: rays under this
// fall in; it is the apparent (shadow) radius seen from far away. Physics,
// not taste.
#define B_CRIT 2.5980762

// ------------------------------------------------------------------- noise --
float hash21(vec2 p) {
    p = fract(p * vec2(234.34, 435.345));
    p += dot(p, p + 34.23);
    return fract(p.x * p.y);
}

// value noise whose y lattice wraps every perY cells — used for the disk's
// angular dimension so the streaks tile seamlessly across the atan branch cut
// (perY must be an integer; y must advance by exactly perY per full turn)
float vnoiseWrapY(vec2 p, float perY) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float y0 = mod(i.y, perY), y1 = mod(i.y + 1.0, perY);
    return mix(mix(hash21(vec2(i.x, y0)),       hash21(vec2(i.x + 1.0, y0)), f.x),
               mix(hash21(vec2(i.x, y1)),       hash21(vec2(i.x + 1.0, y1)), f.x),
               f.y);
}

// mirrored repeat keeps lensed samples on-screen without edge smearing
vec2 mirrorUV(vec2 u) { return 1.0 - abs(1.0 - mod(u, 2.0)); }

vec2 rot(vec2 v, float a) {
    float c = cos(a), s = sin(a);
    return vec2(c * v.x - s * v.y, s * v.x + c * v.y);
}

// unit Lissajous wander: 2+2 incommensurate sines per axis, so the orbit
// never visibly repeats; scale the argument for speed, the result for reach
vec2 lissa(float t) {
    return vec2(0.75 * sin(t * 0.37) + 0.25 * sin(t * 0.83 + 1.0),
                0.70 * sin(t * 0.54 + 2.1) + 0.30 * sin(t * 1.07));
}

// blackbody color from temperature in Kelvin (Tanner Helland fit, normalized)
vec3 blackbody(float T) {
    float t = clamp(T, 1500.0, 40000.0) / 100.0;
    float r = t <= 66.0 ? 1.0
                        : clamp(1.292936 * pow(t - 60.0, -0.1332047), 0.0, 1.0);
    float g = t <= 66.0 ? clamp(0.3900816 * log(t) - 0.6318414, 0.0, 1.0)
                        : clamp(1.1298909 * pow(t - 60.0, -0.0755148), 0.0, 1.0);
    float b = t >= 66.0 ? 1.0
                        : (t <= 19.0 ? 0.0
                                     : clamp(0.5432068 * log(t - 10.0) - 1.1962540, 0.0, 1.0));
    return vec3(r, g, b);
}

// sparse procedural starfield indexed by ray direction — because it is
// sampled with the *bent* ray, stars smear into arcs around the hole for free
vec3 stars(vec3 d) {
    vec2 sph = vec2(atan(d.x, -d.z), asin(clamp(d.y, -1.0, 1.0)));
    vec2 g   = sph * 40.0;
    vec2 id  = floor(g);
    float h  = hash21(id);
    if (h < 0.92) return vec3(0.0);
    vec2 f   = fract(g) - 0.5;
    vec2 off = (vec2(hash21(id + 17.3), hash21(id + 31.7)) - 0.5) * 0.7;
    float spark = smoothstep(0.10, 0.0, length(f - off));
    float tw    = 0.7 + 0.3 * sin(iTime * (0.5 + 2.0 * hash21(id + 5.1)) + 40.0 * h);
    vec3 tint   = mix(vec3(1.0, 0.82, 0.60), vec3(0.75, 0.85, 1.0), hash21(id + 2.9));
    return tint * spark * tw * ((h - 0.92) / 0.08);
}

// ------------------------------------------------------------- background --
// One layer of the flat fallback sky: a hashed grid with at most one star per
// cell, sized in screen-height units so density doesn't depend on resolution.
vec3 skyLayer(vec2 q, float cells, float keep, float rad) {
    vec2 g  = q * cells;
    vec2 id = floor(g);
    float h = hash21(id + cells);
    if (h < keep) return vec3(0.0);
    vec2 off  = (vec2(hash21(id + 11.1), hash21(id + 23.7)) - 0.5) * 0.7;
    float d   = length(fract(g) - 0.5 - off);
    float s   = smoothstep(rad, 0.0, d) * (h - keep) / (1.0 - keep);
    vec3 tint = mix(vec3(1.0, 0.85, 0.65), vec3(0.70, 0.82, 1.0), hash21(id + 5.3));
    return tint * s;
}

// procedural stand-in for the desktop screenshot: dark sky, faint nebula
// haze, three star layers. Static — it only moves through the lensing.
vec3 skyFallback(vec2 uv) {
    vec2 q = uv * vec2(iResolution.x / iResolution.y, 1.0);
    vec3 col = vec3(0.004, 0.006, 0.012);
    float n = vnoiseWrapY(q * 3.0, 1e4) * 0.6 + vnoiseWrapY(q * 7.0 + 3.1, 1e4) * 0.4;
    col += vec3(0.020, 0.010, 0.035) * smoothstep(0.45, 1.0, n);
    col += skyLayer(q, 240.0, 0.90, 0.22) * 0.55;
    col += skyLayer(q, 110.0, 0.93, 0.16) * 0.85;
    col += skyLayer(q,  45.0, 0.96, 0.10) * 1.40;
    return col;
}

// the "sky plane" behind the hole: the screenshot (rows top-down, hence the
// 1 - y) or the fallback starfield
vec3 bgSample(vec2 uv) {
    vec2 m = mirrorUV(uv);
    if (uHasBg == 1) return texture(iChannel0, vec2(m.x, 1.0 - m.y)).rgb;
    return skyFallback(m);
}

// ------------------------------------------------------------------- image --
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2  res    = iResolution.xy;
    vec2  uv     = fragCoord / res;      // GL: y runs bottom-up
    float aspect = res.x / res.y;

    // disk pattern clock (upstream ran it off iTime * DRIFT_SPEED with
    // DRIFT_SPEED = 1; drift has its own clock here so a slow wander doesn't
    // also slow the orbit)
    float t  = iTime;
    float td = (iTime + uSeed) * DRIFT_SPEED;

    DiskLook L = LOOK_DEFAULT;

    // disk extent in r_s, sanitized: the inner edge stays outside the photon
    // sphere (1.5 r_s) where circular orbits stop making sense
    float rin  = max(L.inner, 1.6);
    float rout = max(L.outer, rin + 0.5);

    // ---- master intensity I and size sz ----
    // I drives lensing/disk/dilation; sz scales the shadow.
    float I;
    if (SIZE_MODE == SIZE_MODE_FIXED) {
        I = FIXED_SIZE;
    } else {
        // slow breathing loop: starts small, grows to full over half a
        // period, shrinks back
        float ph = 0.5 - 0.5 * cos(6.2831853 * iTime / max(GROW_PERIOD, 1.0));
        I = mix(SIZE_MIN, 1.0, ph);
    }
    float sz = I;
    float rh = HOLE_RADIUS * sz;           // shadow radius in screen units

    // ---- drift: Lissajous wander scaled to the room the hole has ----
    // Margins keep the shadow and bright inner disk on-screen (the disk's
    // projected half-extent is rout/B_CRIT shadow radii); a hole too big for
    // the screen just gets less room. Never clipped — clipping parks the hole
    // against a wall and reads as a freeze.
    float ext   = (rout / B_CRIT) * rh * 0.8;
    vec2  marg  = vec2(min(ext, 0.45) / aspect, min(ext, 0.45));
    vec2  room  = max(vec2(0.5) - marg, vec2(0.0));
    vec2  wob   = min(vec2(0.012 + 0.02 * I), room * 0.3 + 0.004);
    vec2  center = vec2(0.5) + lissa(td) * max(room - wob, vec2(0.0))
                 + wob * vec2(cos(td * 0.8), sin(td * 1.0));

    // fade-in at launch: warp and disk grow from nothing over a second or two
    float vis = smoothstep(0.0, 0.10, I) * uFade;
    if (vis <= 0.0) {
        fragColor = vec4(bgSample(uv), 1.0);
        return;
    }

    // ---- gravitational time dilation (theme feature) ----
    // A heavier hole slows the clock locally: the accretion disk visibly winds
    // down as the hole grows. dil multiplies the disk's pattern rate, falling
    // from 1 toward DILATION_MIN as the hole reaches full mass.
    float dil = mix(1.0, DILATION_MIN, I);

    // (the original's work-area shield is gone; only the fade remains)
    float shield = vis;

    // aspect-corrected frame centered on the hole (y in units of screen height)
    vec2  p    = (uv - center) * vec2(aspect, 1.0);
    float plen = length(p);

    // screen <-> world mapping: the shadow's true angular size is B_CRIT r_s,
    // and we want it rh screen units wide, so 1 screen unit = W Schwarzschild
    // radii. pr is the pixel in world units, y-up, with the system roll applied.
    float W  = B_CRIT / max(rh, 1e-4);
    vec2  pr = rot(p, L.roll) * W;
    float b  = length(pr);              // the ray's impact parameter, in r_s

    // distance-window: real lensing falls off as 1/b and would shimmer the
    // desktop across the whole screen as the hole drifts; fade it out a few
    // disk diameters away (deliberately unphysical)
    float window = exp(-pow(plen / (7.0 * rh), 2.0));

    float bmax = rout + 3.0;            // rays beyond this can't touch the disk
    float Z0   = max(14.0, rout + 5.0); // camera distance (shared with the tracer)

    // ================= far field: analytic weak deflection ==================
    // The geodesic region's rays start at the finite camera z = Z0 and get
    // projected back onto the sky plane, so they bend *less* than the
    // textbook alpha = 2 r_s/b from infinity — using that raw leaves a ~20%
    // displacement jump at the handoff radius, a visible circular seam.
    // This is the same finite-camera mapping, fitted against the integrator
    // (sub-1% at the boundary): disp = (2/b)(1.29u + 0.07)(L - 2.14u + 0.75)
    // in world units, with u = Z0/sqrt(Z0^2 + b^2).
    if (b >= bmax) {
        float u    = Z0 * inversesqrt(Z0 * Z0 + b * b);
        float defl = (2.0 / (W * W)) / max(plen, 1e-4)
                   * (1.29 * u + 0.07) * max(LENS_DEPTH - 2.14 * u + 0.75, 0.0)
                   * window * shield;
        vec2  dir  = p / max(plen, 1e-5);
        vec3  term;
        // mild chromatic aberration: blue bends a touch more than red; faded
        // in away from the handoff circle (the geodesic side has none)
        float ab = 0.035 * smoothstep(1.0, 2.0, b / bmax);
        for (int i = 0; i < 3; i++) {
            float k   = 1.0 + (float(i) - 1.0) * ab;
            vec2  sp  = p - dir * defl * k;
            term[i]   = bgSample(center + sp / vec2(aspect, 1.0))[i];
        }
        // same starfield as the geodesic region, lit through the weak-field
        // bend so stars don't pop at the boundary circle
        vec3 d = normalize(vec3(-(pr / b) * (2.0 / b), -1.0));
        fragColor = vec4(term + stars(d) * L.star * window * shield, 1.0);
        return;
    }

    // ====================== near field: trace the geodesic ==================
    // Parallel rays from a distant camera at +z. The hole is at the origin,
    // r_s = 1. Integrate  x'' = -(3/2) h² x / r⁵  (exact Schwarzschild photon
    // bending; h = |x×v| is conserved, so it's computed once).
    vec3  x  = vec3(pr, Z0);
    vec3  v  = vec3(0.0, 0.0, -1.0);
    float h2 = dot(pr, pr);

    // disk plane: normal tilted DISK_INCL about the screen x-axis
    float ci = cos(L.incl), si = sin(L.incl);
    vec3  n  = vec3(0.0, si, ci);
    vec3  e2 = vec3(0.0, ci, -si);      // in-plane axis completing (x̂, e2, n)
    float sdir = L.speed < 0.0 ? -1.0 : 1.0;
    float spd  = abs(L.speed);

    vec3  emitc = vec3(0.0);            // accumulated disk light (HDR)
    float trans = 1.0;                  // transmittance toward the background
    bool  captured = false;
    float sPrev = dot(x, n);
    vec3  xPrev = x;

    for (int i = 0; i < N_STEPS; i++) {
        float r2 = dot(x, x);
        if (r2 < 1.0) { captured = true; break; }        // through the horizon
        if (x.z < -Z0 && v.z < 0.0) break;               // escaped out the back
        if (r2 > 4.0 * Z0 * Z0) break;                   // flung far sideways
        float r  = sqrt(r2);
        // step scales with radius: fine near the photon sphere, coarse far
        // out (the far cap is loose — bending falls off as 1/r^4, and longer
        // approach/exit strides leave more of the N_STEPS budget for the
        // strongly curved region)
        float dt = clamp(0.16 * r, 0.03, 1.5);
        // leapfrog (kick-drift-kick) keeps the near-critical orbits stable
        vec3 a = -1.5 * h2 * x / (r2 * r2 * r);
        v += a * (0.5 * dt);
        x += v * dt;
        r2 = dot(x, x);
        r  = sqrt(r2);
        a  = -1.5 * h2 * x / (r2 * r2 * r);
        v += a * (0.5 * dt);

        // ---- thin-disk crossing: the ray pierced the disk plane ----
        float s = dot(x, n);
        if (s * sPrev < 0.0 && trans > 0.02) {
            float tc = sPrev / (sPrev - s);
            vec3  xc = mix(xPrev, x, tc);
            float rc = length(xc);
            if (rc > rin && rc < rout) {
                float band = smoothstep(rin, rin * 1.25, rc)
                           * (1.0 - smoothstep(rout * 0.70, rout, rc));

                // disk-plane polar coords for the streak texture
                float phi   = atan(dot(xc, e2), xc.x);
                float turns = phi / 6.2831853;
                float kep   = pow(rin / rc, 1.5);
                // √(1 − 1.5/r): time runs slower for the inner orbits — the
                // pattern visibly freezes toward the inner edge; dil winds the
                // whole disk down as the hole grows
                float gloc  = sqrt(max(1.0 - 1.5 / rc, 0.02));
                float swirl = rc * L.wind * 0.12 - t * kep * spd * gloc * dil * sdir;
                float streaks = vnoiseWrapY(vec2(rc * 2.8, turns * 19.0 + swirl * 3.0), 19.0) * 0.65 +
                                vnoiseWrapY(vec2(rc * 1.0, turns * 9.0  + swirl * 1.5 + 7.0), 9.0) * 0.35;
                streaks = 0.35 + L.contr * streaks * streaks;

                // relativistic Doppler + gravitational shift for gas on a
                // circular geodesic: g = √(1 − 1.5/r) / (1 − β·k̂), with the
                // photon direction at the crossing taken from the ray itself
                vec3  gasdir = normalize(cross(n, xc)) * sdir;
                float beta   = clamp(inversesqrt(max(2.0 * (rc - 1.0), 0.2)), 0.0, 0.99);
                float g      = gloc / max(1.0 + beta * dot(gasdir, normalize(v)), 0.05);
                g = mix(1.0, g, L.dopp);

                // Shakura–Sunyaev temperature profile, peak normalized to 1
                float xpr   = max(1.0 - sqrt(rin / rc), 0.0);
                float tprof = pow(rin / rc, 0.75) * pow(xpr, 0.25) / 0.488;
                vec3  cbb   = blackbody(L.temp * tprof * g);      // doppler-shifted color
                float boost = pow(g, L.beam);                     // relativistic beaming

                float density = band * streaks;
                emitc += trans * cbb * (L.gain * 2.2 * density * tprof * tprof * boost);
                trans *= 1.0 - clamp(L.opac * density, 0.0, 1.0);
            }
        }
        sPrev = s;
        xPrev = x;
    }
    // rays still wound up near the photon sphere when the budget ran out are
    // as good as captured
    if (!captured && dot(x, x) < 4.0) captured = true;

    // ---- background: where did the escaped ray come from? ----
    vec3 bg = vec3(0.0);
    if (!captured) {
        vec3 d = normalize(v);
        bg += stars(d) * L.star * window * shield;
        if (d.z < -0.05) {
            // project the straight exit ray onto the sky plane at
            // z = -LENS_DEPTH and map back to screen space
            float tpl = (-LENS_DEPTH - x.z) / d.z;
            vec3  hp  = x + d * tpl;
            vec2  sp  = rot(hp.xy, -L.roll) / W;
            // the *displacement* is faded by window/shield, never the color —
            // a continuous warp leaves no seam at the far field
            vec2  suv = center + (p + (sp - p) * window * shield) / vec2(aspect, 1.0);
            // rays bent past ~90° never reach the sky plane behind the hole;
            // they fade to the starfield instead of sampling garbage
            float toward = smoothstep(0.05, 0.35, -d.z);
            bg += bgSample(suv) * toward;
        }
    }

    // disk light is HDR; tonemap it on top of the background sample. The
    // launch fade blends the shadow/disk in from the plain background.
    vec3 col = bg * trans + (vec3(1.0) - exp(-emitc * L.expo));
    col = mix(bgSample(uv), col, clamp(uFade, 0.0, 1.0));
    fragColor = vec4(col, 1.0);
}

void main() {
    mainImage(outColor, gl_FragCoord.xy);
}
