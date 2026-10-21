package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

func runFd(args ...string) (int, string, string) {
	var out, errb bytes.Buffer
	code := run(args, &out, &errb)
	return code, out.String(), errb.String()
}

// oneDocument decodes stdout and fails if anything follows the first JSON value.
func oneDocument(t *testing.T, stdout string, v any) {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(stdout))
	if err := dec.Decode(v); err != nil {
		t.Fatalf("stdout is not JSON: %v\n%s", err, stdout)
	}
	if dec.More() {
		t.Fatalf("more than one JSON document on stdout:\n%s", stdout)
	}
}

func TestVersionPrintsOneJSONDocument(t *testing.T) {
	code, out, _ := runFd("version", "--json")
	if code != 0 {
		t.Fatalf("exit %d", code)
	}
	var v map[string]string
	oneDocument(t, out, &v)
	if v["version"] == "" || v["go"] == "" {
		t.Fatalf("got %v", v)
	}
}

func TestUnknownCommandExitsThreeWithTheErrorAsData(t *testing.T) {
	code, out, _ := runFd("nope", "--json")
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 3 || v["error"] == "" {
		t.Fatalf("exit %d, %v", code, v)
	}
}

func TestABadFlagIsARefusal(t *testing.T) {
	code, out, _ := runFd("version", "--json", "--bogus")
	var v map[string]string
	oneDocument(t, out, &v)
	if code != 2 || !strings.Contains(v["error"], "bogus") {
		t.Fatalf("exit %d, %v", code, v)
	}
}

func TestWithoutJSONARefusalGoesToStderr(t *testing.T) {
	code, out, errOut := runFd("version", "--bogus")
	if code != 2 || out != "" || !strings.Contains(errOut, "bogus") {
		t.Fatalf("exit %d stdout %q stderr %q", code, out, errOut)
	}
}
