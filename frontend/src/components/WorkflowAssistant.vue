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
const open = computed({
  get: () => ui.assistant.open,
  set: (value: boolean) => {
    ui.assistant.open = value;
  },
});
const tab = computed({
  get: () => ui.assistant.tab,
  set: (value: "progress" | "artifacts" | "discuss") =>
    ui.setAssistantTab(value),
});
const task = ref<any>(null);
const report = ref<any>(null);
const workspace = ref<any>(null);
const artifactType = ref("task_brief");
const selected = ref<any>(null);
const references = ref<any[]>([]);
const draft = computed(() => ui.taskDraft);
const draftId = computed(() => ui.draftId);
const loading = ref(false);
const savedPanelSize = (() => {
  try {
    return JSON.parse(localStorage.getItem("ira-assistant-panel-frame-v3") || "{}");
  } catch {
    return {};
  }
})();
const panelSize = ref({
  width: Number(savedPanelSize.width) || 680,
  height: Number(savedPanelSize.height) || 700,
});
const panelPosition = ref({
  right: Number(savedPanelSize.right) || 0,
  bottom: Number(savedPanelSize.bottom) || 60,
});
const resizeLabels: Record<PanelEdge, string> = {
  left: "左侧",
  right: "右侧",
  top: "上侧",
  bottom: "下侧",
};
const panelStyle = computed(() => {
  const viewportInset = 8;
  const shellInset = 24;
  const preferredRight = Math.max(0, panelPosition.value.right);
  const preferredBottom = Math.max(0, panelPosition.value.bottom);
  const availableWidth = Math.max(
    1,
    window.innerWidth - shellInset - preferredRight - viewportInset,
  );
  const availableHeight = Math.max(
    1,
    window.innerHeight - shellInset - preferredBottom - viewportInset,
  );
  const width = Math.min(panelSize.value.width, availableWidth);
  const height = Math.min(
    panelSize.value.height,
    availableHeight,
    Math.floor(window.innerHeight * 0.82),
  );
  return {
    width: `${width}px`,
    height: `${height}px`,
    right: `${Math.min(preferredRight, Math.max(0, window.innerWidth - shellInset - width - viewportInset))}px`,
    bottom: `${Math.min(preferredBottom, Math.max(0, window.innerHeight - shellInset - height - viewportInset))}px`,
  };
});
let timer: number | undefined;
let stopResize: (() => void) | undefined;

type PanelEdge = "left" | "right" | "top" | "bottom";

function persistPanelFrame() {
  localStorage.setItem(
    "ira-assistant-panel-frame-v3",
    JSON.stringify({ ...panelSize.value, ...panelPosition.value }),
  );
}

function startPanelMove(event: PointerEvent) {
  if (window.innerWidth <= 600) return;
  if ((event.target as HTMLElement).closest("button")) return;
  event.preventDefault();
  const panel = (event.currentTarget as HTMLElement).closest(
    ".assistant-panel",
  );
  if (!panel) return;
  const origin = panel.getBoundingClientRect();
  const startX = event.clientX;
  const startY = event.clientY;
  const margin = 8;
  const move = (next: PointerEvent) => {
    const left = Math.max(
      margin,
      Math.min(
        window.innerWidth - margin - origin.width,
        origin.left + next.clientX - startX,
      ),
    );
    const top = Math.max(
      margin,
      Math.min(
        window.innerHeight - margin - origin.height,
        origin.top + next.clientY - startY,
      ),
    );
    panelPosition.value = {
      right: window.innerWidth - 24 - (left + origin.width),
      bottom: window.innerHeight - 24 - (top + origin.height),
    };
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    persistPanelFrame();
    stopResize = undefined;
  };
  stopResize?.();
  stopResize = stop;
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop, { once: true });
}

function startPanelResize(event: PointerEvent, edge: PanelEdge) {
  if (window.innerWidth <= 600) return;
  event.preventDefault();
  const panel = (event.currentTarget as HTMLElement).closest(
    ".assistant-panel",
  );
  if (!panel) return;
  const origin = panel.getBoundingClientRect();
  const margin = 8;
  const minimumWidth = 380;
  const minimumHeight = 480;
  const move = (next: PointerEvent) => {
    let left = origin.left;
    let right = origin.right;
    let top = origin.top;
    let bottom = origin.bottom;
    if (edge === "left")
      left = Math.max(margin, Math.min(next.clientX, right - minimumWidth));
    if (edge === "right")
      right = Math.min(window.innerWidth - margin, Math.max(next.clientX, left + minimumWidth));
    if (edge === "top")
      top = Math.max(margin, Math.min(next.clientY, bottom - minimumHeight));
    if (edge === "bottom")
      bottom = Math.min(window.innerHeight - margin, Math.max(next.clientY, top + minimumHeight));

    panelSize.value = { width: right - left, height: bottom - top };
    panelPosition.value = {
      right: window.innerWidth - 24 - right,
      bottom: window.innerHeight - 24 - bottom,
    };
  };
  const stop = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", stop);
    persistPanelFrame();
    stopResize = undefined;
  };
  stopResize?.();
  stopResize = stop;
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop, { once: true });
}

const visible = computed(
  () => route.path === "/" || route.path.startsWith("/tasks/") || route.path.startsWith("/reports/") || route.path === "/materials" || route.path === "/documents",
);
const taskId = computed(() => String(
  route.params.taskId || route.query.materialSession || route.query.assistantTask || route.query.task || task.value?.task_id || report.value?.task_id || "",
));
const reportId = computed(
  () =>
    Number(
      route.params.reportId || task.value?.report_id || report.value?.id || 0,
    ) || undefined,
);
const isDraft = computed(() => route.path === "/");
const isMaterialPage = computed(() => route.path === "/materials");
const isMaterialSession = computed(() => isMaterialPage.value && Boolean(route.query.materialSession));
const isDocumentSession = computed(() => route.path === "/documents" && Boolean(route.query.task));
const assistantName = computed(() => isMaterialPage.value ? "材料助手" : isDocumentSession.value ? "文档助手" : "任务协作助手");
const assistantCaption = computed(() => isDraft.value ? "任务创建前" : isMaterialPage.value ? (isMaterialSession.value ? "已选材料" : "材料库") : isDocumentSession.value ? "文档理解" : currentStage.value.label);
const interactionScopeReady = computed(() =>
  isDraft.value
    ? Boolean(draftId.value)
    : Boolean(taskId.value || reportId.value),
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
  return (
    selected.value || {
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
      title: isMaterialSession.value ? (task.value?.theme || "所选材料") : isDocumentSession.value ? (task.value?.theme || "文档理解结果") : "当前任务",
    }
  );
});
const assistantFocus = computed(() => ({
  ...activeArtifact.value,
  references: references.value,
}));
const prompts = computed(() => {
  if (
    ["sentence", "paragraph", "qa_issue"].includes(
      activeArtifact.value.artifact_type,
    )
  ) {
    return [
      "解释这段内容存在的问题及其依据。",
      "在不改变事实含义的前提下改写这段内容。",
      "检查这段内容的事实和引用是否匹配。",
    ];
  }
  if (isMaterialSession.value) {
    return [
      "这份材料主要讲了什么？请给出清晰摘要。",
      "请梳理这份材料的结构和关键要点。",
      "这份材料可以证明什么，不能证明什么？",
    ];
  }
  return isDraft.value
    ? [
        "帮我判断当前需求是否清楚，还缺少哪些业务信息？",
        "根据这个目标，建议报告重点回答哪些问题？",
        "我应该准备哪些类型的材料？",
      ]
    : [
        "当前运行到哪一步，已经完成什么，下一步是什么？",
        "请解释当前阶段的输入、产出和必要性。",
        "如果修改当前产物，后续哪些环节需要重新计算？",
      ];
});

const stageMeta: Record<string, { label: string; description: string }> = {
  created: { label: "等待开始", description: "任务目标和材料已登记，尚未进入处理。" },
  parsing: { label: "材料解析", description: "把文件转换为带来源位置的内容单元。" },
  dedup: { label: "去重归并", description: "识别重复材料和重复内容，保留来源关系。" },
  material_analysis: { label: "材料理解", description: "判断材料角色、可证明范围和信息缺口。" },
  requirement_review: { label: "需求讨论", description: "材料理解已完成，等待共同明确任务主题和报告要求。" },
  planning: { label: "分析规划", description: "确定需要回答的问题和证据提取范围。" },
  evidence: { label: "事实与证据", description: "提取事实并绑定原始材料位置。" },
  conflict: { label: "冲突核验", description: "检查多来源对同一事项是否存在矛盾。" },
  analysis: { label: "综合分析", description: "基于事实形成带依据和置信度的分析判断。" },
  directory_review: { label: "目录讨论", description: "最终目录已形成，等待审阅章节结构、顺序和重点安排。" },
  writing: { label: "报告生成", description: "先组织叙事计划，再按章节生成并绑定来源。" },
  review: { label: "等待审核", description: "当前产物已形成，可以审阅、讨论和修改。" },
  done: { label: "已完成", description: "报告已审核，可导出或进行增量更新。" },
  paused: { label: "已暂停", description: "任务停在安全边界，可继续运行。" },
  failed: { label: "运行异常", description: "当前阶段未完成，请查看错误并决定是否重试。" },
};
const currentStage = computed(() => stageMeta[String(task.value?.stage || "created")] || { label: String(task.value?.stage || "处理中"), description: "系统正在处理当前任务。" });

async function loadContext() {
  if (!visible.value || isDraft.value || !taskId.value) {
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
    } else if (route.path.startsWith("/reports/")) {
      report.value = await api<any>(`/api/reports/${String(route.params.reportId)}/assistant-context`);
      task.value = report.value;
    } else {
      task.value = await api<any>(`/api/tasks/${taskId.value}/assistant-context`);
      report.value = null;
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
  if (!selected.value || selected.value.artifact_type !== type)
    selected.value = items[0] || null;
}

async function switchTab(value: "progress" | "artifacts" | "discuss") {
  tab.value = value;
  if (value === "artifacts" && taskId.value) await loadArtifacts();
}

function discussArtifact(item: any) {
  selected.value = selected.value?.object_id === item.object_id ? null : item;
}

function beginArtifactDiscussion(item: any) {
  selected.value = item;
  addReference(item);
  tab.value = "discuss";
}

function referenceKey(item: any) {
  return `${item?.artifact_type || "reference"}:${item?.object_id || ""}:${item?.current?.quote || item?.current?.content || ""}`;
}
function referenceSummary(item: any) {
  return String(
    item?.current?.quote ||
      item?.current?.content ||
      item?.current?.note ||
      item?.title ||
      "所选内容",
  )
    .replace(/\s+/g, " ")
    .slice(0, 72);
}
function artifactPreview(item: any) {
  const current = item?.current || {};
  const value =
    current.requirements ||
    current.content ||
    current.summary ||
    current.objective ||
    current.core_message ||
    current.narrative_logic ||
    current.note ||
    current.quote ||
    item?.summary ||
    "";
  return String(value).replace(/\s+/g, " ").slice(0, 360);
}
function addReference(item: any) {
  if (!item) return;
  const key = referenceKey(item);
  const next = references.value.filter((entry) => referenceKey(entry) !== key);
  references.value = [...next, item].slice(-8);
}
function removeReference(index: number) {
  references.value = references.value.filter(
    (_item, current) => current !== index,
  );
}

function applyFocus(detail: any) {
  if (
    !detail ||
    (detail.taskId && taskId.value && String(detail.taskId) !== taskId.value)
  )
    return;
  if (detail.reference) addReference(detail.reference);
  if (detail.artifact) selected.value = detail.artifact;
  else if (!detail.reference) selected.value = detail;
  if (!detail.append && !detail.reference) references.value = [];
}

function proposalApplied(proposal: any) {
  if (isDraft.value) {
    ui.applyDraftProposal(proposal?.after || {});
  } else {
    loadContext();
    window.dispatchEvent(new CustomEvent("ira:task-artifact-updated", {
      detail: {
        taskId: taskId.value,
        proposalId: proposal?.id,
        status: proposal?.status || "accepted",
        executionStatus: proposal?.execution_status || "",
      },
    }));
  }
}

watch(
  () => route.fullPath,
  async () => {
    ui.closeAssistant();
    tab.value = "progress";
    selected.value = null;
    references.value = [];
    await loadContext();
    if (route.query.assistant === "1" && task.value) {
      ui.openAssistant({
        artifact_type: "material_role",
        object_id: String(route.query.materialId || ""),
        title: task.value.theme || "所选材料",
      });
    }
  },
);
watch(
  () => ui.assistant.revision,
  () => applyFocus(ui.assistant.focus),
);
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
        v-for="edge in ['left', 'right', 'top', 'bottom']"
        :key="edge"
        class="panel-resize-handle"
        :class="`panel-resize-handle--${edge}`"
        type="button"
        :aria-label="`拖动${resizeLabels[edge as PanelEdge]}边缘调整助手窗口大小`"
        title="拖动调整窗口大小"
        @pointerdown="startPanelResize($event, edge as PanelEdge)"
      ></button>
      <header @pointerdown="startPanelMove">
        <div>
          <small>{{ assistantCaption }}</small
          ><b>{{ assistantName }}</b>
        </div>
        <div class="panel-actions">
          <button aria-label="关闭助手" @click="open = false">
            <AppIcon name="close" :size="17" />
          </button>
        </div>
      </header>
      <nav>
        <button
          :class="{ active: tab === 'progress' }"
          @click="switchTab('progress')"
        >
          {{ isDraft ? "需求" : isMaterialPage ? "材料概览" : "任务状态" }}
        </button>
        <button
          v-if="!isDraft && !isMaterialPage"
          :class="{ active: tab === 'artifacts' }"
          @click="switchTab('artifacts')"
        >
          产物
        </button>
        <button
          :class="{ active: tab === 'discuss' }"
          @click="switchTab('discuss')"
        >
          协作对话
        </button>
      </nav>
      <div
        class="assistant-body"
        :class="{ 'discussion-active': tab === 'discuss' }"
      >
        <div v-if="tab === 'progress'" class="progress-view">
          <template v-if="isDraft">
            <small>当前需求草案</small>
            <h3>{{ draft.theme || "尚未填写报告主题" }}</h3>
            <p>
              {{
                draft.requirements ||
                "填写业务目标后，可以先和助手讨论报告重点、材料范围和预期结构。"
              }}
            </p>
            <button class="ask-link" @click="tab = 'discuss'">
              讨论需求是否完整
            </button>
          </template>
          <template v-else-if="isMaterialPage">
            <template v-if="isMaterialSession">
              <small>讨论范围</small>
              <h3>{{ task?.theme?.replace("材料理解：", "") || "所选材料" }}</h3>
              <p>助手只会检索这份材料的解析单元和材料理解结果，可用于快速了解内容、结构、关键事实及证据边界。</p>
              <button class="ask-link" @click="tab = 'discuss'">开始讨论材料</button>
            </template>
            <template v-else>
              <small>材料理解</small>
              <h3>选择一份材料</h3>
              <p>在材料详情中点击“与助手讨论”，即可围绕单份材料查看摘要、结构、关键事实和证据边界。</p>
            </template>
          </template>
          <template v-else>
            <div class="stage-state">
              <span :class="task?.stage"></span>
              <div>
                <small>当前阶段</small>
                <h3>{{ currentStage.label }}</h3>
              </div>
            </div>
            <p>{{ currentStage.description }}</p>
            <dl>
              <div>
                <dt>材料解析</dt>
                <dd>
                  {{ task?.parse_progress?.done || 0 }} /
                  {{ task?.parse_progress?.total || "—" }}
                </dd>
              </div>
              <div>
                <dt>证据批次</dt>
                <dd>
                  {{ task?.evidence_progress?.done || 0 }} /
                  {{ task?.evidence_progress?.total || "—" }}
                </dd>
              </div>
              <div>
                <dt>章节写作</dt>
                <dd>
                  {{ task?.write_progress?.done || 0 }} /
                  {{ task?.write_progress?.total || "—" }}
                </dd>
              </div>
            </dl>
            <button class="ask-link" @click="tab = 'discuss'">
              询问当前阶段如何设计
            </button>
          </template>
        </div>
        <div v-else-if="tab === 'artifacts'" class="artifact-view">
          <div class="artifact-types">
            <button
              v-for="group in workspace?.groups || []"
              :key="group.artifact_type"
              :disabled="!group.available"
              :class="{ active: artifactType === group.artifact_type }"
              @click="loadArtifacts(group.artifact_type)"
            >
              {{ group.label }}<span>{{ group.count }}</span>
            </button>
          </div>
          <div v-if="workspace?.items?.length" class="artifact-items">
            <article
              v-for="item in workspace.items"
              :key="`${item.artifact_type}:${item.object_id}`"
              :class="{ active: selected?.object_id === item.object_id }"
            >
              <button @click="discussArtifact(item)">
                <span
                  ><b>{{ item.title }}</b
                  ><i>{{
                    selected?.object_id === item.object_id ? "收起" : "查看"
                  }}</i></span
                >
                <small v-if="selected?.object_id !== item.object_id">{{
                  item.summary || "查看内容"
                }}</small>
              </button>
              <div
                v-if="selected?.object_id === item.object_id"
                class="assistant-artifact-detail"
              >
                <p>
                  {{
                    artifactPreview(item) ||
                    "该产物已形成，可交给助手结合任务上下文解释。"
                  }}
                </p>
                <button
                  class="btn primary"
                  @click="beginArtifactDiscussion(item)"
                >
                  引用并讨论
                </button>
              </div>
            </article>
          </div>
          <div v-else class="assistant-empty">该阶段尚未形成可审阅产物。</div>
        </div>
        <div v-else class="discussion-view">
          <div v-if="references.length" class="reference-list discussion-references">
            <span
              v-for="(item, index) in references"
              :key="referenceKey(item)"
            >
              <i>引用</i>{{ referenceSummary(item) }}
              <button
                type="button"
                aria-label="移除引用"
                @click="removeReference(index)"
              >
                ×
              </button>
            </span>
            <button
              type="button"
              class="clear-references"
              @click="references = []"
            >
              清除引用
            </button>
          </div>
          <ReviewCopilot
            v-if="interactionScopeReady"
            :key="isDraft ? `draft:${draftId}` : `task:${taskId}`"
            compact
            :task-id="taskId"
            :report-id="isDraft ? reportId : undefined"
            :artifact-type="isDraft ? 'task_draft' : 'task_control'"
            :artifact-version="isDraft ? 'draft' : ''"
            :object-id="isDraft ? draftId : ''"
            :current="
              isDraft
                ? activeArtifact.current
                : { task_id: taskId, stage: task?.stage || 'created' }
            "
            :focus="assistantFocus"
            :suggested-prompts="prompts"
            @applied="proposalApplied"
          />
          <div v-else class="assistant-empty">{{ route.path === "/materials" ? "请先在材料详情中选择“与助手讨论”。" : "正在恢复当前讨论范围…" }}</div>
        </div>
      </div>
    </section>
    <button
      class="assistant-orb"
      :aria-label="open ? '关闭报告协作助手' : '打开报告协作助手'"
      @click="open = !open"
    >
      <span class="orb-mark"><AppIcon name="activity" :size="15" /></span>
      <span class="orb-label">{{ isDraft ? "讨论需求" : isMaterialPage ? "材料助手" : "任务助手" }}</span>
    </button>
  </div>
</template>

<style scoped>
.workflow-assistant {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 80;
}
.assistant-orb {
  display: flex;
  align-items: center;
  gap: 9px;
  height: 46px;
  padding: 0 16px 0 6px;
  border: 1px solid var(--border-strong);
  border-radius: 999px;
  background: var(--surface-raised);
  color: var(--foreground);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.24);
  font-weight: 500;
  transition:
    box-shadow var(--motion-fast),
    background var(--motion-fast);
}
.assistant-orb:hover {
  border-color: color-mix(in srgb, var(--primary) 65%, var(--border-strong));
  background: var(--surface-hover);
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.3);
}
.orb-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border: 1px solid color-mix(in srgb, var(--primary) 45%, var(--border));
  border-radius: 50%;
  background: var(--primary-soft);
  color: var(--accent);
}
.orb-label {
  font-size: 13px;
}
.assistant-panel {
  position: absolute;
  right: 0;
  bottom: 60px;
  width: min(680px, calc(100vw - 40px));
  height: min(820px, calc(100vh - 32px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
  box-shadow: var(--shadow-float);
}
.assistant-panel > header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 17px 18px 15px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-raised);
  cursor: grab;
  user-select: none;
}
.assistant-panel > header small,
.assistant-panel > header b {
  display: block;
}
.assistant-panel > header small {
  color: var(--subtle-foreground);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
}
.assistant-panel > header b {
  margin-top: 3px;
  font-size: 15px;
}
.assistant-panel > header button {
  display: grid;
  place-items: center;
  width: 31px;
  height: 31px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--muted-foreground);
  cursor: pointer;
}
.assistant-panel > header button:hover {
  background: var(--muted);
  color: var(--foreground);
}
.assistant-panel > nav {
  flex: 0 0 auto;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 5px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border);
  background: var(--card);
}
.assistant-panel > nav button {
  padding: 8px 4px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted-foreground);
  font-size: 12px;
}
.assistant-panel > nav button:hover {
  background: var(--muted);
  color: var(--foreground);
}
.assistant-panel > nav button.active {
  background: var(--primary-soft);
  color: var(--foreground);
  font-weight: 600;
}
.assistant-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}
.assistant-body.discussion-active {
  display: flex;
  overflow: hidden;
}
.progress-view {
  padding: 22px;
}
.progress-view > small {
  color: var(--subtle-foreground);
}
.progress-view h3 {
  margin: 4px 0 9px;
  font-size: 18px;
}
.progress-view p {
  margin: 0;
  color: var(--muted-foreground);
  line-height: 1.7;
}
.stage-state {
  display: flex;
  align-items: center;
  gap: 12px;
}
.stage-state > span {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--primary);
  box-shadow: 0 0 0 5px var(--primary-soft);
}
.stage-state > span.review,
.stage-state > span.done {
  background: var(--success);
  box-shadow: 0 0 0 5px var(--success-soft);
}
.stage-state > span.failed {
  background: var(--destructive);
  box-shadow: 0 0 0 5px var(--destructive-soft);
}
.progress-view dl {
  margin: 20px 0;
  border-top: 1px solid var(--border);
}
.progress-view dl div {
  display: flex;
  justify-content: space-between;
  padding: 11px 0;
  border-bottom: 1px solid var(--border);
}
.progress-view dt {
  color: var(--muted-foreground);
}
.progress-view dd {
  margin: 0;
  font-weight: 600;
}
.ask-link {
  margin-top: 17px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--primary);
  font-weight: 600;
}
.artifact-view {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  min-height: 100%;
}
.artifact-types {
  display: flex;
  gap: 4px;
  padding: 10px;
  overflow-x: auto;
  border-bottom: 1px solid var(--border);
  background: var(--muted);
}
.artifact-types button {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 7px;
  padding: 7px 9px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--muted-foreground);
}
.artifact-types button.active {
  background: var(--card);
  color: var(--foreground);
  box-shadow: none;
}
.artifact-types button:disabled {
  opacity: 0.38;
}
.artifact-types span {
  font-size: 11px;
}
.artifact-items {
  min-height: 0;
  overflow: auto;
}
.artifact-items article {
  border-bottom: 1px solid var(--border);
}
.artifact-items article.active {
  background: var(--surface-hover);
  box-shadow: inset 3px 0 var(--primary);
}
.artifact-items article > button {
  display: grid;
  width: 100%;
  gap: 5px;
  padding: 14px 16px;
  border: 0;
  background: transparent;
  text-align: left;
}
.artifact-items article > button > span {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.artifact-items i {
  color: var(--primary);
  font-size: 11px;
  font-style: normal;
  font-weight: 600;
}
.artifact-items small {
  display: -webkit-box;
  overflow: hidden;
  color: var(--muted-foreground);
  font-size: 11px;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.assistant-artifact-detail {
  display: grid;
  gap: 10px;
  padding: 0 16px 16px;
}
.assistant-artifact-detail p {
  margin: 0;
  color: var(--muted-foreground);
  line-height: 1.65;
}
.assistant-artifact-detail .btn {
  justify-self: end;
}
.assistant-empty {
  padding: 28px;
  color: var(--muted-foreground);
}
.discussion-view {
  box-sizing: border-box;
  padding: 14px 18px 18px;
}
.loading {
  padding: 20px;
  color: var(--muted-foreground);
}
.discussion-view {
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.discussion-view :deep(.copilot) {
  flex: 1 1 auto;
  width: 100%;
  min-height: 0;
}
.panel-actions {
  display: flex;
  align-items: center;
  gap: 3px;
}
.panel-resize-handle {
  position: absolute;
  z-index: 4;
  padding: 0;
  border: 0;
  background: transparent;
  touch-action: none;
}
.panel-resize-handle--left,
.panel-resize-handle--right {
  top: 10px;
  bottom: 10px;
  width: 8px;
  cursor: ew-resize;
}
.panel-resize-handle--left {
  left: -4px;
}
.panel-resize-handle--right {
  right: -4px;
}
.panel-resize-handle--top,
.panel-resize-handle--bottom {
  right: 10px;
  left: 10px;
  height: 8px;
  cursor: ns-resize;
}
.panel-resize-handle--top {
  top: -4px;
}
.panel-resize-handle--bottom {
  bottom: -4px;
}
@media (max-width: 600px) {
  .workflow-assistant {
    right: 14px;
    bottom: 14px;
  }
  .assistant-panel {
    position: fixed;
    inset: 12px;
    width: auto;
    height: auto;
    border-radius: 7px;
  }
  .orb-label {
    display: none;
  }
  .assistant-orb {
    width: 48px;
    padding: 0;
    justify-content: center;
  }
}
@media (max-width: 600px) {
  .panel-resize-handle {
    display: none;
  }
}
.reference-list {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 9px;
  overflow-x: auto;
  padding-bottom: 2px;
}
.reference-list > span {
  display: flex;
  align-items: center;
  gap: 5px;
  flex: 0 0 auto;
  max-width: 260px;
  padding: 4px 7px;
  background: var(--color-surface-soft);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  color: var(--color-muted);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.reference-list i {
  color: var(--color-primary);
  font-style: normal;
}
.reference-list span button,
.clear-references {
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--color-faint);
}
.reference-list span button {
  font-size: 15px;
}
.clear-references {
  flex: 0 0 auto;
  font-size: 11px;
}
.discussion-references {
  flex: 0 0 auto;
  margin: 0;
  padding: 0 0 10px;
  border-bottom: 1px solid var(--border);
}
</style>
