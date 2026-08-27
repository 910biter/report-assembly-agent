import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { api, jsonInit, uploadForm } from "@/api/http";
const qc = useQueryClient();
const upload = ref();
const selected = ref(null);
const schema = ref(null);
const tab = ref("document");
const error = ref("");
const editingName = ref("");
const uploading = ref(false);
const uploadPercent = ref(0);
const learningJob = ref(null);
let pollTimer;
const variants = useQuery({ queryKey: ["templates"], queryFn: () => api("/api/style/variants") });
const action = useMutation({ mutationFn: ({ id, op }) => api(`/api/style/variants/${id}${op === 'delete' ? '' : `/${op}`}`, { method: op === 'delete' ? "DELETE" : "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }) });
async function inspect(item) { selected.value = await api(`/api/style/variants/${item.id}/profile`); editingName.value = selected.value.name || ""; schema.value = null; try {
    schema.value = await api(`/api/style/variants/${item.id}/template-schema`);
}
catch {
    schema.value = { error: "该模板暂无结构化 Schema" };
} }
function remove(item) { if (confirm(`删除模板“${item.name || item.id}”？`))
    action.mutate({ id: item.id, op: "delete" }); }
function role(value, key) { return value?.style?.roles?.[key] || value?.roles?.[key] || value?.styles?.[key] || value?.[key] || {}; }
function known(value) { return value !== undefined && value !== null && value !== "" && value !== "unknown" ? value : ""; }
function format(value) { if (!value || typeof value !== "object")
    return "尚未识别"; const run = value.run || {}; const font = known(run.font_east_asia) || known(run.font_ascii) || known(value.font?.name) || known(value.font_name) || (typeof value.font === "string" ? known(value.font) : ""); const size = known(run.font_size_pt) || known(value.font?.size_pt) || known(value.size_pt) || known(value.size); const rawAlign = known(value.paragraph?.alignment) || known(value.alignment); const align = { center: "居中", justify: "两端对齐", left: "左对齐", right: "右对齐" }[rawAlign] || rawAlign; const parts = [font, size ? `${size}pt` : "", align].filter(Boolean); return parts.length ? parts.join(" / ") : "尚未识别"; }
function confidence(key) { const value = selected.value?.profile_confidence?.[key]; if (value === "unavailable")
    return "暂无可用结果"; return { high: "高", medium: "中", low: "低" }[value] || "未完成"; }
async function saveName() { const name = editingName.value.trim(); if (!name) {
    error.value = "模板名称不能为空";
    return;
} await api(`/api/style/variants/${selected.value.id}`, jsonInit("PATCH", { name })); selected.value.name = name; await qc.invalidateQueries({ queryKey: ["templates"] }); }
const editorialRows = computed(() => [
    ["语气", selected.value?.writing_style?.tone], ["句式", selected.value?.writing_style?.sentence_pattern],
    ["分析方式", selected.value?.writing_style?.analysis_style], ["信息推进", selected.value?.writing_patterns?.information_progression],
    ["段内组织", selected.value?.writing_patterns?.paragraph_architecture], ["衔接方式", selected.value?.writing_patterns?.transition_style],
    ["具体程度", selected.value?.writing_patterns?.specificity_preference],
].filter(item => item[1]));
const realizationRows = computed(() => [
    ["事实表达", selected.value?.writing_patterns?.fact_expression],
    ["判断表达", selected.value?.writing_patterns?.judgment_expression],
    ["事实到判断", selected.value?.writing_patterns?.fact_judgment_transition],
    ["信息取舍", selected.value?.writing_patterns?.information_compression],
    ["依据表述", selected.value?.writing_patterns?.attribution_style],
    ["分析框架", selected.value?.reasoning_profile?.analysis_framework],
    ["风险措辞", selected.value?.reasoning_profile?.risk_expression],
    ["建议表达", selected.value?.reasoning_profile?.suggestion_style],
].filter(item => item[1]));
const documentRoles = computed(() => [
    ["document_title", "主标题"], ["heading_1", "一级标题"], ["heading_2", "二级标题"], ["heading_3", "三级标题"], ["body", "正文"],
].map(([key, label]) => ({ key, label, value: role(schema.value, key) })).filter(item => Object.keys(item.value || {}).length));
const learningBusy = computed(() => uploading.value || ["queued", "running"].includes(learningJob.value?.status));
const phaseLabel = computed(() => ({ queued: "等待开始", parsing: "解析文件", classifying: "识别语言与结构", profiling: "生成模板画像", completed: "学习完成", failed: "学习失败" }[learningJob.value?.phase] || "准备上传"));
async function startLearning() {
    const files = Array.from(upload.value?.files || []);
    if (!files.length) {
        error.value = "请先选择模板或成品报告";
        return;
    }
    error.value = "";
    uploading.value = true;
    uploadPercent.value = 0;
    learningJob.value = null;
    const form = new FormData();
    files.forEach(file => form.append("files", file));
    try {
        learningJob.value = await uploadForm("/api/style/analyze-jobs", form, (loaded, total) => uploadPercent.value = total ? Math.round(loaded / total * 100) : 0);
        localStorage.setItem("active-style-learning-job", learningJob.value.id);
        uploadPercent.value = 100;
        await pollLearningJob(learningJob.value.id);
    }
    catch (e) {
        error.value = e.message || "上传失败";
    }
    finally {
        uploading.value = false;
    }
}
async function pollLearningJob(id) {
    window.clearTimeout(pollTimer);
    try {
        learningJob.value = await api(`/api/style/analyze-jobs/${id}`);
        if (learningJob.value.status === "completed") {
            localStorage.removeItem("active-style-learning-job");
            await qc.invalidateQueries({ queryKey: ["templates"] });
            return;
        }
        if (learningJob.value.status === "failed") {
            localStorage.removeItem("active-style-learning-job");
            error.value = learningJob.value.error || "模板学习失败";
            return;
        }
        pollTimer = window.setTimeout(() => pollLearningJob(id), 1000);
    }
    catch (e) {
        if (e?.status === 404)
            localStorage.removeItem("active-style-learning-job");
        error.value = e.message || "无法获取学习进度";
    }
}
onMounted(() => { const id = localStorage.getItem("active-style-learning-job"); if (id)
    pollLearningJob(id); });
onBeforeUnmount(() => window.clearTimeout(pollTimer));
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['template-item']} */ ;
/** @type {__VLS_StyleScopedClasses['template-item']} */ ;
/** @type {__VLS_StyleScopedClasses['template-item']} */ ;
/** @type {__VLS_StyleScopedClasses['selected']} */ ;
/** @type {__VLS_StyleScopedClasses['paper-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['paper-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['item-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-title']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-title']} */ ;
/** @type {__VLS_StyleScopedClasses['name-editor']} */ ;
/** @type {__VLS_StyleScopedClasses['name-editor']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-body']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
/** @type {__VLS_StyleScopedClasses['rule-list']} */ ;
/** @type {__VLS_StyleScopedClasses['case-add']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['template-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['template-item']} */ ;
/** @type {__VLS_StyleScopedClasses['item-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['name-editor']} */ ;
/** @type {__VLS_StyleScopedClasses['example-list']} */ ;
/** @type {__VLS_StyleScopedClasses['example-list']} */ ;
/** @type {__VLS_StyleScopedClasses['example-list']} */ ;
/** @type {__VLS_StyleScopedClasses['example-list']} */ ;
/** @type {__VLS_StyleScopedClasses['example-list']} */ ;
/** @type {__VLS_StyleScopedClasses['example-list']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-heading']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-track']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['notice']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-stack" },
});
/** @type {__VLS_StyleScopedClasses['page-stack']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "page-header" },
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "surface upload-bar" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
    ...{ class: "muted" },
});
/** @type {__VLS_StyleScopedClasses['muted']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.input)({
    ref: "upload",
    type: "file",
    accept: ".docx",
    multiple: true,
});
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (__VLS_ctx.startLearning) },
    ...{ class: "btn primary" },
    disabled: (__VLS_ctx.learningBusy),
});
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
(__VLS_ctx.learningBusy ? '处理中…' : '上传并学习');
if (__VLS_ctx.uploading || __VLS_ctx.learningJob) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "surface learning-progress" },
        'aria-live': "polite",
    });
    /** @type {__VLS_StyleScopedClasses['surface']} */ ;
    /** @type {__VLS_StyleScopedClasses['learning-progress']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "progress-heading" },
    });
    /** @type {__VLS_StyleScopedClasses['progress-heading']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
    (__VLS_ctx.uploading ? '正在上传文件' : __VLS_ctx.phaseLabel);
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.uploading ? `已上传 ${__VLS_ctx.uploadPercent}%` : (__VLS_ctx.learningJob?.message || '正在处理'));
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.uploading ? `${__VLS_ctx.uploadPercent}%` : `${__VLS_ctx.learningJob?.progress_percent || 0}%`);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "progress-track" },
    });
    /** @type {__VLS_StyleScopedClasses['progress-track']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ style: ({ width: `${__VLS_ctx.uploading ? __VLS_ctx.uploadPercent : (__VLS_ctx.learningJob?.progress_percent || 0)}%` }) },
    });
    if (!__VLS_ctx.uploading && __VLS_ctx.learningJob) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "progress-detail" },
        });
        /** @type {__VLS_StyleScopedClasses['progress-detail']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (__VLS_ctx.learningJob.processed_files || 0);
        (__VLS_ctx.learningJob.total_files || 0);
        if (__VLS_ctx.learningJob.current_file) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (__VLS_ctx.learningJob.current_file);
        }
        if (__VLS_ctx.learningJob.failed_files) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "error-text" },
            });
            /** @type {__VLS_StyleScopedClasses['error-text']} */ ;
            (__VLS_ctx.learningJob.failed_files);
        }
    }
}
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "error-text" },
    });
    /** @type {__VLS_StyleScopedClasses['error-text']} */ ;
    (__VLS_ctx.error);
}
__VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
    ...{ class: "template-layout" },
});
/** @type {__VLS_StyleScopedClasses['template-layout']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "surface templates" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['templates']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "section-head" },
});
/** @type {__VLS_StyleScopedClasses['section-head']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
    ...{ class: "muted" },
});
/** @type {__VLS_StyleScopedClasses['muted']} */ ;
if (__VLS_ctx.variants.data.value?.length) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "template-list" },
    });
    /** @type {__VLS_StyleScopedClasses['template-list']} */ ;
    for (const [item] of __VLS_vFor((__VLS_ctx.variants.data.value))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.variants.data.value?.length))
                        throw 0;
                    return (__VLS_ctx.inspect(item));
                    // @ts-ignore
                    [startLearning, learningBusy, learningBusy, uploading, uploading, uploading, uploading, uploading, uploading, learningJob, learningJob, learningJob, learningJob, learningJob, learningJob, learningJob, learningJob, learningJob, learningJob, learningJob, phaseLabel, uploadPercent, uploadPercent, uploadPercent, error, error, variants, variants, inspect,];
                } },
            key: (item.id),
            ...{ class: "template-item" },
            ...{ class: ({ selected: __VLS_ctx.selected?.id === item.id }) },
        });
        /** @type {__VLS_StyleScopedClasses['template-item']} */ ;
        /** @type {__VLS_StyleScopedClasses['selected']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "paper-preview" },
        });
        /** @type {__VLS_StyleScopedClasses['paper-preview']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "template-meta" },
        });
        /** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (item.name || `模板 ${item.id}`);
        __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
        (item.source_reports?.length || 0);
        (item.profile_version || 1);
        (item.exemplar_count || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "item-actions" },
        });
        /** @type {__VLS_StyleScopedClasses['item-actions']} */ ;
        if (item.status === 'locked') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge success" },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
            /** @type {__VLS_StyleScopedClasses['success']} */ ;
        }
        else if (item.status === 'confirmed') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge" },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
        }
        if (item.status === 'draft') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.variants.data.value?.length))
                            throw 0;
                        if (!(item.status === 'draft'))
                            throw 0;
                        return (__VLS_ctx.action.mutate({ id: item.id, op: 'confirm' }));
                        // @ts-ignore
                        [selected, action,];
                    } },
                ...{ class: "btn tertiary" },
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        }
        if (item.status !== 'locked') {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.variants.data.value?.length))
                            throw 0;
                        if (!(item.status !== 'locked'))
                            throw 0;
                        return (__VLS_ctx.action.mutate({ id: item.id, op: 'lock' }));
                        // @ts-ignore
                        [action,];
                    } },
                ...{ class: "btn tertiary" },
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.variants.data.value?.length))
                        throw 0;
                    return (__VLS_ctx.remove(item));
                    // @ts-ignore
                    [remove,];
                } },
            ...{ class: "btn tertiary danger" },
        });
        /** @type {__VLS_StyleScopedClasses['btn']} */ ;
        /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        /** @type {__VLS_StyleScopedClasses['danger']} */ ;
        // @ts-ignore
        [];
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "empty" },
    });
    /** @type {__VLS_StyleScopedClasses['empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
}
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "surface profile-panel" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-panel']} */ ;
if (__VLS_ctx.selected) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "profile-title" },
    });
    /** @type {__VLS_StyleScopedClasses['profile-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "name-editor" },
    });
    /** @type {__VLS_StyleScopedClasses['name-editor']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.input)({
        ...{ onKeyup: (__VLS_ctx.saveName) },
        maxlength: "80",
        'aria-label': "模板名称",
    });
    (__VLS_ctx.editingName);
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (__VLS_ctx.saveName) },
        ...{ class: "btn tertiary" },
        disabled: (!__VLS_ctx.editingName.trim() || __VLS_ctx.editingName.trim() === __VLS_ctx.selected.name),
    });
    /** @type {__VLS_StyleScopedClasses['btn']} */ ;
    /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "badge" },
    });
    /** @type {__VLS_StyleScopedClasses['badge']} */ ;
    (__VLS_ctx.selected.profile_version);
    __VLS_asFunctionalElement1(__VLS_intrinsics.nav, __VLS_intrinsics.nav)({
        ...{ class: "tabs" },
    });
    /** @type {__VLS_StyleScopedClasses['tabs']} */ ;
    for (const [item] of __VLS_vFor(([['document', '文档版式'], ['editorial', '语言与成文'], ['examples', '表达范例']]))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.selected))
                        throw 0;
                    return (__VLS_ctx.tab = item[0]);
                    // @ts-ignore
                    [selected, selected, selected, saveName, saveName, editingName, editingName, editingName, tab,];
                } },
            key: (item[0]),
            ...{ class: "tab" },
            ...{ class: ({ active: __VLS_ctx.tab === item[0] }) },
        });
        /** @type {__VLS_StyleScopedClasses['tab']} */ ;
        /** @type {__VLS_StyleScopedClasses['active']} */ ;
        (item[1]);
        // @ts-ignore
        [tab,];
    }
    if (__VLS_ctx.tab === 'document') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "profile-body" },
        });
        /** @type {__VLS_StyleScopedClasses['profile-body']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "confidence-line" },
        });
        /** @type {__VLS_StyleScopedClasses['confidence-line']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.confidence('document_format'));
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "schema-list" },
        });
        /** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
        for (const [item] of __VLS_vFor((__VLS_ctx.documentRoles))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                key: (item.key),
            });
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (item.label);
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (__VLS_ctx.format(item.value));
            // @ts-ignore
            [tab, confidence, documentRoles, format,];
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
            ...{ class: "schema-note" },
        });
        /** @type {__VLS_StyleScopedClasses['schema-note']} */ ;
    }
    else if (__VLS_ctx.tab === 'editorial') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "profile-body" },
        });
        /** @type {__VLS_StyleScopedClasses['profile-body']} */ ;
        if (__VLS_ctx.editorialRows.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "profile-block" },
            });
            /** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (__VLS_ctx.confidence('editorial_style'));
            __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
            for (const [item] of __VLS_vFor((__VLS_ctx.editorialRows))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                    key: (item[0]),
                });
                __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                (item[0]);
                __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                (item[1]);
                // @ts-ignore
                [tab, confidence, editorialRows, editorialRows,];
            }
        }
        else {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "notice warning" },
            });
            /** @type {__VLS_StyleScopedClasses['notice']} */ ;
            /** @type {__VLS_StyleScopedClasses['warning']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        }
        if (__VLS_ctx.realizationRows.length) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
                ...{ class: "profile-block" },
            });
            /** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
            __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
            for (const [item] of __VLS_vFor((__VLS_ctx.realizationRows))) {
                __VLS_asFunctionalElement1(__VLS_intrinsics.template)({
                    key: (item[0]),
                });
                __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
                (item[0]);
                __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
                (item[1]);
                // @ts-ignore
                [realizationRows, realizationRows,];
            }
        }
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "profile-block" },
        });
        /** @type {__VLS_StyleScopedClasses['profile-block']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.sample_count || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.dl, __VLS_intrinsics.dl)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.paragraph_chars?.p50 || '--');
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.paragraph_chars?.p25 || '--');
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.paragraph_chars?.p75 || '--');
        __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.sentences_per_paragraph?.p50 || '--');
        __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.sentence_chars?.p50 || '--');
        __VLS_asFunctionalElement1(__VLS_intrinsics.dt, __VLS_intrinsics.dt)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.dd, __VLS_intrinsics.dd)({});
        (__VLS_ctx.selected.writing_patterns?.observed_metrics?.source_report_count || __VLS_ctx.selected.source_reports?.length || 0);
    }
    else {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "profile-body" },
        });
        /** @type {__VLS_StyleScopedClasses['profile-body']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "confidence-line" },
        });
        /** @type {__VLS_StyleScopedClasses['confidence-line']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
        (__VLS_ctx.selected.exemplar_bank?.length || 0);
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
            ...{ class: "muted" },
        });
        /** @type {__VLS_StyleScopedClasses['muted']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "example-list" },
        });
        /** @type {__VLS_StyleScopedClasses['example-list']} */ ;
        for (const [item] of __VLS_vFor(((__VLS_ctx.selected.exemplar_bank || []).slice(0, 12)))) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
                key: (item.exemplar_id),
            });
            __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
            __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
            (item.section || item.chapter_type || '正文范例');
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
            (item.purpose || item.sample_type || '语言范例');
            (item.source_report);
            __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
            (item.content);
            // @ts-ignore
            [selected, selected, selected, selected, selected, selected, selected, selected, selected, selected,];
        }
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "empty" },
    });
    /** @type {__VLS_StyleScopedClasses['empty']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
}
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
