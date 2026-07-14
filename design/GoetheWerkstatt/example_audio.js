(function () {
  var pairs = [];

  function reset(audio) {
    try { audio.currentTime = 0; } catch (error) {}
  }

  function stopOthers(active) {
    pairs.forEach(function (pair) {
      if (pair === active) return;
      pair.audio.pause();
      reset(pair.audio);
    });
  }

  function replay(pair, event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    stopOthers(pair);
    reset(pair.audio);
    var result = pair.audio.play();
    if (result && typeof result.catch === "function") {
      result.catch(function () { pair.sentence.classList.remove("gw-example-playing"); });
    }
  }

  document.querySelectorAll(".gw-example").forEach(function (example) {
    var sentence = example.querySelector(".gw-example-main");
    var audio = example.querySelector(".gw-example-audio audio");
    if (!sentence || !audio) return;

    var pair = { sentence: sentence, audio: audio };
    pairs.push(pair);
    sentence.classList.add("gw-example-playable");
    sentence.setAttribute("role", "button");
    sentence.setAttribute("tabindex", "0");
    sentence.setAttribute("title", "Play example audio");
    sentence.setAttribute("aria-label", "Play example audio: " + sentence.textContent.trim());

    sentence.addEventListener("click", function (event) { replay(pair, event); });
    sentence.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") replay(pair, event);
    });
    audio.addEventListener("play", function () { sentence.classList.add("gw-example-playing"); });
    ["pause", "ended", "error"].forEach(function (name) {
      audio.addEventListener(name, function () { sentence.classList.remove("gw-example-playing"); });
    });
  });

  globalThis.goetheWerkstattExampleAudio = { count: pairs.length };
})();
