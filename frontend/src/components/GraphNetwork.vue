<script setup lang="ts">
import cytoscape from "cytoscape";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = defineProps<{ nodes: any[]; edges: any[] }>();
const emit = defineEmits<{ select: [edge: any] }>();
const host = ref<HTMLElement | null>(null);
let graph: cytoscape.Core | null = null;

function token(name: string) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
}

function render() {
  graph?.destroy();
  if (!host.value) return;
  const nodeKeys = new Set(
    (props.nodes || []).map((node) => String(node.key || "")),
  );
  const visible = new Set<string>();
  const edges = (props.edges || [])
    .filter((edge) => {
      if (!edge.subject_key || !edge.target_key) return false;
      // The API contract should provide both endpoints. Keep visualization
      // resilient while a task is being rebuilt or against older projections.
      if (!nodeKeys.has(edge.subject_key) || !nodeKeys.has(edge.target_key))
        return false;
      visible.add(edge.subject_key);
      visible.add(edge.target_key);
      return true;
    })
    .slice(0, 80);
  const nodes = (props.nodes || [])
    .filter((node) => visible.has(node.key))
    .map((node) => ({
      data: {
        id: node.key,
        label: node.name,
        type: node.entity_type || "其他",
      },
    }));
  graph = cytoscape({
    container: host.value,
    elements: [
      ...nodes,
      ...edges.map((edge) => ({
        data: {
          id: edge.assertion_key,
          source: edge.subject_key,
          target: edge.target_key,
          label: edge.predicate,
          edge,
        },
      })),
    ],
    layout: {
      name: "cose",
      animate: false,
      padding: 24,
      nodeRepulsion: () => 12000,
      idealEdgeLength: () => 180,
      componentSpacing: 100,
      nodeOverlap: 30,
    },
    style: [
      {
        selector: "node",
        style: {
          "background-color": token("--graph-node-fill"),
          "border-color": token("--graph-node-border"),
          "border-width": "1px",
          label: "data(label)",
          color: token("--graph-node-text"),
          "font-size": "12px",
          "font-weight": 600,
          "text-wrap": "wrap",
          "text-max-width": "150px",
          "text-valign": "center",
          "text-halign": "center",
          "text-background-color": token("--graph-node-fill"),
          "text-background-opacity": 1,
          "text-background-padding": "5px",
          shape: "roundrectangle",
          width: "label",
          height: "label",
          padding: "12px",
        },
      },
      {
        selector: "edge",
        style: {
          width: "1.2px",
          "line-color": token("--graph-edge"),
          "target-arrow-color": token("--graph-edge"),
          "arrow-scale": 0.8,
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          label: "data(label)",
          color: token("--graph-edge-text"),
          "font-size": "11px",
          "font-weight": 600,
          "text-background-color": token("--graph-label-bg"),
          "text-background-opacity": 1,
          "text-background-padding": "4px",
          "text-border-color": token("--graph-label-border"),
          "text-border-width": 1,
          "text-border-opacity": 1,
        },
      },
      {
        selector: ":selected",
        style: {
          "background-color": token("--primary"),
          "line-color": token("--primary"),
          "target-arrow-color": token("--primary"),
        },
      },
    ],
  });
  graph.on("tap", "edge", (event) => emit("select", event.target.data("edge")));
}

watch(() => [props.nodes, props.edges], render, { deep: true, flush: "post" });
onMounted(render);
onBeforeUnmount(() => graph?.destroy());
</script>

<template><div ref="host" class="graph-network"></div></template>

<style scoped>
.graph-network {
  height: 500px;
  margin: 16px 0;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: var(--radius-control);
  background: var(--graph-canvas);
}
</style>
