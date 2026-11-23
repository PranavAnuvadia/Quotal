// Quotal download page — theme toggle, dynamic releases, pill demo, smart enhancer playground
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  const yr = document.getElementById("yr");
  if (yr) yr.textContent = new Date().getFullYear();

  // ── 1. Dark / Light Theme Toggle ──
  const themeToggle = document.getElementById("themeToggle");
  const savedTheme = localStorage.getItem("quotal_web_theme") ||
    (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  
  function applyTheme(th) {
    document.documentElement.setAttribute("data-theme", th);
    localStorage.setItem("quotal_web_theme", th);
  }
  applyTheme(savedTheme);

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") || "dark";
      const next = cur === "dark" ? "light" : "dark";
      applyTheme(next);
      say(next === "light" ? "Paper light mode" : "Obsidian dark mode");
    });
  }

  // ── 2. Toast Notifications ──
  const toast = document.getElementById("toast");
  let toastTimer;
  function say(msg) {
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 1800);
  }

  // ── 3. Mobile Nav ──
  const burger = document.getElementById("burger");
  const mnav = document.getElementById("mnav");
  if (burger && mnav) {
    burger.addEventListener("click", () => {
      const open = mnav.classList.toggle("open");
      burger.setAttribute("aria-expanded", open ? "true" : "false");
      burger.textContent = open ? "×" : "—";
    });
    mnav.querySelectorAll("a").forEach((a) => a.addEventListener("click", () => {
      mnav.classList.remove("open");
      burger.textContent = "—";
    }));
  }

  // ── 4. Copy to Clipboard ──
  document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.copy);
      say("Copied command to clipboard");
    } catch {
      say("Select text manually");
    }
  }));

  // ── 5. Scroll Reveal ──
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (e.isIntersecting) {
        e.target.classList.add("in");
        io.unobserve(e.target);
      }
    }), { threshold: 0.1 });
    document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
  }

  // ── 6. Setup Tabs (Installer vs Source) ──
  const setTabs = [...document.querySelectorAll(".set-tab")];
  const paneInstaller = document.getElementById("pane-installer");
  const paneSource = document.getElementById("pane-source");
  setTabs.forEach((b) => b.addEventListener("click", () => {
    setTabs.forEach((x) => x.classList.toggle("on", x === b));
    const target = b.dataset.settab;
    if (paneInstaller) paneInstaller.classList.toggle("on", target === "installer");
    if (paneSource) paneSource.classList.toggle("on", target === "source");
  }));

  // ── 7. Enhancer Mode Tabs ──
  const tabBtns = [...document.querySelectorAll("[data-tab]")];
  const panes = [...document.querySelectorAll("[data-pane]")];
  tabBtns.forEach((b) => b.addEventListener("click", () => {
    tabBtns.forEach((x) => {
      x.classList.toggle("on", x === b);
      x.setAttribute("aria-selected", x === b ? "true" : "false");
    });
    panes.forEach((p) => p.classList.toggle("on", p.dataset.pane === b.dataset.tab));
  }));

  // ── 8. Model Rows Visual Selection ──
  document.querySelectorAll(".mrow").forEach((r) => r.addEventListener("click", () => {
    document.querySelectorAll(".mrow").forEach((x) => x.classList.toggle("on", x === r));
    const title = r.querySelector("strong")?.textContent || "Model";
    say(`${title} selected — swap models live anytime in the tray dashboard`);
  }));

  // ── 9. Hero Pill Simulator & Style Switcher ──
  const ptBtns = [...document.querySelectorAll(".pt-switch button")];
  const pillBodyEl = document.getElementById("pillBody");
  const currentPillNameEl = document.getElementById("currentPillName");

  const PILL_NAMES = {
    quotal: "Quotal Brand",
    wispr: "Wispr Minimal",
    bloom: "Bloom Living Orb",
    nothing: "Nothing (1) LED",
    mercury: "Mercury Chrome",
    orbit: "Orbit Celestial",
    aurora: "Aurora Rainbow"
  };

  function setPillStyle(key) {
    if (!pillBodyEl) return;
    pillBodyEl.dataset.ptheme = key;
    ptBtns.forEach((b) => b.classList.toggle("on", b.dataset.pt === key));
    if (currentPillNameEl) currentPillNameEl.textContent = PILL_NAMES[key] || "Quotal";
  }

  ptBtns.forEach((b) => b.addEventListener("click", () => {
    setPillStyle(b.dataset.pt);
    say(`Pill switched to ${PILL_NAMES[b.dataset.pt] || b.dataset.pt}`);
  }));

  // Hold-to-dictate animation
  const title = document.getElementById("pillTitle");
  const sub = document.getElementById("pillSub");
  const timerEl = document.getElementById("pillTimer");
  const typedEl = document.getElementById("typed");
  const holdBtn = document.getElementById("holdBtn");
  const bars = [...document.querySelectorAll("#wave i")];
  const FULL_PHRASE = "Meet at 6 PM tomorrow, theek hai?";
  let tickInt = null, typeInt = null, t0 = 0, holding = false;

  function setPillState(s, t, st) {
    if (!pillBodyEl) return;
    pillBodyEl.dataset.state = s;
    if (title) title.textContent = t;
    if (sub) sub.textContent = st;
  }

  function tick() {
    const s = Math.floor((Date.now() - t0) / 1000);
    if (timerEl) timerEl.textContent = Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
    bars.forEach((b, i) => {
      const v = 0.35 + Math.abs(Math.sin(Date.now() / 170 + i * 0.85)) * 0.9;
      b.style.transform = "scaleY(" + v.toFixed(2) + ")";
      b.style.height = (9 + v * 13).toFixed(0) + "px";
    });
  }

  function startDictation(e) {
    if (e && e.cancelable) e.preventDefault();
    if (holding) return;
    holding = true;
    if (holdBtn) holdBtn.classList.add("held");
    clearInterval(typeInt);
    clearInterval(tickInt);
    if (typedEl) typedEl.textContent = "";
    setPillState("listening", "Listening", "holding the key… speak now");
    t0 = Date.now();
    if (timerEl) timerEl.textContent = "0:00";
    tickInt = setInterval(tick, 60);
  }

  function stopDictation() {
    if (!holding) return;
    holding = false;
    if (holdBtn) holdBtn.classList.remove("held");
    clearInterval(tickInt);
    setPillState("enhancing", "Enhancing", "self-correction pass: fillers & rules…");
    bars.forEach((b) => { b.style.transform = "scaleY(.5)"; });

    let i = 0;
    setTimeout(() => {
      typeInt = setInterval(() => {
        if (typedEl) typedEl.textContent = FULL_PHRASE.slice(0, ++i);
        if (i >= FULL_PHRASE.length) {
          clearInterval(typeInt);
          setPillState("done", "Pasted ✓", "at your cursor — chime");
          setTimeout(() => {
            if (!holding && pillBodyEl && pillBodyEl.dataset.state === "done") {
              setPillState("listening", "Listening", "hold H or the button to replay");
              if (typedEl) typedEl.textContent = "";
              if (timerEl) timerEl.textContent = "0:00";
            }
          }, 3200);
        }
      }, 28);
    }, 700);
  }

  if (holdBtn) {
    holdBtn.addEventListener("pointerdown", startDictation);
    window.addEventListener("pointerup", stopDictation);
    holdBtn.addEventListener("pointercancel", stopDictation);
  }

  window.addEventListener("keydown", (e) => {
    if ((e.key === "h" || e.key === "H") && !e.repeat && !e.metaKey && !e.ctrlKey &&
        document.activeElement.tagName !== "INPUT" &&
        document.activeElement.id !== "playInput") {
      startDictation(e);
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.key === "h" || e.key === "H") stopDictation();
  });

  // ── 11. Bloom Fluid Shader (WebGL Canvas) ──
  (function initOrb() {
    const cv = document.getElementById("orb");
    if (!cv) return;
    const gl = cv.getContext("webgl", { alpha: true, antialias: true });
    const S = 132;
    cv.width = cv.height = S;
    if (!gl) {
      if (cv.parentElement) cv.parentElement.style.background = "radial-gradient(circle at 35% 30%,#fff,#ff5d1f)";
      return;
    }
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

    function sh(type, src) {
      const o = gl.createShader(type);
      gl.shaderSource(o, src);
      gl.compileShader(o);
      return o;
    }
    const pr = gl.createProgram();
    gl.attachShader(pr, sh(gl.VERTEX_SHADER, V));
    gl.attachShader(pr, sh(gl.FRAGMENT_SHADER, F));
    gl.linkProgram(pr);
    gl.useProgram(pr);

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

    function hex(h) {
      const n = parseInt(h.slice(1), 16);
      return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
    }
    const cols = { listening: hex("#5ea8ff"), enhancing: hex("#b087ff"), done: hex("#3ecf8e") };
    let cur = cols.listening.slice();
    const t0o = performance.now();

    (function frame(now) {
      const state = pillBodyEl ? pillBodyEl.dataset.state : "listening";
      const tgt = cols[state] || cols.listening;
      for (let i = 0; i < 3; i++) cur[i] += (tgt[i] - cur[i]) * 0.06;
      gl.uniform3f(uC, cur[0], cur[1], cur[2]);
      gl.uniform1f(uT, (now - t0o) / 1000);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      requestAnimationFrame(frame);
    })(performance.now());
  })();

  // ── 12. GitHub Latest Release Dynamic Link ──
  async function fetchLatestRelease() {
    try {
      const res = await fetch("https://api.github.com/repos/PranavAnuvadia/Quotal/releases/latest", {
        headers: { "Accept": "application/vnd.github+json" }
      });
      if (!res.ok) return;
      const data = await res.json();
      const tag = data.tag_name || "v1.0.2";
      const assets = data.assets || [];
      
      let exeUrl = null;
      let exeSize = null;
      for (const a of assets) {
        if (a.name === "QuotalSetup.exe") {
          exeUrl = a.browser_download_url;
          if (a.size) exeSize = Math.round(a.size / 1048576) + " MB";
          break;
        }
      }

      if (exeUrl) {
        document.querySelectorAll("[data-dl]").forEach((btn) => {
          btn.href = exeUrl;
          const badge = btn.querySelector(".btn-badge");
          if (badge) badge.textContent = tag;
        });

        const heroBtn = document.getElementById("hero-dl-btn");
        if (heroBtn) {
          const sub = heroBtn.querySelector(".dl-tag-sub");
          if (sub && exeSize) sub.textContent = `QuotalSetup.exe (${tag} · ${exeSize})`;
        }
      }
    } catch {
      // Graceful fallback to default GitHub release page
    }
  }
  fetchLatestRelease();

})();