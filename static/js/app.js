/* UR X HOSTING — shared behaviour. Light, passive, no polling. */
(function () {
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  // opening curtain: played once per tab session
  try { sessionStorage.setItem('urh_seen', '1'); } catch (e) {}
  setTimeout(() => { const b = $('.boot'); if (b) b.remove(); document.documentElement.classList.add('seen'); }, 1600);

  // count-up numbers
  $$('[data-count]').forEach(el => {
    const to = parseFloat(el.dataset.count) || 0;
    if (reduce || to === 0) { el.textContent = to; return; }
    const t0 = performance.now(), dur = 800;
    (function tick(t) {
      const p = Math.min(1, (t - t0) / dur);
      el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(tick);
    })(t0);
  });

  // storage meters (scaleX only)
  $$('.meter[data-v]').forEach(m => {
    const v = Math.max(0, Math.min(100, parseFloat(m.dataset.v) || 0));
    m.dataset.lvl = v > 85 ? 'bad' : v > 65 ? 'warn' : '';
    requestAnimationFrame(() => requestAnimationFrame(() => { const f = $('.fill', m); if (f) f.style.transform = 'scaleX(' + v / 100 + ')'; }));
  });

  // flash banners fade out
  $$('.flash').forEach((f, i) => {
    if (f.closest('.lcard')) return;
    setTimeout(() => { f.classList.add('gone'); setTimeout(() => f.remove(), 600); }, 5500 + i * 500);
  });

  // drag & drop upload zone
  const drop = $('.drop');
  if (drop) {
    const inp = $('input[type=file]', drop), out = $('#picked');
    const show = () => {
      const f = inp.files && inp.files[0];
      if (out) out.textContent = f ? f.name + '  ·  ' + (f.size > 1048576 ? (f.size / 1048576).toFixed(2) + ' MB' : (f.size / 1024).toFixed(1) + ' KB') : '';
    };
    inp.addEventListener('change', show);
    ['dragenter', 'dragover'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', ev => { inp.files = ev.dataTransfer.files; show(); });
  }

  // search sites (dashboard)
  const q = $('#q'), rows = $$('.prow');
  if (q && rows.length) {
    q.addEventListener('input', () => {
      const t = q.value.trim().toLowerCase();
      let n = 0;
      rows.forEach(r => { const ok = !t || r.dataset.name.includes(t); r.hidden = !ok; if (ok) n++; });
      const nm = $('#noMatch'); if (nm) nm.hidden = n > 0;
    });
  }

  // copy live link (absolute URL)
  function toast(msg) {
    let s = $('#toasts');
    if (!s) { s = document.createElement('div'); s.id = 'toasts'; s.className = 'tstack'; document.body.appendChild(s); }
    const t = document.createElement('div'); t.className = 'toast ok'; t.textContent = msg; s.appendChild(t);
    setTimeout(() => t.classList.add('show'), 10);
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 2200);
  }
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-copy]');
    if (!b) return;
    const u = location.origin + b.dataset.copy;
    const done = () => toast('Link copied');
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(u).then(done, () => toast('Could not copy'));
    else { const t = document.createElement('textarea'); t.value = u; document.body.appendChild(t); t.select(); try { document.execCommand('copy'); done(); } catch (x) { toast('Could not copy'); } t.remove(); }
  });

  // keep the active site tab in view on small screens
  const on = $('.ptabs a.on');
  if (on) requestAnimationFrame(() => on.scrollIntoView({ inline: 'center', block: 'nearest' }));

  // tap ripple
  document.addEventListener('pointerdown', e => {
    if (reduce) return;
    const b = e.target.closest('.btn, .ib');
    if (!b || b.disabled) return;
    const r = b.getBoundingClientRect(), s = Math.max(r.width, r.height) * 2;
    const el = document.createElement('span');
    el.className = 'rip';
    el.style.cssText = 'width:' + s + 'px;height:' + s + 'px;left:' + (e.clientX - r.left - s / 2) + 'px;top:' + (e.clientY - r.top - s / 2) + 'px';
    if (getComputedStyle(b).position === 'static') b.style.position = 'relative';
    b.style.overflow = 'hidden';
    b.appendChild(el);
    setTimeout(() => el.remove(), 600);
  }, { passive: true });

  // spinner on submit (stops double-submit); uploads say "Uploading…"
  $$('form').forEach(f => f.addEventListener('submit', () => {
    const b = $('button[type=submit]', f);
    if (!b || b.classList.contains('busy')) return;
    const up = $('input[type=file]', f);
    setTimeout(() => { b.classList.add('busy'); if (up) b.lastChild.textContent = ' Uploading…'; }, 0);
  }));
  window.addEventListener('pageshow', () => $$('.btn.busy').forEach(b => b.classList.remove('busy')));
})();
