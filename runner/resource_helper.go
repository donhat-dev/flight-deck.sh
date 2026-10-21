package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"os"
	"strings"
	"time"
)

const helperMaxLine = 64 * 1024

var helperMethods = map[string]bool{
	"initialize": true, "notifications/initialized": true, "ping": true,
	"tools/list": true, "tools/call": true,
}

func runResourceHelper(sockPath, tokenPath string) {
	tokenRaw, err := os.ReadFile(tokenPath)
	if err != nil {
		log.Fatalf("resource-mcp: token: %v", err)
	}
	token := strings.TrimSpace(string(tokenRaw))
	sc := bufio.NewScanner(os.Stdin)
	sc.Buffer(make([]byte, 1<<10), helperMaxLine)
	out := bufio.NewWriter(os.Stdout)
	for sc.Scan() {
		line := sc.Bytes()
		var req struct {
			ID     any    `json:"id"`
			Method string `json:"method"`
		}
		if err := json.Unmarshal(line, &req); err != nil || !helperMethods[req.Method] {
			writeHelperLine(out, map[string]any{"jsonrpc": "2.0", "id": req.ID,
				"error": map[string]any{"code": -32601, "message": "method not allowed"}})
			continue
		}
		var msg map[string]any
		if json.Unmarshal(line, &msg) != nil {
			continue
		}
		msg["token"] = token
		notify := strings.HasPrefix(req.Method, "notifications/")
		resp, err := helperCall(sockPath, msg, notify)
		if err != nil {
			if notify {
				continue
			}
			writeHelperLine(out, map[string]any{"jsonrpc": "2.0", "id": req.ID,
				"error": map[string]any{"code": -32000, "message": err.Error()}})
			continue
		}
		if notify {
			continue
		}
		out.Write(resp)
		out.WriteByte('\n')
		out.Flush()
	}
}

func helperCall(sockPath string, msg map[string]any, notify bool) ([]byte, error) {
	conn, err := net.DialTimeout("unix", sockPath, 5*time.Second)
	if err != nil {
		return nil, err
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(30 * time.Second))
	raw, err := json.Marshal(msg)
	if err != nil {
		return nil, err
	}
	if _, err := conn.Write(append(raw, '\n')); err != nil {
		return nil, err
	}
	if notify {
		return nil, nil
	}
	sc := bufio.NewScanner(conn)
	sc.Buffer(make([]byte, 1<<10), 4<<20)
	if !sc.Scan() {
		return nil, fmt.Errorf("gateway closed without answering")
	}
	return sc.Bytes(), nil
}

func writeHelperLine(out *bufio.Writer, v any) {
	raw, _ := json.Marshal(v)
	out.Write(raw)
	out.WriteByte('\n')
	out.Flush()
}
