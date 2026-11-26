//go:build !windows

package processowner

import (
	"errors"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

type Command struct {
	cmd       *exec.Cmd
	mu        sync.Mutex
	pid       int
	closed    bool
	confirmed atomic.Bool
	waitOnce  sync.Once
	waitErr   error
}

func Start(cmd *exec.Cmd) (*Command, error) {
	o := &Command{cmd: cmd}
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	} else {
		copy := *cmd.SysProcAttr
		cmd.SysProcAttr = &copy
	}
	cmd.SysProcAttr.Setpgid = true
	if cmd.WaitDelay == 0 {
		cmd.WaitDelay = 5 * time.Second
	}
	if cmd.Cancel != nil {
		cmd.Cancel = o.Terminate
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	o.pid = cmd.Process.Pid
	return o, nil
}
func (o *Command) Terminate() error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed || o.confirmed.Load() || o.pid <= 0 {
		return os.ErrProcessDone
	}
	err := syscall.Kill(-o.pid, syscall.SIGKILL)
	if err == syscall.ESRCH {
		return os.ErrProcessDone
	}
	return err
}
func (o *Command) Wait() error {
	o.waitOnce.Do(func() {
		o.waitErr = o.cmd.Wait()
	})
	deadline := time.Now().Add(5 * time.Second)
	for {
		if o.confirmed.Load() {
			return o.waitErr
		}
		err := syscall.Kill(-o.pid, 0)
		// The parent is reaped once; its process group is checked again after
		// scoped cleanup, without caching an earlier remaining-child timeout.
		if err == syscall.ESRCH && o.cmd.ProcessState != nil {
			o.confirmed.Store(true)
			return o.waitErr
		}
		if time.Now().After(deadline) {
			return errors.Join(o.waitErr, errors.New("owned process group exit is unconfirmed"))
		}
		time.Sleep(10 * time.Millisecond)
	}
}
func (o *Command) ExitConfirmed() bool { return o.confirmed.Load() }
func (o *Command) Identity() map[string]any {
	return map[string]any{"ownership": "PROCESS_GROUP", "pid": o.pid, "exit_confirmed": o.confirmed.Load()}
}
func (o *Command) Close() error {
	if !o.ExitConfirmed() {
		_ = o.Terminate()
		_ = o.Wait()
	}
	o.mu.Lock()
	o.closed = true
	o.mu.Unlock()
	if !o.ExitConfirmed() {
		return errors.New("owned process group exit is unconfirmed")
	}
	return nil
}
