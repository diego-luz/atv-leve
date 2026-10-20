// atvleve · painel local. Sem dependências e sem rede.
(function () {
  "use strict";
  document.documentElement.classList.remove("no-js");
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Fundo: fios de luz que saem da borda esquerda, se abrem e ondulam em direção ao ponteiro.
  var canvas = document.getElementById("flow");
  if (canvas && canvas.getContext) {
    var ctx = canvas.getContext("2d");
    var N = 45, w = 0, h = 0, dpr = 1, start = performance.now(), raf = 0;
    var pointer = { x: -1e4, y: -1e4, tx: -1e4, ty: -1e4 };
    var colors = [[13, 153, 166], [26, 217, 191], [51, 242, 217]]; // deep aqua, teal, bright cyan
    var strands = [];
    for (var i = 0; i < N; i++) {
      var c = colors[i % 3];
      strands.push({
        spread: (i / (N - 1)) * 2 - 1,
        phase: Math.random() * Math.PI * 2,
        freq: 0.0016 + Math.random() * 0.0018,
        amp: 18 + Math.random() * 42,
        speed: 0.00018 + Math.random() * 0.00032,
        alpha: 0.05 + Math.random() * 0.16,
        width: Math.random() < 0.15 ? 1.4 : 0.8,
        rgb: c
      });
    }
    var resize = function () {
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    var ease = function (t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; };
    var draw = function (now) {
      var intro = reduce ? 1 : ease(Math.min(1, (now - start) / 3500));
      pointer.x += (pointer.tx - pointer.x) * 0.06; pointer.y += (pointer.ty - pointer.y) * 0.06;
      ctx.clearRect(0, 0, w, h);
      var originY = h * 0.42, reach = w * (0.15 + 0.85 * intro);
      for (var s = 0; s < N; s++) {
        var st = strands[s], t0 = reduce ? 0 : now * st.speed;
        var grad = ctx.createLinearGradient(0, 0, reach, 0);
        grad.addColorStop(0, "rgba(" + st.rgb + ",0)");
        grad.addColorStop(0.25, "rgba(" + st.rgb + "," + st.alpha + ")");
        grad.addColorStop(1, "rgba(" + st.rgb + "," + st.alpha * 0.35 + ")");
        ctx.strokeStyle = grad; ctx.lineWidth = st.width;
        ctx.beginPath();
        for (var x = 0; x <= reach; x += 18) {
          var k = x / Math.max(w, 1);
          var y = originY + st.spread * h * 0.62 * Math.pow(k, 0.9)
                + Math.sin(x * st.freq + st.phase + t0) * st.amp * k
                + Math.sin(x * st.freq * 0.37 + t0 * 1.7 + s) * 10 * k;
          var dx = x - pointer.x, dy = y - pointer.y, d2 = dx * dx + dy * dy;
          if (d2 < 90000) y += (dy >= 0 ? 1 : -1) * 38 * (1 - d2 / 90000);
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
      if (!reduce) raf = requestAnimationFrame(draw);
    };
    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", function (e) { pointer.tx = e.clientX; pointer.ty = e.clientY; }, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) cancelAnimationFrame(raf); else if (!reduce) raf = requestAnimationFrame(draw);
    });
    raf = requestAnimationFrame(draw);
  }

  // Carregando: toda ação demora alguns segundos por causa do adb.
  var loader = document.querySelector(".loader");
  document.querySelectorAll("form[data-loading]").forEach(function (f) {
    f.addEventListener("submit", function () {
      if (!loader) return;
      loader.querySelector(".loader-text").textContent = f.dataset.loading;
      document.body.classList.add("loading");
      var n = 0, out = loader.querySelector(".core b span");
      var timer = setInterval(function () {
        n += Math.max(0.4, (96 - n) * 0.04);
        out.textContent = String(Math.min(96, Math.round(n))).padStart(2, "0");
      }, 120);
      window.addEventListener("pagehide", function () { clearInterval(timer); });
    });
  });
  window.addEventListener("pageshow", function () { document.body.classList.remove("loading"); });

  // Copiar comandos.
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var old = b.textContent;
      if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.copy).then(function () {
        b.textContent = "Copiado"; setTimeout(function () { b.textContent = old; }, 1400);
      }, function () {});
    });
  });

  // Listas de apps em diálogos.
  document.querySelectorAll("[data-dialog]").forEach(function (b) {
    b.addEventListener("click", function () { document.getElementById(b.dataset.dialog).showModal(); });
  });
  document.querySelectorAll("dialog [data-close]").forEach(function (b) {
    b.addEventListener("click", function () { b.closest("dialog").close(); });
  });
  document.querySelectorAll("dialog").forEach(function (d) {
    d.addEventListener("click", function (e) { if (e.target === d) d.close(); });
  });

  // Aparecer ao rolar.
  var items = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduce) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    items.forEach(function (el, i) { el.style.transitionDelay = (i % 4) * 0.08 + "s"; io.observe(el); });
  } else {
    items.forEach(function (el) { el.classList.add("in"); });
  }

  // Tema: a prévia acompanha a cor escolhida (amostra ou seletor livre).
  var preview = document.querySelector("[data-preview]");
  if (preview) {
    var custom = document.querySelector("[data-custom]");
    var show = function (color) { preview.style.setProperty("--bg", color); };
    document.querySelectorAll("input[name=color]").forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (custom) { custom.value = radio.value; custom.removeAttribute("name"); }
        show(radio.value);
      });
    });
    if (custom) {
      custom.addEventListener("input", function () {
        document.querySelectorAll("input[name=color]").forEach(function (r) { r.checked = false; });
        custom.setAttribute("name", "custom");  // só é enviado depois de usar o seletor
        custom.closest(".choice").querySelector(".chip-color").style.background = custom.value;
        show(custom.value);
      });
    }
  }
})();
