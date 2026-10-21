package main

import (
	"errors"
	"fmt"
	"time"

	"golang.org/x/sys/unix"
)

const sigTERM = unix.SIGTERM

func pidfdOpen(pid int) (int, error) {
	fd, err := unix.PidfdOpen(pid, 0)
	if err != nil {
		return -1, fmt.Errorf("pidfd_open(%d): %w", pid, err)
	}
	return fd, nil
}

func pidfdSendSignal(fd int, sig unix.Signal) error {
	if err := unix.PidfdSendSignal(fd, sig, nil, 0); err != nil {
		if errors.Is(err, unix.ESRCH) {
			return fmt.Errorf("process already exited")
		}
		return err
	}
	return nil
}

func pidfdPollExit(fd int, deadline time.Time) (bool, error) {
	remain := time.Until(deadline)
	if remain < 0 {
		remain = 0
	}
	fds := []unix.PollFd{{Fd: int32(fd), Events: unix.POLLIN}}
	n, err := unix.Poll(fds, int(remain.Milliseconds()))
	if err != nil {
		return false, err
	}
	return n > 0 && fds[0].Revents&unix.POLLIN != 0, nil
}

func closeFD(fd int) {
	unix.Close(fd)
}
