export type WorkflowStageMeta = { label: string; description: string };

export const stageLabels: Record<string, string> = {
  created: "待运行",
  parsing: "材料准备",
  material_analysis: "材料理解",
  requirement_review: "需求讨论",
  planning: "分析规划",
  evidence: "事实与证据",
  conflict: "冲突核验",
  analysis: "综合分析",
  final_plan: "目录生成",
  directory_review: "目录讨论",
  narrative: "成文组织",
  writing: "报告生成",
  qa: "质量检查",
  review: "待审核",
  done: "已完成",
  failed: "异常",
  paused: "已暂停",
  queued: "排队中",
  running: "运行中",
  draft: "草稿",
  final: "已定稿",
};

export const stageMeta: Record<string, WorkflowStageMeta> = {
  created: { label: "等待开始", description: "任务目标和材料已登记，尚未进入处理。" },
  queued: { label: "排队中", description: "任务已进入队列，等待可用执行资源。" },
  running: { label: "运行中", description: "任务正在执行当前工作流阶段。" },
  parsing: { label: "材料解析", description: "把文件转换为带来源位置的内容单元。" },
  material_analysis: { label: "材料理解", description: "判断材料角色、可证明范围和信息缺口。" },
  requirement_review: { label: "需求讨论", description: "材料理解已完成，等待共同明确任务主题和报告要求。" },
  planning: { label: "分析规划", description: "确定需要回答的问题和证据提取范围。" },
  evidence: { label: "事实与证据", description: "提取事实并绑定原始材料位置。" },
  conflict: { label: "冲突核验", description: "检查多来源对同一事项是否存在矛盾。" },
  analysis: { label: "综合分析", description: "基于事实形成带依据和置信度的分析判断。" },
  final_plan: { label: "目录生成", description: "根据事实和推论形成最终报告结构。" },
  directory_review: { label: "目录讨论", description: "审阅章节结构、顺序和重点安排。" },
  narrative: { label: "成文组织", description: "组织章节主线、话题和逻辑顺序。" },
  writing: { label: "报告生成", description: "先组织叙事计划，再按章节生成并绑定来源。" },
  qa: { label: "质量检查", description: "检查事实、结构、语言和格式问题。" },
  review: { label: "等待审核", description: "当前产物已形成，可以审阅、讨论和修改。" },
  done: { label: "已完成", description: "报告已审核，可导出或进行增量更新。" },
  paused: { label: "已暂停", description: "任务停在安全边界，可继续运行。" },
  failed: { label: "运行异常", description: "当前阶段未完成，请查看错误并决定是否重试。" },
};

export const stageGroups = {
  comparison: [
    { name: "新增材料准备", keys: ["created", "parsing", "material_analysis", "planning"] },
    { name: "证据与变化分析", keys: ["evidence", "conflict", "analysis"] },
    { name: "变化审阅", keys: ["review", "done"] },
  ],
  standard: [
    { name: "材料准备", keys: ["created", "parsing"] },
    { name: "分析规划", keys: ["material_analysis", "requirement_review", "planning", "evidence", "conflict", "analysis"] },
    { name: "报告生成", keys: ["final_plan", "directory_review", "narrative", "writing", "qa"] },
    { name: "审核完成", keys: ["review", "done"] },
  ],
};
