import { JsonPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { rxResource } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatTableModule } from '@angular/material/table';

import { ApiService } from '../../core/api.service';
import { Severity } from '../../core/models';
import { SEVERITY_ICON } from '../../shared/field-status';
import { SourceBadge } from '../../shared/source-badge';

@Component({
  selector: 'app-data-issues',
  imports: [
    JsonPipe, RouterLink, MatButtonModule, MatButtonToggleModule, MatIconModule, MatProgressBarModule,
    MatTableModule, SourceBadge,
  ],
  template: `
    <h1>Data-quality issues</h1>
    <p class="muted">
      Problems found while ingesting the source files. Every record is kept; values were repaired where it was
      safe to do so and each repair is listed here.
    </p>

    <mat-button-toggle-group [value]="severity()" (change)="severity.set($event.value)" aria-label="Severity">
      <mat-button-toggle value="">All ({{ issues.value()?.length ?? 0 }})</mat-button-toggle>
      @for (s of severities; track s) {
        <mat-button-toggle [value]="s">{{ s }} ({{ counts()[s] }})</mat-button-toggle>
      }
    </mat-button-toggle-group>

    @if (issues.isLoading()) {
      <mat-progress-bar mode="indeterminate" />
    }

    <table mat-table [dataSource]="filtered()" class="mat-elevation-z1">
      <ng-container matColumnDef="severity">
        <th mat-header-cell *matHeaderCellDef></th>
        <td mat-cell *matCellDef="let i"><mat-icon class="{{ i.severity }}">{{ severityIcon[i.severity] }}</mat-icon></td>
      </ng-container>
      <ng-container matColumnDef="record">
        <th mat-header-cell *matHeaderCellDef>Record</th>
        <td mat-cell *matCellDef="let i">
          <app-source-badge [sources]="[i.source]" /> <strong>{{ i.source_id }}</strong>
        </td>
      </ng-container>
      <ng-container matColumnDef="field">
        <th mat-header-cell *matHeaderCellDef>Field</th>
        <td mat-cell *matCellDef="let i"><code>{{ i.field }}</code></td>
      </ng-container>
      <ng-container matColumnDef="message">
        <th mat-header-cell *matHeaderCellDef>Issue</th>
        <td mat-cell *matCellDef="let i">{{ i.message }}</td>
      </ng-container>
      <ng-container matColumnDef="raw">
        <th mat-header-cell *matHeaderCellDef>Raw value</th>
        <td mat-cell *matCellDef="let i">
          @if (i.raw_value !== null) { <code>{{ i.raw_value | json }}</code> }
        </td>
      </ng-container>
      <ng-container matColumnDef="meeting">
        <th mat-header-cell *matHeaderCellDef></th>
        <td mat-cell *matCellDef="let i">
          @if (i.meeting_id) { <a mat-button [routerLink]="['/meetings', i.meeting_id]">View meeting</a> }
        </td>
      </ng-container>
      <tr mat-header-row *matHeaderRowDef="columns"></tr>
      <tr mat-row *matRowDef="let row; columns: columns"></tr>
    </table>
  `,
  styles: `
    h1 { font: var(--mat-sys-headline-small); margin: 8px 0; }
    .muted { color: var(--mat-sys-on-surface-variant); }
    mat-button-toggle-group { margin: 8px 0 16px; text-transform: capitalize; }
    table { width: 100%; }
    code { font-size: 12px; background: var(--mat-sys-surface-container); padding: 1px 6px; border-radius: 4px; }
    .error { color: var(--conflict-color); }
    .warning { color: var(--warning-color); }
    .info { color: var(--info-color); }
  `,
})
export class DataIssues {
  private readonly api = inject(ApiService);

  protected readonly issues = rxResource({ stream: () => this.api.issues() });
  protected readonly severity = signal<Severity | ''>('');
  protected readonly severities: Severity[] = ['error', 'warning', 'info'];
  protected readonly severityIcon = SEVERITY_ICON;
  protected readonly columns = ['severity', 'record', 'field', 'message', 'raw', 'meeting'];

  protected readonly counts = computed(() => {
    const counts: Record<Severity, number> = { error: 0, warning: 0, info: 0 };
    for (const i of this.issues.value() ?? []) counts[i.severity]++;
    return counts;
  });

  protected readonly filtered = computed(() => {
    const s = this.severity();
    return (this.issues.value() ?? []).filter((i) => !s || i.severity === s);
  });
}
