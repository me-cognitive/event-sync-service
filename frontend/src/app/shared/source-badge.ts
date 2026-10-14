import { Component, input } from '@angular/core';
import { MatTooltipModule } from '@angular/material/tooltip';

import { Source } from '../core/models';

/** Coloured CRM / Calendar badges; optional IDs show the underlying record IDs on hover. */
@Component({
  selector: 'app-source-badge',
  imports: [MatTooltipModule],
  template: `
    @for (s of sources(); track s) {
      <span class="badge" [class]="s" [matTooltip]="ids()?.[s]?.join(', ') ?? ''">
        {{ s === 'crm' ? 'CRM' : 'Calendar' }}
      </span>
    }
  `,
  styles: `
    :host { display: inline-flex; gap: 4px; flex-wrap: wrap; }
    .badge {
      font-size: 11px; font-weight: 500; padding: 2px 8px; border-radius: 10px; white-space: nowrap;
      color: #fff;
    }
    .crm { background: var(--crm-color); }
    .calendar { background: var(--cal-color); }
  `,
})
export class SourceBadge {
  readonly sources = input.required<Source[]>();
  readonly ids = input<Partial<Record<Source, string[]>>>();
}
