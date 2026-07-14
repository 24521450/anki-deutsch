(function () {
  function text(id) {
    var node = document.getElementById(id);
    return node ? node.textContent.trim() : "";
  }
  function unique(values) {
    var seen = {};
    return values.filter(function (value) {
      var key = value.toLocaleLowerCase("de-DE");
      if (!value || seen[key]) return false;
      seen[key] = true;
      return true;
    });
  }
  function umlaut(value) {
    var replacements = [["a", "ä"], ["o", "ö"], ["u", "ü"], ["A", "Ä"], ["O", "Ö"], ["U", "Ü"]];
    for (var index = value.length - 1; index >= 0; index -= 1) {
      for (var pair = 0; pair < replacements.length; pair += 1) {
        if (value[index] === replacements[pair][0]) {
          return value.slice(0, index) + replacements[pair][1] + value.slice(index + 1);
        }
      }
    }
    return value;
  }
  function stripGenderQualifier(value) {
    return value.replace(/\s*\((?:m\u00e4nnlich|weiblich)\)\s*$/i, "").trim();
  }
  function terms() {
    var lemma = text("gw-lemma");
    var lexicalLemma = stripGenderQualifier(lemma);
    var values = [lemma, lexicalLemma];
    values = values.concat(text("gw-accepted-answers").split("|").map(stripGenderQualifier));

    var noun = text("gw-noun-forms");
    var suffixes = noun.match(/-(?:e|en|er|n|s)\b/gi) || [];
    suffixes.forEach(function (suffix) { values.push(lexicalLemma + suffix.slice(1)); });
    if (/-[AÄÖÜäöü]/.test(noun)) {
      var ending = /,\s*(e|er|en|n)?\b/i.exec(noun);
      values.push(umlaut(lexicalLemma) + (ending && ending[1] ? ending[1] : ""));
    }

    var pos = text("gw-pos");
    if (/^n\.?$/i.test(pos) && /e$/i.test(lexicalLemma) && /-(?:n|en)\b/i.test(noun)) {
      var nominalizedBase = lexicalLemma.slice(0, -1);
      ["e", "en", "em", "er", "es"].forEach(function (ending) {
        values.push(nominalizedBase + ending);
      });
    }

    var stop = { hat: true, ist: true, sind: true, wird: true, sein: true, haben: true, sich: true };
    var verbLemma = lexicalLemma.replace(/^\(sich\)\s*/i, "").replace(/^sich\s+/i, "").trim();
    var isSingleWordVerb = /^v\.?$/i.test(pos) && !/\s/.test(verbLemma);
    text("gw-verb-forms").split(",").forEach(function (form) {
      form = form.trim();
      if (!form) return;
      values.push(form);
      var parts = form.split(/\s+/);
      parts.forEach(function (part) {
        if (!stop[part.toLocaleLowerCase("de-DE")]) values.push(part);
      });
      if (isSingleWordVerb && parts.length > 1) {
        var particle = parts[parts.length - 1];
        var lowerLemma = verbLemma.toLocaleLowerCase("de-DE");
        var lowerParticle = particle.toLocaleLowerCase("de-DE");
        if (lowerParticle.length > 1 && lowerLemma.indexOf(lowerParticle) === 0 && verbLemma.length > particle.length + 2) {
          values.push(verbLemma.slice(particle.length));
        }
      }
    });

    if (/adj|det|pron/i.test(pos)) {
      var base = lexicalLemma.replace(/-$/, "");
      ["e", "en", "em", "er", "es"].forEach(function (ending) { values.push(base + ending); });
    }
    return unique(values.map(function (value) { return value.trim(); })).sort(function (left, right) { return right.length - left.length; });
  }
  function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  function matchRanges(source, candidates) {
    var ranges = [];
    candidates.forEach(function (candidate) {
      var escaped = escapeRegex(candidate);
      var bounded = candidate.length < 4 ? "(?<![\\p{L}\\p{N}])" + escaped + "(?![\\p{L}\\p{N}])" : escaped;
      var matcher = new RegExp(bounded, "giu");
      var match;
      while ((match = matcher.exec(source))) ranges.push([match.index, match.index + match[0].length]);
    });
    ranges.sort(function (a, b) { return a[0] - b[0] || b[1] - a[1]; });
    var selected = [];
    ranges.forEach(function (range) {
      if (!selected.length || range[0] >= selected[selected.length - 1][1]) selected.push(range);
    });
    return selected;
  }
  function highlight(node, candidates) {
    var source = node.textContent;
    var selected = matchRanges(source, candidates);
    if (!selected.length) {
      var badge = document.createElement("span");
      badge.className = "gw-target-fallback";
      badge.textContent = "Target · " + text("gw-lemma");
      node.parentNode.insertBefore(badge, node);
      return;
    }
    var fragment = document.createDocumentFragment();
    var cursor = 0;
    selected.forEach(function (range) {
      fragment.appendChild(document.createTextNode(source.slice(cursor, range[0])));
      var mark = document.createElement("mark");
      mark.className = "gw-target-word";
      mark.textContent = source.slice(range[0], range[1]);
      fragment.appendChild(mark);
      cursor = range[1];
    });
    fragment.appendChild(document.createTextNode(source.slice(cursor)));
    node.replaceChildren(fragment);
  }
  globalThis.goetheWerkstattTargetHighlighter = { terms: terms, matchRanges: matchRanges };
  var candidates = terms();
  document.querySelectorAll(".gw-example-de").forEach(function (node) { highlight(node, candidates); });
})();
