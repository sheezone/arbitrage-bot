// Winter decoration (owner 2026-11-06): realistic snowfall in three depth layers with
// some rotating six-armed snowflakes. Pure decoration -- pointer-events: none; pauses
// while the app is hidden and is off for prefers-reduced-motion. (Snow piles, icicles
// and the iced avatar were tried and dropped by the owner.)
(function () {
  const style = document.createElement("style");
  style.textContent = "#w-snow { position: fixed; inset: 0; width: 100%; height: 100%; pointer-events: none; z-index: 19; }";
  document.head.appendChild(style);

  function snowfall() {
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const c = document.createElement("canvas");
    c.id = "w-snow";
    document.body.appendChild(c);
    const ctx = c.getContext("2d");
    let W, H, dpr;
    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = c.width = innerWidth * dpr;
      H = c.height = innerHeight * dpr;
    };
    resize();
    addEventListener("resize", resize);
    // soft round flake sprite, and a six-armed crystal for some of the mid layer
    const soft = document.createElement("canvas");
    soft.width = soft.height = 64;
    const sg = soft.getContext("2d");
    const g = sg.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,255,255,1)"); g.addColorStop(0.35, "rgba(255,255,255,0.85)");
    g.addColorStop(0.7, "rgba(230,242,255,0.25)"); g.addColorStop(1, "rgba(230,242,255,0)");
    sg.fillStyle = g; sg.fillRect(0, 0, 64, 64);
    const crystal = document.createElement("canvas");
    crystal.width = crystal.height = 64;
    const cg = crystal.getContext("2d");
    cg.translate(32, 32); cg.strokeStyle = "rgba(255,255,255,0.95)"; cg.lineCap = "round";
    cg.shadowColor = "rgba(200,230,255,0.9)"; cg.shadowBlur = 4;
    for (let i = 0; i < 6; i++) {
      cg.rotate(Math.PI / 3);
      cg.lineWidth = 3; cg.beginPath(); cg.moveTo(0, 0); cg.lineTo(0, -26); cg.stroke();
      cg.lineWidth = 2; cg.beginPath(); cg.moveTo(0, -14); cg.lineTo(-7, -21); cg.moveTo(0, -14); cg.lineTo(7, -21); cg.stroke();
    }
    // three depth layers: far (tiny, slow, faint), mid, near (big, fast, soft)
    const layers = [
      { n: 0.55, r: [0.6, 1.3], v: [0.00025, 0.0004], o: [0.3, 0.55], crystal: 0 },
      { n: 0.35, r: [1.5, 2.8], v: [0.0005, 0.0008], o: [0.55, 0.85], crystal: 0.3 },
      { n: 0.1, r: [3.5, 5.5], v: [0.0011, 0.0016], o: [0.35, 0.6], crystal: 0 },
    ];
    const total = Math.round(Math.min(110, innerWidth / 3.6));
    const lerp = ([a, b]) => a + Math.random() * (b - a);
    const flakes = [];
    layers.forEach((L, li) => {
      for (let i = 0; i < Math.round(total * L.n); i++) {
        flakes.push({ li, x: Math.random(), y: Math.random(), r: lerp(L.r), v: lerp(L.v), o: lerp(L.o),
          sway: Math.random() * 6.28, sw: 0.006 + Math.random() * 0.012, rot: Math.random() * 6.28,
          spin: (Math.random() - 0.5) * 0.03, crystal: Math.random() < L.crystal });
      }
    });
    let last = performance.now(), wind = 0, windT = 0;
    const frame = (t) => {
      const dt = Math.min(50, t - last) / 16.7;
      last = t;
      if (!document.hidden) {
        windT += 0.004 * dt;
        wind = Math.sin(windT) * 0.00035 + Math.sin(windT * 2.7) * 0.00015;  // slow gusts
        ctx.clearRect(0, 0, W, H);
        for (const f of flakes) {
          const depthK = 0.5 + f.li * 0.5;
          f.y += f.v * dt;
          f.sway += 0.018 * dt;
          f.x += wind * dt * depthK;
          f.rot += f.spin * dt;
          if (f.y > 1.03) { f.y = -0.03; f.x = Math.random(); }
          if (f.x > 1.03) f.x = -0.03; else if (f.x < -0.03) f.x = 1.03;
          const x = (f.x + Math.sin(f.sway) * f.sw) * W, y = f.y * H, s = f.r * dpr * (f.crystal ? 3.2 : 2.4);
          ctx.globalAlpha = f.o;
          if (f.crystal) {
            ctx.save(); ctx.translate(x, y); ctx.rotate(f.rot);
            ctx.drawImage(crystal, -s / 2, -s / 2, s, s);
            ctx.restore();
          } else {
            ctx.drawImage(soft, x - s / 2, y - s / 2, s, s);
          }
        }
        ctx.globalAlpha = 1;
      }
      requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", snowfall);
  else snowfall();
})();
