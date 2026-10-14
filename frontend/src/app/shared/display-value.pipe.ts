import { Pipe, PipeTransform } from '@angular/core';

import { FirmTimePipe } from './firm-time.pipe';

const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Renders any field value (string, timestamp, list, boolean, null) as readable text. */
@Pipe({ name: 'displayValue' })
export class DisplayValuePipe implements PipeTransform {
  private readonly time = new FirmTimePipe();

  transform(value: unknown): string {
    if (value === null || value === undefined || value === '') return '—';
    if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'string' && TIMESTAMP.test(value)) return this.time.transform(value);
    if (typeof value === 'string' && DATE_ONLY.test(value)) return this.time.transform(value, 'date');
    return String(value);
  }
}
