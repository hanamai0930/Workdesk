package components

import (
	"context"
	"io"
)

// Progress describes work within a phase. Total zero means the source does
// not provide a meaningful length for that phase.
type Progress struct {
	Phase string `json:"phase"`
	Done  int64  `json:"done"`
	Total int64  `json:"total"`
}

func (m *Manager) progress(phase string, done, total int64) {
	if m.Progress != nil {
		if total < done {
			total = 0
		}
		m.Progress(Progress{Phase: phase, Done: done, Total: total})
	}
}

type progressWriter struct {
	ctx    context.Context
	target io.Writer
	done   int64
	report func(int64)
}

func (w *progressWriter) Write(p []byte) (int, error) {
	if err := w.ctx.Err(); err != nil {
		return 0, err
	}
	n, err := w.target.Write(p)
	if n > 0 {
		w.done += int64(n)
		w.report(w.done)
	}
	return n, err
}
