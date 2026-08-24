export type TaskStage = "created" | "parsing" | "dedup" | "material_analysis" | "planning" | "evidence" | "conflict" | "analysis" | "writing" | "knowledge" | "review" | "done" | "failed" | "paused" | string;

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

export interface ReportSection { title: string; display_title?: string; paragraphs: Array<{ sentences: Sentence[] }> }
export interface ReportData {
  id: number;
  title: string;
  status: string;
  task_id?: string;
  sections: ReportSection[];
  qa_issues: any[];
  versions: any[];
}
