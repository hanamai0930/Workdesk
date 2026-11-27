package app

import (
	"context"
	"encoding/json"
	"os/exec"
	"sync"
	"time"
	"workdesk/core/internal/bridge"
)

type documentProviderProbe struct {
	mu           sync.Mutex
	expires      time.Time
	capabilities map[string]any
}

func (a *App) probeDocumentProviders() map[string]any {
	p := &a.documentProbe
	p.mu.Lock()
	defer p.mu.Unlock()
	if time.Now().Before(p.expires) {
		return p.capabilities
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, a.Profile.PythonExecutable, "-B", "-X", "utf8", "-m", "researchos.workdesk.professional_tools.document_probe")
	cmd.Dir = a.Profile.ProgramRoot
	cmd.Env = bridge.Environment(map[string]string{"PYTHONPATH": a.pythonSources(), "WORKDESK_HOME": a.Profile.UserRoot, "WORKDESK_DATA_DIR": a.Profile.DataRoot})
	quietCommand(cmd)
	output, err := cmd.Output()
	p.capabilities = nil
	if err == nil {
		var found map[string]any
		if json.Unmarshal(output, &found) == nil && len(found) > 0 {
			p.capabilities = found
		}
	}
	p.expires = time.Now().Add(30 * time.Second)
	return p.capabilities
}
