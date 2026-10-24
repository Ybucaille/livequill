/* LiveQuill : le ciel animé commun aux pages (rendu GPU WebGL2 de nuages volumétriques, sinon nuages CSS de secours).
   Ajoute lui-même son décor en fond de page : <script src="/__lq/sky.js" defer></script> suffit. */
(() => {
  const sky = document.createElement('div');
  sky.className = 'sky';
  sky.setAttribute('aria-hidden', 'true');
  sky.innerHTML = '<canvas></canvas><div class="astre"></div><div class="stars"></div><div class="clouds"></div>';
  document.body.prepend(sky);

  function cssClouds() {
    const layers = [[7, 0.55, 0.45, '7px', 150, 210], [5, 0.85, 0.7, '4px', 100, 150], [4, 1.25, 0.9, '2px', 70, 110]];
    let html = '';
    for (const [n, s, o, b, dmin, dmax] of layers) {
      for (let i = 0; i < n; i++) {
        const d = dmin + Math.random() * (dmax - dmin);
        html += `<div class="cloud" style="--y:${(Math.random() * 92).toFixed(1)}%;--s:${(s * (0.75 + Math.random() * 0.6)).toFixed(2)};--o:${o};--b:${b};--d:${d.toFixed(0)}s;--dl:-${(Math.random() * d).toFixed(0)}s"></div>`;
      }
    }
    sky.querySelector('.clouds').innerHTML = html;
  }

  function glSky() {
    const canvas = sky.querySelector('canvas'), gl = canvas.getContext('webgl2', { alpha: false, antialias: false, powerPreference: 'low-power' });
    if (!gl) return false;
    const vert = `#version 300 es
      void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);gl_Position=vec4(p*2.-1.,0.,1.);}`;
    // Bruit fractal déformé (« domain warping ») sur trois couches ; éclairage par différence de densité vers l'astre
    const frag = `#version 300 es
      precision highp float;
      out vec4 o;
      uniform vec2 R; uniform float T, D; uniform vec2 M;
      float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
        return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+1.),f.x),f.y);}
      float fbm(vec2 p,int k){float v=0.,a=.5;for(int i=0;i<6;i++){if(i>=k)break;v+=a*n(p);p=mat2(1.6,1.2,-1.2,1.6)*p+.17;a*=.5;}return v;}
      float cloud(vec2 q,float t,int k){
        vec2 w=vec2(fbm(q*.9+vec2(t*.6,0.),3),fbm(q*.9+vec2(4.7,1.3-t*.4),3));
        return fbm(q*1.4+w*1.6+vec2(t,0.),k);}
      void main(){
        vec2 uv=gl_FragCoord.xy/R, p=(uv-.5)*vec2(R.x/R.y,1.);
        vec3 sky=mix(mix(vec3(.74,.87,1.),vec3(.18,.46,.90),pow(uv.y,.85)),mix(vec3(.07,.12,.30),vec3(.008,.018,.06),uv.y),D);
        vec2 L=vec2(.33*R.x/R.y,.42);
        float g=exp(-length((p-L)*vec2(1.,1.25))*2.6);
        sky+=mix(vec3(1.,.9,.68)*.55,vec3(.55,.66,1.)*.32,D)*g;
        sky+=mix(vec3(1.,.97,.85),vec3(.85,.9,1.),D)*smoothstep(.06,.045,length(p-L))*mix(.9,.75,D);
        // étoiles (nuit) : grille aléatoire, scintillement lent
        vec2 sg=p*120.,sid=floor(sg),sf=fract(sg)-.5;float sr=h(sid);
        float st=step(.985,sr)*(1.-smoothstep(.0,.09,length(sf-(vec2(h(sid+3.),h(sid+7.))-.5)*.6)))*(.6+.4*sin(T*(.6+sr*2.)+sr*40.));
        sky+=vec3(.85,.9,1.)*st*D*smoothstep(-.1,.3,p.y);
        vec3 lit=mix(vec3(1.),vec3(.72,.80,1.),D), shade=mix(vec3(.55,.65,.82),vec3(.08,.12,.29),D), rim=mix(vec3(1.,.93,.80),vec3(.85,.92,1.),D);
        // couches : lointaine (voiles), milieu, proche (mer de nuages en bas)
        for(int l=0;l<3;l++){
          float fl=float(l);
          float sc=mix(2.6,1.15,fl*.5), sp=mix(.010,.034,fl*.5), par=mix(.03,.12,fl*.5);
          vec2 q=p*sc+M*par+vec2(fl*7.3,fl*3.1);
          float t=T*sp;
          float c=cloud(q,t,l==0?4:5);
          float mask=l==0?.55*smoothstep(-.2,.3,p.y)+.15:l==1?1.-smoothstep(-.1,.38,p.y):1.-smoothstep(-.42,-.02,p.y);
          float th=l==2?.36:l==1?.42:.47;
          float dns=smoothstep(th,th+.2,c)*mask;
          if(dns<.003)continue;
          vec2 toL=normalize(L-p)*.09;
          float c2=cloud(q+toL*sc,t,3);
          float light=clamp(.5+(c-c2)*4.5,0.,1.);
          vec3 col=mix(shade,lit,light);
          col+=rim*smoothstep(.45,0.,dns)*(g*.9+.12)*light;
          float a=dns*(l==0?.6:l==1?.88:1.);
          sky=mix(sky,col,a);
        }
        o=vec4(sky,1.);
      }`;
    const sh = (type, src) => { const x = gl.createShader(type); gl.shaderSource(x, src); gl.compileShader(x); if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(x)); return x; };
    let prog;
    try {
      prog = gl.createProgram(); gl.attachShader(prog, sh(gl.VERTEX_SHADER, vert)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, frag)); gl.linkProgram(prog);
      if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(prog));
    } catch (e) { console.warn('LiveQuill sky:', e); return false; }
    gl.useProgram(prog);
    gl.bindVertexArray(gl.createVertexArray());
    const U = k => gl.getUniformLocation(prog, k), uR = U('R'), uT = U('T'), uD = U('D'), uM = U('M');
    const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches, dark = matchMedia('(prefers-color-scheme: dark)');
    let D = dark.matches ? 1 : 0, mx = 0, my = 0, px = 0, py = 0, last = 0, stopped = false;
    const SCALE = 0.5;   // nuages flous : la demi-résolution suffit et divise le coût par quatre
    function resize() {
      canvas.width = Math.max(1, Math.round(innerWidth * SCALE)); canvas.height = Math.max(1, Math.round(innerHeight * SCALE));
      gl.viewport(0, 0, canvas.width, canvas.height);
    }
    addEventListener('resize', () => { resize(); if (reduced) draw(0, true); }, { passive: true });
    addEventListener('pointermove', e => { mx = e.clientX / innerWidth - .5; my = .5 - e.clientY / innerHeight; }, { passive: true });
    dark.addEventListener('change', () => { if (reduced) { D = dark.matches ? 1 : 0; draw(0, true); } });
    canvas.addEventListener('webglcontextlost', e => { e.preventDefault(); stopped = true; sky.classList.remove('gl'); cssClouds(); }, { once: true });
    function draw(t, force) {
      if (stopped) return;
      if (force || (!document.hidden && (t - last > 33 || !last))) {
        last = t || 1;
        D += ((dark.matches ? 1 : 0) - D) * .06;          // transition douce si le système change de thème
        px += (mx - px) * .04; py += (my - py) * .04;
        gl.uniform2f(uR, canvas.width, canvas.height); gl.uniform1f(uT, reduced ? 40 : t * .001 + 40);
        gl.uniform1f(uD, D); gl.uniform2f(uM, px, py);
        gl.drawArrays(gl.TRIANGLES, 0, 3);
      }
      if (!reduced) requestAnimationFrame(draw);
    }
    resize(); draw(0, true);
    sky.classList.add('gl');
    return true;
  }
  if (!glSky()) cssClouds();
})();
