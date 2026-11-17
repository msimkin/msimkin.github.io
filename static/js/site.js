/* univariate.org: the name in the hero (optical sizing + a short decrypt), math rendering, and
   keeping punctuation glued to inline math. No dependencies besides KaTeX on pages with math. */
(function () {
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var root = document.documentElement;
  var name = document.querySelector('#intro .name');

  /* measure the face so the light name sits optically flush with both container edges */
  function measure() {
    var fam = getComputedStyle(root).getPropertyValue('--mono');
    var c = document.createElement('canvas').getContext('2d'); c.font = '300 1000px ' + fam;
    var adv = c.measureText('MMMMMMMMMM').width / 10000;
    if (!(adv > .4 && adv < .8)) return;
    var M = c.measureText('M'), lsb = -M.actualBoundingBoxLeft / 1000, cap = M.actualBoundingBoxAscent / 1000;
    root.style.setProperty('--adv', adv.toFixed(4));
    if (lsb > -.01 && lsb < .2) root.style.setProperty('--lsb', lsb.toFixed(4));
    if (cap > .5 && cap < .9) root.style.setProperty('--cap', cap.toFixed(4));
  }
  if (name) { if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure); else measure(); }

  /* the name decrypts once, in 280ms */
  var GLYPHS = '0123456789abcdef#$%&*+<>=~';
  function decrypt(el, dur) {
    var text = el.getAttribute('data-text') || el.textContent, n = text.length, t0 = null;
    el.setAttribute('aria-label', text);
    function frame(now) {
      if (t0 === null) t0 = now;
      var p = (now - t0) / dur, out = '';
      for (var i = 0; i < n; i++) {
        var at = (i / n) * .7 + .3 * ((i * 7919) % 13) / 13;
        out += (text[i] === ' ' || p >= at) ? text[i] : GLYPHS[(Math.random() * GLYPHS.length) | 0];
      }
      el.textContent = out;
      if (p < 1) requestAnimationFrame(frame); else el.textContent = text;
    }
    requestAnimationFrame(frame);
  }
  if (name && !reduce) decrypt(name, 280);
})();

/* math: KaTeX auto-render (loaded only on pages that contain math), then glue punctuation */
window.univariateMath = function () {
  if (!window.renderMathInElement) return;
  window.renderMathInElement(document.body, {
    delimiters: [{ left: '\\[', right: '\\]', display: true }, { left: '\\(', right: '\\)', display: false }],
    throwOnError: false
  });
  [].forEach.call(document.querySelectorAll('.katex'), function (k) {
    if (k.closest('.katex-display') || k.closest('.mg')) return;
    var h = (k.parentNode.childNodes.length === 1 && k.parentNode.tagName === 'SPAN') ? k.parentNode : k;
    var n = h.nextSibling, m;
    if (!n || n.nodeType !== 3 || !(m = /^[,.;:)]+/.exec(n.data))) return;
    var w = document.createElement('span'); w.className = 'mg'; w.style.whiteSpace = 'nowrap';
    h.parentNode.insertBefore(w, h); w.appendChild(h); n.data = n.data.slice(m[0].length); w.appendChild(document.createTextNode(m[0]));
  });
};
