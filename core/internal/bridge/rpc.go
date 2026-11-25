// Package bridge provides the private, bidirectional channel to trusted legacy code.
package bridge

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
	"workdesk/core/internal/processowner"
)

type Handler func(string, json.RawMessage) (any, error)
type RPCError struct {
	Message string `json:"message"`
	Type    string `json:"type"`
	Code    int    `json:"sqlite_errorcode,omitempty"`
}

func (e *RPCError) Error() string { return e.Message }

type message struct {
	ID     string          `json:"id"`
	Method string          `json:"method,omitempty"`
	Params json.RawMessage `json:"params,omitempty"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *RPCError       `json:"error,omitempty"`
}
type Peer struct {
	cmd      *exec.Cmd
	owner    *processowner.Command
	input    io.WriteCloser
	write    sync.Mutex
	mu       sync.Mutex
	pending  map[string]chan message
	sequence atomic.Uint64
	done     chan struct{}
	once     sync.Once
	handler  Handler
}

func Start(python, base string, env []string, log io.Writer, handler Handler) (*Peer, error) {
	cmd := exec.Command(python, "-B", "-X", "utf8", "-m", "researchos.workdesk.bridge")
	cmd.Dir = base
	cmd.Env = env
	cmd.Stderr = log
	hideWindow(cmd)
	input, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	output, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	p := &Peer{cmd: cmd, input: input, pending: map[string]chan message{}, done: make(chan struct{}), handler: handler}
	p.owner, err = processowner.Start(cmd)
	if err != nil {
		if p.owner != nil {
			_ = p.owner.Close()
		}
		return nil, err
	}
	go p.read(output)
	go func() {
		err := p.owner.Wait()
		if err != nil {
			fmt.Fprintln(log, "legacy bridge exited:", err)
		}
		p.finish()
		_ = p.owner.Close()
	}()
	return p, nil
}
func (p *Peer) PID() int              { return p.cmd.Process.Pid }
func (p *Peer) Done() <-chan struct{} { return p.done }
func (p *Peer) finish() {
	p.once.Do(func() {
		close(p.done)
		p.mu.Lock()
		defer p.mu.Unlock()
		for id, ch := range p.pending {
			ch <- message{Error: &RPCError{Message: "兼容连接已断开；原操作需要核对。", Type: "ConnectionError"}}
			delete(p.pending, id)
		}
	})
}
func (p *Peer) send(m message) error {
	p.write.Lock()
	defer p.write.Unlock()
	return json.NewEncoder(p.input).Encode(m)
}
func (p *Peer) read(r io.Reader) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 65536), 64*1024*1024)
	for scanner.Scan() {
		var m message
		if json.Unmarshal(scanner.Bytes(), &m) != nil {
			p.finish()
			return
		}
		if m.Method != "" {
			go func(m message) {
				value, err := p.handler(m.Method, m.Params)
				reply := message{ID: m.ID}
				if err != nil {
					var rpc *RPCError
					if errors.As(err, &rpc) {
						reply.Error = rpc
					} else {
						reply.Error = &RPCError{Message: err.Error(), Type: "RuntimeError"}
					}
				} else {
					reply.Result, _ = json.Marshal(value)
				}
				_ = p.send(reply)
			}(m)
		} else {
			p.mu.Lock()
			ch := p.pending[m.ID]
			delete(p.pending, m.ID)
			p.mu.Unlock()
			if ch != nil {
				ch <- m
			}
		}
	}
	p.finish()
}
func (p *Peer) Call(ctx context.Context, method string, params any, out any) error {
	id := fmt.Sprintf("go-%d", p.sequence.Add(1))
	raw, err := json.Marshal(params)
	if err != nil {
		return err
	}
	ch := make(chan message, 1)
	p.mu.Lock()
	p.pending[id] = ch
	p.mu.Unlock()
	if err = p.send(message{ID: id, Method: method, Params: raw}); err != nil {
		p.mu.Lock()
		delete(p.pending, id)
		p.mu.Unlock()
		return err
	}
	select {
	case reply := <-ch:
		if reply.Error != nil {
			return reply.Error
		}
		if out != nil {
			return json.Unmarshal(reply.Result, out)
		}
		return nil
	case <-ctx.Done():
		p.mu.Lock()
		delete(p.pending, id)
		p.mu.Unlock()
		return ctx.Err()
	case <-p.done:
		return errors.New("兼容连接已退出；未自动重发")
	}
}
func (p *Peer) Close() {
	_ = p.input.Close()
	select {
	case <-p.done:
	default:
		if p.owner != nil {
			_ = p.owner.Close()
		}
	}
}

// Environment replaces only this process's variables, leaving global configuration alone.
func Environment(values map[string]string) []string {
	result := []string{}
	for _, item := range os.Environ() {
		keep := true
		for key := range values {
			if len(item) > len(key) && item[:len(key)+1] == key+"=" {
				keep = false
				break
			}
		}
		if keep {
			result = append(result, item)
		}
	}
	for key, value := range values {
		result = append(result, key+"="+value)
	}
	return result
}
