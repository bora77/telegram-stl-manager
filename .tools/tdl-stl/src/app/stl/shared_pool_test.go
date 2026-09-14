package stl

import (
	"context"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gotd/td/bin"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/tg"
)

type fakeSharedPool struct{ closes atomic.Int32 }

func (p *fakeSharedPool) Invoke(context.Context, bin.Encoder, bin.Decoder) error { return nil }
func (p *fakeSharedPool) Close() error                                           { p.closes.Add(1); return nil }
func TestFinishingOneFileDoesNotCloseAnotherFilesPool(t *testing.T) {
	registry := poolRegistry{}
	pool := &fakeSharedPool{}
	opens := 0
	open := func() (telegram.CloseInvoker, *tg.Client, error) { opens++; return pool, nil, nil }
	key := poolIdentity{dc: 1, endpoint: "server"}
	a, _, err := registry.acquire(context.Background(), key, open)
	if err != nil {
		t.Fatal(err)
	}
	b, _, err := registry.acquire(context.Background(), key, open)
	if err != nil {
		t.Fatal(err)
	}
	a.Close()
	a.Close()
	if opens != 1 || pool.closes.Load() != 0 {
		t.Fatal("completed file disrupted its peer")
	}
	c, _, err := registry.acquire(context.Background(), key, open)
	if err != nil {
		t.Fatal(err)
	}
	b.Close()
	if pool.closes.Load() != 0 {
		t.Fatal("next file lost reused pool")
	}
	c.Close()
	if pool.closes.Load() != 1 || len(registry.entries) != 0 {
		t.Fatal("final pool not released")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, _, err := registry.acquire(ctx, key, open); err == nil || opens != 1 {
		t.Fatal("cancelled operation opened a pool")
	}
}
func TestParallelTrafficMeasuresCombinedUniquePayload(t *testing.T) {
	var traffic trafficWindow
	start := time.Unix(1700000000, 0)
	traffic.start(start)
	traffic.start(start)
	for second := 1; second <= 40; second++ {
		now := start.Add(time.Duration(second) * time.Second)
		traffic.record(now, 15_000_000)
		traffic.record(now, 14_000_000)
	}
	active, bps, valid := traffic.sample(start.Add(40 * time.Second))
	if active != 2 || !valid || bps < 28e6 || bps > 30e6 {
		t.Fatal("incorrect combined speed", active, bps, valid)
	}
	traffic.finish()
	traffic.finish()
	traffic.start(start.Add(time.Hour))
	_, _, valid = traffic.sample(start.Add(time.Hour + 5*time.Second))
	if valid {
		t.Fatal("idle period reused old traffic")
	}
	traffic.finish()
}
func TestHealthyCombinedTrafficSuppressesFalseCriticalRetest(t *testing.T) {
	o, e, _, s := speedFixture(t)
	s.Health = nil
	WriteJSON(o.Cache, Cache{cacheKey(o): s})
	start := time.Now()
	downloadTraffic.start(start)
	downloadTraffic.start(start)
	defer downloadTraffic.finish()
	defer downloadTraffic.finish()
	m := newSpeedMonitor(o, "old", e, start)
	for second := 1; second <= 100; second++ {
		now := start.Add(time.Duration(second) * time.Second)
		downloadTraffic.record(now, 30_000_000)
		if err := m.observe(now, int64(second)*15_000_000); err != nil {
			t.Fatal("healthy parallel traffic triggered a check", err)
		}
		if m.watch.health.LowSeconds != 0 {
			t.Fatal("parallel time was counted as a slowdown between cache writes")
		}
	}
	h := readCache(o.Cache)[cacheKey(o)].Health
	if h == nil || h.LowSeconds != 0 || h.BPS < 29e6 {
		t.Fatal("parallel health not retained", h)
	}
}
