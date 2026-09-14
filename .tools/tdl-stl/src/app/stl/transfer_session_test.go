package stl

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/gotd/td/rpc"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/tmedia"
)

type functionClient struct {
	downloader.Client
	get func(context.Context, *tg.UploadGetFileRequest) (tg.UploadFileClass, error)
}

func (c functionClient) UploadGetFile(ctx context.Context, r *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
	return c.get(ctx, r)
}

func readTestEvents(t *testing.T, e *Events) []map[string]any {
	t.Helper()
	data, err := os.ReadFile(e.file.Name())
	if err != nil {
		t.Fatal(err)
	}
	var events []map[string]any
	for _, line := range bytes.Split(bytes.TrimSpace(data), []byte{'\n'}) {
		var event map[string]any
		if err := json.Unmarshal(line, &event); err != nil {
			t.Fatal(err)
		}
		events = append(events, event)
	}
	return events
}

func TestConnectionRetriesAreBoundedAndReuseCompletedChunks(t *testing.T) {
	for _, failAll := range []bool{false, true} {
		t.Run(fmt.Sprint(failAll), func(t *testing.T) {
			o, e, _, _ := speedFixture(t)
			o.Server = "manual"
			file, err := os.CreateTemp(t.TempDir(), "payload")
			if err != nil {
				t.Fatal(err)
			}
			defer file.Close()
			size := int64(3*partSize + 17)
			attempts := 0
			firstReads := 0
			var mu sync.Mutex
			hooks := transferHooks{
				connect: func(ctx context.Context, key string) (downloader.Client, func(), error) {
					attempts++
					return functionClient{get: func(ctx context.Context, r *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
						if r.Offset == 0 {
							mu.Lock()
							firstReads++
							mu.Unlock()
							return strictFileClient{size: size}.UploadGetFile(ctx, r)
						}
						if failAll || attempts < 3 {
							for {
								info, _ := file.Stat()
								if info.Size() >= partSize {
									break
								}
								select {
								case <-ctx.Done():
									return nil, ctx.Err()
								case <-time.After(time.Millisecond):
								}
							}
							return nil, rpc.ErrEngineClosed
						}
						return strictFileClient{size: size}.UploadGetFile(ctx, r)
					}}, func() {}, nil
				},
				selectEndpoint: func(context.Context, Options) (string, error) {
					t.Fatal("manual endpoint must be preserved")
					return "", nil
				},
				monitor: func(Options, string) *speedMonitor { return nil },
			}
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			err = transferWithSwitches(ctx, &tmedia.Media{Size: size}, o, e, file, "manual", hooks)
			if (err != nil) != failAll || attempts != 3 || firstReads != 1 {
				t.Fatal("invalid retry result", err, attempts, firstReads)
			}
		})
	}
}

func TestCriticalSwitchRetainsChunksAndProgress(t *testing.T) {
	for _, mode := range []string{"faster", "failed_probes", "connection_retry", "stalled"} {
		t.Run(mode, func(t *testing.T) {
			o, e, endpoints, s := speedFixture(t)
			s.Health = nil
			if err := WriteJSON(o.Cache, Cache{cacheKey(o): s}); err != nil {
				t.Fatal(err)
			}
			size := int64(64*partSize + 37)
			file, err := os.CreateTemp(t.TempDir(), "payload")
			if err != nil {
				t.Fatal(err)
			}
			defer file.Close()
			var mu sync.Mutex
			calls := map[int64]int{}
			connections, comparisons := 0, 0
			closed := false
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			hooks := transferHooks{
				connect: func(ctx context.Context, key string) (downloader.Client, func(), error) {
					connections++
					if connections > 2 {
						t.Fatal("unbounded retry")
					}
					if connections == 1 {
						return functionClient{get: func(ctx context.Context, r *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
							if r.Offset == 0 && mode != "stalled" {
								return strictFileClient{size: size}.UploadGetFile(ctx, r)
							}
							if mode == "connection_retry" {
								for {
									if info, err := file.Stat(); err == nil && info.Size() >= partSize {
										return nil, errors.New("simulated connection failure after first write")
									}
									select {
									case <-ctx.Done():
										return nil, ctx.Err()
									case <-time.After(time.Millisecond):
									}
								}
							}
							<-ctx.Done()
							return nil, ctx.Err()
						}}, func() { closed = true }, nil
					}
					want := "new"
					if mode == "failed_probes" {
						want = "old"
					}
					if key != want {
						t.Errorf("resumed on %s, wanted %s", key, want)
					}
					return functionClient{get: func(ctx context.Context, r *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
						mu.Lock()
						calls[r.Offset]++
						mu.Unlock()
						return strictFileClient{size: size}.UploadGetFile(ctx, r)
					}}, func() {}, nil
				},
				selectEndpoint: func(ctx context.Context, options Options) (string, error) {
					if !closed {
						t.Error("probes compete with previous transfer")
					}
					comparisons++
					return chooseEndpoint(ctx, endpoints, &tmedia.Media{Size: size}, options, e, func(_ context.Context, key string, d time.Duration) Measurement {
						speed := 8e6
						if key == "new" {
							speed = 28e6
						}
						return Measurement{Endpoint: key, BPS: speed, Failed: mode == "failed_probes"}
					})
				},
				monitor: func(options Options, key string) *speedMonitor {
					now := time.Now()
					m := newSpeedMonitor(options, key, e, now)
					if connections == 1 && mode != "connection_retry" {
						m.watch.start = now.Add(-60 * time.Second)
						m.watch.points = []speedPoint{{at: now.Add(-31 * time.Second), bytes: 0}}
					}
					return m
				},
			}
			err = transferWithSwitches(ctx, &tmedia.Media{Size: size, DC: 4}, o, e, file, "old", hooks)
			if err != nil {
				t.Fatal(err)
			}
			if comparisons != 1 || connections != 2 {
				t.Fatal("wrong switch count", comparisons, connections)
			}
			saved := int64(partSize)
			if mode == "stalled" {
				saved = 0
			}
			if calls[0] != 0 && saved > 0 {
				t.Fatal("already saved bytes downloaded again")
			}
			for off := saved; off < size; off += partSize {
				if calls[off] != 1 {
					t.Fatalf("range %d requested %d times", off, calls[off])
				}
			}
			buffer := make([]byte, partSize)
			for off := int64(0); off < size; off += partSize {
				n := int(min(int64(partSize), size-off))
				if _, err := file.ReadAt(buffer[:n], off); err != nil {
					t.Fatal(err)
				}
				if !bytes.Equal(buffer[:n], bytes.Repeat([]byte{byte(off / partSize)}, n)) {
					t.Fatal("corrupt resumed output", off)
				}
			}
			starts, resumes := 0, 0
			last := float64(0)
			for _, event := range readTestEvents(t, e) {
				switch event["event"] {
				case "download_start":
					starts++
				case "download_resumed":
					resumes++
					if event["bytes"] != float64(saved) {
						t.Fatal("resume lost saved progress", event)
					}
				case "server_slow_retest":
					if event["reason"] != "below_half_threshold" {
						t.Fatal("wrong retest reason")
					}
				case "progress":
					done := event["bytes"].(float64)
					if done < last || done > float64(size) {
						t.Fatal("non-monotonic or double-counted progress")
					}
					last = done
				}
			}
			if starts != 1 || resumes != 1 {
				t.Fatal("resume reset download", starts, resumes)
			}
			if mode != "connection_retry" {
				selection := readCache(o.Cache)[cacheKey(o)]
				if selection.Reason != "below_half_threshold" || selection.LastSlowCheck.IsZero() {
					t.Fatal("missing critical comparison cooldown")
				}
			}
		})
	}
}

func TestRetainedOutOfOrderChunksLeaveHolesForNetwork(t *testing.T) {
	size := int64(4*partSize + 37)
	f, err := os.CreateTemp(t.TempDir(), "payload")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	w := &countWriter{file: f, total: size, chunks: map[int64]int{}}
	for _, off := range []int64{3 * partSize, partSize} {
		if _, err := w.WriteAt(bytes.Repeat([]byte{byte(off / partSize)}, partSize), off); err != nil {
			t.Fatal(err)
		}
	}
	var mu sync.Mutex
	seen := map[int64]int{}
	api := functionClient{get: func(ctx context.Context, r *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
		mu.Lock()
		seen[r.Offset]++
		mu.Unlock()
		return strictFileClient{size: size}.UploadGetFile(ctx, r)
	}}
	if err := transfer(context.Background(), api, &tmedia.Media{Size: size}, w); err != nil {
		t.Fatal(err)
	}
	if w.done != size || seen[partSize] != 0 || seen[3*partSize] != 0 || seen[0] != 1 || seen[2*partSize] != 1 || seen[4*partSize] != 1 {
		t.Fatal("holes or duplicate bytes", w.done, seen)
	}
	if err := f.Truncate(partSize); err != nil {
		t.Fatal(err)
	}
	_, err = (retainedClient{Client: api, writer: w}).UploadGetFile(context.Background(), &tg.UploadGetFileRequest{Offset: 3 * partSize, Limit: partSize})
	if !errors.Is(err, io.EOF) {
		t.Fatal("missing retained data accepted", err)
	}
}

func TestCancellationDoesNotTriggerServerComparison(t *testing.T) {
	o, e, _, _ := speedFixture(t)
	f, err := os.CreateTemp(t.TempDir(), "payload")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	ctx, cancel := context.WithCancel(context.Background())
	closed := false
	err = transferWithSwitches(ctx, &tmedia.Media{Size: partSize}, o, e, f, "old", transferHooks{
		connect: func(context.Context, string) (downloader.Client, func(), error) {
			return functionClient{get: func(ctx context.Context, _ *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
				cancel()
				<-ctx.Done()
				return nil, ctx.Err()
			}}, func() { closed = true }, nil
		},
		selectEndpoint: func(context.Context, Options) (string, error) {
			t.Error("manual stop triggered probes")
			return "", nil
		},
		monitor: func(Options, string) *speedMonitor { return nil },
	})
	if !errors.Is(err, context.Canceled) || !closed {
		t.Fatal("stop did not cancel/close", err)
	}
}
