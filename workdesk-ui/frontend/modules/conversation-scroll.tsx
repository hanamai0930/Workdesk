import { useLayoutEffect, useRef, useState } from 'react';
import { ThreadPrimitive, useAuiState } from '@assistant-ui/react';
import { ArrowDown } from 'lucide-react';
import { Button } from '../components/ui/button';
import './conversation-scroll.css';

/** The viewport's native controller owns following; this only counts new replies. */
export function ConversationScroll({ conversationKey }: { conversationKey: string }) {
  const messages = useAuiState(state => state.thread.messages);
  const replyIds = messages.filter(message => message.role === 'assistant' &&
    message.content.some(part => part.type === 'text' && part.text.trim())).map(message => message.id);
  const replyKey = JSON.stringify(replyIds);
  const marker = useRef<HTMLDivElement>(null);
  const atEnd = useRef(true);
  const seen = useRef({ conversationKey, ids: new Set(replyIds) });
  const [unread, setUnread] = useState(0);

  useLayoutEffect(() => {
    atEnd.current = true;
    setUnread(0);
    const viewport = marker.current?.closest<HTMLElement>('[data-reading-viewport]');
    if (!viewport) return;
    const readPosition = () => {
      atEnd.current = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 2;
      if (atEnd.current) setUnread(value => value ? 0 : value);
    };
    viewport.addEventListener('scroll', readPosition, { passive: true });
    const frame = requestAnimationFrame(readPosition);
    return () => { cancelAnimationFrame(frame); viewport.removeEventListener('scroll', readPosition); };
  }, [conversationKey]);

  useLayoutEffect(() => {
    const ids = new Set(replyIds);
    if (seen.current.conversationKey !== conversationKey) {
      seen.current = { conversationKey, ids };
      setUnread(0);
      return;
    }
    const added = replyIds.filter(id => !seen.current.ids.has(id)).length;
    seen.current = { conversationKey, ids };
    if (atEnd.current) setUnread(value => value ? 0 : value);
    else if (added) setUnread(value => value + added);
  }, [conversationKey, replyKey]);

  const label = unread ? `有 ${unread} 条新回复，回到最新` : '回到最新';
  return <div ref={marker} className="wd-reading-position" data-reading-progress data-new-replies={unread}>
    <span className="wd-reading-announcement" role="status" aria-live="polite">{unread ? `有 ${unread} 条新回复` : ''}</span>
    <ThreadPrimitive.ScrollToBottom asChild behavior="instant" onClick={() => { atEnd.current = true; setUnread(0); }}>
      <Button type="button" variant="outline" className="wd-reading-latest" aria-label={label}><ArrowDown/>{label}</Button>
    </ThreadPrimitive.ScrollToBottom>
  </div>;
}
