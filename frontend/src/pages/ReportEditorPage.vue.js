import { computed, nextTick, ref, watch } from "vue";
import { useQuery, useQueryClient } from "@tanstack/vue-query";
import { useRoute, RouterLink } from "vue-router";
import { api, jsonInit } from "@/api/http";
import StatusBadge from "@/components/StatusBadge.vue";
import VersionReviewWorkspace from "@/components/VersionReviewWorkspace.vue";
import MaterialComparisonWorkspace from "@/components/MaterialComparisonWorkspace.vue";
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
const qaTargetSentenceId = ref(null);
const paperRef = ref(null);
const selectionAction = ref(null);
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
function allSentences() {
    return (report.data.value?.sections || []).flatMap((section) => (section.paragraphs || []).flatMap((paragraph) => paragraph.sentences || []));
}
function sentenceRefs(sentence) {
    return {
        fact_ids: [...new Set([
                ...(sentence.fact_ids || []),
                ...(sentence.sources || sentence.facts || []).map((item) => item.fact_id || item.id),
            ].filter(Boolean))],
        inference_ids: [...new Set([
                ...(sentence.inference_ids || []),
                ...(sentence.inferences || []).map((item) => item.inference_id || item.id),
            ].filter(Boolean))],
    };
}
function sentenceArtifact(sentence, quote = "") {
    return {
        artifact_type: "sentence",
        object_id: sentence.id,
        artifact_version: String(report.data.value?.versions?.[0]?.version_no || 1),
        current: { content: sentence.content, quote: quote || sentence.content, source_refs: sentenceRefs(sentence) },
        title: "正文句子",
    };
}
function paragraphArtifact(section, paragraph, paragraphIndex, quote = "") {
    const sentences = paragraph.sentences || [];
    const refs = sentences.map(sentenceRefs);
    const content = sentences.map((item) => item.content).filter(Boolean).join("");
    return {
        artifact_type: "paragraph",
        object_id: `${section.title}:${paragraphIndex + 1}`,
        artifact_version: String(report.data.value?.versions?.[0]?.version_no || 1),
        current: {
            section: section.title, paragraph: paragraphIndex + 1, content, quote: quote || content,
            sentence_ids: sentences.map((item) => item.id),
            source_refs: {
                fact_ids: [...new Set(refs.flatMap((item) => item.fact_ids))],
                inference_ids: [...new Set(refs.flatMap((item) => item.inference_ids))],
            },
        },
        title: `${section.display_title || section.title} · 第 ${paragraphIndex + 1} 段`,
    };
}
function dispatchAssistant(artifact, reference, append = false) {
    window.dispatchEvent(new CustomEvent("ira:assistant-focus", {
        detail: { taskId: report.data.value?.task_id, artifact, reference, append },
    }));
}
async function locateIssue(issue) {
    sideTab.value = "qa";
    const sentenceId = Number(issue?.sentence_id || 0);
    if (sentenceId) {
        qaTargetSentenceId.value = sentenceId;
        const sentence = allSentences().find((item) => Number(item.id) === sentenceId);
        if (sentence)
            selected.value = sentence;
        await nextTick();
        document.getElementById(`sentence-${sentenceId}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
        return;
    }
    const section = String(issue?.section || "");
    const sectionIndex = (report.data.value?.sections || []).findIndex((item) => item.title === section);
    if (sectionIndex >= 0) {
        await nextTick();
        document.getElementById(`section-${sectionIndex}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}
watch(() => report.data.value, async (value) => {
    if (!value)
        return;
    if (route.query.panel === "qa")
        sideTab.value = "qa";
    const sentenceId = Number(route.query.qa_sentence || 0);
    const section = String(route.query.qa_section || "");
    if (sentenceId || section)
        await locateIssue({ sentence_id: sentenceId || undefined, section });
}, { immediate: true });
function choose(sentence) {
    selected.value = sentence;
    const artifact = sentenceArtifact(sentence);
    discussionScope.value = {
        artifactType: artifact.artifact_type,
        objectId: artifact.object_id,
        current: artifact.current,
    };
    if (mode.value === "trace")
        sideTab.value = "evidence";
}
function discussParagraph(section, paragraph, paragraphIndex) {
    const artifact = paragraphArtifact(section, paragraph, paragraphIndex);
    discussionScope.value = { artifactType: artifact.artifact_type, objectId: artifact.object_id, current: artifact.current };
    dispatchAssistant(artifact);
}
function referenceSelectedSentence() {
    if (!currentDetails.value)
        return;
    const artifact = sentenceArtifact(currentDetails.value);
    dispatchAssistant(artifact, artifact, true);
}
function askAboutIssue(issue) {
    const sentence = allSentences().find((item) => Number(item.id) === Number(issue?.sentence_id || 0));
    const target = sentence ? sentenceArtifact(sentence) : undefined;
    const reference = {
        artifact_type: "qa_issue", object_id: String(issue?.sentence_id || issue?.section || ""),
        current: { section: issue?.section || "", quote: issue?.quote || "", note: issue?.note || issue?.message || "" },
        title: "质量问题",
    };
    dispatchAssistant(target, reference, true);
}
function captureSelection(event) {
    const selection = window.getSelection();
    const text = String(selection?.toString() || "").replace(/\s+/g, " ").trim();
    if (!selection || selection.rangeCount === 0 || text.length < 2 || !paperRef.value) {
        selectionAction.value = null;
        return;
    }
    const range = selection.getRangeAt(0);
    if (!paperRef.value.contains(range.commonAncestorContainer))
        return;
    const elements = [...paperRef.value.querySelectorAll(".sentence")]
        .filter((element) => { try {
        return range.intersectsNode(element);
    }
    catch {
        return false;
    } });
    const sentences = elements.map((element) => allSentences().find((item) => String(item.id) === element.id.replace("sentence-", ""))).filter(Boolean);
    const firstElement = elements[0];
    const paragraphElement = firstElement?.closest(".paragraph-block");
    let artifact;
    if (sentences.length === 1)
        artifact = sentenceArtifact(sentences[0], text);
    else if (paragraphElement && elements.every((element) => element.closest(".paragraph-block") === paragraphElement)) {
        const sectionIndex = Number(paragraphElement.dataset.sectionIndex || 0);
        const paragraphIndex = Number(paragraphElement.dataset.paragraphIndex || 0);
        const section = report.data.value?.sections?.[sectionIndex];
        if (section?.paragraphs?.[paragraphIndex]) {
            artifact = paragraphArtifact(section, section.paragraphs[paragraphIndex], paragraphIndex, text);
        }
    }
    if (!artifact) {
        const refs = sentences.map(sentenceRefs);
        artifact = {
            artifact_type: "report_excerpt", object_id: "selection", title: "报告选段",
            current: { quote: text.slice(0, 2000), sentence_ids: sentences.map((item) => item.id), source_refs: {
                    fact_ids: [...new Set(refs.flatMap((item) => item.fact_ids))],
                    inference_ids: [...new Set(refs.flatMap((item) => item.inference_ids))],
                } },
        };
    }
    selectionAction.value = {
        artifact,
        x: Math.min(window.innerWidth - 170, Math.max(12, event.clientX + 8)),
        y: Math.min(window.innerHeight - 52, Math.max(12, event.clientY + 8)),
    };
}
function sendSelectionReference() {
    const artifact = selectionAction.value?.artifact;
    if (!artifact)
        return;
    dispatchAssistant(["sentence", "paragraph"].includes(artifact.artifact_type) ? artifact : undefined, artifact, true);
    selectionAction.value = null;
    window.getSelection()?.removeAllRanges();
}
function discussTitle() {
    discussionScope.value = {
        artifactType: "report_title",
        objectId: "",
        current: { title: report.data.value?.title || "" },
    };
    openTaskAssistant();
}
function discussSection(section) {
    discussionScope.value = {
        artifactType: "section_title",
        objectId: section.title,
        current: { title: section.title },
    };
    openTaskAssistant();
}
function openTaskAssistant() {
    const target = discussionTarget.value;
    window.dispatchEvent(new CustomEvent("ira:assistant-focus", {
        detail: {
            taskId: report.data.value?.task_id,
            artifact: {
                artifact_type: target.artifactType,
                object_id: target.objectId,
                artifact_version: String(report.data.value?.versions?.[0]?.version_no || 1),
                current: target.current,
                title: target.artifactType === "sentence" ? "当前正文句子" : target.artifactType === "section_title" ? "当前章节标题" : "报告标题",
            },
        },
    }));
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
function qaLabel(issue) {
    const key = String(issue?.type || issue?.problem_type || "").toUpperCase();
    return {
        CONCRETENESS_ISSUE: "表述不够具体", DUPLICATE: "内容重复", REPETITION: "内容重复",
        LOGIC_GAP: "论证衔接不足", EVIDENCE_GAP: "依据不足", UNSUPPORTED: "依据不足",
        STYLE_ISSUE: "表达不够自然", STRUCTURE_ISSUE: "结构问题", FORMAT_ISSUE: "格式问题",
        TITLE_MISMATCH: "标题与内容需复核",
    }[key] || "质量问题";
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
/** @type {__VLS_StyleScopedClasses['sentence']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-block']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-discuss']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-discuss']} */ ;
/** @type {__VLS_StyleScopedClasses['selection-action']} */ ;
/** @type {__VLS_StyleScopedClasses['selection-action']} */ ;
/** @type {__VLS_StyleScopedClasses['selection-action']} */ ;
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
/** @type {__VLS_StyleScopedClasses['paragraph-discuss']} */ ;
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
        ...{ onMouseup: (__VLS_ctx.captureSelection) },
        ref: "paperRef",
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
                    [report, report, captureSelection, discussTitle, saveTitle, discussSection,];
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
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                key: (paragraphIndex),
                ...{ class: "paragraph-block" },
                'data-section-index': (sectionIndex),
                'data-paragraph-index': (paragraphIndex),
            });
            /** @type {__VLS_StyleScopedClasses['paragraph-block']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.report.data.value))
                            throw 0;
                        return (__VLS_ctx.discussParagraph(section, paragraph, paragraphIndex));
                        // @ts-ignore
                        [discussParagraph,];
                    } },
                ...{ class: "paragraph-discuss" },
                type: "button",
            });
            /** @type {__VLS_StyleScopedClasses['paragraph-discuss']} */ ;
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
                        id: (`sentence-${sentence.id}`),
                        ...{ class: "sentence subheading" },
                        ...{ class: ([
                                __VLS_ctx.sourceClass(sentence),
                                { selected: __VLS_ctx.selected?.id === sentence.id, 'qa-target': __VLS_ctx.qaTargetSentenceId === sentence.id },
                            ]) },
                        contenteditable: true,
                        spellcheck: "false",
                    });
                    /** @type {__VLS_StyleScopedClasses['sentence']} */ ;
                    /** @type {__VLS_StyleScopedClasses['subheading']} */ ;
                    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
                    /** @type {__VLS_StyleScopedClasses['qa-target']} */ ;
                    (sentence.display_content || sentence.content);
                }
                // @ts-ignore
                [sourceClass, selected, qaTargetSentenceId,];
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
                        id: (`sentence-${sentence.id}`),
                        ...{ class: "sentence" },
                        ...{ class: ([
                                __VLS_ctx.sourceClass(sentence),
                                {
                                    selected: __VLS_ctx.selected?.id === sentence.id,
                                    'qa-target': __VLS_ctx.qaTargetSentenceId === sentence.id,
                                    excluded: sentence.selected === false,
                                },
                            ]) },
                        contenteditable: true,
                        spellcheck: "false",
                    });
                    /** @type {__VLS_StyleScopedClasses['sentence']} */ ;
                    /** @type {__VLS_StyleScopedClasses['selected']} */ ;
                    /** @type {__VLS_StyleScopedClasses['excluded']} */ ;
                    /** @type {__VLS_StyleScopedClasses['qa-target']} */ ;
                    (sentence.content);
                    // @ts-ignore
                    [sourceClass, selected, qaTargetSentenceId,];
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
        ...{ onClick: (__VLS_ctx.openTaskAssistant) },
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
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (__VLS_ctx.referenceSelectedSentence) },
                ...{ class: "btn sentence-assistant" },
                type: "button",
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['sentence-assistant']} */ ;
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
                    [sideTab, sideTab, sideTab, openTaskAssistant, currentDetails, currentDetails, currentDetails, currentDetails, referenceSelectedSentence,];
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
            (__VLS_ctx.qaLabel(issue));
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (issue.note || issue.quote || issue.message);
            if (issue.sentence_id || issue.section) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.report.data.value))
                                throw 0;
                            if (!!(__VLS_ctx.sideTab === 'evidence'))
                                throw 0;
                            if (!(__VLS_ctx.sideTab === 'qa'))
                                throw 0;
                            if (!(issue.sentence_id || issue.section))
                                throw 0;
                            return (__VLS_ctx.locateIssue(issue));
                            // @ts-ignore
                            [mode, sideTab, currentDetails, currentDetails, currentDetails, qaIssues, qaLabel, locateIssue,];
                        } },
                    type: "button",
                    ...{ class: "qa-locate" },
                });
                /** @type {__VLS_StyleScopedClasses['qa-locate']} */ ;
                (issue.sentence_id ? "定位到问题句" : "定位到章节");
            }
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.report.data.value))
                            throw 0;
                        if (!!(__VLS_ctx.sideTab === 'evidence'))
                            throw 0;
                        if (!(__VLS_ctx.sideTab === 'qa'))
                            throw 0;
                        return (__VLS_ctx.askAboutIssue(issue));
                        // @ts-ignore
                        [askAboutIssue,];
                    } },
                type: "button",
                ...{ class: "qa-locate" },
            });
            /** @type {__VLS_StyleScopedClasses['qa-locate']} */ ;
            // @ts-ignore
            [];
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
            ...{ class: "panel-body assistant-handoff" },
        });
        /** @type {__VLS_StyleScopedClasses['panel-body']} */ ;
        /** @type {__VLS_StyleScopedClasses['assistant-handoff']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.openTaskAssistant) },
            ...{ class: "btn primary" },
            type: "button",
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['primary']} */ ;
    }
    if (__VLS_ctx.selectionAction) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "selection-action" },
            ...{ style: ({ left: `${__VLS_ctx.selectionAction.x}px`, top: `${__VLS_ctx.selectionAction.y}px` }) },
        });
        /** @type {__VLS_StyleScopedClasses['selection-action']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (__VLS_ctx.sendSelectionReference) },
            type: "button",
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.report.data.value))
                        throw 0;
                    if (!(__VLS_ctx.selectionAction))
                        throw 0;
                    return (__VLS_ctx.selectionAction = null);
                    // @ts-ignore
                    [openTaskAssistant, qaIssues, selectionAction, selectionAction, selectionAction, selectionAction, sendSelectionReference,];
                } },
            type: "button",
            'aria-label': "取消引用",
        });
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
