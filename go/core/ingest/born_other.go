//go:build !linux

package ingest

// bornNS has no portable birth time to read here; modification order stands in.
func bornNS(path string, fallback int64) int64 { return fallback }
