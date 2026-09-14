package stl

import (
	"context"
	"errors"
	"io"
	"os"
	"time"

	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/tmedia"
)

// Replaying completed ranges from the local file lets the parallel downloader
// resume on another endpoint without requesting saved chunks over the network.
// The map records complete writes only; sparse holes must still be downloaded.
type retainedClient struct {
	downloader.Client
	writer *countWriter
}

func (c retainedClient) UploadGetFile(ctx context.Context, request *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	w := c.writer
	w.mu.Lock()
	n, saved := w.chunks[request.Offset]
	if saved && int64(n) == min(int64(request.Limit), w.total-request.Offset) && w.file != nil {
		data := make([]byte, n)
		read, err := w.file.ReadAt(data, request.Offset)
		w.mu.Unlock()
		if err != nil {
			return nil, err
		}
		if read != n {
			return nil, io.ErrUnexpectedEOF
		}
		return &tg.UploadFile{Type: &tg.StorageFileUnknown{}, Bytes: data}, nil
	}
	w.mu.Unlock()
	return c.Client.UploadGetFile(ctx, request)
}

type transferHooks struct {
	connect        func(context.Context, string) (downloader.Client, func(), error)
	selectEndpoint func(context.Context, Options) (string, error)
	monitor        func(Options, string) *speedMonitor
}

// Keep measuring even when no chunk arrives: a stalled connection has zero
// throughput and must be able to trigger the same comparison as a slow one.
func transferAttempt(ctx context.Context, api downloader.Client, file *tmedia.Media, w *countWriter) error {
	if w.speed == nil {
		return transfer(ctx, api, file, w)
	}
	attempt, cancel := context.WithCancelCause(ctx)
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-attempt.Done():
				return
			case now := <-ticker.C:
				w.mu.Lock()
				var err error
				if w.done < w.total {
					err = w.speed.observe(now, w.done)
				}
				w.mu.Unlock()
				if err != nil {
					cancel(err)
					return
				}
			}
		}
	}()
	err := transfer(attempt, api, file, w)
	if cause := context.Cause(attempt); cause != nil && (err == nil || errors.Is(err, context.Canceled)) {
		err = cause
	}
	cancel(nil)
	<-finished
	return err
}

func transferWithSwitches(ctx context.Context, file *tmedia.Media, o Options, e *Events, f *os.File, key string, hooks transferHooks) error {
	w := &countWriter{file: f, total: file.Size, events: e, chunks: map[int64]int{}}
	downloadTraffic.start(time.Now())
	defer downloadTraffic.finish()
	started, retried := false, false
	for {
		if err := ctx.Err(); err != nil {
			return err
		}
		api, closePool, err := hooks.connect(ctx, key)
		if err == nil {
			kind := "download_start"
			if started {
				kind = "download_resumed"
			}
			if err = e.Emit(kind, map[string]any{"bytes": w.done, "total": file.Size, "endpoint": key}); err != nil {
				closePool()
				return err
			}
			started = true
			w.speed = hooks.monitor(o, key)
			err = transferAttempt(ctx, api, file, w)
			// Parallel waits for canceled requests before returning. Close the old
			// pool before comparing so it cannot compete with the speed samples.
			closePool()
			if flushErr := w.speed.flush(); err == nil {
				err = flushErr
			}
			if errors.Is(err, errImmediateRetest) && w.done == file.Size {
				err = nil
			}
			if err == nil && w.done != file.Size {
				err = errors.New("download byte count did not match expected size")
			}
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err == nil {
			return nil
		}
		immediate := errors.Is(err, errImmediateRetest)
		if o.Server != "auto" || (!immediate && retried) {
			return err
		}
		if !immediate {
			retried = true
			if err = e.Emit("server_retry", nil); err != nil {
				return err
			}
		}
		o.ReuseServer = key
		// Let chooseEndpoint claim a slowdown comparison under the shared lock.
		// Another job may have compared already; never bypass its cooldown.
		o.Retest = !immediate
		o.QuickTest = false
		key, err = hooks.selectEndpoint(ctx, o)
		if err != nil {
			return err
		}
		if err = e.Emit("server_selected", map[string]any{"endpoint": key, "dc": file.DC}); err != nil {
			return err
		}
	}
}
