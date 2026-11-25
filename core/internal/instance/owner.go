package instance

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

type OwnerLock struct {
	file   *os.File
	unlock func() error
	once   sync.Once
	err    error
}

// AcquireOwner locks exactly byte [0,1) of the existing backend.lock file. It
// interoperates with the old Python owner instead of creating a separate mutex.
func AcquireOwner(dataRoot string) (*OwnerLock, error) {
	root, err := resolvePath(dataRoot, "data_root")
	if err != nil {
		return nil, err
	}
	if err = os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	path := filepath.Join(root, "backend.lock")
	if info, err := os.Lstat(path); err == nil {
		if info.Mode()&os.ModeSymlink != 0 || !info.Mode().IsRegular() {
			return nil, errors.New("owner lock must be a regular file")
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	unlock, err := lockFile(f)
	if err != nil {
		f.Close()
		return nil, fmt.Errorf("active library already has an execution owner: %w", err)
	}
	// Lock first: an old Windows owner protects this byte against writes too.
	if info, err := f.Stat(); err != nil {
		unlock()
		f.Close()
		return nil, err
	} else if info.Size() == 0 {
		if _, err = f.WriteAt([]byte{'0'}, 0); err != nil {
			unlock()
			f.Close()
			return nil, err
		}
		if err = f.Sync(); err != nil {
			unlock()
			f.Close()
			return nil, err
		}
	}
	return &OwnerLock{file: f, unlock: unlock}, nil
}

func (l *OwnerLock) Close() error {
	l.once.Do(func() {
		l.err = l.unlock()
		closeErr := l.file.Close()
		if l.err == nil {
			l.err = closeErr
		}
	})
	return l.err
}
