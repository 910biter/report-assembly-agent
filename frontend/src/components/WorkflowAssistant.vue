<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/http";
import AppIcon from "@/components/AppIcon.vue";
import ReviewCopilot from "@/components/ReviewCopilot.vue";
import { useUiStore } from "@/stores/ui";

const route = useRoute();
const ui = useUiStore();
ui.ensureDraftId();
const open = ref(false);
const tab = ref<"progress" | "artifacts" | "discuss">("discuss");
const task = ref<any>(null);
const report = ref<any>(null);
const workspace = ref<any>(null);
const artifactType = ref("task_brief");
const selected = ref<any>(null);
const draft = computed(() => ui.taskDraft);
const draftId = computed(() => ui.draftId);
const loading = ref(false);
const expanded = ref(false);
const savedPanelSize = (() => {
  try {
    return JSON.parse(localStorage.getItem("ira-assistant-panel-size") || "{}");
  } catch {
    return {};
  }
})();
const panelSize = ref({
  width: Number(savedPanelSize.width) || 560,
  height: Number(savedPanelSize.height) || 720,
});
const panelStyle = computed(() => {
  if (expanded.value) {
    return {
      width: `${Math.min(860, window.innerWidth - 48)}px`,
      height: `${Math.max(480, window.innerHeight - 48)}px`,
    };
  }
  return {
    width: `${Math.min(panelSize.value.width, window.innerWidth - 32)}px`,
    height: `${Math.min(panelSize.value.height, window.innerHeight - 100)}px`,
  };
});
let timer: number | undefined;
let stopResize: (() => void) | undefined;

function startPanelResize(event: PointerEvent) {
  if (window.innerWidth <= 600) return;
  event.preventDefault();
  expanded.value = false;
  const origin = {
    x: event.clientX,
    y: event.clientY,
    width: panelSize.value.width,
    height: panelSize.value.height,
  };
  const move = (next: PointerEvent) => {
    panelSize.value = {
      width: Math.max(
        380,
        Math.min(window.innerWidth - 32, origin.width + origin.x - next.clientX),
      ),
      height: Math.max(
        480,
        Math.min(window.innerHeight - 80, origin.height + origin.y - next.clientY),
      ),
    };
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    localStorage.setItem("ira-assistant-panel-size", JSON.stringify(panelSize.value));
    stopResize = undefined;
  };
  stopResize?.();
  stopResize = stop;
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop, { once: true });
}

const visible = computed(
  () => route.path === "/" || route.path.startsWith("/tasks/") || route.path.startsWith("/reports/"),
);
const taskId = computed(() => String(route.params.taskId || task.value?.task_id || report.value?.task_id || ""));
const reportId = computed(() => Number(route.params.reportId || task.value?.report_id || report.value?.id || 0) || undefined);
const isDraft = computed(() => route.path === "/");
const interactionScopeReady = computed(() =>
  isDraft.value ? Boolean(draftId.value) : Boolean(taskId.value || reportId.value),
);
const activeArtifact = computed(() => {
  if (isDraft.value) {
    return {
      artifact_type: "task_draft",
      artifact_version: "draft",
      object_id: draftId.value,
      current: draft.value,
      title: "任务需求草案",
    };
  }
  return selected.value || {
    artifact_type: "task_brief",
    artifact_version: String(task.value?.run_revision || 1),
    object_id: "",
    current: {
      theme: task.value?.theme || "",
      requirements: task.value?.user_requirements || "",
      stage: task.value?.stage || "",
      queue_status: task.value?.queue_status || {},
      parse_progress: task.value?.parse_progress || {},
      evidence_progress: task.value?.evidence_progress || {},
      write_progress: task.value?.write_progress || {},
    },
    title: "当前任务",
  };
});
const prompts = computed(() =>
  isDraft.value
    ? [
        "帮我判断当前需求是否清楚，还缺少哪些业务信息？",
        "根据这个目标，建议报告重点回答哪些问题？",
        "我应该准备哪些类型的材料？",
      ]
    : [
        "当前运行到哪一步，已经完成什么，下一步是什么？",
        "请解释当前阶段的输入、产出和必要性。",
        "如果修改当前产物，后续哪些环节需要重新计算？",
      ],
);

const stageMeta: Record<string, { label: string; description: string }> = {
  created: { label: "等待开始", description: "任务目标和材料已登记，尚未进入处理。" },
  parsing: { label: "材料解析", description: "把文件转换为带来源位置的内容单元。" },
  dedup: { label: "去重归并", description: "识别重复材料和重复内容，保留来源关系。" },
  material_analysis: { label: "材料理解", description: "判断材料角色、可证明范围和信息缺口。" },
  planning: { label: "分析规划", description: "确定需要回答的问题和证据提取范围。" },
  evidence: { label: "事实与证据", description: "提取事实并绑定原始材料位置。" },
  conflict: { label: "冲突核验", description: "检查多来源对同一事项是否存在矛盾。" },
  analysis: { label: "综合分析", description: "基于事实形成带依据和置信度的分析判断。" },
  writing: { label: "报告生成", description: "先组织叙事计划，再按章节生成并绑定来源。" },
  knowledge: { label: "深度检查", description: "报告可审核后继续执行知识整理和深度质检。" },
  review: { label: "等待审核", description: "报告草稿已形成，可以审阅、讨论和修改。" },
  done: { label: "已完成", description: "报告已审核，可导出或进行增量更新。" },
  paused: { label: "已暂停", description: "任务停在安全边界，可继续运行。" },
  failed: { label: "运行异常", description: "当前阶段未完成，请查看错误并决定是否重试。" },
};
const currentStage = computed(() => stageMeta[String(task.value?.stage || "created")] || {
  label: String(task.value?.stage || "处理中"), description: "系统正在处理当前任务。",
});

async function loadContext() {
  if (!visible.value || isDraft.value) {
    task.value = null;
    report.value = null;
    workspace.value = null;
    selected.value = null;
    return;
  }
  loading.value = true;
  try {
    if (route.path.startsWith("/tasks/")) {
      task.value = await api<any>(`/api/tasks/${String(route.params.taskId)}/assistant-context`);
      report.value = null;
    } else {
      report.value = await api<any>(`/api/reports/${String(route.params.reportId)}/assistant-context`);
      task.value = report.value;
    }
    if (tab.value === "artifacts") await loadArtifacts(artifactType.value);
  } catch {
    task.value = null;
  } finally {
    loading.value = false;
  }
}

async function loadArtifacts(type = artifactType.value) {
  if (!taskId.value) return;
  artifactType.value = type;
  workspace.value = await api<any>(
    `/api/tasks/${taskId.value}/review-workspace?artifact_type=${encodeURIComponent(type)}&offset=0&limit=30`,
  );
  const items = workspace.value?.items || [];
  if (!selected.value || selected.value.artifact_type !== type) selected.value = items[0] || null;
}

async function switchTab(value: "progress" | "artifacts" | "discuss") {
  tab.value = value;
  if (value === "artifacts" && taskId.value) await loadArtifacts();
}

function discussArtifact(item: any) {
  selected.value = item;
  tab.value = "discuss";
}

function proposalApplied(proposal: any) {
  if (isDraft.value) {
    ui.applyDraftProposal(proposal?.after || {});
  } else {
    loadContext();
  }
}

watch(() => route.fullPath, () => {
  open.value = false;
  tab.value = "discuss";
  selected.value = null;
  loadContext();
});
onMounted(() => {
  loadContext();
  timer = window.setInterval(() => {
    if (open.value && taskId.value) loadContext();
  }, 5000);
});
onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
  stopResize?.();
});
</script>

<template>
  <div v-if="visible" class="workflow-assistant" :class="{ open }">
    <section v-if="open" class="assistant-panel" :style="panelStyle">
      <button
        class="panel-resize-handle"
        type="button"
        aria-label="调整助手窗口大小"
        title="拖动调整窗口大小"
        @pointerdown="startPanelResize"
      ></button>
      <header>
        <div><small>{{ isDraft ? "任务创建前" : "当前任务" }}</small><b>报告协作助手</b></div>
        <div class="panel-actions">
          <button type="button" @click="expanded = !expanded">
            {{ expanded ? "还原" : "扩展" }}
          </button>
          <button aria-label="关闭助手" @click="open = false">
            <AppIcon name="close" :size="17" />
          </button>
        </div>
      </header>
      <nav>
        <button :class="{ active: tab === 'progress' }" @click="switchTab('progress')">{{ isDraft ? "需求" : "进度" }}</button>
        <button v-if="!isDraft" :class="{ active: tab === 'artifacts' }" @click="switchTab('artifacts')">历史产物</button>
        <button :class="{ active: tab === 'discuss' }" @click="switchTab('discuss')">讨论</button>
      </nav>
      <div class="assistant-body" :class="{ 'discussion-active': tab === 'discuss' }">
        <div v-if="tab === 'progress'" class="progress-view">
          <template v-if="isDraft">
            <small>当前需求草案</small>
            <h3>{{ draft.theme || "尚未填写报告主题" }}</h3>
            <p>{{ draft.requirements || "填写业务目标后，可以先和助手讨论报告重点、材料范围和预期结构。" }}</p>
            <button class="ask-link" @click="tab = 'discuss'">讨论需求是否完整</button>
          </template>
          <template v-else>
            <div class="stage-state"><span :class="task?.stage"></span><div><small>当前阶段</small><h3>{{ currentStage.label }}</h3></div></div>
            <p>{{ currentStage.description }}</p>
            <dl>
              <div><dt>材料解析</dt><dd>{{ task?.parse_progress?.done || 0 }} / {{ task?.parse_progress?.total || "—" }}</dd></div>
              <div><dt>证据批次</dt><dd>{{ task?.evidence_progress?.done || 0 }} / {{ task?.evidence_progress?.total || "—" }}</dd></div>
              <div><dt>章节写作</dt><dd>{{ task?.write_progress?.done || 0 }} / {{ task?.write_progress?.total || "—" }}</dd></div>
            </dl>
            <button class="ask-link" @click="tab = 'discuss'">询问当前阶段如何设计</button>
          </template>
        </div>
        <div v-else-if="tab === 'artifacts'" class="artifact-view">
          <div class="artifact-types">
            <button v-for="group in workspace?.groups || []" :key="group.artifact_type" :disabled="!group.available" :class="{ active: artifactType === group.artifact_type }" @click="loadArtifacts(group.artifact_type)">
              {{ group.label }}<span>{{ group.count }}</span>
            </button>
          </div>
          <div v-if="workspace?.items?.length" class="artifact-items">
            <button v-for="item in workspace.items" :key="`${item.artifact_type}:${item.object_id}`" @click="discussArtifact(item)">
              <b>{{ item.title }}</b><span>{{ item.summary || "查看并讨论" }}</span>
            </button>
          </div>
          <div v-else class="assistant-empty">该阶段尚未形成可审阅产物。</div>
        </div>
        <div v-else class="discussion-view">
          <div class="discussion-scope"><small>正在讨论</small><b>{{ activeArtifact.title }}</b></div>
          <ReviewCopilot
            v-if="interactionScopeReady"
            :key="`${taskId}:${activeArtifact.artifact_type}:${activeArtifact.object_id}`"
            compact
            :task-id="taskId"
            :report-id="reportId"
            :artifact-type="activeArtifact.artifact_type"
            :artifact-version="activeArtifact.artifact_version"
            :object-id="activeArtifact.object_id"
            :current="activeArtifact.current"
            :suggested-prompts="prompts"
            @applied="proposalApplied"
          />
          <div v-else class="assistant-empty">正在恢复当前任务的讨论记录…</div>
        </div>
      </div>
    </section>
    <button class="assistant-orb" :aria-label="open ? '关闭报告协作助手' : '打开报告协作助手'" @click="open = !open">
      <span class="orb-mark"><i></i><i></i><i></i></span>
      <span class="orb-label">{{ isDraft ? "先讨论" : "问进度" }}</span>
    </button>
  </div>
</template>

<style scoped>
.workflow-assistant{position:fixed;right:24px;bottom:24px;z-index:80}.assistant-orb{display:flex;align-items:center;gap:9px;height:46px;padding:0 15px 0 11px;border:1px solid #173f6c;border-radius:23px;background:#245b90;color:#fff;box-shadow:0 8px 24px rgba(24,55,89,.22);font-weight:650}.assistant-orb:hover{background:#1e4e7d}.orb-mark{display:flex;align-items:flex-end;justify-content:center;gap:2px;width:22px;height:22px;padding:5px 4px;border:1px solid rgba(255,255,255,.42);border-radius:50%}.orb-mark i{display:block;width:2px;background:#fff}.orb-mark i:nth-child(1){height:5px}.orb-mark i:nth-child(2){height:10px}.orb-mark i:nth-child(3){height:7px}.orb-label{font-size:13px}.assistant-panel{position:absolute;right:0;bottom:58px;width:min(430px,calc(100vw - 32px));height:min(690px,calc(100vh - 100px));display:grid;grid-template-rows:auto auto minmax(0,1fr);overflow:hidden;border:1px solid var(--color-border);border-radius:8px;background:#fff;box-shadow:0 18px 50px rgba(24,39,57,.2)}.assistant-panel>header{display:flex;align-items:center;justify-content:space-between;padding:15px 17px;border-bottom:1px solid var(--color-border)}.assistant-panel>header small,.assistant-panel>header b{display:block}.assistant-panel>header small{color:var(--color-muted);font-size:11px}.assistant-panel>header b{margin-top:2px}.assistant-panel>header button{display:grid;place-items:center;width:30px;height:30px;border:0;background:transparent;color:var(--color-muted)}.assistant-panel>nav{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;padding:0 14px;border-bottom:1px solid var(--color-border)}.assistant-panel>nav button{padding:11px 4px;border:0;border-bottom:2px solid transparent;background:transparent;color:var(--color-muted)}.assistant-panel>nav button.active{border-bottom-color:var(--color-primary);color:var(--color-primary);font-weight:650}.assistant-body{min-height:0;overflow:auto}.progress-view{padding:22px}.progress-view>small{color:var(--color-primary)}.progress-view h3{margin:4px 0 9px;font-size:18px}.progress-view p{margin:0;color:var(--color-muted);line-height:1.7}.stage-state{display:flex;align-items:center;gap:12px}.stage-state>span{width:9px;height:9px;border-radius:50%;background:var(--color-primary);box-shadow:0 0 0 5px var(--color-primary-soft)}.stage-state>span.review,.stage-state>span.done{background:var(--color-success);box-shadow:0 0 0 5px #edf7ef}.stage-state>span.failed{background:var(--color-danger);box-shadow:0 0 0 5px #fff1ef}.progress-view dl{margin:20px 0;border-top:1px solid var(--color-border)}.progress-view dl div{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--color-border)}.progress-view dt{color:var(--color-muted)}.progress-view dd{margin:0;font-weight:650}.ask-link{margin-top:17px;padding:0;border:0;background:transparent;color:var(--color-primary);font-weight:650}.artifact-view{display:grid;grid-template-columns:125px 1fr;min-height:100%}.artifact-types{padding:10px;border-right:1px solid var(--color-border);background:#fafbfc}.artifact-types button{display:flex;justify-content:space-between;width:100%;padding:9px 8px;border:0;border-left:2px solid transparent;background:transparent;color:var(--color-muted);text-align:left}.artifact-types button.active{border-left-color:var(--color-primary);background:var(--color-primary-soft);color:var(--color-primary)}.artifact-types button:disabled{opacity:.38}.artifact-types span{font-size:11px}.artifact-items button{display:grid;width:100%;gap:4px;padding:12px 14px;border:0;border-bottom:1px solid var(--color-border);background:#fff;text-align:left}.artifact-items button:hover{background:#f7f9fb}.artifact-items span{display:-webkit-box;overflow:hidden;color:var(--color-muted);font-size:11px;-webkit-line-clamp:2;-webkit-box-orient:vertical}.assistant-empty{padding:28px;color:var(--color-muted)}.discussion-view{padding:17px}.discussion-scope{padding-bottom:12px;margin-bottom:14px;border-bottom:1px solid var(--color-border)}.discussion-scope small,.discussion-scope b{display:block}.discussion-scope small{color:var(--color-muted)}.discussion-scope b{margin-top:2px}.loading{padding:20px;color:var(--color-muted)}
.assistant-body.discussion-active{overflow:hidden}
.discussion-view{height:100%;min-height:0;display:grid;grid-template-rows:auto minmax(0,1fr)}
.panel-actions{display:flex;align-items:center;gap:3px}.panel-actions button:first-child{width:auto;min-width:42px;padding:0 7px;color:var(--color-primary);font-size:12px}.panel-resize-handle{position:absolute;top:-5px;left:-5px;z-index:2;width:18px;height:18px;padding:0;border:0;border-top:2px solid var(--color-border-strong);border-left:2px solid var(--color-border-strong);background:transparent;cursor:nwse-resize}
:global(body:has(.collaboration-shell) .workflow-assistant){display:none}
@media(max-width:600px){.workflow-assistant{right:14px;bottom:14px}.assistant-panel{position:fixed;inset:12px;width:auto;height:auto;border-radius:7px}.orb-label{display:none}.assistant-orb{width:48px;padding:0;justify-content:center}}
@media(max-width:600px){.assistant-panel{width:auto!important;height:auto!important}.panel-resize-handle{display:none}}
</style>
