const loaded = new Map<string, Promise<void>>();

const groups: Record<string, string[]> = {
  'work-items': ['cooperation-toolbar', 'results-page', 'work-items-page'],
  files: ['files-page'], records: ['records-page'], results: ['results-page'], monitor: ['monitor-page'],
  projects: ['projects-page'], planning: ['planning-page'], libraries: ['libraries-page'],
  management: ['management'], extensions: ['extensions'], control: ['control'],
  collab: ['collaboration'], operations: ['operations'], settings: ['settings'],
};

export function loadScript(name: string): Promise<void> {
  if (!loaded.has(name)) {
    loaded.set(name, new Promise<void>((resolve, reject) => {
      const script = document.createElement('script');
      script.src = name + '.js';
      script.onload = () => resolve();
      script.onerror = () => { loaded.delete(name); script.remove(); reject(new Error('界面模块读取失败：' + name)); };
      document.head.append(script);
    }));
  }
  return loaded.get(name)!;
}

export async function loadLegacyModule(route: string) {
  for (const name of groups[route] || []) await loadScript(name);
}
