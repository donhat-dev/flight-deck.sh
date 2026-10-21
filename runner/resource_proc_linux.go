package main

import (
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const (
	resourcePollInterval = 15 * time.Second
	resourceSnapshotTTL  = 45 * time.Second
	resourceActionTTL    = 2 * time.Minute
	resourceStopWait     = 5 * time.Second
	resourceMaxProcesses = 8192
)

type ResourceSocket struct {
	Inode    string `json:"inode"`
	Protocol string `json:"protocol"`
	State    string `json:"state"`
}

type ResourceProcess struct {
	Key             string            `json:"key"`
	PID             int               `json:"pid"`
	ParentPID       int               `json:"parent_pid"`
	UID             int               `json:"uid"`
	StartTicks      uint64            `json:"start_ticks,string"`
	Namespace       string            `json:"namespace"`
	Comm            string            `json:"comm"`
	State           string            `json:"state"`
	ExeIdentity     string            `json:"exe_identity"`
	HarnessEvidence string            `json:"harness_evidence,omitempty"`
	AgeSeconds      *float64          `json:"age_seconds"`
	RSSBytes        *int64            `json:"rss_bytes"`
	FDCount         *int              `json:"fd_count"`
	SocketCount     *int              `json:"socket_count"`
	Sockets         []ResourceSocket  `json:"sockets"`
	Warnings        []string          `json:"warnings"`
	FDLinks         map[string]string `json:"-"`
}

type ResourceProcSnapshot struct {
	BootID    string            `json:"boot_id"`
	Namespace string            `json:"namespace"`
	TakenAt   string            `json:"taken_at"`
	Processes []ResourceProcess `json:"processes"`
	Warnings  []string          `json:"warnings"`
}

type resourceStat struct {
	pid, parentPID int
	comm, state    string
	startTicks     uint64
}

func parseResourceStat(raw string) (resourceStat, error) {
	left, right := strings.Index(raw, " ("), strings.LastIndex(raw, ") ")
	if left < 1 || right <= left+1 {
		return resourceStat{}, errors.New("invalid process stat")
	}
	pid, err := strconv.Atoi(raw[:left])
	if err != nil || pid < 1 {
		return resourceStat{}, errors.New("invalid process pid")
	}
	fields := strings.Fields(raw[right+2:])
	if len(fields) < 20 || len(fields[0]) != 1 {
		return resourceStat{}, errors.New("incomplete process stat")
	}
	parent, err := strconv.Atoi(fields[1])
	if err != nil || parent < 0 {
		return resourceStat{}, errors.New("invalid parent pid")
	}
	start, err := strconv.ParseUint(fields[19], 10, 64)
	if err != nil {
		return resourceStat{}, errors.New("invalid process start")
	}
	return resourceStat{pid, parent, raw[left+2 : right], fields[0], start}, nil
}

func parseResourceStatus(raw string) (int, *int64, error) {
	uid := -1
	var rss *int64
	for _, line := range strings.Split(raw, "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 3 && fields[0] == "Uid:" {
			value, err := strconv.Atoi(fields[2])
			if err != nil || value < 0 {
				return 0, nil, errors.New("invalid effective uid")
			}
			uid = value
		}
		if len(fields) == 3 && fields[0] == "VmRSS:" && fields[2] == "kB" {
			value, err := strconv.ParseInt(fields[1], 10, 64)
			if err == nil && value >= 0 && value <= math.MaxInt64/1024 {
				bytes := value * 1024
				rss = &bytes
			}
		}
	}
	if uid < 0 {
		return 0, nil, errors.New("effective uid is unavailable")
	}
	return uid, rss, nil
}

func resourceKey(boot, namespace string, pid int, start uint64) string {
	return fmt.Sprintf("%s:%s:%d:%d", boot, namespace, pid, start)
}

func resourceInstallationEvidence(executable, comm, home string) string {
	executable = strings.TrimSuffix(executable, " (deleted)")
	if !filepath.IsAbs(executable) || home == "" {
		return ""
	}
	executable = filepath.Clean(executable)
	switch comm {
	case "claude":
		native := filepath.Join(home, ".local", "share", "claude", "versions") + string(os.PathSeparator)
		node := filepath.Join(home, ".nvm", "versions", "node") + string(os.PathSeparator)
		vscode := filepath.Join(home, ".vscode", "extensions", "anthropic.claude-code-")
		if (strings.HasPrefix(executable, native) && !strings.Contains(strings.TrimPrefix(executable, native), "/")) ||
			(strings.HasPrefix(executable, node) && strings.HasSuffix(executable,
				"/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe")) ||
			(strings.HasPrefix(executable, vscode) && strings.HasSuffix(executable, "/resources/native-binary/claude")) ||
			executable == "/usr/local/bin/claude" || executable == filepath.Join(home, ".local", "bin", "claude") {
			return "known_installation"
		}
	case "devin", "devin-cli":
		prefix := filepath.Join(home, ".local", "share", "devin", "cli", "_versions") + string(os.PathSeparator)
		if strings.HasPrefix(executable, prefix) && strings.HasSuffix(executable, "/bin/devin") {
			return "known_installation"
		}
	}
	return ""
}

func resourceSocketTable(raw, protocol string) map[string]ResourceSocket {
	tcpStates := map[string]string{
		"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV",
		"04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
		"07": "CLOSED", "08": "CLOSE_WAIT", "09": "LAST_ACK",
		"0A": "LISTEN", "0B": "CLOSING", "0C": "NEW_SYN_RECV",
	}
	out := map[string]ResourceSocket{}
	for _, line := range strings.Split(raw, "\n")[1:] {
		fields := strings.Fields(line)
		inode, state := "", "UNKNOWN"
		if protocol == "unix" && len(fields) >= 7 {
			inode = fields[6]
			state = map[string]string{"01": "UNCONNECTED", "02": "CONNECTING",
				"03": "CONNECTED", "04": "DISCONNECTING"}[fields[5]]
			if fields[3] == "00010000" {
				state = "LISTEN"
			}
		} else if protocol != "unix" && len(fields) >= 10 {
			inode = fields[9]
			if protocol == "udp" || protocol == "udp6" {
				state = map[string]string{"01": "CONNECTED", "07": "UNCONNECTED"}[fields[3]]
			} else {
				state = tcpStates[fields[3]]
			}
		}
		number, err := strconv.ParseUint(inode, 10, 64)
		if err != nil || number == 0 {
			continue
		}
		if state == "" {
			state = "UNKNOWN"
		}
		out[inode] = ResourceSocket{inode, protocol, state}
	}
	return out
}

func readResourceFDs(dir string, row *ResourceProcess) {
	entries, err := os.ReadDir(filepath.Join(dir, "fd"))
	if err != nil {
		row.Warnings = append(row.Warnings, "File descriptors are unavailable")
		return
	}
	count := len(entries)
	row.FDCount = &count
	row.FDLinks = map[string]string{}
	inodes := map[string]bool{}
	complete := true
	for _, entry := range entries {
		link, err := os.Readlink(filepath.Join(dir, "fd", entry.Name()))
		if err != nil {
			complete = false
			continue
		}
		for _, kind := range []string{"socket", "pipe"} {
			prefix := kind + ":["
			if strings.HasPrefix(link, prefix) && strings.HasSuffix(link, "]") {
				inode := strings.TrimSuffix(strings.TrimPrefix(link, prefix), "]")
				if _, err := strconv.ParseUint(inode, 10, 64); err == nil {
					row.FDLinks[entry.Name()] = link
					if kind == "socket" {
						inodes[inode] = true
					}
				}
			}
		}
	}
	if complete {
		count := len(inodes)
		row.SocketCount = &count
	} else {
		row.Warnings = append(row.Warnings, "File descriptor links changed or are unreadable")
	}
	if len(inodes) == 0 {
		return
	}
	tables := map[string]ResourceSocket{}
	for _, protocol := range []string{"tcp", "tcp6", "udp", "udp6", "unix"} {
		raw, err := os.ReadFile(filepath.Join(dir, "net", protocol))
		if err != nil {
			continue
		}
		for inode, socket := range resourceSocketTable(string(raw), protocol) {
			tables[inode] = socket
		}
	}
	for inode := range inodes {
		socket, ok := tables[inode]
		if !ok {
			socket = ResourceSocket{inode, "unknown", "UNKNOWN"}
		}
		row.Sockets = append(row.Sockets, socket)
	}
	sort.Slice(row.Sockets, func(i, j int) bool { return row.Sockets[i].Inode < row.Sockets[j].Inode })
}

func readResourceProcess(root string, pid int, boot, namespace string, hz, uptime float64, withFDs bool) (ResourceProcess, error) {
	dir := filepath.Join(root, strconv.Itoa(pid))
	raw, err := os.ReadFile(filepath.Join(dir, "stat"))
	if err != nil {
		return ResourceProcess{}, err
	}
	stat, err := parseResourceStat(string(raw))
	if err != nil || stat.pid != pid {
		return ResourceProcess{}, errors.New("process identity is unavailable")
	}
	raw, err = os.ReadFile(filepath.Join(dir, "status"))
	if err != nil {
		return ResourceProcess{}, err
	}
	uid, rss, err := parseResourceStatus(string(raw))
	if err != nil {
		return ResourceProcess{}, err
	}
	pidns, err := os.Readlink(filepath.Join(dir, "ns", "pid"))
	if err != nil || pidns != namespace {
		return ResourceProcess{}, errors.New("process is outside the runner pid namespace")
	}
	row := ResourceProcess{
		Key: resourceKey(boot, namespace, pid, stat.startTicks),
		PID: pid, ParentPID: stat.parentPID, UID: uid, StartTicks: stat.startTicks,
		Namespace: namespace, Comm: stat.comm, State: stat.state, RSSBytes: rss,
		Sockets: []ResourceSocket{}, Warnings: []string{},
	}
	if hz > 0 && !math.IsNaN(hz) && !math.IsInf(hz, 0) && uptime >= 0 {
		age := uptime - float64(stat.startTicks)/hz
		if age >= 0 && !math.IsNaN(age) && !math.IsInf(age, 0) {
			row.AgeSeconds = &age
		}
	}
	info, err := os.Stat(filepath.Join(dir, "exe"))
	if err == nil {
		if file, ok := info.Sys().(*syscall.Stat_t); ok {
			row.ExeIdentity = fmt.Sprintf("%d:%d", file.Dev, file.Ino)
		}
	}
	if row.ExeIdentity == "" {
		row.Warnings = append(row.Warnings, "Executable identity is unavailable; observation only")
	}
	if executable, err := os.Readlink(filepath.Join(dir, "exe")); err == nil {
		home, _ := os.UserHomeDir()
		row.HarnessEvidence = resourceInstallationEvidence(executable, row.Comm, home)
	}
	if withFDs {
		readResourceFDs(dir, &row)
	}
	raw, err = os.ReadFile(filepath.Join(dir, "stat"))
	if err != nil {
		return ResourceProcess{}, err
	}
	last, err := parseResourceStat(string(raw))
	if err != nil || last.pid != stat.pid || last.startTicks != stat.startTicks {
		return ResourceProcess{}, errors.New("process changed during observation")
	}
	row.ParentPID, row.Comm, row.State = last.parentPID, last.comm, last.state
	return row, nil
}

func collectResourceProcesses(root string, uid int, hz float64) (ResourceProcSnapshot, error) {
	raw, err := os.ReadFile(filepath.Join(root, "sys", "kernel", "random", "boot_id"))
	if err != nil || strings.TrimSpace(string(raw)) == "" {
		return ResourceProcSnapshot{}, errors.New("boot identity is unavailable")
	}
	boot := strings.TrimSpace(string(raw))
	namespace, err := os.Readlink(filepath.Join(root, "self", "ns", "pid"))
	if err != nil || namespace == "" {
		return ResourceProcSnapshot{}, errors.New("runner pid namespace is unavailable")
	}
	uptime := -1.0
	raw, err = os.ReadFile(filepath.Join(root, "uptime"))
	if err == nil {
		fields := strings.Fields(string(raw))
		if len(fields) > 0 {
			if value, err := strconv.ParseFloat(fields[0], 64); err == nil {
				uptime = value
			}
		}
	}
	entries, err := os.ReadDir(root)
	if err != nil {
		return ResourceProcSnapshot{}, err
	}
	snapshot := ResourceProcSnapshot{
		BootID: boot, Namespace: namespace, TakenAt: time.Now().UTC().Format(time.RFC3339Nano),
		Processes: []ResourceProcess{}, Warnings: []string{},
	}
	pids := []int{}
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err == nil && pid > 0 && entry.IsDir() {
			pids = append(pids, pid)
		}
	}
	if len(pids) > resourceMaxProcesses {
		return ResourceProcSnapshot{}, errors.New("process inventory exceeds the observation limit")
	}
	for _, pid := range pids {
		row, err := readResourceProcess(root, pid, boot, namespace, hz, uptime, false)
		if err != nil || row.UID != uid {
			continue
		}
		snapshot.Processes = append(snapshot.Processes, row)
	}
	sort.Slice(snapshot.Processes, func(i, j int) bool { return snapshot.Processes[i].PID < snapshot.Processes[j].PID })
	return snapshot, nil
}
