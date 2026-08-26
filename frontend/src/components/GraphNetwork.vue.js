import cytoscape from "cytoscape";
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
const props = defineProps();
const emit = defineEmits();
const host = ref(null);
let graph = null;
function render() {
    graph?.destroy();
    if (!host.value)
        return;
    const nodeKeys = new Set((props.nodes || []).map((node) => String(node.key || "")));
    const visible = new Set();
    const edges = (props.edges || [])
        .filter((edge) => {
        if (!edge.subject_key || !edge.target_key)
            return false;
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
            nodeRepulsion: () => 5200,
            idealEdgeLength: () => 115,
        },
        style: [
            {
                selector: "node",
                style: {
                    "background-color": "#eff5fb",
                    "border-color": "#245b9e",
                    "border-width": "1px",
                    label: "data(label)",
                    color: "#1f2329",
                    "font-size": "11px",
                    "text-wrap": "wrap",
                    "text-max-width": "88px",
                    "text-valign": "center",
                    "text-halign": "center",
                    width: "58px",
                    height: "58px",
                },
            },
            {
                selector: "edge",
                style: {
                    width: "1.2px",
                    "line-color": "#9db5cf",
                    "target-arrow-color": "#9db5cf",
                    "target-arrow-shape": "triangle",
                    "curve-style": "bezier",
                    label: "data(label)",
                    color: "#646a73",
                    "font-size": "10px",
                    "text-background-color": "#fff",
                    "text-background-opacity": 0.9,
                    "text-background-padding": "2px",
                },
            },
            {
                selector: ":selected",
                style: {
                    "background-color": "#245b9e",
                    "line-color": "#245b9e",
                    "target-arrow-color": "#245b9e",
                },
            },
        ],
    });
    graph.on("tap", "edge", (event) => emit("select", event.target.data("edge")));
}
watch(() => [props.nodes, props.edges], render, { deep: true, flush: "post" });
onMounted(render);
onBeforeUnmount(() => graph?.destroy());
const __VLS_ctx = {
    ...{},
    ...{},
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ref: "host",
    ...{ class: "graph-network" },
});
/** @type {__VLS_StyleScopedClasses['graph-network']} */ ;
const __VLS_export = (await import('vue')).defineComponent({
    __typeEmits: {},
    __typeProps: {},
});
export default {};
