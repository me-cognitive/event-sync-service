import { FieldStatus, Severity } from '../core/models';

export const FIELD_STATUS_META: Record<FieldStatus, { label: string; icon: string; hint: string }> = {
  agree: { label: 'Agree', icon: 'check_circle', hint: 'Both sources give the same value' },
  equivalent: { label: 'Equivalent', icon: 'drag_handle', hint: 'Worded differently, same meaning' },
  refinement: { label: 'Refined', icon: 'zoom_in', hint: 'One source is more specific' },
  complementary: { label: 'Complementary', icon: 'join_inner', hint: 'Different but not contradictory' },
  filled: { label: 'Filled', icon: 'format_color_fill', hint: 'Missing in one source, filled from the other' },
  conflict: { label: 'Conflict', icon: 'error', hint: 'Sources contradict each other' },
  single_source: { label: 'Single source', icon: 'looks_one', hint: 'Only one source has this value' },
};

export const SEVERITY_ICON: Record<Severity | string, string> = {
  info: 'info',
  warning: 'warning',
  error: 'error',
};

export const FIELD_LABELS: Record<string, string> = {
  title: 'Title',
  date: 'Date',
  start: 'Start',
  end: 'End',
  client_name: 'Client',
  client_company: 'Company',
  owner: 'Owner / organizer',
  attendees: 'Attendees',
  location: 'Location',
  meeting_type: 'Meeting type',
  status: 'Status',
  notes: 'Notes',
  is_recurring: 'Recurring',
};
