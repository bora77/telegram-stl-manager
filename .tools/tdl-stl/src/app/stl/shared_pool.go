package stl

import (
	"context"
	"sync"

	"github.com/gotd/td/bin"
	"github.com/gotd/td/telegram"
	"github.com/gotd/td/tg"
	"github.com/iyear/tdl/core/stltransport"
)

type poolIdentity struct {
	client   *telegram.Client
	dc       int
	endpoint string
}
type sharedPool struct {
	pool       telegram.CloseInvoker
	api        *tg.Client
	references int
}
type poolRegistry struct {
	mu      sync.Mutex
	entries map[poolIdentity]*sharedPool
}
type poolLease struct {
	registry *poolRegistry
	key      poolIdentity
	entry    *sharedPool
	once     sync.Once
}

var downloadPools poolRegistry

func (p *poolLease) Invoke(ctx context.Context, in bin.Encoder, out bin.Decoder) error {
	return p.entry.pool.Invoke(ctx, in, out)
}
func (p *poolLease) Close() error {
	var err error
	p.once.Do(func() {
		p.registry.mu.Lock()
		p.entry.references--
		last := p.entry.references == 0
		if last {
			delete(p.registry.entries, p.key)
		}
		p.registry.mu.Unlock()
		if last {
			err = p.entry.pool.Close()
		}
	})
	return err
}
func (r *poolRegistry) acquire(ctx context.Context, key poolIdentity, open func() (telegram.CloseInvoker, *tg.Client, error)) (*poolLease, *tg.Client, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	entry := r.entries[key]
	if entry == nil {
		pool, api, err := open()
		if err != nil {
			return nil, nil, err
		}
		entry = &sharedPool{pool: pool, api: api}
		if r.entries == nil {
			r.entries = map[poolIdentity]*sharedPool{}
		}
		r.entries[key] = entry
	}
	entry.references++
	return &poolLease{registry: r, key: key, entry: entry}, entry.api, nil
}
func poolClient(ctx context.Context, c *telegram.Client, r *stltransport.Resolver, dc int, key string) (telegram.CloseInvoker, *tg.Client, error) {
	return downloadPools.acquire(ctx, poolIdentity{c, dc, key}, func() (telegram.CloseInvoker, *tg.Client, error) { return openPoolClient(ctx, c, r, dc, key) })
}
