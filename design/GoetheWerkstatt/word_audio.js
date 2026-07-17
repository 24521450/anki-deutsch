(function () {
  var headword = document.querySelector(".gw-headword, .gw-german-answer");
  var container = document.querySelector(".gw-word-audio");
  if (!headword || !container) return;

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
