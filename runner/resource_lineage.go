package main

import "strings"

type ResourceSessionInfo struct {
	SessionID     string
	State         string
	StartTicks    uint64
	ExeIdentity   string
	ResourceAgent bool
}

type ResourceOrigin struct {
	OwnerKey  string `json:"owner_key"`
	OwnerPID  int    `json:"owner_pid"`
	Harness   string `json:"harness"`
	UID       int    `json:"uid"`
	Namespace string `json:"namespace"`
	Protected bool   `json:"protected"`
	IsRoot    bool   `json:"is_root"`
	Verified  bool   `json:"verified"`
}

type ResourceRow struct {
	ResourceProcess
	Harness          string `json:"harness"`
	Role             string `json:"role"`
	OwnerKey         string `json:"owner_key"`
	SessionID        string `json:"session_id"`
	RuntimeState     string `json:"runtime_state"`
	Managed          bool   `json:"managed"`
	Orphan           bool   `json:"orphan"`
	Protected        bool   `json:"protected"`
	Manageable       bool   `json:"manageable"`
	ProtectionReason string `json:"protection_reason"`
	StdioPeerKey     string `json:"stdio_peer_key,omitempty"`
}

func resourceHarness(comm string) string {
	switch comm {
	case "claude":
		return "claude"
	case "devin", "devin-cli":
		return "devin"
	}
	return ""
}

func resourceManaged(row ResourceProcess, session ResourceSessionInfo) bool {
	return session.SessionID != "" && session.StartTicks == row.StartTicks &&
		session.ExeIdentity != "" && session.ExeIdentity == row.ExeIdentity
}

func resourceContainerProcess(comm string) bool {
	return comm == "docker" || comm == "podman" || comm == "dockerd" ||
		strings.HasPrefix(comm, "containerd") || strings.HasPrefix(comm, "docker-proxy")
}

func classifyResourceProcesses(processes []ResourceProcess, previous map[string]ResourceOrigin,
	managed map[int]ResourceSessionInfo, protected map[int]string) ([]ResourceRow, map[string]ResourceOrigin) {
	byPID := map[int]ResourceProcess{}
	byKey := map[string]ResourceProcess{}
	roots := map[int]ResourceOrigin{}
	for _, row := range processes {
		byPID[row.PID], byKey[row.Key] = row, row
		harness := resourceHarness(row.Comm)
		own := resourceManaged(row, managed[row.PID])
		if own {
			harness = "claude"
		}
		if harness != "" {
			roots[row.PID] = ResourceOrigin{
				OwnerKey: row.Key, OwnerPID: row.PID, Harness: harness,
				UID: row.UID, Namespace: row.Namespace, IsRoot: true,
				Protected: protected[row.PID] != "" || (own && managed[row.PID].ResourceAgent),
				Verified:  own || row.HarnessEvidence == "known_installation",
			}
		}
	}
	out := []ResourceRow{}
	retained := map[string]ResourceOrigin{}
	for _, process := range processes {
		origin, known := roots[process.PID]
		if !known {
			current := process
			seen := map[int]bool{current.PID: true}
			for current.ParentPID > 1 && !seen[current.ParentPID] {
				parent, exists := byPID[current.ParentPID]
				if !exists || parent.StartTicks > current.StartTicks ||
					parent.UID != process.UID || parent.Namespace != process.Namespace {
					break
				}
				seen[parent.PID] = true
				if root, exists := roots[parent.PID]; exists {
					origin, known = root, true
					origin.IsRoot = false
					break
				}
				current = parent
			}
		}
		if !known {
			origin, known = previous[process.Key]
			known = known && origin.UID == process.UID && origin.Namespace == process.Namespace
		}
		if prior, exists := previous[process.Key]; known && exists &&
			prior.UID == process.UID && prior.Namespace == process.Namespace && prior.Protected {
			origin.Protected = true
		}
		if known {
			current := process
			seen := map[int]bool{current.PID: true}
			for current.ParentPID > 1 && !seen[current.ParentPID] {
				parent, exists := byPID[current.ParentPID]
				if !exists || parent.StartTicks > current.StartTicks ||
					parent.UID != process.UID || parent.Namespace != process.Namespace {
					break
				}
				seen[parent.PID] = true
				parentOrigin := roots[parent.PID]
				priorOrigin := previous[parent.Key]
				if parentOrigin.Protected || priorOrigin.Protected || resourceContainerProcess(parent.Comm) {
					origin.Protected = true
					break
				}
				current = parent
			}
		}
		row := ResourceRow{ResourceProcess: process}
		if known {
			row.Harness, row.OwnerKey = origin.Harness, origin.OwnerKey
			row.Role = "child"
			if origin.IsRoot {
				row.Role = "harness"
			}
			owner, exists := byKey[origin.OwnerKey]
			row.Orphan = !exists
			if exists && resourceManaged(owner, managed[owner.PID]) {
				session := managed[owner.PID]
				row.SessionID, row.RuntimeState = session.SessionID, session.State
				row.Managed = origin.IsRoot
				if session.ResourceAgent {
					origin.Protected = true
					if origin.IsRoot {
						row.Role = "management"
					}
				}
			}
			row.Protected = origin.Protected
			if row.Protected {
				row.ProtectionReason = "Runner or management-agent lineage"
			}
			retained[process.Key] = origin
		} else if protected[process.PID] == "runner" {
			row.Role = "runner"
		} else if strings.HasPrefix(process.Comm, "mcp") || strings.HasPrefix(process.Comm, "chrome-devtools") {
			row.Role = "unattributed"
		} else {
			continue
		}
		if reason := protected[process.PID]; reason != "" {
			row.Protected, row.ProtectionReason = true, reason
		}
		if process.PID <= 1 || resourceContainerProcess(process.Comm) {
			row.Protected, row.ProtectionReason = true, "System or container runtime"
		}
		row.Manageable = known && origin.Verified && !row.Protected && process.ExeIdentity != "" &&
			process.State != "Z" && (row.Role == "child" || row.Managed)
		if known && !origin.Verified && !row.Protected {
			row.ProtectionReason = "Harness name only; installation is unverified"
		}
		out = append(out, row)
	}
	return out, retained
}

func annotateResourceStdio(rows []ResourceRow) {
	byPID := map[int]ResourceRow{}
	for _, row := range rows {
		byPID[row.PID] = row
	}
	for index := range rows {
		child := &rows[index]
		child.StdioPeerKey = ""
		parent, exists := byPID[child.ParentPID]
		if !exists || parent.StartTicks > child.StartTicks || parent.UID != child.UID ||
			parent.Namespace != child.Namespace {
			continue
		}
		input, output := child.FDLinks["0"], child.FDLinks["1"]
		if input == output || !strings.HasPrefix(input, "pipe:[") || !strings.HasPrefix(output, "pipe:[") {
			continue
		}
		seenInput, seenOutput := false, false
		for _, link := range parent.FDLinks {
			seenInput = seenInput || link == input
			seenOutput = seenOutput || link == output
		}
		if seenInput && seenOutput {
			child.StdioPeerKey = parent.Key
		}
	}
}
