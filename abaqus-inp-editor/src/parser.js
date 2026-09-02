/*
 * parser.js -- Abaqus/Standard .inp keyword-file parser.
 *
 * The file is read as a flat, ordered list of "blocks". A block is one keyword
 * card (`*NODE`, `*ELEMENT, TYPE=C3D10H`, ...) together with the data lines
 * that follow it and any comment lines (`**`) that immediately precede it.
 *
 * Round-trip fidelity is the priority: every block keeps the exact source lines
 * it came from. The writer re-emits those verbatim unless the block has been
 * edited, so opening and exporting an untouched file returns it byte for byte.
 */
(function (global) {
  'use strict';

  var blockSeq = 0;

  /* Split a comma separated list, honouring "quoted, values". */
  function splitCommas(text) {
    var out = [], cur = '', quoted = false, i, ch;
    for (i = 0; i < text.length; i++) {
      ch = text.charAt(i);
      if (ch === '"') { quoted = !quoted; cur += ch; }
      else if (ch === ',' && !quoted) { out.push(cur); cur = ''; }
      else cur += ch;
    }
    out.push(cur);
    return out;
  }

  function unquote(s) {
    s = s.trim();
    if (s.length > 1 && s.charAt(0) === '"' && s.charAt(s.length - 1) === '"') {
      return s.substring(1, s.length - 1);
    }
    return s;
  }

  /* A value needs quoting if it carries spaces, commas, equals or a leading digit-ish oddity. */
  function requote(s) {
    if (s === '') return s;
    if (/[\s,=*]/.test(s)) return '"' + s.replace(/"/g, '') + '"';
    return s;
  }

  function isKeywordLine(line) {
    return line.charAt(0) === '*' && line.charAt(1) !== '*';
  }

  function isCommentLine(line) {
    return line.charAt(0) === '*' && line.charAt(1) === '*';
  }

  /*
   * Parse one (possibly continued) keyword card into a name and parameter list.
   * `*Elset, elset=Set-1, internal, instance="Part-1"` becomes
   *   { keyword: 'Elset', params: [ {key:'elset', value:'Set-1', flag:false}, ... ] }
   */
  function parseKeywordCard(text) {
    var body = text.replace(/^\*/, '');
    var parts = splitCommas(body);
    var keyword = parts.shift().trim();
    var params = parts.map(function (p) {
      var eq = -1, quoted = false, i;
      for (i = 0; i < p.length; i++) {
        if (p.charAt(i) === '"') quoted = !quoted;
        else if (p.charAt(i) === '=' && !quoted) { eq = i; break; }
      }
      if (eq === -1) {
        return { key: p.trim(), value: null, flag: true };
      }
      return {
        key: p.substring(0, eq).trim(),
        value: unquote(p.substring(eq + 1)),
        flag: false
      };
    }).filter(function (p) { return p.key !== '' || !p.flag; });
    return { keyword: keyword, params: params };
  }

  function buildKeywordLine(block) {
    var s = '*' + block.keyword;
    block.params.forEach(function (p) {
      if (!p.key) return;
      s += ', ' + p.key + (p.flag ? '' : '=' + requote(p.value == null ? '' : String(p.value)));
    });
    return s;
  }

  /*
   * Normalised lookup key: Abaqus keywords are case insensitive and treat
   * runs of whitespace as insignificant. `*End Part` -> `ENDPART`.
   */
  function normKey(keyword) {
    return keyword.replace(/\s+/g, '').toUpperCase();
  }

  function getParam(block, key) {
    var want = String(key).toUpperCase(), i;
    for (i = 0; i < block.params.length; i++) {
      if (block.params[i].key.toUpperCase() === want) return block.params[i].value;
    }
    return null;
  }

  function hasParam(block, key) {
    var want = String(key).toUpperCase(), i;
    for (i = 0; i < block.params.length; i++) {
      if (block.params[i].key.toUpperCase() === want) return true;
    }
    return false;
  }

  function setParam(block, key, value) {
    var want = String(key).toUpperCase(), i;
    for (i = 0; i < block.params.length; i++) {
      if (block.params[i].key.toUpperCase() === want) {
        block.params[i].value = value;
        block.params[i].flag = (value === null);
        block.dirty = true;
        return;
      }
    }
    block.params.push({ key: key, value: value, flag: value === null });
    block.dirty = true;
  }

  function makeBlock(keyword, params, data) {
    var b = {
      id: 'b' + (++blockSeq),
      keyword: keyword,
      key: normKey(keyword),
      params: params || [],
      comments: [],
      data: data || [],
      srcKeywordLines: null,
      lineNo: 0,
      dirty: true
    };
    return b;
  }

  /*
   * Main entry point. Returns { blocks: [...], eof: [trailing comment lines] }.
   */
  function parse(text) {
    var lines = text.split(/\r\n|\r|\n/);
    /* A trailing newline yields one empty final entry; drop it so we do not
       gain a blank line on every save/re-open cycle. */
    if (lines.length && lines[lines.length - 1] === '') lines.pop();

    var blocks = [];
    var pendingComments = [];
    var current = null;
    var i = 0;

    while (i < lines.length) {
      var raw = lines[i];
      var trimmed = raw.trim();

      if (trimmed === '') {
        /* Blank lines are metadata-free; keep them with whatever follows. */
        pendingComments.push(raw);
        i++;
        continue;
      }

      if (isCommentLine(trimmed)) {
        pendingComments.push(raw);
        i++;
        continue;
      }

      if (isKeywordLine(trimmed)) {
        var cardLines = [raw];
        var cardText = trimmed;
        /* A keyword card continues while its line ends in a comma. */
        while (/,\s*$/.test(cardText) && i + 1 < lines.length) {
          var next = lines[i + 1];
          var nextTrim = next.trim();
          if (nextTrim === '' || isKeywordLine(nextTrim)) break;
          if (isCommentLine(nextTrim)) { i++; cardLines.push(next); continue; }
          i++;
          cardLines.push(next);
          cardText += nextTrim;
        }
        var parsed = parseKeywordCard(cardText);
        current = {
          id: 'b' + (++blockSeq),
          keyword: parsed.keyword,
          key: normKey(parsed.keyword),
          params: parsed.params,
          comments: pendingComments,
          data: [],
          srcKeywordLines: cardLines,
          lineNo: i + 1,
          dirty: false
        };
        blocks.push(current);
        pendingComments = [];
        i++;
        continue;
      }

      /* Data line. */
      if (!current) {
        /* Data before any keyword: park it in a synthetic block so nothing is lost. */
        current = {
          id: 'b' + (++blockSeq),
          keyword: '',
          key: '',
          params: [],
          comments: pendingComments,
          data: [],
          srcKeywordLines: [],
          lineNo: i + 1,
          dirty: false
        };
        blocks.push(current);
        pendingComments = [];
      }
      /* Comments interleaved inside a data section stay in place. */
      current.data.push(raw);
      i++;
    }

    return { blocks: blocks, trailing: pendingComments };
  }

  /*
   * Data rows. Abaqus data lines are comma separated and continue onto the
   * next line when they end with a comma (element connectivity, long set
   * lists). `rowSpan` records how many source lines each logical row used so
   * an edit can be written back without disturbing its neighbours.
   */
  function dataRows(block) {
    if (block._rows) return block._rows;
    var rows = [], i = 0, data = block.data;
    while (i < data.length) {
      var line = data[i];
      var t = line.trim();
      if (t === '' || isCommentLine(t)) {
        rows.push({ fields: null, raw: line, comment: true, src: [line], dirty: false });
        i++;
        continue;
      }
      var text = t;
      var src = [line];
      while (/,\s*$/.test(text) && i + 1 < data.length) {
        var nxt = data[i + 1].trim();
        if (nxt === '' || isCommentLine(nxt)) break;
        text += nxt;
        i++;
        src.push(data[i]);
      }
      rows.push({
        fields: splitCommas(text).map(function (f) { return f.trim(); }),
        raw: null,
        comment: false,
        /* The original source lines are kept so an edit to one row never
           reformats its untouched neighbours. */
        src: src,
        dirty: false
      });
      i++;
    }
    block._rows = rows;
    return rows;
  }

  /* Right-align a data row roughly the way Abaqus/CAE writes them. */
  function formatFields(fields, perLine) {
    var widths = fields.map(function (f, i) {
      return i === 0 ? 7 : Math.max(8, Math.min(13, f.length + 2));
    });
    var out = [];
    for (var i = 0; i < fields.length; i += perLine) {
      var chunk = [];
      for (var j = i; j < Math.min(i + perLine, fields.length); j++) {
        var f = String(fields[j]);
        var w = widths[j];
        chunk.push(f.length >= w ? f : new Array(w - f.length + 1).join(' ') + f);
      }
      var last = (i + perLine) >= fields.length;
      out.push(chunk.join(',') + (last ? '' : ','));
    }
    return out;
  }

  /* Re-emit `block.data` from parsed rows (used after an edit). */
  function rowsToData(rows, perLine) {
    var out = [];
    perLine = perLine || 16;
    rows.forEach(function (r) {
      if (!r.dirty && r.src) { out.push.apply(out, r.src); return; }
      if (r.comment) { out.push(r.raw); return; }
      out.push.apply(out, formatFields(r.fields, perLine));
    });
    return out;
  }

  /* Mark one parsed row as edited so only that row is rewritten. */
  function markRow(block, row) {
    row.dirty = true;
    block.dirty = true;
    block._rowsDirty = true;
  }

  global.InpParser = {
    parse: parse,
    parseKeywordCard: parseKeywordCard,
    buildKeywordLine: buildKeywordLine,
    dataRows: dataRows,
    rowsToData: rowsToData,
    formatFields: formatFields,
    markRow: markRow,
    splitCommas: splitCommas,
    unquote: unquote,
    requote: requote,
    normKey: normKey,
    getParam: getParam,
    hasParam: hasParam,
    setParam: setParam,
    makeBlock: makeBlock,
    isCommentLine: isCommentLine,
    isKeywordLine: isKeywordLine
  };
})(typeof window !== 'undefined' ? window : globalThis);
