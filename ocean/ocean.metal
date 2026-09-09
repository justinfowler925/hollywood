#include <metal_stdlib>
using namespace metal;
constant float TAU = 6.28318530718;
float segment(float2 p,float2 a,float2 b) { float2 ab=b-a;return length(p-a-ab*clamp(dot(p-a,ab)/dot(ab,ab),0.0,1.0)); }
kernel void ocean(texture2d<float,access::sample> image [[texture(0)]], texture2d<float,access::write> output [[texture(1)]], constant float4& u [[buffer(0)]], uint2 gid [[thread_position_in_grid]]) {
    if(gid.x>=output.get_width() || gid.y>=output.get_height()) return;
    constexpr sampler s(coord::normalized,address::clamp_to_edge,filter::linear);
    float2 uv=(float2(gid)+0.5)/float2(output.get_width(),output.get_height());
    float t=u.x*TAU;
    float depth=clamp((uv.y-u.y)/(1.0-u.y),0.0,1.0);
    float water=smoothstep(0.0,0.06,depth);
    // Perspective-compressed, independently timed swells. The horizon never moves.
    float perspective=pow(depth,0.65);
    float a=sin(uv.x*22.0 + perspective*55.0-t*9.0);
    float b=sin(uv.x*37.0-perspective*91.0+t*13.0+0.7);
    float c=sin(uv.x*71.0+perspective*123.0-t*17.0+2.1);
    float2 delta=float2((a*0.0016+b*0.0007)*perspective,(a*0.0022+c*0.00065)*perspective)*water;
    float2 sampleUV=uv+delta;
    // Aspect-fill the source while keeping all geometry fixed.
    float sourceAspect=float(image.get_width())/float(image.get_height());
    if(sourceAspect>u.w) sampleUV.x=(sampleUV.x-0.5)*u.w/sourceAspect+0.5;
    else sampleUV.y=(sampleUV.y-0.5)*sourceAspect/u.w+0.5;
    float3 color=image.sample(s,sampleUV).rgb;
    float glint=pow(max(0.0,a*0.45+b*0.3+c*0.25),8.0)*water*perspective;
    color*=1.0+water*perspective*(a*0.014+b*0.008);
    color+=glint*float3(0.065,0.067,0.06);
    // Two small distant gulls, entering and leaving beyond the frame, not teleporting in view.
    if(u.z>0.5) for(int i=0;i<2;i++) {
        float phase=fract(u.x+float(i)*0.37);
        float x=phase*2.8-0.9;
        float y=u.y*0.65+sin(t+float(i)*1.7)*0.035+float(i)*0.03;
        float2 p=float2((uv.x-x)*u.w,uv.y-y);
        float scale=i==0?0.006:0.004;
        float wing=sin(t*43.0+float(i))*0.45;
        float d=min(segment(p,float2(-scale,wing*scale),float2(0,scale*0.22)),segment(p,float2(0,scale*0.22),float2(scale,wing*scale)));
        float alpha=1.0-smoothstep(0.00028,0.00085,d);
        color=mix(color,float3(0.23,0.29,0.32),alpha*0.82);
    }
    output.write(float4(clamp(color,0.0,1.0),1),gid);
}
