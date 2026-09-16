export type TaskStage = "created" | "parsing" | "material_analysis" | "requirement_review" | "planning" | "evidence" | "conflict" | "analysis" | "final_plan" | "directory_review" | "narrative" | "writing" | "qa" | "review" | "done" | "failed" | "paused" | string;

export interface TaskSummary {
  task_id: string;
  theme: string;
  stage: TaskStage;
  created_at?: string;
  updated_at?: string;
  report_id?: number;
  material_count?: number;
  variant_id?: number;
  progress?: Record<string, any>;
  queue_status?: Record<string, any>;
  run_revision?: number;
  run_mode?: string;
  [key: string]: any;
}

export interface MaterialSummary {
  id: number;
  filename: string;
  file_type: string;
  unit_count: number;
  is_duplicate?: boolean;
  duplicate_of?: number;
  parsed_at?: string;
  parse_status?: "ready" | "pending" | "error" | string;
  tasks?: Array<{ task_id: string; theme?: string } | string>;
  [key: string]: any;
}

export interface Sentence {
  id: number;
  content: string;
  display_content?: string;
  source_level: string;
  fact_ids?: number[];
  inference_ids?: number[];
  evidences?: any[];
  sources?: any[];
  facts?: any[];
  inferences?: any[];
  [key: string]: any;
}

export interface ReportSection { title: string; display_title?: string; show_title?: boolean; paragraphs: Array<{ sentences: Sentence[] }> }
export interface QualityIssue {
  issue_id: string;
  type: string;
  severity?: "high" | "medium" | "low";
  status?: "open" | "resolved" | "ignored" | "stale";
  target_type?: "sentence" | "paragraph" | "section" | "report";
  sentence_id?: number;
  sentence_ids?: number[];
  section?: string;
  paragraph?: number;
  quote?: string;
  note?: string;
  location_confidence?: string;
}
export interface ReportData {
  id: number;
  title: string;
  status: string;
  task_id?: string;
  sections: ReportSection[];
  qa_issues: QualityIssue[];
  versions: any[];
}
