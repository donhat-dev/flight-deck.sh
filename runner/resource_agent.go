package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

const (
	resourceTokenFile  = "agent.token"
	resourceAgentsFile = "agents.json"
)

func (r *resourceService) mintToken() {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		r.tokenErr = err
		return
	}
	r.token = hex.EncodeToString(buf)
	if err := writePrivateAtomic(r.tokenPath(), []byte(r.token)); err != nil {
		r.tokenErr = err
	}
}

func (r *resourceService) tokenPath() string {
	return filepath.Join(r.dir, resourceTokenFile)
}

func newUUID() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		return ""
	}
	buf[6] = (buf[6] & 0x0f) | 0x40
	buf[8] = (buf[8] & 0x3f) | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", buf[0:4], buf[4:6], buf[6:8], buf[8:10], buf[10:16])
}

func (r *resourceService) agentIDsPath() string {
	return filepath.Join(r.dir, resourceAgentsFile)
}

func (r *resourceService) loadAgentIDs() (map[string]bool, error) {
	out := map[string]bool{}
	raw, err := os.ReadFile(r.agentIDsPath())
	if err != nil {
		if os.IsNotExist(err) {
			return out, nil
		}
		return nil, fmt.Errorf("agent profile registry is unreadable: %w", err)
	}
	var data struct {
		IDs []string `json:"ids"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, fmt.Errorf("agent profile registry is malformed: %w", err)
	}
	for _, id := range data.IDs {
		out[id] = true
	}
	return out, nil
}

func (r *resourceService) agentRegistered(id string) (bool, error) {
	ids, err := r.loadAgentIDs()
	if err != nil {
		return false, err
	}
	return ids[id], nil
}

func (r *resourceService) registerAgent(id string) error {
	if !resourceUUIDShape.MatchString(id) {
		return errors.New("management session id must be a UUID")
	}
	ids, err := r.loadAgentIDs()
	if err != nil {
		return err
	}
	if ids[id] {
		return nil
	}
	ids[id] = true
	list := []string{}
	for k := range ids {
		list = append(list, k)
	}
	raw, err := json.Marshal(map[string]any{"ids": list})
	if err != nil {
		return err
	}
	return writePrivateAtomic(r.agentIDsPath(), raw)
}

func (m *Manager) ensureAgent(c Command) error {
	id := c.SessionID
	if id == "" {
		return errors.New("resource agent start needs a session_id")
	}
	if known, err := m.res.agentRegistered(id); err != nil {
		return err
	} else if known {
		if s := m.get(id); s != nil && !s.resourceAgent {
			return errors.New("a management UUID holds a non-management session; refusing")
		}
	}
	if s := m.get(id); s != nil {
		m.setState(s, s.runtimeState())
		return nil
	}
	pid, err := m.livePID(id)
	if err != nil {
		return fmt.Errorf("cannot verify the session is not open elsewhere (%v); refusing", err)
	}
	if pid != 0 {
		return fmt.Errorf("session is open in an interactive process (pid %d)", pid)
	}
	c.Args.ResourceAgent = true
	if err := m.res.registerAgent(id); err != nil {
		return fmt.Errorf("could not record the management profile: %w", err)
	}
	if err := m.start(c); err != nil {
		return err
	}
	if err := m.handshakeAgent(id); err != nil {
		m.stop(id)
		return fmt.Errorf("resource agent never became ready: %w", err)
	}
	return nil
}

func (m *Manager) handshakeAgent(id string) error {
	s := m.get(id)
	if s == nil {
		return errors.New("session died before it was ready")
	}
	if _, err := m.res.controlCall(s, "", map[string]any{"subtype": "initialize"}); err != nil {
		return errors.New("initialize handshake failed")
	}
	s.meta.Lock()
	s.startTicks, s.exeIdentity = captureResourceIdentity("/proc", s.cmd.Process.Pid)
	s.meta.Unlock()
	return nil
}
