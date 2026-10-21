//go:build unix

package rtk

import (
	"os/exec"
	"syscall"
)

// ownGroup starts rtk in its own process group and makes the timeout kill
// that group, so descendants die with it.
func ownGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }
}
