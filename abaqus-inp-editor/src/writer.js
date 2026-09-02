/*
 * writer.js -- serialise the block list back to an Abaqus .inp file.
 *
 * A block that has not been edited is re-emitted from its original source
 * lines, so exporting an untouched file reproduces it exactly. Only edited
 * blocks are regenerated, which keeps diffs between the original and the
 * exported file limited to what the user actually changed.
 */
(function (global) {
  'use strict';

  var P = global.InpParser;

  /* Data-line field counts Abaqus writes for the common cards. */
  function perLineFor(block) {
    switch (block.key) {
      case 'NODE': return 4;
      case 'ELEMENT': return 16;
      case 'NSET':
      case 'ELSET': return 16;
      default: return 16;
    }
  }

  function blockLines(block) {
    var out = [];
    if (block.comments && block.comments.length) out.push.apply(out, block.comments);

    if (!block.dirty && block.srcKeywordLines) {
      out.push.apply(out, block.srcKeywordLines);
      out.push.apply(out, block.data);
      return out;
    }

    if (block.keyword) out.push(P.buildKeywordLine(block));

    /* If rows were edited through a grid, they are the source of truth. */
    if (block._rowsDirty && block._rows) {
      block.data = P.rowsToData(block._rows, perLineFor(block));
      block._rowsDirty = false;
    }
    out.push.apply(out, block.data);
    return out;
  }

  function write(model, options) {
    options = options || {};
    var lines = [];
    model.blocks.forEach(function (b) {
      lines.push.apply(lines, blockLines(b));
    });
    if (model.trailing && model.trailing.length) {
      lines.push.apply(lines, model.trailing);
    }
    var text = lines.join('\n');
    if (options.trailingNewline !== false) text += '\n';
    return text;
  }

  /* Mark a block as edited so the writer regenerates it. */
  function touch(block) {
    block.dirty = true;
  }

  function touchRows(block) {
    block.dirty = true;
    block._rowsDirty = true;
  }

  /*
   * Count how many blocks differ from the source. Drives the "unsaved changes"
   * indicator and the change summary in the export dialog.
   */
  function changeSummary(model) {
    var changed = [];
    model.blocks.forEach(function (b) {
      if (b.dirty) {
        changed.push({
          id: b.id,
          keyword: b.keyword,
          label: describe(b),
          added: !b.srcKeywordLines || b.srcKeywordLines.length === 0
        });
      }
    });
    return changed;
  }

  function describe(block) {
    var name = P.getParam(block, 'name') || P.getParam(block, 'nset') ||
      P.getParam(block, 'elset') || P.getParam(block, 'elset') || '';
    return '*' + block.keyword + (name ? ' (' + name + ')' : '');
  }

  global.InpWriter = {
    write: write,
    blockLines: blockLines,
    touch: touch,
    touchRows: touchRows,
    changeSummary: changeSummary,
    describe: describe
  };
})(typeof window !== 'undefined' ? window : globalThis);
