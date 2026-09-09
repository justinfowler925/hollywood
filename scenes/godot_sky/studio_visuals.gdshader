shader_type canvas_item;
uniform float elapsed=0.;
uniform float daylight=1.;
uniform float energy=0.;
uniform vec3 bands=vec3(0.);
uniform int mode=0;
uniform vec3 primary=vec3(.02,.7,.75);
uniform vec3 secondary=vec3(.35,.15,.8);
uniform float intensity=1.;
uniform int camera_mode=0;
float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float noise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);return mix(mix(hash(i),hash(i+vec2(1,0)),f.x),mix(hash(i+vec2(0,1)),hash(i+vec2(1,1)),f.x),f.y);}
float terrain(vec2 p){float v=0.,a=.5;for(int i=0;i<5;i++){v+=a*noise(p);p=p*2.03+vec2(4.7,9.2);a*=.5;}return v;}
float star(vec2 p){vec2 cell=floor(p);vec2 f=fract(p)-.5;float r=hash(cell);return pow(max(0.,1.-length(f)*5.),8.)*step(.97,r);}
void fragment(){
 vec2 uv=UV;vec2 p=(uv-.5)*vec2(1.77778,1.);float t=elapsed;
 if(camera_mode==1)p.x+=.03*sin(t*.15);
 if(camera_mode==2){float a=.03*sin(t*.1);p=mat2(vec2(cos(a),sin(a)),vec2(-sin(a),cos(a)))*p;}
 vec3 color=vec3(.007,.012,.026);
 if(mode==0){
  float glow=0.;
  for(int i=0;i<7;i++){
   float j=float(i);vec2 q=p-vec2(.25*sin(t*.21+j*1.8),.18*cos(t*.17+j*2.1));
   float radius=.1+j*.047+energy*.045;
   float d=abs(length(q)-radius-.025*sin(atan(q.y,q.x)*6.+t*(.3+j*.08)));
   float line=.0025/(d+.004);glow+=line;
   color+=mix(primary,secondary,j/6.)*line*(.14+.13*energy)*intensity;
  }
  color+=primary*energy*.08*exp(-length(p)*3.);
  for(int i=0;i<24;i++){
   float j=float(i);float band=(i<8?bands.x:(i<16?bands.y:bands.z));
   float x=-.7+j*.06;float bar=step(abs(p.x-x),.008)*step(.37-p.y,.015+band*.09)*step(p.y,.37);
   color+=secondary*bar*.6;
  }
 }else if(mode==1){
  vec2 sp=p+vec2(t*.002,0.);color+=vec3(star(sp*70.)+star(sp*133.)*.35);
  float r=length(p-vec2(.1,0.));
  if(r<.29){vec3 n=vec3((p-vec2(.1,0.))/.29,sqrt(max(0.,1.-r*r/(.29*.29))));
   float lighting=max(0.,dot(n,normalize(vec3(-.6,-.4,1.))));
   vec2 globe=vec2(atan(n.x,n.z)+t*.025,asin(n.y))*3.;
   float land=terrain(globe*2.5);
   vec3 surface=mix(vec3(.007,.035,.12),mix(vec3(.025,.13,.065),primary*.2,.25),smoothstep(.49,.55,land));
   float clouds=smoothstep(.58,.72,terrain(globe*5.+vec2(t*.015,0.)));
   color=mix(surface,vec3(.65,.72,.8),clouds*.8)*(.015+lighting);

   color+=vec3(.1,.3,.6)*pow(1.-n.z,4.)*.7;
  }
  color+=primary*.05*exp(-abs(r-.3)*100.);
  float ring=abs(length((p-vec2(.1,0.))*vec2(1.,3.2))-.42);
  if(p.y>0.||r>.29)color+=secondary*.006/(ring+.01);
 }else{
  // Illustrated rainy window: city lights beyond glass, moving droplets in front.
  color=mix(vec3(.025,.035,.075),primary*.12,1.-uv.y);
  for(int i=0;i<28;i++){
   float j=float(i);float x=j/28.;float h=.2+hash(vec2(j,3.))*.35;
   if(uv.x>x&&uv.x<x+.03&&uv.y>1.-h){color=vec3(.012,.018,.033);
    vec2 grid=vec2(fract(uv.x*210.),fract(uv.y*85.));
    if(grid.x>.3&&grid.x<.65&&grid.y>.3&&grid.y<.6)color+=primary*.35*step(.4,hash(floor(uv*vec2(210.,85.))));
   }
  }
  vec2 drops=uv*vec2(40.,15.);vec2 id=floor(drops);float phase=hash(id);
  vec2 q=vec2(fract(drops.x)-.5,fract(drops.y+t*(.7+phase))-.5);
  float rain=exp(-abs(q.x)*65.)*exp(-abs(q.y)*9.);
  color+=vec3(.18,.3,.45)*rain;
  color+=secondary*.15*exp(-length((uv-vec2(.88,.65))*vec2(4.,2.))*3.);
  if(abs(uv.x-.5)<.008||uv.y>.93||uv.y<.035||uv.x<.025||uv.x>.975)color=vec3(.06,.033,.022);
 }
 color*=smoothstep(1.1,.25,length(p))*.4+.6;
 color*=daylight;
 COLOR=vec4(pow(max(color,vec3(0.)),vec3(.5))*1.25,1.);
}
