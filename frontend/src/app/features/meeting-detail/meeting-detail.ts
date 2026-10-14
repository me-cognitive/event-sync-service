import { JsonPipe, PercentPipe } from '@angular/common';
import { Component, computed, inject, input } from '@angular/core';
import { rxResource } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatExpansionModule } from '@angular/material/expansion';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';

import { ApiService } from '../../core/api.service';
import { FieldValue } from '../../core/models';
import { DisplayValuePipe } from '../../shared/display-value.pipe';
import { FIELD_LABELS, FIELD_STATUS_META, SEVERITY_ICON } from '../../shared/field-status';
import { FirmTimePipe } from '../../shared/firm-time.pipe';
import { SourceBadge } from '../../shared/source-badge';

interface FieldRow extends FieldValue {
  key: string;
  label: string;
}

@Component({
  selector: 'app-meeting-detail',
  imports: [
    JsonPipe, PercentPipe, RouterLink, MatButtonModule, MatCardModule, MatExpansionModule, MatIconModule,
    MatProgressBarModule, MatTableModule, MatTooltipModule, DisplayValuePipe, FirmTimePipe, SourceBadge,
  ],
  templateUrl: './meeting-detail.html',
  styleUrl: './meeting-detail.scss',
})
export class MeetingDetail {
  private readonly api = inject(ApiService);

  /** Bound from the `:id` route parameter. */
  readonly id = input.required<string>();

  protected readonly meeting = rxResource({
    params: () => this.id(),
    stream: ({ params }) => this.api.meeting(params),
  });

  protected readonly rows = computed<FieldRow[]>(() => {
    const m = this.meeting.value();
    if (!m) return [];
    return Object.entries(m.fields).map(([key, f]) => ({ ...f, key, label: FIELD_LABELS[key] ?? key }));
  });

  protected readonly isMatched = computed(() => (this.meeting.value()?.sources.length ?? 0) === 2);
  protected readonly columns = computed(() =>
    this.isMatched() ? ['field', 'value', 'crm', 'calendar', 'status'] : ['field', 'value', 'status'],
  );

  protected readonly breakdown = computed(() =>
    Object.entries(this.meeting.value()?.match?.breakdown ?? {}).map(([signal, score]) => ({ signal, score })),
  );

  /** True when the match was scored on a superseded duplicate rather than the record supplying the values. */
  protected readonly scoredOnSuperseded = computed(() => {
    const m = this.meeting.value();
    const scored = Object.values(m?.match?.scored_pair ?? {});
    return (m?.duplicates ?? []).some((d) => d.superseded_ids.some((id) => scored.includes(id)));
  });

  protected readonly rawRecords = computed(() => Object.entries(this.meeting.value()?.raw_records ?? {}));

  protected readonly statusMeta = FIELD_STATUS_META;
  protected readonly severityIcon = SEVERITY_ICON;

  protected meta(row: FieldRow) {
    return this.statusMeta[row.status];
  }

  protected statusTooltip(row: FieldRow): string {
    return `${this.meta(row).hint}. Rule: ${row.rule}`;
  }
}
