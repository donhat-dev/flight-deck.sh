//go:build !unix

package rtk

import "os/exec"

// ownGroup is a no-op where process groups are not available; WaitDelay still
// bounds the wait.
func ownGroup(*exec.Cmd) {}
