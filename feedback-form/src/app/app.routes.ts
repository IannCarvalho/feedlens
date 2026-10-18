import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';
import { FeedbackSession } from './core/feedback-session';

/** A página de agradecimento só faz sentido depois de um envio. */
const sentGuard: CanActivateFn = () =>
  inject(FeedbackSession).sent() ? true : inject(Router).createUrlTree(['/']);

export const routes: Routes = [
  {
    path: '',
    title: 'Envie seu feedback',
    loadComponent: () => import('./features/send/send.page').then((m) => m.SendPage),
  },
  {
    path: 'obrigado',
    title: 'Feedback recebido',
    canActivate: [sentGuard],
    loadComponent: () => import('./features/thanks/thanks.page').then((m) => m.ThanksPage),
  },
  { path: '**', redirectTo: '' },
];
