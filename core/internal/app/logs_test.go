package app

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

func TestToolLogSegmentsHaveBoundedVerifiedChunks(t *testing.T) {
	data := t.TempDir()
	root := filepath.Join(data, "results", "fixture-logs")
	writer := &segmentWriter{data: data, root: root, stream: "stderr"}
	input := bytes.Repeat([]byte("A日志\n"), 1100000)
	if _, e := writer.Write(input); e != nil {
		t.Fatal(e)
	}
	if e := writer.Close(); e != nil {
		t.Fatal(e)
	}
	if len(writer.segments) < 2 {
		t.Fatalf("long log was not segmented: %d", len(writer.segments))
	}
	reconstructed := []byte{}
	for _, segment := range writer.segments {
		if segment.Bytes > logSegmentBytes || segment.ChunkBytes != logChunkBytes {
			t.Fatal("unbounded log segment")
		}
		raw, e := os.ReadFile(filepath.Join(data, filepath.FromSlash(segment.RelativePath)))
		if e != nil {
			t.Fatal(e)
		}
		for index, start := 0, 0; start < len(raw); index, start = index+1, start+logChunkBytes {
			end := min(start+logChunkBytes, len(raw))
			hash := sha256.Sum256(raw[start:end])
			if hex.EncodeToString(hash[:]) != segment.ChunkSHA256[index] {
				t.Fatal("chunk hash changed")
			}
		}
		reconstructed = append(reconstructed, raw...)
	}
	if !bytes.Equal(reconstructed, input) {
		t.Fatal("log bytes were truncated or duplicated")
	}
}
