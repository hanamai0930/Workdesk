import type { ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { flushSync } from 'react-dom';
import { Template } from '../components/template';

const roots = new WeakMap<HTMLElement, Root>();
let revision = 0;
export function release(host:HTMLElement) {
  const root=roots.get(host);if(root){flushSync(()=>root.unmount());roots.delete(host);}
}
export function renderInto(host: HTMLElement, node: ReactNode) {
  let root = roots.get(host);
  if (!root) { root = createRoot(host); roots.set(host, root); }
  flushSync(() => root.render(node));
}
export function renderTemplate(host: HTMLElement, html: string) {
  // A full presenter render intentionally resets its form, as innerHTML used to.
  renderInto(host, <div key={++revision} style={{display:'contents'}} data-react-template><Template html={html}/></div>);
}
export function ownHost(host: HTMLElement) {
  const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML')!;
  Object.defineProperty(host, 'innerHTML', {
    configurable: true,
    get() { return descriptor.get!.call(this); },
    set(html: string) { renderTemplate(this, String(html)); },
  });
  const replaceChildren=host.replaceChildren.bind(host);
  host.replaceChildren=(...nodes:(Node|string)[])=>{
    // Controllers clear shared menus through the DOM API. Unmount through React
    // first so the next open never reconciles against already removed children.
    if(roots.has(host))renderInto(host,null);
    if(nodes.length)replaceChildren(...nodes);
  };
  host.dataset.renderer = 'react';
}
