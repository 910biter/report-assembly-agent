import { computed } from "vue";
const props = defineProps();
const labels = { created: "待运行", parsing: "材料准备", dedup: "材料准备", material_analysis: "材料理解", planning: "分析规划", evidence: "证据提取", conflict: "冲突核验", analysis: "综合分析", writing: "报告生成", knowledge: "质量完善", review: "待审核", done: "已完成", failed: "异常", paused: "已暂停", queued: "排队中", running: "运行中", draft: "草稿", final: "已定稿" };
const cls = computed(() => props.stage === "failed" ? "danger" : ["done", "final", "review"].includes(props.stage || "") ? "success" : props.stage === "paused" ? "warning" : ["created", "draft"].includes(props.stage || "") ? "" : "running");
const __VLS_ctx = {
    ...{},
    ...{},
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "badge" },
    ...{ class: (__VLS_ctx.cls) },
});
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
(__VLS_ctx.labels[__VLS_ctx.stage || ""] || __VLS_ctx.stage || "未知");
// @ts-ignore
[cls, labels, stage, stage,];
const __VLS_export = (await import('vue')).defineComponent({
    __typeProps: {},
});
export default {};
