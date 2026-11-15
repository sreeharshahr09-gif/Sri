/* Runs inside the downloaded HTML report (after charts.js): brings the charts to life
 * and adds navigation. Kept separate so the report works on its own, offline. */
(function () {
  'use strict';
  const root = document.documentElement;
  const specs = JSON.parse(document.getElementById('chart-specs').textContent);
  mountSpecs(document, specs);

  // contents bar: jump to sections, highlight the one in view
  const links = [...document.querySelectorAll('.toc a[href^="#"]')];
  const targets = links.map((a) => document.getElementById(a.getAttribute('href').slice(1)));
  const spy = () => {
    let k = 0;
    targets.forEach((t, i) => { if (t && t.getBoundingClientRect().top < 120) k = i; });
    links.forEach((a, i) => a.classList.toggle('on', i === k));
  };
  addEventListener('scroll', spy, { passive: true });
  spy();
  links.forEach((a) => a.addEventListener('click', () => {
    const t = document.getElementById(a.getAttribute('href').slice(1));
    if (t) t.classList.remove('collapsed');
  }));

  // fold / unfold sections
  document.querySelectorAll('.rsec-h').forEach((h) => h.addEventListener('click', () => h.closest('.rsec').classList.toggle('collapsed')));
  const foldAll = (fold) => document.querySelectorAll('.rsec').forEach((s) => s.classList.toggle('collapsed', fold));
  document.getElementById('rExpand').onclick = () => foldAll(false);
  document.getElementById('rCollapse').onclick = () => foldAll(true);

  // light / dark
  document.getElementById('rTheme').onclick = () => {
    root.dataset.theme = root.dataset.theme === 'dark' ? 'light' : 'dark';
    redrawAll();
  };

  // printing: always light, all sections open
  let printTheme = null;
  addEventListener('beforeprint', () => {
    printTheme = root.dataset.theme;
    document.querySelectorAll('.rsec').forEach((s) => { s.dataset.was = s.classList.contains('collapsed') ? '1' : ''; });
    foldAll(false);
    if (printTheme === 'dark') { root.dataset.theme = 'light'; redrawAll(); }
  });
  addEventListener('afterprint', () => {
    document.querySelectorAll('.rsec').forEach((s) => s.classList.toggle('collapsed', s.dataset.was === '1'));
    if (printTheme === 'dark') { root.dataset.theme = 'dark'; redrawAll(); }
  });
  document.getElementById('rPrint').onclick = () => print();

  // sortable tables
  document.querySelectorAll('table.sortable').forEach((tb) => {
    const ths = [...tb.tHead.rows[0].cells];
    ths.forEach((th, i) => {
      th.title = 'Click to sort';
      th.addEventListener('click', () => {
        const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
        ths.forEach((x) => { delete x.dataset.dir; });
        th.dataset.dir = dir;
        const val = (r) => { const c = r.cells[i], v = c.dataset.v; return v != null && v !== '' ? +v : v === '' ? null : c.textContent.trim().toLowerCase(); };
        const rows = [...tb.tBodies[0].rows].sort((a, b) => {
          const x = val(a), y = val(b);
          if (x === y) return 0;
          if (x == null) return 1;
          if (y == null) return -1;
          return (x > y ? 1 : -1) * (dir === 'asc' ? 1 : -1);
        });
        rows.forEach((r) => tb.tBodies[0].appendChild(r));
      });
    });
  });

  // a tire's row: hover = highlight it in every chart, click = go to its section
  document.querySelectorAll('tr[data-name]').forEach((tr) => {
    tr.addEventListener('mouseenter', () => highlightAll(tr.dataset.name));
    tr.addEventListener('mouseleave', () => highlightAll(null));
    if (tr.dataset.target) {
      tr.addEventListener('click', () => {
        const t = document.getElementById(tr.dataset.target);
        if (t) { t.classList.remove('collapsed'); t.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
      });
    }
  });
})();
