package telegram

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/gotd/td/crypto"
	"github.com/gotd/td/pool"
)

func TestConcurrentFilePoolsAuthorizeEachDCKeyOnce(t *testing.T) {
	var key crypto.Key
	key[0] = 1
	session := pool.NewSyncSession(pool.Session{DC: 1, AuthKey: key.WithID()})
	c := &Client{sessions: map[int]*pool.SyncSession{1: session}}
	var calls atomic.Int32
	var group sync.WaitGroup
	for n := 0; n < 20; n++ {
		group.Add(1)
		go func() {
			defer group.Done()
			if err := c.authorizeDCOnce(context.Background(), 1, func() error { calls.Add(1); return nil }); err != nil {
				t.Error(err)
			}
		}()
	}
	group.Wait()
	if calls.Load() != 1 {
		t.Fatal("reimported authorization for an active key", calls.Load())
	}
	// A replaced key needs its own import; the old receipt must not bypass it.
	key[0] = 2
	session.Store(pool.Session{DC: 1, AuthKey: key.WithID()})
	if err := c.authorizeDCOnce(context.Background(), 1, func() error { calls.Add(1); return nil }); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 2 {
		t.Fatal("replacement key was not authorized")
	}
}

func TestFailedOrCancelledDCInitializationIsNeverCached(t *testing.T) {
	var key crypto.Key
	key[0] = 1
	c := &Client{sessions: map[int]*pool.SyncSession{1: pool.NewSyncSession(pool.Session{DC: 1, AuthKey: key.WithID()})}}
	sentinel := errors.New("import failed")
	if err := c.authorizeDCOnce(context.Background(), 1, func() error { return sentinel }); !errors.Is(err, sentinel) {
		t.Fatal(err)
	}
	calls := 0
	if err := c.authorizeDCOnce(context.Background(), 1, func() error { calls++; return nil }); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatal("failed import was cached")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := c.authorizeDCOnce(ctx, 2, func() error { t.Error("cancelled setup ran"); return nil }); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
