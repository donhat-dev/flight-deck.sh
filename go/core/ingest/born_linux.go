//go:build linux

package ingest

import "golang.org/x/sys/unix"

// bornNS is the file's birth time from statx, or fallback where the
// filesystem does not record one.
func bornNS(path string, fallback int64) int64 {
	var stx unix.Statx_t
	err := unix.Statx(unix.AT_FDCWD, path, unix.AT_STATX_SYNC_AS_STAT, unix.STATX_BTIME, &stx)
	if err != nil || stx.Mask&unix.STATX_BTIME == 0 {
		return fallback
	}
	return stx.Btime.Sec*1_000_000_000 + int64(stx.Btime.Nsec)
}
