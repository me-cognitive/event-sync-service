import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'meetings' },
  {
    path: 'meetings',
    title: 'Meetings · Event Sync',
    loadComponent: () => import('./features/meetings-list/meetings-list').then((m) => m.MeetingsList),
  },
  {
    path: 'meetings/:id',
    title: 'Meeting · Event Sync',
    loadComponent: () => import('./features/meeting-detail/meeting-detail').then((m) => m.MeetingDetail),
  },
  {
    path: 'issues',
    title: 'Data issues · Event Sync',
    loadComponent: () => import('./features/data-issues/data-issues').then((m) => m.DataIssues),
  },
  { path: '**', redirectTo: 'meetings' },
];
