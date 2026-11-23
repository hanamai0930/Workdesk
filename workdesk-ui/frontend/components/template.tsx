import parse, { attributesToProps, domToReact, Element, type DOMNode, type HTMLReactParserOptions } from 'html-react-parser';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { Textarea } from './ui/textarea';
import { NativeSelect } from './ui/native-select';
import { Card } from './ui/card';
import { SafeMarkdown } from './safe-markdown';

// Compatibility boundary for the existing audited control-page presenters.
// Only application templates reach this component; model text is escaped upstream.
// Form names, native controls and delegated commands remain the service contract.
const options: HTMLReactParserOptions = {
  replace(node) {
    if (!(node instanceof Element)) return;
    if (node.name === 'workdesk-markdown' && typeof node.attribs['data-source'] === 'string') {
      return <SafeMarkdown text={node.attribs['data-source']}/>;
    }
    const props: any = attributesToProps(node.attribs);
    const children = () => domToReact(node.children as DOMNode[], options);
    if (node.name === 'button') {
      const primary = /(^|\s)primary(\s|$)/.test(props.className || '');
      const danger = /danger/.test(props.className || '');
      const ghost = /wk-(icon|menu-item|chip|record|top-menu)/.test(props.className || '');
      return <Button variant={primary ? 'default' : danger ? 'destructive' : ghost ? 'ghost' : 'outline'} {...props}>{children()}</Button>;
    }
    if (node.name === 'input') {
      if ('value' in props) { props.defaultValue = props.value; delete props.value; }
      if ('checked' in props) { props.defaultChecked = props.checked; delete props.checked; }
      if (['checkbox', 'radio', 'file', 'hidden'].includes(props.type)) return <input {...props}/>;
      return <Input {...props}/>;
    }
    if (node.name === 'textarea') {
      return <Textarea {...props} defaultValue={node.children.map(c => c.type === 'text' ? c.data : '').join('')}/>;
    }
    if (node.name === 'select') {
      const values: string[] = [];
      const find = (nodes: DOMNode[]) => nodes.forEach(c => {
        if (c instanceof Element) {
          if (c.name === 'option' && 'selected' in c.attribs) values.push(c.attribs.value ?? c.children.map(x => x.type === 'text' ? x.data : '').join(''));
          find(c.children as DOMNode[]);
        }
      });
      find(node.children as DOMNode[]);
      delete props.value;
      return <NativeSelect {...props} defaultValue={props.multiple ? values : values[0]}>{children()}</NativeSelect>;
    }
    if (node.name === 'option') { delete props.selected; return <option {...props}>{children()}</option>; }
    if (node.name === 'article' && /wb-feature-card/.test(props.className || '')) return <Card {...props}>{children()}</Card>;
  },
};

export function Template({ html }: { html: string }) { return <>{parse(html, options)}</>; }
