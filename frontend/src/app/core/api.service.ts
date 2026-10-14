import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { IssueItem, MeetingDetail, MeetingFilters, MeetingListItem, SyncSummary } from './models';

/** Thin typed wrapper over the backend REST API (served same-origin under /api). */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  summary(): Observable<SyncSummary> {
    return this.http.get<SyncSummary>('/api/summary');
  }

  meetings(filters: MeetingFilters = {}): Observable<MeetingListItem[]> {
    let params = new HttpParams();
    if (filters.source) params = params.set('source', filters.source);
    if (filters.has_conflicts) params = params.set('has_conflicts', true);
    if (filters.q?.trim()) params = params.set('q', filters.q.trim());
    return this.http.get<MeetingListItem[]>('/api/meetings', { params });
  }

  meeting(id: string): Observable<MeetingDetail> {
    return this.http.get<MeetingDetail>(`/api/meetings/${id}`);
  }

  issues(): Observable<IssueItem[]> {
    return this.http.get<IssueItem[]>('/api/issues');
  }

  sync(): Observable<SyncSummary> {
    return this.http.post<SyncSummary>('/api/sync', {});
  }
}
