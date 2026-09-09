shader_type spatial;
uniform float elapsed=0.;
uniform vec3 tint=vec3(.015,.11,.14);
uniform float wave_height=1.4;
float wave(vec2 p) {
    return wave_height*0.55*(.8*sin(dot(p,vec2(.22,.13))-elapsed*1.2)+.4*sin(dot(p,vec2(-.16,.31))-elapsed*1.8)+.18*sin(dot(p,vec2(.7,.36))-elapsed*2.4));
}
void vertex() {
    vec2 p=VERTEX.xz;
    VERTEX.y=wave(p);
    float dx=(wave(p+vec2(.05,0))-wave(p-vec2(.05,0)))/.1;
    float dz=(wave(p+vec2(0,.05))-wave(p-vec2(0,.05)))/.1;
    NORMAL=normalize(vec3(-dx,1,-dz));
}
void fragment() {
    ALBEDO=tint;
    ROUGHNESS=.15;
    METALLIC=.25;
    SPECULAR=.5;
}
