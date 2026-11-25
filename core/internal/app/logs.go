package app

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
)

const logSegmentBytes = 4 * 1024 * 1024
const logChunkBytes = 65536

type LogSegment struct {
	Stream       string   `json:"stream"`
	Segment      int      `json:"segment"`
	RelativePath string   `json:"relative_path"`
	Bytes        int64    `json:"bytes"`
	SHA256       string   `json:"sha256"`
	ChunkBytes   int      `json:"chunk_bytes"`
	ChunkSHA256  []string `json:"chunk_sha256"`
}
type segmentWriter struct {
	data, root, stream string
	file               *os.File
	size               int64
	segments           []LogSegment
}

func (w *segmentWriter) Write(raw []byte) (int, error) {
	total := 0
	for len(raw) > 0 {
		if w.file == nil {
			if e := os.MkdirAll(w.root, 0700); e != nil {
				return total, e
			}
			path := filepath.Join(w.root, fmt.Sprintf("%s.%06d.log", w.stream, len(w.segments)))
			if !inside(path, w.data) {
				return total, errors.New("日志位置越界")
			}
			file, e := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
			if e != nil {
				return total, e
			}
			w.file = file
			w.size = 0
		}
		n := int64(len(raw))
		if n > logSegmentBytes-w.size {
			n = logSegmentBytes - w.size
		}
		written, e := w.file.Write(raw[:int(n)])
		w.size += int64(written)
		total += written
		raw = raw[written:]
		if e != nil {
			return total, e
		}
		if w.size == logSegmentBytes {
			if e = w.seal(); e != nil {
				return total, e
			}
		}
	}
	return total, nil
}
func (w *segmentWriter) seal() error {
	if w.file == nil {
		return nil
	}
	path := w.file.Name()
	if e := w.file.Sync(); e != nil {
		return e
	}
	if e := w.file.Close(); e != nil {
		return e
	}
	w.file = nil
	file, e := os.Open(path)
	if e != nil {
		return e
	}
	defer file.Close()
	hash := sha256.New()
	chunks := []string{}
	block := make([]byte, logChunkBytes)
	for {
		n, err := io.ReadFull(file, block)
		if err != nil && err != io.EOF && err != io.ErrUnexpectedEOF {
			return err
		}
		if n > 0 {
			hash.Write(block[:n])
			digest := sha256.Sum256(block[:n])
			chunks = append(chunks, hex.EncodeToString(digest[:]))
		}
		if err != nil {
			break
		}
	}
	relative, e := filepath.Rel(w.data, path)
	if e != nil {
		return e
	}
	w.segments = append(w.segments, LogSegment{Stream: w.stream, Segment: len(w.segments), RelativePath: filepath.ToSlash(relative), Bytes: w.size, SHA256: hex.EncodeToString(hash.Sum(nil)), ChunkBytes: logChunkBytes, ChunkSHA256: chunks})
	return nil
}
func (w *segmentWriter) Close() error { return w.seal() }

func (a *App) serveLog(w http.ResponseWriter, r *http.Request, id string) (any, error) {
	rows, e := a.query(`SELECT data FROM core_tool_jobs WHERE id=?`, id)
	if e != nil {
		return nil, e
	}
	if len(rows) != 1 {
		return nil, errors.New("工具运行不存在")
	}
	var job Job
	if e = json.Unmarshal([]byte(str(rows[0]["data"])), &job); e != nil {
		return nil, e
	}
	stream := r.URL.Query().Get("stream")
	if stream == "" {
		stream = "stdout"
	}
	segment, e := strconv.Atoi(r.URL.Query().Get("segment"))
	if e != nil || segment < 0 {
		segment = 0
	}
	offset, e := strconv.ParseInt(r.URL.Query().Get("offset"), 10, 64)
	if e != nil {
		offset = 0
	}
	if offset < 0 {
		return nil, errors.New("日志位置无效")
	}
	limit := bounded(r.URL.Query().Get("limit"), 65536, 256*1024)
	for _, entry := range job.Logs {
		if entry.Stream != stream || entry.Segment != segment {
			continue
		}
		path := filepath.Join(a.Profile.DataRoot, filepath.FromSlash(entry.RelativePath))
		root := filepath.Join(a.Profile.DataRoot, "results", job.ID+"-logs")
		if !inside(path, root) {
			return nil, errors.New("日志引用越界")
		}
		file, e := os.Open(path)
		if e != nil {
			return nil, e
		}
		defer file.Close()
		info, e := file.Stat()
		if e != nil {
			return nil, e
		}
		if info.Size() != entry.Bytes {
			return nil, errors.New("日志大小与清单不符")
		}
		if offset > entry.Bytes {
			return nil, errors.New("日志游标超过文件末尾")
		}
		end := min(offset+int64(limit), entry.Bytes)
		raw := []byte{}
		verified := int64(0)
		if entry.ChunkBytes == logChunkBytes && len(entry.ChunkSHA256) == int((entry.Bytes+logChunkBytes-1)/logChunkBytes) {
			for start := offset / logChunkBytes * logChunkBytes; start < end; start += logChunkBytes {
				block := make([]byte, min(int64(logChunkBytes), entry.Bytes-start))
				n, err := file.ReadAt(block, start)
				if err != nil && err != io.EOF {
					return nil, err
				}
				if n != len(block) {
					return nil, errors.New("日志块缺失")
				}
				digest := sha256.Sum256(block)
				if hex.EncodeToString(digest[:]) != entry.ChunkSHA256[start/logChunkBytes] {
					return nil, errors.New("日志块版本不一致")
				}
				verified += int64(n)
				lo := max(offset-start, 0)
				hi := min(end-start, int64(n))
				raw = append(raw, block[lo:hi]...)
			}
		} else {
			hash := sha256.New()
			if _, e = io.Copy(hash, file); e != nil {
				return nil, e
			}
			verified = entry.Bytes
			if hex.EncodeToString(hash.Sum(nil)) != entry.SHA256 {
				return nil, errors.New("日志版本不一致")
			}
			if _, e = file.Seek(offset, io.SeekStart); e != nil {
				return nil, e
			}
			raw = make([]byte, end-offset)
			if _, e = io.ReadFull(file, raw); e != nil {
				return nil, e
			}
		}
		return map[string]any{"encoding": "base64", "data": base64.StdEncoding.EncodeToString(raw), "offset": offset, "next_offset": end, "total_bytes": entry.Bytes, "verified_bytes": verified, "has_more": end < entry.Bytes, "stream": stream, "segment": segment}, nil
	}
	return nil, errors.New("日志段未登记或尚未封存")
}
