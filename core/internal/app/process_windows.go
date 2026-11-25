//go:build windows

package app

import (
	"os/exec"
	"syscall"
	"workdesk/core/internal/processowner"
)

func quietCommand(cmd *exec.Cmd) {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	cmd.SysProcAttr.HideWindow = true
	cmd.SysProcAttr.CreationFlags |= 0x08000000
}

type ownedCommand = processowner.Command

func startOwnedCommand(cmd *exec.Cmd) (*ownedCommand, error) { return processowner.Start(cmd) }
