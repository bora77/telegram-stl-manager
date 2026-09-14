package stl

import (
	"sync"
	"time"
)

type trafficWindow struct {
	mu     sync.Mutex
	active int
	bytes  int64
	points []speedPoint
}

var downloadTraffic trafficWindow

func (t *trafficWindow) start(now time.Time) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.active == 0 {
		t.bytes = 0
		t.points = []speedPoint{{at: now, bytes: 0}}
	}
	t.active++
}
func (t *trafficWindow) finish() { t.mu.Lock(); defer t.mu.Unlock(); t.active-- }
func (t *trafficWindow) record(now time.Time, n int64) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.active == 0 {
		return
	}
	t.bytes += n
	if len(t.points) == 0 || now.Sub(t.points[len(t.points)-1].at) >= time.Second {
		t.points = append(t.points, speedPoint{now, t.bytes})
	}
	cutoff := now.Add(-speedWindow)
	for len(t.points) > 1 && !t.points[1].at.After(cutoff) {
		t.points = t.points[1:]
	}
}
func (t *trafficWindow) sample(now time.Time) (int, float64, bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(t.points) == 0 {
		return t.active, 0, false
	}
	first := t.points[0]
	elapsed := now.Sub(first.at)
	if elapsed < speedWindow {
		return t.active, 0, false
	}
	// Tick-only observations also account for stalls after the last write.
	if now.Sub(t.points[len(t.points)-1].at) > speedWindow {
		return t.active, 0, true
	}
	return t.active, float64(t.bytes-first.bytes) / elapsed.Seconds(), true
}
