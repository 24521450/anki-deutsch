(function () {
  var headword = document.querySelector(".gw-headword, .gw-german-answer");
  var container = document.querySelector(".gw-word-audio");
  var variantsRoot = document.querySelector(".gw-word-variants");
  if (!headword) return;

  function markPlaying(node) {
    node.classList.add("gw-word-playing");
    setTimeout(function () { node.classList.remove("gw-word-playing"); }, 600);
  }

  function playVariant(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    var button = event && event.currentTarget ? event.currentTarget : this;
    var audio = button && button.querySelector(".gw-word-variant-audio");
    if (!audio) return;
    var all = variantsRoot.querySelectorAll(".gw-word-variant-audio");
    for (var index = 0; index < all.length; index += 1) {
      if (all[index] !== audio) {
        all[index].pause();
        try { all[index].currentTime = 0; } catch (error) {}
      }
    }
    try { audio.currentTime = 0; } catch (error) {}
    var promise;
    try { promise = audio.play(); } catch (error) { promise = null; }
    if (promise && typeof promise.catch === "function") promise.catch(function () {});
    markPlaying(button);
  }

  if (variantsRoot) {
    var variantButtons = variantsRoot.querySelectorAll(".gw-word-variant");
    for (var variantIndex = 0; variantIndex < variantButtons.length; variantIndex += 1) {
      var button = variantButtons[variantIndex];
      button.classList.add("gw-word-variant-playable");
      button.setAttribute("type", "button");
      button.setAttribute("aria-label", "Play pronunciation: " + (button.getAttribute("data-variant-label") || button.textContent.trim()));
      button.addEventListener("click", playVariant);
      button.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") playVariant.call(this, event);
      });
    }
    globalThis.goetheWerkstattWordAudio = { enabled: true, variants: true };
    return;
  }

  if (!container) return;

  function replay(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    var trigger = container.querySelector(".replay-button, .soundLink, a, button");
    if (!trigger) return;
    trigger.click();
    headword.classList.add("gw-word-playing");
    setTimeout(function () { headword.classList.remove("gw-word-playing"); }, 600);
  }

  headword.classList.add("gw-word-playable");
  headword.setAttribute("role", "button");
  headword.setAttribute("tabindex", "0");
  headword.setAttribute("title", "Play headword audio");
  headword.setAttribute("aria-label", "Play headword audio: " + headword.textContent.trim());
  headword.addEventListener("click", replay);
  headword.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") replay(event);
  });

  globalThis.goetheWerkstattWordAudio = { enabled: true };
})();
