import { computed, ref } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, RouterLink } from "vue-router";
import { api, jsonInit } from "@/api/http";
import StatusBadge from "@/components/StatusBadge.vue";
import VersionReviewWorkspace from "@/components/VersionReviewWorkspace.vue";
import MaterialComparisonWorkspace from "@/components/MaterialComparisonWorkspace.vue";
import ReviewCopilot from "@/components/ReviewCopilot.vue";
const route = useRoute();
const queryClient = useQueryClient();
const reportId = Number(route.params.reportId);
const mode = ref("edit");
const sideTab = ref("evidence");
const selected = ref(null);
const discussionScope = ref(null);
const saveState = ref("已保存");
const incrementOpen = ref(false);
const comparisonOpen = ref(false);
const versionOpen = ref(Boolean(route.query.version));
const updateReason = ref("");
const incrementalFiles = ref();
const incrementalMaterialIds = ref([]);
const sourceComparisonId = ref(null);
const report = useQuery({
    queryKey: ["report", reportId],
    queryFn: () => api(`/api/reports/${reportId}`),
});
const materials = useQuery({
    queryKey: ["materials"],
    queryFn: () => api("/api/materials"),
    enabled: incrementOpen,
});
const qaIssues = computed(() => report.data.value?.qa_issues || []);
const currentDetails = computed(() => selected.value);
const discussionTarget = computed(() => discussionScope.value || {
    artifactType: "report_title",
    objectId: "",
    current: { title: report.data.value?.title || "" },
});
function choose(sentence) {
    selected.value = sentence;
    discussionScope.value = {
        artifactType: "sentence",
        objectId: sentence.id,
        current: {
            content: sentence.content,
            source_refs: {
                fact_ids: sentence.fact_ids || [],
                inference_ids: sentence.inference_ids || [],
            },
        },
    };
    if (mode.value === "trace")
        sideTab.value = "evidence";
}
function discussTitle() {
    discussionScope.value = {
        artifactType: "report_title",
        objectId: "",
        current: { title: report.data.value?.title || "" },
    };
    sideTab.value = "discuss";
}
function discussSection(section) {
    discussionScope.value = {
        artifactType: "section_title",
        objectId: section.title,
        current: { title: section.title },
    };
    sideTab.value = "discuss";
}
function comparisonHandoff(payload) {
    incrementalMaterialIds.value = payload.material_ids || [];
    sourceComparisonId.value = payload.comparison_id || null;
    updateReason.value = payload.update_reason || "";
    comparisonOpen.value = false;
    incrementOpen.value = true;
}
async function saveTitle(event) {
    const value = event.target.innerText.trim();
    if (!value || value === report.data.value?.title)
        return;
    saveState.value = "正在保存";
    try {
        await api(`/api/reports/${reportId}`, jsonInit("PUT", { title: value }));
        if (report.data.value)
            report.data.value.title = value;
        saveState.value = "已保存";
    }
    catch {
        saveState.value = "保存失败";
    }
}
async function saveSection(section, index, event) {
    const visible = event.target.innerText.trim();
    if (!visible)
        return;
    saveState.value = "正在保存";
    try {
        const result = await api(`/api/reports/${reportId}/sections`, jsonInit("PUT", {
            old_title: section.title,
            new_title: visible,
            section_index: index + 1,
        }));
        section.title = result.title;
        section.display_title = result.display_title;
        saveState.value = "已保存";
    }
    catch {
        saveState.value = "保存失败";
    }
}
async function saveSentence(sentence, event) {
    const value = event.target.innerText.trim();
    if (value === sentence.content)
        return;
    saveState.value = "正在保存";
    try {
        const result = await api(`/api/reports/${reportId}/sentences/${sentence.id}`, jsonInit("PUT", { content: value }));
        sentence.content = result.content || value;
        saveState.value = "已保存";
    }
    catch {
        saveState.value = "保存失败";
    }
}
async function finalize(force = false) {
    try {
        await api(`/api/reports/${reportId}/finalize`, jsonInit("POST", { force }));
        await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
    }
    catch (error) {
        if (error?.payload?.error === "QA_BLOCKING" &&
            confirm("仍有阻断性质量问题。确认在知情情况下完成审核？"))
            return finalize(true);
        alert(error.message || "审核失败");
    }
}
async function createIncremental() {
    const form = new FormData();
    form.set("update_reason", updateReason.value);
    form.set("existing_material_ids", incrementalMaterialIds.value.join(","));
    if (sourceComparisonId.value)
        form.set("source_comparison_id", String(sourceComparisonId.value));
    for (const file of incrementalFiles.value?.files || [])
        form.append("files", file);
    try {
        const result = await api(`/api/reports/${reportId}/incremental/tasks`, { method: "POST", body: form });
        location.href = `/tasks/${result.task_id}`;
    }
    catch (error) {
        alert(error.message || "创建增量任务失败");
    }
}
async function reviewApplied() {
    await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
    versionOpen.value = false;
}
async function createSnapshot() {
    try {
        await api(`/api/reports/${reportId}/versions`, jsonInit("POST", { change_summary: "手动生成版本快照" }));
        await queryClient.invalidateQueries({ queryKey: ["report", reportId] });
    }
    catch (error) {
        alert(error.message || "生成版本快照失败");
    }
}
function sourceClass(sentence) {
    const fact = sentence.sources?.length ||
        sentence.facts?.length ||
        sentence.fact_ids?.length ||
        sentence.source_level === "MATERIAL_FACT";
    const inference = sentence.inferences?.length ||
        sentence.inference_ids?.length ||
        ["MATERIAL_INFERENCE", "EXTERNAL_INFORMATION"].includes(sentence.source_level);
    return fact && inference
        ? "mixed"
        : inference
            ? "inference"
            : fact
                ? "fact"
                : "";
}
function confidence(item) {
    return ({ high: "高", medium: "中", low: "低" }[String(item.confidence_level || "").toLowerCase()] || "需人工复核");
}
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['toolbar-left']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-left']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-left']} */ ;
/** @type {__VLS_StyleScopedClasses['segmented']} */ ;
/** @type {__VLS_StyleScopedClasses['segmented']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['toc-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['toc-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['toc-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['toc-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-key']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-key']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-key']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-key']} */ ;
/** @type {__VLS_StyleScopedClasses['paper']} */ ;
/** @type {__VLS_StyleScopedClasses['paper']} */ ;
/** @type {__VLS_StyleScopedClasses['paper']} */ ;
/** @type {__VLS_StyleScopedClasses['paper']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['mode-trace']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['fact']} */ ;
/** @type {__VLS_StyleScopedClasses['mode-trace']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['inference']} */ ;
/** @type {__VLS_StyleScopedClasses['mode-trace']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['mixed']} */ ;
/** @type {__VLS_StyleScopedClasses['mode-trace']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['context-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-card']} */ ;
/** @type {__VLS_StyleScopedClasses['evidence-card']} */ ;
/** @type {__VLS_StyleScopedClasses['qa-item']} */ ;
/** @type {__VLS_StyleScopedClasses['editor-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['paper']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['editor-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['context-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-strip']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['toc-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['editor-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['paper']} */ ;
/** @type {__VLS_StyleScopedClasses['editor-toolbar']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-left']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-strip']} */ ;
if (__VLS_ctx.report.data.value) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "report-editor" },
        ...{ class: (`mode-${__VLS_ctx.mode}`) },
    });
    /** @type {__VLS_StyleScopedClasses['report-editor']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
        ...{ class: "editor-toolbar" },
    });
    /** @type {__VLS_StyleScopedClasses['editor-toolbar']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "toolbar-left" },
    });
    /** @type {__VLS_StyleScopedClasses['toolbar-left']} */ ;
    if (__VLS_ctx.report.data.value.task_id) {
        let __VLS_0;
        /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
        RouterLink;
        // @ts-ignore
        const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
            to: (`/tasks/${__VLS_ctx.report.data.value.task_id}`),
            ...{ class: "btn tertiary" },
        }));
        const __VLS_2 = __VLS_1({
            to: (`/tasks/${__VLS_ctx.report.data.value.task_id}`),
            ...{ class: "btn tertiary" },
        }, ...__VLS_functionalComponentArgsRest(__VLS_1));
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        const { default: __VLS_5 } = __VLS_3.slots;
        // @ts-ignore
        [report, report, report, mode,];
        var __VLS_3;
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.report.data.value.title);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.saveState);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "toolbar-actions" },
    });
    /** @type {__VLS_StyleScopedClasses['toolbar-actions']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "segmented" },
    });
    /** @type {__VLS_StyleScopedClasses['segmented']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.mode = 'edit');
                // @ts-ignore
                [report, mode, saveState,];
            } },
        ...{ class: ({ active: __VLS_ctx.mode === 'edit' }) },
    });
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.mode = 'trace');
                // @ts-ignore
                [mode, mode,];
            } },
        ...{ class: ({ active: __VLS_ctx.mode === 'trace' }) },
    });
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    const __VLS_6 = StatusBadge;
    // @ts-ignore
    const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
        stage: (__VLS_ctx.report.data.value.status),
    }));
    const __VLS_8 = __VLS_7({
        stage: (__VLS_ctx.report.data.value.status),
    }, ...__VLS_functionalComponentArgsRest(__VLS_7));
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.versionOpen = true);
                // @ts-ignore
                [report, mode, versionOpen,];
            } },
        ...{ class: "btn" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.comparisonOpen = true);
                // @ts-ignore
                [comparisonOpen,];
            } },
        ...{ class: "btn" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.incrementOpen = !__VLS_ctx.incrementOpen);
                // @ts-ignore
                [incrementOpen, incrementOpen,];
            } },
        ...{ class: "btn" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.finalize(false));
                // @ts-ignore
                [finalize,];
            } },
        ...{ class: "btn primary" },
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.a, __VLS_intrinsics.a)({
        ...{ class: "btn" },
        href: (`/api/reports/${__VLS_ctx.reportId}/export`),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    if (__VLS_ctx.incrementOpen) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
            ...{ class: "drawer-strip" },
        });
        /** @type {__VLS_StyleScopedClasses['drawer-strip']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.select, __VLS_intrinsics.select)({
            value: (__VLS_ctx.incrementalMaterialIds),
            multiple: true,
            size: "4",
        });
        for (const [item] of __VLS_vFor((__VLS_ctx.materials.data.value || []))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.option, __VLS_intrinsics.option)({
                key: (item.id),
                value: (item.id),
            });
            (item.filename);
            // @ts-ignore
            [incrementOpen, reportId, incrementalMaterialIds, materials,];
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.input)({
            ref: "incrementalFiles",
            type: "file",
            multiple: true,
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.textarea, __VLS_intrinsics.textarea)({
            value: (__VLS_ctx.updateReason),
            rows: "3",
            placeholder: "本次更新说明",
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "button-row" },
        });
        /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.createIncremental) },
            ...{ class: "btn primary" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.report.data.value))
                        throw 0;
                    if (!(__VLS_ctx.incrementOpen))
                        throw 0;
                    return (__VLS_ctx.incrementOpen = false);
                    // @ts-ignore
                    [incrementOpen, updateReason, createIncremental,];
                } },
            ...{ class: "btn tertiary" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
    }
    if (__VLS_ctx.versionOpen) {
        const __VLS_11 = VersionReviewWorkspace;
        // @ts-ignore
        const __VLS_12 = __VLS_asFunctionalComponent1(__VLS_11, new __VLS_11({
            ...{ 'onClose': {} },
            ...{ 'onApplied': {} },
            ...{ 'onSnapshot': {} },
            reportId: (__VLS_ctx.reportId),
            reportTitle: (__VLS_ctx.report.data.value.title),
            versions: (__VLS_ctx.report.data.value.versions || []),
            initialVersion: (__VLS_ctx.route.query.version ? Number(__VLS_ctx.route.query.version) : null),
        }));
        const __VLS_13 = __VLS_12({
            ...{ 'onClose': {} },
            ...{ 'onApplied': {} },
            ...{ 'onSnapshot': {} },
            reportId: (__VLS_ctx.reportId),
            reportTitle: (__VLS_ctx.report.data.value.title),
            versions: (__VLS_ctx.report.data.value.versions || []),
            initialVersion: (__VLS_ctx.route.query.version ? Number(__VLS_ctx.route.query.version) : null),
        }, ...__VLS_functionalComponentArgsRest(__VLS_12));
        let __VLS_16;
        const __VLS_17 = {
            /** @type {typeof __VLS_16.close} */
            onClose: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                if (!(__VLS_ctx.versionOpen))
                    throw 0;
                return (__VLS_ctx.versionOpen = false);
                // @ts-ignore
                [report, report, versionOpen, versionOpen, reportId, route, route,];
            },
        };
        const __VLS_18 = {
            /** @type {typeof __VLS_16.applied} */
            onApplied: (__VLS_ctx.reviewApplied),
        };
        const __VLS_19 = {
            /** @type {typeof __VLS_16.snapshot} */
            onSnapshot: (__VLS_ctx.createSnapshot),
        };
        var __VLS_14;
        var __VLS_15;
    }
    if (__VLS_ctx.comparisonOpen) {
        const __VLS_20 = MaterialComparisonWorkspace;
        // @ts-ignore
        const __VLS_21 = __VLS_asFunctionalComponent1(__VLS_20, new __VLS_20({
            ...{ 'onClose': {} },
            ...{ 'onHandoff': {} },
            reportId: (__VLS_ctx.reportId),
            versions: (__VLS_ctx.report.data.value.versions || []),
        }));
        const __VLS_22 = __VLS_21({
            ...{ 'onClose': {} },
            ...{ 'onHandoff': {} },
            reportId: (__VLS_ctx.reportId),
            versions: (__VLS_ctx.report.data.value.versions || []),
        }, ...__VLS_functionalComponentArgsRest(__VLS_21));
        let __VLS_25;
        const __VLS_26 = {
            /** @type {typeof __VLS_25.close} */
            onClose: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                if (!(__VLS_ctx.comparisonOpen))
                    throw 0;
                return (__VLS_ctx.comparisonOpen = false);
                // @ts-ignore
                [report, comparisonOpen, comparisonOpen, reportId, reviewApplied, createSnapshot,];
            },
        };
        const __VLS_27 = {
            /** @type {typeof __VLS_25.handoff} */
            onHandoff: (__VLS_ctx.comparisonHandoff),
        };
        var __VLS_23;
        var __VLS_24;
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "editor-grid" },
    });
    /** @type {__VLS_StyleScopedClasses['editor-grid']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
        ...{ class: "toc-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['toc-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
    for (const [section, index] of __VLS_vFor((__VLS_ctx.report.data.value.sections))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.a, __VLS_intrinsics.a)({
            key: (section.title),
            href: (`#section-${index}`),
        });
        (section.display_title || section.title);
        // @ts-ignore
        [report, comparisonHandoff,];
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "trace-key" },
    });
    /** @type {__VLS_StyleScopedClasses['trace-key']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "fact" },
    });
    /** @type {__VLS_StyleScopedClasses['fact']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "inference" },
    });
    /** @type {__VLS_StyleScopedClasses['inference']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "mixed" },
    });
    /** @type {__VLS_StyleScopedClasses['mixed']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.main, __VLS_intrinsics.main)({
        ...{ class: "paper" },
    });
    /** @type {__VLS_StyleScopedClasses['paper']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({
        ...{ onClick: (__VLS_ctx.discussTitle) },
        ...{ onBlur: (__VLS_ctx.saveTitle) },
        contenteditable: true,
        spellcheck: "false",
    });
    (__VLS_ctx.report.data.value.title);
    for (const [section, sectionIndex] of __VLS_vFor((__VLS_ctx.report.data.value.sections))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
            key: (section.title),
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.report.data.value))
                        throw 0;
                    return (__VLS_ctx.discussSection(section));
                    // @ts-ignore
                    [report, report, discussTitle, saveTitle, discussSection,];
                } },
            ...{ onBlur: (...[$event]) => {
                    if (!(__VLS_ctx.report.data.value))
                        throw 0;
                    return (__VLS_ctx.saveSection(section, sectionIndex, $event));
                    // @ts-ignore
                    [saveSection,];
                } },
            id: (`section-${sectionIndex}`),
            contenteditable: true,
            spellcheck: "false",
        });
        (section.display_title || section.title);
        for (const [paragraph, paragraphIndex] of __VLS_vFor((section.paragraphs))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                key: (paragraphIndex),
            });
            for (const [sentence] of __VLS_vFor((paragraph.sentences))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                    key: (sentence.id),
                });
                if (sentence.source_level === 'SUBHEADING') {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({
                        ...{ onClick: (...[$event]) => {
                                if (!(__VLS_ctx.report.data.value))
                                    throw 0;
                                if (!(sentence.source_level === 'SUBHEADING'))
                                    throw 0;
                                return (__VLS_ctx.choose(sentence));
                                // @ts-ignore
                                [choose,];
                            } },
                        ...{ onBlur: (...[$event]) => {
                                if (!(__VLS_ctx.report.data.value))
                                    throw 0;
                                if (!(sentence.source_level === 'SUBHEADING'))
                                    throw 0;
                                return (__VLS_ctx.saveSentence(sentence, $event));
                                // @ts-ignore
                                [saveSentence,];
                            } },
                        ...{ class: "sentence subheading" },
                        ...{ class: ([
                                __VLS_ctx.sourceClass(sentence),
                                { selected: __VLS_ctx.selected?.id === sentence.id },
                            ]) },
                        contenteditable: true,
                        spellcheck: "false",
                    });
                    /** @type {__VLS_StyleScopedClasses['sentence']} */ ;
                    /** @type {__VLS_StyleScopedClasses['subheading']} */ ;
                    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
                    (sentence.display_content || sentence.content);
                }
                // @ts-ignore
                [sourceClass, selected,];
            }
            if (paragraph.sentences.some((sentence) => sentence.source_level !== 'SUBHEADING')) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                for (const [sentence] of __VLS_vFor((paragraph.sentences.filter((sentence) => sentence.source_level !== 'SUBHEADING')))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ onClick: (...[$event]) => {
                                if (!(__VLS_ctx.report.data.value))
                                    throw 0;
                                if (!(paragraph.sentences.some((sentence) => sentence.source_level !== 'SUBHEADING')))
                                    throw 0;
                                return (__VLS_ctx.choose(sentence));
                                // @ts-ignore
                                [choose,];
                            } },
                        ...{ onBlur: (...[$event]) => {
                                if (!(__VLS_ctx.report.data.value))
                                    throw 0;
                                if (!(paragraph.sentences.some((sentence) => sentence.source_level !== 'SUBHEADING')))
                                    throw 0;
                                return (__VLS_ctx.saveSentence(sentence, $event));
                                // @ts-ignore
                                [saveSentence,];
                            } },
                        key: (sentence.id),
                        ...{ class: "sentence" },
                        ...{ class: ([
                                __VLS_ctx.sourceClass(sentence),
                                {
                                    selected: __VLS_ctx.selected?.id === sentence.id,
                                    excluded: sentence.selected === false,
                                },
                            ]) },
                        contenteditable: true,
                        spellcheck: "false",
                    });
                    /** @type {__VLS_StyleScopedClasses['sentence']} */ ;
                    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
                    /** @type {__VLS_StyleScopedClasses['excluded']} */ ;
                    (sentence.content);
                    // @ts-ignore
                    [sourceClass, selected,];
                }
            }
            // @ts-ignore
            [];
        }
        // @ts-ignore
        [];
    }
    __VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
        ...{ class: "context-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['context-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "tabs" },
    });
    /** @type {__VLS_StyleScopedClasses['tabs']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.sideTab = 'evidence');
                // @ts-ignore
                [sideTab,];
            } },
        ...{ class: "tab" },
        ...{ class: ({ active: __VLS_ctx.sideTab === 'evidence' }) },
    });
    /** @type {__VLS_StyleScopedClasses['tab']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.sideTab = 'qa');
                // @ts-ignore
                [sideTab, sideTab,];
            } },
        ...{ class: "tab" },
        ...{ class: ({ active: __VLS_ctx.sideTab === 'qa' }) },
    });
    /** @type {__VLS_StyleScopedClasses['tab']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                return (__VLS_ctx.sideTab = 'discuss');
                // @ts-ignore
                [sideTab, sideTab,];
            } },
        ...{ class: "tab" },
        ...{ class: ({ active: __VLS_ctx.sideTab === 'discuss' }) },
    });
    /** @type {__VLS_StyleScopedClasses['tab']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    if (__VLS_ctx.sideTab === 'evidence') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "panel-body" },
        });
        /** @type {__VLS_StyleScopedClasses['panel-body']} */ ;
        if (__VLS_ctx.currentDetails) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                ...{ class: "selected-quote" },
            });
            /** @type {__VLS_StyleScopedClasses['selected-quote']} */ ;
            (__VLS_ctx.currentDetails.content);
            for (const [source] of __VLS_vFor((__VLS_ctx.currentDetails.sources ||
                __VLS_ctx.currentDetails.facts ||
                []))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
                    key: (source.fact_id || source.id),
                    ...{ class: "evidence-card" },
                });
                /** @type {__VLS_StyleScopedClasses['evidence-card']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    ...{ class: "badge running" },
                });
                /** @type {__VLS_StyleScopedClasses['badge']} */ ;
                /** @type {__VLS_StyleScopedClasses['running']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (source.content);
                for (const [evidence] of __VLS_vFor((source.evidence || []))) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.blockquote, __VLS_intrinsics.blockquote)({
                        key: (evidence.quote),
                    });
                    (evidence.source_file);
                    (evidence.page ? ` 第 ${evidence.page} 页` : "");
                    __VLS_asFunctionalElement1(__VLS_intrinsics.br)({});
                    (evidence.quote);
                    // @ts-ignore
                    [sideTab, sideTab, currentDetails, currentDetails, currentDetails, currentDetails,];
                }
                // @ts-ignore
                [];
            }
            for (const [item] of __VLS_vFor((__VLS_ctx.currentDetails.inferences || []))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
                    key: (item.inference_id || item.id),
                    ...{ class: "evidence-card inference-card" },
                });
                /** @type {__VLS_StyleScopedClasses['evidence-card']} */ ;
                /** @type {__VLS_StyleScopedClasses['inference-card']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                    ...{ class: "badge success" },
                });
                /** @type {__VLS_StyleScopedClasses['badge']} */ ;
                /** @type {__VLS_StyleScopedClasses['success']} */ ;
                (__VLS_ctx.confidence(item));
                __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                (item.content);
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                (item.based_fact_ids?.join("、") || "待核验");
                if (item.reasoning_chain) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.blockquote, __VLS_intrinsics.blockquote)({});
                    (item.reasoning_chain);
                }
                // @ts-ignore
                [currentDetails, confidence,];
            }
            if (!(__VLS_ctx.currentDetails.sources?.length ||
                __VLS_ctx.currentDetails.facts?.length ||
                __VLS_ctx.currentDetails.inferences?.length)) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                    ...{ class: "empty" },
                });
                /** @type {__VLS_StyleScopedClasses['empty']} */ ;
                __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            }
        }
        else {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "empty" },
            });
            /** @type {__VLS_StyleScopedClasses['empty']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            (__VLS_ctx.mode === "trace"
                ? "查看对应事实、原文和分析推论。"
                : "点击正文后可查看依据。");
        }
    }
    else if (__VLS_ctx.sideTab === 'qa') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "panel-body" },
        });
        /** @type {__VLS_StyleScopedClasses['panel-body']} */ ;
        for (const [issue, index] of __VLS_vFor((__VLS_ctx.qaIssues))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                key: (index),
                ...{ class: "qa-item" },
            });
            /** @type {__VLS_StyleScopedClasses['qa-item']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge warning" },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
            /** @type {__VLS_StyleScopedClasses['warning']} */ ;
            (issue.type || "质量问题");
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (issue.note || issue.quote || issue.message);
            // @ts-ignore
            [mode, sideTab, currentDetails, currentDetails, currentDetails, qaIssues,];
        }
        if (!__VLS_ctx.qaIssues.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "empty" },
            });
            /** @type {__VLS_StyleScopedClasses['empty']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        }
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "panel-body" },
        });
        /** @type {__VLS_StyleScopedClasses['panel-body']} */ ;
        const __VLS_28 = ReviewCopilot;
        // @ts-ignore
        const __VLS_29 = __VLS_asFunctionalComponent1(__VLS_28, new __VLS_28({
            ...{ 'onApplied': {} },
            taskId: (__VLS_ctx.report.data.value.task_id),
            reportId: (__VLS_ctx.reportId),
            artifactType: (__VLS_ctx.discussionTarget.artifactType),
            objectId: (__VLS_ctx.discussionTarget.objectId),
            current: (__VLS_ctx.discussionTarget.current),
        }));
        const __VLS_30 = __VLS_29({
            ...{ 'onApplied': {} },
            taskId: (__VLS_ctx.report.data.value.task_id),
            reportId: (__VLS_ctx.reportId),
            artifactType: (__VLS_ctx.discussionTarget.artifactType),
            objectId: (__VLS_ctx.discussionTarget.objectId),
            current: (__VLS_ctx.discussionTarget.current),
        }, ...__VLS_functionalComponentArgsRest(__VLS_29));
        let __VLS_33;
        const __VLS_34 = {
            /** @type {typeof __VLS_33.applied} */
            onApplied: (...[$event]) => {
                if (!(__VLS_ctx.report.data.value))
                    throw 0;
                if (!!(__VLS_ctx.sideTab === 'evidence'))
                    throw 0;
                if (!!(__VLS_ctx.sideTab === 'qa'))
                    throw 0;
                return (__VLS_ctx.queryClient.invalidateQueries({ queryKey: ['report', __VLS_ctx.reportId] }));
                // @ts-ignore
                [report, reportId, reportId, qaIssues, discussionTarget, discussionTarget, discussionTarget, queryClient,];
            },
        };
        var __VLS_31;
        var __VLS_32;
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "loading-line" },
    });
    /** @type {__VLS_StyleScopedClasses['loading-line']} */ ;
}
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
