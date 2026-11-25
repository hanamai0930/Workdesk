//go:build windows

// Package processowner controls only a process tree created by this application.
package processowner

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

type accounting struct {
	TotalUserTime, TotalKernelTime, ThisPeriodTotalUserTime, ThisPeriodTotalKernelTime int64
	TotalPageFaultCount, TotalProcesses, ActiveProcesses, TotalTerminatedProcesses     uint32
}

type Command struct {
	cmd              *exec.Cmd
	mu               sync.Mutex
	job, process     windows.Handle
	creation         string
	assigned, closed bool
	cancelRequested  atomic.Bool
	confirmed        atomic.Bool
	waitOnce         sync.Once
	waitErr          error
}

// Start admits a suspended process to a non-inheritable, kill-on-close Job before
// running its initial thread. This closes the launcher/child assignment race.
// See https://learn.microsoft.com/windows/win32/procthread/job-objects.
func Start(cmd *exec.Cmd) (*Command, error) {
	job, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return nil, fmt.Errorf("create owned job: %w", err)
	}
	limits := windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION{}
	limits.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if _, err = windows.SetInformationJobObject(job, windows.JobObjectExtendedLimitInformation, uintptr(unsafe.Pointer(&limits)), uint32(unsafe.Sizeof(limits))); err != nil {
		windows.CloseHandle(job)
		return nil, fmt.Errorf("configure owned job: %w", err)
	}
	o := &Command{cmd: cmd, job: job}
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	} else {
		copy := *cmd.SysProcAttr
		cmd.SysProcAttr = &copy
	}
	cmd.SysProcAttr.HideWindow = true
	cmd.SysProcAttr.CreationFlags |= windows.CREATE_SUSPENDED | windows.CREATE_NO_WINDOW
	if cmd.WaitDelay == 0 {
		cmd.WaitDelay = 5 * time.Second
	}
	if cmd.Cancel != nil {
		cmd.Cancel = o.Terminate
	}
	o.mu.Lock()
	if err = cmd.Start(); err != nil {
		o.closed = true
		o.mu.Unlock()
		windows.CloseHandle(job)
		return nil, err
	}
	// The exec.Cmd keeps its just-created process handle until Wait. Open an
	// additional identity/assignment handle while that process remains suspended.
	o.process, err = windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE|windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.SYNCHRONIZE, false, uint32(cmd.Process.Pid))
	if err == nil {
		var creation, exit, kernel, user windows.Filetime
		err = windows.GetProcessTimes(o.process, &creation, &exit, &kernel, &user)
		if err == nil {
			o.creation = strconv.FormatUint(uint64(creation.HighDateTime)<<32|uint64(creation.LowDateTime), 10)
		}
	}
	if err == nil {
		err = windows.AssignProcessToJobObject(job, o.process)
		o.assigned = err == nil
	}
	if err == nil && !o.cancelRequested.Load() {
		err = resumeInitialThread(uint32(cmd.Process.Pid))
	}
	if err == nil && o.cancelRequested.Load() {
		err = windows.TerminateJobObject(job, 137)
	}
	o.mu.Unlock()
	if err != nil {
		// The process has never been permitted to execute outside its Job. If
		// assignment failed, the exact exec-created handle is still available.
		_ = o.Terminate()
		_ = o.Wait()
		_ = o.Close()
		return o, fmt.Errorf("admit owned process (exit confirmed=%v): %w", o.ExitConfirmed(), err)
	}
	return o, nil
}

func resumeInitialThread(pid uint32) error {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPTHREAD, 0)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(snapshot)
	entry := windows.ThreadEntry32{Size: uint32(unsafe.Sizeof(windows.ThreadEntry32{}))}
	var thread windows.Handle
	defer func() {
		if thread != 0 {
			windows.CloseHandle(thread)
		}
	}()
	for err = windows.Thread32First(snapshot, &entry); err == nil; err = windows.Thread32Next(snapshot, &entry) {
		if entry.OwnerProcessID != pid {
			continue
		}
		if thread != 0 {
			return errors.New("new suspended process has more than one thread; initial thread is ambiguous")
		}
		thread, err = windows.OpenThread(windows.THREAD_SUSPEND_RESUME, false, entry.ThreadID)
		if err != nil {
			return err
		}
	}
	if err != windows.ERROR_NO_MORE_FILES {
		return err
	}
	if thread == 0 {
		return errors.New("new suspended process has no initial thread")
	}
	previous, err := windows.ResumeThread(thread)
	if err != nil {
		return err
	}
	if previous != 1 {
		return fmt.Errorf("unexpected initial thread suspend count %d", previous)
	}
	return nil
}

func (o *Command) activeLocked() (uint32, error) {
	if o.closed {
		return 0, os.ErrClosed
	}
	var value accounting
	err := windows.QueryInformationJobObject(o.job, windows.JobObjectBasicAccountingInformation, uintptr(unsafe.Pointer(&value)), uint32(unsafe.Sizeof(value)), nil)
	return value.ActiveProcesses, err
}

// Terminate addresses the owned Job handle, not a process name or discovered PID.
// CommandContext calls it instead of killing only a venv launcher process.
func (o *Command) Terminate() error {
	o.cancelRequested.Store(true)
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed || o.confirmed.Load() {
		return os.ErrProcessDone
	}
	if !o.assigned {
		if o.cmd.Process == nil {
			return os.ErrProcessDone
		}
		return o.cmd.Process.Kill()
	}
	active, err := o.activeLocked()
	if err != nil {
		return err
	}
	if active == 0 {
		return os.ErrProcessDone
	}
	return windows.TerminateJobObject(o.job, 137)
}

// Wait is safe to join from the normal waiter and the cleanup path. A launcher
// exit alone is insufficient: the Job's active process count must also be zero.
func (o *Command) Wait() error {
	o.waitOnce.Do(func() {
		o.waitErr = o.cmd.Wait()
		deadline := time.Now().Add(5 * time.Second)
		for {
			o.mu.Lock()
			active, err := o.activeLocked()
			o.mu.Unlock()
			if err != nil {
				o.waitErr = errors.Join(o.waitErr, fmt.Errorf("owned job exit could not be verified: %w", err))
				return
			}
			if active == 0 && o.cmd.ProcessState != nil && o.cmd.ProcessState.Exited() {
				o.confirmed.Store(true)
				return
			}
			if time.Now().After(deadline) {
				o.waitErr = errors.Join(o.waitErr, errors.New("owned job still has local processes; stop outcome is unknown"))
				return
			}
			time.Sleep(10 * time.Millisecond)
		}
	})
	return o.waitErr
}

func (o *Command) ExitConfirmed() bool { return o.confirmed.Load() }

func (o *Command) Identity() map[string]any {
	o.mu.Lock()
	defer o.mu.Unlock()
	value := map[string]any{"ownership": "WINDOWS_JOB_OBJECT", "pid": o.cmd.Process.Pid, "creation": o.creation, "kill_on_close": true, "exit_confirmed": o.confirmed.Load()}
	if !o.closed {
		if active, err := o.activeLocked(); err == nil {
			value["active_processes"] = active
		}
	}
	return value
}

// Close cannot turn an unverified stop into a successful one. Its kill-on-close
// handle also prevents a parent crash from leaving this owned local tree alive.
func (o *Command) Close() error {
	if !o.ExitConfirmed() {
		_ = o.Terminate()
		_ = o.Wait()
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed {
		return nil
	}
	o.closed = true
	var err error
	if o.process != 0 {
		err = windows.CloseHandle(o.process)
		o.process = 0
	}
	if o.job != 0 {
		err = errors.Join(err, windows.CloseHandle(o.job))
		o.job = 0
	}
	if !o.confirmed.Load() {
		err = errors.Join(err, errors.New("owned process exit was not confirmed"))
	}
	return err
}
