// Package stl implements the local application's bounded, machine-readable CLI.
package stl

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/gotd/td/telegram"
	"github.com/gotd/td/telegram/downloader"
	"github.com/gotd/td/telegram/peers"
	"github.com/gotd/td/tg"

	"github.com/iyear/tdl/core/stltransport"
	"github.com/iyear/tdl/core/storage"
	"github.com/iyear/tdl/core/tclient"
	"github.com/iyear/tdl/core/tmedia"
	"github.com/iyear/tdl/core/util/tutil"
)

const partSize = 1024 * 1024

type Source struct {
	ChatID       int64 `json:"chat_id"`
	TOCMessageID int   `json:"toc_message_id"`
}

func LoadSource(path string) (Source, error) {
	var source Source
	data, err := os.ReadFile(path)
	if err != nil {
		return source, errors.New("private source configuration is missing or unreadable")
	}
	if len(data) > 4096 || json.Unmarshal(data, &source) != nil || source.ChatID <= 0 || source.ChatID > 9007199254740991 || source.TOCMessageID <= 0 || source.TOCMessageID > 2147483647 {
		return Source{}, errors.New("invalid private source configuration")
	}
	return source, nil
}

type Endpoint struct {
	Key     string `json:"key"`
	DC      int    `json:"dc"`
	Address string `json:"address"`
	Port    int    `json:"port"`
	Media   bool   `json:"media"`
	IPv6    bool   `json:"ipv6"`
}

func Servers(c *telegram.Client) []Endpoint {
	seen := map[string]bool{}
	out := []Endpoint{}
	for _, o := range c.Config().DCOptions {
		key := stltransport.Key(o)
		if !stltransport.Allowed(o) || seen[key] {
			continue
		}
		seen[key] = true
		out = append(out, Endpoint{key, o.ID, o.IPAddress, o.Port, o.MediaOnly, o.Ipv6})
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := out[i], out[j]
		if a.DC != b.DC {
			return a.DC < b.DC
		}
		if a.Media != b.Media {
			return a.Media
		}
		if a.IPv6 != b.IPv6 {
			return !a.IPv6
		}
		return a.Key < b.Key
	})
	return out
}
func WriteJSON(path string, value any) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".stl-json-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	err = json.NewEncoder(f).Encode(value)
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}

type Events struct {
	mu      sync.Mutex
	encoder *json.Encoder
	file    *os.File
}

func OpenEvents(path string) (*Events, error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, err
	}
	return &Events{encoder: json.NewEncoder(f), file: f}, nil
}
func (e *Events) Emit(event string, values map[string]any) error {
	e.mu.Lock()
	defer e.mu.Unlock()
	if values == nil {
		values = map[string]any{}
	}
	values["event"] = event
	return e.encoder.Encode(values)
}
func (e *Events) Close() error { return e.file.Close() }

type Options struct {
	Topic, Message, DC                       int
	ChatID                                   int64
	DocumentID, Size                         int64
	Filename, Output, Server, Cache, Network string
	ReuseServer                              string
	ProbeOnly, Retest, IPv6, QuickTest       bool
	MinSpeedMBPS                             float64
}
type Measurement struct {
	Endpoint string  `json:"endpoint"`
	Bytes    int64   `json:"bytes"`
	Seconds  float64 `json:"seconds"`
	BPS      float64 `json:"bps"`
	Failed   bool    `json:"failed"`
}
type Selection struct {
	Endpoint         string        `json:"endpoint"`
	TestedAt         time.Time     `json:"tested_at"`
	Measurements     []Measurement `json:"measurements"`
	Health           *SpeedHealth  `json:"health,omitempty"`
	LastSlowCheck    time.Time     `json:"last_slow_check,omitempty"`
	PreviousEndpoint string        `json:"previous_endpoint,omitempty"`
	Reason           string        `json:"reason,omitempty"`
}
type Cache map[string]Selection

var cacheMu sync.Mutex

func readCache(path string) Cache {
	cache := Cache{}
	if data, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(data, &cache)
	}
	if cache == nil {
		cache = Cache{}
	}
	return cache
}

func cacheKey(o Options) string { return fmt.Sprintf("%s/%d", o.Network, o.DC) }

func verifyMessage(m *tg.Message, o Options) (*tmedia.Media, error) {
	peer, ok := m.PeerID.(*tg.PeerChannel)
	if !ok || o.ChatID <= 0 || peer.ChannelID != o.ChatID || m.ID != o.Message {
		return nil, errors.New("attachment outside approved group or message")
	}
	if m.ID != o.Topic {
		reply, ok := m.ReplyTo.(*tg.MessageReplyHeader)
		if !ok || !reply.ForumTopic {
			return nil, errors.New("attachment is not in the selected forum topic")
		}
		topic := reply.ReplyToTopID
		if topic == 0 {
			topic = reply.ReplyToMsgID
		}
		if topic != o.Topic {
			return nil, errors.New("attachment moved outside the selected topic")
		}
	}
	media, ok := m.Media.(*tg.MessageMediaDocument)
	if !ok {
		return nil, errors.New("selected attachment is not a document")
	}
	document, ok := media.Document.(*tg.Document)
	if !ok || document.ID != o.DocumentID {
		return nil, errors.New("attachment identity changed; rescan required")
	}
	file, ok := tmedia.GetMedia(m)
	if !ok || file.Name != o.Filename || file.Size != o.Size || file.DC != o.DC {
		return nil, errors.New("attachment filename, size or data center changed; rescan required")
	}
	return file, nil
}

func poolClient(ctx context.Context, c *telegram.Client, r *stltransport.Resolver, dc int, key string) (telegram.CloseInvoker, *tg.Client, error) {
	pool, err := c.MediaOnlyWithResolver(ctx, dc, 8, r.Selected(key))
	if err != nil {
		return nil, nil, err
	}
	var invoker tg.Invoker = pool
	chain := tclient.NewDefaultMiddlewares(context.WithoutCancel(ctx), 30*time.Second)
	for i := len(chain) - 1; i >= 0; i-- {
		invoker = chain[i].Handle(invoker)
	}
	return pool, tg.NewClient(invoker), nil
}

type countWriter struct {
	mu          sync.Mutex
	file        *os.File
	total, done int64
	last        time.Time
	events      *Events
	speed       *speedMonitor
}

func (w *countWriter) WriteAt(data []byte, off int64) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if off < 0 || off+int64(len(data)) > w.total {
		return 0, errors.New("received bytes exceed expected attachment size")
	}
	n := len(data)
	var err error
	if w.file != nil {
		n, err = w.file.WriteAt(data, off)
	}
	w.done += int64(n)
	if err == nil {
		err = w.speed.observe(time.Now(), w.done)
	}
	if err == nil && w.events != nil && time.Since(w.last) >= 250*time.Millisecond {
		w.last = time.Now()
		err = w.events.Emit("progress", map[string]any{"bytes": w.done, "total": w.total})
	}
	return n, err
}

// The generic downloader reads until EOF. Telegram can reject offsets beyond
// large, chunk-aligned files instead of returning an empty EOF response.
// Use the size verified from the message to stop those speculative requests.
type sizedClient struct {
	downloader.Client
	size int64
}

func (c sizedClient) UploadGetFile(ctx context.Context, request *tg.UploadGetFileRequest) (tg.UploadFileClass, error) {
	if request.Offset < 0 || request.Limit <= 0 {
		return nil, errors.New("invalid file range")
	}
	if request.Offset >= c.size {
		return &tg.UploadFile{Type: &tg.StorageFileUnknown{}}, nil
	}
	result, err := c.Client.UploadGetFile(ctx, request)
	if err != nil {
		return nil, err
	}
	if chunk, ok := result.(*tg.UploadFile); ok {
		expected := min(int64(request.Limit), c.size-request.Offset)
		if int64(len(chunk.Bytes)) != expected {
			return nil, fmt.Errorf("incomplete file chunk at %d: expected %d bytes, received %d", request.Offset, expected, len(chunk.Bytes))
		}
	}
	return result, nil
}

func transfer(ctx context.Context, api downloader.Client, file *tmedia.Media, w *countWriter) error {
	_, err := downloader.NewDownloader().WithPartSize(partSize).Download(sizedClient{Client: api, size: file.Size}, file.InputFileLoc).WithThreads(16).Parallel(ctx, w)
	return err
}
func probe(ctx context.Context, c *telegram.Client, r *stltransport.Resolver, file *tmedia.Media, key string, duration time.Duration) Measurement {
	result := Measurement{Endpoint: key}
	setup, cancel := context.WithTimeout(ctx, 8*time.Second)
	pool, api, err := poolClient(setup, c, r, file.DC, key)
	cancel()
	if err != nil {
		result.Failed = true
		return result
	}
	defer pool.Close()
	sample, stop := context.WithTimeout(ctx, duration)
	defer stop()
	w := &countWriter{total: file.Size}
	started := time.Now()
	err = transfer(sample, api, file, w)
	result.Bytes = w.done
	result.Seconds = time.Since(started).Seconds()
	result.Failed = w.done == 0 || (err != nil && !errors.Is(err, context.DeadlineExceeded) && !errors.Is(err, context.Canceled))
	if !result.Failed && result.Seconds > 0 {
		result.BPS = float64(w.done) / result.Seconds
	}
	return result
}

func choose(ctx context.Context, c *telegram.Client, r *stltransport.Resolver, file *tmedia.Media, o Options, e *Events) (string, error) {
	candidates := []Endpoint{}
	for _, endpoint := range Servers(c) {
		if endpoint.DC == file.DC && (o.IPv6 || !endpoint.IPv6) {
			candidates = append(candidates, endpoint)
		}
	}
	return chooseEndpoint(ctx, candidates, file, o, e, func(ctx context.Context, key string, duration time.Duration) Measurement {
		return probe(ctx, c, r, file, key, duration)
	})
}

func chooseEndpoint(ctx context.Context, candidates []Endpoint, file *tmedia.Media, o Options, e *Events, measure func(context.Context, string, time.Duration) Measurement) (string, error) {
	if len(candidates) == 0 {
		return "", errors.New("no usable Telegram endpoints for this attachment")
	}
	if o.Server != "auto" {
		for _, candidate := range candidates {
			if candidate.Key == o.Server {
				return o.Server, nil
			}
		}
		return "", errors.New("manual server is unavailable for this file's data center or network; choose Auto")
	}
	cacheMu.Lock()
	cache := readCache(o.Cache)
	selection := cache[cacheKey(o)]
	previous := selection.Endpoint
	if o.ReuseServer != "" {
		previous = o.ReuseServer
	}
	previousAvailable := false
	for _, candidate := range candidates {
		if candidate.Key == previous {
			previousAvailable = true
		}
	}
	now := time.Now().UTC()
	slowCheck := file.Size >= 64*1024*1024 && !o.ProbeOnly && previousAvailable && slowCheckDue(selection, o.MinSpeedMBPS*1e6, previous, now)
	if slowCheck {
		// Claim under the shared cache lock so concurrent jobs do not both test.
		selection.LastSlowCheck = now
		selection.Health = nil
		cache[cacheKey(o)] = selection
		if err := WriteJSON(o.Cache, cache); err != nil {
			cacheMu.Unlock()
			return "", err
		}
		o.Retest = true
		o.QuickTest = false
	}
	cacheMu.Unlock()
	if slowCheck {
		if err := e.Emit("server_slow_retest", map[string]any{"threshold_bps": o.MinSpeedMBPS * 1e6, "endpoint": previous}); err != nil {
			return "", err
		}
	}
	// Reuse the release's measured endpoint even if another concurrent job
	// updates the shared cache. Automatic retry can still compare again.
	if o.ReuseServer != "" && !o.Retest {
		for _, candidate := range candidates {
			if candidate.Key == o.ReuseServer {
				return candidate.Key, nil
			}
		}
		o.Retest = true
	}
	if selection, ok := cache[cacheKey(o)]; ok && !o.Retest && time.Since(selection.TestedAt) >= 0 && time.Since(selection.TestedAt) < 6*time.Hour {
		for _, candidate := range candidates {
			if candidate.Key == selection.Endpoint {
				return selection.Endpoint, nil
			}
		}
	}
	// Tiny files do not provide a meaningful throughput comparison. Prefer a
	// media endpoint, but leave the cache empty until a larger release arrives.
	if file.Size < 64*1024*1024 && !o.ProbeOnly {
		return candidates[0].Key, nil
	}
	measurements := []Measurement{}
	duration := 4 * time.Second
	if o.QuickTest {
		duration = 2 * time.Second
	}
	for index, candidate := range candidates {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		if err := e.Emit("server_testing", map[string]any{"endpoint": candidate.Key, "index": index + 1, "count": len(candidates)}); err != nil {
			return "", err
		}
		measured := measure(ctx, candidate.Key, duration)
		measurements = append(measurements, measured)
		if err := e.Emit("server_result", map[string]any{"measurement": measured}); err != nil {
			return "", err
		}
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	best := measuredEndpoint(measurements, previous, slowCheck)
	if best == "" && slowCheck {
		best = previous
		if err := e.Emit("server_test_fallback", map[string]any{"endpoint": previous}); err != nil {
			return "", err
		}
	}
	if best == "" {
		return "", errors.New("all download server tests failed; check the connection and retry")
	}
	cacheMu.Lock()
	cache = readCache(o.Cache)
	selection = cache[cacheKey(o)]
	selection.PreviousEndpoint = previous
	selection.Endpoint = best
	selection.TestedAt = time.Now().UTC()
	selection.Measurements = measurements
	selection.Health = nil
	selection.Reason = "cache_refresh"
	if slowCheck {
		selection.Reason = "sustained_slowdown"
	}
	cache[cacheKey(o)] = selection
	err := WriteJSON(o.Cache, cache)
	cacheMu.Unlock()
	if err != nil {
		return "", err
	}
	return best, nil
}

func Download(ctx context.Context, c *telegram.Client, kv storage.Storage, r *stltransport.Resolver, o Options, e *Events) error {
	if math.IsNaN(o.MinSpeedMBPS) || math.IsInf(o.MinSpeedMBPS, 0) || o.MinSpeedMBPS < 0 || o.MinSpeedMBPS > 1000 {
		return errors.New("invalid download speed threshold")
	}
	if o.ChatID <= 0 || o.Topic <= 0 || o.Message <= 0 || o.DC <= 0 || o.DocumentID == 0 || o.Size < 0 || o.Filename == "" || filepath.Base(o.Filename) != o.Filename {
		return errors.New("invalid attachment request")
	}
	manager := peers.Options{Storage: storage.NewPeers(kv)}.Build(c.API())
	peer, err := tutil.GetInputPeer(ctx, manager, fmt.Sprint(o.ChatID))
	if err != nil {
		return err
	}
	message, err := tutil.GetSingleMessage(ctx, c.API(), peer.InputPeer(), o.Message)
	if err != nil {
		return err
	}
	file, err := verifyMessage(message, o)
	if err != nil {
		return err
	}
	// Media endpoints need an existing cross-DC key; initialize on the ordinary
	// endpoint first. The home DC already has its authenticated primary key.
	if file.DC != c.Config().ThisDC {
		setup, cancel := context.WithTimeout(ctx, 30*time.Second)
		bootstrap, err := c.DC(setup, file.DC, 1)
		cancel()
		if err != nil {
			return err
		}
		defer bootstrap.Close()
	}
	key, err := choose(ctx, c, r, file, o, e)
	if err != nil {
		return err
	}
	if err = e.Emit("server_selected", map[string]any{"endpoint": key, "dc": file.DC}); err != nil {
		return err
	}
	if o.ProbeOnly {
		return e.Emit("probe_complete", nil)
	}
	f, err := os.OpenFile(o.Output, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	for attempt := 0; attempt < 2; attempt++ {
		setup, cancel := context.WithTimeout(ctx, 15*time.Second)
		pool, api, openErr := poolClient(setup, c, r, file.DC, key)
		cancel()
		err = openErr
		if err == nil {
			if err = e.Emit("download_start", map[string]any{"total": file.Size, "endpoint": key}); err != nil {
				pool.Close()
				return err
			}
			w := &countWriter{file: f, total: file.Size, events: e, speed: newSpeedMonitor(o, key, e, time.Now())}
			err = transfer(ctx, api, file, w)
			pool.Close()
			if flushErr := w.speed.flush(); err == nil {
				err = flushErr
			}
			if err == nil && w.done != file.Size {
				err = errors.New("download byte count did not match expected size")
			}
		}
		if err == nil {
			break
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if attempt != 0 || o.Server != "auto" {
			return err
		}
		if emitErr := e.Emit("server_retry", nil); emitErr != nil {
			return emitErr
		}
		o.Retest = true
		key, err = choose(ctx, c, r, file, o, e)
		if err != nil {
			return err
		}
		if err = e.Emit("server_selected", map[string]any{"endpoint": key, "dc": file.DC}); err != nil {
			return err
		}
		if err = f.Truncate(0); err != nil {
			return err
		}
	}
	if err = e.Emit("download_finished", map[string]any{"bytes": file.Size, "total": file.Size}); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	info, err := f.Stat()
	if err != nil {
		return err
	}
	if info.Size() != file.Size {
		return errors.New("downloaded file size mismatch")
	}
	if _, err = f.Seek(0, io.SeekStart); err != nil {
		return err
	}
	hash := sha256.New()
	buffer := make([]byte, 4*1024*1024)
	for {
		if err = ctx.Err(); err != nil {
			return err
		}
		n, readErr := f.Read(buffer)
		if n > 0 {
			hash.Write(buffer[:n])
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
	}
	if err = f.Close(); err != nil {
		return err
	}
	return e.Emit("complete", map[string]any{"bytes": file.Size, "total": file.Size, "sha256": hex.EncodeToString(hash.Sum(nil)), "message_id": o.Message, "filename": o.Filename})
}
