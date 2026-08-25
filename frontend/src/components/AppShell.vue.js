import { computed } from "vue";
import { useRoute, RouterLink } from "vue-router";
import { useUiStore } from "@/stores/ui";
import AppIcon from "./AppIcon.vue";
import WorkflowAssistant from "./WorkflowAssistant.vue";
const route = useRoute();
const ui = useUiStore();
const editor = computed(() => Boolean(route.meta.editor));
const nav = [
    { to: "/", label: "工作台", icon: "home" },
    { to: "/tasks", label: "任务", icon: "tasks" },
    { to: "/materials", label: "材料库", icon: "files" },
    { to: "/style", label: "模板中心", icon: "template" },
    { to: "/settings", label: "系统设置", icon: "settings" },
];
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['brand']} */ ;
/** @type {__VLS_StyleScopedClasses['brand']} */ ;
/** @type {__VLS_StyleScopedClasses['brand']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['topbar']} */ ;
/** @type {__VLS_StyleScopedClasses['content']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar']} */ ;
/** @type {__VLS_StyleScopedClasses['shell-main']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-scrim']} */ ;
/** @type {__VLS_StyleScopedClasses['topbar']} */ ;
/** @type {__VLS_StyleScopedClasses['content']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "app-shell" },
    ...{ class: ({ 'is-editor': __VLS_ctx.editor }) },
});
/** @type {__VLS_StyleScopedClasses['app-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['is-editor']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.aside, __VLS_intrinsics.aside)({
    ...{ class: "sidebar" },
    ...{ class: ({ open: __VLS_ctx.ui.navOpen }) },
});
/** @type {__VLS_StyleScopedClasses['sidebar']} */ ;
/** @type {__VLS_StyleScopedClasses['open']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "brand" },
});
/** @type {__VLS_StyleScopedClasses['brand']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "brand-mark" },
});
/** @type {__VLS_StyleScopedClasses['brand-mark']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.small, __VLS_intrinsics.small)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.nav, __VLS_intrinsics.nav)({});
for (const [item] of __VLS_vFor((__VLS_ctx.nav))) {
    let __VLS_0;
    /** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
    RouterLink;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
        ...{ 'onClick': {} },
        key: (item.to),
        to: (item.to),
        ...{ class: ({ active: __VLS_ctx.route.path === item.to || (item.to === '/tasks' && __VLS_ctx.route.path.startsWith('/tasks/')) }) },
    }));
    const __VLS_2 = __VLS_1({
        ...{ 'onClick': {} },
        key: (item.to),
        to: (item.to),
        ...{ class: ({ active: __VLS_ctx.route.path === item.to || (item.to === '/tasks' && __VLS_ctx.route.path.startsWith('/tasks/')) }) },
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    let __VLS_5;
    const __VLS_6 = {
        /** @type {typeof __VLS_5.click} */
        onClick: (...[$event]) => {
            return (__VLS_ctx.ui.closeNav());
            // @ts-ignore
            [editor, ui, ui, nav, route, route,];
        },
    };
    /** @type {__VLS_StyleScopedClasses['active']} */ ;
    const { default: __VLS_7 } = __VLS_3.slots;
    const __VLS_8 = AppIcon;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent1(__VLS_8, new __VLS_8({
        name: (item.icon),
    }));
    const __VLS_10 = __VLS_9({
        name: (item.icon),
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (item.label);
    // @ts-ignore
    [];
    var __VLS_3;
    var __VLS_4;
    // @ts-ignore
    [];
}
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "nav-foot" },
});
/** @type {__VLS_StyleScopedClasses['nav-foot']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "health-dot" },
});
/** @type {__VLS_StyleScopedClasses['health-dot']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "shell-main" },
});
/** @type {__VLS_StyleScopedClasses['shell-main']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.header, __VLS_intrinsics.header)({
    ...{ class: "topbar" },
});
/** @type {__VLS_StyleScopedClasses['topbar']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
    ...{ onClick: (...[$event]) => {
            return (__VLS_ctx.ui.toggleNav());
            // @ts-ignore
            [ui,];
        } },
    ...{ class: "nav-toggle" },
    'aria-label': "打开导航",
});
/** @type {__VLS_StyleScopedClasses['nav-toggle']} */ ;
const __VLS_13 = AppIcon;
// @ts-ignore
const __VLS_14 = __VLS_asFunctionalComponent1(__VLS_13, new __VLS_13({
    name: "menu",
}));
const __VLS_15 = __VLS_14({
    name: "menu",
}, ...__VLS_functionalComponentArgsRest(__VLS_14));
__VLS_asFunctionalElement1(__VLS_intrinsics.strong, __VLS_intrinsics.strong)({});
(__VLS_ctx.route.meta.title);
__VLS_asFunctionalElement1(__VLS_intrinsics.main, __VLS_intrinsics.main)({
    ...{ class: "content" },
    ...{ class: ({ wide: __VLS_ctx.route.meta.wide, editor: __VLS_ctx.editor }) },
});
/** @type {__VLS_StyleScopedClasses['content']} */ ;
/** @type {__VLS_StyleScopedClasses['wide']} */ ;
/** @type {__VLS_StyleScopedClasses['editor']} */ ;
var __VLS_18 = {};
if (__VLS_ctx.ui.navOpen) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.button, __VLS_intrinsics.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.ui.navOpen))
                    throw 0;
                return (__VLS_ctx.ui.closeNav());
                // @ts-ignore
                [editor, ui, ui, route, route,];
            } },
        ...{ class: "nav-scrim" },
        'aria-label': "关闭导航",
    });
    /** @type {__VLS_StyleScopedClasses['nav-scrim']} */ ;
}
const __VLS_20 = WorkflowAssistant;
// @ts-ignore
const __VLS_21 = __VLS_asFunctionalComponent1(__VLS_20, new __VLS_20({}));
const __VLS_22 = __VLS_21({}, ...__VLS_functionalComponentArgsRest(__VLS_21));
// @ts-ignore
var __VLS_19 = __VLS_18;
// @ts-ignore
[];
const __VLS_base = (await import('vue')).defineComponent({});
const __VLS_export = {};
export default {};
