package stl

import "time"

const speedWarmup = 10 * time.Second
const speedWindow = 30 * time.Second
const slowdownMinimum = 5 * time.Minute
const slowdownCooldown = 10 * time.Minute
const speedHistoryAge = 30 * time.Minute

// SpeedHealth counts observed download time only, across consecutive files.
// Extraction, moves, startup, idle time and probes never add to LowSeconds.
type SpeedHealth struct {
	Endpoint     string    `json:"endpoint"`
	ThresholdBPS float64   `json:"threshold_bps"`
	BPS          float64   `json:"bps"`
	LowSeconds   float64   `json:"low_seconds"`
	ObservedAt   time.Time `json:"observed_at"`
}

type speedPoint struct {
	at    time.Time
	bytes int64
}
type speedWatch struct {
	start  time.Time
	last   time.Time
	points []speedPoint
	health SpeedHealth
}

func newSpeedWatch(now time.Time, endpoint string, threshold float64, saved *SpeedHealth) *speedWatch {
	w := &speedWatch{start: now, health: SpeedHealth{Endpoint: endpoint, ThresholdBPS: threshold}}
	if saved != nil && saved.Endpoint == endpoint && saved.ThresholdBPS == threshold && now.Sub(saved.ObservedAt) >= 0 && now.Sub(saved.ObservedAt) < speedHistoryAge {
		w.health.LowSeconds = saved.LowSeconds
	}
	return w
}

func (w *speedWatch) observe(now time.Time, done int64) bool {
	if now.Sub(w.start) < speedWarmup || (len(w.points) > 0 && now.Sub(w.points[len(w.points)-1].at) < time.Second) {
		return false
	}
	w.points = append(w.points, speedPoint{now, done})
	cutoff := now.Add(-speedWindow)
	for len(w.points) > 1 && !w.points[1].at.After(cutoff) {
		w.points = w.points[1:]
	}
	first := w.points[0]
	if now.Sub(first.at) < speedWindow {
		return false
	}
	w.health.BPS = float64(done-first.bytes) / now.Sub(first.at).Seconds()
	duration := now.Sub(w.last).Seconds()
	if w.last.IsZero() {
		duration = 0
	}
	if w.health.BPS < w.health.ThresholdBPS {
		w.health.LowSeconds += duration
	} else {
		w.health.LowSeconds = 0
	}
	w.last = now
	w.health.ObservedAt = now.UTC()
	return true
}

func slowCheckDue(s Selection, threshold float64, endpoint string, now time.Time) bool {
	h := s.Health
	return threshold > 0 && h != nil && h.Endpoint == endpoint && h.ThresholdBPS == threshold && h.BPS < threshold && h.LowSeconds >= slowdownMinimum.Seconds() &&
		now.Sub(h.ObservedAt) >= 0 && now.Sub(h.ObservedAt) < speedHistoryAge &&
		(s.LastSlowCheck.IsZero() || now.Sub(s.LastSlowCheck) >= slowdownCooldown)
}

type speedMonitor struct {
	watch      *speedWatch
	options    Options
	generation time.Time
	events     *Events
	saved      time.Time
	announced  bool
}

func newSpeedMonitor(o Options, endpoint string, events *Events, now time.Time) *speedMonitor {
	if o.MinSpeedMBPS <= 0 || o.Server != "auto" || o.ProbeOnly {
		return nil
	}
	cacheMu.Lock()
	s := readCache(o.Cache)[cacheKey(o)]
	cacheMu.Unlock()
	return &speedMonitor{watch: newSpeedWatch(now, endpoint, o.MinSpeedMBPS*1e6, s.Health), options: o, generation: s.TestedAt, events: events}
}

func (m *speedMonitor) observe(now time.Time, done int64) error {
	if m == nil || !m.watch.observe(now, done) {
		return nil
	}
	if now.Sub(m.saved) < 30*time.Second {
		return nil
	}
	return m.flush()
}

func (m *speedMonitor) flush() error {
	if m == nil || m.watch.health.ObservedAt.IsZero() {
		return nil
	}
	h := m.watch.health
	cacheMu.Lock()
	cache := readCache(m.options.Cache)
	s := cache[cacheKey(m.options)]
	// An older in-flight request must not overwrite a newer server comparison.
	if s.Endpoint != h.Endpoint || !s.TestedAt.Equal(m.generation) || (s.Health != nil && s.Health.ObservedAt.After(h.ObservedAt)) {
		cacheMu.Unlock()
		return nil
	}
	s.Health = &h
	cache[cacheKey(m.options)] = s
	err := WriteJSON(m.options.Cache, cache)
	due := slowCheckDue(s, h.ThresholdBPS, h.Endpoint, h.ObservedAt)
	cacheMu.Unlock()
	if err != nil {
		return err
	}
	m.saved = h.ObservedAt
	if due && !m.announced {
		m.announced = true
		return m.events.Emit("speed_retest_pending", map[string]any{"bps": h.BPS, "threshold_bps": h.ThresholdBPS, "low_seconds": h.LowSeconds, "endpoint": h.Endpoint})
	}
	return nil
}

// Avoid changing endpoints for a marginal difference in short samples.
func measuredEndpoint(measurements []Measurement, previous string, slowCheck bool) string {
	best := ""
	bestBPS := float64(0)
	previousBPS := float64(0)
	for _, m := range measurements {
		if m.Failed {
			continue
		}
		if m.Endpoint == previous {
			previousBPS = m.BPS
		}
		if m.BPS > bestBPS {
			best = m.Endpoint
			bestBPS = m.BPS
		}
	}
	if slowCheck && previousBPS > 0 && bestBPS < previousBPS*1.1 {
		return previous
	}
	return best
}
