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
  function addFirstPersonPresentForm(values, infinitive) {
    if (!/en$/i.test(infinitive)) return;
    var stem = infinitive.slice(0, -2);
    values.push(stem + "e");
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
    if (isSingleWordVerb) addFirstPersonPresentForm(values, verbLemma);
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
          var baseInfinitive = verbLemma.slice(particle.length);
          values.push(baseInfinitive);
          addFirstPersonPresentForm(values, baseInfinitive);
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
  function validateRanges(source, ranges) {
    if (!Array.isArray(ranges)) return null;
    var sorted = ranges.map(function (range) {
      if (!Array.isArray(range) || range.length !== 2) return null;
      var start = range[0];
      var end = range[1];
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > source.length) return null;
      return [start, end];
    });
    if (sorted.some(function (range) { return !range; })) return null;
    sorted.sort(function (left, right) { return left[0] - right[0] || left[1] - right[1]; });
    for (var index = 1; index < sorted.length; index += 1) {
      if (sorted[index][0] < sorted[index - 1][1]) return null;
    }
    return sorted;
  }
  function parsePrecomputed(raw) {
    if (!raw) return null;
    try {
      var value = JSON.parse(raw);
      return Array.isArray(value) ? value : null;
    } catch (error) {
      return null;
    }
  }
  function hasTargetAncestor(node, root) {
    var parent = node.parentNode;
    while (parent && parent !== root) {
      if (parent.classList && parent.classList.contains("gw-target-word")) return true;
      parent = parent.parentNode;
    }
    return false;
  }
  function collectTextNodes(root) {
    if (!document.createTreeWalker) return [];
    var walker = document.createTreeWalker(root, 4, null, false);
    var nodes = [];
    var current;
    while ((current = walker.nextNode())) {
      nodes.push({ node: current, marked: hasTargetAncestor(current, root) });
    }
    return nodes;
  }
  function addFallback(node) {
    var badge = document.createElement("span");
    badge.className = "gw-target-fallback";
    badge.textContent = "Target · " + text("gw-lemma");
    node.parentNode.insertBefore(badge, node);
  }
  function wrapRange(root, start, end) {
    var cursor = 0;
    collectTextNodes(root).forEach(function (entry) {
      var textNode = entry.node;
      var length = textNode.nodeValue.length;
      var localStart = Math.max(start - cursor, 0);
      var localEnd = Math.min(end - cursor, length);
      if (!entry.marked && localStart < localEnd) {
        var target = textNode;
        if (localStart > 0) target = target.splitText(localStart);
        var targetLength = localEnd - localStart;
        if (targetLength < target.nodeValue.length) target.splitText(targetLength);
        var mark = document.createElement("mark");
        mark.className = "gw-target-word";
        mark.textContent = target.nodeValue;
        target.parentNode.replaceChild(mark, target);
      }
      cursor += length;
    });
  }
  function highlightRanges(node, ranges) {
    if (!ranges || !ranges.length) {
      addFallback(node);
      return;
    }
    for (var index = ranges.length - 1; index >= 0; index -= 1) {
      wrapRange(node, ranges[index][0], ranges[index][1]);
    }
  }
  function highlight(node, candidates) {
    var source = node.textContent;
    highlightRanges(node, matchRanges(source, candidates));
  }
  function setExampleLanguages() {
    document.querySelectorAll(".gw-example-de").forEach(function (node) { node.setAttribute("lang", "de"); });
    document.querySelectorAll(".gw-example-sub").forEach(function (node) { node.setAttribute("lang", "en"); });
  }

  globalThis.goetheWerkstattTargetHighlighter = {
    terms: terms,
    matchRanges: matchRanges,
    validateRanges: validateRanges,
    parsePrecomputed: parsePrecomputed,
  };
  setExampleLanguages();
  var targetRaw = text("gw-example-target-spans");
  var precomputed = parsePrecomputed(targetRaw);
  var candidates = targetRaw === "" ? terms() : null;
  document.querySelectorAll(".gw-example-de").forEach(function (node, index) {
    if (targetRaw !== "") {
      var ranges = precomputed && validateRanges(node.textContent, precomputed[index]);
      highlightRanges(node, ranges);
      return;
    }
    highlight(node, candidates);
  });
})();
