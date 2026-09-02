/*
 * ui.js -- small DOM helpers and the reusable widgets the panels are built
 * from: a virtualised data grid (node blocks run to six figures of rows), a
 * key/value parameter editor, modals and toasts.
 */
(function (global) {
  'use strict';

  var doc = global.document;

  /* el('div.klass#id', {attrs}, [children | text]) */
  function el(spec, attrs, children) {
    var m = /^([a-zA-Z0-9-]+)?((?:[.#][^.#]+)*)$/.exec(spec) || [];
    var tag = m[1] || 'div';
    var node = doc.createElement(tag);
    var rest = m[2] || '';
    rest.replace(/([.#])([^.#]+)/g, function (_, sym, val) {
      if (sym === '.') node.classList.add(val);
      else node.id = val;
      return '';
    });
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === 'text') node.textContent = v;
        else if (k === 'html') node.innerHTML = v;
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') {
          node.addEventListener(k.slice(2).toLowerCase(), v);
        } else if (k === 'value') node.value = v;
        else node.setAttribute(k, v === true ? '' : v);
      });
    }
    append(node, children);
    return node;
  }

  function append(node, children) {
    if (children === null || children === undefined) return node;
    if (Array.isArray(children)) {
      children.forEach(function (c) { append(node, c); });
      return node;
    }
    if (typeof children === 'string' || typeof children === 'number') {
      node.appendChild(doc.createTextNode(String(children)));
      return node;
    }
    if (children instanceof global.Node) node.appendChild(children);
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  function fmt(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  /* ------------------------------------------------------- virtual table */

  /*
   * Windowed grid. Only the rows in view exist in the DOM, so a 108,000-row
   * node block scrolls as smoothly as a 10-row one.
   *
   * opts: { columns:[{label,width,align}], rowCount, getRow(i)->[strings],
   *         editable(i,col)->bool, onEdit(i,col,value), rowLabel }
   */
  function VirtualTable(opts) {
    this.opts = opts;
    this.rowHeight = opts.rowHeight || 26;
    this.overscan = 8;
    this.filtered = null;

    this.head = el('.vt-head');
    this.viewport = el('.vt-viewport');
    this.spacer = el('.vt-spacer');
    this.body = el('.vt-body');
    this.viewport.appendChild(this.spacer);
    this.viewport.appendChild(this.body);
    this.root = el('.vt', null, [this.head, this.viewport]);

    var self = this;
    this.viewport.addEventListener('scroll', function () { self.render(); });
    this._buildHead();
    this.setRowCount(opts.rowCount);
  }

  VirtualTable.prototype._buildHead = function () {
    var self = this;
    clear(this.head);
    var row = el('.vt-row.vt-header');
    this.opts.columns.forEach(function (c) {
      row.appendChild(el('.vt-cell', {
        style: 'width:' + (c.width || 110) + 'px;text-align:' + (c.align || 'right'),
        text: c.label
      }));
    });
    this.head.appendChild(row);
    this._width = this.opts.columns.reduce(function (a, c) { return a + (c.width || 110); }, 0);
    this.head.style.minWidth = this._width + 'px';
  };

  VirtualTable.prototype.setRowCount = function (n) {
    this.rowCount = n;
    this.spacer.style.height = (n * this.rowHeight) + 'px';
    this.render();
  };

  VirtualTable.prototype.setFilter = function (indices) {
    this.filtered = indices;
    this.setRowCount(indices ? indices.length : this.opts.rowCount);
    this.viewport.scrollTop = 0;
  };

  VirtualTable.prototype.sourceIndex = function (visualIndex) {
    return this.filtered ? this.filtered[visualIndex] : visualIndex;
  };

  VirtualTable.prototype.scrollToRow = function (visualIndex) {
    this.viewport.scrollTop = Math.max(0, (visualIndex - 3) * this.rowHeight);
    this.render();
  };

  VirtualTable.prototype.render = function () {
    var self = this;
    var vh = this.viewport.clientHeight || 400;
    var top = this.viewport.scrollTop;
    var first = Math.max(0, Math.floor(top / this.rowHeight) - this.overscan);
    var last = Math.min(this.rowCount, Math.ceil((top + vh) / this.rowHeight) + this.overscan);

    clear(this.body);
    this.body.style.transform = 'translateY(' + (first * this.rowHeight) + 'px)';
    this.body.style.minWidth = this._width + 'px';

    for (var v = first; v < last; v++) {
      var si = this.sourceIndex(v);
      var vals = this.opts.getRow(si);
      if (!vals) continue;
      var row = el('.vt-row', { 'data-row': si });
      if (this.opts.rowClass) {
        var extra = this.opts.rowClass(si);
        if (extra) row.classList.add(extra);
      }
      /* eslint-disable no-loop-func */
      (function (si, row, vals) {
        self.opts.columns.forEach(function (c, ci) {
          var editable = self.opts.editable ? self.opts.editable(si, ci) : false;
          var cell = el('.vt-cell' + (editable ? '.vt-edit' : ''), {
            style: 'width:' + (c.width || 110) + 'px;text-align:' + (c.align || 'right'),
            text: vals[ci] === undefined ? '' : vals[ci],
            contenteditable: editable ? 'true' : null,
            spellcheck: 'false'
          });
          if (editable) {
            cell.addEventListener('blur', function () {
              var nv = cell.textContent.trim();
              if (nv !== String(vals[ci] === undefined ? '' : vals[ci])) {
                self.opts.onEdit(si, ci, nv, cell);
              }
            });
            cell.addEventListener('keydown', function (ev) {
              if (ev.key === 'Enter') { ev.preventDefault(); cell.blur(); }
              if (ev.key === 'Escape') { cell.textContent = vals[ci]; cell.blur(); }
            });
          }
          row.appendChild(cell);
        });
      })(si, row, vals);
      this.body.appendChild(row);
    }
  };

  /* ----------------------------------------------------- parameter editor */

  /*
   * Editor for one keyword card's parameters. Values are free text because
   * Abaqus parameters are open ended; known ones get a datalist of suggestions.
   */
  function paramEditor(block, onChange, suggestions) {
    var P = global.InpParser;
    var wrap = el('.params');

    function rebuild() {
      clear(wrap);
      var table = el('.param-grid');
      block.params.forEach(function (p, i) {
        var keyIn = el('input.param-key', {
          value: p.key, spellcheck: 'false', 'aria-label': 'parameter name'
        });
        var valIn = el('input.param-val', {
          value: p.flag ? '' : (p.value === null ? '' : p.value),
          placeholder: p.flag ? '(flag)' : '',
          spellcheck: 'false', 'aria-label': 'parameter value'
        });
        keyIn.addEventListener('change', function () {
          p.key = keyIn.value.trim();
          commit();
        });
        valIn.addEventListener('change', function () {
          var v = valIn.value;
          if (v === '') { p.flag = true; p.value = null; }
          else { p.flag = false; p.value = v; }
          commit();
        });
        var del = el('button.icon-btn', {
          title: 'Remove parameter', text: '×',
          onclick: function () { block.params.splice(i, 1); commit(); rebuild(); }
        });
        table.appendChild(el('.param-row', null, [keyIn, el('span.eq', { text: '=' }), valIn, del]));
      });
      wrap.appendChild(table);
      wrap.appendChild(el('button.add-btn', {
        text: '+ parameter',
        onclick: function () {
          block.params.push({ key: '', value: '', flag: false });
          commit();
          rebuild();
        }
      }));
    }

    function commit() {
      block.dirty = true;
      if (onChange) onChange(block);
    }

    rebuild();
    return wrap;
  }

  /* --------------------------------------------------------------- modals */

  function modal(title, contentNode, actions) {
    var backdrop = el('.modal-backdrop');
    var box = el('.modal', null, [
      el('.modal-head', null, [
        el('h3', { text: title }),
        el('button.icon-btn', { text: '×', title: 'Close', onclick: close })
      ]),
      el('.modal-body', null, contentNode),
      el('.modal-foot', null, (actions || []).map(function (a) {
        return el('button' + (a.primary ? '.btn.primary' : '.btn'), {
          text: a.label,
          onclick: function () { if (a.onClick) a.onClick(close); }
        });
      }))
    ]);
    backdrop.appendChild(box);
    backdrop.addEventListener('mousedown', function (e) {
      if (e.target === backdrop) close();
    });
    doc.body.appendChild(backdrop);
    function close() {
      if (backdrop.parentNode) backdrop.parentNode.removeChild(backdrop);
    }
    return { close: close, node: box };
  }

  var toastHost = null;
  function toast(message, kind) {
    if (!toastHost) {
      toastHost = el('.toast-host');
      doc.body.appendChild(toastHost);
    }
    var t = el('.toast' + (kind ? '.' + kind : ''), { text: message });
    toastHost.appendChild(t);
    global.setTimeout(function () {
      t.classList.add('out');
      global.setTimeout(function () {
        if (t.parentNode) t.parentNode.removeChild(t);
      }, 300);
    }, kind === 'error' ? 5200 : 2600);
  }

  /* Section header with an optional set of action buttons. */
  function sectionHeader(title, subtitle, actions) {
    return el('.section-head', null, [
      el('.section-titles', null, [
        el('h2', { text: title }),
        subtitle ? el('p.subtitle', { text: subtitle }) : null
      ]),
      el('.section-actions', null, (actions || []).map(function (a) {
        return el('button.btn' + (a.primary ? '.primary' : ''), {
          text: a.label, title: a.title || a.label, onclick: a.onClick
        });
      }))
    ]);
  }

  function statGrid(items) {
    return el('.stat-grid', null, items.map(function (s) {
      return el('.stat', null, [
        el('.stat-value', { text: typeof s.value === 'number' ? fmt(s.value) : s.value }),
        el('.stat-label', { text: s.label })
      ]);
    }));
  }

  function kvTable(rows) {
    return el('table.kv', null, [
      el('tbody', null, rows.map(function (r) {
        return el('tr', null, [
          el('th', { text: r[0] }),
          el('td', null, (r[1] instanceof global.Node) ? r[1] : String(r[1]))
        ]);
      }))
    ]);
  }

  function chip(text, cls) {
    return el('span.chip' + (cls ? '.' + cls : ''), { text: text });
  }

  global.InpUI = {
    el: el,
    clear: clear,
    append: append,
    fmt: fmt,
    VirtualTable: VirtualTable,
    paramEditor: paramEditor,
    modal: modal,
    toast: toast,
    sectionHeader: sectionHeader,
    statGrid: statGrid,
    kvTable: kvTable,
    chip: chip
  };
})(typeof window !== 'undefined' ? window : globalThis);
