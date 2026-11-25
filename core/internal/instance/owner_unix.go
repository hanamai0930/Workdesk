//go:build !windows

package instance

import (
	"os"
	"os/exec"
	"syscall"
)

func lockFile(file *os.File) (func() error, error) {
	if err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return nil, err
	}
	return func() error { return syscall.Flock(int(file.Fd()), syscall.LOCK_UN) }, nil
}

func hideWindow(cmd *exec.Cmd) {}
