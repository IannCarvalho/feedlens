import { provideHttpClient, withFetch } from '@angular/common/http';
import { ApplicationConfig, Provider, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { routes } from './app.routes';
import { APP_CONFIG, AppConfig } from './core/app-config';
import { FeedbackGateway } from './core/feedback.gateway';
import { HttpFeedbackGateway } from './core/http-feedback.gateway';
import { MockFeedbackGateway } from './core/mock-feedback.gateway';

/** Escolhe o gateway pela configuração: dados de demonstração ou API real. */
export function gatewayProviders(config: AppConfig): Provider[] {
  return config.useMock
    ? [MockFeedbackGateway, { provide: FeedbackGateway, useExisting: MockFeedbackGateway }]
    : [{ provide: FeedbackGateway, useClass: HttpFeedbackGateway }];
}

export function buildAppConfig(config: AppConfig): ApplicationConfig {
  return {
    providers: [
      provideBrowserGlobalErrorListeners(),
      provideRouter(routes, withComponentInputBinding()),
      provideHttpClient(withFetch()),
      { provide: APP_CONFIG, useValue: config },
      ...gatewayProviders(config),
    ],
  };
}
