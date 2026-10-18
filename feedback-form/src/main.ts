import { bootstrapApplication } from '@angular/platform-browser';
import { App } from './app/app';
import { buildAppConfig } from './app/app.config';
import { loadConfig } from './app/core/app-config';

// A configuração é lida antes de subir o app para o gateway (demo ou API real)
// já nascer escolhido, sem estado intermediário na tela.
const config = await loadConfig();

bootstrapApplication(App, buildAppConfig(config)).catch((error: unknown) => console.error(error));
