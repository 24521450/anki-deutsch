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
  function policy() {
    return globalThis.goetheWerkstattVerbTargetPolicy || {
      blank_pos_verb_source_ids: [], verb_specs: {}, exact_overrides: []
    };
  }
  function usableVerbForms() {
    var raw = text("gw-verb-forms").trim();
    if (raw && !/^(?:A|CH)\)$/i.test(raw)) return raw.split(";", 1)[0].trim();
    var source = text("gw-source-note-raw");
    var prefix = source.split("|", 1)[0].trim();
    if (prefix[0] === "{") {
      try {
        var parsed = JSON.parse(prefix);
        if (String(parsed.Verbformen || "").trim()) return String(parsed.Verbformen).split(";", 1)[0].trim();
      } catch (error) {}
    }
    var match = /(?:^|\|)\s*source:\s*(.*)$/i.exec(source);
    return match ? match[1].split(/;\s*(?:Expansion\/context:)?/i, 1)[0].trim() : "";
  }
  function splitForms(raw) {
    var result = [];
    String(raw || "").split(",").forEach(function (chunk) {
      chunk.split("/").forEach(function (part) {
        part = part.replace(/\b(?:jdn?\.|jdm\.)\b/gi, "").replace(/\s*\((?:von|auf|CH|A)\)\s*$/i, "").trim();
        if (part) result.push(part);
      });
    });
    return result;
  }
  function stripVerbLemma(value) {
    return String(value || "").replace(/^\(sich\)\s*/i, "").replace(/^sich\s+/i, "")
      .replace(/\s+\([^()]+\)\s*$/, "").trim();
  }
  function verbStem(infinitive) {
    if (/eln$/i.test(infinitive) || /ern$/i.test(infinitive)) return infinitive.slice(0, -1);
    if (/en$/i.test(infinitive)) return infinitive.slice(0, -2);
    if (/n$/i.test(infinitive)) return infinitive.slice(0, -1);
    return infinitive;
  }
  function verbSurfaces(infinitive, rawForms) {
    var result = {};
    function add(value) { if (value && value.length > 1) result[value] = true; }
    add(infinitive);
    var stem = verbStem(infinitive);
    if (/eln$/i.test(infinitive)) {
      var contracted = stem.slice(0, -2) + stem.slice(-1);
      [stem + "e", contracted + "e", stem + "st", stem + "t", infinitive].forEach(add);
    } else if (/ern$/i.test(infinitive)) {
      [stem + "e", stem.slice(0, -1) + "re", stem + "st", stem + "t", infinitive].forEach(add);
    } else {
      [stem, stem + "e", stem + "en"].forEach(add);
      if (/(?:[td]|[bcdfgkp]m|[bcdfgkp]n)$/i.test(stem)) [stem + "est", stem + "et"].forEach(add);
      else [stem + (/[sxzß]$/i.test(stem) ? "t" : "st"), stem + "t"].forEach(add);
    }
    var stop = {hat:1, ist:1, sind:1, wird:1, sein:1, haben:1, sich:1};
    var simple = [];
    rawForms.forEach(function (form) {
      form.split(/\s+/).forEach(function (part) {
        if (/^\([^()]+\)$/.test(part)) return;
        var clean = part.replace(/^[.,;:()]+|[.,;:()]+$/g, "");
        if (clean && !stop[clean.toLocaleLowerCase("de-DE")]) { add(clean); simple.push(clean); }
      });
    });
    simple.forEach(function (form) {
      if (/t$/i.test(form) && form.length > 3) {
        var present = form.slice(0, -1);
        [present, present + "e", present + "st", present + "t"].forEach(add);
      }
      if (/te$/i.test(form) && form.length > 4) [form + "n", form + "st", form + "t"].forEach(add);
    });
    var irregular = {
      sein:["bin","bist","ist","sind","seid","war","waren","gewesen"],
      haben:["habe","hast","hat","haben","habt","hatte","hatten","gehabt"],
      werden:["werde","wirst","wird","werden","werdet","wurde","wurden","geworden"],
      "dürfen":["darf","darfst","dürfen","dürft"], "können":["kann","kannst","können","könnt"],
      "mögen":["mag","magst","mögen","mögt"], "müssen":["muss","musst","müssen","müsst"],
      sollen:["soll","sollst","sollen","sollt"], wissen:["weiß","weißt","wissen","wisst","wusste","gewusst"]
    };
    (irregular[infinitive.toLocaleLowerCase("de-DE")] || []).forEach(add);
    return Object.keys(result);
  }
  function verbSpec(lemma, rawForms, pos) {
    var source = text("gw-source-id");
    var config = policy();
    var isVerb = /^v\.?$/i.test(pos) || (config.blank_pos_verb_source_ids || []).indexOf(source) >= 0;
    if (!isVerb) return null;
    if (config.verb_specs && config.verb_specs[source]) return config.verb_specs[source];
    var lexical = stripVerbLemma(lemma);
    var tokens = lexical.split(/\s+/).filter(Boolean);
    if (tokens.length > 1) {
      var head = tokens[tokens.length - 1];
      if (!/(?:en|eln|ern|sein|haben|werden|lassen|leben|bleiben|gehen|geben|sagen|fahren)$/i.test(head)) return null;
      return {head:head, predicate:tokens.slice(0, -1).filter(function (value) {
        return ["sich", "etwas", "über"].indexOf(value.toLocaleLowerCase("de-DE")) < 0;
      })};
    }
    for (var index = 0; index < rawForms.length; index += 1) {
      var parts = rawForms[index].split(/\s+/);
      var particle = parts[parts.length - 1].replace(/[.,;:()]/g, "");
      if (parts.length > 1 && particle.length > 1 && lexical.toLocaleLowerCase("de-DE").indexOf(particle.toLocaleLowerCase("de-DE")) === 0) {
        var base = lexical.slice(particle.length);
        if (/(?:en|eln|ern)$/i.test(base)) return {head:base, particle:particle};
      }
    }
    return {head:lexical};
  }
  function terms() {
    var lemma = text("gw-lemma");
    var lexicalLemma = stripGenderQualifier(lemma);
    var values = [lemma, lexicalLemma];
    var verbEntries = [];
    var caseSensitive = {};
    var accepted = text("gw-accepted-answers").split("|").map(stripGenderQualifier);
    var lexicalBases = unique([lexicalLemma].concat(accepted));
    values = values.concat(accepted);

    var noun = text("gw-noun-forms");
    var suffixes = noun.match(/-(?:e|en|er|n|s)\b/gi) || [];
    suffixes.forEach(function (suffix) {
      lexicalBases.forEach(function (base) { values.push(base + suffix.slice(1)); });
    });
    var markerMatcher = /(?:\u00a8-|"-?)(en|er|e|n|s)?(?![\p{L}\p{N}_])/giu;
    var markerSuffix = null;
    var markerMatch;
    while ((markerMatch = markerMatcher.exec(noun))) {
      var suffix = markerMatch[1] || "";
      if (markerSuffix === null || suffix.length > markerSuffix.length) markerSuffix = suffix;
    }
    if (markerSuffix !== null) {
      lexicalBases.forEach(function (base) {
        var umlautForm = umlaut(base) + markerSuffix;
        values.push(umlautForm);
        caseSensitive[umlautForm] = true;
      });
    }
    if (/-[AÄÖÜäöü]/.test(noun)) {
      var ending = /,\s*(e|er|en|n)?\b/i.exec(noun);
      lexicalBases.forEach(function (base) {
        values.push(umlaut(base) + (ending && ending[1] ? ending[1] : ""));
      });
    }

    var pos = text("gw-pos");
    if (/^n\.?$/i.test(pos) && /-(?:n|en)\b/i.test(noun)) {
      lexicalBases.forEach(function (base) {
        if (!/e$/i.test(base)) return;
        var nominalizedBase = base.slice(0, -1);
        ["e", "en", "em", "er", "es"].forEach(function (ending) {
          values.push(nominalizedBase + ending);
        });
      });
    }

    var rawForms = splitForms(usableVerbForms());
    var spec = verbSpec(lexicalLemma, rawForms, pos);
    if (spec) {
      var pairKey = "verb:" + (text("gw-source-id") || lexicalLemma.toLocaleLowerCase("de-DE"));
      var role = spec.particle || (spec.predicate || []).length ? "head" : "standalone";
      verbSurfaces(String(spec.head), rawForms).forEach(function (value) {
        var valueRole = role;
        var valuePair = pairKey;
        if (spec.particle && value.toLocaleLowerCase("de-DE").indexOf(String(spec.particle).toLocaleLowerCase("de-DE")) === 0
            && value.toLocaleLowerCase("de-DE") !== String(spec.particle).toLocaleLowerCase("de-DE")) {
          valueRole = "standalone"; valuePair = "";
        }
        verbEntries.push({text:value, caseMode:"verb", role:valueRole, pairKey:valuePair});
      });
      (spec.forms || []).forEach(function (value) {
        verbEntries.push({text:String(value), caseMode:"verb", role:"standalone", pairKey:""});
      });
      (spec.head_forms || []).forEach(function (value) {
        verbEntries.push({text:String(value), caseMode:"verb", role:role, pairKey:pairKey});
      });
      if (spec.particle) {
        verbEntries.push({text:String(spec.particle), caseMode:"fold", role:"particle", pairKey:pairKey});
        var joined = stripVerbLemma(lexicalLemma).replace(/\s+/g, "");
        if (joined) verbEntries.push({text:joined, caseMode:"verb", role:"standalone", pairKey:""});
        verbEntries.push({text:String(spec.particle) + "zu" + String(spec.head), caseMode:"verb", role:"standalone", pairKey:""});
      }
      (spec.predicate || []).forEach(function (value) {
        verbEntries.push({text:String(value), caseMode:"fold", role:"predicate", pairKey:pairKey});
      });
      rawForms.forEach(function (value) {
        verbEntries.push({text:value, caseMode:"verb", role:"standalone", pairKey:""});
      });
    }

    if (/adj|det|pron/i.test(pos)) {
      var base = lexicalLemma.replace(/-$/, "");
      ["e", "en", "em", "er", "es"].forEach(function (ending) { values.push(base + ending); });
    }
    var entries = values.map(function (value) {
      value = value.trim();
      return {text:value, caseMode:caseSensitive[value] ? "exact" : "fold", role:"standalone", pairKey:""};
    }).concat(verbEntries);
    var seenEntries = {};
    entries = entries.filter(function (entry) {
      var key = entry.text.toLocaleLowerCase("de-DE") + "\u0000" + entry.caseMode + "\u0000" + entry.role + "\u0000" + entry.pairKey;
      if (!entry.text || seenEntries[key]) return false;
      seenEntries[key] = true; return true;
    }).sort(function (left, right) { return right.text.length - left.text.length; });
    var result = entries.map(function (entry) { return entry.text; });
    Object.defineProperty(result, "caseSensitive", { value: caseSensitive });
    Object.defineProperty(result, "wholeWord", { value: !/^n\.?$/i.test(pos) });
    Object.defineProperty(result, "meta", { value: entries });
    return result;
  }
  function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }
  function matchRanges(source, candidates) {
    var occurrences = [];
    candidates.forEach(function (candidate, candidateIndex) {
      var meta = candidates.meta ? candidates.meta[candidateIndex] : {
        caseMode:candidates.caseSensitive && candidates.caseSensitive[candidate] ? "exact" : "fold",
        role:"standalone", pairKey:""
      };
      var escaped = escapeRegex(candidate);
      var bounded = candidate.length < 4 || candidates.wholeWord
        ? "(?<![\\p{L}\\p{N}_])" + escaped + "(?![\\p{L}\\p{N}_])"
        : escaped;
      var flags = meta.caseMode === "exact" ? "gu" : "giu";
      var matcher = new RegExp(bounded, flags);
      var match;
      while ((match = matcher.exec(source))) {
        if (meta.caseMode === "verb" && /^[A-ZÄÖÜ]/.test(match[0]) && /^[a-zäöüß]/.test(candidate)) {
          var prefix = source.slice(0, match.index).replace(/[\s"'„“»«(\[]+$/g, "");
          if (prefix && !/[.!?;:\n–—]$/.test(prefix)) continue;
        }
        occurrences.push([match.index, match.index + match[0].length, meta.role || "standalone", meta.pairKey || ""]);
      }
    });
    function clauseAt(index) { return (source.slice(0, index).match(/[,.!?;:\n–—]/g) || []).length; }
    var ranges = [];
    var grouped = {};
    occurrences.forEach(function (item) {
      if (item[2] === "standalone" || !item[3]) ranges.push(item.slice(0, 2));
      else (grouped[item[3]] = grouped[item[3]] || []).push(item);
    });
    Object.keys(grouped).forEach(function (key) {
      var items = grouped[key];
      var heads = items.filter(function (item) { return item[2] === "head"; });
      var particles = items.filter(function (item) { return item[2] === "particle"; });
      var predicates = items.filter(function (item) { return item[2] === "predicate"; });
      predicates.forEach(function (item) { ranges.push(item.slice(0, 2)); });
      var used = {};
      heads.forEach(function (head) {
        var clause = clauseAt(head[0]);
        if (particles.length) {
          var choices = particles.filter(function (item) {
            return clauseAt(item[0]) === clause && item[0] >= head[1] && !used[item[0] + ":" + item[1]];
          });
          if (!choices.length) return;
          choices.sort(function (left, right) { return right[0] - left[0]; });
          var chosen = choices[0]; used[chosen[0] + ":" + chosen[1]] = true;
          ranges.push(head.slice(0, 2), chosen.slice(0, 2));
        } else if (predicates.some(function (item) { return clauseAt(item[0]) === clause; })) {
          ranges.push(head.slice(0, 2));
        }
      });
    });
    var uniqueRanges = {};
    ranges = ranges.filter(function (range) {
      var key = range[0] + ":" + range[1];
      if (uniqueRanges[key]) return false;
      uniqueRanges[key] = true; return true;
    });
    ranges.sort(function (a, b) { return a[0] - b[0] || b[1] - a[1]; });
    var selected = [];
    ranges.forEach(function (range) {
      if (!selected.length || range[0] >= selected[selected.length - 1][1]) selected.push(range);
    });
    return selected;
  }
  function rangesForExample(source, candidates, exampleIndex) {
    var ranges = matchRanges(source, candidates);
    var sourceId = text("gw-source-id");
    (policy().exact_overrides || []).forEach(function (item) {
      if (item.source_id !== sourceId || item.example_index !== exampleIndex) return;
      if (item.text !== source) throw new Error("reviewed target override text drift: " + sourceId);
      ranges = ranges.concat(item.ranges.map(function (range) { return range.slice(); }));
    });
    var seen = {};
    return ranges.filter(function (range) {
      var key = range[0] + ":" + range[1];
      if (seen[key]) return false;
      seen[key] = true; return true;
    }).sort(function (left, right) { return left[0] - right[0] || left[1] - right[1]; });
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
  function highlight(node, candidates, exampleIndex) {
    var source = node.textContent;
    highlightRanges(node, rangesForExample(source, candidates, exampleIndex));
  }
  function setExampleLanguages() {
    document.querySelectorAll(".gw-example-de").forEach(function (node) { node.setAttribute("lang", "de"); });
    document.querySelectorAll(".gw-example-sub").forEach(function (node) { node.setAttribute("lang", "en"); });
  }

  globalThis.goetheWerkstattTargetHighlighter = {
    terms: terms,
    matchRanges: matchRanges,
    rangesForExample: rangesForExample,
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
    highlight(node, candidates, index + 1);
  });
})();
