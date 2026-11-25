//go:build !windows

package app

import (
	"os/exec"
	"workdesk/core/internal/processowner"
)

func quietCommand(cmd *exec.Cmd) {}

type ownedCommand = processowner.Command

func startOwnedCommand(cmd *exec.Cmd) (*ownedCommand, error) { return processowner.Start(cmd) }
