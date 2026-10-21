package main

import "testing"

func resourceLineageFixture(pid, parent int, start uint64, comm string) ResourceProcess {
	return ResourceProcess{
		Key: resourceKey("boot", "pid:[200]", pid, start),
		PID: pid, ParentPID: parent, UID: 1000, StartTicks: start,
		Namespace: "pid:[200]", Comm: comm, State: "S", ExeIdentity: "1:1",
		HarnessEvidence: "known_installation",
	}
}

func TestResourceLineageRetainsOnlyLivingDescendantsAfterOwnerLoss(t *testing.T) {
	root := resourceLineageFixture(101, 40, 10, "claude")
	child := resourceLineageFixture(102, 101, 20, "node")
	rows, prior := classifyResourceProcesses([]ResourceProcess{root, child}, nil, nil, nil)
	if len(rows) != 2 || rows[0].Manageable || !rows[1].Manageable || rows[1].Orphan ||
		rows[1].OwnerKey != root.Key {
		t.Fatalf("initial rows=%+v", rows)
	}
	child.ParentPID = 1
	rows, prior = classifyResourceProcesses([]ResourceProcess{child}, prior, nil, nil)
	if len(rows) != 1 || !rows[0].Orphan || !rows[0].Manageable || rows[0].OwnerKey != root.Key || len(prior) != 1 {
		t.Fatalf("detached rows=%+v retained=%+v", rows, prior)
	}
	rows, prior = classifyResourceProcesses(nil, prior, nil, nil)
	if len(rows) != 0 || len(prior) != 0 {
		t.Fatal("ended process observations must not accumulate")
	}
}

func TestResourceLineageRefusesReuseAndUnattributedMCP(t *testing.T) {
	root := resourceLineageFixture(101, 40, 30, "claude")
	older := resourceLineageFixture(102, 101, 20, "node")
	rows, _ := classifyResourceProcesses([]ResourceProcess{root, older}, nil, nil, nil)
	if len(rows) != 1 {
		t.Fatal("a newer parent identity cannot establish older-child ownership")
	}
	mcp := resourceLineageFixture(103, 1, 40, "mcp-server")
	rows, _ = classifyResourceProcesses([]ResourceProcess{mcp}, nil, nil, nil)
	if len(rows) != 1 || rows[0].Role != "unattributed" || rows[0].Manageable {
		t.Fatalf("unattributed row=%+v", rows)
	}
	root.HarnessEvidence = ""
	child := resourceLineageFixture(104, 101, 40, "node")
	rows, _ = classifyResourceProcesses([]ResourceProcess{root, child}, nil, nil, nil)
	if len(rows) != 2 || rows[1].Manageable || rows[1].ProtectionReason == "" {
		t.Fatal("a name-only harness match cannot authorize descendant actions")
	}
}

func TestResourceLineageProtectsManagementDescendantsAfterLoss(t *testing.T) {
	root := resourceLineageFixture(101, 40, 10, "claude")
	child := resourceLineageFixture(102, 101, 20, "fd-runner")
	managed := map[int]ResourceSessionInfo{101: {
		SessionID: "agent", State: "idle", StartTicks: 10, ExeIdentity: "1:1", ResourceAgent: true,
	}}
	rows, prior := classifyResourceProcesses([]ResourceProcess{root, child}, nil, managed, nil)
	if len(rows) != 2 || rows[0].Role != "management" || !rows[0].Protected ||
		!rows[1].Protected || rows[0].Manageable || rows[1].Manageable {
		t.Fatalf("management rows=%+v", rows)
	}
	rows, _ = classifyResourceProcesses([]ResourceProcess{root, child}, prior, nil, nil)
	if !rows[0].Protected || !rows[1].Protected || rows[1].Manageable {
		t.Fatalf("runner restart must not unprotect a surviving management lineage: %+v", rows)
	}
	child.ParentPID = 1
	rows, _ = classifyResourceProcesses([]ResourceProcess{child}, prior, nil, nil)
	if len(rows) != 1 || !rows[0].Protected || rows[0].Manageable {
		t.Fatalf("lost management lineage=%+v", rows)
	}
	child.ParentPID, child.Comm = root.PID, "claude"
	grandchild := resourceLineageFixture(103, child.PID, 30, "node")
	rows, _ = classifyResourceProcesses([]ResourceProcess{root, child, grandchild}, nil, managed, nil)
	if len(rows) != 3 || !rows[1].Protected || !rows[2].Protected {
		t.Fatal("a nested harness name must not erase management protection")
	}
}

func TestResourceLineageRequiresManagedGenerationAndProtectsContainers(t *testing.T) {
	root := resourceLineageFixture(101, 40, 10, "claude")
	container := resourceLineageFixture(102, 101, 20, "docker")
	managed := map[int]ResourceSessionInfo{101: {
		SessionID: "session", State: "busy", StartTicks: 11, ExeIdentity: "1:1",
	}}
	rows, _ := classifyResourceProcesses([]ResourceProcess{root, container}, nil, managed, nil)
	if rows[0].Managed || rows[0].Manageable || !rows[1].Protected || rows[1].Manageable {
		t.Fatalf("rows=%+v", rows)
	}
	session := managed[101]
	session.StartTicks = 10
	managed[101] = session
	rows, _ = classifyResourceProcesses([]ResourceProcess{root}, nil, managed, nil)
	if len(rows) != 1 || !rows[0].Managed || !rows[0].Manageable || rows[0].SessionID != "session" {
		t.Fatalf("verified managed root=%+v", rows)
	}
}

func TestResourceStdioIsAnObservedPipeLinkNotAnMCPClaim(t *testing.T) {
	parent := ResourceRow{ResourceProcess: resourceLineageFixture(101, 40, 10, "claude")}
	child := ResourceRow{ResourceProcess: resourceLineageFixture(102, 101, 20, "node")}
	parent.FDLinks = map[string]string{"5": "pipe:[11]", "6": "pipe:[12]"}
	child.FDLinks = map[string]string{"0": "pipe:[11]", "1": "pipe:[12]"}
	rows := []ResourceRow{parent, child}
	annotateResourceStdio(rows)
	if rows[1].StdioPeerKey != parent.Key || rows[0].StdioPeerKey != "" {
		t.Fatalf("pipe links=%+v", rows)
	}
	rows[1].FDLinks = map[string]string{"0": "pipe:[11]"}
	annotateResourceStdio(rows)
	if rows[1].StdioPeerKey != "" {
		t.Fatal("a vanished pipe link must not remain visible")
	}
	child.UID = 1001
	rows = []ResourceRow{parent, child}
	annotateResourceStdio(rows)
	if rows[1].StdioPeerKey != "" {
		t.Fatal("pipe links must not cross ownership")
	}
}
