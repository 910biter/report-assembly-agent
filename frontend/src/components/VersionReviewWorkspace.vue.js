import { computed, ref, watch } from "vue";
import { api, jsonInit } from "@/api/http";
const props = defineProps();
const emit = defineEmits();
const activeVersion = ref(props.initialVersion || null);
const diff = ref(null);
const decisions = ref({});
const loading = ref(false);
const error = ref("");
const filter = ref("all");
const activeSection = ref("all");
const fineParagraph = ref("");
const workspaceMode = ref("review");
const applying = ref(false);
const historicalVersion = ref(null);
const historicalLoading = ref(false);
const historicalError = ref("");
const changedSections = computed(() => (diff.value?.sections || []).filter((item) => item.change_type !== "unchanged"));
const visibleSections = computed(() => activeSection.value === "all" ? changedSections.value : changedSections.value.filter((item) => item.section === activeSection.value));
const versionMeta = computed(() => props.versions.find(item => Number(item.id) === activeVersion.value));
const changedSentences = computed(() => changedSections.value.flatMap((section) => (section.paragraphs || []).flatMap((paragraph) => (paragraph.sentences || []).filter((sentence) => sentence.change_type !== "unchanged")
    .map((sentence) => ({ ...sentence, section_key: section.change_key, paragraph_key: paragraph.change_key })))));
const explicitCount = computed(() => Object.keys(decisions.value).length);
const restoredCount = computed(() => Object.values(decisions.value).filter(item => item.decision === "use_base").length);
const reviewedCount = computed(() => changedSentences.value.filter((item) => decisions.value[item.change_key] || decisions.value[item.paragraph_key] || decisions.value[item.section_key]).length);
const lengths = computed(() => {
    const sections = diff.value?.sections || [];
    const oldLength = sections.reduce((total, section) => total + (section.paragraphs || []).reduce((sum, paragraph) => sum + String(paragraph.old_text || "").length, 0), 0);
    const newLength = sections.reduce((total, section) => total + (section.paragraphs || []).reduce((sum, paragraph) => sum + String(paragraph.new_text || "").length, 0), 0);
    return { oldLength, newLength, delta: newLength - oldLength };
});
const historicalSections = computed(() => {
    const rows = [...(historicalVersion.value?.sentence_snapshot || [])]
        .filter((row) => Number(row.selected ?? 1) !== 0)
        .sort((a, b) => Number(a.position || 0) - Number(b.position || 0) || Number(a.id || 0) - Number(b.id || 0));
    const sections = new Map();
    rows.forEach((row) => {
        const title = String(row.section || "未命名章节");
        const paragraph = Number(row.paragraph || 1);
        if (!sections.has(title))
            sections.set(title, new Map());
        const paragraphs = sections.get(title);
        if (!paragraphs.has(paragraph))
            paragraphs.set(paragraph, []);
        paragraphs.get(paragraph).push(String(row.rendered_text || row.user_edit || row.content || ""));
    });
    return [...sections.entries()].map(([title, paragraphs]) => ({
        title,
        paragraphs: [...paragraphs.entries()].sort((a, b) => a[0] - b[0]).map(([, texts]) => texts.join("")),
    }));
});
watch(() => props.versions, versions => {
    if (!activeVersion.value && versions.length)
        activeVersion.value = Number(versions[0].id);
}, { immediate: true });
watch(activeVersion, async (versionId) => {
    diff.value = null;
    decisions.value = {};
    activeSection.value = "all";
    fineParagraph.value = "";
    historicalVersion.value = null;
    historicalError.value = "";
    error.value = "";
    if (!versionId)
        return;
    loading.value = true;
    try {
        diff.value = await api(`/api/report-versions/${versionId}/diff-current?granularity=sentence`);
        await loadDecisions();
    }
    catch (reason) {
        error.value = reason.message || "版本差异加载失败";
    }
    finally {
        loading.value = false;
    }
    if (workspaceMode.value === "history")
        await loadHistoricalVersion();
});
watch(workspaceMode, async (mode) => {
    if (mode === "history")
        await loadHistoricalVersion();
});
async function loadHistoricalVersion() {
    if (!activeVersion.value || Number(historicalVersion.value?.id) === activeVersion.value || historicalLoading.value)
        return;
    historicalLoading.value = true;
    historicalError.value = "";
    try {
        historicalVersion.value = await api(`/api/report-versions/${activeVersion.value}`);
    }
    catch (reason) {
        historicalError.value = reason.message || "历史原文加载失败";
    }
    finally {
        historicalLoading.value = false;
    }
}
async function loadDecisions() {
    if (!activeVersion.value || !diff.value?.candidate_hash)
        return;
    const result = await api(`/api/report-versions/${activeVersion.value}/decisions?candidate_hash=${encodeURIComponent(diff.value.candidate_hash)}`);
    decisions.value = Object.fromEntries((result.decisions || []).filter((item) => item.status === "pending").map((item) => [item.change_key, item]));
}
function sectionScope(section) { return { level: "section", section: section.section }; }
function paragraphScope(section, paragraph) {
    return { level: "paragraph", section: section.section, paragraph: paragraph.old_paragraph ?? paragraph.paragraph, old_paragraph: paragraph.old_paragraph, new_paragraph: paragraph.new_paragraph };
}
function sentenceScope(section, paragraph, sentence) {
    return { level: "sentence", section: section.section, paragraph: paragraph.new_paragraph ?? paragraph.old_paragraph, old_paragraph: paragraph.old_paragraph, new_paragraph: paragraph.new_paragraph, sentence_index: sentence.old_index, old_sentence_id: sentence.old_sentence_id, current_sentence_id: sentence.current_sentence_id };
}
async function decide(key, changeType, scope, decision) {
    if (!activeVersion.value || !diff.value)
        return;
    await api(`/api/report-versions/${activeVersion.value}/decisions`, jsonInit("POST", { candidate_hash: diff.value.candidate_hash, change_key: key, change_type: changeType, scope, decision }));
    await loadDecisions();
}
async function apply() {
    if (!activeVersion.value || !diff.value || applying.value)
        return;
    applying.value = true;
    try {
        await api(`/api/report-versions/${activeVersion.value}/decisions/apply`, jsonInit("POST", { candidate_hash: diff.value.candidate_hash }));
        emit("applied");
    }
    catch (reason) {
        alert(reason.message || "应用失败，候选稿可能已经变化，请重新加载。");
    }
    finally {
        applying.value = false;
    }
}
function decision(key) { return decisions.value[key]?.decision || ""; }
function decisionLabel(key) { return decision(key) === "use_base" ? "采用历史版本" : decision(key) === "keep_current" ? "保留当前版本" : "默认保留当前版本"; }
function paragraphKey(section, paragraph) { return `${section.section}:${paragraph.change_key}`; }
function toggleFine(section, paragraph) { const key = paragraphKey(section, paragraph); fineParagraph.value = fineParagraph.value === key ? "" : key; }
function changedLabel(type, unit = "") { return type === "added" ? `新增${unit}` : type === "removed" ? `删除${unit}` : `修改${unit}`; }
function reviewParagraphs(section) {
    const paragraphs = section.paragraphs || [];
    const changed = new Set();
    paragraphs.forEach((paragraph, index) => { if (paragraph.change_type !== "unchanged" && (filter.value === "all" || paragraph.change_type === filter.value))
        changed.add(index); });
    const visible = new Set();
    changed.forEach(index => { visible.add(index - 1); visible.add(index); visible.add(index + 1); });
    return paragraphs.map((paragraph, index) => ({ ...paragraph, _context: !changed.has(index), _index: index })).filter((_, index) => visible.has(index));
}
function spans(sentence, side) { return sentence.inline_diff?.[side] || [{ type: "unchanged", text: side === "old" ? sentence.old_text : sentence.new_text }]; }
function sourceImpact(sentence) {
    const source = sentence.new_sentence || sentence.old_sentence || {};
    const refs = source.source_refs || {};
    const facts = (refs.fact_ids || source.fact_ids || []).length;
    const inferences = (refs.inference_ids || source.inference_ids || []).length;
    return [facts ? `${facts} 条事实` : "", inferences ? `${inferences} 条推论` : ""].filter(Boolean).join(" · ") || "结构或过渡表达";
}
function previewSentence(sentence) {
    if (decision(sentence.change_key) === "use_base")
        return sentence.change_type === "added" ? "" : sentence.old_text || "";
    return sentence.change_type === "removed" ? "" : sentence.new_text || "";
}
function previewParagraph(section, paragraph) {
    if (decision(section.change_key) === "use_base" || decision(paragraph.change_key) === "use_base")
        return paragraph.old_text || "";
    return (paragraph.sentences || []).map(previewSentence).join("");
}
function previewSection(section) { return (section.paragraphs || []).map((paragraph) => previewParagraph(section, paragraph)).filter(Boolean); }
const __VLS_ctx = {
    ...{},
    ...{},
    ...{},
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['header-stats']} */ ;
/** @type {__VLS_StyleScopedClasses['header-stats']} */ ;
/** @type {__VLS_StyleScopedClasses['header-stats']} */ ;
/** @type {__VLS_StyleScopedClasses['header-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['header-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['version-option']} */ ;
/** @type {__VLS_StyleScopedClasses['chapter-option']} */ ;
/** @type {__VLS_StyleScopedClasses['version-option']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['chapter-option']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['version-option']} */ ;
/** @type {__VLS_StyleScopedClasses['version-option']} */ ;
/** @type {__VLS_StyleScopedClasses['version-option']} */ ;
/** @type {__VLS_StyleScopedClasses['version-option']} */ ;
/** @type {__VLS_StyleScopedClasses['chapter-option']} */ ;
/** @type {__VLS_StyleScopedClasses['chapter-option']} */ ;
/** @type {__VLS_StyleScopedClasses['snapshot-button']} */ ;
/** @type {__VLS_StyleScopedClasses['filterbar']} */ ;
/** @type {__VLS_StyleScopedClasses['filterbar']} */ ;
/** @type {__VLS_StyleScopedClasses['filterbar']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['filterbar']} */ ;
/** @type {__VLS_StyleScopedClasses['review-section']} */ ;
/** @type {__VLS_StyleScopedClasses['review-section']} */ ;
/** @type {__VLS_StyleScopedClasses['change-kind']} */ ;
/** @type {__VLS_StyleScopedClasses['change-kind']} */ ;
/** @type {__VLS_StyleScopedClasses['change-kind']} */ ;
/** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['chosen']} */ ;
/** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['review-paragraph']} */ ;
/** @type {__VLS_StyleScopedClasses['added']} */ ;
/** @type {__VLS_StyleScopedClasses['review-paragraph']} */ ;
/** @type {__VLS_StyleScopedClasses['removed']} */ ;
/** @type {__VLS_StyleScopedClasses['review-paragraph']} */ ;
/** @type {__VLS_StyleScopedClasses['review-paragraph']} */ ;
/** @type {__VLS_StyleScopedClasses['context']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-head']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-head']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-change']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-change']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-change']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-change']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['sentence-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['review-progress']} */ ;
/** @type {__VLS_StyleScopedClasses['review-progress']} */ ;
/** @type {__VLS_StyleScopedClasses['principle']} */ ;
/** @type {__VLS_StyleScopedClasses['principle']} */ ;
/** @type {__VLS_StyleScopedClasses['apply-button']} */ ;
/** @type {__VLS_StyleScopedClasses['review-state']} */ ;
/** @type {__VLS_StyleScopedClasses['review-state']} */ ;
/** @type {__VLS_StyleScopedClasses['review-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['header-stats']} */ ;
/** @type {__VLS_StyleScopedClasses['review-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['header-stats']} */ ;
/** @type {__VLS_StyleScopedClasses['review-workspace']} */ ;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
/** @type {__VLS_StyleScopedClasses['header-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
/** @type {__VLS_StyleScopedClasses['review-main']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
/** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "review-workspace" },
});
/** @type {__VLS_StyleScopedClasses['review-workspace']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "review-header" },
});
/** @type {__VLS_StyleScopedClasses['review-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "eyebrow" },
});
/** @type {__VLS_StyleScopedClasses['eyebrow']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
if (__VLS_ctx.diff) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "header-stats" },
    });
    /** @type {__VLS_StyleScopedClasses['header-stats']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.diff.summary?.sections_changed || 0);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.diff.summary?.paragraphs_changed || 0);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({
        ...{ class: (__VLS_ctx.lengths.delta >= 0 ? 'positive' : 'negative') },
    });
    (__VLS_ctx.lengths.delta >= 0 ? '+' : '');
    (__VLS_ctx.lengths.delta);
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "header-actions" },
});
/** @type {__VLS_StyleScopedClasses['header-actions']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.workspaceMode = 'review');
            // @ts-ignore
            [diff, diff, diff, lengths, lengths, lengths, workspaceMode,];
        } },
    ...{ class: ({ active: __VLS_ctx.workspaceMode === 'review' }) },
});
/** @type {__VLS_StyleScopedClasses['active']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.workspaceMode = 'history');
            // @ts-ignore
            [workspaceMode, workspaceMode,];
        } },
    ...{ class: ({ active: __VLS_ctx.workspaceMode === 'history' }) },
});
/** @type {__VLS_StyleScopedClasses['active']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.workspaceMode = 'preview');
            // @ts-ignore
            [workspaceMode, workspaceMode,];
        } },
    ...{ class: ({ active: __VLS_ctx.workspaceMode === 'preview' }) },
});
/** @type {__VLS_StyleScopedClasses['active']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.emit('close'));
            // @ts-ignore
            [workspaceMode, emit,];
        } },
});
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "review-navigation" },
});
/** @type {__VLS_StyleScopedClasses['review-navigation']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.emit('snapshot'));
            // @ts-ignore
            [emit,];
        } },
    ...{ class: "snapshot-button" },
});
/** @type {__VLS_StyleScopedClasses['snapshot-button']} */ ;
for (const [version] of __VLS_vFor((__VLS_ctx.versions))) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                return (__VLS_ctx.activeVersion = Number(version.id));
                // @ts-ignore
                [versions, activeVersion,];
            } },
        key: (version.id),
        ...{ class: "version-option" },
        ...{ class: ({ active: __VLS_ctx.activeVersion === Number(version.id) }) },
    });
    /** @type {__VLS_StyleScopedClasses['version-option']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (version.version_label || version.version_no);
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    (version.change_summary || '版本快照');
    __VLS_asFunctionalElement1(__VLS_intrinsics.time, __VLS_intrinsics.time)({});
    (version.created_at);
    // @ts-ignore
    [activeVersion,];
}
if (__VLS_ctx.diff) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.diff))
                    throw 0;
                return (__VLS_ctx.activeSection = 'all');
                // @ts-ignore
                [diff, activeSection,];
            } },
        ...{ class: "chapter-option" },
        ...{ class: ({ active: __VLS_ctx.activeSection === 'all' }) },
    });
    /** @type {__VLS_StyleScopedClasses['chapter-option']} */ ;
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.changedSections.length);
    for (const [section] of __VLS_vFor((__VLS_ctx.changedSections))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.diff))
                        throw 0;
                    return (__VLS_ctx.activeSection = section.section);
                    // @ts-ignore
                    [activeSection, activeSection, changedSections, changedSections,];
                } },
            key: (section.section),
            ...{ class: "chapter-option" },
            ...{ class: ({ active: __VLS_ctx.activeSection === section.section }) },
        });
        /** @type {__VLS_StyleScopedClasses['chapter-option']} */ ;
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        (section.section);
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        ((section.paragraphs || []).filter((paragraph) => paragraph.change_type !== 'unchanged').length);
        // @ts-ignore
        [activeSection,];
    }
}
__VLS_asFunctionalElement1(__VLS_intrinsics.main, __VLS_intrinsics.main)({
    ...{ class: "review-main" },
});
/** @type {__VLS_StyleScopedClasses['review-main']} */ ;
if (__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-state" },
    });
    /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "spinner" },
    });
    /** @type {__VLS_StyleScopedClasses['spinner']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
}
else if (__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-state" },
    });
    /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    (__VLS_ctx.historicalError);
}
else if (__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "historical-preview" },
    });
    /** @type {__VLS_StyleScopedClasses['historical-preview']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.versionMeta?.version_label || __VLS_ctx.versionMeta?.version_no);
    __VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
    (__VLS_ctx.historicalVersion.title || __VLS_ctx.reportTitle);
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    (__VLS_ctx.versionMeta?.created_at);
    (__VLS_ctx.versionMeta?.change_summary || '版本快照');
    for (const [section] of __VLS_vFor((__VLS_ctx.historicalSections))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
            key: (section.title),
        });
        __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
        (section.title);
        for (const [paragraph, index] of __VLS_vFor((section.paragraphs))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                key: (index),
            });
            (paragraph);
            // @ts-ignore
            [workspaceMode, workspaceMode, workspaceMode, historicalLoading, historicalError, historicalError, historicalVersion, historicalVersion, versionMeta, versionMeta, versionMeta, versionMeta, reportTitle, historicalSections,];
        }
        // @ts-ignore
        [];
    }
    if (!__VLS_ctx.historicalSections.length) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "review-state" },
        });
        /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    }
}
else if (__VLS_ctx.loading) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-state" },
    });
    /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "spinner" },
    });
    /** @type {__VLS_StyleScopedClasses['spinner']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
}
else if (__VLS_ctx.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-state" },
    });
    /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
    (__VLS_ctx.error);
}
else if (__VLS_ctx.diff) {
    if (__VLS_ctx.workspaceMode === 'review') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "review-document" },
        });
        /** @type {__VLS_StyleScopedClasses['review-document']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "filterbar" },
        });
        /** @type {__VLS_StyleScopedClasses['filterbar']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "tabs" },
        });
        /** @type {__VLS_StyleScopedClasses['tabs']} */ ;
        for (const [item] of __VLS_vFor(([['all', '全部'], ['modified', '修改'], ['added', '新增'], ['removed', '删除']]))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                            throw 0;
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                            throw 0;
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                            throw 0;
                        if (!!(__VLS_ctx.loading))
                            throw 0;
                        if (!!(__VLS_ctx.error))
                            throw 0;
                        if (!(__VLS_ctx.diff))
                            throw 0;
                        if (!(__VLS_ctx.workspaceMode === 'review'))
                            throw 0;
                        return (__VLS_ctx.filter = item[0]);
                        // @ts-ignore
                        [diff, workspaceMode, historicalSections, loading, error, error, filter,];
                    } },
                key: (item[0]),
                ...{ class: ({ active: __VLS_ctx.filter === item[0] }) },
            });
            /** @type {__VLS_StyleScopedClasses['active']} */ ;
            (item[1]);
            // @ts-ignore
            [filter,];
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        for (const [section] of __VLS_vFor((__VLS_ctx.visibleSections))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
                key: (section.section),
                ...{ class: "review-section" },
            });
            /** @type {__VLS_StyleScopedClasses['review-section']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: (['change-kind', section.change_type]) },
            });
            /** @type {__VLS_StyleScopedClasses['change-kind']} */ ;
            (__VLS_ctx.changedLabel(section.change_type, '章节'));
            __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
            (section.section);
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "scope-actions" },
            });
            /** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
            (__VLS_ctx.decisionLabel(section.change_key));
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                            throw 0;
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                            throw 0;
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                            throw 0;
                        if (!!(__VLS_ctx.loading))
                            throw 0;
                        if (!!(__VLS_ctx.error))
                            throw 0;
                        if (!(__VLS_ctx.diff))
                            throw 0;
                        if (!(__VLS_ctx.workspaceMode === 'review'))
                            throw 0;
                        return (__VLS_ctx.decide(section.change_key, section.change_type, __VLS_ctx.sectionScope(section), 'keep_current'));
                        // @ts-ignore
                        [visibleSections, changedLabel, decisionLabel, decide, sectionScope,];
                    } },
                ...{ class: ({ chosen: __VLS_ctx.decision(section.change_key) === 'keep_current' }) },
            });
            /** @type {__VLS_StyleScopedClasses['chosen']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                            throw 0;
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                            throw 0;
                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                            throw 0;
                        if (!!(__VLS_ctx.loading))
                            throw 0;
                        if (!!(__VLS_ctx.error))
                            throw 0;
                        if (!(__VLS_ctx.diff))
                            throw 0;
                        if (!(__VLS_ctx.workspaceMode === 'review'))
                            throw 0;
                        return (__VLS_ctx.decide(section.change_key, section.change_type, __VLS_ctx.sectionScope(section), 'use_base'));
                        // @ts-ignore
                        [decide, sectionScope, decision,];
                    } },
                ...{ class: ({ chosen: __VLS_ctx.decision(section.change_key) === 'use_base' }) },
            });
            /** @type {__VLS_StyleScopedClasses['chosen']} */ ;
            for (const [paragraph] of __VLS_vFor((__VLS_ctx.reviewParagraphs(section)))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                    key: (paragraph.change_key),
                    ...{ class: (['review-paragraph', paragraph.change_type, { context: paragraph._context }]) },
                });
                /** @type {__VLS_StyleScopedClasses['context']} */ ;
                /** @type {__VLS_StyleScopedClasses['review-paragraph']} */ ;
                if (paragraph._context) {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: "context-label" },
                    });
                    /** @type {__VLS_StyleScopedClasses['context-label']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                    (paragraph.new_text || paragraph.old_text);
                }
                else {
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                        ...{ class: "paragraph-head" },
                    });
                    /** @type {__VLS_StyleScopedClasses['paragraph-head']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                        ...{ class: (['change-kind', paragraph.change_type]) },
                    });
                    /** @type {__VLS_StyleScopedClasses['change-kind']} */ ;
                    (__VLS_ctx.changedLabel(paragraph.change_type));
                    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                    (paragraph.new_paragraph || paragraph.old_paragraph);
                    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                    (__VLS_ctx.decisionLabel(paragraph.change_key));
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                        ...{ class: "scope-actions compact" },
                    });
                    /** @type {__VLS_StyleScopedClasses['scope-actions']} */ ;
                    /** @type {__VLS_StyleScopedClasses['compact']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                        ...{ onClick: (...[$event]) => {
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                                    throw 0;
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                                    throw 0;
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                                    throw 0;
                                if (!!(__VLS_ctx.loading))
                                    throw 0;
                                if (!!(__VLS_ctx.error))
                                    throw 0;
                                if (!(__VLS_ctx.diff))
                                    throw 0;
                                if (!(__VLS_ctx.workspaceMode === 'review'))
                                    throw 0;
                                if (!!(paragraph._context))
                                    throw 0;
                                return (__VLS_ctx.decide(paragraph.change_key, paragraph.change_type, __VLS_ctx.paragraphScope(section, paragraph), 'keep_current'));
                                // @ts-ignore
                                [changedLabel, decisionLabel, decide, decision, reviewParagraphs, paragraphScope,];
                            } },
                        ...{ class: ({ chosen: __VLS_ctx.decision(paragraph.change_key) === 'keep_current' }) },
                    });
                    /** @type {__VLS_StyleScopedClasses['chosen']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                        ...{ onClick: (...[$event]) => {
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                                    throw 0;
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                                    throw 0;
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                                    throw 0;
                                if (!!(__VLS_ctx.loading))
                                    throw 0;
                                if (!!(__VLS_ctx.error))
                                    throw 0;
                                if (!(__VLS_ctx.diff))
                                    throw 0;
                                if (!(__VLS_ctx.workspaceMode === 'review'))
                                    throw 0;
                                if (!!(paragraph._context))
                                    throw 0;
                                return (__VLS_ctx.decide(paragraph.change_key, paragraph.change_type, __VLS_ctx.paragraphScope(section, paragraph), 'use_base'));
                                // @ts-ignore
                                [decide, decision, paragraphScope,];
                            } },
                        ...{ class: ({ chosen: __VLS_ctx.decision(paragraph.change_key) === 'use_base' }) },
                    });
                    /** @type {__VLS_StyleScopedClasses['chosen']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                        ...{ onClick: (...[$event]) => {
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                                    throw 0;
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                                    throw 0;
                                if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                                    throw 0;
                                if (!!(__VLS_ctx.loading))
                                    throw 0;
                                if (!!(__VLS_ctx.error))
                                    throw 0;
                                if (!(__VLS_ctx.diff))
                                    throw 0;
                                if (!(__VLS_ctx.workspaceMode === 'review'))
                                    throw 0;
                                if (!!(paragraph._context))
                                    throw 0;
                                return (__VLS_ctx.toggleFine(section, paragraph));
                                // @ts-ignore
                                [decision, toggleFine,];
                            } },
                    });
                    (__VLS_ctx.fineParagraph === __VLS_ctx.paragraphKey(section, paragraph) ? '收起精调' : '逐句调整');
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                        ...{ class: "paragraph-compare" },
                    });
                    /** @type {__VLS_StyleScopedClasses['paragraph-compare']} */ ;
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
                    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                    (paragraph.old_text || '（本段不存在）');
                    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
                    __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
                    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                    (paragraph.new_text || '（本段已删除）');
                    if (__VLS_ctx.fineParagraph === __VLS_ctx.paragraphKey(section, paragraph)) {
                        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                            ...{ class: "sentence-review" },
                        });
                        /** @type {__VLS_StyleScopedClasses['sentence-review']} */ ;
                        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                            ...{ class: "fine-note" },
                        });
                        /** @type {__VLS_StyleScopedClasses['fine-note']} */ ;
                        for (const [sentence] of __VLS_vFor(((paragraph.sentences || []).filter((item) => item.change_type !== 'unchanged')))) {
                            __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                                key: (sentence.change_key),
                                ...{ class: (['sentence-change', sentence.change_type]) },
                            });
                            /** @type {__VLS_StyleScopedClasses['sentence-change']} */ ;
                            __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
                            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
                            (__VLS_ctx.changedLabel(sentence.change_type, '句'));
                            __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
                            (__VLS_ctx.sourceImpact(sentence));
                            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
                            (__VLS_ctx.decisionLabel(sentence.change_key));
                            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                                ...{ class: "inline-compare" },
                            });
                            /** @type {__VLS_StyleScopedClasses['inline-compare']} */ ;
                            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                            __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
                            for (const [part, index] of __VLS_vFor((__VLS_ctx.spans(sentence, 'old')))) {
                                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                                    key: (index),
                                    ...{ class: (`inline-${part.type}`) },
                                });
                                (part.text);
                                // @ts-ignore
                                [changedLabel, decisionLabel, fineParagraph, fineParagraph, paragraphKey, paragraphKey, sourceImpact, spans,];
                            }
                            if (!sentence.old_text) {
                                __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
                            }
                            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
                            __VLS_asFunctionalElement1(__VLS_intrinsics.label, __VLS_intrinsics.label)({});
                            for (const [part, index] of __VLS_vFor((__VLS_ctx.spans(sentence, 'new')))) {
                                __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                                    key: (index),
                                    ...{ class: (`inline-${part.type}`) },
                                });
                                (part.text);
                                // @ts-ignore
                                [spans,];
                            }
                            if (!sentence.new_text) {
                                __VLS_asFunctionalElement1(__VLS_intrinsics.i, __VLS_intrinsics.i)({});
                            }
                            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                                ...{ class: "sentence-actions" },
                            });
                            /** @type {__VLS_StyleScopedClasses['sentence-actions']} */ ;
                            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                                ...{ onClick: (...[$event]) => {
                                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                                            throw 0;
                                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                                            throw 0;
                                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                                            throw 0;
                                        if (!!(__VLS_ctx.loading))
                                            throw 0;
                                        if (!!(__VLS_ctx.error))
                                            throw 0;
                                        if (!(__VLS_ctx.diff))
                                            throw 0;
                                        if (!(__VLS_ctx.workspaceMode === 'review'))
                                            throw 0;
                                        if (!!(paragraph._context))
                                            throw 0;
                                        if (!(__VLS_ctx.fineParagraph === __VLS_ctx.paragraphKey(section, paragraph)))
                                            throw 0;
                                        return (__VLS_ctx.decide(sentence.change_key, sentence.change_type, __VLS_ctx.sentenceScope(section, paragraph, sentence), 'keep_current'));
                                        // @ts-ignore
                                        [decide, sentenceScope,];
                                    } },
                                ...{ class: ({ chosen: __VLS_ctx.decision(sentence.change_key) === 'keep_current' }) },
                            });
                            /** @type {__VLS_StyleScopedClasses['chosen']} */ ;
                            (sentence.change_type === 'removed' ? '保持删除' : '保留当前');
                            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                                ...{ onClick: (...[$event]) => {
                                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalLoading))
                                            throw 0;
                                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalError))
                                            throw 0;
                                        if (!!(__VLS_ctx.workspaceMode === 'history' && __VLS_ctx.historicalVersion))
                                            throw 0;
                                        if (!!(__VLS_ctx.loading))
                                            throw 0;
                                        if (!!(__VLS_ctx.error))
                                            throw 0;
                                        if (!(__VLS_ctx.diff))
                                            throw 0;
                                        if (!(__VLS_ctx.workspaceMode === 'review'))
                                            throw 0;
                                        if (!!(paragraph._context))
                                            throw 0;
                                        if (!(__VLS_ctx.fineParagraph === __VLS_ctx.paragraphKey(section, paragraph)))
                                            throw 0;
                                        return (__VLS_ctx.decide(sentence.change_key, sentence.change_type, __VLS_ctx.sentenceScope(section, paragraph, sentence), 'use_base'));
                                        // @ts-ignore
                                        [decide, decision, sentenceScope,];
                                    } },
                                ...{ class: ({ chosen: __VLS_ctx.decision(sentence.change_key) === 'use_base' }) },
                            });
                            /** @type {__VLS_StyleScopedClasses['chosen']} */ ;
                            (sentence.change_type === 'added' ? '删除新增' : '恢复原句');
                            // @ts-ignore
                            [decision,];
                        }
                    }
                }
                // @ts-ignore
                [];
            }
            // @ts-ignore
            [];
        }
        if (!__VLS_ctx.visibleSections.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "review-state" },
            });
            /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        }
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "merged-preview" },
        });
        /** @type {__VLS_StyleScopedClasses['merged-preview']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
        (__VLS_ctx.reportTitle);
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        for (const [section] of __VLS_vFor(((__VLS_ctx.diff.sections || [])))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
                key: (section.section),
            });
            __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
            (section.section);
            for (const [paragraph, index] of __VLS_vFor((__VLS_ctx.previewSection(section)))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
                    key: (index),
                });
                (paragraph);
                // @ts-ignore
                [diff, reportTitle, visibleSections, previewSection,];
            }
            // @ts-ignore
            [];
        }
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-state" },
    });
    /** @type {__VLS_StyleScopedClasses['review-state']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
}
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "review-summary" },
});
/** @type {__VLS_StyleScopedClasses['review-summary']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "baseline-card" },
});
/** @type {__VLS_StyleScopedClasses['baseline-card']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
(__VLS_ctx.versionMeta?.version_label || __VLS_ctx.versionMeta?.version_no || '—');
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
(__VLS_ctx.versionMeta?.change_summary || '版本快照');
if (__VLS_ctx.diff) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.lengths.oldLength);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.lengths.newLength);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.explicitCount);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
    (__VLS_ctx.restoredCount);
}
if (__VLS_ctx.diff) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "review-progress" },
    });
    /** @type {__VLS_StyleScopedClasses['review-progress']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.reviewedCount);
    (__VLS_ctx.changedSentences.length);
    __VLS_asFunctionalElement1(__VLS_intrinsics.progress, __VLS_intrinsics.progress)({
        value: (__VLS_ctx.reviewedCount),
        max: (Math.max(__VLS_ctx.changedSentences.length, 1)),
    });
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "principle" },
});
/** @type {__VLS_StyleScopedClasses['principle']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (__VLS_ctx.apply) },
    ...{ class: "apply-button" },
    disabled: (!__VLS_ctx.diff || __VLS_ctx.applying),
});
/** @type {__VLS_StyleScopedClasses['apply-button']} */ ;
(__VLS_ctx.applying ? '正在生成版本…' : '完成审阅并生成版本');
// @ts-ignore
[diff, diff, diff, lengths, lengths, versionMeta, versionMeta, versionMeta, explicitCount, restoredCount, reviewedCount, reviewedCount, changedSentences, changedSentences, apply, applying, applying,];
const __VLS_export = (await import('vue')).defineComponent({
    __typeEmits: {},
    __typeProps: {},
});
export default {};
