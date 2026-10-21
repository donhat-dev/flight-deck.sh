package main

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func resourceTestStat(pid, parent int, start uint64, comm string) string {
	fields := make([]string, 20)
	for i := range fields {
		fields[i] = "0"
	}
	fields[0], fields[1], fields[19] = "S", strconv.Itoa(parent), strconv.FormatUint(start, 10)
	return fmt.Sprintf("%d (%s) %s", pid, comm, strings.Join(fields, " "))
}

func resourceTestWrite(t *testing.T, root, name, value string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(value), 0o600); err != nil {
		t.Fatal(err)
	}
}

func resourceTestLink(t *testing.T, root, name, target string) {
	t.Helper()
	path := filepath.Join(root, name)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, path); err != nil {
		t.Fatal(err)
	}
}

func TestResourceStatParsesParenthesesAndStartIdentity(t *testing.T) {
	stat, err := parseResourceStat(resourceTestStat(101, 40, 12345, "mcp (bridge) worker)"))
	if err != nil || stat.pid != 101 || stat.parentPID != 40 || stat.startTicks != 12345 ||
		stat.comm != "mcp (bridge) worker)" || stat.state != "S" {
		t.Fatalf("stat=%+v error=%v", stat, err)
	}
	for _, raw := range []string{"", "101 (worker) S 40", "-1 (worker) S 40",
		strings.Replace(resourceTestStat(101, 40, 12345, "worker"), "12345", "-1", 1)} {
		if _, err := parseResourceStat(raw); err == nil {
			t.Fatalf("accepted invalid stat %q", raw)
		}
	}
}

func TestResourceStatusUsesEffectiveUIDAndKiB(t *testing.T) {
	uid, rss, err := parseResourceStatus("Uid:\t1001\t1000\t1001\t1001\nVmRSS:\t4096 kB\n")
	if err != nil || uid != 1000 || rss == nil || *rss != 4194304 {
		t.Fatalf("uid=%d rss=%v error=%v", uid, rss, err)
	}
	_, rss, err = parseResourceStatus("Uid:\t1000\t1000\t1000\t1000\n")
	if err != nil || rss != nil {
		t.Fatalf("missing RSS must be unknown: rss=%v error=%v", rss, err)
	}
	if _, _, err := parseResourceStatus("VmRSS: 4096 kB"); err == nil {
		t.Fatal("accepted a missing owner")
	}
	_, rss, err = parseResourceStatus("Uid: 1000 1000 1000 1000\nVmRSS: 9223372036854775807 kB")
	if err != nil || rss != nil {
		t.Fatal("overflowing RSS must remain unknown")
	}
}

func TestResourceSocketTablesDoNotExposeEndpointsOrPaths(t *testing.T) {
	tcp := resourceSocketTable("header\n0: 0100007F:1F90 00000000:0000 08 0:0 0:0 0 1000 0 123 1\n", "tcp")
	if tcp["123"] != (ResourceSocket{"123", "tcp", "CLOSE_WAIT"}) {
		t.Fatalf("TCP socket=%+v", tcp)
	}
	unix := resourceSocketTable("header\n000: 00000002 00000000 00000000 0001 03 124 /private/socket\n", "unix")
	if unix["124"] != (ResourceSocket{"124", "unix", "CONNECTED"}) {
		t.Fatalf("UNIX socket=%+v", unix)
	}
	udp := resourceSocketTable("header\n0: 0100007F:1F90 00000000:0000 07 0:0 0:0 0 1000 0 125 1\n", "udp")
	if udp["125"].State != "UNCONNECTED" {
		t.Fatalf("UDP socket=%+v", udp)
	}
}

func TestResourceProcessMeasuresDescriptorsAndUniqueSockets(t *testing.T) {
	root := t.TempDir()
	resourceTestWrite(t, root, "101/stat", resourceTestStat(101, 40, 12345, "claude"))
	resourceTestWrite(t, root, "101/status", "Uid: 1000 1000 1000 1000\nVmRSS: 4096 kB\n")
	resourceTestWrite(t, root, "binary", "fixture")
	resourceTestLink(t, root, "101/exe", filepath.Join(root, "binary"))
	resourceTestLink(t, root, "101/ns/pid", "pid:[200]")
	resourceTestLink(t, root, "101/fd/0", "pipe:[555]")
	resourceTestLink(t, root, "101/fd/1", "socket:[123]")
	resourceTestLink(t, root, "101/fd/2", "socket:[123]")
	resourceTestWrite(t, root, "101/net/tcp", "header\n0: A B 08 0:0 0:0 0 1000 0 123 1\n")
	row, err := readResourceProcess(root, 101, "boot", "pid:[200]", 100, 200, true)
	if err != nil || row.Key != "boot:pid:[200]:101:12345" || row.ExeIdentity == "" ||
		row.FDCount == nil || *row.FDCount != 3 || row.SocketCount == nil || *row.SocketCount != 1 ||
		row.AgeSeconds == nil || math.Abs(*row.AgeSeconds-76.55) > 0.000001 ||
		row.RSSBytes == nil || *row.RSSBytes != 4194304 || len(row.Sockets) != 1 ||
		row.Sockets[0].State != "CLOSE_WAIT" {
		t.Fatalf("row=%+v error=%v", row, err)
	}
	row, err = readResourceProcess(root, 101, "boot", "pid:[201]", 100, 200, false)
	if err == nil {
		t.Fatal("accepted another pid namespace")
	}
	row, err = readResourceProcess(root, 101, "boot", "pid:[200]", 0, 200, false)
	if err != nil || row.AgeSeconds != nil || row.FDCount != nil || row.SocketCount != nil {
		t.Fatalf("unmeasured values must remain unknown: row=%+v error=%v", row, err)
	}
}

func TestResourceKeyChangesAcrossPIDReuseAndNamespaces(t *testing.T) {
	key := resourceKey("boot", "pid:[200]", 101, 12345)
	for _, other := range []string{
		resourceKey("boot", "pid:[200]", 101, 12346),
		resourceKey("new-boot", "pid:[200]", 101, 12345),
		resourceKey("boot", "pid:[201]", 101, 12345),
	} {
		if key == other {
			t.Fatal("identity key aliases another process")
		}
	}
}

func TestResourceInstallationEvidenceDoesNotTrustProcessNames(t *testing.T) {
	home := "/home/operator"
	for _, executable := range []string{
		home + "/.local/share/claude/versions/2.1.292",
		home + "/.nvm/versions/node/v22/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
		home + "/.vscode/extensions/anthropic.claude-code-2.1.292-linux-x64/resources/native-binary/claude",
		"/usr/local/bin/claude",
	} {
		if resourceInstallationEvidence(executable, "claude", home) != "known_installation" {
			t.Fatalf("known installation not recognized: %s", executable)
		}
	}
	for _, executable := range []string{"/tmp/claude", "/usr/bin/node", "/home/other/.local/bin/claude",
		home + "/.local/share/claude/versions/../../../../tmp/claude", "claude"} {
		if resourceInstallationEvidence(executable, "claude", home) != "" {
			t.Fatalf("unverified executable accepted: %s", executable)
		}
	}
	if resourceInstallationEvidence(home+"/.local/share/devin/cli/_versions/3000/bin/devin", "devin", home) != "known_installation" {
		t.Fatal("known Devin installation not recognized")
	}
}
