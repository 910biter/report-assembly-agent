import { ref } from "vue";
import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { api } from "@/api/http";
const qc = useQueryClient();
const upload = ref();
const selected = ref(null);
const schema = ref(null);
const error = ref("");
const variants = useQuery({ queryKey: ["templates"], queryFn: () => api("/api/style/variants") });
const analyze = useMutation({ mutationFn: () => { const fd = new FormData(); for (const file of upload.value?.files || [])
        fd.append("files", file); return api("/api/style/analyze", { method: "POST", body: fd }); }, onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }), onError: e => error.value = e instanceof Error ? e.message : "学习失败" });
const action = useMutation({ mutationFn: ({ id, op }) => api(`/api/style/variants/${id}${op === 'delete' ? '' : `/${op}`}`, { method: op === 'delete' ? "DELETE" : "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }) });
async function inspect(item) { selected.value = item; schema.value = null; try {
    schema.value = await api(`/api/style/variants/${item.id}/template-schema`);
}
catch {
    schema.value = { error: "该模板暂无结构化 Schema" };
} }
function remove(item) { if (confirm(`删除模板“${item.name || item.label || item.id}”？`))
    action.mutate({ id: item.id, op: "delete" }); }
function role(schema, key) { return schema?.roles?.[key] || schema?.styles?.[key] || schema?.[key] || {}; }
function format(value) { if (!value || typeof value !== "object")
    return "尚未识别"; const font = value.font?.name || value.font_name || value.font || "字体未定"; const size = value.font?.size_pt || value.size_pt || value.size || ""; const align = value.paragraph?.alignment || value.alignment || ""; return [font, size ? `${size}pt` : "", align].filter(Boolean).join(" / "); }
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
/** @type {__VLS_StyleScopedClasses['paper-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['paper-preview']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['template-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['template-layout']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['template-grid']} */ ;
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
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.analyze.mutate());
            // @ts-ignore
            [analyze,];
        } },
    ...{ class: "btn primary" },
    disabled: (__VLS_ctx.analyze.isPending.value),
});
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['primary']} */ ;
(__VLS_ctx.analyze.isPending.value ? '正在学习…' : '上传并学习');
if (__VLS_ctx.error) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
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
        ...{ class: "template-grid" },
    });
    /** @type {__VLS_StyleScopedClasses['template-grid']} */ ;
    for (const [item] of __VLS_vFor((__VLS_ctx.variants.data.value))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.article, __VLS_intrinsics.article)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.variants.data.value?.length))
                        throw 0;
                    return (__VLS_ctx.inspect(item));
                    // @ts-ignore
                    [analyze, analyze, error, error, variants, variants, inspect,];
                } },
            key: (item.id),
            ...{ class: "template-item" },
        });
        /** @type {__VLS_StyleScopedClasses['template-item']} */ ;
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
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        __VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
        (item.name || item.label || `模板 ${item.id}`);
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({});
        (item.description || 'Word 文档模板');
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "button-row" },
        });
        /** @type {__VLS_StyleScopedClasses['button-row']} */ ;
        if (item.locked) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge success" },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
            /** @type {__VLS_StyleScopedClasses['success']} */ ;
        }
        else if (item.confirmed) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
                ...{ class: "badge" },
            });
            /** @type {__VLS_StyleScopedClasses['badge']} */ ;
        }
        if (!item.confirmed) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.variants.data.value?.length))
                            throw 0;
                        if (!(!item.confirmed))
                            throw 0;
                        return (__VLS_ctx.action.mutate({ id: item.id, op: 'confirm' }));
                        // @ts-ignore
                        [action,];
                    } },
                ...{ class: "btn tertiary" },
            });
            /** @type {__VLS_StyleScopedClasses['btn']} */ ;
            /** @type {__VLS_StyleScopedClasses['tertiary']} */ ;
        }
        if (!item.locked) {
            __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.variants.data.value?.length))
                            throw 0;
                        if (!(!item.locked))
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
    ...{ class: "surface schema-panel" },
});
/** @type {__VLS_StyleScopedClasses['surface']} */ ;
/** @type {__VLS_StyleScopedClasses['schema-panel']} */ ;
if (__VLS_ctx.selected) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({});
    (__VLS_ctx.selected.name || __VLS_ctx.selected.label);
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "muted" },
    });
    /** @type {__VLS_StyleScopedClasses['muted']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "schema-list" },
    });
    /** @type {__VLS_StyleScopedClasses['schema-list']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.format(__VLS_ctx.role(__VLS_ctx.schema, 'document_title')));
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.format(__VLS_ctx.role(__VLS_ctx.schema, 'heading_1')));
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.format(__VLS_ctx.role(__VLS_ctx.schema, 'heading_2')));
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.format(__VLS_ctx.role(__VLS_ctx.schema, 'heading_3')));
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.b, __VLS_intrinsics.b)({});
    (__VLS_ctx.format(__VLS_ctx.role(__VLS_ctx.schema, 'body')));
    __VLS_asFunctionalElement1(__VLS_intrinsics.details, __VLS_intrinsics.details)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.summary, __VLS_intrinsics.summary)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.pre, __VLS_intrinsics.pre)({});
    (JSON.stringify(__VLS_ctx.schema, null, 2));
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
[selected, selected, selected, format, format, format, format, format, role, role, role, role, role, schema, schema, schema, schema, schema, schema,];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
