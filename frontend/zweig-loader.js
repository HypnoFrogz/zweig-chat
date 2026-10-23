/* Zweig — загрузчик: знак «дерево серверов», по веткам по кругу бегут сообщения
   (ваш сервер → партнёр → филиал → вы). Цвета берутся из CSS-переменных
   --mk-*, поэтому одна и та же анимация работает в тёмной и светлой теме.
   API: ZweigLoader.mount(svg) → {stop()}; ZweigLoader.boot.hide()/show(). */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var ROUTES = {
    TA: 'M46 36 V62 C46 52 56 48 66 48',
    AB: 'M66 48 C56 48 46 52 46 62 V74 C46 68 38 64 30 64',
    BT: 'M30 64 C38 64 46 68 46 74 V36'
  };
  var NODES = {
    top:   { x: 34, y: 12, s: 24, rx: 7, c: 'var(--mk-top)' },
    amber: { x: 64, y: 37, s: 22, rx: 7, c: 'var(--mk-amber)' },
    bone:  { x: 14, y: 56, s: 17, rx: 5, c: 'var(--mk-bone)' }
  };
  var HOPS = [['TA', 'var(--mk-mint-pkt)', 'amber'], ['AB', 'var(--mk-amber)', 'bone'], ['BT', 'var(--mk-bone)', 'top']];
  var D = 2.4, HOP = 0.8, TR = 0.6;

  function el(tag, a, p, st) {
    var e = document.createElementNS(NS, tag);
    for (var k in a) e.setAttribute(k, a[k]);
    if (st) for (var s in st) e.style[s] = st[s];
    if (p) p.appendChild(e);
    return e;
  }
  function ease(x) { return -(Math.cos(Math.PI * x) - 1) / 2; }
  function outCubic(x) { return 1 - Math.pow(1 - x, 3); }

  function mount(svg) {
    svg.setAttribute('viewBox', '0 0 100 100');
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    var g = el('g', { transform: 'translate(50 49) scale(1.08) translate(-50 -49)' }, svg);
    var line = { fill: 'none', stroke: 'var(--mk-stem)', strokeLinecap: 'round' };
    el('path', { d: 'M46 86 V32' }, g, Object.assign({ strokeWidth: 7 }, line));
    el('path', { d: 'M46 62 C46 52 56 48 66 48' }, g, Object.assign({ strokeWidth: 7 }, line));
    el('path', { d: 'M46 74 C46 68 38 64 30 64' }, g, Object.assign({ strokeWidth: 6 }, line));
    var nodes = {};
    Object.keys(NODES).forEach(function (k) {
      var n = NODES[k];
      var ring = el('rect', {}, g, { fill: 'none', stroke: n.c, strokeWidth: 1.6, opacity: 0 });
      var r = el('rect', { x: n.x, y: n.y, width: n.s, height: n.s, rx: n.rx }, g, { fill: n.c });
      nodes[k] = { n: n, r: r, ring: ring, cx: n.x + n.s / 2, cy: n.y + n.s / 2 };
    });
    var probes = {}, lens = {};
    Object.keys(ROUTES).forEach(function (k) {
      probes[k] = el('path', { d: ROUTES[k] }, g, { fill: 'none', stroke: 'none' });
      lens[k] = probes[k].getTotalLength();
    });
    var trail = el('path', {}, g, { fill: 'none', strokeWidth: 3.2, strokeLinecap: 'round', opacity: 0 });
    var head = el('rect', { width: 10, height: 7, rx: 3 }, g, { fill: 'var(--mk-ink)', strokeWidth: 2, opacity: 0 });

    var raf = 0, t0 = performance.now(), stopped = false;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

    function frame(now) {
      if (stopped) return;
      var t = ((now - t0) / 1000) % D;
      var i = Math.floor(t / HOP), local = t - i * HOP, hop = HOPS[i];
      if (local <= TR) {
        var p = local / TR, L = lens[hop[0]], at = L * ease(p), tl = Math.min(at, 14);
        var pt = probes[hop[0]].getPointAtLength(at), a = Math.min(1, p * 8, (1 - p) * 8);
        trail.setAttribute('d', ROUTES[hop[0]]);
        trail.style.stroke = hop[1];
        trail.style.strokeDasharray = tl + ' ' + (L * 3);
        trail.style.strokeDashoffset = -(at - tl);
        trail.style.opacity = 0.55 * a;
        head.setAttribute('x', pt.x - 5); head.setAttribute('y', pt.y - 3.5);
        head.style.stroke = hop[1]; head.style.opacity = a;
      } else { trail.style.opacity = 0; head.style.opacity = 0; }
      // приход: получатель подпрыгивает, от него расходится волна
      HOPS.forEach(function (h, j) {
        var nd = nodes[h[2]], d = t - (j * HOP + TR);
        if (d < 0) d += D;
        var bump = d < 0.3 ? Math.sin(Math.PI * d / 0.3) : 0;
        var sc = 1 + 0.14 * bump;
        nd.r.setAttribute('transform', 'translate(' + nd.cx + ' ' + nd.cy + ') scale(' + sc + ') translate(' + -nd.cx + ' ' + -nd.cy + ')');
        if (d < 0.55) {
          var q = d / 0.55, grow = 2 + 8 * outCubic(q), s = nd.n.s + grow * 2;
          nd.ring.setAttribute('x', nd.cx - s / 2); nd.ring.setAttribute('y', nd.cy - s / 2);
          nd.ring.setAttribute('width', s); nd.ring.setAttribute('height', s);
          nd.ring.setAttribute('rx', nd.n.rx + grow * 0.6);
          nd.ring.style.opacity = 0.8 * (1 - q);
        } else nd.ring.style.opacity = 0;
      });
      raf = requestAnimationFrame(frame);
    }
    if (reduce) { frame(performance.now()); stopped = true; }
    else raf = requestAnimationFrame(frame);
    return { stop: function () { stopped = true; cancelAnimationFrame(raf); } };
  }

  /* Заставка при запуске: #boot-loader в index.html. */
  var boot = (function () {
    var root = null, anim = null, shownAt = performance.now();
    function ensure() {
      root = root || document.getElementById('boot-loader');
      return root;
    }
    function show() {
      if (!ensure()) return;
      root.classList.remove('hidden');
      if (!anim) anim = mount(root.querySelector('svg'));
      shownAt = performance.now();
      var host = root.querySelector('.zw-host');
      if (host) { try { host.textContent = (localStorage.getItem('ch_server') || '').replace(/^https?:\/\//, ''); } catch (_) {} }
    }
    function hide() {
      if (!ensure()) return;
      // короткий минимум, чтобы заставка не мигала на быстром соединении
      var wait = Math.max(0, 500 - (performance.now() - shownAt));
      setTimeout(function () {
        root.classList.add('hidden');
        setTimeout(function () { if (root.classList.contains('hidden') && anim) { anim.stop(); anim = null; } }, 400);
      }, wait);
    }
    return { show: show, hide: hide };
  })();

  window.ZweigLoader = { mount: mount, boot: boot };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot.show);
  else boot.show();
})();
