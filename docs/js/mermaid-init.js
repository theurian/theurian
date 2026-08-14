// Render the mermaid fences that Markdown leaves as code blocks.
//
// A ```mermaid fence becomes <pre><code class="language-mermaid">, which is
// markup mermaid does not look for, so nothing rendered them before.
//
// The bundle is not loaded by the page itself. It is requested from here, and
// only when the page actually has a diagram to draw: it is 3,566,058 bytes and
// most published pages have no diagram at all.
(function () {
  "use strict";

  // document.currentScript is only meaningful while this script is executing,
  // so the URL is captured now and used later. Resolving the bundle against it
  // means this works at any page depth without knowing the site root.
  var SELF = document.currentScript ? document.currentScript.src : null;

  var FENCE = "pre > code.language-mermaid";

  function fences() {
    var found = document.querySelectorAll(FENCE);
    var items = [];

    for (var i = 0; i < found.length; i++) {
      var source = found[i].textContent;

      // An empty or whitespace-only fence has nothing to draw, and handing it
      // to mermaid only produces the error graphic. Leave it as it is.
      if (!source || source.trim() === "") {
        continue;
      }

      items.push({ code: found[i], source: source });
    }

    return items;
  }

  function toMermaidNodes(items) {
    var nodes = [];

    for (var i = 0; i < items.length; i++) {
      var original = items[i].code.parentNode;
      var host = document.createElement("pre");

      host.className = "mermaid";
      // textContent, not innerHTML: the build has already escaped the fence
      // into entities and wrapped it in markup, and mermaid needs the diagram
      // source back as plain text.
      host.textContent = items[i].source;

      original.parentNode.replaceChild(host, original);
      items[i].host = host;
      nodes.push(host);
    }

    return nodes;
  }

  function restoreFailures(items) {
    for (var i = 0; i < items.length; i++) {
      var host = items[i].host;
      var first = host.firstElementChild;

      // mermaid.render empties its container before it parses, so a diagram
      // that fails to parse has already lost its source from the DOM and shows
      // mermaid's error graphic instead. Putting the source back is the only
      // way this degrades to something a reader can still use.
      //
      // The discriminator depends on mermaid 11.x's output shape: a rendered
      // diagram leaves the <svg> as the direct child of the host, while a
      // failed one leaves the error graphic wrapped in a <div>.
      if (!first || first.tagName.toLowerCase() !== "svg") {
        host.textContent = items[i].source;
      }
    }
  }

  function enforceNaturalScale(items) {
    for (var i = 0; i < items.length; i++) {
      var svg = items[i].host.firstElementChild;

      if (!svg || svg.tagName.toLowerCase() !== "svg") {
        continue;
      }

      var natural =
        svg.viewBox && svg.viewBox.baseVal ? svg.viewBox.baseVal.width : 0;

      // The invariant is that no diagram renders below its natural scale. The
      // useMaxWidth settings below are the native way to get that, but they
      // only cover the diagram types named there, and useMaxWidth is not on
      // mermaid's secure list, so a fence can turn it back on for itself with
      // %%{init: ...}%%. Either route silently shrinks a diagram to the content
      // column, and it shrinks the svg rather than overflowing, so the CSS
      // cannot scroll it back. This holds the invariant for any type and any
      // directive; the tolerance is for sub-pixel layout rounding.
      if (natural && svg.getBoundingClientRect().width < natural - 1) {
        svg.style.maxWidth = "none";
        svg.style.width = natural + "px";
      }
    }
  }

  function render(items) {
    if (typeof window.mermaid === "undefined") {
      return;
    }

    var nodes = toMermaidNodes(items);

    window.mermaid.initialize({
      // The bundle registers its own window load handler that renders
      // everything matching .mermaid. This runs before that handler can fire,
      // so rendering happens once, here, over the nodes we chose.
      startOnLoad: false,
      // Stated rather than inherited. This is the setting that sanitises
      // diagram source, and it should not move quietly with a library default.
      securityLevel: "strict",
      // Without this every diagram is scaled down to the width of the content
      // column, which makes a wide one unreadable. At natural size it overflows
      // instead, and docs/css/mermaid.css turns that overflow into a scroll.
      // This is the native path and it covers the four types the docs use;
      // enforceNaturalScale above is what holds the invariant for the rest.
      flowchart: { useMaxWidth: false },
      sequence: { useMaxWidth: false },
      er: { useMaxWidth: false },
      state: { useMaxWidth: false },
    });

    // Known gap: the rendered SVGs carry no aria-label, title or desc, and no
    // fence in docs/ declares accTitle or accDescr, so the diagrams are not
    // described to a screen reader. The source text is not exposed either once
    // a diagram renders.
    //
    // mermaid.run catches per node internally and keeps going, so one bad
    // diagram cannot stop the ones after it. suppressErrors keeps the failure
    // it re-raises afterwards from becoming an unhandled rejection; both the
    // restore and the scale backstop run on either settle path.
    var settle = function () {
      restoreFailures(items);
      enforceNaturalScale(items);
    };

    window.mermaid.run({ nodes: nodes, suppressErrors: true }).then(settle, settle);
  }

  function start() {
    var items = fences();

    // Nothing to draw: the bundle is never requested on this page.
    if (items.length === 0 || !SELF) {
      return;
    }

    var script = document.createElement("script");

    script.src = new URL("mermaid.min.js", SELF).href;
    // The fences are only rewritten once the bundle has arrived, so a fetch
    // that fails leaves the page exactly as the build produced it.
    script.onload = function () {
      render(items);
    };

    document.head.appendChild(script);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
