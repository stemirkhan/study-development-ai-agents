(function renderLessonDiagram() {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  var ALLOWED_DIRECTIONS = ["top-down", "left-right"];
  var NODE_ID_PATTERN = /^[a-z][a-z0-9_-]{0,47}$/;
  var ALLOWED_KINDS = [
    "default",
    "actor",
    "accent",
    "info",
    "success",
    "danger"
  ];
  var main = document.getElementById("lesson-diagram");

  if (!main) {
    return;
  }

  function fail(message) {
    throw new Error(message);
  }

  function isObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function requireString(value, field, maxLength) {
    if (typeof value !== "string" || value.trim().length === 0) {
      fail(field + " must be a non-empty string");
    }
    var normalized = value.trim();
    if (Array.from(normalized).length > maxLength) {
      fail(field + " must be at most " + maxLength + " characters");
    }
    if (normalized.split(/\r?\n/).length > 4) {
      fail(field + " must use at most four lines");
    }
    return normalized;
  }

  function requireInteger(value, field, minimum, maximum) {
    if (!Number.isInteger(value) || value < minimum || value > maximum) {
      fail(field + " must be an integer from " + minimum + " to " + maximum);
    }
    return value;
  }

  function validateConfig(raw) {
    if (!isObject(raw)) {
      fail("diagram-data must contain a JSON object");
    }

    var config = {
      title:
        raw.title === undefined
          ? ""
          : requireString(raw.title, "title", 120),
      width: requireInteger(raw.width, "width", 480, 1600),
      height: requireInteger(raw.height, "height", 320, 1200),
      direction: raw.direction,
      nodes: [],
      edges: []
    };

    if (ALLOWED_DIRECTIONS.indexOf(config.direction) === -1) {
      fail("direction must be top-down or left-right");
    }
    if (!Array.isArray(raw.nodes) || raw.nodes.length === 0) {
      fail("nodes must be a non-empty array");
    }
    if (raw.nodes.length > 24) {
      fail("nodes must contain at most 24 items");
    }
    if (!Array.isArray(raw.edges)) {
      fail("edges must be an array");
    }
    if (raw.edges.length > 48) {
      fail("edges must contain at most 48 items");
    }

    var knownIds = new Set();
    var occupiedSlots = new Set();
    raw.nodes.forEach(function validateNode(rawNode, index) {
      var field = "nodes[" + index + "]";
      if (!isObject(rawNode)) {
        fail(field + " must be an object");
      }

      var id = requireString(rawNode.id, field + ".id", 48);
      if (!NODE_ID_PATTERN.test(id)) {
        fail(field + ".id must be lowercase ASCII");
      }
      if (knownIds.has(id)) {
        fail(field + ".id duplicates " + id);
      }
      knownIds.add(id);

      var kind = rawNode.kind === undefined ? "default" : rawNode.kind;
      if (ALLOWED_KINDS.indexOf(kind) === -1) {
        fail(field + ".kind is not supported");
      }
      var rank = requireInteger(rawNode.rank, field + ".rank", 0, 12);
      var order = requireInteger(rawNode.order, field + ".order", 0, 24);
      var slot = rank + "/" + order;
      if (occupiedSlots.has(slot)) {
        fail(field + " duplicates rank/order slot " + slot);
      }
      occupiedSlots.add(slot);

      config.nodes.push({
        id: id,
        label: requireString(rawNode.label, field + ".label", 120),
        rank: rank,
        order: order,
        kind: kind,
        sourceIndex: index
      });
    });

    var occupiedEdges = new Set();
    raw.edges.forEach(function validateEdge(rawEdge, index) {
      var field = "edges[" + index + "]";
      if (!isObject(rawEdge)) {
        fail(field + " must be an object");
      }

      var from = requireString(rawEdge.from, field + ".from", 48);
      var to = requireString(rawEdge.to, field + ".to", 48);
      if (!NODE_ID_PATTERN.test(from) || !NODE_ID_PATTERN.test(to)) {
        fail(field + " must reference lowercase ASCII node ids");
      }
      if (!knownIds.has(from) || !knownIds.has(to)) {
        fail(field + " references an unknown node");
      }
      if (from === to) {
        fail(field + " self-references are not supported");
      }

      var label = "";
      if (rawEdge.label !== undefined) {
        label = requireString(rawEdge.label, field + ".label", 64);
      }
      var lane =
        rawEdge.lane === undefined
          ? 0
          : requireInteger(rawEdge.lane, field + ".lane", -2, 2);
      var dashed = rawEdge.dashed === undefined ? false : rawEdge.dashed;
      if (typeof dashed !== "boolean") {
        fail(field + ".dashed must be a boolean");
      }
      var edgeSlot = from + ">" + to + ":" + lane;
      if (occupiedEdges.has(edgeSlot)) {
        fail(field + " duplicates edge/lane " + edgeSlot);
      }
      occupiedEdges.add(edgeSlot);

      config.edges.push({
        from: from,
        to: to,
        label: label,
        lane: lane,
        dashed: dashed,
        sourceIndex: index
      });
    });

    return config;
  }

  function parseConfig() {
    var dataElement = document.getElementById("diagram-data");
    if (!dataElement) {
      fail("diagram-data script is missing");
    }

    var raw;
    try {
      raw = JSON.parse(dataElement.textContent);
    } catch (error) {
      fail("diagram-data contains invalid JSON");
    }
    return validateConfig(raw);
  }

  function setDimensions(config) {
    var width = config.width + "px";
    var height = config.height + "px";
    var root = document.documentElement;

    root.style.setProperty("--diagram-width", width);
    root.style.setProperty("--diagram-height", height);
    root.style.width = width;
    root.style.height = height;
    document.body.style.width = width;
    document.body.style.height = height;
    main.style.width = width;
    main.style.height = height;

    var pageStyle = document.createElement("style");
    pageStyle.setAttribute("data-lesson-diagram-page", "");
    pageStyle.textContent =
      "@page { size: " + width + " " + height + "; margin: 0; }";
    document.head.appendChild(pageStyle);
  }

  function groupByRank(nodes) {
    var groupsByRank = new Map();
    nodes.forEach(function addToRank(node) {
      if (!groupsByRank.has(node.rank)) {
        groupsByRank.set(node.rank, []);
      }
      groupsByRank.get(node.rank).push(node);
    });

    return Array.from(groupsByRank.entries())
      .sort(function sortRanks(left, right) {
        return left[0] - right[0];
      })
      .map(function sortRank(entry) {
        entry[1].sort(function sortNodes(left, right) {
          return (
            left.order - right.order ||
            left.sourceIndex - right.sourceIndex ||
            left.id.localeCompare(right.id)
          );
        });
        return { rank: entry[0], nodes: entry[1] };
      });
  }

  function clamp(minimum, value, maximum) {
    return Math.max(minimum, Math.min(value, maximum));
  }

  function evenPosition(index, count, start, end) {
    if (count === 1) {
      return (start + end) / 2;
    }
    return start + ((end - start) * index) / (count - 1);
  }

  function centeredSlotPosition(index, count, start, end) {
    return start + ((end - start) * (index + 0.5)) / count;
  }

  function calculateLayout(config, groups) {
    var maximumRankSize = groups.reduce(function maximum(current, group) {
      return Math.max(current, group.nodes.length);
    }, 1);
    var rankCount = groups.length;
    var nodeWidth;
    var naturalNodeWidth;

    if (config.direction === "top-down") {
      naturalNodeWidth = (config.width - 48) / maximumRankSize - 18;
      if (naturalNodeWidth < 112) {
        fail("a top-down rank has too many nodes for this canvas");
      }
      if (
        rankCount > 1 &&
        (config.height - 140) / (rankCount - 1) < 130
      ) {
        fail("top-down layout has too many ranks for this canvas");
      }
      nodeWidth = clamp(112, naturalNodeWidth, 190);
    } else {
      naturalNodeWidth = (config.width - 60) / rankCount - 26;
      if (naturalNodeWidth < 122) {
        fail("left-right layout has too many ranks for this canvas");
      }
      if ((config.height - 68) / maximumRankSize < 96) {
        fail("a left-right rank has too many nodes for this canvas");
      }
      nodeWidth = clamp(122, naturalNodeWidth, 190);
    }

    var positions = new Map();
    groups.forEach(function positionRank(group, rankIndex) {
      group.nodes.forEach(function positionNode(node, nodeIndex) {
        var x;
        var y;
        if (config.direction === "top-down") {
          x = centeredSlotPosition(
            nodeIndex,
            group.nodes.length,
            28,
            config.width - 28
          );
          y = evenPosition(rankIndex, rankCount, 70, config.height - 70);
        } else {
          x = evenPosition(
            rankIndex,
            rankCount,
            nodeWidth / 2 + 34,
            config.width - nodeWidth / 2 - 34
          );
          y = centeredSlotPosition(
            nodeIndex,
            group.nodes.length,
            34,
            config.height - 34
          );
        }
        positions.set(node.id, {
          x: x,
          y: y,
          width: nodeWidth
        });
      });
    });

    return positions;
  }

  function hashString(value) {
    var hash = 2166136261;
    for (var index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function tiltClass(id) {
    var variants = ["tilt-left", "tilt-right", "tilt-soft"];
    return variants[hashString(id) % variants.length];
  }

  function createNodes(config, positions) {
    var layer = document.createElement("div");
    layer.className = "diagram-node-layer";
    layer.setAttribute("role", "list");
    layer.setAttribute("aria-label", "Diagram nodes");

    var elements = new Map();
    config.nodes.forEach(function createNode(node) {
      var position = positions.get(node.id);
      var element = document.createElement("section");
      var label = document.createElement("span");

      element.className =
        "diagram-node kind-" + node.kind + " " + tiltClass(node.id);
      element.dataset.nodeId = node.id;
      element.dataset.rank = String(node.rank);
      element.setAttribute("role", "listitem");
      element.style.left = position.x + "px";
      element.style.top = position.y + "px";
      element.style.setProperty("--node-width", position.width + "px");

      label.textContent = node.label;
      element.appendChild(label);
      layer.appendChild(element);
      elements.set(node.id, element);
    });

    main.appendChild(layer);
    return elements;
  }

  function svgElement(name, attributes) {
    var element = document.createElementNS(SVG_NS, name);
    Object.keys(attributes || {}).forEach(function setAttribute(name) {
      element.setAttribute(name, String(attributes[name]));
    });
    return element;
  }

  function createConnectorCanvas(config) {
    var svg = svgElement("svg", {
      class: "diagram-connectors",
      viewBox: "0 0 " + config.width + " " + config.height,
      width: config.width,
      height: config.height,
      role: "img",
      "aria-label": "Connections between diagram nodes",
      preserveAspectRatio: "xMidYMid meet"
    });
    var definitions = svgElement("defs");
    var marker = svgElement("marker", {
      id: "diagram-arrow",
      markerWidth: 11,
      markerHeight: 11,
      refX: 9,
      refY: 5.5,
      orient: "auto",
      markerUnits: "userSpaceOnUse",
      viewBox: "0 0 11 11"
    });
    var arrow = svgElement("path", {
      class: "diagram-arrowhead",
      d: "M 0.7 0.8 L 9.1 5.5 L 0.7 10.2 L 2.7 5.5 Z"
    });

    marker.appendChild(arrow);
    definitions.appendChild(marker);
    svg.appendChild(definitions);
    main.insertBefore(svg, main.firstChild);
    return svg;
  }

  function elementMetrics(element, position) {
    return {
      x: position.x,
      y: position.y,
      width: element.offsetWidth,
      height: element.offsetHeight
    };
  }

  function cubicPoint(start, controlOne, controlTwo, end, progress) {
    var inverse = 1 - progress;
    return {
      x:
        inverse * inverse * inverse * start.x +
        3 * inverse * inverse * progress * controlOne.x +
        3 * inverse * progress * progress * controlTwo.x +
        progress * progress * progress * end.x,
      y:
        inverse * inverse * inverse * start.y +
        3 * inverse * inverse * progress * controlOne.y +
        3 * inverse * progress * progress * controlTwo.y +
        progress * progress * progress * end.y
    };
  }

  function routeVertical(source, target, lane, wobble) {
    var direction = target.y >= source.y ? 1 : -1;
    var labelProgress = direction > 0 ? 0.64 : 0.36;
    var lowerNode = source.y >= target.y ? source : target;
    var upperNode = source.y >= target.y ? target : source;
    var branchDirection = Math.sign(lowerNode.x - upperNode.x);
    var laneOffset = lane * 23;
    var start = {
      x: source.x + laneOffset,
      y: source.y + direction * source.height / 2
    };
    var end = {
      x: target.x + laneOffset,
      y: target.y - direction * target.height / 2
    };
    var delta = end.y - start.y;
    var controlOne = {
      x: start.x + lane * 5 + wobble,
      y: start.y + delta * 0.46
    };
    var controlTwo = {
      x: end.x + lane * 5 - wobble,
      y: end.y - delta * 0.46
    };
    var label = cubicPoint(
      start,
      controlOne,
      controlTwo,
      end,
      labelProgress
    );
    label.x += branchDirection * 22;
    if (lane !== 0) {
      label.x += lane * 21;
      label.y += lane * 15;
    } else {
      label.x += wobble >= 0 ? 10 : -10;
    }
    return {
      path:
        "M " +
        start.x +
        " " +
        start.y +
        " C " +
        controlOne.x +
        " " +
        controlOne.y +
        ", " +
        controlTwo.x +
        " " +
        controlTwo.y +
        ", " +
        end.x +
        " " +
        end.y,
      label: label
    };
  }

  function routeHorizontal(source, target, lane, wobble) {
    var direction = target.x >= source.x ? 1 : -1;
    var labelProgress = direction > 0 ? 0.64 : 0.36;
    var laneOffset = lane * 18;
    var start = {
      x: source.x + direction * source.width / 2,
      y: source.y + laneOffset
    };
    var end = {
      x: target.x - direction * target.width / 2,
      y: target.y + laneOffset
    };
    var delta = end.x - start.x;
    var controlOne = {
      x: start.x + delta * 0.46,
      y: start.y + lane * 4 + wobble
    };
    var controlTwo = {
      x: end.x - delta * 0.46,
      y: end.y + lane * 4 - wobble
    };
    var label = cubicPoint(
      start,
      controlOne,
      controlTwo,
      end,
      labelProgress
    );
    if (lane !== 0) {
      label.x += lane * 15;
      label.y += lane * 17;
    } else {
      label.y += wobble >= 0 ? 10 : -10;
    }
    return {
      path:
        "M " +
        start.x +
        " " +
        start.y +
        " C " +
        controlOne.x +
        " " +
        controlOne.y +
        ", " +
        controlTwo.x +
        " " +
        controlTwo.y +
        ", " +
        end.x +
        " " +
        end.y,
      label: label
    };
  }

  function createRoute(config, edge, sourceNode, targetNode, metrics) {
    var source = metrics.get(edge.from);
    var target = metrics.get(edge.to);
    var sameRank = sourceNode.rank === targetNode.rank;
    var vertical =
      config.direction === "top-down" ? !sameRank : sameRank;
    var wobbleSeed = hashString(
      edge.from + ">" + edge.to + ":" + edge.sourceIndex
    );
    var wobble = (wobbleSeed % 7) - 3;

    return vertical
      ? routeVertical(source, target, edge.lane, wobble)
      : routeHorizontal(source, target, edge.lane, wobble);
  }

  function createConnectors(config, positions, elements) {
    var svg = createConnectorCanvas(config);
    var nodeById = new Map(
      config.nodes.map(function indexNode(node) {
        return [node.id, node];
      })
    );
    var metrics = new Map();
    config.nodes.forEach(function measureNode(node) {
      metrics.set(
        node.id,
        elementMetrics(elements.get(node.id), positions.get(node.id))
      );
    });

    config.edges.forEach(function createEdge(edge) {
      var route = createRoute(
        config,
        edge,
        nodeById.get(edge.from),
        nodeById.get(edge.to),
        metrics
      );
      var classes = edge.dashed ? " is-dashed" : "";
      var echo = svgElement("path", {
        class: "diagram-edge-echo" + classes,
        d: route.path,
        "aria-hidden": "true"
      });
      var path = svgElement("path", {
        class: "diagram-edge" + classes,
        d: route.path,
        "marker-end": "url(#diagram-arrow)",
        "aria-hidden": "true"
      });

      svg.appendChild(echo);
      svg.appendChild(path);

      if (edge.label) {
        var label = svgElement("text", {
          class: "diagram-edge-label",
          x: route.label.x,
          y: route.label.y
        });
        label.textContent = edge.label;
        svg.appendChild(label);
      }
    });
  }

  function render(config) {
    setDimensions(config);

    if (config.title) {
      document.title = config.title;
      document.getElementById("diagram-title").textContent = config.title;
    }

    var groups = groupByRank(config.nodes);
    var positions = calculateLayout(config, groups);
    var elements = createNodes(config, positions);
    createConnectors(config, positions, elements);
  }

  function showError(error) {
    Array.from(
      main.querySelectorAll(
        ".diagram-node-layer, .diagram-connectors, .diagram-error"
      )
    ).forEach(function removeRenderedElement(element) {
      element.remove();
    });

    var errorBox = document.createElement("section");
    var heading = document.createElement("strong");
    var message = document.createElement("span");
    var errorMessage =
      error && error.message ? error.message : "Unknown rendering error";
    errorBox.className = "diagram-error";
    errorBox.setAttribute("role", "alert");
    heading.textContent = "Diagram render error";
    message.textContent = errorMessage;
    errorBox.appendChild(heading);
    errorBox.appendChild(message);
    main.appendChild(errorBox);
    main.dataset.renderError = errorMessage;
    main.dataset.renderState = "error";
  }

  try {
    render(parseConfig());
    main.dataset.renderState = "ready";
  } catch (error) {
    showError(error);
  }
})();
