import { Component, computed, inject, signal } from '@angular/core';
import { rxResource } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSnackBar } from '@angular/material/snack-bar';
import { MatTableModule } from '@angular/material/table';
import { MatTooltipModule } from '@angular/material/tooltip';

import { ApiService } from '../../core/api.service';
import { MeetingFilters, MeetingListItem } from '../../core/models';
import { FIELD_LABELS, SEVERITY_ICON } from '../../shared/field-status';
import { FirmTimePipe } from '../../shared/firm-time.pipe';
import { SourceBadge } from '../../shared/source-badge';

@Component({
  selector: 'app-meetings-list',
  imports: [
    FormsModule, MatButtonModule, MatButtonToggleModule, MatCardModule, MatFormFieldModule, MatIconModule,
    MatInputModule, MatProgressBarModule, MatSlideToggleModule, MatTableModule, MatTooltipModule,
    FirmTimePipe, SourceBadge,
  ],
  templateUrl: './meetings-list.html',
  styleUrl: './meetings-list.scss',
})
export class MeetingsList {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly snack = inject(MatSnackBar);

  protected readonly source = signal<MeetingFilters['source']>('');
  protected readonly conflictsOnly = signal(false);
  protected readonly query = signal('');
  protected readonly syncing = signal(false);

  private readonly filters = computed<MeetingFilters>(() => ({
    source: this.source(),
    has_conflicts: this.conflictsOnly(),
    q: this.query(),
  }));

  protected readonly summary = rxResource({ stream: () => this.api.summary() });
  protected readonly meetings = rxResource({
    params: () => this.filters(),
    stream: ({ params }) => this.api.meetings(params),
  });

  protected readonly columns = ['when', 'title', 'owner', 'location', 'status', 'sources', 'conflicts'];

  protected open(row: MeetingListItem): void {
    this.router.navigate(['/meetings', row.id]);
  }

  protected conflictIcon(row: MeetingListItem): string {
    return row.max_conflict_severity ? SEVERITY_ICON[row.max_conflict_severity] : '';
  }

  protected clientLine(row: MeetingListItem): string {
    return [row.client_name, row.client_company].filter((v) => !!v).join(' · ');
  }

  protected conflictTooltip(row: MeetingListItem): string {
    return row.conflict_fields.map((f) => FIELD_LABELS[f] ?? f).join(', ');
  }

  protected resync(): void {
    this.syncing.set(true);
    this.api.sync().subscribe({
      next: (s) => {
        this.snack.open(
          `Synced ${s.meetings} meetings from ${s.records.crm ?? 0} CRM + ${s.records.calendar ?? 0} calendar records`,
          'OK',
          { duration: 4000 },
        );
        this.summary.reload();
        this.meetings.reload();
        this.syncing.set(false);
      },
      error: (e) => {
        this.snack.open(`Sync failed: ${e.error?.detail ?? e.message}`, 'Dismiss');
        this.syncing.set(false);
      },
    });
  }
}
