// Mirrors the backend response models (backend/app/models).

export type Source = 'crm' | 'calendar';
export type Severity = 'info' | 'warning' | 'error';
export type FieldStatus =
  | 'agree'
  | 'equivalent'
  | 'refinement'
  | 'complementary'
  | 'filled'
  | 'conflict'
  | 'single_source';

export interface FieldValue {
  value: unknown;
  source: Source | null;
  crm: unknown;
  calendar: unknown;
  status: FieldStatus;
  rule: string;
}

export interface Conflict {
  field: string;
  severity: Severity;
  message: string;
  crm: unknown;
  calendar: unknown;
}

export interface MatchInfo {
  confidence: number;
  reasons: string[];
  breakdown: Record<string, number>;
  scored_pair: Partial<Record<Source, string>>;
}

export interface DuplicateInfo {
  source: Source;
  canonical_id: string;
  superseded_ids: string[];
  reason: string;
}

export interface DataIssue {
  source: Source;
  source_id: string;
  field: string;
  code: string;
  severity: Severity;
  message: string;
  raw_value: unknown;
}

export interface IssueItem extends DataIssue {
  meeting_id: string | null;
}

export interface MeetingListItem {
  id: string;
  title: string | null;
  date: string | null;
  start: string | null;
  end: string | null;
  client_name: string | null;
  client_company: string | null;
  owner: string | null;
  location: string | null;
  status: string | null;
  sources: Source[];
  source_ids: Partial<Record<Source, string[]>>;
  match_confidence: number | null;
  conflict_count: number;
  max_conflict_severity: Severity | null;
  conflict_fields: string[];
  issue_count: number;
  has_duplicates: boolean;
}

export interface MeetingDetail {
  id: string;
  title: string | null;
  date: string | null;
  start: string | null;
  status: string | null;
  sources: Source[];
  source_ids: Partial<Record<Source, string[]>>;
  match: MatchInfo | null;
  fields: Record<string, FieldValue>;
  conflicts: Conflict[];
  duplicates: DuplicateInfo[];
  issues: DataIssue[];
  raw_records: Record<string, Record<string, unknown>>;
}

export interface SyncSummary {
  synced_at: string;
  data_source: string;
  records: Partial<Record<Source, number>>;
  meetings: number;
  matched: number;
  crm_only: number;
  calendar_only: number;
  with_conflicts: number;
  conflicts_by_field: Record<string, number>;
  duplicate_groups: number;
  issues_by_severity: Record<Severity, number>;
}

export interface MeetingFilters {
  source?: 'crm' | 'calendar' | 'both' | '';
  has_conflicts?: boolean;
  q?: string;
}
