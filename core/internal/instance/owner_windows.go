//go:build windows

package instance

import (
	"os"
	"os/exec"
	"syscall"
	"unsafe"
)

var kernel32 = syscall.NewLazyDLL("kernel32.dll")
var lockFileEx = kernel32.NewProc("LockFileEx")
var unlockFileEx = kernel32.NewProc("UnlockFileEx")

func lockFile(file *os.File) (func() error, error) {
	overlapped := new(syscall.Overlapped)
	result, _, err := lockFileEx.Call(file.Fd(), 3, 0, 1, 0, uintptr(unsafe.Pointer(overlapped)))
	if result == 0 {
		return nil, err
	}
	return func() error {
		result, _, err := unlockFileEx.Call(file.Fd(), 0, 1, 0, uintptr(unsafe.Pointer(overlapped)))
		if result == 0 {
			return err
		}
		return nil
	}, nil
}

func hideWindow(cmd *exec.Cmd) { cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true} }
