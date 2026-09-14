package stl

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/iyear/tdl/core/tmedia"
)

func TestSlowdownRequiresTwoMinutesAndHealthySpeedResets(t *testing.T) {
	start := time.Unix(1700000000, 0)
	w := newSpeedWatch(start, "server", 20e6, nil)
	for second := 0; second < 160; second++ {
		w.observe(start.Add(time.Duration(second)*time.Second), int64(second)*11_000_000)
		if w.health.LowSeconds >= 120 {
			t.Fatalf("triggered before two measured minutes: %d", second)
		}
	}
	w.observe(start.Add(160*time.Second), 160*11_000_000)
	if w.health.LowSeconds != 120 {
		t.Fatalf("low duration=%v", w.health.LowSeconds)
	}
	for second := 161; second <= 200; second++ {
		w.observe(start.Add(time.Duration(second)*time.Second), 160*11_000_000+int64(second-160)*30_000_000)
	}
	if w.health.LowSeconds != 0 {
		t.Fatal("recovery did not reset the slowdown")
	}
	// Brief dips cannot create two minutes of low-speed evidence.
	w = newSpeedWatch(start, "server", 20e6, nil)
	done := int64(0)
	for second := 0; second <= 600; second++ {
		speed := int64(30_000_000)
		if second%60 < 5 {
			speed = 1_000_000
		}
		done += speed
		w.observe(start.Add(time.Duration(second)*time.Second), done)
		if w.health.LowSeconds >= 120 {
			t.Fatal("brief dips triggered retest")
		}
	}
}

func TestSlowdownCarriesAcrossFilesWithoutCountingMoveTime(t *testing.T) {
	start := time.Unix(1700000000, 0)
	w := newSpeedWatch(start, "server", 20e6, nil)
	for second := 0; second <= 100; second++ {
		w.observe(start.Add(time.Duration(second)*time.Second), int64(second)*11_000_000)
	}
	if w.health.LowSeconds != 60 {
		t.Fatalf("first file=%v", w.health.LowSeconds)
	}
	// Even a 90-minute extraction/move gap adds no time and loses no evidence.
	next := start.Add(100*time.Second + 90*time.Minute)
	following := newSpeedWatch(next, "server", 20e6, &w.health)
	for second := 0; second < 100; second++ {
		following.observe(next.Add(time.Duration(second)*time.Second), int64(second)*11_000_000)
		if following.health.LowSeconds >= 120 {
			t.Fatal("idle time counted as a slowdown")
		}
	}
	following.observe(next.Add(100*time.Second), 100*11_000_000)
	if following.health.LowSeconds != 120 {
		t.Fatal("consecutive files did not accumulate active time")
	}
	for _, changed := range []*speedWatch{
		newSpeedWatch(next, "another-server", 20e6, &w.health),
		newSpeedWatch(next, "server", 10e6, &w.health),
		newSpeedWatch(start, "server", 20e6, &w.health),
	} {
		if changed.health.LowSeconds != 0 {
			t.Fatal("future or unrelated evidence reused")
		}
	}
}

func speedFixture(t *testing.T) (Options, *Events, []Endpoint, Selection) {
	t.Helper()
	directory := t.TempDir()
	o := Options{Server: "auto", Cache: filepath.Join(directory, "cache.json"), Network: "network-a", DC: 4, Size: 1 << 30, MinSpeedMBPS: 20}
	e, err := OpenEvents(filepath.Join(directory, "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { e.Close() })
	now := time.Now().UTC()
	s := Selection{Endpoint: "old", TestedAt: now.Add(-time.Hour), Health: &SpeedHealth{Endpoint: "old", ThresholdBPS: 20e6, BPS: 11e6, LowSeconds: 120, ObservedAt: now}}
	if err := WriteJSON(o.Cache, Cache{cacheKey(o): s}); err != nil {
		t.Fatal(err)
	}
	return o, e, []Endpoint{{Key: "old", DC: 4}, {Key: "new", DC: 4}}, s
}

func TestSlowdownBypassesCacheAndReleasePinButHonorsCooldown(t *testing.T) {
	o, e, endpoints, _ := speedFixture(t)
	o.ReuseServer = "old"
	o.QuickTest = true
	count := 0
	measure := func(ctx context.Context, key string, duration time.Duration) Measurement {
		count++
		if duration != 4*time.Second {
			t.Error("slowdown used noisy shortened samples")
		}
		speed := 11e6
		if key == "new" {
			speed = 28e6
		}
		return Measurement{Endpoint: key, BPS: speed}
	}
	key, err := chooseEndpoint(context.Background(), endpoints, &tmedia.Media{Size: o.Size}, o, e, measure)
	if err != nil || key != "new" || count != 2 {
		t.Fatalf("choice=%s tests=%d error=%v", key, count, err)
	}
	cache := readCache(o.Cache)
	s := cache[cacheKey(o)]
	if s.PreviousEndpoint != "old" || s.Reason != "sustained_slowdown" || s.LastSlowCheck.IsZero() || s.Health != nil {
		t.Fatalf("missing decision record: %+v", s)
	}
	s.Health = &SpeedHealth{Endpoint: "new", ThresholdBPS: 20e6, BPS: 11e6, LowSeconds: 120, ObservedAt: time.Now().UTC()}
	cache[cacheKey(o)] = s
	WriteJSON(o.Cache, cache)
	o.ReuseServer = ""
	key, err = chooseEndpoint(context.Background(), endpoints, &tmedia.Media{Size: o.Size}, o, e, measure)
	if err != nil || key != "new" || count != 2 {
		t.Fatal("repeated test inside cooldown", err)
	}
	s.LastSlowCheck = time.Now().Add(-11 * time.Minute)
	cache[cacheKey(o)] = s
	WriteJSON(o.Cache, cache)
	_, err = chooseEndpoint(context.Background(), endpoints, &tmedia.Media{Size: o.Size}, o, e, measure)
	if err != nil || count != 4 {
		t.Fatal("did not retest after cooldown", err)
	}
}

func TestSlowChecksRespectDisabledManualTinyAndOtherNetwork(t *testing.T) {
	for _, mode := range []string{"disabled", "manual", "tiny", "network", "duration", "threshold", "clock_rollback"} {
		t.Run(mode, func(t *testing.T) {
			o, e, endpoints, s := speedFixture(t)
			switch mode {
			case "disabled":
				o.MinSpeedMBPS = 0
			case "manual":
				o.Server = "old"
			case "tiny":
				o.Size = 10
			case "network":
				o.Network = "network-b"
				WriteJSON(o.Cache, Cache{cacheKey(o): {Endpoint: "old", TestedAt: time.Now().UTC()}})
			case "duration":
				s.Health.LowSeconds = 119
				WriteJSON(o.Cache, Cache{cacheKey(o): s})
			case "threshold":
				o.MinSpeedMBPS = 10
			case "clock_rollback":
				s.Health.ObservedAt = time.Now().Add(time.Hour)
				WriteJSON(o.Cache, Cache{cacheKey(o): s})
			}
			key, err := chooseEndpoint(context.Background(), endpoints, &tmedia.Media{Size: o.Size}, o, e, func(context.Context, string, time.Duration) Measurement {
				t.Fatal("unnecessary comparison")
				return Measurement{}
			})
			if err != nil || key != "old" {
				t.Fatal(key, err)
			}
		})
	}
}

func TestMarginalOrFailedTestsKeepWorkingServer(t *testing.T) {
	for _, mode := range []string{"marginal", "failed", "cancelled"} {
		t.Run(mode, func(t *testing.T) {
			o, e, endpoints, _ := speedFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			key, err := chooseEndpoint(ctx, endpoints, &tmedia.Media{Size: o.Size}, o, e, func(ctx context.Context, key string, d time.Duration) Measurement {
				if mode == "cancelled" {
					cancel()
				}
				speed := 11e6
				if key == "new" {
					speed = 11.5e6
				}
				return Measurement{Endpoint: key, BPS: speed, Failed: mode != "marginal"}
			})
			if mode == "cancelled" {
				if err == nil {
					t.Fatal("cancellation was swallowed")
				}
				return
			}
			if err != nil || key != "old" {
				t.Fatal("discarded working endpoint", key, err)
			}
		})
	}
}

func TestSpeedMonitorPersistsAndDoesNotOverwriteNewComparison(t *testing.T) {
	o, e, _, s := speedFixture(t)
	s.Health = nil
	WriteJSON(o.Cache, Cache{cacheKey(o): s})
	start := time.Now()
	monitor := newSpeedMonitor(o, "old", e, start)
	for second := 0; second <= 161; second++ {
		if err := monitor.observe(start.Add(time.Duration(second)*time.Second), int64(second)*11_000_000); err != nil {
			t.Fatal(err)
		}
	}
	if err := monitor.flush(); err != nil {
		t.Fatal(err)
	}
	saved := readCache(o.Cache)[cacheKey(o)]
	if saved.Health.LowSeconds < 120 || !monitor.announced {
		t.Fatal("slowdown not retained for the next file")
	}
	var decoded Cache
	data, _ := os.ReadFile(o.Cache)
	if json.Unmarshal(data, &decoded) != nil || decoded[cacheKey(o)].Health.LowSeconds < 120 {
		t.Fatal("not durable")
	}
	newer := Selection{Endpoint: "new", TestedAt: start.Add(time.Hour)}
	WriteJSON(o.Cache, Cache{cacheKey(o): newer})
	monitor.flush()
	if readCache(o.Cache)[cacheKey(o)].Health != nil {
		t.Fatal("old transfer overwrote new selection")
	}
}

func TestCriticalSpeedSkipsDurationButKeepsWarmupAndCooldown(t *testing.T) {
	for _, mode := range []string{"critical", "exact_half", "cooldown", "zero_speed"} {
		t.Run(mode, func(t *testing.T) {
			o, e, _, s := speedFixture(t)
			s.Health = nil
			if mode == "cooldown" {
				s.LastSlowCheck = time.Now()
			}
			if err := WriteJSON(o.Cache, Cache{cacheKey(o): s}); err != nil {
				t.Fatal(err)
			}
			start := time.Now()
			monitor := newSpeedMonitor(o, "old", e, start)
			speed := int64(9_000_000)
			if mode == "exact_half" {
				speed = 10_000_000
			}
			if mode == "zero_speed" {
				speed = 0
			}
			for second := 0; second <= 40; second++ {
				err := monitor.observe(start.Add(time.Duration(second)*time.Second), int64(second)*speed)
				want := second == 40 && (mode == "critical" || mode == "zero_speed")
				if errors.Is(err, errImmediateRetest) != want || (err != nil && !want) {
					t.Fatalf("second=%d error=%v want immediate=%v", second, err, want)
				}
			}
			saved := readCache(o.Cache)[cacheKey(o)]
			if saved.Health == nil || saved.Health.LowSeconds != 0 {
				t.Fatal("critical detection waited for accumulated duration")
			}
		})
	}
}

func TestReleaseBoundaryAndLongProcessingGapKeepChoiceAndEvidence(t *testing.T) {
	o, e, endpoints, s := speedFixture(t)
	s.Health.LowSeconds = 60
	s.Health.ObservedAt = time.Now().Add(-90 * time.Minute)
	if err := WriteJSON(o.Cache, Cache{cacheKey(o): s}); err != nil {
		t.Fatal(err)
	}
	o.Topic = 300
	o.Message = 200000
	o.Filename = "Another Artist 2026-10.zip"
	key, err := chooseEndpoint(context.Background(), endpoints, &tmedia.Media{Size: o.Size}, o, e, func(context.Context, string, time.Duration) Measurement {
		t.Fatal("release completion triggered unnecessary server test")
		return Measurement{}
	})
	if err != nil || key != "old" {
		t.Fatal(key, err)
	}
	monitor := newSpeedMonitor(o, key, e, time.Now())
	if monitor.watch.health.LowSeconds != 60 {
		t.Fatal("release or processing gap reset counter")
	}
	s.Health.LowSeconds = 120
	if err := WriteJSON(o.Cache, Cache{cacheKey(o): s}); err != nil {
		t.Fatal(err)
	}
	count := 0
	_, err = chooseEndpoint(context.Background(), endpoints, &tmedia.Media{Size: o.Size}, o, e, func(_ context.Context, key string, _ time.Duration) Measurement {
		count++
		return Measurement{Endpoint: key, BPS: 28e6}
	})
	if err != nil || count != 2 {
		t.Fatal("long pause lost queued test", count, err)
	}
}
