// Quotal download page — pill demo, orb, tabs, models, reveal
(function () {
  "use strict";
  document.documentElement.classList.add("js");
  document.getElementById("yr").textContent = new Date().getFullYear();

  // Mobile nav
  const burger = document.getElementById("burger");
  const mnav = document.getElementById("mnav");
  burger.addEventListener("click", () => {
    const open = mnav.classList.toggle("open");
    burger.setAttribute("aria-expanded", open ? "true" : "false");
    burger.textContent = open ? "×" : "—";
  });
  mnav.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => {
    mnav.classList.remove("open"); burger.textContent = "—";
  }));

  // Copy buttons
  const toast = document.getElementById("toast");
  let toastT;
  function say(msg) {
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toastT);
    toastT = setTimeout(() => toast.classList.remove("show"), 1600);
  }
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(b.dataset.copy); say("copied to clipboard"); }
    catch { say("copy failed — select manually"); }
  }));

  // Scroll reveal
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
  }), { threshold: 0.12 });
  document.querySelectorAll(".reveal").forEach((el) => io.observe(el));

  // Tabs
  const tabBtns = [...document.querySelectorAll("[data-tab]")];
  const panes = [...document.querySelectorAll("[data-pane]")];
  tabBtns.forEach((b) => b.addEventListener("click", () => {
    tabBtns.forEach((x) => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b ? "true" : "false"); });
    panes.forEach((p) => p.classList.toggle("on", p.dataset.pane === b.dataset.tab));
  }));

  // Model rows (visual selection)
  document.querySelectorAll(".mrow").forEach((r) => r.addEventListener("click", () => {
    document.querySelectorAll(".mrow").forEach((x) => x.classList.toggle("on", x === r));
    say(r.querySelector("strong").textContent + " — switch it for real in the dashboard");
  }));

  // ── Pill hold-to-talk demo ──
  const body = document.getElementById("pillBody");
  const title = document.getElementById("pillTitle");
  const sub = document.getElementById("pillSub");
  const timerEl = document.getElementById("pillTimer");
  const typedEl = document.getElementById("typed");
  const holdBtn = document.getElementById("holdBtn");
  const bars = [...document.querySelectorAll("#wave i")];
  const FULL = "Meet at 6 PM tomorrow, theek hai?";
  let tickInt = null, typeInt = null, t0 = 0, holding = false;

  function setState(s, t, st) { body.dataset.state = s; title.textContent = t; sub.textContent = st; }
  function tick() {
    const s = Math.floor((Date.now() - t0) / 1000);
    timerEl.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    bars.forEach((b, i) => {
      const v = 0.35 + Math.abs(Math.sin(Date.now() / 170 + i * 0.85)) * 0.9;
      b.style.transform = "scaleY(" + v.toFixed(2) + ")";
      b.style.height = (9 + v * 13).toFixed(0) + "px";
    });
  }
  function start(e) {
    if (e && e.cancelable) e.preventDefault();
    if (holding) return;
    holding = true;
    holdBtn.classList.add("held");
    clearInterval(typeInt); clearInterval(tickInt);
    typedEl.textContent = "";
    setState("listening", "Listening", "holding the key… speak now");
    t0 = Date.now(); timerEl.textContent = "0:00";
    tickInt = setInterval(tick, 60);
  }
  function stop() {
    if (!holding) return;
    holding = false;
    holdBtn.classList.remove("held");
    clearInterval(tickInt);
    setState("enhancing", "Enhancing", "rules pass: fillers, fixes…");
    bars.forEach((b) => { b.style.transform = "scaleY(.5)"; });
    let i = 0;
    setTimeout(() => {
      typeInt = setInterval(() => {
        typedEl.textContent = FULL.slice(0, ++i);
        if (i >= FULL.length) {
          clearInterval(typeInt);
          setState("done", "Pasted ✓", "at your cursor — chime");
          setTimeout(() => {
            if (!holding && body.dataset.state === "done") {
              setState("listening", "Listening", "hold H or the button to replay");
              typedEl.textContent = ""; timerEl.textContent = "0:00";
            }
          }, 2800);
        }
      }, 32);
    }, 850);
  }
  holdBtn.addEventListener("pointerdown", start);
  window.addEventListener("pointerup", stop);
  holdBtn.addEventListener("pointercancel", stop);
  window.addEventListener("keydown", (e) => {
    if ((e.key === "h" || e.key === "H") && !e.repeat && !e.metaKey && !e.ctrlKey && document.activeElement.tagName !== "INPUT") start(e);
  });
  window.addEventListener("keyup", (e) => { if (e.key === "h" || e.key === "H") stop(); });

  // ── Fluid orb (same shader family as the real pill.html) ──
  (function orb() {
    const cv = document.getElementById("orb");
    if (!cv) return;
    const gl = cv.getContext("webgl", { alpha: true, antialias: true });
    const S = 132;
    cv.width = cv.height = S;
    if (!gl) { cv.parentElement.style.background = "radial-gradient(circle at 35% 30%,#fff,#8b7cff)"; return; }
    const V = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
    const F = "precision mediump float;uniform vec2 r;uniform float t;uniform vec3 c;"
      + "float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5);}"
      + "float n(vec2 p){vec2 i=floor(p),f=fract(p);vec2 u=f*f*(3.-2.*f);"
      + "return mix(mix(h(i),h(i+vec2(1,0)),u.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),u.x),u.y);}"
      + "void main(){vec2 uv=gl_FragCoord.xy/r;float tt=t*.4;"
      + "vec2 d=vec2(sin(tt)+.6*sin(tt*1.7+1.3),cos(tt*.8)+.6*cos(tt*1.3+2.1));"
      + "vec2 p=vec2(uv.x*1.8,uv.y)+d*.7;"
      + "float f=n(p+d)+n(p+vec2(3.2,1.5)-d);"
      + "float g=clamp(1.-uv.y,0.,1.);"
      + "float s=clamp(g+(f-1.)*.4*smoothstep(0.,.3,uv.y),0.,1.);"
      + "vec3 col=mix(vec3(.99,1.,1.),mix(vec3(.99,1.,1.),c,.5),smoothstep(.28,.52,s));"
      + "col=mix(col,c,smoothstep(.58,.88,s));"
      + "float e=smoothstep(.5,.485,distance(uv,vec2(.5)));"
      + "gl_FragColor=vec4(col*e,e);}";
    function sh(t, s) { const o = gl.createShader(t); gl.shaderSource(o, s); gl.compileShader(o); return o; }
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, V));
    gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, F));
    gl.linkProgram(pr); gl.useProgram(pr);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, "p");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.viewport(0, 0, S, S);
    const uR = gl.getUniformLocation(pr, "r"),
          uT = gl.getUniformLocation(pr, "t"),
          uC = gl.getUniformLocation(pr, "c");
    gl.uniform2f(uR, S, S);
    function hex(h) { const n = parseInt(h.slice(1), 16); return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255]; }
    const cols = { listening: hex("#5ea8ff"), enhancing: hex("#b087ff"), done: hex("#3ecf8e") };
    let cur = cols.listening.slice();
    const t0o = performance.now();
    (function frame(now) {
      const tgt = cols[body.dataset.state] || cols.listening;
      for (let i = 0; i < 3; i++) cur[i] += (tgt[i] - cur[i]) * 0.06;
      gl.uniform3f(uC, cur[0], cur[1], cur[2]);
      gl.uniform1f(uT, (now - t0o) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      requestAnimationFrame(frame);
    })(performance.now());
  })();
})();
