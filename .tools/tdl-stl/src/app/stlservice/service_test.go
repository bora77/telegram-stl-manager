package stlservice

import (
	"context"
	"errors"
	"net"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestIndependentJobsAndCancellation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "service.sock")
	source := "/example/source.json"
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	entered := make(chan string, 4)
	released := make(chan struct{})
	cancelled := make(chan struct{})
	handler := func(ctx context.Context, r Request) error {
		entered <- r.Args[0]
		if r.Args[0] == "scan" {
			<-ctx.Done()
			close(cancelled)
			return ctx.Err()
		}
		select {
		case <-released:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	done := make(chan error, 1)
	go func() { done <- Serve(ctx, path, source, time.Minute, handler) }()
	deadline := time.Now().Add(time.Second)
	for {
		conn, err := net.Dial("unix", path)
		if err == nil {
			conn.Close()
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("service did not start")
		}
		time.Sleep(time.Millisecond)
	}
	scanCtx, stopScan := context.WithCancel(ctx)
	var wg sync.WaitGroup
	wg.Add(2)
	downloadDone := make(chan struct{})
	go func() {
		defer wg.Done()
		handled, err := Call(ctx, path, Request{source, []string{"download"}})
		if !handled || err != nil {
			t.Errorf("download: %v %v", handled, err)
		}
		close(downloadDone)
	}()
	go func() {
		defer wg.Done()
		handled, err := Call(scanCtx, path, Request{source, []string{"scan"}})
		if !handled || err == nil {
			t.Errorf("cancelled scan: %v %v", handled, err)
		}
	}()
	for range 2 {
		select {
		case <-entered:
		case <-time.After(time.Second):
			t.Fatal("jobs were serialized")
		}
	}
	stopScan()
	select {
	case <-cancelled:
	case <-time.After(time.Second):
		t.Fatal("scan was not cancelled")
	}
	select {
	case <-downloadDone:
		t.Fatal("cancelling scan interrupted download")
	default:
	}
	if handled, err := Call(ctx, path, Request{source, []string{"health"}}); !handled || err != nil {
		t.Fatal("service was cancelled", err)
	}
	if handled, err := Call(ctx, path, Request{"/wrong/source.json", []string{"health"}}); !handled || err == nil {
		t.Fatal("wrong source accepted")
	}
	close(released)
	wg.Wait()
	cancel()
	if err := <-done; err != nil && !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
