export {};
import type { CoreClient } from './core/client';
declare global {
  interface Window {
    WorkdeskReact: any;
    WorkdeskModules: any;
    WorkdeskWorkbench: any;
    WorkdeskLegacy: any;
    WorkdeskCore: CoreClient;
    WorkdeskLegacyModules: (route: string) => Promise<void>;
  }
}
