import { Pipe, PipeTransform } from '@angular/core';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/;

/**
 * Formats backend timestamps as the wall-clock time they carry (firm-local,
 * America/New_York) instead of converting to the viewer's timezone. Converting
 * would shift every time for viewers elsewhere and hide what each source said.
 */
@Pipe({ name: 'firmTime' })
export class FirmTimePipe implements PipeTransform {
  transform(value: unknown, mode: 'datetime' | 'date' | 'time' = 'datetime'): string {
    if (typeof value !== 'string') return '';
    const m = ISO.exec(value);
    if (!m) return value;
    const [, y, mo, d, h, mi] = m;
    const date = `${+d} ${MONTHS[+mo - 1]} ${y}`;
    const time = h ? `${h}:${mi}` : '';
    if (mode === 'date') return date;
    if (mode === 'time') return time;
    return time ? `${date}, ${time}` : date;
  }
}
